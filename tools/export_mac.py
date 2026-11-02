#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""FM-1 Switch's Mac source export: exports/FM1Switch_mac_ready_vX.Y.Z.zip, from the committed tree
(git HEAD, so the bytes are the repository's: LF, no BOM), a reviewed list of files under one FM1Switch/
root, '/' in every path, shell scripts executable. It checks the finished archive the way Noisebud
Builder's delivery gate does (and release.ps1 then runs that gate itself, check_export.py). An export
that already exists is never rewritten: a changed source needs a new VERSION.
  python tools/export_mac.py [--out exports]"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "FM1Switch/"
FILES = ["VERSION", "LICENSE", "DISCLAIMER.md", "README.md", "CHANGELOG.md", "requirements.txt",
         "FM1SWITCH_BUILD_FLOW.txt", "build_fm1_mac.sh", "builder-flows.json", "installer/EULA.txt",
         "tools/fm1_switch.pyw", "tools/fm1_install.py", "tools/fm1_rescue.py",
         "tests/install_test.py", "tests/switch_test.py", "tests/rescue_test.py"]
TREES = ["mac/"]
EXECUTABLE = (".sh", ".command")
SHELL = (".sh", ".command")
FORBIDDEN = ("build_mac_app.sh", "noisebud-product.json", "synthdeploy.json")   # other Builder flows
FLOW = json.loads((ROOT / "builder-flows.json").read_text(encoding="utf-8"))[0]


class ExportError(Exception):
    pass


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()


def file_list(index=False):
    """the export's files, as committed (HEAD) or, for the tests before a commit, as staged (the index)"""
    listing = ("ls-files", "-z") if index else ("ls-tree", "-r", "--name-only", "-z", "HEAD")
    tracked = [t for t in git(*listing).split("\0") if t]
    out = [f for f in FILES if f in tracked]
    missing = [f for f in FILES if f not in tracked]
    if missing:
        raise ExportError("not committed: " + ", ".join(missing))
    out += sorted(t for t in tracked if any(t.startswith(d) for d in TREES) and "__pycache__" not in t)
    return out


def check_archive(data, flow=FLOW):
    """the finished bytes: one FM1Switch/ root, portable paths, shell files with a shebang, UTF-8, no BOM,
    LF; the flow's marker and entrypoint; nothing that would make Builder pick another flow"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if z.testzip():
            raise ExportError("CRC error in " + z.testzip())
        names = [i.filename for i in z.infolist()]
        if len(names) != len(set(names)):
            raise ExportError("duplicate entries")
        for i in z.infolist():
            n = i.filename
            p = PurePosixPath(n)
            if "\\" in n or p.is_absolute() or ".." in p.parts or not n.startswith(PREFIX):
                raise ExportError("not a portable FM1Switch/ path: " + n)
            if p.name in FORBIDDEN:
                raise ExportError("would select another Builder flow: " + n)
            if n.endswith(SHELL):
                b = z.read(i)
                b.decode("utf-8")
                if not b.startswith(b"#!") or b"\r" in b or b.startswith(b"\xef\xbb\xbf"):
                    raise ExportError("shell file needs a shebang, UTF-8 without BOM and LF: " + n)
                if not (i.external_attr >> 16) & 0o111:
                    raise ExportError("shell file is not executable: " + n)
        for key in ("marker", "entrypoint"):
            if flow[key] not in names:
                raise ExportError(f"the flow's {key} is missing: {flow[key]}")
        if z.read(PREFIX + "VERSION").decode().strip() == "":
            raise ExportError("empty VERSION")
    return True


def build_zip(files, read, date):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            info = zipfile.ZipInfo(PREFIX + f, date_time=date)
            info.create_system = 3                     # Unix, so the mode below counts
            mode = 0o755 if f.endswith(EXECUTABLE) or f == "mac/build_release.py" else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, read(f))
    return buf.getvalue()


def export(out_dir):
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise ExportError("uncommitted changes: commit first (the export is the committed tree)")
    version = git("show", "HEAD:VERSION").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ExportError("VERSION is not X.Y.Z")
    stamp = git("log", "-1", "--format=%cd", "--date=format:%Y %m %d %H %M %S").split()
    data = build_zip(file_list(), lambda f: git("show", "HEAD:" + f, binary=True), tuple(int(x) for x in stamp))
    check_archive(data)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"FM1Switch_mac_ready_v{version}.zip"
    if path.exists():
        if path.read_bytes() != data:
            raise ExportError(f"{path.name} exists with other contents: bump VERSION (old exports are kept)")
        print("Unchanged, kept: " + str(path))
    else:
        path.write_bytes(data)
    print(json.dumps({"export": str(path), "version": version, "sha256": hashlib.sha256(data).hexdigest(),
                      "flowId": FLOW["id"], "commit": git("rev-parse", "HEAD").strip()}, indent=2))
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "exports")
    a = ap.parse_args()
    try:
        export(a.out)
    except (ExportError, subprocess.CalledProcessError) as e:
        print(f"EXPORT FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
