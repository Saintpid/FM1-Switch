#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""FM-1 Switch: a small window to put any FM-1 firmware file (.fwsc) on the FM-1, Wild or NB303,
whatever its number. M-UPGRADE only takes a number higher than the one installed; this takes any
package for the FM-1 model (the update itself is fm1_install.py's: Felucca's, over USB-MIDI).

It opens on four tasks: Protect my FM-1 (m-bootloader in the boot area, fm1_rescue.py --protect),
Install firmware, Back up my FM-1 and My FM-1 won't start (the guide). Install firmware lists the .fwsc files in its folders (its own, and "Firmware Wild" / "Firmware NB303" /
"Firmware n001se" / "Firmware Original" beside it or under C:\\Projects\\M-Vave FM1 n001se
(~/M-Vave FM1 n001se on a Mac), and Downloads, if they are there; more with Add folder), newest
first, reads what the FM-1 runs, and installs the one chosen with a progress bar. M-VAVE's own
V15 (no Felucca loader) is not listed, but Through update mode… (the rescue) finds it there.

The guide tells what state the FM-1 is in (running, an install cut off, the chip's own update mode,
or nothing on USB) and what to do. M-VAVE's SMK-37 Pro / Elite, MKE-P37 and Starrykey 37 Play (the
FM-1's chip and update; not yet tried on a real keyboard) take their own firmware, M-VAVE's stock
packages included, over USB-MIDI; never an FM-1's, and Rescue refuses them. The rescue puts M-VAVE's stock firmware back on an FM-1 whose
firmware no longer starts, through the chip's own update mode (fm1_rescue.py, run as administrator
or root: the program starts itself again with --rescue for that).

  python tools/fm1_switch.pyw            (needs mido and python-rtmidi; Tk comes with Python)
  tools/build_switch_exe.bat             a FM1-Switch.exe for Windows (PyInstaller)
  tools/export_switch_mac.cmd            to the Mac, where Build_FM1Switch_Mac.command makes FM1-Switch.app
                                         and its DMG (tools/build_switch_mac.sh)
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fm1_install as I  # noqa: E402
import fm1_rescue as R  # noqa: E402

HOME = Path(r"C:\Projects") if sys.platform == "win32" else Path.home()
PROJECTS = (HOME / "M-Vave FM1 n001se", HOME / "M-Wave FM1 n001se")
SUBDIRS = ("Firmware Wild", "Firmware NB303", "Firmware n001se", "Firmware Original")


# ------------------------------------------------------------------ core ---
# (no Tk here: tests/switch_test.py drives it against a simulated FM-1)

def base_dir():
    """where the program is: the .exe's folder, the folder the .app is in, or this script's"""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    exe = Path(sys.executable).resolve()
    for p in exe.parents:               # FM1-Switch.app/Contents/MacOS/FM1-Switch
        if p.suffix == ".app":
            return p.parent
    return exe.parent


def default_folders(here=None):
    here = Path(here) if here else base_dir()
    out = [here]
    for root in (here, here.parent, *PROJECTS):
        for sub in SUBDIRS:
            p = root / sub
            if p.is_dir() and p not in out:
                out.append(p)
    dl = Path.home() / "Downloads"      # where a file from Patreon lands
    if dl.is_dir() and dl not in out:
        out.append(dl)
    return out


class Package:
    """an FM-1 or SMK-37 package; kind "felucca" (and "stock", M-VAVE's own for a keyboard) installs over
    USB-MIDI, "v15" / "other" only by Rescue"""

    def __init__(self, path, product, kind="felucca"):
        self.path, self.product, self.kind = Path(path), product, kind
        m = re.fullmatch(r"[^_]+_(\d+)", product)
        self.number = int(m[1]) if m else 0

    @property
    def model(self):
        return "FM-1" if self.kind == "v15" else I.model_of(self.product)

    @property
    def family(self):
        if self.kind in ("v15", "stock"):
            return "M-VAVE"
        if self.model != "FM-1":
            return self.model
        n = self.path.name.lower()
        return "NB303" if n.startswith("nb303") else "Wild" if n.startswith("wild") else \
            "n001se" if n.startswith("n001se") else "?"

    def label(self):
        return f"{self.path.stem}   ({'V15' if self.kind == 'v15' else self.product})" + \
            ("   rescue only" if self.kind in ("v15", "other") else "")


def package_at(f):
    """the Package for a file, or None if it is not an FM-1 (or SMK-37) package"""
    try:
        raw = Path(f).read_bytes()
        product = I.product_of(raw)
    except (OSError, I.InstallError):
        return None
    if R.sha256(raw) in R.STOCK_FM1:                          # M-VAVE's own: V15 or 016
        return Package(f, product or f"FM-1_{R.STOCK_FM1[R.sha256(raw)].lstrip('V').zfill(3)}", "v15")
    if I.keyboard_stock(raw):
        return Package(f, I.keyboard_stock(raw), "stock")
    if re.fullmatch(r"[^_]+_\d+", product) and I.model_of(product) == "FM-1":
        return Package(f, product, "felucca" if I.LOADER_MARK in raw else "other")
    if re.fullmatch(r"[^_]+_\d+", product) and I.model_of(product) in I.KEYBOARDS and I.LOADER_MARK in raw:
        return Package(f, product, "felucca")
    return None


def scan(folders, rescue_only=True):
    """every FM-1 package in the folders, the highest number first (M-VAVE's V15 and other non-Felucca
    firmware too, marked rescue only, unless rescue_only is False)"""
    seen, out = set(), []
    for d in folders:
        try:
            files = sorted(Path(d).glob("*.fwsc"))
        except OSError:
            continue
        for f in files:
            key = f.resolve()
            if key in seen:
                continue
            seen.add(key)
            p = package_at(f)
            if p and (rescue_only or p.kind in ("felucca", "stock")):
                out.append(p)
    out.sort(key=lambda p: (p.kind in ("v15", "stock"), p.model != "FM-1", -p.number, p.path.name))
    return out


def rescue_choices(folders):
    """what Rescue can put back: V15 first (None: download it from M-VAVE), then the rest (FM-1 only)"""
    pk = [p for p in scan(folders) if p.model == "FM-1"]
    v15 = [p for p in pk if p.kind == "v15"][:1]
    return (v15 or [None]) + [p for p in pk if p.kind != "v15"]


def device_text(backend):
    """what the FM-1 runs ("FM-1_846"), or None"""
    up = I.Updater(backend)
    dev = up.find()
    if not dev:
        return None
    dev.link.close()
    return dev.id.text + ("  (in update mode: install again to finish)" if dev.id.loader else "")


def install(backend, pkg, step, port=None):
    """the whole update; step(kind, value) as fm1_install's Progress gets it. -> the identity after"""
    if pkg.kind not in ("felucca", "stock"):
        raise I.InstallError("badpkg", f"{pkg.path.name} has no Felucca update loader, so it cannot be "
                                       "installed over USB-MIDI here: use M-UPGRADE, or Install firmware… "
                                       "and Through update mode…, which puts any FM-1 firmware back")
    product, image = I.load_package(str(pkg.path), False)
    up = I.Updater(backend, port=port)
    dev = up.find()
    if not dev:
        raise I.not_found(up)
    return up.install(dev, image, product, step)


def flash_support(pkg, fid=R.UCUN_FLASH):
    """("supported" | "unsupported" | "unknown", reason) of an FM-1 package for a flash part (cached)"""
    cache = pkg.__dict__.setdefault("_flash", {})
    if fid not in cache:
        try:
            image = R.package_flash_image(pkg.path.read_bytes())[:R.APP_END]
            cache[fid] = R.flash_support(image, fid, pkg.kind)
        except (OSError, R.Fail) as e:
            cache[fid] = ("unknown", f"the package could not be read ({e})")
    return cache[fid]


def last_hardware(d):
    """the newest Hardware / backup identification saved in folder d: (info, when) or None"""
    best = None
    try:
        files = list(Path(d).glob("fm1-hardware-*.json"))
    except OSError:
        return None
    for f in files:
        try:
            info = json.loads(f.read_text(encoding="utf-8"))["hardware"]
            when = f.stat().st_mtime
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(info, dict) and (best is None or when > best[1]):
            best = (info, when)
    return best


def flash_warning(pkg, hardware=None):
    """Before a USB-MIDI install: the running firmware does not tell its flash part, so a package
    whose exact-ID gate refuses UCUN UC25HQ80 gets a warning (None when nothing is known against it)"""
    if pkg.model != "FM-1" or pkg.kind not in ("felucca", "v15"):
        return None
    support, why = flash_support(pkg)
    if support != "unsupported":
        return None
    text = (f"{pkg.path.name} ({pkg.product}) does not work with the newer FM-1 memory chip (UCUN, "
            f"B36014): {why}.\n\n"
            "FM-1s sold since autumn 2026 have this chip. On such an FM-1, this firmware forgets what you "
            "save when you switch it off, and the update may stop halfway. Choose a version whose maker says "
            "it works with UCUN.")
    if hardware:
        info, when = hardware
        text += ("\n\nThe last FM-1 checked here "
                 f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(when))}) has "
                 f"{chip_text(info)}.")
    return text + ("\n\nThe FM-1 cannot tell over USB which chip it has. Install anyway only if your FM-1 has "
                   "the original chip (Puya, 856014). Back up my FM-1 shows which one it has.")


STEP_TEXT = {"start": "Starting the update on {}", "check": "Checking the file ({} reads)",
             "loader": "Getting the FM-1 ready to receive it", "write": "Copying to the FM-1: {}%. Keep the cable in.",
             "reboot": "Restarting the FM-1", "done": "Done: the FM-1 now runs {}"}


def later(after, f, *a, **kw):
    """f(*a, **kw) on Tk's thread (after() itself passes no keywords: bar.configure(value=...))"""
    after(0, lambda: f(*a, **kw))


def confirm_rescue_boot(backend, product, port=None, secs=40, poll=0.5):
    """Read identity only: a verified flash is not proof that its firmware started."""
    if backend is None:
        return None
    end = time.monotonic() + secs
    while True:
        dev = I.Updater(backend, port=port).find(only_port=False)
        if dev:
            dev.link.close()
            if not dev.id.loader:
                return dev.id.text
        if time.monotonic() >= end:
            return None
        time.sleep(poll)


def rescue_result(product, identity):
    if identity == product:
        return True, f"It worked: the FM-1 runs {identity} again."
    if identity:
        return False, f"The firmware was written and checked, but the FM-1 says it runs {identity}, not {product}."
    return False, ("The firmware was written and checked, but the FM-1 has not said hello yet. "
                   "Switch it off, wait 10 seconds and switch it on. If you used an adapter, unplug it and "
                   "connect the FM-1 straight to the computer. Still nothing? Open My FM-1 won't start.")


def install_error_text(error):
    text = str(error)
    if error.code in ("stopped", "lost", "noloader"):
        text += ("\n\nNewer FM-1s have a different memory chip (UCUN). Some older firmware does not know "
                 "it: then updates fail, or what you save is gone after switching off. This error alone "
                 "does not tell which chip yours has: Back up my FM-1 finds out (and saves a backup). "
                 "Use a firmware whose maker says it works with UCUN.")
    return text


HUB_CREDIT = "(Firmware names from The Firmware Hub, thefirmwarehub.com, CC BY 4.0.)"
CHIP_NAMES = {"856014": "the original FM-1 memory chip (Puya)", "B36014": "the newer FM-1 memory chip (UCUN)"}


def hardware_text(info):
    found = ""
    if info.get("bootloader_text"):
        found = (f"Bootloader: {info['bootloader_text']}.\n"
                 f"Firmware: {info.get('firmware', 'unknown')}"
                 + ("" if info.get("firmware_status") in (None, "ok") else f" ({info['firmware_status'].replace('_', ' ')})")
                 + ".\n" + (HUB_CREDIT + "\n" if info.get("firmware_hub") else ""))
    fid = str(info.get("jedec_id", "")).upper()
    return (f"Memory chip: {CHIP_NAMES.get(fid, info.get('flash_name', 'unknown'))}.\n" + found
            + f"\nDetails: {info.get('flash_name')}, ID {info.get('jedec_id')}, chip key {info.get('chip_key')}, "
            f"flash type {info.get('flash_type')}.")


UPDATE_MODE_KEYS = ("If it does not show up: switch the FM-1 off. If it is protected, hold REC while you "
                    "switch it on. Some firmwares also open it when you hold OCT− and OCT+ for 5 seconds.")


def send_update_mode(backend, port=None):
    """the soft key to an FM-1 that answers on MIDI -> its identity text, or None (none answered)"""
    if backend is None:
        return None
    try:
        return I.enter_update_mode(I.Updater(backend, port=port))
    except I.InstallError:
        return None


def step_text(kind, value=None):
    return STEP_TEXT[kind].format(value if value is not None else "")


NO_MIDI = ("This copy of FM-1 Switch cannot talk to the FM-1 over USB ({}). Use the installed app "
           "(FM1-Switch.exe or the Mac app), which has everything built in. (Running from the source: "
           "pip install -r requirements.txt.)")


def open_midi(make=None):
    """(backend, None), or (None, why) when mido or its MIDI backend (python-rtmidi) is missing: mido
    imports without it and only fails at the first port list, which looked like no FM-1"""
    try:
        b = (make or I.MidoBackend)()
        b.input_names()
        return b, None
    except Exception as e:
        return None, str(e) or type(e).__name__


def row_values(p):
    """the firmware list's columns for a Package: family, file, identity, note"""
    return (p.family, p.path.stem, "V15" if p.kind == "v15" else p.product,
            {"felucca": "", "stock": "stock"}.get(p.kind, "rescue only"))


def keyboard(ident):
    """the M-VAVE keyboard model (SMK-37 Pro...) an identity is from, or None for an FM-1"""
    m = I.model_of(ident or "")
    return m if m in I.KEYBOARDS else None


KEYBOARD_NOTE = ("M-VAVE keyboards are new here and not yet tried on a real one. FM-1 firmware cannot go "
                 "on a keyboard, and the rescue is for the FM-1 only.")


STATE_TONE = {"running": "ok", "loader": "warn", "uboot": "warn", "none": "off"}


# ------------------------------------------------------------- diagnosis ---

def diagnose(backend, probe=R.uboot_present, secs=8.0, poll=0.5, port=None):
    """-> (state, identity): "running" / "loader" (an install cut off) with the identity, "uboot"
    (the chip's own update mode), or "none". Watches for a few seconds: a crash-looping FM-1 is in
    UBOOT only now and then."""
    end = time.monotonic() + secs
    while True:
        if backend is not None:
            dev = I.Updater(backend, port=port).find()
            if dev:
                dev.link.close()
                return ("loader" if dev.id.loader else "running"), dev.id.text
        if probe is not None and probe():
            return "uboot", None
        if time.monotonic() >= end:
            return "none", None
        time.sleep(poll)


def advice(state, ident=None, probe_ok=True):
    """(headline, what to do) for a diagnosis"""
    kb = keyboard(ident)
    if kb and state in ("running", "loader"):
        return (f"{kb}: " + (f"runs {ident}" if state == "running" else f"in update mode ({ident})"),
                f"Choose a firmware for the {kb} (M-VAVE's own stock file, or a port made for it) and "
                "press Install" + (" to finish the install that was cut off. " if state == "loader" else ". ")
                + KEYBOARD_NOTE)
    if state == "running":
        return (f"FM-1: connected, running {ident}",
                "All good. Install firmware… puts another firmware on it. If its screen freezes: some "
                "firmwares open update mode when you hold OCT− and OCT+ for 5 seconds.")
    if state == "loader":
        return (f"FM-1: waiting for a firmware ({ident})",
                "An install was stopped halfway, and the FM-1 is waiting for it. Nothing is broken: open "
                "Install firmware…, pick a firmware (the same one is fine) and press Install.")
    if state == "uboot":
        return ("FM-1: in update mode (screen dark)",
                "Update mode is a special mode for loading software, and the screen is dark in it. After "
                "holding REC, a backup or an install, that is normal: switch the FM-1 off, wait 10 seconds and "
                "switch it on. If it ended up here by itself, its firmware does not start: open My FM-1 "
                "won't start.")
    return ("FM-1: not found",
            "1. Plug the FM-1 straight into the computer (no hub) with a cable that carries data (some "
            "only charge). Switch it on. Close other music apps. 2. Shows a picture but no answer? Some "
            "firmwares answer after you hold OCT− and OCT+ for 5 seconds; then press Check again. 3. Screen dark, or it keeps restarting? "
            "Open My FM-1 won't start."
            + ("" if probe_ok else " (This computer cannot see update mode: pyusb is missing.)"))


# ----------------------------------------------------------------- guide ---
# The unbrick guide: what the FM-1 says first; the user's answers only where it says nothing; then
# one thing to try, behind one button.

SCREEN = (("normal", "Its usual picture (its firmware runs)"),
          ("safe", "The words \"SAFE MODE\""),
          ("update", "The words \"UPDATE MODE\""),
          ("dark", "Nothing: the screen stays dark"),
          ("loop", "It starts, goes black, starts again, over and over"))
LAST = (("cut", "An install stopped halfway (cable out, computer went to sleep, app closed)"),
        ("nostart", "I installed a firmware, and now it does not start"),
        ("other", "Nothing special, or I don't know"))


class Plan:
    """what to do: a title, the steps for the user, and what the button does
    (action: "install", "finish", "check", "rescue" or "hardware"; button: its text)"""

    def __init__(self, action, title, steps, button):
        self.action, self.title, self.steps, self.button = action, title, list(steps), button


CABLE = ("Plug the FM-1 straight into the computer, no hub. Many USB cables only charge: if in doubt, "
         "try another cable and another USB port.",
         "Close other apps that may be talking to the FM-1: music programs, M-UPGRADE, a browser tab "
         "with a web installer.")
TRANSPORTER = "https://github.com/kurogedelic/FM-1-transporter"


def needs_questions(state):
    """only when the FM-1 says nothing does the guide ask"""
    return state == "none"


def plan(state, ident=None, screen=None, last=None, rescue_failed=False):
    """the Plan for what was found (diagnose()) and what the user answered (SCREEN, LAST)"""
    if rescue_failed:
        return Plan("hardware", "The FM-1 never showed up in update mode",
                    ["First: charge the FM-1 for half an hour, then try once more.",
                     "If it still does not show up, it cannot get into update mode by itself. Then it needs "
                     "a small helper board, an FM-1-transporter: a Seeed XIAO RP2040 joined to the FM-1's USB "
                     "lines with three wires (D+, D−, GND; never 5 V). It needs some soldering.",
                     "The board opens update mode from outside. Then the firmware is put back just as "
                     "carefully: a backup first, the start-up part never touched.",
                     "With the board wired and plugged in: Try with the adapter (experimental). It has not "
                     "been tried on a real FM-1 from this app yet."],
                    "Open the FM-1-transporter guide")
    kb = keyboard(ident)
    if kb and state in ("running", "loader"):
        return Plan("install" if state == "running" else "finish",
                    f"The {kb} works: it runs {ident}" if state == "running"
                    else f"An install stopped halfway: the {kb} is waiting for it",
                    [f"Choose a firmware for the {kb} in the list (M-VAVE's own stock file, or a port made "
                     f"for the {kb}) and press Install. Do not unplug it while it writes.", KEYBOARD_NOTE],
                    "Install the chosen file" if state == "running" else "Finish the install")
    if state == "running":
        return Plan("install", f"Good news: the FM-1 works (it runs {ident})",
                    ["Nothing is broken. To put another firmware on it, pick one in the list and press "
                     "Install.",
                     "If its screen freezes: some firmwares open update mode when you hold OCT− and OCT+ for 5 "
                     "seconds, and Install still works from there."],
                    "Install the chosen file")
    if state == "loader":
        return Plan("finish", "An install stopped halfway: the FM-1 is waiting for it",
                    ["Nothing is broken. The FM-1 waits until an install is finished.",
                     "Pick a firmware (the same one is fine) and press the button: the install starts over. "
                     "Keep the cable in until it is done."],
                    "Finish the install")
    if state == "uboot":
        return _rescue("The FM-1 is in update mode (its screen is dark)",
                       ["If you just held REC, made a backup or installed something, that is normal: switch "
                        "the FM-1 off, wait 10 seconds and switch it on. Nothing more to do.",
                        "If it ends up here every time you switch it on, its firmware does not start. Then "
                        "the steps below put a working firmware back. Everything on it is saved first."])
    if screen in ("normal", "safe", "update"):
        what = {"normal": "It runs", "safe": "It is in its firmware's safe mode (USB on, no sound)",
                "update": "It is in update mode"}[screen]
        return Plan("check", f"{what}, but the computer cannot hear it",
                    [*CABLE, "Switch the FM-1 off and on, then press Check again."
                     + (" When it is found, Install finishes the update." if screen == "update" else "")],
                    "Check again")
    if screen == "dark":
        return _rescue("The screen stays dark",
                       ["Charge it first: 10 minutes on a charger. An empty battery looks just like this.",
                        CABLE[0],
                        "Still dark? Then its firmware does not start. The steps below catch it in update "
                        "mode and put a working firmware back"
                        + (" (a firmware that keeps crashing ends up there by itself)." if last != "other"
                           else ".")])
    if screen == "loop":
        return _rescue("Its firmware crashes while it starts",
                       ["After a few crashes in a row, the FM-1 goes into update mode by itself. The steps "
                        "below wait for that moment and put a working firmware back."])
    return Plan("check", "The FM-1 was not found", [*CABLE, "Then press Check again."], "Check again")


def _rescue(title, why):
    return Plan("rescue", title,
                [*why,
                 "Press Try to unbrick and pick the firmware to put back. M-VAVE's own V15 is the safest; "
                 "any FM-1 firmware works. The computer then asks for " + ("permission (Windows' "
                                                                           "administrator window)" if sys.platform == "win32"
                                                                           else "your password") + ".",
                 "Switch the FM-1 off and on (or let it keep restarting). When its screen goes black, it is "
                 "caught. Copying takes about a minute: keep the cable in.",
                 "Then it runs the firmware you picked. From V15 you can install any other firmware as usual."],
                "Try to unbrick")


# ---------------------------------------------------------------- rescue ---

def rescue_dir(here=None):
    """where V15, the loader and the backups are kept: beside the program, or in the home folder
    when that is not writable (an app in /Applications)"""
    for d in ((Path(here) if here else base_dir()) / "FM-1 Rescue", Path.home() / "FM-1 Rescue"):
        try:
            d.mkdir(exist_ok=True)
            probe = d / ".w"
            probe.write_bytes(b"")
            probe.unlink()
            return d
        except OSError:
            continue
    return Path(tempfile.mkdtemp(prefix="fm1-rescue-"))


def app_version():
    for p in (Path(getattr(sys, "_MEIPASS", "")) / "VERSION", Path(__file__).resolve().parent.parent / "VERSION"):
        try:
            return p.read_text().strip()
        except OSError:
            continue
    return ""


DISCLAIMER = """FM-1 Switch is free, open-source software (GPL-3.0), made by a hobbyist and \
given away as it is.

Everything you do with it is AT YOUR OWN RISK. Installing or rescuing firmware can fail, and \
can leave your FM-1 unusable for good. There is NO WARRANTY of any kind, and neither \
Noisebud nor the author nor anyone who contributed to it is responsible or liable for \
anything that happens to your FM-1, your computer, your files or anything else, however it \
happens.

FM-1 Switch is not made, approved or supported by M-VAVE or JieLi. Do not use it if you do not \
accept this."""

RESCUE_RISK = ("I understand that this changes my FM-1's memory at my own risk, that it could stop the FM-1 "
               "from working for good, and that nobody (Noisebud, the author, M-VAVE) is responsible.")


def accepted(d):
    try:
        return (Path(d) / "accepted-disclaimer.txt").read_text().strip() == "yes"
    except OSError:
        return False


def accept(d):
    try:
        (Path(d) / "accepted-disclaimer.txt").write_text("yes\n")
    except OSError:
        pass


def _sysctl_int(name):
    """a Mac sysctl integer (hw.optional.arm64, sysctl.proc_translated), or None"""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        v, n = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        if libc.sysctlbyname(name.encode(), ctypes.byref(v), ctypes.byref(n), None, ctypes.c_size_t(0)) != 0:
            return None
        return v.value
    except (OSError, AttributeError):
        return None


def mac_hardware_arch(sysctl=_sysctl_int):
    """the Mac's own CPU, also when this process runs under Rosetta: arm64 or x86_64"""
    return "arm64" if sysctl("hw.optional.arm64") == 1 else "x86_64"


def native_prefix(platform=sys.platform, sysctl=_sysctl_int):
    """on a Mac, /usr/bin/arch with the hardware's own slice: the rescue helper runs native even when the
    window was started translated (a universal app otherwise inherits its parent's choice)"""
    if platform != "darwin":
        return []
    return ["/usr/bin/arch", "-" + mac_hardware_arch(sysctl)]


def native_probe(sysctl=_sysctl_int):
    """--native-process-probe, for the Mac release gate (verify_native_mac.py): what this process is"""
    import platform as P
    hw = mac_hardware_arch(sysctl)
    arch = {"aarch64": "arm64", "amd64": "x86_64"}.get(P.machine().lower(), P.machine())
    translated = sysctl("sysctl.proc_translated") == 1
    return {"product": "FM-1 Switch", "version": app_version(), "executable": sys.executable,
            "pid": os.getpid(), "architecture": arch, "hardwareArchitecture": hw,
            "translated": translated, "native": arch == hw and not translated}


def helper_command(args, platform=sys.platform, sysctl=_sysctl_int):
    """this program with --rescue: the .exe / the .app itself, or Python with this script; on a Mac
    started with the hardware's own architecture"""
    me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
    return native_prefix(platform, sysctl) + me + ["--rescue", *args]


def rescue_args(target, d, progress, any_firmware=False, via="usb", inspect=False, allow_ucun=False):
    # over USB, M-Flasher's loader (checked copies in d); JieLi's through the transporter
    loader = ["--mflr-dir", str(d)] if via == "usb" else ["--loader", str(d / R.LOADER_NAME)]
    a = (["--inspect"] if inspect else [str(target), "--write"]) + ["--backup-dir", str(d), *loader,
         "--wait", "300", "--tries", "5", "--progress", str(progress)]
    if allow_ucun and not inspect:
        a.append("--allow-ucun-write")
    if any_firmware and not inspect:
        a.append("--any-firmware")
    if via != "usb":
        a += ["--via", via]
    if hasattr(os, "getuid"):
        a += ["--owner", f"{os.getuid()}:{os.getgid()}"]
    return a


def protect_args(d, progress, mode="protect", backup=None):
    """the helper's arguments for Protect my FM-1 (mode "protect"), putting a backup back ("restore") or
    M-VAVE's bootloader from one ("stock"): it backs up, plans, waits for the window's answer (R.answer)
    and only then writes"""
    what = ["--protect"] if mode == "protect" else ["--restore", str(backup)] + (["--boot-only"] if mode == "stock" else [])
    a = what + ["--ask", "--backup-dir", str(d), "--mflr-dir", str(d),
                "--wait", "300", "--tries", "3", "--progress", str(progress)]
    if hasattr(os, "getuid"):
        a += ["--owner", f"{os.getuid()}:{os.getgid()}"]
    return a


# ------------------------------------------------------- protect my FM-1 ---
# The rescue bootloader (m-bootloader) in seven screens: why, connect, update mode, backup, review,
# writing, try REC. The helper (fm1_rescue.py --protect --ask) does the device side; these are its words.

PROTECT_WHY = ("Think of it as a spare key. When the FM-1 starts, a tiny start-up program (the "
               "bootloader) runs first. This replaces M-VAVE's with one that has a way in: hold REC while you "
               "switch the FM-1 on, and it is ready for new firmware, even if the firmware is broken. (Only "
               "a damaged start-up program itself, for example from a power cut while writing it, would "
               "need more help.)",
               "If a firmware is damaged, the new start-up program does not run it. The FM-1 waits for a new "
               "one instead of getting stuck.",
               "Only that start-up program changes: a tiny part at the start of the memory. Your firmware, "
               "sounds, patterns and settings stay exactly as they are.",
               "Everything on the FM-1 is copied to this computer first, so it can always be put back.")
PROTECT_CREDIT = ("The new start-up program is m-bootloader " + R.MBOOT_VERSION + " by Julius Vitkauskas "
                  "(MIT licence). FM-1 Switch downloads it and checks that it is the right file.")
MBOOT_PAGE = "https://github.com/jvitkauskas/m-bootloader"
PROTECT_TICK = "I will keep the cable in and the FM-1 switched on for the next 10 seconds."
UCUN_NOTE = ("This FM-1 has the newer memory chip (UCUN). Writing to it is still new here. One early FM-1 "
             "with this chip did not start after an old version of the start-up program (cause unknown); "
             "many have worked since. As long as the FM-1 can reach update mode, your backup can put "
             "everything back.")
PROTECT_STEP = {"ready": "Thanks. Putting the FM-1 into update mode (its screen goes dark)…",
                "hardware": "Found the FM-1. Copying its memory…",
                "backup_read": "Copying the FM-1's memory ({} of 2)…",
                "analysis": "Saving the backup…",
                "backup": "Checking the saved copy…",
                "write": "Writing… {}%. Keep the cable in.",
                "done": "Done and checked."}
MAP_COLORS = {"boot": "#6f42c1", "firmware": "#0969da", "settings": "#1a7f37", "pairings": "#bf8700",
              "calibration": "#cf222e", "presets": "#2da44e", "sequences": "#1b9aaa", "sounds": "#d4a72c",
              "mac": "#cf222e", "free": "#d0d7de"}


def chip_text(info):
    """the flash chip, in plain words"""
    fid = str(info.get("jedec_id", "")).upper()
    return CHIP_NAMES.get(fid, f"{info.get('flash_name', 'unknown chip')} ({fid or '?'})")


def found_text(hardware, analysis):
    """what the backup found, as the guide shows it"""
    lines = [f"Memory chip: {chip_text(hardware)}."]
    if analysis:
        lines.append(f"Start-up program (bootloader): {analysis.get('bootloader_text', 'unknown')}.")
        fw = analysis.get("firmware", "unknown")
        st = analysis.get("firmware_status")
        lines.append(f"Firmware: {fw}" + ("" if st in (None, "ok", "not_found") else
                                          f" ({analysis.get('firmware_status_text', st)})") + ".")
        if analysis.get("firmware_hub"):
            lines.append(HUB_CREDIT)
    return "\n".join(lines)


def plan_text(p):
    """the review screen's sentence for the helper's plan"""
    n = len(p.get("sectors") or [])
    text = (f"Only the start-up program is written: the {n} outlined squares at the top left of the map, "
            "out of 256. Your firmware, sounds, patterns and settings are not touched.")
    if not p.get("firmware_ok", True):
        text += (" Note: the firmware on this FM-1 is damaged. The new start-up program will not run it; the "
                 "FM-1 will wait in update mode for a new firmware.")
    return text


def last_backup(d):
    """the newest backup's description saved in folder d (fm1-backup-*.bin.json): (info, when) or None"""
    best = None
    try:
        files = list(Path(d).glob("fm1-backup-*.bin.json"))
    except OSError:
        return None
    for f in files:
        try:
            info = json.loads(f.read_text(encoding="utf-8"))["hardware"]
            when = f.stat().st_mtime
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(info, dict) and (best is None or when > best[1]):
            best = (info, when)
    return best


def backups(d):
    """the backups saved in folder d, newest first: dicts with path, when, firmware, bootloader (its text),
    kind ("stock", "open", ...) and flash; only those with their checksum beside them"""
    out = []
    try:
        files = list(Path(d).glob("fm1-backup-*.bin"))
    except OSError:
        return out
    for f in files:
        try:
            meta = json.loads(Path(str(f) + ".json").read_text(encoding="utf-8"))
            hw = meta.get("hardware") or {}
            if f.stat().st_size != R.FLASH_SIZE or not meta.get("backup_sha256"):
                continue
            out.append({"path": f, "when": f.stat().st_mtime, "firmware": hw.get("firmware", "unknown"),
                        "bootloader": hw.get("bootloader_text", "unknown"), "kind": hw.get("bootloader"),
                        "flash": hw.get("flash_name", "")})
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(out, key=lambda b: b["when"], reverse=True)


def backup_row(b):
    return (time.strftime("%Y-%m-%d %H:%M", time.localtime(b["when"])), b["firmware"], b["bootloader"])


def protection(d):
    """"protected", "not" or None (never read): from the newest backup or protect run in folder d"""
    b = last_backup(d)
    stamp = Path(d) / "protected.json"
    try:
        p = json.loads(stamp.read_text(encoding="utf-8"))
        pw = stamp.stat().st_mtime
    except (OSError, ValueError):
        p, pw = None, 0
    if p and (b is None or pw >= b[1]):
        return "not" if p.get("state") == "not" else "protected"
    if b is None:
        return None
    return "protected" if b[0].get("bootloader") == "open" else "not"


def mark_protected(d, version, state="protected"):
    """after a verified install (or a backup put back: its own state): what the start screen shows"""
    try:
        (Path(d) / "protected.json").write_text(json.dumps({"version": version, "state": state,
                                                            "when": time.time()}), encoding="utf-8")
    except OSError:
        pass


def map_legend(regions):
    """[(region, its name, its colour)] for the regions a 256-sector map holds, in flash order"""
    seen = []
    for r in regions:
        if r not in seen:
            seen.append(r)
    return [(r, R.FL.REGION_TEXT.get(r, r), MAP_COLORS.get(r, MAP_COLORS["free"])) for r in seen]


REC_STEPS = ("Switch the FM-1 off. (If its screen still says RESTARTING, that is fine: it does not always "
             "restart by itself.)",
             "Hold REC and switch it on. The screen stays dark: that is how update mode looks. This window "
             "shows a tick when it sees the FM-1 there. Then let go of REC.",
             "Switch it off, wait 10 seconds and switch it on without holding anything. Your firmware starts "
             "as usual.")
REC_NOTE = ("Want to see it on the FM-1's own screen? Hold PLAY instead of REC while you switch it on: a menu "
            "appears (M-VAVE's original start-up program has none). Choose Boot app, or switch it off and on.")
PROTECT_FAIL_WRITTEN = ("Do not switch the FM-1 off yet, and keep the cable in: it is most likely still waiting in "
                        "update mode. Press Put this backup back: it makes the FM-1 exactly as it was just "
                        "before (running it all again would not work now). Only if FM-1 Switch cannot find "
                        "the FM-1: switch it off, hold REC while you switch it on, and press Put this backup "
                        "back again. If that does not work either, open My FM-1 won't start. The backup made "
                        "just before writing:")

PUTBACK_FAILED = ("Something went wrong while writing, and putting things back as they were did not finish "
                  "either ({n} small part{s} left). Keep the cable in and the FM-1 switched on: it waits as long "
                  "as this page is open. Check that the cable sits firmly (no hub), then press Try again.")

PUTBACK_GAVE_UP = ("Not everything could be put back as it was. Do not switch the FM-1 off yet, and keep the "
                   "cable in: it is most likely still waiting in update mode. Press Put this backup back now. "
                   "Only if FM-1 Switch cannot find the FM-1: switch it off, hold REC while you switch it on, "
                   "and press Put this backup back again. If that does not work either, open My FM-1 won't "
                   "start. The backup made just before writing:")

TASK_TEXT = {"protect": "Give the FM-1 a spare key: hold REC while you switch it on, and new firmware can go "
                        "on even if the old one breaks. Make a backup first.",
             "install": "Put a new firmware on the FM-1, over the USB cable.",
             "backup": "Copy everything on the FM-1 to this computer: firmware, sounds, patterns and settings. "
                       "Put it back later if anything goes wrong.",
             "guide": "The FM-1 does not start, or behaves strangely? A guide finds out what is wrong and what "
                      "to try, one step at a time."}
INSTALL_HELP = ("Pick a firmware in the list and press Install. The FM-1 should be switched on and running. "
                "If its firmware does not start at all, use Through update mode… instead.")
BACKUP_TEXT = ("FM-1 Switch puts the FM-1 into update mode (its screen goes dark), copies everything in its "
               "memory twice, checks that both copies match, and saves them as one backup file. Nothing on "
               "the FM-1 is changed.\n\n"
               "The computer will ask for permission first. When it is done, switch the FM-1 off, wait 10 "
               "seconds and switch it on. Keep backups to yourself: they hold your FM-1's own data.")

FLOW = {"protect": {"title": "Protect my FM-1", "install": "Install", "write": "Writing the start-up program",
                    "screen": "Protect my FM-1"},
        "restore": {"title": "Put a backup back", "install": "Put it back", "write": "Putting the backup back",
                    "screen": "Put a backup back"},
        "stock": {"title": "Put M-VAVE's start-up program back", "install": "Put it back",
                  "write": "Writing the start-up program", "screen": "M-VAVE's bootloader back"}}
RESTORE_WHY = ("This makes the FM-1 exactly as it was when the backup was made: the firmware, its sounds, "
               "patterns and settings, and the start-up program.",
               "Anything you saved on the FM-1 after that backup is replaced. So what is on it now is "
               "backed up first, and you can go back to it.",
               "Only backups of this very FM-1 can go back on it. Another FM-1's backup is refused.")
STOCK_WHY = ("This puts M-VAVE's original start-up program (bootloader) back, taken from a backup made "
             "before Protect my FM-1. Your firmware, sounds, patterns and settings stay.",
             "Afterwards, holding REC no longer opens update mode. (An official M-VAVE update does the same.)",
             "What is on the FM-1 now is backed up first.")


START_INTRO = ("Just got an FM-1 and new to all this? Do these three things, in this order. Each one tells "
               "you what to do. You can close this window and come back any time: it remembers how far "
               "you got.")
START_STEPS = (
    ("backup", "Make a backup",
     "Copies everything on your FM-1 to this computer: its firmware, sounds, patterns and settings. "
     "Whatever happens later, this puts it back exactly as it is today. Nothing on the FM-1 is changed. "
     "Afterwards, copy the backup files somewhere else too (a USB stick, a cloud folder)."
     + (" (If Windows says access is denied, its ransomware protection blocks the app: allow FM1-Switch.exe "
        "under Windows Security > Ransomware protection > Allow an app.)" if sys.platform == "win32" else ""),
     "Back up now…"),
    ("protect", "Give it a spare key",
     "This is the one step that writes to the FM-1: only its tiny start-up program, in a few seconds. "
     "Afterwards, holding REC while you switch it on lets new firmware go on even if a firmware breaks "
     "(only a power cut while this step writes could need more help). Use a cable straight into the computer (no hub), and charge the FM-1 first. You can skip "
     "this, but then a broken firmware is much harder to fix.",
     "Protect…"),
    ("firmware", "Put on a firmware",
     "Now you can try any FM-1 firmware. Pick one in the list and press Install. Don't like it? Install "
     "another one, or put your backup back. If the FM-1 ever stops starting, hold REC while you switch it "
     "on, then install again.",
     "Choose firmware…"))
HUB_PAGE = "https://thefirmwarehub.com"
HUB_LINK = "Where to find firmwares: The Firmware Hub, thefirmwarehub.com"
# the one place a firmware is named on its own: a tip from the app's maker, labelled as such
MAKER_TIP = "From the maker of FM-1 Switch: Wild, great if you like dark techno."
MAKER_TIP_URL = "https://www.patreon.com/Noisebud/posts/noisebud/173838561/"
START_AFTER = ("After a backup or Protect the FM-1's screen stays dark (update mode). Switch it off, wait 10 "
               "seconds and switch it on: then it starts as usual.")
START_DONE = "All three done. Have fun! (This window stays here if you want to look again.)"


_CHECKED = {}


def _backup_ok(b):
    """a backup file that still matches its checksum (read once per file and time)"""
    key = (str(b["path"]), b["when"])
    if key not in _CHECKED:
        try:
            R.read_backup(str(b["path"]))
            _CHECKED[key] = True
        except R.Fail:
            _CHECKED[key] = False
    return _CHECKED[key]


def start_state(d):
    """the Start here guide's progress, from what is really there since it was started (Start over: for
    another FM-1): a backup that matches its checksum, the FM-1 protected (or the step skipped), a
    firmware installed from here -> {step: True/False/"skipped", "since": time, "backup_when": time}"""
    try:
        seen = json.loads((Path(d) / "start-here.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    seen = seen if isinstance(seen, dict) else {}
    since = seen.get("since", 0)
    good = [b for b in backups(d) if b["when"] >= since and _backup_ok(b)]
    try:  # its own time (a file's mtime can run ahead of time.time() on Windows)
        pf = Path(d) / "protected.json"
        pw = json.loads(pf.read_text(encoding="utf-8")).get("when") or pf.stat().st_mtime
    except (OSError, ValueError, AttributeError):
        pw = 0
    prot = protection(d) == "protected" and (pw > since or bool(good) and good[0]["kind"] == "open")
    return {"backup": bool(good),
            "protect": True if prot else "skipped" if seen.get("protect") == "skipped" else False,
            "firmware": bool(seen.get("firmware")), "since": since,
            "backup_when": good[0]["when"] if good else None}


def start_over(d):
    """Start here for another FM-1: only what happens from now on counts"""
    try:
        (Path(d) / "start-here.json").write_text(json.dumps({"since": time.time()}), encoding="utf-8")
    except OSError:
        pass


def start_mark(d, step, value=True):
    try:
        f = Path(d) / "start-here.json"
        try:
            seen = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            seen = {}
        seen[step] = value
        f.write_text(json.dumps(seen), encoding="utf-8")
    except OSError:
        pass


def start_next(st):
    """the first step not done (a skipped Protect counts as done), or None"""
    for k, *_ in START_STEPS:
        if not st.get(k):
            return k
    return None


def rec_step(st, present):
    """Try it: hold REC. "off": waiting for the FM-1 to leave update mode (after writing it may stay
    there until switched off), "on": waiting for it to come back with REC held, "rec": it did. An FM-1
    that never left update mode does not pass (present: True, False, or None = cannot tell)"""
    if st == "off":
        return "on" if present is False else "off"
    if st == "on":
        return "rec" if present else "on"
    return st


def restore_plan_text(p):
    """the review screen's sentence for a backup put back"""
    n = len(p.get("sectors") or [])
    if p.get("boot_only"):
        return (f"Only the start-up program is written: the {n} outlined squares on the map, out of 256. "
                "Your firmware, sounds, patterns and settings are not touched.")
    return (f"The {n} outlined squares on the map (out of 256) are what differs from the backup. They are "
            f"written so that the FM-1 holds exactly what the backup holds: {p.get('firmware', 'its firmware')} "
            "and its sounds, patterns and settings"
            + (", and the start-up program." if p.get("boot") else ". The start-up program is already the same."))


BACKUPS_HELP = ("A backup is a full copy of everything on the FM-1, saved on this computer (in the FM-1 Rescue "
                "folder). Make one before you try something new. Put back… makes the FM-1 exactly as it was "
                "when that backup was made. M-VAVE's bootloader back… undoes Protect my FM-1. Keep backups to "
                "yourself: they hold your FM-1's own data.")

PROTECTION_TEXT = {"protected": "Protected: if anything goes wrong, hold REC while you switch it on.",
                   "not": "Not protected yet. New here? Press Start here.",
                   None: "Not known yet if it is protected. New here? Press Start here."}


def open_folder(d):
    """the folder in the system's own file window"""
    try:
        if sys.platform == "win32":
            os.startfile(str(d))
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(d)])
    except Exception:
        pass


def applescript_admin(cmd):
    """osascript's line that runs cmd as root, after the system's password window"""
    line = " ".join(shlex.quote(c) for c in cmd)
    return 'do shell script "' + line.replace("\\", "\\\\").replace('"', '\\"') + '" with administrator privileges'


def run_elevated(cmd):
    """start cmd as administrator / root; -> wait() for its exit code. The window stays the user's."""
    if R.is_admin():
        return subprocess.Popen(cmd).wait
    if sys.platform == "win32":
        return _shell_runas(cmd)
    if sys.platform == "darwin":
        return subprocess.Popen(["osascript", "-e", applescript_admin(cmd)]).wait
    if shutil.which("pkexec"):
        return subprocess.Popen(["pkexec", *cmd]).wait
    raise R.Fail("needs root: run tools/fm1_rescue.py with sudo")


def _shell_runas(cmd):
    import ctypes as C
    from ctypes import wintypes as W

    class SEI(C.Structure):                                  # SHELLEXECUTEINFOW
        _fields_ = [("cbSize", W.DWORD), ("fMask", C.c_ulong), ("hwnd", W.HWND), ("lpVerb", W.LPCWSTR),
                    ("lpFile", W.LPCWSTR), ("lpParameters", W.LPCWSTR), ("lpDirectory", W.LPCWSTR),
                    ("nShow", C.c_int), ("hInstApp", W.HINSTANCE), ("lpIDList", C.c_void_p),
                    ("lpClass", W.LPCWSTR), ("hkeyClass", W.HKEY), ("dwHotKey", W.DWORD),
                    ("hIcon", W.HANDLE), ("hProcess", W.HANDLE)]
    e = SEI(cbSize=C.sizeof(SEI), fMask=0x40, lpVerb="runas", lpFile=cmd[0],     # NOCLOSEPROCESS
            lpParameters=subprocess.list2cmdline(cmd[1:]), nShow=0)
    if not C.windll.shell32.ShellExecuteExW(C.byref(e)) or not e.hProcess:
        raise R.Fail("the rescue needs an administrator (Windows asked, and it was not allowed)")
    k = C.windll.kernel32

    def wait():
        k.WaitForSingleObject(W.HANDLE(e.hProcess), 0xFFFFFFFF)
        code = W.DWORD()
        k.GetExitCodeProcess(W.HANDLE(e.hProcess), C.byref(code))
        k.CloseHandle(W.HANDLE(e.hProcess))
        return code.value
    return wait


RESCUE_STEP = {"ready": "Thanks. Looking for the FM-1; follow the next instruction.",
               "backup_read": "Copying the FM-1's memory ({} of 2). Nothing has been changed.",
               "backup": "Backup saved and checked: {}", "write": "Writing {}%. Keep the cable in.",
               "done": "Written and checked. Waiting for the FM-1 to start…"}


# ------------------------------------------------------------------- window ---

def dark_mode():
    """the system's own choice: Windows' app mode, macOS' appearance; FM1_SWITCH_THEME=dark/light wins"""
    want = os.environ.get("FM1_SWITCH_THEME", "").lower()
    if want in ("dark", "light"):
        return want == "dark"
    try:
        if sys.platform == "win32":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True,
                               text=True, timeout=2)
            return r.stdout.strip() == "Dark"
    except Exception:
        pass
    return False


# the colours beside the theme's own: text, quiet text, card, line, accent, and the status dot's
PALETTE = {True: {"bg": "#1c1c1c", "fg": "#fafafa", "muted": "#a0a0a0", "card": "#2b2b2b",
                  "line": "#3a3a3a", "accent": "#57c8ff", "ok": "#3fb950", "warn": "#e3b341",
                  "bad": "#f85149", "off": "#6e7681"},
           False: {"bg": "#fafafa", "fg": "#1c1c1c", "muted": "#5f5f5f", "card": "#ffffff",
                   "line": "#e5e5e5", "accent": "#005fb8", "ok": "#1a7f37", "warn": "#9a6700",
                   "bad": "#cf222e", "off": "#8c959f"}}


def dark_titlebar(win, dark):
    """Windows 10/11 draws the title bar dark too when asked (DWMWA_USE_IMMERSIVE_DARK_MODE)"""
    if sys.platform != "win32" or not dark:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break
    except Exception:
        pass


def main():
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, messagebox, ttk

    backend, midi_err = None, None

    if sys.platform == "win32":                 # sharp text on a scaled screen, not a blurry bitmap
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    root.title("FM-1 Switch")
    mac = sys.platform == "darwin"
    dark = dark_mode()
    try:
        import sv_ttk                           # Sun Valley: Windows 11's own look for ttk
        sv_ttk.set_theme("dark" if dark else "light")
    except Exception:
        ttk.Style().theme_use("clam")
    C = PALETTE[dark]
    root.configure(background=C["bg"])
    scale = root.winfo_fpixels("1i") / 96.0

    def px(n):
        return int(n * scale)
    family = "SF Pro Text" if mac else "Segoe UI Variable Text"
    if family not in tkfont.families():
        family = "Helvetica" if mac else "Segoe UI"
    body_f = (family, 13 if mac else 10)
    small_f = (family, 11 if mac else 9)
    head_f = (family, 15 if mac else 12, "bold")
    title_f = (family, 22 if mac else 18, "bold")
    st = ttk.Style()
    st.configure("Muted.TLabel", foreground=C["muted"], font=small_f)
    st.configure("Head.TLabel", font=head_f)
    st.configure("Title.TLabel", font=title_f)
    st.configure("Section.TLabel", foreground=C["muted"], font=(family, small_f[1], "bold"))
    st.configure("Badge.TLabel", foreground=C["accent"], font=(family, small_f[1], "bold"))
    st.configure("Treeview", rowheight=px(30), font=body_f)
    st.configure("Treeview.Heading", font=small_f)
    root.geometry(f"{px(760)}x{px(600)}")
    root.minsize(px(640), px(520))
    dark_titlebar(root, dark)
    folders = default_folders()
    state = {"pkgs": [], "busy": False, "rescue": None}
    tone = {"value": "off"}

    top = ttk.Frame(root, padding=(px(20), px(16), px(20), px(16)))
    top.pack(fill="both", expand=True)

    head = ttk.Frame(top)
    head.pack(fill="x")
    ttk.Label(head, text="FM-1 Switch", style="Title.TLabel").pack(side="left")
    ttk.Label(head, text=f"  {app_version()}", style="Muted.TLabel").pack(side="left", anchor="s", pady=(0, px(5)))

    card = ttk.Frame(top, style="Card.TFrame", padding=(px(16), px(14)))
    card.pack(fill="x", pady=(px(14), px(16)))
    card.columnconfigure(1, weight=1)
    dot = tk.Canvas(card, width=px(14), height=px(14), highlightthickness=0, bd=0, background=C["card"])
    dot.grid(row=0, column=0, sticky="w", padx=(0, px(10)))
    dot_id = dot.create_oval(px(2), px(2), px(12), px(12), fill=C["off"], outline="")
    dev_var = tk.StringVar(value="FM-1: not read yet")
    ttk.Label(card, textvariable=dev_var, style="Head.TLabel").grid(row=0, column=1, sticky="w")
    status = tk.StringVar(value="Connect the FM-1 with a USB data cable, close other MIDI apps.")
    msg = ttk.Label(card, textvariable=status, style="Muted.TLabel", justify="left", wraplength=px(520))
    msg.grid(row=1, column=1, sticky="we", pady=(px(4), 0))
    prot_var = tk.StringVar(value=PROTECTION_TEXT[None])
    prot = ttk.Label(card, textvariable=prot_var, style="Muted.TLabel", justify="left", wraplength=px(520))
    prot.grid(row=2, column=1, sticky="we", pady=(px(4), 0))
    card_btns = ttk.Frame(card)
    card_btns.grid(row=0, column=2, rowspan=3, sticky="ne", padx=(px(12), 0))
    card.bind("<Configure>", lambda e: [m.configure(wraplength=max(px(200), e.width - px(200))) for m in (msg, prot)])

    def set_tone(t):
        tone["value"] = t
        dot.itemconfigure(dot_id, fill=C.get(t, C["off"]))

    # the start screen: four tasks, Protect marked recommended until the FM-1 is protected
    adv = ttk.Frame(top)                        # (packed from the bottom: the tasks take what is left)
    adv.pack(side="bottom", fill="x", pady=(px(10), 0))
    bar = ttk.Progressbar(top, maximum=100)
    bar.pack(side="bottom", fill="x", pady=(px(12), 0))
    ttk.Label(top, text="WHAT DO YOU WANT TO DO?", style="Section.TLabel").pack(anchor="w", pady=(0, px(6)))
    tasks = ttk.Frame(top)
    tasks.pack(fill="both", expand=True)
    tasks.columnconfigure((0, 1), weight=1, uniform="t")
    tasks.rowconfigure((0, 1), weight=1, uniform="t")
    badge = tk.StringVar(value="RECOMMENDED")

    def tile(r, c, title, text, button, command, var=None):
        t = ttk.Frame(tasks, style="Card.TFrame", padding=(px(14), px(12)))
        t.grid(row=r, column=c, sticky="nsew", padx=(0, px(6)) if c == 0 else (px(6), 0),
               pady=(0, px(6)) if r == 0 else (px(6), 0))
        b = ttk.Button(t, text=button, command=command)
        b.pack(side="bottom", anchor="w", pady=(px(8), 0))
        if var is not None:
            ttk.Label(t, textvariable=var, style="Badge.TLabel").pack(anchor="w")
        ttk.Label(t, text=title, style="Head.TLabel").pack(anchor="w")
        d = ttk.Label(t, text=text, style="Muted.TLabel", wraplength=px(300), justify="left")
        d.pack(anchor="w", fill="x", pady=(px(4), 0))
        t.bind("<Configure>", lambda e: d.configure(wraplength=max(px(120), e.width - px(30))))
        return b
    pb_ = tile(0, 0, "Protect my FM-1", TASK_TEXT["protect"], "Protect…", lambda: open_protect(), badge)
    tb_ = [pb_,
           tile(0, 1, "Install firmware", TASK_TEXT["install"], "Choose firmware…", lambda: show_install()),
           tile(1, 0, "Back up my FM-1", TASK_TEXT["backup"], "Backups…", lambda: open_backups()),
           tile(1, 1, "My FM-1 won't start", TASK_TEXT["guide"], "Open the guide…", lambda: open_guide())]

    def show_protection():
        p = protection(rescue_dir())
        prot_var.set(PROTECTION_TEXT[p])
        badge.set("" if p == "protected" else "RECOMMENDED")
        pb_.configure(style="TButton" if p == "protected" else "Accent.TButton")

    # Install firmware: its own window, made once and shown when asked (the list keeps its files)
    inst = tk.Toplevel(root)
    inst.withdraw()
    inst.title("FM-1 Switch – Install firmware")
    inst.configure(background=C["bg"])
    inst.geometry(f"{px(720)}x{px(460)}")
    inst.transient(root)
    inst.protocol("WM_DELETE_WINDOW", inst.withdraw)
    inst_f = ttk.Frame(inst, padding=(px(20), px(16)))
    inst_f.pack(fill="both", expand=True)
    ttk.Label(inst_f, text="Install firmware", style="Head.TLabel").pack(anchor="w")
    ttk.Label(inst_f, text=INSTALL_HELP, style="Muted.TLabel", wraplength=px(670),
              justify="left").pack(anchor="w", pady=(px(4), px(10)))
    row = ttk.Frame(inst_f)
    row.pack(side="bottom", fill="x", pady=(px(12), 0))
    ttk.Label(inst_f, text="FIRMWARE FILES", style="Section.TLabel").pack(anchor="w", pady=(0, px(6)))
    lf = ttk.Frame(inst_f)
    lf.pack(fill="both", expand=True)
    shown_once = {"inst": False}

    def show_install():
        inst.deiconify()
        inst.lift()
        inst.focus_set()
        if not shown_once["inst"]:
            shown_once["inst"] = True
            dark_titlebar(inst, dark)
    cols = ("fam", "name", "id", "note")
    lst = ttk.Treeview(lf, columns=cols, show="headings", selectmode="browse", height=6)
    for c, t, w, s in (("fam", "Family", 90, False), ("name", "File", 280, True), ("id", "Identity", 120, False),
                       ("note", "", 110, False)):
        lst.heading(c, text=t, anchor="w")
        lst.column(c, width=px(w), anchor="w", stretch=s)
    lst.tag_configure("other", foreground=C["muted"])
    sb = ttk.Scrollbar(lf, orient="vertical", command=lst.yview)
    lst.configure(yscrollcommand=sb.set)
    lst.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    def chosen(tree):
        """the selected row's index in the list, or None"""
        sel = tree.selection()
        return tree.index(sel[0]) if sel else None

    def choose(tree, i):
        kids = tree.get_children()
        if kids:
            iid = kids[i]
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)

    def dialog(title, w, h):
        d = tk.Toplevel(root)
        d.title(title)
        d.configure(background=C["bg"])
        d.geometry(f"{px(w)}x{px(h)}")
        d.transient(root)
        dark_titlebar(d, dark)
        return d

    def ui(f, *a, **kw):
        later(root.after, f, *a, **kw)

    def ui_wait(f, *a):
        """Show a phase before launching a system permission prompt."""
        shown = threading.Event()
        def show():
            try:
                f(*a)
                root.update_idletasks()
            finally:
                shown.set()
        ui(show)
        shown.wait()

    port_var = tk.StringVar(value="Automatic")
    ttk.Label(adv, text="ADVANCED", style="Section.TLabel").pack(side="left", padx=(0, px(10)))
    ttk.Label(adv, text="MIDI input:", style="Muted.TLabel").pack(side="left")
    port_box = ttk.Combobox(adv, textvariable=port_var, state="readonly", width=30)
    port_box.pack(side="left", padx=px(8))

    def selected_port():
        return None if port_var.get() == "Automatic" else port_var.get()

    def refresh():
        try:
            ports = backend.input_names() if backend else []
        except Exception:
            ports = []
        port_box["values"] = ["Automatic"] + ports
        state["pkgs"] = scan(folders)
        lst.delete(*lst.get_children())
        for p in state["pkgs"]:
            lst.insert("", "end", values=row_values(p), tags=(() if p.kind in ("felucca", "stock") else ("other",)))
        if not state["pkgs"]:
            lst.insert("", "end", values=("", "No .fwsc files here yet: Add folder…", "", ""), tags=("other",))
        elif state["pkgs"][0].kind == "felucca":
            choose(lst, 0)
        show_protection()

    def busy(on):
        state["busy"] = on
        port_box.configure(state="disabled" if on else "readonly")
        for b in buttons:
            b.state(["disabled"] if on else ["!disabled"])

    def check_device(secs=8.0):
        if backend is None:
            dev_var.set("FM-1: MIDI is missing")
            status.set(NO_MIDI.format(midi_err))
            set_tone("bad")
            return
        busy(True)
        dev_var.set("FM-1: looking…")
        set_tone("off")
        if secs:
            status.set("Looking for the FM-1 (a few seconds)…")

        def work():
            probe_ok = R.uboot_present() is not None
            try:
                state, ident = diagnose(backend, R.uboot_present if probe_ok else None, secs, port=port)
            except Exception as e:      # (a port that will not open: another app has it)
                ui(dev_var.set, "FM-1: could not be read")
                ui(status.set, f"Error: {e} (another app using the FM-1's MIDI port?)")
                ui(set_tone, "bad")
            else:
                head, todo = advice(state, ident, probe_ok)
                ui(dev_var.set, head)
                ui(status.set, todo)
                ui(set_tone, STATE_TONE.get(state, "off"))
            ui(busy, False)
        port = selected_port()
        threading.Thread(target=work, daemon=True).start()

    def stop_rescue():
        """the card's Stop, while a run waits for the FM-1 (a write in progress is finished)"""
        if state["rescue"]:
            open(state["rescue"] + ".stop", "w").close()
            status.set("Stopping… (if it is already writing, it finishes first)")

    def stopper(on):
        if on:
            stop_b.state(["!disabled"])
            stop_b.pack(pady=(px(4), 0))
        else:
            stop_b.pack_forget()

    def do_rescue(via="usb"):
        """Rescue: 1 choose the firmware to put back, 2 accept the risk, 3 run it"""
        if state["busy"]:
            return
        d = rescue_dir()
        choices = rescue_choices(folders + [d])
        w = dialog("FM-1 Switch – Through update mode" + (", with the adapter (experimental)" if via != "usb" else ""),
                   680, 680)
        w.grab_set()
        f = ttk.Frame(w, padding=(px(20), px(16)))
        f.pack(fill="both", expand=True)
        wrap = px(630)
        ttk.Label(f, text="1  Pick the firmware to put on", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text="M-VAVE's own V15 is the safest choice: it is what the FM-1 came with. Any other "
                          "FM-1 firmware works too. The FM-1's start-up program is not touched.",
                  style="Muted.TLabel", wraplength=wrap, justify="left").pack(anchor="w", pady=(px(4), px(8)))
        box = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse", height=6)
        for c, t, wd, s in (("fam", "Family", 90, False), ("name", "File", 300, True), ("id", "Identity", 110, False),
                            ("note", "", 100, False)):
            box.heading(c, text=t, anchor="w")
            box.column(c, width=px(wd), anchor="w", stretch=s)
        box.pack(fill="x")

        def names():
            box.delete(*box.get_children())
            for c in choices:
                box.insert("", "end", values=("M-VAVE", "V15, downloaded from M-VAVE and checked", "V15", "")
                           if c is None else row_values(c))
        names()
        choose(box, 0)

        def browse():
            fn = filedialog.askopenfilename(parent=w, title="An FM-1 firmware file",
                                            filetypes=[("FM-1 firmware", "*.fwsc"), ("All files", "*.*")])
            if not fn:
                return
            pkg = package_at(fn)
            if pkg is None:
                messagebox.showerror("FM-1 Switch", f"{Path(fn).name} is not an FM-1 firmware file.", parent=w)
                return
            choices.append(pkg)
            names()
            choose(box, len(choices) - 1)
        ttk.Button(f, text="Browse…", command=browse).pack(anchor="w", pady=(px(8), 0))
        note = tk.StringVar()
        ttk.Label(f, textvariable=note, style="Muted.TLabel", wraplength=wrap, justify="left").pack(anchor="w", pady=(px(4), 0))

        def picked(_e=None):
            i = chosen(box)
            c = choices[i] if i is not None else None
            note.set("" if c is None or c.kind != "other" else
                     "Careful: if this one does not start, the FM-1 may not get into update mode by itself "
                     "afterwards (unless it is protected), and then it needs the adapter board.")
        box.bind("<<TreeviewSelect>>", picked)
        ucun_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Also write if this FM-1 has the newer UCUN memory chip (experimental)",
                        variable=ucun_var).pack(anchor="w", pady=(px(10), 0))
        ttk.Label(f, text="Most FM-1s have the original chip, and then this box does not matter. With the newer "
                  "UCUN chip and the box empty, it only makes a backup and stops. Writing to UCUN has not "
                  "been tried on a real FM-1 yet: pick a firmware whose maker says it works with UCUN.",
                  style="Muted.TLabel",
                  wraplength=wrap, justify="left").pack(anchor="w")
        ttk.Label(f, text="2  Your own risk", style="Head.TLabel").pack(anchor="w", pady=(px(16), 0))
        ttk.Label(f, text="Everything on the FM-1 is backed up first, the start-up program is never touched, "
                          "and nothing is written unless it really is an FM-1. Even so, this can go wrong.",
                  style="Muted.TLabel", wraplength=wrap, justify="left").pack(anchor="w", pady=(px(4), px(8)))
        ok_var = tk.BooleanVar(value=False)
        tick = ttk.Frame(f)                     # (a ttk check box does not wrap its text: a label beside it)
        tick.pack(fill="x")

        def ticked():
            go.state(["!disabled"] if ok_var.get() else ["disabled"])
        ttk.Checkbutton(tick, variable=ok_var, command=ticked).pack(side="left", anchor="n")
        risk = ttk.Label(tick, text=RESCUE_RISK, wraplength=wrap - px(40), justify="left")
        risk.pack(side="left", fill="x")
        risk.bind("<Button-1>", lambda e: (ok_var.set(not ok_var.get()), ticked()))
        row2 = ttk.Frame(f)
        row2.pack(fill="x", side="bottom")

        def start():
            i = chosen(box)
            c = choices[i] if i is not None else None
            w.grab_release()
            w.destroy()
            run_rescue(c, d, via, allow_ucun=ucun_var.get())
        ttk.Label(f, text="Next: the computer asks for permission (take your time), then everything is backed "
                  "up and checked, and only then written.",
                  style="Muted.TLabel", wraplength=wrap).pack(anchor="w", pady=(px(8), 0))
        go = ttk.Button(row2, text="Start", style="Accent.TButton", command=start)
        go.state(["disabled"])
        go.pack(side="right")
        ttk.Button(row2, text="Cancel", command=w.destroy).pack(side="right", padx=(0, px(8)))

    def do_inspect():
        if state["busy"]:
            return
        if messagebox.askokcancel("FM-1 Switch – Back up my FM-1", BACKUP_TEXT, parent=root):
            run_rescue(None, rescue_dir(), "usb", inspect=True)

    def open_backups():
        """Back up my FM-1: the backups saved here, newest first; back up now, put one back, or M-VAVE's
        bootloader back from one made before Protect"""
        if state["busy"]:
            return
        d = rescue_dir()
        w = dialog("FM-1 Switch – Back up my FM-1", 720, 460)
        f = ttk.Frame(w, padding=(px(20), px(16)))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Back up my FM-1", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text=BACKUPS_HELP, style="Muted.TLabel", wraplength=px(670), justify="left").pack(
            anchor="w", pady=(px(4), px(10)))
        r = ttk.Frame(f)
        r.pack(side="bottom", fill="x", pady=(px(12), 0))
        box = ttk.Treeview(f, columns=("when", "fw", "boot"), show="headings", selectmode="browse", height=8)
        for c, t, wd, st_ in (("when", "Saved", 140, False), ("fw", "Firmware", 260, True),
                              ("boot", "Start-up program", 220, True)):
            box.heading(c, text=t, anchor="w")
            box.column(c, width=px(wd), anchor="w", stretch=st_)
        box.pack(fill="both", expand=True)
        items = []

        def fill():
            items[:] = backups(d)
            box.delete(*box.get_children())
            for b in items:
                box.insert("", "end", values=backup_row(b))
            if not items:
                box.insert("", "end", values=("", "No backups yet: Back up now", ""))
            else:
                choose(box, 0)
            picked()

        def sel():
            i = chosen(box)
            return items[i] if i is not None and i < len(items) else None

        def picked(_e=None):
            b = sel()
            put.state(["!disabled"] if b else ["disabled"])
            stock.state(["!disabled"] if b and b["kind"] == "stock" else ["disabled"])
        box.bind("<<TreeviewSelect>>", picked)

        def go(mode):
            b = sel()
            if b:
                w.destroy()
                open_protect(mode, b)

        def now():
            w.destroy()
            do_inspect()
        ttk.Button(r, text="Back up now", style="Accent.TButton", command=now).pack(side="left")
        ttk.Button(r, text="Open folder", command=lambda: open_folder(d)).pack(side="left", padx=(px(8), 0))
        ttk.Button(r, text="Close", command=w.destroy).pack(side="right")
        put = ttk.Button(r, text="Put back…", command=lambda: go("restore"))
        put.pack(side="right", padx=(0, px(8)))
        stock = ttk.Button(r, text="M-VAVE's bootloader back…", command=lambda: go("stock"))
        stock.pack(side="right", padx=(0, px(8)))
        fill()

    def open_protect(mode="protect", backup=None, stuck=False):
        """Protect my FM-1 in seven screens: why, connect, update mode, backup, check and confirm, writing,
        try REC. The helper (fm1_rescue.py --protect --ask) backs up and plans; it writes only after Install.
        The same screens put a backup back (mode "restore") or M-VAVE's bootloader from one ("stock")."""
        if state["busy"]:
            return
        d = rescue_dir()
        F = FLOW[mode]
        w = dialog("FM-1 Switch – " + F["screen"], 680, 620)
        body = ttk.Frame(w, padding=(px(20), px(16)))
        body.pack(fill="both", expand=True)
        wrap = px(630)
        live = {"open": True, "page": None, "prog": None, "running": False, "writing": False,
                "cancel": False, "hardware": None, "backup": None, "bar": None}
        say = tk.StringVar()
        found = tk.StringVar()
        port = selected_port()

        def clear(page):
            """a new screen: its button row comes first, at the bottom"""
            live["page"] = page
            for c in body.winfo_children():
                c.destroy()
            r = ttk.Frame(body)
            r.pack(fill="x", side="bottom", pady=(px(12), 0))
            return r

        def heading(n, t):
            if n:
                ttk.Label(body, text=f"STEP {n} OF 7", style="Section.TLabel").pack(anchor="w")
            ttk.Label(body, text=t, style="Head.TLabel", wraplength=wrap, justify="left").pack(
                anchor="w", pady=(px(2), px(10)))

        def text(t, style="TLabel", var=None, **kw):
            lab = ttk.Label(body, text=t, textvariable=var, style=style, wraplength=wrap, justify="left")
            lab.pack(anchor="w", **kw)
            return lab

        def tick_row(var, t, cmd):
            """a check box beside a label that wraps (a ttk check box does not)"""
            f = ttk.Frame(body)
            f.pack(fill="x", pady=(px(8), 0))
            ttk.Checkbutton(f, variable=var, command=cmd).pack(side="left", anchor="n")
            lab = ttk.Label(f, text=t, wraplength=wrap - px(40), justify="left")
            lab.pack(side="left", fill="x")
            lab.bind("<Button-1>", lambda e: (var.set(not var.get()), cmd()))

        def close():
            if live["writing"]:
                messagebox.showinfo("FM-1 Switch", "It is writing right now. Let it finish: it takes a few "
                                    "seconds. Keep the cable in.", parent=w)
                return
            if live["running"]:
                live["cancel"] = True
                open(live["prog"] + ".stop", "w").close()
                say.set("Stopping after this step… Nothing has been changed.")
                return                  # (the window closes when the run has ended)
            live["open"] = False
            w.destroy()
        w.protocol("WM_DELETE_WINDOW", close)

        def why():
            if mode != "protect":
                return why_restore()
            r = clear("why")
            heading(1, "Protect my FM-1")
            for t in PROTECT_WHY:
                text("•  " + t, pady=(0, px(6)))
            if protection(d) == "protected":
                text("This FM-1 already looks protected. Running this again only checks it, and changes "
                     "nothing if it is.", style="Muted.TLabel", pady=(px(4), 0))
            text(PROTECT_CREDIT, style="Muted.TLabel", pady=(px(10), 0))
            link = ttk.Label(body, text=MBOOT_PAGE, style="Muted.TLabel", foreground=C["accent"], cursor="hand2")
            link.pack(anchor="w")
            link.bind("<Button-1>", lambda e: webbrowser.open(MBOOT_PAGE))
            ttk.Button(r, text="Next", style="Accent.TButton", command=connect).pack(side="right")
            ttk.Button(r, text="Cancel", command=close).pack(side="right", padx=(0, px(8)))

        def why_restore():
            r = clear("why")
            heading(1, F["title"])
            b = backup["path"] if isinstance(backup, dict) else backup
            info = backup if isinstance(backup, dict) else {}
            for t in (RESTORE_WHY if mode == "restore" else STOCK_WHY):
                text("•  " + t, pady=(0, px(6)))
            if info:
                text("The backup: " + "  ·  ".join(backup_row(info)), style="Muted.TLabel", pady=(px(8), 0))
            text(str(b), style="Muted.TLabel")
            ttk.Button(r, text="Next", style="Accent.TButton", command=connect).pack(side="right")
            ttk.Button(r, text="Cancel", command=close).pack(side="right", padx=(0, px(8)))

        def connect():
            r = clear("connect")
            heading(2, "Connect your FM-1")
            for i, t in enumerate(("Keep the cable in.",
                                   "Leave the FM-1 as it is, switched on, with its screen dark: it is waiting in "
                                   "update mode, which is what is needed now.",
                                   "Close music programs or web editors that might be talking to it.") if stuck else
                                  ("Plug the FM-1 straight into this computer (no hub).",
                                   "Switch it on and let your usual firmware start.",
                                   "Close music programs or web editors that might be talking to it."), 1):
                text(f"{i}. {t}", pady=(0, px(4)))
            text("Then press Start. The computer will ask for permission: take your time, nothing is changed "
                 "on the FM-1 until you say so in step 5.", style="Muted.TLabel", pady=(px(10), 0))
            text(dev_var.get(), style="Muted.TLabel", pady=(px(8), 0))
            ttk.Button(r, text="Start", style="Accent.TButton", command=start).pack(side="right")
            ttk.Button(r, text="Back", command=why).pack(side="right", padx=(0, px(8)))
            ttk.Button(r, text="Cancel", command=close).pack(side="left")

        def moving(n, title, lines):
            """a screen that waits on the FM-1: a moving bar and the helper's own words"""
            r = clear(title)
            heading(n, title)
            for t in lines:
                text(t, pady=(0, px(6)))
            pb = ttk.Progressbar(body, mode="indeterminate")
            pb.pack(fill="x", pady=(px(10), px(6)))
            pb.start(12)
            text("", style="Muted.TLabel", var=say)
            ttk.Button(r, text="Cancel", command=close).pack(side="left")
            return r

        def update_mode():
            moving(3, "Update mode", ("FM-1 Switch now puts the FM-1 into update mode, a special mode for "
                                      "loading software. Its screen goes dark or shows a bar: that is normal.",
                                      UPDATE_MODE_KEYS))

        def backing_up():
            r = clear("backup")
            heading(4, "Backing up your FM-1")
            text("Everything on the FM-1 is copied to this computer, twice, and the two copies are compared. "
                 "Nothing is changed yet. Keep the cable in.")
            pb = ttk.Progressbar(body, maximum=3)
            pb.pack(fill="x", pady=(px(12), px(6)))
            live["bar"] = pb
            text("", style="Muted.TLabel", var=say)
            text("", var=found, pady=(px(12), 0))
            ttk.Button(r, text="Cancel", command=close).pack(side="left")

        def draw_map(regions, changed):
            cols, cell = 32, px(17)
            cv = tk.Canvas(body, width=cols * cell, height=(len(regions) + cols - 1) // cols * cell,
                           highlightthickness=0, bd=0, background=C["bg"])
            cv.pack(anchor="w", pady=(px(10), px(4)))
            for i, reg in enumerate(regions):
                x, y = i % cols * cell, i // cols * cell
                hot = i * R.SECTOR in changed
                cv.create_rectangle(x + 1, y + 1, x + cell - 1, y + cell - 1, fill=MAP_COLORS.get(reg, MAP_COLORS["free"]),
                                    outline=C["fg"] if hot else "", width=2 if hot else 0)
            leg = ttk.Frame(body)
            leg.pack(anchor="w", fill="x")
            for i, (reg, name, colour) in enumerate(map_legend(regions)):
                sw = tk.Canvas(leg, width=px(12), height=px(12), highlightthickness=0, bd=0, background=C["bg"])
                sw.create_rectangle(0, 0, px(12), px(12), fill=colour, outline="")
                sw.grid(row=i // 4, column=i % 4 * 2, padx=(0, px(4)), pady=px(1))
                ttk.Label(leg, text=name, style="Muted.TLabel").grid(row=i // 4, column=i % 4 * 2 + 1, sticky="w",
                                                                    padx=(0, px(14)))
            text("The map shows the FM-1's memory as 256 squares. Outlined squares are the ones that change.",
                 style="Muted.TLabel", pady=(px(4), 0))

        def review(p):
            r = clear("review")
            heading(5, "Check, then confirm")
            text(plan_text(p) if mode == "protect" else restore_plan_text(p))
            text(f"Start-up program now: {p.get('from')}. Afterwards: " + (f"m-bootloader {p.get('to')}." if mode == "protect"
                                                                             else f"{p.get('to')}."),
                 style="Muted.TLabel", pady=(px(6), 0))
            draw_map(p.get("map") or [], p.get("sectors") or [])
            if live["backup"]:
                text("Your backup is saved and checked: " + str(live["backup"]), style="Muted.TLabel", pady=(px(6), 0))
            exp = bool((live["hardware"] or {}).get("experimental"))
            ucun, ok = tk.BooleanVar(value=False), tk.BooleanVar(value=False)

            def ticked():
                go.state(["!disabled"] if ok.get() and (ucun.get() or not exp) else ["disabled"])
            if exp:
                text(UCUN_NOTE, style="Muted.TLabel", pady=(px(8), 0))
                tick_row(ucun, "Write to this newer UCUN memory chip anyway (experimental).", ticked)
            tick_row(ok, PROTECT_TICK, ticked)
            go = ttk.Button(r, text=F["install"], style="Accent.TButton", command=lambda: install(ucun.get()))
            go.state(["disabled"])
            go.pack(side="right")
            ttk.Button(r, text="Cancel", command=close).pack(side="right", padx=(0, px(8)))

        def install(ucun):
            live["writing"] = True
            R.answer(live["prog"], ucun)
            clear("write")
            heading(6, F["write"])
            text("Keep the cable in and the FM-1 switched on. It takes a few seconds, then everything is "
                 "read back and checked.")
            pb = ttk.Progressbar(body, maximum=100)
            pb.pack(fill="x", pady=(px(12), px(6)))
            live["bar"] = pb
            text("", style="Muted.TLabel", var=say)

        def putback_failed(v):
            r = clear("putback")
            heading(None, "Putting it back did not finish")
            n = len((v or {}).get("sectors") or [])
            text(PUTBACK_FAILED.format(n=n, s="s" if n != 1 else ""))

            def again():
                R.answer(live["prog"])
                clear("write")
                heading(None, "Putting it back again")
                text("Keep the cable in and the FM-1 switched on.")
                text("", style="Muted.TLabel", var=say)

            def give_up():
                open(live["prog"] + ".stop", "w").close()
                say.set("Stopping…")
            ttk.Button(r, text="Try again", style="Accent.TButton", command=again).pack(side="right")
            ttk.Button(r, text="Stop trying", command=give_up).pack(side="right", padx=(0, px(8)))

        def try_rec(version, new):
            r = clear("rec")
            heading(7, "Try it: hold REC")
            text(("Done! The new start-up program is on the FM-1 and checked. " if new else
                  f"This FM-1 already has the new start-up program ({version}): nothing was changed. ")
                 + "Try the spare key once, so you know it works:")
            for i, t in enumerate(REC_STEPS, 1):
                text(f"{i}. {t}", pady=(px(6), 0))
            seen = tk.StringVar(value="Waiting for the FM-1 to be switched off…")
            text("", style="Head.TLabel", var=seen, pady=(px(14), 0))
            text(REC_NOTE, style="Muted.TLabel", pady=(px(6), 0))
            ttk.Button(r, text="Done", style="Accent.TButton", command=finish).pack(side="right")

            def watch():
                # the FM-1 must first leave update mode (it may not restart by itself: then it stays there
                # until switched off), and only then come back to it: else an FM-1 that never left would pass
                st = "off"
                while live["open"] and live["page"] == "rec":
                    if st != "rec":
                        was, st = st, rec_step(st, R.uboot_present())
                        if st == "on" and was == "off":
                            ui(seen.set, "Waiting for the FM-1 in update mode…")
                        elif st == "rec":
                            ui(seen.set, "✓ Update mode reached: the rescue works. Now switch it off and on.")
                    else:
                        try:
                            st_, ident = diagnose(backend, None, 0, port=port)
                        except Exception:
                            st_, ident = "none", None
                        if st_ == "running":
                            ui(seen.set, f"✓ Update mode reached, and the firmware runs again ({ident}).")
                            return
                    time.sleep(1.0)
            threading.Thread(target=watch, daemon=True).start()

        def done_page(new):
            r = clear("rec")
            heading(7, "Done")
            text("The backup is back on the FM-1 and checked." if new else
                 "The FM-1 already holds exactly this backup: nothing was changed.")
            seen = tk.StringVar(value="Waiting for its firmware to answer…")
            text("", style="Head.TLabel", var=seen, pady=(px(14), 0))
            text("If nothing happens within a few seconds, switch the FM-1 off, wait 10 seconds and switch it on.",
                 style="Muted.TLabel", pady=(px(6), 0))
            ttk.Button(r, text="Done", style="Accent.TButton", command=finish).pack(side="right")

            def watch():
                for _ in range(60):
                    if not (live["open"] and live["page"] == "rec"):
                        return
                    try:
                        st_, ident = diagnose(backend, None, 0, port=port)
                    except Exception:
                        st_, ident = "none", None
                    if st_ == "running":
                        ui(seen.set, f"✓ The FM-1 runs {ident}.")
                        return
                    time.sleep(1.0)
            threading.Thread(target=watch, daemon=True).start()

        def failed(msg, wrote):
            r = clear("failed")
            heading(None, F["title"] + " did not finish")
            text(msg)
            if wrote and "put back as it was" in msg:
                text("Everything was put back as it was. Switch the FM-1 off, wait 10 seconds and switch it on: "
                     "it starts as before. You can try again later.", pady=(px(10), 0))
            elif wrote and "could not be put back" in msg:
                text(PUTBACK_GAVE_UP, pady=(px(10), 0))
                text(str(live["backup"] or "(no backup path reported)"), style="Muted.TLabel")
            elif wrote:
                text(PROTECT_FAIL_WRITTEN, pady=(px(10), 0))
                text(str(live["backup"] or "(no backup path reported)"), style="Muted.TLabel")
            elif "did not appear" in msg:
                text("The FM-1 did not show up in update mode. " + UPDATE_MODE_KEYS, pady=(px(10), 0))
            else:
                text("Nothing on the FM-1 was changed.", pady=(px(10), 0))
            if wrote and "put back as it was" not in msg and live["backup"]:
                # half written: only the backup from just before brings it back (running it again is
                # refused: the boot area is no longer whole); that backup, not the newest in the list
                # (putting a backup back that was cut off: that same backup again, not the half-written copy
                # just saved)
                again = backup if mode == "restore" and backup else live["backup"]
                ttk.Button(r, text="Put this backup back", style="Accent.TButton",
                           command=lambda b=again: (close(), open_protect("restore", b, stuck=True))).pack(side="right")
            else:
                ttk.Button(r, text="Try again", style="Accent.TButton", command=connect).pack(side="right")
            ttk.Button(r, text="Close", command=close).pack(side="right", padx=(0, px(8)))

        def finish():
            live["open"] = False
            w.destroy()
            show_protection()
            check_device(0)

        def event(e):
            if not live["open"]:
                return
            s, v = e.get("step"), e.get("value")
            if "say" in e:
                say.set(e["say"])
            elif s == "hardware":
                live["hardware"] = v
                dev_var.set(f"Memory chip: {chip_text(v)}")
                backing_up()
                found.set(found_text(v, None))
                say.set(PROTECT_STEP["hardware"])
            elif s in ("backup_read", "analysis", "backup") and live["page"] == "backup":
                live["bar"].configure(value={"backup_read": v if isinstance(v, int) else 1,
                                             "analysis": 2, "backup": 3}[s])
                say.set(PROTECT_STEP[s].format(v))
                if s == "analysis":
                    found.set(found_text(live["hardware"], v))
                elif s == "backup":
                    live["backup"] = v
            elif s == "plan":
                review(v)
            elif s == "putback_failed":
                putback_failed(v)
            elif s == "write" and live["page"] == "write":
                live["bar"].configure(value=v)
                say.set(PROTECT_STEP["write"].format(v))
            elif s == "done":
                say.set(PROTECT_STEP["done"])

        def ended(end):
            state["rescue"] = None
            busy(False)
            wrote, live["writing"], live["running"] = live["writing"], False, False
            if end and end.get("end") == "ok" and mode != "protect" and end.get("result") in ("restored", "already"):
                kind = backup.get("kind") if isinstance(backup, dict) else None
                if mode == "stock" or kind in ("stock", "open"):
                    mark_protected(d, None, "protected" if mode == "restore" and kind == "open" else "not")
                show_protection()
                status.set("The backup is back on the FM-1." if mode == "restore" else
                           "M-VAVE's bootloader is back on the FM-1.")
                if live["open"]:
                    done_page(end["result"] == "restored")
                return
            if end and end.get("end") == "ok" and end.get("result") in ("installed", "already"):
                mark_protected(d, end.get("version") or R.MBOOT_VERSION)
                show_protection()
                status.set("Protected: the FM-1 has its spare key.")
                if live["open"]:
                    try_rec(end.get("version") or R.MBOOT_VERSION, end["result"] == "installed")
                return
            if live["cancel"] and not wrote:
                status.set(F["title"] + " was stopped. Nothing was written.")
                live["open"] = False
                w.destroy()
                return
            if not live["open"]:
                return
            if end is None:
                failed("It stopped without saying why." if wrote else
                       "It did not start: the computer's permission was not given.", wrote)
            elif end.get("end") == "ok":
                failed("It stopped before writing.", False)
            else:
                failed(end.get("error") or "It failed.", wrote)

        def start():
            fd, prog = tempfile.mkstemp(prefix="fm1-protect-", suffix=".jsonl")
            os.close(fd)
            live.update(prog=prog, running=True, writing=False, cancel=False, hardware=None, backup=None)
            state["rescue"] = prog
            busy(True)
            say.set("Downloading what is needed…")
            update_mode()

            def work():
                end = None
                try:
                    if mode == "protect":
                        R.get_mbootloader(str(d), lambda t: ui(say.set, t.strip()))
                    R.get_mflr(str(d), lambda t: ui(say.set, t.strip()))
                    cmd = helper_command(protect_args(d, prog, mode, backup["path"] if isinstance(backup, dict)
                                                      else backup))
                    ui_wait(say.set, "Waiting for your permission in the computer's own window. Take your "
                            "time: nothing has been changed, and nothing is waiting.")
                    wait = run_elevated(cmd)
                except Exception as e:
                    ui(ended, {"end": "fail", "error": f"It did not start: {e}"})
                    return
                done = {}
                t = threading.Thread(target=lambda: done.update(code=wait()), daemon=True)
                t.start()
                pos = 0
                while True:
                    alive = t.is_alive()
                    ev, pos = R.read_progress(prog, pos)
                    for e in ev:
                        if e.get("step") == "ready":     # a running FM-1: the soft key, no keys to hold
                            sent = send_update_mode(backend, port)
                            ui(say.set, f"Restarting the FM-1 ({sent}) in update mode…" if sent else
                               "Waiting for the FM-1 in update mode…")
                        elif "end" in e:
                            end = e
                        else:
                            ui(event, e)
                    if not alive:
                        break
                    time.sleep(0.2)
                ui(ended, end)
            threading.Thread(target=work, daemon=True).start()

        why()

    def run_rescue(target, d, via, inspect=False, allow_ucun=False):
        busy(True)
        port = selected_port()
        status.set("Getting ready. Nothing has been changed.")
        bar["value"] = 0
        fd, prog = tempfile.mkstemp(prefix="fm1-rescue-", suffix=".jsonl")
        os.close(fd)
        state["rescue"] = prog

        def work():
            try:
                path = None if inspect else (target.path if target else R.download_stock(str(d), lambda t: ui(status.set, t.strip())))
                ui(status.set, "Downloading what is needed…")
                if via == "usb":
                    R.get_mflr(str(d), lambda t: ui(status.set, t.strip()))
                else:
                    R.get_loader(None, str(d), lambda t: ui(status.set, t.strip()))
                cmd = helper_command(rescue_args(path, d, prog, target is not None and target.kind != "v15", via,
                                                inspect=inspect, allow_ucun=allow_ucun))
                ui_wait(status.set, "Waiting for system approval. Take your time with the permission dialog. "
                        "The device timer starts after access is granted; firmware has not been changed."
                        if via == "usb" else "Connecting to the adapter. Firmware has not been changed.")
                wait = subprocess.Popen(cmd).wait if via != "usb" else run_elevated(cmd)
            except Exception as e:
                ui(status.set, f"It did not start: {e}")
                ui(end_rescue)
                return
            state["rescue"] = prog
            done, pos, end, hardware = {"code": None}, 0, None, None
            t = threading.Thread(target=lambda: done.update(code=wait()), daemon=True)
            t.start()
            def handle_events(ev):
                nonlocal end, hardware
                for e in ev:
                    if "say" in e:
                        ui(status.set, e["say"])
                    elif e.get("step") == "hardware":
                        hardware = e["value"]
                        ui(dev_var.set, f"Flash: {hardware['flash_name']} ({hardware['jedec_id']})")
                    elif e.get("step") in RESCUE_STEP:
                        ui(status.set, RESCUE_STEP[e["step"]].format(e.get("value") or ""))
                        if e["step"] == "ready":
                            ui(stopper, True)
                            if via == "usb":                 # a running FM-1: the soft key, no keys to hold
                                sent = send_update_mode(backend, port)
                                ui(status.set, f"Restarting the FM-1 ({sent}) in update mode… " + UPDATE_MODE_KEYS
                                   if sent else "Waiting for the FM-1 in update mode. " + UPDATE_MODE_KEYS)
                        if e["step"] == "write":
                            ui(stop_b.state, ["disabled"])
                            ui(bar.configure, value=e["value"])
                        elif e["step"] == "done":
                            ui(bar.configure, value=100)
                    elif "end" in e:
                        end = e
            while True:
                ev, pos = R.read_progress(prog, pos)
                handle_events(ev)
                if not t.is_alive():
                    ev, pos = R.read_progress(prog, pos)
                    handle_events(ev)
                    break
                time.sleep(0.2)
            if end and end["end"] == "ok" and inspect:
                msg = (hardware_text(end["hardware"]) + "\n\nBackup verified: " + end["backup"]
                       + "\nNothing on the FM-1 was changed. Now switch it off, wait 10 seconds and switch it on.")
                ui(status.set, "Backup saved and checked. Switch the FM-1 off, wait 10 seconds and switch it on.")
                ui(messagebox.showinfo, "FM-1 Switch – Back up my FM-1", msg)
            elif end and end["end"] == "ok":
                ui(status.set, "Written and checked. Waiting up to 40 seconds for the FM-1 to start…")
                try:
                    identity = confirm_rescue_boot(backend, end.get("product"), port=port)
                except Exception:
                    identity = None
                confirmed, msg = rescue_result(end.get("product"), identity)
                ui(dev_var.set, f"FM-1: {identity}" if confirmed else "FM-1: startup not confirmed")
                ui(set_tone, "ok" if confirmed else "off")
                ui(status.set, msg)
                ui(messagebox.showinfo, "FM-1 Switch", msg)
            elif end and "did not appear" in (end.get("error") or "") and via == "usb":
                ui(status.set, "The FM-1 did not show up in update mode.")
                ui(open_guide, "rescue_failed")
            elif end:
                ui(status.set, f"It did not work: {end.get('error')}")
                msg = end.get("error") or "failed"
                if hardware:
                    msg += "\n\n" + hardware_text(hardware) + "\nHardware report: " + hardware["report"]
                ui(messagebox.showerror, "FM-1 Switch – It did not work", msg)
            else:
                ui(status.set, "It did not start: the computer's permission was not given.")
            ui(end_rescue)

        def end_rescue():
            state["rescue"] = None
            stopper(False)
            busy(False)
        threading.Thread(target=work, daemon=True).start()

    def open_guide(start="detect"):
        """the unbrick guide: 1 listen to the FM-1, 2 ask only what it did not say, 3 one thing to try"""
        if state["busy"] and start != "rescue_failed":      # (that one comes as a rescue ends)
            return
        port = selected_port()
        g = dialog("FM-1 Switch – Check and unbrick", 660, 480)
        body = ttk.Frame(g, padding=(px(20), px(16)))
        body.pack(fill="both", expand=True)
        live = {"open": True, "page": None}
        wrap = px(610)

        def clear(page):
            live["page"] = page
            for w in body.winfo_children():
                w.destroy()

        def close():
            live["open"] = False
            g.destroy()
        g.protocol("WM_DELETE_WINDOW", close)

        def heading(text):
            ttk.Label(body, text=text, style="Head.TLabel", wraplength=wrap, justify="left").pack(anchor="w")

        def text(t, **kw):
            ttk.Label(body, text=t, wraplength=wrap, justify="left").pack(anchor="w", **kw)

        def detect():
            clear("detect")
            heading("Step 1 of 3: looking for the FM-1")
            text("Plug the FM-1 into the computer and switch it on. The guide looks for it for a few seconds.",
                 pady=(8, 0))
            pb = ttk.Progressbar(body, mode="indeterminate")
            pb.pack(fill="x", pady=14)
            pb.start(12)

            def work():
                probe_ok = R.uboot_present() is not None
                try:
                    st, ident = diagnose(backend, R.uboot_present if probe_ok else None, 8, port=port)
                except Exception:       # (a MIDI port another app holds)
                    st, ident = "none", None
                ui(found, st, ident, probe_ok)
            threading.Thread(target=work, daemon=True).start()

        def found(st, ident, probe_ok):
            if not live["open"]:
                return
            dev_var.set(advice(st, ident, probe_ok)[0])
            if needs_questions(st):
                ask(probe_ok)
            else:
                show(plan(st, ident))

        def ask(probe_ok):
            clear("ask")
            heading("Step 2 of 3: what does the FM-1 do?")
            text("The computer cannot find the FM-1, so the guide needs your eyes. (It keeps looking meanwhile: "
                 "if the FM-1 turns up, it goes on by itself.)", pady=(6, 8))
            scr, last = tk.StringVar(value=""), tk.StringVar(value="other")
            text("What does its screen show?")
            for k, t in SCREEN:
                ttk.Radiobutton(body, text=t, value=k, variable=scr).pack(anchor="w", padx=12)
            text("What happened last?", pady=(10, 0))
            for k, t in LAST:
                ttk.Radiobutton(body, text=t, value=k, variable=last).pack(anchor="w", padx=12)
            row2 = ttk.Frame(body)
            row2.pack(fill="x", side="bottom")
            ttk.Button(row2, text="Next", style="Accent.TButton", command=lambda: show(plan("none", screen=scr.get() or None,
                                                                   last=last.get()))).pack(side="left")
            ttk.Button(row2, text="Close", command=close).pack(side="right")

            def watch():
                while live["open"] and live["page"] == "ask":
                    try:
                        st, ident = diagnose(backend, R.uboot_present if probe_ok else None, 0, port=port)
                    except Exception:
                        st, ident = "none", None
                    if st != "none":
                        ui(lambda: live["page"] == "ask" and found(st, ident, probe_ok))
                        return
                    time.sleep(1.0)
            threading.Thread(target=watch, daemon=True).start()

        def show(p):
            clear("plan")
            heading("Step 3 of 3: " + p.title)
            for i, step in enumerate(p.steps, 1):
                text(f"{i}. {step}", pady=(6, 0))
            row2 = ttk.Frame(body)
            row2.pack(fill="x", side="bottom")
            ttk.Button(row2, text=p.button, style="Accent.TButton", command=lambda: act(p)).pack(side="left")
            if p.action == "hardware":
                ttk.Button(row2, text="Try with the adapter (experimental)",
                           command=lambda: (close(), do_rescue("transporter"))).pack(side="left", padx=6)
            if p.action != "check":
                ttk.Button(row2, text="Check again", command=detect).pack(side="left", padx=6)
            ttk.Button(row2, text="Close", command=close).pack(side="right")

        def act(p):
            if p.action == "check":
                detect()
                return
            close()
            if p.action == "rescue":
                do_rescue()
            elif p.action == "hardware":
                webbrowser.open(TRANSPORTER)
            else:                       # install / finish: the chosen file, else the highest
                if chosen(lst) is None and state["pkgs"]:
                    choose(lst, 0)
                show_install()
                do_install()

        if start == "rescue_failed":
            show(plan("none", rescue_failed=True))
        else:
            detect()

    def do_install():
        i = chosen(lst)
        if i is None or not state["pkgs"]:
            messagebox.showinfo("FM-1 Switch", "Pick a firmware in the list first.")
            return
        pkg = state["pkgs"][i]
        if backend is None:
            messagebox.showerror("FM-1 Switch", NO_MIDI.format(midi_err))
            return
        warn = flash_warning(pkg, last_hardware(rescue_dir()))
        if warn and not messagebox.askyesno("FM-1 Switch – memory chip", warn, icon="warning",
                                            default="no"):
            return
        if not messagebox.askyesno("FM-1 Switch", f"Install {pkg.path.name} ({pkg.product})?\n\n"
                                                  "Keep the cable in until it is done."):
            return
        busy(True)
        bar["value"] = 0

        def step(kind, value=None):
            ui(status.set, step_text(kind, value))
            if kind == "write":
                ui(bar.configure, value=value)
            elif kind == "done":
                ui(bar.configure, value=100)
                ui(dev_var.set, f"FM-1: runs {value}")
                start_mark(rescue_dir(), "firmware")
                ui(set_tone, "ok")

        def work():
            try:
                install(backend, pkg, step, port=port)
            except I.InstallError as e:
                ui(status.set, f"Error: {e}")
                ui(messagebox.showerror, "FM-1 Switch", install_error_text(e))
            except Exception as e:
                ui(status.set, f"Error: {e}")
            ui(busy, False)
        port = selected_port()
        threading.Thread(target=work, daemon=True).start()

    def add_folder():
        d = filedialog.askdirectory(title="A folder with .fwsc files")
        if d and Path(d) not in folders:
            folders.append(Path(d))
            refresh()

    ib = ttk.Button(row, text="Install", style="Accent.TButton", width=12, command=do_install)
    rb = ttk.Button(row, text="Through update mode…", command=lambda: do_rescue())
    check_b = ttk.Button(card_btns, text="Check again", command=check_device)
    adapter_b = ttk.Button(adv, text="Adapter rescue (experimental)…",
                           command=lambda: do_rescue("transporter"))
    buttons = [check_b, ttk.Button(row, text="Add folder…", command=add_folder),
               ttk.Button(row, text="Refresh", command=refresh), ib, rb, adapter_b, *tb_]
    check_b.pack()
    adapter_b.pack(side="right")
    buttons[1].pack(side="left", padx=(0, px(8)))
    buttons[2].pack(side="left")
    ib.pack(side="right")
    rb.pack(side="right", padx=(0, px(8)))
    stop_b = ttk.Button(card_btns, text="Stop", command=stop_rescue)

    def open_start():
        """Start here: the three first things, in order, each ticked from what is really there"""
        d = rescue_dir()
        w = dialog("FM-1 Switch – Start here", 640, 740)
        f = ttk.Frame(w, padding=(px(20), px(16)))
        f.pack(fill="both", expand=True)
        wrap = px(580)
        ttk.Label(f, text="Start here", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text=START_INTRO, wraplength=wrap, justify="left").pack(anchor="w", pady=(px(4), px(10)))
        rows = {}
        def first_backup(go):
            def run():
                if start_state(d)["backup"] or messagebox.askyesno(
                        "FM-1 Switch – Start here", "There is no backup of this FM-1 yet. Without one, a "
                        "firmware that goes wrong cannot be undone. Go on anyway?", icon="warning",
                        default="no", parent=w):
                    go()
            return run
        acts = {"backup": do_inspect, "protect": lambda: open_protect(),
                "firmware": first_backup(lambda: show_install())}
        for i, (k, title, text, button) in enumerate(START_STEPS, 1):
            c = ttk.Frame(f, style="Card.TFrame", padding=(px(14), px(10)))
            c.pack(fill="x", pady=(0, px(8)))
            mark = tk.StringVar()
            top_ = tk.Frame(c, background=C["card"])
            top_.pack(fill="x")
            ttk.Label(top_, textvariable=mark, style="Badge.TLabel").pack(side="left")
            ttk.Label(top_, text=f"  {i}. {title}", style="Head.TLabel").pack(side="left")
            ttk.Label(c, text=text, style="Muted.TLabel", wraplength=wrap - px(30), justify="left").pack(
                anchor="w", pady=(px(4), px(6)))
            br = tk.Frame(c, background=C["card"])
            br.pack(fill="x")
            b = ttk.Button(br, text=button, command=acts[k])
            b.pack(side="left")
            extra = None
            if k == "backup":
                extra = ttk.Button(br, text="Open the backup folder", command=lambda: open_folder(d))
            elif k == "protect":
                extra = ttk.Button(br, text="Skip this step", command=lambda: (start_mark(d, "protect", "skipped"),
                                                                              refresh()))
            if extra is not None:
                extra.pack(side="left", padx=(px(8), 0))
            if k == "firmware":
                for t, url in ((HUB_LINK, HUB_PAGE), (MAKER_TIP, MAKER_TIP_URL)):
                    lk = ttk.Label(c, text=t, style="Muted.TLabel", wraplength=wrap - px(30), justify="left",
                                   **({"foreground": C["accent"], "cursor": "hand2"} if url else {}))
                    lk.pack(anchor="w", pady=(px(6), 0))
                    if url:
                        lk.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
            rows[k] = (mark, b)
        ttk.Label(f, text=START_AFTER, style="Muted.TLabel", wraplength=wrap, justify="left").pack(anchor="w")
        done_var = tk.StringVar()
        ttk.Label(f, textvariable=done_var, style="Head.TLabel", wraplength=wrap).pack(anchor="w", pady=(px(8), 0))
        foot = ttk.Frame(f)
        foot.pack(side="bottom", fill="x")
        ttk.Button(foot, text="Close", command=w.destroy).pack(side="right")
        ttk.Button(foot, text="Start over (another FM-1)",
                   command=lambda: (start_over(d), refresh())).pack(side="left")
        which = tk.StringVar()
        ttk.Label(f, textvariable=which, style="Muted.TLabel", wraplength=wrap).pack(side="bottom", anchor="w",
                                                                                   pady=(0, px(6)))

        def refresh():
            if not w.winfo_exists():
                return
            st = start_state(d)
            nxt = start_next(st)
            for i, (k, *_) in enumerate(START_STEPS):
                mark, b = rows[k]
                mark.set({True: "✓ DONE", "skipped": "SKIPPED"}.get(st[k], "NEXT" if k == nxt else ""))
                b.configure(style="Accent.TButton" if k == nxt else "TButton")
            done_var.set(START_DONE if nxt is None else "")
            which.set(("The ticks are for the FM-1 backed up here on "
                       + time.strftime("%Y-%m-%d %H:%M", time.localtime(st["backup_when"]))
                       + ". Another FM-1? Press Start over.") if st["backup_when"] else "")
            start_b.configure(style="TButton" if nxt is None else "Accent.TButton")
        def tick():
            if w.winfo_exists():
                refresh()
                w.after(2000, tick)
        tick()

    start_b = ttk.Button(head, text="Start here", style="Accent.TButton", command=open_start)
    ttk.Button(head, text="About", command=lambda: messagebox.showinfo(
        "About FM-1 Switch", f"FM-1 Switch {app_version()}\n\n" + DISCLAIMER
        + "\n\nSource and licence: github.com/Saintpid/FM1-Switch (GPL-3.0)")).pack(side="right")
    start_b.pack(side="right", padx=(0, px(8)))
    if start_next(start_state(rescue_dir())) is None:
        start_b.configure(style="TButton")

    def closing():
        if state["busy"]:
            messagebox.showinfo("FM-1 Switch", "Please keep this window open until it is done. If the "
                                "computer is asking for permission, answer that window first. While it waits "
                                "for the FM-1 you can press Stop; once it writes, let it finish.", parent=root)
            return
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", closing)
    d0 = rescue_dir()
    if not accepted(d0):
        if not messagebox.askokcancel("FM-1 Switch – read this first", DISCLAIMER + "\n\nOK: I accept this. "
                                                                      "Cancel: quit."):
            root.destroy()
            return
        accept(d0)
    status.set("Getting ready…")
    root.update_idletasks()
    if mac and not messagebox.askokcancel(
            "FM-1 Switch – Prepare access",
            "Next, FM-1 Switch will open MIDI and look for firmware files in its folders, including "
            "Downloads. macOS may ask for access. Take your time reading each request: no firmware "
            "is being installed and no device timer is running.\n\n"
            "Recovery asks separately for administrator access when you start it.", parent=root):
        root.destroy()
        return
    backend, midi_err = open_midi()
    refresh()
    check_device(0)
    root.mainloop()


def rescue_main(argv):
    """--rescue: fm1_rescue in this program, as administrator / root (a windowed .exe has no console)"""
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
    return R.main(argv)


def reexec_native(platform=sys.platform, frozen=None, sysctl=_sysctl_int, env=os.environ, execv=os.execv):
    """the Mac app started translated (an x86_64 parent, Open using Rosetta) starts itself again with the
    hardware's own slice; once (FM1_SWITCH_NATIVE), and only the built app (a script follows its Python)"""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if platform != "darwin" or not frozen or env.get("FM1_SWITCH_NATIVE") or sysctl("sysctl.proc_translated") != 1:
        return False
    env["FM1_SWITCH_NATIVE"] = "1"
    execv("/usr/bin/arch", ["/usr/bin/arch", "-" + mac_hardware_arch(sysctl), sys.executable, *sys.argv[1:]])
    return True


if __name__ == "__main__":
    reexec_native()
    if len(sys.argv) > 1 and sys.argv[1] == "--native-process-probe":
        import json
        print(json.dumps(native_probe()))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--rescue":
        sys.exit(rescue_main(sys.argv[2:]))
    main()
