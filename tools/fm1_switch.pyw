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
or nothing on USB) and what to do. Rescue… puts M-VAVE's stock firmware back on an FM-1 whose
firmware no longer starts, through the chip's own update mode (fm1_rescue.py, run as administrator
or root: the program starts itself again with --rescue for that).

  python tools/fm1_switch.pyw            (needs mido and python-rtmidi; Tk comes with Python)
  tools/build_switch_exe.bat             a FM1-Switch.exe for Windows (PyInstaller)
  tools/export_switch_mac.cmd            to the Mac, where Build_FM1Switch_Mac.command makes FM1-Switch.app
                                         and its DMG (tools/build_switch_mac.sh)
"""
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
    """an FM-1 package; kind "felucca" installs over USB-MIDI, "v15" / "other" only by Rescue"""

    def __init__(self, path, product, kind="felucca"):
        self.path, self.product, self.kind = Path(path), product, kind
        m = re.fullmatch(r"[^_]+_(\d+)", product)
        self.number = int(m[1]) if m else 0

    @property
    def family(self):
        if self.kind == "v15":
            return "M-VAVE"
        n = self.path.name.lower()
        return "NB303" if n.startswith("nb303") else "Wild" if n.startswith("wild") else \
            "n001se" if n.startswith("n001se") else "?"

    def label(self):
        return f"{self.path.stem}   ({'V15' if self.kind == 'v15' else self.product})" + \
            ("   rescue only" if self.kind != "felucca" else "")


def package_at(f):
    """the Package for a file, or None if it is not an FM-1 package"""
    try:
        raw = Path(f).read_bytes()
        product = I.product_of(raw)
    except (OSError, I.InstallError):
        return None
    if R.sha256(raw) == R.STOCK_SHA256:
        return Package(f, product, "v15")
    if re.fullmatch(r"[^_]+_\d+", product) and I.model_of(product) == "FM-1":
        return Package(f, product, "felucca" if I.LOADER_MARK in raw else "other")
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
            if p and (rescue_only or p.kind == "felucca"):
                out.append(p)
    out.sort(key=lambda p: (p.kind == "v15", -p.number, p.path.name))
    return out


def rescue_choices(folders):
    """what Rescue can put back: V15 first (None: download it from M-VAVE), then the rest"""
    pk = scan(folders)
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


def install(backend, pkg, step):
    """the whole update; step(kind, value) as fm1_install's Progress gets it. -> the identity after"""
    if pkg.kind != "felucca":
        raise I.InstallError("badpkg", f"{pkg.path.name} has no Felucca update loader, so it cannot be "
                                       "installed over USB-MIDI here: use M-UPGRADE, or Check / unbrick… "
                                       "and Rescue, which puts any FM-1 firmware back")
    product, image = I.load_package(str(pkg.path), False)
    up = I.Updater(backend)
    dev = up.find()
    if not dev:
        raise I.not_found(up)
    return up.install(dev, image, product, step)


STEP_TEXT = {"start": "Starting the update on {}", "check": "Checking the package ({} reads)",
             "loader": "Switching to update mode", "write": "Writing {}%", "reboot": "Restarting",
             "done": "Done: the FM-1 runs {}"}


def later(after, f, *a, **kw):
    """f(*a, **kw) on Tk's thread (after() itself passes no keywords: bar.configure(value=...))"""
    after(0, lambda: f(*a, **kw))


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


def row_values(p):
    """the firmware list's columns for a Package: family, file, identity, note"""
    return (p.family, p.path.stem, "V15" if p.kind == "v15" else p.product,
            "" if p.kind == "felucca" else "rescue only")


STATE_TONE = {"running": "ok", "loader": "warn", "uboot": "warn", "none": "off"}


# ------------------------------------------------------------- diagnosis ---

def diagnose(backend, probe=R.uboot_present, secs=8.0, poll=0.5):
    """-> (state, identity): "running" / "loader" (an install cut off) with the identity, "uboot"
    (the chip's own update mode), or "none". Watches for a few seconds: a crash-looping FM-1 is in
    UBOOT only now and then."""
    end = time.monotonic() + secs
    while True:
        if backend is not None:
            dev = I.Updater(backend).find()
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
                "then install Wild or NB303 as usual.")
    return ("FM-1: not found",
            "1. A USB data cable straight into the computer (no hub), the FM-1 switched on, no other "
            "MIDI app open. 2. If it shows a picture but does not answer: hold OCT- and OCT+ for 5 "
            "seconds, then Check FM-1 again. 3. If the screen stays dark or it restarts over and over: "
            "press Rescue… and switch it off and on; a firmware that keeps crashing ends up in the "
            "chip's own update mode, where it is caught."
            + ("" if probe_ok else " (This computer cannot see that mode: pyusb is missing.)")
            + " 4. If nothing at all shows on USB: it needs an FM-1-transporter (RP2040) adapter.")


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
                        "(V15) back, and saves the whole flash first."])
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


def rescue_args(target, d, progress, any_firmware=False, via="usb"):
    a = [str(target), "--write", "--backup-dir", str(d), "--loader", str(d / R.LOADER_NAME),
         "--wait", "300", "--tries", "5", "--progress", str(progress)]
    if any_firmware:
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


RESCUE_STEP = {"backup": "Backup saved: {}", "write": "Writing {}%",
               "done": "Done: the firmware is back on the FM-1, and it restarts."}


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

    backend, midi_err = open_midi()

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
    st.configure("Treeview", rowheight=px(30), font=body_f)
    st.configure("Treeview.Heading", font=small_f)
    root.geometry(f"{px(760)}x{px(560)}")
    root.minsize(px(620), px(460))
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
    card_btns = ttk.Frame(card)
    card_btns.grid(row=0, column=2, rowspan=2, sticky="ne", padx=(px(12), 0))
    card.bind("<Configure>", lambda e: msg.configure(wraplength=max(px(200), e.width - px(220))))

    def set_tone(t):
        tone["value"] = t
        dot.itemconfigure(dot_id, fill=C.get(t, C["off"]))

    row = ttk.Frame(top)                        # (packed from the bottom: the list takes what is left)
    row.pack(side="bottom", fill="x", pady=(px(12), 0))
    bar = ttk.Progressbar(top, maximum=100)
    bar.pack(side="bottom", fill="x", pady=(px(12), 0))
    ttk.Label(top, text="FIRMWARE FILES", style="Section.TLabel").pack(anchor="w", pady=(0, px(6)))
    lf = ttk.Frame(top)
    lf.pack(fill="both", expand=True)
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

    def refresh():
        state["pkgs"] = scan(folders)
        lst.delete(*lst.get_children())
        for p in state["pkgs"]:
            lst.insert("", "end", values=row_values(p), tags=(() if p.kind == "felucca" else ("other",)))
        if not state["pkgs"]:
            lst.insert("", "end", values=("", "No .fwsc files here yet: Add folder…", "", ""), tags=("other",))
        elif state["pkgs"][0].kind == "felucca":
            choose(lst, 0)

    def busy(on):
        state["busy"] = on
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
                state, ident = diagnose(backend, R.uboot_present if probe_ok else None, secs)
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
                   680, 600)
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
            run_rescue(c, d, via)
        go = ttk.Button(row2, text="Start the rescue", style="Accent.TButton", command=start)
        go.state(["disabled"])
        go.pack(side="right")
        ttk.Button(row2, text="Cancel", command=w.destroy).pack(side="right", padx=(0, px(8)))

    def run_rescue(target, d, via):
        busy(True)
        rb.state(["!disabled"])
        bar["value"] = 0
        fd, prog = tempfile.mkstemp(prefix="fm1-rescue-", suffix=".jsonl")
        os.close(fd)
        name = "M-VAVE's V15" if target is None or target.kind == "v15" else target.path.name

        def work():
            try:
                path = target.path if target else R.download_stock(str(d), lambda t: ui(status.set, t.strip()))
                ui(status.set, "Getting JieLi's flash loader…")
                R.get_loader(None, str(d), lambda t: ui(status.set, t.strip()))
                cmd = helper_command(rescue_args(path, d, prog, target is not None and target.kind != "v15", via))
                wait = subprocess.Popen(cmd).wait if via != "usb" else run_elevated(cmd)
            except Exception as e:
                ui(status.set, f"Rescue not started: {e}")
                ui(end_rescue)
                return
            state["rescue"] = prog
            ui(rb.configure, text="Stop")
            ui(status.set, f"Putting back {name}. Waiting for the FM-1 in the chip's update mode: "
                           + ("follow the adapter's steps." if via != "usb" else "switch it off and on now."))
            done, pos, end = {"code": None}, 0, None
            t = threading.Thread(target=lambda: done.update(code=wait()), daemon=True)
            t.start()
            while True:
                ev, pos = R.read_progress(prog, pos)
                for e in ev:
                    if "say" in e:
                        ui(status.set, e["say"])
                    elif e.get("step") in RESCUE_STEP:
                        ui(status.set, RESCUE_STEP[e["step"]].format(e.get("value") or ""))
                        if e["step"] == "write":
                            ui(bar.configure, value=e["value"])
                        elif e["step"] == "done":
                            ui(bar.configure, value=100)
                    elif "end" in e:
                        end = e
                if not t.is_alive():
                    ev, pos = R.read_progress(prog, pos)
                    end = next((e for e in ev if "end" in e), end)
                    break
                time.sleep(0.2)
            if end and end["end"] == "ok":
                msg = f"Done: {name} is back on the FM-1, and it restarts."
                ui(dev_var.set, f"FM-1: {name}")
                ui(status.set, msg)
                ui(messagebox.showinfo, "FM-1 Switch", msg)
            elif end and "did not appear" in (end.get("error") or "") and via == "usb":
                ui(status.set, "Rescue: the FM-1 did not show up in the chip's update mode.")
                ui(open_guide, "rescue_failed")
            elif end:
                ui(status.set, f"Rescue: {end.get('error')}")
                ui(messagebox.showerror, "FM-1 Switch – Rescue", end.get("error") or "failed")
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
                    st, ident = diagnose(backend, R.uboot_present if probe_ok else None, 8)
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
                        st, ident = diagnose(backend, R.uboot_present if probe_ok else None, 0)
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
                install(backend, pkg, step)
            except I.InstallError as e:
                ui(status.set, f"Error: {e}")
                ui(messagebox.showerror, "FM-1 Switch", str(e))
            except Exception as e:
                ui(status.set, f"Error: {e}")
            ui(busy, False)
        threading.Thread(target=work, daemon=True).start()

    def add_folder():
        d = filedialog.askdirectory(title="A folder with .fwsc files")
        if d and Path(d) not in folders:
            folders.append(Path(d))
            refresh()

    ib = ttk.Button(row, text="Install", style="Accent.TButton", width=12, command=do_install)
    rb = ttk.Button(row, text="Rescue…", command=lambda: do_rescue())
    buttons = [ttk.Button(card_btns, text="Check / unbrick…", command=open_guide),
               ttk.Button(row, text="Add folder…", command=add_folder),
               ttk.Button(row, text="Refresh", command=refresh), ib, rb]
    buttons[0].pack()
    buttons[1].pack(side="left", padx=(0, px(8)))
    buttons[2].pack(side="left")
    ib.pack(side="right")
    rb.pack(side="right", padx=(0, px(8)))
    ttk.Button(head, text="About", command=lambda: messagebox.showinfo(
        "About FM-1 Switch", f"FM-1 Switch {app_version()}\n\n" + DISCLAIMER
        + "\n\nSource and licence: github.com/Saintpid/FM1-Switch (GPL-3.0)")).pack(side="right")

    def closing():
        if state["rescue"] and not messagebox.askyesno(
                "FM-1 Switch", "A rescue is running as administrator. Closing the window does not stop it "
                               "(Stop does, while it waits). Close anyway?"):
            return
        if state["busy"] and not state["rescue"] and not messagebox.askyesno(
                "FM-1 Switch", "An update is running. Closing now leaves the FM-1 in update mode "
                               "(install again to finish). Close anyway?"):
            return
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", closing)
    refresh()
    d0 = rescue_dir()
    if not accepted(d0):
        if not messagebox.askokcancel("FM-1 Switch – read this first", DISCLAIMER + "\n\nOK: I accept this. "
                                                                      "Cancel: quit."):
            root.destroy()
            return
        accept(d0)
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
