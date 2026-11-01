#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/fm1_switch.pyw's core against the simulated FM-1 of install_test.py (no Tk, no hardware):
it lists FM-1 packages (highest number first, not other models or files without the loader), and
installs both ways: a lower number over a higher one (NB303 900 -> Wild 846) and back. Check FM-1's
diagnosis (running, an install cut off, the chip's update mode, nothing) and how Rescue… starts
fm1_rescue as administrator / root (the rescue itself: tests/rescue_test.py)."""
import importlib.machinery
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.argv = [sys.argv[0]]
import install_test as T  # noqa: E402  (its fake FM-1 and packages; its own tests run on import's main only)

loader = importlib.machinery.SourceFileLoader("fm1_switch", str(ROOT / "tools" / "fm1_switch.pyw"))
spec = importlib.util.spec_from_loader("fm1_switch", loader)
S = importlib.util.module_from_spec(spec)
loader.exec_module(S)
I = S.I
I.DELAY.update(T.I.DELAY)
failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<64} {'ok' if cond else 'FAIL'}")
    failed += not cond


d = Path(tempfile.mkdtemp(prefix="fm1-switch-"))
(d / "Firmware Wild").mkdir()
(d / "Firmware NB303").mkdir()
wild = d / "Firmware Wild" / "Wild-0.3.12.fwsc"
nb = d / "Firmware NB303" / "NB303-0.1.0.fwsc"
wild.write_bytes(T.package("FM-1_846"))
nb.write_bytes(T.package("FM-1_900"))
(d / "Firmware Wild" / "other.fwsc").write_bytes(T.package("XYZ-2_5"))
(d / "Firmware Wild" / "noloader.fwsc").write_bytes(T.package("FM-1_999", marker=False))
(d / "Firmware Wild" / "notes.txt").write_text("x")

folders = S.default_folders(d)
ok(d / "Firmware Wild" in folders and d / "Firmware NB303" in folders, "default folders: its own and the Firmware ones")
pk = S.scan(folders)
ok([p.product for p in pk] == ["FM-1_900", "FM-1_846"] and [p.family for p in pk] == ["NB303", "Wild"],
   f"scan: FM-1 packages only, highest first ({[p.product for p in pk]})")

dev = T.FakeFM1(I.logical_image(T.package("FM-1_846")), identity="FM-1_900", after_write="FM-1_846")
ok(S.device_text(dev) == "FM-1_900", "reads the FM-1: FM-1_900")
steps = []
got = S.install(dev, pk[1], lambda k, v=None: steps.append((k, v)))
ok(got == "FM-1_846" and dev.bad == 0 and ("write", 100) in steps and steps[-1] == ("done", "FM-1_846"),
   "NB303 900 -> Wild 846: a lower number installs")
ok(all(S.step_text(k, v) for k, v in steps), "every step has its text")

dev = T.FakeFM1(I.logical_image(T.package("FM-1_900")), identity="FM-1_846", after_write="FM-1_900")
ok(S.install(dev, pk[0], lambda *a: None) == "FM-1_900" and dev.bad == 0, "Wild 846 -> NB303 900: and back")

dev = T.FakeFM1(b"", identity="XYZ-2_5")
try:
    S.install(dev, pk[0], lambda *a: None)
    ok(False, "another model: refused")
except I.InstallError as e:
    ok(e.code == "model", "another model: refused")

dev = T.FakeFM1(b"")
dev.connected = False
ok(S.device_text(dev) is None, "no FM-1: none")
# ---- Check FM-1
dev = T.FakeFM1(b"", identity="FM-1_846")
ok(S.diagnose(dev, lambda: False, 0) == ("running", "FM-1_846"), "diagnosis: running FM-1_846")
dev = T.FakeFM1(b"", identity="ota-FM-1_900", name="Felucca Update")
ok(S.diagnose(dev, lambda: False, 0) == ("loader", "ota-FM-1_900"), "diagnosis: an install cut off (update mode)")
dev = T.FakeFM1(b"")
dev.connected = False
seen = iter([False, False, True])
ok(S.diagnose(dev, lambda: next(seen), 2, poll=0.01) == ("uboot", None),
   "diagnosis: the chip's update mode, caught when it shows up")
ok(S.diagnose(dev, lambda: False, 0.05, poll=0.01) == ("none", None) and S.diagnose(None, None, 0) == ("none", None),
   "diagnosis: nothing (and without MIDI or USB probing)")
texts = [S.advice("running", "FM-1_846"), S.advice("loader", "ota-FM-1_900"), S.advice("uboot"),
         S.advice("none"), S.advice("none", probe_ok=False)]
ok("Install" in texts[1][1] and "Rescue" in texts[2][1] and "transporter" in texts[3][1]
   and "pyusb" in texts[4][1] and "FM-1_846" in texts[0][0], "advice: what to do in each state")

# ---- the unbrick guide: the FM-1 first, questions only when it says nothing, then one thing to try
ok([S.needs_questions(x) for x in ("running", "loader", "uboot", "none")] == [False, False, False, True],
   "guide: asks only when the FM-1 says nothing")
ok(S.plan("running", "FM-1_846").action == "install" and S.plan("loader", "ota-FM-1_900").action == "finish"
   and S.plan("uboot").action == "rescue", "guide: what the FM-1 says decides by itself")
acts = {k: S.plan("none", screen=k).action for k, _ in S.SCREEN}
ok(acts == {"normal": "check", "safe": "check", "update": "check", "dark": "rescue", "loop": "rescue"},
   f"guide: by the screen, a picture is the cable, dark or restarting is Rescue ({acts})")
ok(S.plan("none").action == "check" and S.plan("none", screen="loop", rescue_failed=True).action == "hardware",
   "guide: no answers: check again; a rescue that never caught it: the hardware")
ok("Charge" in " ".join(S.plan("none", screen="dark").steps) and "Install" in " ".join(S.plan("none", screen="update").steps),
   "guide: dark asks for charging first; update mode finishes with Install")
ok(all(p.title and p.steps and p.button for p in [S.plan(st, "FM-1_1", sc, la) for st in ("running", "loader", "uboot", "none")
                                                   for sc, _ in S.SCREEN + ((None, ""),) for la, _ in S.LAST]),
   "guide: every case has a title, steps and a button")

# ---- Rescue…: the command it runs as administrator / root
rd = S.rescue_dir(d)
ok(rd == d / "FM-1 Rescue" and rd.is_dir(), "rescue folder: beside the program")
a = S.rescue_args(rd / "FM-1-V15.fwsc", rd, "/tmp/p.jsonl")
ok(a[0].endswith("FM-1-V15.fwsc") and "--write" in a and a[a.index("--progress") + 1] == "/tmp/p.jsonl"
   and a[a.index("--backup-dir") + 1] == str(rd), "rescue: V15, --write, the backup folder, the progress file")
cmd = S.helper_command(a)
ok(cmd[0] == sys.executable and cmd[1].endswith("fm1_switch.pyw") and cmd[2] == "--rescue" and cmd[3:] == a,
   "rescue: this program again, with --rescue")
osa = S.applescript_admin(["/Apps/FM 1/x", 'a"b', "c\\d"])
ok(osa == 'do shell script "\'/Apps/FM 1/x\' \'a\\"b\' \'c\\\\d\'" with administrator privileges',
   f"rescue on a Mac: one quoted shell line for osascript ({osa})")

print("switch: all ok" if not failed else f"switch: {failed} FAILED")
sys.exit(failed != 0)
