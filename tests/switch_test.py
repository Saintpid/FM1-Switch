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
allpk = S.scan(folders)
ok([(p.product, p.kind) for p in allpk] == [("FM-1_999", "other"), ("FM-1_900", "felucca"), ("FM-1_846", "felucca")]
   and "rescue only" in allpk[0].label(), f"scan: every FM-1 package, without a loader marked rescue only")
pk = S.scan(folders, rescue_only=False)
ok([p.product for p in pk] == ["FM-1_900", "FM-1_846"] and [p.family for p in pk] == ["NB303", "Wild"],
   f"scan: the installable ones, highest first ({[p.product for p in pk]})")
try:
    S.install(T.FakeFM1(b""), allpk[0], lambda *a: None)
    ok(False, "Install: a package without the loader is refused (Rescue's)")
except I.InstallError as e:
    ok(e.code == "badpkg" and "Rescue" in str(e), "Install: a package without the loader is refused (Rescue's)")
ch = S.rescue_choices(folders)
ok(ch[0] is None and [p.product for p in ch[1:]] == ["FM-1_999", "FM-1_900", "FM-1_846"],
   "rescue choices: V15 first (to download when not found), then any FM-1 firmware")

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
ok("--any-firmware" in S.rescue_args("w.fwsc", rd, "p", True) and "--any-firmware" not in S.rescue_args("v.fwsc", rd, "p")
   and (lambda a: a[a.index("--via") + 1] == "transporter")(S.rescue_args("v.fwsc", rd, "p", via="transporter")),
   "rescue: another firmware with --any-firmware, the adapter with --via transporter")
ok(not S.accepted(rd) and (S.accept(rd) or S.accepted(rd)) and "OWN RISK" in S.DISCLAIMER and "NO WARRANTY" in S.DISCLAIMER,
   "disclaimer: asked once, remembered; own risk, no warranty")
a = S.rescue_args(rd / "FM-1-V15.fwsc", rd, "/tmp/p.jsonl")
ok(a[0].endswith("FM-1-V15.fwsc") and "--write" in a and a[a.index("--progress") + 1] == "/tmp/p.jsonl"
   and a[a.index("--backup-dir") + 1] == str(rd), "rescue: V15, --write, the backup folder, the progress file")
cmd = S.helper_command(a)
ok(cmd[0] == sys.executable and cmd[1].endswith("fm1_switch.pyw") and cmd[2] == "--rescue" and cmd[3:] == a,
   "rescue: this program again, with --rescue")
osa = S.applescript_admin(["/Apps/FM 1/x", 'a"b', "c\\d"])
ok(osa == 'do shell script "\'/Apps/FM 1/x\' \'a\\"b\' \'c\\\\d\'" with administrator privileges',
   f"rescue on a Mac: one quoted shell line for osascript ({osa})")


# ---- MIDI missing: said so, not "FM-1 not found" (mido imports without python-rtmidi, fails at the port list)
class NoRtmidi:
    def input_names(self):
        raise ModuleNotFoundError("No module named 'rtmidi'")


def no_mido():
    raise ImportError("No module named 'mido'")


b0, why0 = S.open_midi(NoRtmidi)
b1, why1 = S.open_midi(no_mido)
b2, why2 = S.open_midi(lambda: dev)
ok(b0 is None and "rtmidi" in why0 and b1 is None and "mido" in why1 and b2 is not None and why2 is None,
   "MIDI: a missing backend is reported, a working one used")
ok("pip install -r requirements.txt" in S.NO_MIDI.format(why0) and "rtmidi" in S.NO_MIDI.format(why0),
   "MIDI: the window says how to fix it")
ok([S.row_values(p) for p in allpk] == [("?", "noloader", "FM-1_999", "rescue only"),
                                        ("NB303", "NB303-0.1.0", "FM-1_900", ""), ("Wild", "Wild-0.3.12", "FM-1_846", "")],
   "list columns: family, file, identity, rescue only")

print("switch: all ok" if not failed else f"switch: {failed} FAILED")
sys.exit(failed != 0)
