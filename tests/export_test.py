#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The Mac source export as Noisebud Builder gets it, from the real exporter's bytes (tools/export_mac.py
on the staged tree): one FM1Switch/ root, '/' paths, shell files with a shebang, UTF-8 without BOM, LF and
executable; the custom flow's marker and entrypoint; nothing that would select Builder's licensed app
flow. The archive check refuses each kind of fault. The standalone runner picks the highest version and
stops without one. The exported helpers import from a fresh interpreter in an unrelated folder without
PYTHONPATH. The shared runtime copies match their pinned hashes. The Mac build's release rules: no
notary profile or a non-universal Python stops it; the installer and the app's plist. When Builder's own
gate is on this computer (NOISEBUD_BUILDER_DIR, or C:/Projects/Noisebud Builder), it must say EXPORT OK.
  python3 tests/export_test.py"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "mac"))
import export_mac as X  # noqa: E402

failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<64} {'ok' if cond else 'FAIL'}")
    failed += not cond


def refused(data, flow=X.FLOW):
    try:
        X.check_archive(data, flow)
        return False
    except X.ExportError:
        return True


tmp = Path(tempfile.mkdtemp(prefix="fm1-export-"))
files = X.file_list(index=True)
data = X.build_zip(files, lambda f: (ROOT / f).read_bytes(), (2026, 11, 2, 0, 0, 0))
ok(X.check_archive(data), f"the export's own bytes pass its checks ({len(files)} files)")

with zipfile.ZipFile(io.BytesIO(data)) as z:
    names = z.namelist()
    infos = {i.filename: i for i in z.infolist()}
    ok(all(n.startswith("FM1Switch/") and "\\" not in n for n in names), "one FM1Switch/ root, '/' in every path")
    flow = json.loads((ROOT / "builder-flows.json").read_text())[0]
    ok(flow["id"] == "fm1-switch-source" and flow["kind"] == "custom" and flow["marker"] in names
       and flow["entrypoint"] in names and flow["outputGlobs"] == ["FM1Switch/dist/FM1Switch_macOS_v*.dmg"],
       "Builder custom flow: its marker and entrypoint are in the export")
    ok(not any(n.endswith(("/build_mac_app.sh", "/noisebud-product.json", "/synthdeploy.json")) for n in names),
       "no licensed app flow (build_mac_app.sh / noisebud-product.json)")
    sh = [n for n in names if n.endswith((".sh", ".command"))]
    ok(sh and all(z.read(n).startswith(b"#!") and b"\r" not in z.read(n) and (infos[n].external_attr >> 16) & 0o111
                  for n in sh), "shell files: shebang, LF, executable")
    for f in ("installer/EULA.txt", "mac/build_release.py", "mac/signing/noisebud_signing.py",
              "mac/noisebud-mac/verify_native_mac.py", "mac/assets/MacInstaller.png", "tools/fm1_switch.pyw"):
        ok("FM1Switch/" + f in names, "exported: " + f)
    z.extractall(tmp / "x")


# ---- the archive check refuses what Builder would refuse
def variant(edit):
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as dst:
        for i in src.infolist():
            out = edit(i, src.read(i))
            if out:
                dst.writestr(*out)
    return buf.getvalue()


ENTRY = "FM1Switch/build_fm1_mac.sh"
ok(refused(variant(lambda i, b: (i.filename.replace("FM1Switch/tools/", "FM1Switch\\tools\\"), b))),
   "refused: a backslash path")
ok(refused(variant(lambda i, b: (i, b.replace(b"\n", b"\r\n") if i.filename == ENTRY else b))), "refused: CRLF in a shell file")
ok(refused(variant(lambda i, b: (i, b"\xef\xbb\xbf" + b if i.filename == ENTRY else b))), "refused: a BOM")
ok(refused(variant(lambda i, b: None if i.filename == ENTRY else (i, b))), "refused: no custom entrypoint")
ok(refused(data, dict(flow, marker="FM1Switch/OTHER.txt")), "refused: no marker")


def plus(name, b=b"#!/bin/bash\n"):
    def edit(i, d):
        return (i, d)
    v = variant(edit)
    buf = io.BytesIO(v)
    with zipfile.ZipFile(buf, "a") as z:
        info = zipfile.ZipInfo(name)
        info.external_attr = 0o100755 << 16
        z.writestr(info, b)
    return buf.getvalue()


ok(refused(plus("FM1Switch/build_mac_app.sh")), "refused: build_mac_app.sh back (Builder's licensed app flow)")
ok(refused(plus("FM1Switch/src/noisebud-product.json", b"{}")), "refused: a product/licence manifest")


# ---- the standalone runner: the highest version beside it, never by date, nothing without one
cmd = ROOT / "tools/Build_FM1Switch_Mac.command"
ok(cmd.read_bytes().startswith(b"#!") and b"\r" not in cmd.read_bytes(), "runner: shebang, LF")
bash = None if os.name == "nt" else shutil.which("bash")     # (Windows' bash may be WSL's: Mac and Linux test it)
sel = tmp / "sem"
sel.mkdir()
shutil.copy(cmd, sel / cmd.name)
for v in ("1.9.0", "1.10.0", "1.2.3"):
    (sel / f"FM1Switch_mac_ready_v{v}.zip").write_bytes(b"x")
(sel / "FM1Switch_mac_ready_vbad.zip").write_bytes(b"x")
os.utime(sel / "FM1Switch_mac_ready_v1.2.3.zip", (2e9, 2e9))           # the newest by date
env = dict(os.environ, FM1_SWITCH_SELECT_ONLY="1")
if bash:
    r = subprocess.run([bash, str(sel / cmd.name)], capture_output=True, text=True, env=env, cwd=tmp)
    ok(r.returncode == 0 and "FM1Switch_mac_ready_v1.10.0.zip" in r.stdout, "runner: picks 1.10.0 (highest), not the newest file")
    for f in sel.glob("*.zip"):
        f.unlink()
    r = subprocess.run([bash, str(sel / cmd.name)], capture_output=True, text=True, env=env, cwd=tmp)
    ok(r.returncode != 0 and "no FM1Switch_mac_ready" in r.stderr, "runner: no export beside it, it stops")
else:
    print("(runner: tested on Mac and Linux)")
ok("Glass.aiff" in cmd.read_text() and "Basso.aiff" in cmd.read_text() and "trap finish EXIT" in cmd.read_text(),
   "runner: Glass / Basso from its EXIT trap")


# ---- the exported helpers, from a fresh interpreter in another folder, no PYTHONPATH
x = tmp / "x/FM1Switch"
clean = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
elsewhere = tempfile.mkdtemp(prefix="fm1-elsewhere-")
r = subprocess.run([sys.executable, "-E", "-s", str(x / "mac/build_release.py"), "--check-imports"],
                   capture_output=True, text=True, env=clean, cwd=elsewhere)
ok(r.returncode == 0 and "imports ok" in r.stdout, "exported build_release.py imports its helpers (fresh interpreter)")
r = subprocess.run([sys.executable, "-E", "-s", str(x / "mac/noisebud-mac/verify_native_mac.py"), "--help"],
                   capture_output=True, text=True, env=clean, cwd=elsewhere)
ok(r.returncode == 0, "exported verify_native_mac.py finds package_common beside it")
r = subprocess.run([sys.executable, "-E", "-s", str(x / "mac/signing/noisebud_signing.py"), "json"],
                   capture_output=True, text=True, env=dict(clean, HOME=elsewhere), cwd=elsewhere)
ok(r.returncode == 0 and json.loads(r.stdout)["team"] == "249F9ZZ46Y", "exported signing runtime reads its bundled selectors")

# ---- the shared runtime copies: as pinned (byte-identical to upstream when imported)
pins = json.loads((ROOT / "mac/SOURCE_HASHES.json").read_text())["files"]
ok(all(hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == p["sha256"] for f, p in pins.items())
   and "mac/signing/noisebud_signing.py" in pins, f"shared runtime copies match their pins ({len(pins)})")

# ---- the Mac build's rules
import build_release as B  # noqa: E402

try:
    B.release_config(env={}, lines=[], settings=lambda: {"team": "T", "application": "A", "installer": "I"})
    ok(False, "release: no notary profile stops it")
except B.Fail:
    ok(True, "release: no notary profile stops it")
ok(B.release_config(env={"NOISEBUD_APPLE_NOTARY_PROFILE": "P"}, lines=[],
                    settings=lambda: {"team": "T", "application": "A", "installer": "I"})["notary"] == "P",
   "release: the notary profile from the environment")
for args, what in (((sys.executable, (3, 12), "macosx-11.0-arm64"), "a single-architecture Python"),
                   ((sys.executable, (3, 13), "macosx-10.13-universal2"), "Python 3.13 (no rtmidi wheels)")):
    try:
        B.check_python(*args)
        ok(False, "release: stops on " + what)
    except B.Fail:
        ok(True, "release: stops on " + what)
try:
    B.check_native_host(lambda n: {"hw.optional.arm64": 1, "sysctl.proc_translated": 1}.get(n), "x86_64")
    ok(False, "release: stops when the build tools run translated")
except B.Fail:
    ok(True, "release: stops when the build tools run translated")
ok(B.setup_dest({"NOISEBUD_SETUP_DEST": "/b"}, []) == "/b" and B.setup_dest({}, []) == B.SETUP_FALLBACK,
   "release: NOISEBUD_SETUP_DEST first, then the Noisebud setup folder")
pl = B.app_plist({}, "9.8.7")
ok(pl["LSMinimumSystemVersion"] == "11.0" and pl["LSRequiresNativeExecution"] is True
   and pl["CFBundleIdentifier"] == "se.noisebud.FM1Switch" and pl["CFBundleShortVersionString"] == "9.8.7",
   "app plist: macOS 11.0, native execution, the bundle id, the version")
dx = B.distribution_xml("9.8.7").decode()
ok('license file="EULA.html"' in dx and 'enable_localSystem="true"' in dx and 'min="11.0"' in dx
   and "x86_64,arm64" in dx and "registration" not in dx.lower(), "PKG: the licence page, Applications, macOS 11.0, no registration")
B.write_resources(tmp / "res", ROOT, "9.8.7")
ok("NO WARRANTY" in (tmp / "res/EULA.html").read_text() and "own risk" in (tmp / "res/Welcome.html").read_text(),
   "PKG pages: the EULA and the risk")
app = tmp / "A.app"
files = [app / "Contents/MacOS/FM1-Switch", app / "Contents/Frameworks/lib/x.dylib",
         app / "Contents/Frameworks/Tcl.framework/Versions/8.6/Tcl", app / "Contents/Resources/y.so"]
order = B.sign_order(app, files)
ok(order[-1] == app and order[-2] == app / "Contents/Frameworks/Tcl.framework" and order.index(files[2]) < order.index(order[-2])
   and files[0] not in order, "signing order: nested code, then its bundle, the app last (no --deep)")
ok(B.dmg_name("1.2.0") == "FM1Switch_macOS_v1.2.0.dmg" and "DEV" in B.dmg_name("1.2.0", True),
   "the DMG's name matches the flow's output; a dev build's does not")

hook = (ROOT / "mac/pyinstaller-hooks/hook-usb.py").read_text()
ok("binaries = []" in hook and any(x.startswith("importlib_resources==") for x in B.PURE)
   and "FM1Switch/mac/pyinstaller-hooks/hook-usb.py" in names,
   "app: no build Mac's libusb bundled (own pyusb hook, exported), libusb-package's dependency pinned")

# ---- Noisebud Builder's own gate, when it is on this computer
gate = Path(os.environ.get("NOISEBUD_BUILDER_DIR", "C:/Projects/Noisebud Builder")) / "tools/check_export.py"
if gate.is_file():
    zp = tmp / "FM1Switch_mac_ready_v0.0.0.zip"
    zp.write_bytes(data)
    r = subprocess.run([sys.executable, str(gate), str(zp), "--flows", str(ROOT / "builder-flows.json")],
                       capture_output=True, text=True)
    ok(r.returncode == 0 and "EXPORT OK" in r.stdout and '"flowId": "fm1-switch-source"' in r.stdout,
       "Noisebud Builder's check_export.py: EXPORT OK, fm1-switch-source")
else:
    print(f"(Noisebud Builder's check_export.py is not here: {gate}; release.ps1 runs it)")

print("export: all ok" if not failed else f"export: {failed} FAILED")
sys.exit(failed != 0)
