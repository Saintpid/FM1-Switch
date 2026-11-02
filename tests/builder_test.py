#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Mac export as Noisebud Builder reads it (its app flow, engine/builder.py inspect_zip): the zip
tools/export_switch_mac.cmd makes, rebuilt here from the files it copies, must have exactly one
build_mac_app.sh, one src/noisebud-product.json under that folder with schema 1, a releaseVersion and
registration appId / productId / productName, the same version as VERSION, and no setup folder
hard-coded in the script (Builder refuses SETUP_DEST="..." without NOISEBUD_SETUP_DEST).
  python3 tests/builder_test.py"""
import io
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<64} {'ok' if cond else 'FAIL'}")
    failed += not cond


cmd = (ROOT / "tools" / "export_switch_mac.cmd").read_text()
name = re.search(r'set "PROJECT_NAME=([^"]+)"', cmd)[1]
files = [f"tools/{f}" for f in re.search(r"for %%F in \(([^)]*)\)", cmd)[1].split()]
files += [p.replace("\\", "/") for p in re.findall(r'copy /Y "%PROJECT_DIR%\\([^"]+)"', cmd)]
ok(all((ROOT / f).is_file() for f in files), f"export: every file it copies is there ({len(files)})")

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    for f in files:
        z.write(ROOT / f, f"{name}/{f}")

# ---- Builder's app flow, as inspect_zip does it
with zipfile.ZipFile(buf) as z:
    names = {i.filename.rstrip("/") for i in z.infolist()}
    ok(not any(n.startswith("product/") for n in names) and "synthdeploy.json" not in {PurePosixPath(n).name for n in names},
       "Builder: not taken for a plugin or a module")
    scripts = sorted(n for n in names if n.endswith("/build_mac_app.sh") or n == "build_mac_app.sh")
    ok(len(scripts) == 1, "Builder: exactly one build_mac_app.sh")
    root = str(PurePosixPath(scripts[0]).parent)
    cands = [n for n in names if n.endswith("/src/noisebud-product.json") and ("/" + n).startswith("/" + root + "/")]
    ok(len(cands) == 1, "Builder: one src/noisebud-product.json beside it")
    m = json.loads(z.read(cands[0]).decode("utf-8-sig"))
    reg = m.get("registration", {})
    ok(m.get("schemaVersion") == 1 and isinstance(m.get("releaseVersion"), str)
       and all(isinstance(reg.get(k), str) and reg[k].strip() for k in ("appId", "productId", "productName")),
       "Builder: the manifest is valid (schema 1, registration)")
    ok(m["releaseVersion"] == (ROOT / "VERSION").read_text().strip(), f"manifest version = VERSION ({m['releaseVersion']})")
    script = z.read(scripts[0]).decode()
    hard = re.search(r'^SETUP_DEST="([^$"\n]+)"', script, re.M)
    ok(hard is None and "NOISEBUD_SETUP_DEST" in script, "Builder: the setup folder comes from NOISEBUD_SETUP_DEST")
    ok("dist/" in script and "build_switch_mac.sh" in script, "build_mac_app.sh: builds, and the DMG goes to dist/")

print("builder: all ok" if not failed else f"builder: {failed} FAILED")
sys.exit(failed != 0)
