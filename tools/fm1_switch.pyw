#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""FM-1 Switch: a small window to put any FM-1 firmware file (.fwsc) on the FM-1, Wild or NB303,
whatever its number. M-UPGRADE only takes a number higher than the one installed; this takes any
package for the FM-1 model (the update itself is fm1_install.py's: Felucca's, over USB-MIDI).

It lists the .fwsc files in its folders (its own, and "Firmware Wild" / "Firmware NB303" /
"Firmware n001se" / "Firmware Original" beside it or under C:\\Projects\\M-Vave FM1 n001se
(~/M-Vave FM1 n001se on a Mac), and Downloads, if they are there; more with Add folder), newest
first, reads what the FM-1 runs, and installs the one chosen with a progress bar. M-VAVE's own
V15 (no Felucca loader) is not listed, but Rescue… finds it there.

Check FM-1 tells what state the FM-1 is in (running, an install cut off, the chip's own update mode,
or nothing on USB) and what to do. M-VAVE's SMK-37 Pro / Elite, MKE-P37 and Starrykey 37 Play (the
FM-1's chip and update; not yet tried on a real keyboard) take their own firmware, M-VAVE's stock
packages included, over USB-MIDI; never an FM-1's, and Rescue refuses them. Rescue… puts M-VAVE's stock firmware back on an FM-1 whose
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
    if R.sha256(raw) == R.STOCK_SHA256:
        return Package(f, product, "v15")
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
                                       "installed over USB-MIDI here: use M-UPGRADE, or Check / unbrick… "
                                       "and Rescue, which puts any FM-1 firmware back")
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
    text = (f"{pkg.path.name} ({pkg.product}) does not support the UCUN UC25HQ80 flash (B36014): {why}.\n\n"
            "FM-1s sold since autumn 2026 have this flash. On such a unit this firmware loses its saves "
            "at power-off and the update may stop. Wild from FM-1_864 and NB303 from FM-1_908 support it.")
    if hardware:
        info, when = hardware
        text += ("\n\nThe last FM-1 identified here (Hardware / backup, "
                 f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(when))}) has "
                 f"{info.get('flash_name', '?')} ({info.get('jedec_id', '?')}).")
    return text + ("\n\nThe FM-1 does not report its flash over MIDI. Install anyway only if this FM-1 "
                   "has the Puya P25Q80H flash (856014).")


STEP_TEXT = {"start": "Starting the update on {}", "check": "Checking the package ({} reads)",
             "loader": "Switching to update mode", "write": "Writing {}%", "reboot": "Restarting",
             "done": "Done: the FM-1 runs {}"}


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
        return True, f"Recovery confirmed: the FM-1 runs {identity}."
    if identity:
        return False, f"Flash verified, but the FM-1 reports {identity}; expected {product}."
    return False, ("Flash verified. Startup has not been confirmed over MIDI. "
                   "If using an adapter, disconnect it after writing has finished, then reconnect "
                   "the FM-1 directly by USB. Check the screen and press Check / unbrick….")


def install_error_text(error):
    text = str(error)
    if error.code in ("stopped", "lost", "noloader"):
        text += ("\n\nSome FM-1 units use UCUN UC25HQ80 flash. Older firmware or update loaders "
                 "may reject it, causing failed updates or saves lost after power-off. This error "
                 "alone does not identify the chip. Hardware / backup… can read it if the device "
                 "already reaches the chip's UBOOT mode. Use a release whose author confirms UCUN "
                 "support; installing successfully does not prove that saves persist.")
    return text


def hardware_text(info):
    return (f"{info['flash_name']} — JEDEC {info['jedec_id']}\n"
            f"Chip key {info['chip_key']}, flash type {info['flash_type']}.\n"
            "Firmware compatibility and persistent saves are not established by this identification.")


def step_text(kind, value=None):
    return STEP_TEXT[kind].format(value if value is not None else "")


NO_MIDI = ("This copy of FM-1 Switch cannot use MIDI, so it cannot hear the FM-1 ({}). Run "
           "pip install -r requirements.txt in the FM1-Switch folder, or use FM1-Switch.exe / the Mac app, "
           "which have it built in.")


def open_midi(make=None):
    """(backend, None), or (None, why) when mido or its MIDI backend (python-rtmidi) is missing: mido
    imports without it and only fails at the first port list, which looked like no FM-1"""
    try:
        b = (make or I.MidoBackend)()
        b.input_names()
        return b, None
    except Exception as e:
        return None, str(e) or type(e).__name__


UBOOT_KEY = bytes.fromhex("F0 22 24 35 7D F7")


class BackupMidiSession:
    """An identified, open target; the key is sent only after helper readiness.

    Stock V15 only: FM-1-transporter/src/pio_host.c documents this ROM-entry key.
    A MIDI identity is not cryptographic firmware attestation. Real-device acceptance
    of the direct USB path remains required. No upgrade or flash packets are sent.
    """
    def __init__(self, link, identity):
        self.link, self.identity, self.sent = link, identity, False

    def enter(self, stopped=lambda: False):
        if self.sent or stopped():
            return False
        ident = I.handshake(self.link, 1)
        if not ident or ident.text != self.identity:
            raise RuntimeError("FM-1 identity changed or disappeared; recovery key not sent.")
        if stopped():
            return False
        self.sent = True  # never retry a mode-changing command
        if not self.link.send(UBOOT_KEY):
            raise RuntimeError("Recovery key delivery failed; power-cycle the FM-1 before retrying.")
        return True

    def close(self):
        self.link.close()


def prepare_backup_midi(backend, port=None):
    """Fail closed on ambiguous ports/unknown firmware; no key during preparation.

    No matching MIDI port means wait for an already-recovering unit, never a
    broadcast. Explicit selection must match a complete input name.
    """
    if backend is None:
        return None
    names = backend.input_names()
    candidates = [n for n in names if n == port] if port else [n for n in names if I.PORT_RE.search(n)]
    if port and not candidates:
        raise RuntimeError("Selected MIDI port is missing. Refresh the port list and select the FM-1 again.")
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError("Multiple MIDI targets found. Select the FM-1 MIDI port explicitly before BACKUP.")
    name = candidates[0]
    base = lambda n: re.sub(r"\s+\d+$", "", n)
    outs = backend.output_names()
    matches = [name] if name in outs else [n for n in outs if base(n) == base(name)]
    if len(matches) != 1:
        raise RuntimeError("Cannot pair the selected FM-1 input with a unique MIDI output; no key sent.")
    link = backend.open(name, matches[0])
    try:
        ident = I.handshake(link, 2)
        if not ident or ident.model != "FM-1" or ident.version != 15:
            raise RuntimeError("Automatic BACKUP recovery entry currently supports stock FM-1 V15 only. "
                               "Other firmware and update loaders are refused; no recovery key sent.")
        return BackupMidiSession(link, ident.text)
    except Exception:
        link.close()
        raise


def row_values(p):
    """the firmware list's columns for a Package: family, file, identity, note"""
    return (p.family, p.path.stem, "V15" if p.kind == "v15" else p.product,
            {"felucca": "", "stock": "stock"}.get(p.kind, "rescue only"))


def keyboard(ident):
    """the M-VAVE keyboard model (SMK-37 Pro...) an identity is from, or None for an FM-1"""
    m = I.model_of(ident or "")
    return m if m in I.KEYBOARDS else None


KEYBOARD_NOTE = ("Support for M-VAVE's keyboards is new and not yet tried on a real one. FM-1 firmware is "
                 "refused on it, and Rescue is for the FM-1 only.")


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
        return (f"FM-1: runs {ident}",
                "Choose a firmware file and press Install. If the screen is stuck, hold OCT- and OCT+ "
                "for 5 seconds: that is the update mode, and Install works from there.")
    if state == "loader":
        return (f"FM-1: in update mode ({ident})",
                "An install was cut off, and the FM-1 waits for it. Choose a firmware file (the same "
                "one, or any other) and press Install to finish it.")
    if state == "uboot":
        return ("FM-1: in the chip's own update mode",
                "Its firmware does not start. Press Rescue… to put M-VAVE's stock firmware back; "
                "then install Wild or NB303 as usual. (An SMK-37 keyboard in this mode: Rescue refuses "
                "it, it is for the FM-1 only.)")
    return ("FM-1: not found",
            "Connect the FM-1 directly with a USB data cable and switch it on. Close other MIDI apps.\n\n"
            "Still not found? Choose Check / unbrick for step-by-step help."
            + ("" if probe_ok else " USB recovery detection is unavailable: pyusb is missing."))


# ----------------------------------------------------------------- guide ---
# The unbrick guide: what the FM-1 says first; the user's answers only where it says nothing; then
# one thing to try, behind one button.

SCREEN = (("normal", "Its usual picture (or Wild / NB303 running)"),
          ("safe", "\"SAFE MODE\""),
          ("update", "\"UPDATE MODE\""),
          ("dark", "Nothing: the screen stays dark"),
          ("loop", "It starts, goes blank, and starts again (over and over)"))
LAST = (("cut", "An install was cut off (the cable, the computer slept, an app closed)"),
        ("nostart", "I installed a firmware, and now it does not start"),
        ("other", "Nothing special, or I don't know"))


class Plan:
    """what to do: a title, the steps for the user, and what the button does
    (action: "install", "finish", "check", "rescue" or "hardware"; button: its text)"""

    def __init__(self, action, title, steps, button):
        self.action, self.title, self.steps, self.button = action, title, list(steps), button


CABLE = ("Use a USB data cable straight into the computer, no hub (many cables only charge: try "
         "another one), and another USB port.",
         "Close every other app that may hold the FM-1's MIDI port: a DAW, M-UPGRADE, a browser tab "
         "with a web installer.")
TRANSPORTER = "https://github.com/kurogedelic/FM-1-transporter"


def needs_questions(state):
    """only when the FM-1 says nothing does the guide ask"""
    return state == "none"


def plan(state, ident=None, screen=None, last=None, rescue_failed=False):
    """the Plan for what was found (diagnose()) and what the user answered (SCREEN, LAST)"""
    if rescue_failed:
        return Plan("hardware", "The FM-1 never showed up in the chip's update mode",
                    ["Its firmware does not reach the chip's update mode on its own, so it needs a "
                     "little hardware: an FM-1-transporter, an RP2040 board (a Seeed XIAO RP2040) wired "
                     "to the FM-1's USB lines with three wires (D+, D-, GND; never 5 V).",
                     "It wakes the chip's update mode from outside and puts the firmware back the same "
                     "careful way (a backup first, the boot code never written).",
                     "Before that: charge the FM-1 for half an hour and try Rescue once more.",
                     "With the adapter wired and on USB: Try with the adapter (experimental). It has not "
                     "been tried on an FM-1 from this app yet."],
                    "Open the FM-1-transporter guide")
    kb = keyboard(ident)
    if kb and state in ("running", "loader"):
        return Plan("install" if state == "running" else "finish",
                    f"The {kb} runs {ident}: it is not bricked" if state == "running"
                    else f"An install was cut off: the {kb} waits for it",
                    [f"Choose a firmware for the {kb} in the list (M-VAVE's own stock file, or a port made "
                     f"for the {kb}) and press Install. Do not unplug it while it writes.", KEYBOARD_NOTE],
                    "Install the chosen file" if state == "running" else "Finish the install")
    if state == "running":
        return Plan("install", f"The FM-1 runs {ident}: it is not bricked",
                    ["Choose a firmware file in the list and press Install.",
                     "If the screen is stuck while it runs, hold OCT- and OCT+ for 5 seconds: that is the "
                     "update mode, and Install works from there too."],
                    "Install the chosen file")
    if state == "loader":
        return Plan("finish", "An install was cut off: the FM-1 waits for it",
                    ["Nothing is broken: the FM-1 is in update mode and will stay there until an install "
                     "finishes.",
                     "Choose a firmware file (the same one, or any other) and the install starts over and "
                     "finishes. Do not unplug the FM-1 while it writes."],
                    "Finish the install")
    if state == "uboot":
        return _rescue("The firmware does not start: the FM-1 is in the chip's own update mode",
                       ["The chip waits for a computer to put a firmware back. Rescue puts M-VAVE's own "
                        "(V15) back, and saves the whole flash first.",
                        "An M-VAVE SMK-37 keyboard looks the same in this mode: Rescue refuses it (after the "
                        "backup) and writes nothing, as it is for the FM-1 only."])
    if screen in ("normal", "safe", "update"):
        what = {"normal": "It runs", "safe": "It is in Wild's safe mode (USB on, no sound)",
                "update": "It is in update mode"}[screen]
        return Plan("check", f"{what}, but the computer does not hear it",
                    [*CABLE, "Switch the FM-1 off and on, then press Check again."
                     + (" When it is found, Install finishes the update." if screen == "update" else "")],
                    "Check again")
    if screen == "dark":
        return _rescue("The screen stays dark",
                       ["Charge it first: plug it into a charger for 10 minutes. A flat battery looks "
                        "just like this.", CABLE[0],
                        "If it is still dark, its firmware does not start. Rescue catches it in the chip's "
                        "own update mode"
                        + (", which a firmware that keeps crashing reaches by itself." if last != "other"
                           else ".")])
    if screen == "loop":
        return _rescue("Its firmware crashes while it starts",
                       ["A firmware that keeps crashing reaches the chip's own update mode by itself, "
                        "after a few tries. Rescue waits for that moment and catches it."])
    return Plan("check", "The FM-1 was not found", [*CABLE, "Then press Check again."], "Check again")


def _rescue(title, why):
    return Plan("rescue", title,
                [*why,
                 "Press Try to unbrick and choose the firmware to put back (M-VAVE's V15 is the safest; "
                 "any FM-1 firmware works). It asks for " + ("an administrator" if sys.platform == "win32"
                                                              else "your password") + ", then waits.",
                 "Then switch the FM-1 off and on (or leave it restarting). When it goes blank, it is "
                 "caught; the write takes about a minute. Do not unplug it.",
                 "Afterwards it runs the firmware you chose; from V15 you can install Wild or NB303 as usual."],
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

RESCUE_RISK = ("I understand that this writes to my FM-1's flash at my own risk, that it can make the "
               "FM-1 unusable for good, and that nobody (Noisebud, the author, M-VAVE) is responsible.")


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
    a = (["--inspect"] if inspect else [str(target), "--write"]) + ["--backup-dir", str(d), "--loader", str(d / R.LOADER_NAME),
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


RESCUE_STEP = {"ready": "Access ready. Connecting to the FM-1; follow the next instruction.",
               "backup_read": "Reading backup {}/2. Firmware has not been changed.",
               "backup": "Two matching reads and saved backup verified: {}", "write": "Writing {}% — keep connected.",
               "done": "Flash verified. Checking startup…"}


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
PALETTE = {True: {"bg": "#181b21", "fg": "#ecedf2", "muted": "#a2a8b5", "card": "#22262e",
                  "line": "#3a414e", "accent": "#8aa4ff", "ok": "#73c49a", "warn": "#e1b675",
                  "bad": "#ed9296", "off": "#818a9b", "hover": "#2d3340"},
           False: {"bg": "#f5f6f8", "fg": "#202635", "muted": "#667085", "card": "#ffffff",
                   "line": "#d9dee7", "accent": "#3759d7", "ok": "#25835d", "warn": "#99672b",
                   "bad": "#b44752", "off": "#929baa", "hover": "#edf0f6"}}


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
    body_f = (family, 13 if mac else 11)
    small_f = (family, 11 if mac else 10)
    head_f = (family, 15 if mac else 12, "bold")
    title_f = (family, 24 if mac else 22, "bold")
    button_f = (family, 13 if mac else 10, "bold")
    st = ttk.Style()
    st.configure(".", font=body_f)
    st.configure("TFrame", background=C["bg"])
    st.layout("TLabel", [("Label.padding", {"sticky": "nswe", "children": [
        ("Label.label", {"sticky": "nswe"})]})])
    st.configure("TLabel", background=C["bg"], foreground=C["fg"], font=body_f)
    st.configure("Card.TFrame", background=C["card"])
    # Opaque, antialiased corners match the parent surface. Transparent corners
    # expose ttk's rectangular widget background on some installed themes.
    button_images = []
    def rounded_image(colour, parent, outline, focused=False):
        radius = max(4, px(10))
        size = radius * 2 + px(8)
        stroke = max(1, px(2 if focused else 1))
        outline = (C["fg"] if colour == C["accent"] else C["accent"]) if focused else outline
        def rgb(c):
            return tuple(int(c[i:i+2], 16) for i in (1, 3, 5))
        bg, edge, fill = rgb(parent), rgb(outline), rgb(colour)
        def coverage(x, y, inset):
            r = max(1, radius - inset)
            count = 0
            for ox, oy in ((.25, .25), (.75, .25), (.25, .75), (.75, .75)):
                xx, yy = x + ox, y + oy
                if inset <= xx < size-inset and inset <= yy < size-inset:
                    dx = max(inset+r-xx, xx-(size-inset-r), 0)
                    dy = max(inset+r-yy, yy-(size-inset-r), 0)
                    count += dx*dx + dy*dy <= r*r
            return count / 4
        rows = []
        for y in range(size):
            row = []
            for x in range(size):
                outer, inner = coverage(x, y, 0), coverage(x, y, stroke)
                colour_rgb = tuple(round(bg[i]*(1-outer) + edge[i]*(outer-inner) + fill[i]*inner)
                                   for i in range(3))
                row.append("#%02x%02x%02x" % colour_rgb)
            rows.append("{" + " ".join(row) + "}")
        im = tk.PhotoImage(master=root, width=size, height=size)
        im.put(" ".join(rows))
        button_images.append(im)
        return im
    for name in ("Backup", "Check", "Install", "Rescue", "Utility"):
        primary = name in ("Backup", "Install")
        parent = C["card"] if name in ("Backup", "Check") else C["bg"]
        colour = C["accent"] if primary else C["card"]
        hover = ("#a3b7ff" if dark else "#2c48bb") if primary else C["hover"]
        foreground = ("#151d35" if dark else "#ffffff") if primary else C["fg"]
        outline = colour if primary else C["line"]
        style, element = name + ".TButton", name + ".rounded"
        normal = rounded_image(colour, parent, outline)
        disabled = rounded_image(C["hover"], parent, C["line"])
        active = rounded_image(hover, parent, hover if primary else C["line"])
        focus = rounded_image(colour, parent, outline, True)
        st.element_create(element, "image", normal, ("disabled", disabled),
                          ("pressed", active), ("focus", focus), ("active", active),
                          border=px(10), sticky="nswe")
        st.layout(style, [(element, {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"})]})]})])
        st.configure(style, background=parent, foreground=foreground,
                     font=(family, small_f[1]) if name == "Utility" else button_f,
                     anchor="center", padding=(px(12 if name == "Utility" else 16), px(4)))
        st.map(style, foreground=[("disabled", C["muted"])])
    panel_image = rounded_image(C["card"], C["bg"], C["line"])
    st.element_create("Card.panel", "image", panel_image, border=px(10), sticky="nswe")
    st.layout("Card.TFrame", [("Card.panel", {"sticky": "nswe"})])
    st.configure("Surface.TFrame", background=C["card"])
    st.configure("Muted.TLabel", foreground=C["muted"], font=small_f)
    st.configure("Card.Muted.TLabel", background=C["card"], foreground=C["muted"], font=small_f)
    st.configure("Card.Head.TLabel", background=C["card"], font=head_f)
    st.configure("Head.TLabel", font=head_f)
    st.configure("Title.TLabel", font=title_f)
    st.configure("Section.TLabel", foreground=C["fg"], font=(family, small_f[1], "bold"))
    # Override inherited Sun Valley state maps as well as default colours.
    for label_style, surface, ink in (("TLabel", C["bg"], C["fg"]),
                                       ("Muted.TLabel", C["bg"], C["muted"]),
                                       ("Card.Muted.TLabel", C["card"], C["muted"]),
                                       ("Card.Head.TLabel", C["card"], C["fg"])):
        st.map(label_style, background=[("!disabled", surface)], foreground=[("!disabled", ink)])
    st.configure("Treeview", rowheight=px(38), font=body_f, background=C["card"],
                 fieldbackground=C["card"], foreground=C["fg"], borderwidth=0)
    st.map("Treeview", background=[("selected", "#344266" if dark else "#e8edfc")],
           foreground=[("selected", C["fg"])])
    st.layout("Treeview.Heading", [("Treeheading.padding", {"sticky": "nswe", "children": [
        ("Treeheading.text", {"sticky": "we"})]})])
    st.configure("Treeview.Heading", font=(family, small_f[1], "bold"), foreground=C["muted"], padding=(px(8), px(8)))
    for element, colour in (("Quiet.trough", C["line"]), ("Quiet.pbar", C["accent"])):
        strip = tk.PhotoImage(master=root, width=1, height=px(3))
        strip.put(colour, to=(0, 0, 1, px(3)))
        button_images.append(strip)
        st.element_create(element, "image", strip, sticky="nswe")
    st.layout("Quiet.Horizontal.TProgressbar", [("Quiet.trough", {"sticky": "we", "children": [
        ("Quiet.pbar", {"side": "left", "sticky": "ns"})]})])
    st.configure("Quiet.Horizontal.TProgressbar", troughcolor=C["line"], background=C["accent"],
                 lightcolor=C["accent"], darkcolor=C["accent"], borderwidth=0, thickness=3)
    root.geometry(f"{px(840)}x{px(640)}")
    root.minsize(px(720), px(560))
    dark_titlebar(root, dark)
    folders = default_folders()
    state = {"pkgs": [], "busy": False, "rescue": None}
    tone = {"value": "off"}

    top = ttk.Frame(root, padding=(px(24), px(22), px(24), px(22)))
    top.pack(fill="both", expand=True)

    head = ttk.Frame(top)
    head.pack(fill="x")
    tk.Label(head, text="FM-1 Switch", background=C["bg"], foreground=C["fg"], font=title_f, padx=0, pady=0).pack(side="left")
    tk.Label(head, text=f"  v{app_version()}", background=C["bg"], foreground=C["muted"], font=small_f, padx=0, pady=0).pack(side="left", anchor="s", pady=(0, px(5)))

    card = ttk.Frame(top, style="Card.TFrame", padding=(px(20), px(18)))
    card.pack(fill="x", pady=(px(22), px(22)))
    card.columnconfigure(1, weight=1)
    dot = tk.Canvas(card, width=px(14), height=px(14), highlightthickness=0, bd=0, background=C["card"])
    dot.grid(row=0, column=0, sticky="w", padx=(0, px(10)))
    dot_id = dot.create_oval(px(2), px(2), px(12), px(12), fill=C["off"], outline="")
    dev_var = tk.StringVar(value="FM-1: not read yet")
    tk.Label(card, textvariable=dev_var, background=C["card"], foreground=C["fg"], font=head_f, anchor="w", padx=0, pady=0).grid(row=0, column=1, sticky="w")
    status = tk.StringVar(value="Connect the FM-1 with a USB data cable, close other MIDI apps.")
    msg = tk.Label(card, textvariable=status, background=C["card"], foreground=C["muted"], font=small_f, anchor="nw", justify="left", width=1, wraplength=px(360), padx=0, pady=0)
    msg.grid(row=1, column=1, sticky="we", pady=(px(4), 0))
    card_btns = ttk.Frame(card, style="Surface.TFrame")
    card_btns.grid(row=0, column=2, rowspan=2, sticky="ne", padx=(px(12), 0))
    msg.bind("<Configure>", lambda e: msg.configure(wraplength=max(1, e.width)))

    def set_tone(t):
        tone["value"] = t
        dot.itemconfigure(dot_id, fill=C.get(t, C["off"]))

    row = ttk.Frame(top)                        # (packed from the bottom: the list takes what is left)
    row.pack(side="bottom", fill="x", pady=(px(12), 0))
    bar = ttk.Progressbar(top, maximum=100, style="Quiet.Horizontal.TProgressbar")
    bar.pack(side="bottom", fill="x", pady=(px(12), 0))
    list_head = ttk.Frame(top)
    list_head.pack(fill="x", pady=(0, px(10)))
    ttk.Label(list_head, text="Firmware files", style="Section.TLabel").pack(side="left")
    ttk.Label(list_head, text="Choose a file, then Install", style="Muted.TLabel").pack(side="right")
    lf = ttk.Frame(top)
    lf.pack(fill="both", expand=True)
    cols = ("fam", "name", "id", "note")
    lst = ttk.Treeview(lf, columns=cols, show="headings", selectmode="browse", height=6)
    for c, t, w, s in (("fam", "Family", 90, False), ("name", "File", 280, True), ("id", "Identity", 120, False),
                       ("note", "Status", 110, False)):
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
    port_row = ttk.Frame(top)
    port_row.pack(side="bottom", fill="x", pady=(px(6), 0))
    ttk.Label(port_row, text="MIDI input:", style="Muted.TLabel").pack(side="left")
    port_box = ttk.Combobox(port_row, textvariable=port_var, state="readonly", width=42)
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
            status.set("Looking for the FM-1 on USB, in every mode it can be in (a few seconds)…")

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

    def do_rescue(via="usb"):
        """Rescue: 1 choose the firmware to put back, 2 accept the risk, 3 run it"""
        if state["rescue"]:                                     # the button is Stop while it waits
            open(state["rescue"] + ".stop", "w").close()
            status.set("Stopping (only while it waits: a write in progress is finished)…")
            return
        if state["busy"]:
            return
        d = rescue_dir()
        choices = rescue_choices(folders + [d])
        w = dialog("FM-1 Switch – Rescue" + (" through the adapter (experimental)" if via != "usb" else ""),
                   680, 680)
        w.grab_set()
        f = ttk.Frame(w, padding=(px(20), px(16)))
        f.pack(fill="both", expand=True)
        wrap = px(630)
        ttk.Label(f, text="1  Choose the firmware to put back", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text="M-VAVE's own V15 is the safest choice: it is what the FM-1 came with. Any other "
                          "FM-1 firmware works too (its app is written; the FM-1's own boot part stays).",
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
                     "Not V15 and not Felucca-based: if it does not start, it may not fall back to the "
                     "chip's update mode by itself, and the next rescue may need the adapter.")
        box.bind("<<TreeviewSelect>>", picked)
        ucun_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Allow experimental UCUN UC25HQ80 recovery (direct USB only)",
                        variable=ucun_var).pack(anchor="w", pady=(px(10), 0))
        ttk.Label(f, text="If UCUN is detected, leaving this unchecked saves a backup and refuses writes. "
                  "Puya recovery writes normally. UCUN recovery has simulation coverage; actual device "
                  "acceptance is pending. Choose firmware with "
                  "confirmed UCUN support. Other chips are still refused.", style="Muted.TLabel",
                  wraplength=wrap, justify="left").pack(anchor="w")
        ttk.Label(f, text="2  Your own risk", style="Head.TLabel").pack(anchor="w", pady=(px(16), 0))
        ttk.Label(f, text="The whole flash is saved first, the chip's boot code is never written, and nothing "
                          "is written unless the chip and its flash are the FM-1's. Even so, this can fail.",
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
        ttk.Label(f, text="Next: prepare files, request system access, verify two backups, then write. "
                  "You can read the system permission request at your own pace.",
                  style="Muted.TLabel", wraplength=wrap).pack(anchor="w", pady=(px(8), 0))
        go = ttk.Button(row2, text="Prepare recovery", style="Accent.TButton", command=start)
        go.state(["disabled"])
        go.pack(side="right")
        ttk.Button(row2, text="Cancel", command=w.destroy).pack(side="right", padx=(0, px(8)))

    def do_inspect():
        if state["busy"]:
            return
        if messagebox.askokcancel("FM-1 Switch – BACKUP",
                "Save the full 1 MiB flash: boot code, firmware and stored device/user data. "
                "This does not erase or program flash.\n\n"
                "Experimental: for a running stock V15 FM-1, this asks the device to enter chip "
                "recovery (UBOOT) over USB-MIDI after system access is ready. Save your changes first. "
                "Other running firmware is refused. Devices already in UBOOT need no MIDI command. "
                "OCT- and OCT+ enter a different, firmware update mode. "
                "Do not interrupt an installation or damage firmware to force UBOOT.\n\n"
                "1. Connect only the target FM-1 directly with a USB data cable, without a hub.\n"
                "2. Select the FM-1 MIDI port if needed. Press OK to allow recovery entry, "
                "prepare the checked RAM loader and approve system access.\n"
                "3. Keep it connected while two full reads are compared and the saved file is verified.\n\n"
                f"Save folder: {rescue_dir()}\n\n"
                "Success shows the backup path. Switch the FM-1 off and on afterwards. "
                "Unknown chips and keyboards are refused. Keep backups private: they contain user data.", parent=root):
            run_rescue(None, rescue_dir(), "usb", inspect=True)

    def run_rescue(target, d, via, inspect=False, allow_ucun=False):
        busy(True)
        port = selected_port()
        status.set("Preparing recovery files. Firmware has not been changed.")
        bar["value"] = 0
        fd, prog = tempfile.mkstemp(prefix="fm1-rescue-", suffix=".jsonl")
        os.close(fd)
        state["rescue"] = prog

        def work():
            midi_session = None
            try:
                path = None if inspect else (target.path if target else R.download_stock(str(d), lambda t: ui(status.set, t.strip())))
                ui(status.set, "Getting JieLi's flash loader…")
                R.get_loader(None, str(d), lambda t: ui(status.set, t.strip()))
                if inspect and via == "usb" and R.uboot_present() is not True:
                    midi_session = prepare_backup_midi(backend, port)
                cmd = helper_command(rescue_args(path, d, prog, target is not None and target.kind != "v15", via,
                                                inspect=inspect, allow_ucun=allow_ucun))
                ui_wait(status.set, "Waiting for system approval. Take your time with the permission dialog. "
                        "The device timer starts after access is granted; firmware has not been changed."
                        if via == "usb" else "Connecting to the adapter. Firmware has not been changed.")
                wait = subprocess.Popen(cmd).wait if via != "usb" else run_elevated(cmd)
            except Exception as e:
                if midi_session:
                    midi_session.close()
                ui(status.set, f"Rescue not started: {e}")
                ui(messagebox.showerror, "FM-1 Switch – BACKUP" if inspect else "FM-1 Switch – Rescue", str(e))
                ui(end_rescue)
                return
            state["rescue"] = prog
            done, pos, end, hardware = {"code": None}, 0, None, None
            t = threading.Thread(target=lambda: done.update(code=wait()), daemon=True)
            t.start()
            transition_error = None
            def handle_events(ev):
                nonlocal end, hardware, transition_error
                for e in ev:
                    if "say" in e:
                        ui(status.set, e["say"])
                    elif e.get("step") == "hardware":
                        hardware = e["value"]
                        ui(dev_var.set, f"Flash: {hardware['flash_name']} ({hardware['jedec_id']})")
                    elif e.get("step") in RESCUE_STEP:
                        ui(status.set, RESCUE_STEP[e["step"]].format(e.get("value") or ""))
                        if e["step"] == "ready":
                            ui(rb.configure, text="Stop")
                            ui(rb.state, ["!disabled"])
                            if midi_session:
                                try:
                                    if midi_session.enter(lambda: Path(prog + ".stop").exists()):
                                        ui(status.set, "Recovery key sent to stock V15. Waiting for USB UBOOT; "
                                           "keep connected. No backup verified yet.")
                                except Exception as exc:
                                    transition_error = str(exc)
                                    Path(prog + ".stop").touch()
                        if e["step"] == "write":
                            ui(rb.state, ["disabled"])
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
            if midi_session:
                midi_session.close()
            if transition_error:
                end = {"end": "fail", "error": transition_error}
            if end and end["end"] == "ok" and inspect:
                msg = (hardware_text(end["hardware"]) + "\n\nBackup verified: " + end["backup"]
                       + ".\nThe .json beside it contains the chip details and backup hash. "
                       "No flash written. Power-cycle the device before using it again.")
                ui(status.set, "Hardware identified and backup verified. No flash written; power-cycle the device.")
                ui(messagebox.showinfo, "FM-1 Switch – Hardware / backup", msg)
            elif end and end["end"] == "ok":
                ui(status.set, "Flash verified. Waiting up to 40 seconds for the firmware to answer over MIDI…")
                try:
                    identity = confirm_rescue_boot(backend, end.get("product"), port=port)
                except Exception:
                    identity = None
                confirmed, msg = rescue_result(end.get("product"), identity)
                ui(dev_var.set, f"FM-1: {identity}" if confirmed else "FM-1: startup not confirmed")
                ui(set_tone, "ok" if confirmed else "off")
                ui(status.set, msg)
                ui(messagebox.showinfo, "FM-1 Switch", msg)
            elif end and "did not appear" in (end.get("error") or "") and via == "usb" and not inspect:
                ui(status.set, "Rescue: the FM-1 did not show up in the chip's update mode.")
                ui(open_guide, "rescue_failed")
            elif end:
                ui(status.set, f"Rescue: {end.get('error')}")
                msg = end.get("error") or "failed"
                if hardware:
                    msg += "\n\n" + hardware_text(hardware) + "\nHardware report: " + hardware["report"]
                ui(messagebox.showerror, "FM-1 Switch – Hardware / recovery", msg)
            else:
                ui(status.set, "Rescue did not run (the password or administrator was not given).")
            ui(end_rescue)

        def end_rescue():
            state["rescue"] = None
            rb.configure(text="Rescue…")
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
            heading("Step 1 of 3: listening to the FM-1")
            text("Connect the FM-1 with its USB cable and switch it on. For a few seconds the guide listens "
                 "for it in every mode it can be in.", pady=(8, 0))
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
            text("The computer cannot hear the FM-1, so the guide needs your eyes. "
                 "(It keeps listening meanwhile: if the FM-1 turns up, it goes on by itself.)", pady=(6, 8))
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
                do_install()

        if start == "rescue_failed":
            show(plan("none", rescue_failed=True))
        else:
            detect()

    def do_install():
        i = chosen(lst)
        if i is None or not state["pkgs"]:
            messagebox.showinfo("FM-1 Switch", "Choose a firmware file first.")
            return
        pkg = state["pkgs"][i]
        if backend is None:
            messagebox.showerror("FM-1 Switch", NO_MIDI.format(midi_err))
            return
        warn = flash_warning(pkg, last_hardware(rescue_dir()))
        if warn and not messagebox.askyesno("FM-1 Switch – flash compatibility", warn, icon="warning",
                                            default="no"):
            return
        if not messagebox.askyesno("FM-1 Switch", f"Install {pkg.path.name} ({pkg.product})?\n\n"
                                                  "Do not unplug the FM-1 while it writes."):
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

    ib = ttk.Button(row, text="Install", style="Install.TButton", width=12, command=do_install)
    rb = ttk.Button(row, text="Rescue…", style="Rescue.TButton", command=lambda: do_rescue())
    buttons = [ttk.Button(card_btns, text="Check / unbrick…", command=open_guide),
               ttk.Button(row, text="Add folder…", style="Utility.TButton", command=add_folder),
               ttk.Button(row, text="Refresh", style="Utility.TButton", command=refresh), ib, rb,
               ttk.Button(card_btns, text="BACKUP…", style="Backup.TButton", width=18, command=do_inspect)]
    buttons[0].configure(style="Check.TButton", width=18)
    buttons[0].pack()
    buttons[5].pack(pady=(px(8), 0))
    tk.Label(card_btns, text="Full flash · V15 / UBOOT", background=C["card"], foreground=C["muted"], font=small_f, padx=0, pady=0).pack(pady=(px(4), 0))
    buttons[1].pack(side="left", padx=(0, px(8)))
    buttons[2].pack(side="left")
    ib.pack(side="right")
    rb.pack(side="right", padx=(0, px(8)))
    ttk.Button(head, text="About", style="Utility.TButton", command=lambda: messagebox.showinfo(
        "About FM-1 Switch", f"FM-1 Switch {app_version()}\n\n" + DISCLAIMER
        + "\n\nSource and licence: github.com/Saintpid/FM1-Switch (GPL-3.0)")).pack(side="right")

    def closing():
        if state["busy"]:
            messagebox.showinfo("FM-1 Switch", "Please keep this window open until the operation finishes. "
                                "If a system permission dialog is open, you can approve or cancel it. "
                                "During recovery, Stop is available while waiting for the device. "
                                "Once writing starts, let it finish.", parent=root)
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
    status.set("Preparing MIDI and firmware folders. Firmware has not been changed.")
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
