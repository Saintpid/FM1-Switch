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
from unittest.mock import patch

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

# Exercise folder discovery against fixture projects/Downloads only. A developer's
# real firmware must never change the simulated install targets or test ordering.
test_home = d / 'test-home'
(test_home / 'Downloads').mkdir(parents=True)
with patch.object(S, 'PROJECTS', (d / 'fixture-project',)), patch.object(Path, 'home', return_value=test_home):
    folders = S.default_folders(d)
ok(all(p.is_relative_to(d) for p in folders) and test_home / 'Downloads' in folders,
   'default folders: isolated from real projects and Downloads')
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
    ok(e.code == "badpkg" and "Through update mode" in str(e), "Install: a package without the loader is refused (Rescue's)")
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
# ---- M-VAVE's SMK-37: its own firmware (stock by SHA-256, ports with the loader), never an FM-1's
kbd = d / "Firmware SMK"
kbd.mkdir()
stock_raw = T.package("SMK-37 Pro_016", marker=False)
(kbd / "SMK-37_Pro_016.fwsc").write_bytes(stock_raw)
(kbd / "port.fwsc").write_bytes(T.package("SMK-37 Pro_901"))
(kbd / "odd.fwsc").write_bytes(T.package("SMK-37 Pro_903", marker=False))
with patch.dict(I.KEYBOARD_STOCK, {S.R.sha256(stock_raw): "SMK-37 Pro_016"}):
    kp = S.scan(folders + [kbd], rescue_only=False)
    ok([(p.product, p.kind, p.family) for p in kp] == [("FM-1_900", "felucca", "NB303"), ("FM-1_846", "felucca", "Wild"),
                                                        ("SMK-37 Pro_901", "felucca", "SMK-37 Pro"),
                                                        ("SMK-37 Pro_016", "stock", "M-VAVE")]
       and S.row_values(kp[3])[3] == "stock",
       f"scan: SMK-37 port and M-VAVE's stock listed after the FM-1's ({[p.product for p in kp]})")
    ok(all(p.model == "FM-1" for p in S.rescue_choices(folders + [kbd])[1:]), "rescue choices: FM-1 firmware only")
    dev = T.FakeFM1(I.logical_image(stock_raw), identity="SMK-37 Pro_901", name="SMK-37 Pro",
                    after_write="SMK-37 Pro_016")
    ok(S.install(dev, kp[3], lambda *a: None) == "SMK-37 Pro_016" and dev.bad == 0,
       "SMK-37 Pro: M-VAVE's stock (no loader) goes back over USB-MIDI")
    dev = T.FakeFM1(b"", identity="SMK-37 Pro_016", name="SMK-37 Pro")
    try:
        S.install(dev, pk[0], lambda *a: None)
        ok(False, "SMK-37 Pro: an FM-1 firmware is refused, nothing sent")
    except I.InstallError as e:
        ok(e.code == "model" and dev.upgrades == 0, "SMK-37 Pro: an FM-1 firmware is refused, nothing sent")
    dev = T.FakeFM1(b"", identity="FM-1_846")
    try:
        S.install(dev, kp[3], lambda *a: None)
        ok(False, "FM-1: an SMK-37 firmware is refused, nothing sent")
    except I.InstallError as e:
        ok(e.code == "model" and dev.upgrades == 0, "FM-1: an SMK-37 firmware is refused, nothing sent")
dev = T.FakeFM1(b"", identity="SMK-37 Elite_016", name="SMK-37 Elite")
ok(S.diagnose(dev, lambda: False, 0) == ("running", "SMK-37 Elite_016"), "diagnosis: an SMK-37 Elite is heard")
h, t = S.advice("running", "SMK-37 Elite_016")
pl = S.plan("loader", "ota-SMK-37 Elite_016")
ok(h.startswith("SMK-37 Elite") and "not yet tried" in t and pl.action == "finish" and "SMK-37 Elite" in pl.title
   and S.keyboard("FM-1_846") is None, "advice and guide: for the keyboard, untried, its own firmware")

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
ok("Install" in texts[1][1] and "won't start" in texts[2][1] and "won't start" in texts[3][1]
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
inspection = S.rescue_args(None, rd, "p", inspect=True, any_firmware=True, allow_ucun=True)
ok("--inspect" in inspection and "--write" not in inspection and "--any-firmware" not in inspection
   and "--allow-ucun-write" not in inspection and "None" not in inspection,
   "Hardware / backup: helper command cannot request writes or a package")
ok("--allow-ucun-write" not in a
   and "--allow-ucun-write" in S.rescue_args("v.fwsc", rd, "p", allow_ucun=True),
   "UCUN recovery: explicit opt-in only")
err = I.InstallError("stopped", "stopped answering after 0 requests")
ok("UCUN" in S.install_error_text(err) and "does not tell which chip" in S.install_error_text(err)
   and S.install_error_text(I.InstallError("model", "wrong model")) == "wrong model",
   "update diagnosis: possible UCUN incompatibility, no inferred chip ID")
ok(a[0].endswith("FM-1-V15.fwsc") and "--write" in a and a[a.index("--progress") + 1] == "/tmp/p.jsonl"
   and a[a.index("--backup-dir") + 1] == str(rd), "rescue: V15, --write, the backup folder, the progress file")
t = S.rescue_args("v.fwsc", rd, "p", via="transporter")
ok(a[a.index("--mflr-dir") + 1] == str(rd) and "--loader" not in a
   and t[t.index("--loader") + 1] == str(rd / S.R.LOADER_NAME) and "--mflr-dir" not in t,
   "rescue: M-Flasher's loader over USB, JieLi's through the adapter")
cmd = S.helper_command(a, "win32")
ok(cmd[0] == sys.executable and cmd[1].endswith("fm1_switch.pyw") and cmd[2] == "--rescue" and cmd[3:] == a,
   "rescue: this program again, with --rescue")


def silicon(translated):
    return lambda name: {"hw.optional.arm64": 1, "sysctl.proc_translated": int(translated)}.get(name)


def intel(name):
    return None                         # (an Intel Mac has neither sysctl)


mac_cmd = S.helper_command(a, "darwin", silicon(True))
ok(mac_cmd[:2] == ["/usr/bin/arch", "-arm64"] and mac_cmd[2:] == cmd,
   "Mac: the rescue helper starts arm64 on Apple Silicon, also from a translated window")
ok(S.helper_command(a, "darwin", intel)[:2] == ["/usr/bin/arch", "-x86_64"], "Mac: x86_64 on an Intel Mac")
calls = []
env = {}
ok(S.reexec_native("darwin", True, silicon(True), env, lambda *a: calls.append(a)) and calls
   and calls[0][1][:2] == ["/usr/bin/arch", "-arm64"] and env.get("FM1_SWITCH_NATIVE") == "1"
   and not S.reexec_native("darwin", True, silicon(True), env, lambda *a: calls.append(a)) and len(calls) == 1,
   "Mac: started translated, the app restarts itself arm64 (once)")
ok(not S.reexec_native("darwin", True, silicon(False), {}, lambda *a: calls.append(a))
   and not S.reexec_native("win32", True, silicon(True), {}, lambda *a: calls.append(a)) and len(calls) == 1,
   "Mac: native already, or not a Mac: no restart")
pr = S.native_probe(silicon(False))
ok(pr["hardwareArchitecture"] == "arm64" and {"native", "translated", "architecture", "executable", "pid"} <= set(pr)
   and S.native_probe(silicon(True))["native"] is False, "native probe: hardware, translation, what verify_native_mac reads")
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

# ---- the window's thread hop: keywords too (the progress bar is bar.configure(value=...))
queued, got = [], {}
S.later(lambda ms, f, *a: queued.append((ms, f, a)), lambda *a, **kw: got.update(a=a, kw=kw), "x", value=42)
for ms, f, a in queued:
    f(*a)
ok(len(queued) == 1 and got == {"a": ("x",), "kw": {"value": 42}}, "window: a call with keywords reaches Tk's thread")

renamed = T.FakeFM1(b"", identity="FM-1_846", name="SLOOP")
ok(S.confirm_rescue_boot(renamed, "FM-1_846", secs=0) is None,
   "renamed MIDI: automatic search does not probe unrelated names")
ok(S.confirm_rescue_boot(renamed, "FM-1_846", port="SLOOP", secs=0) == "FM-1_846",
   "renamed MIDI: explicit selection reads identity")
ok(S.diagnose(renamed, probe=lambda: False, secs=0, port="SLOOP") == ("running", "FM-1_846"),
   "diagnosis uses selected MIDI port")
ok(S.rescue_result("FM-1_846", "FM-1_846")[0]
   and not S.rescue_result("FM-1_846", "FM-1_900")[0]
   and not S.rescue_result("FM-1_846", None)[0],
   "recovery confirmed only for the expected running identity")
ok(S.confirm_rescue_boot(None, "FM-1_846", secs=0) is None,
   "recovery: missing MIDI never implies successful startup")
renamed.boot("ota-FM-1_900", "SLOOP")
ok(S.confirm_rescue_boot(renamed, "FM-1_846", port="SLOOP", secs=0) is None,
   "recovery: update loader is not a running firmware")
renamed.boot("FM-1_015", "FM-1")
ok(S.confirm_rescue_boot(renamed, "FM-1_015", port="SLOOP", secs=0) == "FM-1_015",
   "recovery: restored stock port is found after a renamed input")

# ---- before a USB-MIDI install: a package that refuses UCUN flash is warned about
import json as _json  # noqa: E402
import random as _random  # noqa: E402
import struct as _struct  # noqa: E402
R = S.R
img = bytearray(_random.Random(5).randrange(256) for _ in range(R.APP_END))
head = bytearray(_random.Random(3).randrange(256) for _ in range(32))
_struct.pack_into("<H", head, 0, R.crc16(bytes(head[2:32])))
R.enc(head, 0x980F)
img[R.WRITE_MIN:R.WRITE_MIN + 32] = head


def ids_image(ids):
    plain = bytearray(R.app_area(bytes(img)))
    for i, fid in enumerate(ids):
        plain[0x9000 + 16 * i:0x9004 + 16 * i] = fid.to_bytes(4, "little")
    out = bytearray(img)
    out[R.WRITE_MIN:R.APP_END] = R.app_area(bytes(R.WRITE_MIN) + bytes(plain))
    return bytes(out)


hw = Path(tempfile.mkdtemp(prefix="fm1-hw-"))
pk = S.Package(wild, "FM-1_863")
with patch.object(R, "package_flash_image", return_value=ids_image([0x856014])):
    warn = S.flash_warning(pk)
    pk2 = S.Package(wild, "FM-1_863")
    (hw / "fm1-hardware-a.json").write_text(_json.dumps({"hardware": {"flash_name": "UCUN UC25HQ80",
                                                                      "jedec_id": "B36014"}}))
    warn2 = S.flash_warning(pk2, S.last_hardware(hw))
ok(warn and "B36014" in warn and "maker says" in warn and "FM-1_864" not in warn, "install: Puya-only firmware warned about UCUN flash")
ok(warn2 and "newer FM-1 memory chip (UCUN)" in warn2.split("checked here")[1], "install: the last identified flash is named")
with patch.object(R, "package_flash_image", return_value=ids_image([0x856014, 0xB36014])):
    ok(S.flash_warning(S.Package(wild, "FM-1_869")) is None, "install: firmware with UCUN support not warned")
with patch.object(R, "package_flash_image", return_value=bytes(img)):
    ok(S.flash_warning(S.Package(wild, "FM-1_870")) is None, "install: unknown support not blocked")
(hw / "broken.json").write_text("{")
(hw / "fm1-hardware-b.json").write_text("[]")
ok(S.last_hardware(hw)[0]["jedec_id"] == "B36014" and S.last_hardware(hw / "missing") is None,
   "install: hardware reports read safely")

hw_info = {"flash_name": "Puya P25Q80H", "jedec_id": "856014", "chip_key": "980F", "flash_type": 3,
           "bootloader_text": "M-VAVE's (stock)", "firmware": "OberVave, by zvenson", "firmware_status": "ok"}
ok("OberVave, by zvenson" in S.hardware_text({**hw_info, "firmware_hub": ["obervave"]})
   and "thefirmwarehub.com" in S.hardware_text({**hw_info, "firmware_hub": ["obervave"]})
   and "thefirmwarehub.com" not in S.hardware_text({**hw_info, "firmware_hub": []}),
   "window: a firmware named from the hub's list credits it")

# ------------------------------------------------------- protect my FM-1 ---
pa = S.protect_args(rd, "/tmp/p.jsonl")
ok(pa[:2] == ["--protect", "--ask"] and "--write" not in pa and pa[pa.index("--progress") + 1] == "/tmp/p.jsonl"
   and pa[pa.index("--mflr-dir") + 1] == str(rd) and pa[pa.index("--tries") + 1] == "3"
   and "--allow-ucun-write" not in pa and "--via" not in pa,
   "protect: the helper asks the window before it writes, over USB, UCUN only by the answer")
ok(S.chip_text({"jedec_id": "856014"}).startswith("the original") and "UCUN" in S.chip_text({"jedec_id": "b36014"})
   and S.chip_text({"jedec_id": "123456", "flash_name": "X"}) == "X (123456)", "protect: the flash chip in plain words")
ft = S.found_text({"jedec_id": "856014"}, {"bootloader_text": "M-VAVE's (stock)", "firmware": "Wild 0.3.43",
                                           "firmware_status": "ok", "firmware_hub": []})
ok(ft.count("\n") == 2 and "Wild 0.3.43." in ft and "stock" in ft and "thefirmwarehub" not in ft,
   "protect: what the backup found")
ft = S.found_text({"jedec_id": "856014"}, {"firmware": "X", "firmware_status": "damaged",
                                           "firmware_status_text": "damaged files", "firmware_hub": ["x"]})
ok("X (damaged files)." in ft and "thefirmwarehub" in ft and S.found_text({"jedec_id": "856014"}, None).count("\n") == 0,
   "protect: a damaged firmware is said, a hub name credited")
pt = S.plan_text({"sectors": [0, 0x1000, 0x3000], "firmware_ok": True})
ok("3 outlined" in pt and "damaged" not in pt and "damaged" in S.plan_text({"sectors": [0], "firmware_ok": False}),
   "protect: the plan in one sentence")
lg = S.map_legend(["boot"] * 4 + ["firmware"] * 100 + ["free"] * 150 + ["mac", "boot"])
ok([x[0] for x in lg] == ["boot", "firmware", "free", "mac"] and lg[0][1] == "Start-up program"
   and all(c.startswith("#") for _, _, c in lg), "protect: the map's legend, each region once")
pd = Path(tempfile.mkdtemp(prefix="fm1-protect-"))
ok(S.protection(pd) is None and S.last_backup(pd) is None, "protect: nothing read yet")
(pd / "fm1-backup-1.bin.json").write_text(_json.dumps({"hardware": {"bootloader": "stock"}}))
(pd / "fm1-backup-2.bin.json").write_text("{")
ok(S.protection(pd) == "not", "protect: a stock bootloader in the last backup: not protected")
import os as _os  # noqa: E402
S.mark_protected(pd, "2.4")
_os.utime(pd / "protected.json", (2e9, 2e9))
ok(S.protection(pd) == "protected", "protect: after an install, protected")
(pd / "fm1-backup-3.bin.json").write_text(_json.dumps({"hardware": {"bootloader": "stock"}}))
_os.utime(pd / "fm1-backup-3.bin.json", (2.1e9, 2.1e9))
ok(S.protection(pd) == "not", "protect: a later backup with the stock bootloader wins (an M-VAVE update put it back)")
(pd / "fm1-backup-4.bin.json").write_text(_json.dumps({"hardware": {"bootloader": "open"}}))
_os.utime(pd / "fm1-backup-4.bin.json", (2.2e9, 2.2e9))
ok(S.protection(pd) == "protected" and S.last_backup(pd)[0]["bootloader"] == "open",
   "protect: a backup with m-bootloader in it: protected")
ok(all(k in S.PROTECTION_TEXT for k in ("protected", "not", None)) and set(S.TASK_TEXT) ==
   {"protect", "install", "backup", "guide"}, "window: the start screen's four tasks and the protection line")

# ------------------------------------------------------------- backups ---
ra = S.protect_args(rd, "p", "restore", "/b/fm1-backup-1.bin")
sa = S.protect_args(rd, "p", "stock", "/b/fm1-backup-1.bin")
ok(ra[:2] == ["--restore", "/b/fm1-backup-1.bin"] and "--boot-only" not in ra and "--ask" in ra and "--protect" not in ra
   and sa[2] == "--boot-only" and "--write" not in ra + sa, "backups: put back, or the boot area alone, after the window's yes")
bd = Path(tempfile.mkdtemp(prefix="fm1-backups-"))
for n, kind, when in (("a", "stock", 1.0e9), ("b", "open", 1.1e9)):
    f = bd / f"fm1-backup-{n}.bin"
    f.write_bytes(bytes(R.FLASH_SIZE))
    (bd / f"fm1-backup-{n}.bin.json").write_text(_json.dumps({"backup_sha256": "x", "hardware": {
        "firmware": "Wild 0.3.43", "bootloader": kind, "bootloader_text": kind + " text"}}))
    _os.utime(f, (when, when))
(bd / "fm1-backup-c.bin").write_bytes(b"short")
(bd / "fm1-backup-c.bin.json").write_text('{"backup_sha256": "x"}')
(bd / "fm1-backup-d.bin").write_bytes(bytes(R.FLASH_SIZE))
bl = S.backups(bd)
ok([b["path"].name for b in bl] == ["fm1-backup-b.bin", "fm1-backup-a.bin"] and bl[1]["kind"] == "stock"
   and S.backup_row(bl[0])[1:] == ("Wild 0.3.43", "open text") and S.backups(bd / "none") == [],
   "backups: whole ones with their description, newest first")
ok("3 outlined" in S.restore_plan_text({"sectors": [0, 1, 2], "boot": True, "firmware": "Wild"})
   and "start-up program." in S.restore_plan_text({"sectors": [0], "boot": True})
   and "already the same" in S.restore_plan_text({"sectors": [0x5000], "boot": False})
   and "not touched" in S.restore_plan_text({"sectors": [0], "boot_only": True}), "backups: the plan in one sentence")
S.mark_protected(pd, None, "not")
_os.utime(pd / "protected.json", (2.3e9, 2.3e9))
ok(S.protection(pd) == "not", "backups: M-VAVE's bootloader put back: not protected")
ok(set(S.FLOW) == {"protect", "restore", "stock"} and all(len(v) == 4 for v in S.FLOW.values()),
   "backups: each flow has its words")
ok("Put this backup back" in S.PUTBACK_GAVE_UP and "Put this backup back" in S.PROTECT_FAIL_WRITTEN
   and "Try again" not in S.PROTECT_FAIL_WRITTEN and "won't start" in S.PUTBACK_GAVE_UP and "Do not switch the FM-1 off yet" in
   S.PUTBACK_GAVE_UP and "Do not switch the FM-1 off yet" in S.PROTECT_FAIL_WRITTEN and "waits as long" in S.PUTBACK_FAILED, "put back: the words when it gave up, and to try again")
st = "off"
for present in (True, True, None, True):
    st = S.rec_step(st, present)
ok(st == "off", "REC: an FM-1 still in update mode after writing does not count as REC working")
for present in (False, None, True):
    st = S.rec_step(st, present)
ok(st == "rec" and S.rec_step("rec", False) == "rec", "REC: it must leave update mode and come back")
ok("dark" in S.REC_STEPS[1] and "PLAY" in S.REC_NOTE and "normal" in S.advice("uboot")[1],
   "REC: the screen stays dark in update mode, PLAY shows the menu")
ok("RESTARTING" in S.REC_STEPS[0], "REC: says to switch it off when it did not restart by itself")
import re as _re
_ui = " ".join(str(v) for k, v in vars(S).items() if k.isupper() and k not in ("SUBDIRS", "PROJECTS", "MAKER_TIP")
               and isinstance(v, (str, tuple, dict)))
ok(not _re.search(r"\b(Wild|NB303)\b", _ui + S.advice("running", "X")[1] + S.advice("uboot")[1] + S.advice("none")[1]
                  + " ".join(" ".join(S.plan(st, "X", sc).steps) for st in ("running", "uboot", "none")
                             for sc in (None, "normal", "safe", "dark", "loop"))),
   "texts: no firmware is favoured by name (M-VAVE's V15 only, as the factory one)")
import time  # noqa: E402
sd = Path(tempfile.mkdtemp(prefix="fm1-start-"))
st0 = S.start_state(sd)
ok((st0["backup"], st0["protect"], st0["firmware"]) == (False, False, False) and S.start_next(st0) == "backup",
   "start here: a new FM-1 begins with the backup")
(sd / "fm1-backup-1.bin").write_bytes(bytes(R.FLASH_SIZE))
(sd / "fm1-backup-1.bin.json").write_text(_json.dumps({"backup_sha256": "x", "hardware": {"bootloader": "stock"}}))
ok(S.start_next(S.start_state(sd)) == "backup", "start here: a backup that does not match its checksum does not count")
(sd / "fm1-backup-1.bin.json").write_text(_json.dumps({"backup_sha256": R.sha256(bytes(R.FLASH_SIZE)),
                                                       "hardware": {"bootloader": "stock"}}))
_os.utime(sd / "fm1-backup-1.bin", (time.time() - 50, time.time() - 50))
ok(S.start_next(S.start_state(sd)) == "protect", "start here: a saved backup ticks step 1, Protect is next")
S.start_mark(sd, "protect", "skipped")
ok(S.start_state(sd)["protect"] == "skipped" and S.start_next(S.start_state(sd)) == "firmware",
   "start here: Protect can be skipped")
S.start_mark(sd, "firmware")
ok(S.start_next(S.start_state(sd)) is None, "start here: an install from here ticks the last step")
S.mark_protected(sd, "2.4")
ok(S.start_state(sd)["protect"] is True, "start here: a protected FM-1 ticks Protect")
S.start_over(sd)
so = S.start_state(sd)
ok(not so["backup"] and not so["protect"] and not so["firmware"],
   "start here: Start over (another FM-1) counts only what happens from then on")
ok("thefirmwarehub.com" in S.HUB_PAGE and S.MAKER_TIP.startswith("From the maker") and "patreon.com" in S.MAKER_TIP_URL,
   "start here: the hub linked, the one firmware tip labelled as the maker's")

_main_src = Path(S.__file__).read_text(encoding="utf-8")
_inst = _main_src[_main_src.index("    def do_install():"):_main_src.index("        busy(True)", _main_src.index("    def do_install():"))]
ok("inst.withdraw()" in _inst and _inst.rindex("inst.withdraw()") > _inst.rindex("askyesno"),
   "install: the firmware list closes once Install is confirmed")

# ---- the big flash screen: 100 % only when it is safe to unplug, never earlier
_seq = [("start", "FM-1_900"), ("check", 3), ("loader", None)] + [("write", v) for v in (0, 37, 99, 100)] + \
       [("reboot", None), ("settle", None), ("safe", None)]
_ph = [S.flash_phase(k, v) for k, v in _seq]
ok(all(_ph) and [p[0] for p in _ph] == sorted(p[0] for p in _ph) and [p[0] for p in _ph].count(100) == 1
   and _ph[-1][0] == 100 and _ph[-1][2] == "safe" and all(p[2] == "keep" for p in _ph[:-1]),
   "flash screen (install): rises step by step, 100 % and 'safe' only at the very end")
ok(all(S.flash_phase("settle" if k == "done" else k, v) for k, v in steps),
   "flash screen (install): every step a real install sends is shown")
_rs = [("prepare", None), ("ready", None), ("backup_read", 1), ("backup_read", 2), ("backup", "x.bin")] + \
      [("write", v) for v in (0, 50, 100)] + [("done", None), ("settle", None), ("safe", True)]
_ph = [S.flash_phase(k, v, "rescue") for k, v in _rs]
ok(all(_ph) and [p[0] for p in _ph] == sorted(p[0] for p in _ph) and [p[0] for p in _ph].count(100) == 1
   and [p[2] for p in _ph[:2]] == ["wait", "wait"] and all(p[2] == "keep" for p in _ph[2:-1]),
   "flash screen (rescue): waits, then keep the cable in, 100 % and 'safe' only at the end")
ok(S.flash_phase("safe", False, "rescue")[1] == "Written and checked" and S.flash_phase("failed")[0] is None
   and S.flash_phase("failed")[2] == "ended" and S.flash_phase("hardware", {}, "rescue") is None,
   "flash screen: not confirmed, failed, and steps it does not show")
ok("safe to disconnect your FM-1" in S.UNPLUG["safe"] and "Do not unplug" in S.UNPLUG["keep"]
   and all("safe" not in S.UNPLUG[k].lower() for k in ("wait", "ended")),
   "flash screen: says it is safe to disconnect only when it is")
_slept = []
dev = T.FakeFM1(b"", identity="FM-1_846")
ok(S.settle(dev, sleep=_slept.append, wait=1) == "FM-1_846" and _slept == [S.SETTLE_SECS] and S.SETTLE_SECS >= 3,
   "settle: waits a few seconds after hello, then asks the FM-1 again")
dev.connected = False
ok(S.settle(dev, sleep=lambda s: None, wait=0.2, poll=0.05) is None, "settle: an FM-1 gone quiet is None")
_ins = _main_src[_main_src.index("    def do_install():"):_main_src.index("    def add_folder():")]
ok(_ins.index("install(backend, pkg, step") < _ins.index("settle(backend") < _ins.index('ui(flash_show, "safe")'),
   "install: safe is shown only after the install returned and the FM-1 settled")
_res = _main_src[_main_src.index("    def run_rescue("):_main_src.index("    def open_guide(")]
ok(_res.index("confirm_rescue_boot(") < _res.index("settle(backend") < _res.index('ui(flash_show, "safe"')
   and _res.count('ui(flash_show, "safe"') == 1 and "showinfo, \"FM-1 Switch\", msg" not in _res,
   "rescue: safe only after written, checked and the start waited for")

_sheets = {}
try:
    import sv_ttk as _sv
    for _d in (False, True):
        _sheets[_d] = S.png_rgba((Path(_sv.__file__).parent / "theme" /
                                  ("spritesheet_dark.png" if _d else "spritesheet_light.png")).read_bytes())[2]
except ImportError:
    pass
_w, _h, _rows = S.png_rgba(S.png_bytes(2, 1, [bytearray(b"\x01\x02\x03\x04\xff\x00\x80\x40")]))
ok((_w, _h, bytes(_rows[0])) == (2, 1, b"\x01\x02\x03\x04\xff\x00\x80\x40"), "look: a PNG made here reads back the same")
_bad = [(h, d) for d in _sheets for h, c in S.HUES.items()
        for fill in [tuple(S.tinted(_sheets[d], *S.BUTTON_SPRITES[st], 20, 20, S._rgb(c), .3)[10][40:43])
                     for st in ("rest", "hover", "pressed")]
        if S.contrast(S.soft_text(S._rgb(c), fill, d), fill) < 4.5]
ok(not _bad and S.contrast((0, 0, 0), (255, 255, 255)) > 20,
   f"look: every soft button's text reads at 4.5:1 or more, light and dark ({len(_sheets)} themes) {_bad}")

_theme = _main_src[_main_src.index("sv_ttk.set_theme("):_main_src.index("C = dict(PALETTE[dark])")]
ok('"<<ThemeChanged>>", ""' in _theme and "configure_colors" in _theme,
   "look: sv_ttk's colours applied once, so our own label colours (Muted, Badge) stay")

print("switch: all ok" if not failed else f"switch: {failed} FAILED")
sys.exit(failed != 0)
