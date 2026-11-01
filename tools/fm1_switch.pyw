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
    def __init__(self, path, product):
        self.path, self.product = Path(path), product
        self.number = int(product.split("_")[1])

    @property
    def family(self):
        n = self.path.name.lower()
        return "NB303" if n.startswith("nb303") else "Wild" if n.startswith("wild") else \
            "n001se" if n.startswith("n001se") else "?"

    def label(self):
        return f"{self.path.stem}   ({self.product})"


def scan(folders):
    """every FM-1 package in the folders, the highest number first"""
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
            try:
                raw = f.read_bytes()
                product = I.product_of(raw)
            except (OSError, I.InstallError):
                continue
            if re.fullmatch(r"[^_]+_\d+", product) and I.model_of(product) == "FM-1" and I.LOADER_MARK in raw:
                out.append(Package(f, product))
    out.sort(key=lambda p: (-p.number, p.path.name))
    return out


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
    product, image = I.load_package(str(pkg.path), False)
    up = I.Updater(backend)
    dev = up.find()
    if not dev:
        raise I.not_found(up)
    return up.install(dev, image, product, step)


STEP_TEXT = {"start": "Starting the update on {}", "check": "Checking the package ({} reads)",
             "loader": "Switching to update mode", "write": "Writing {}%", "reboot": "Restarting",
             "done": "Done: the FM-1 runs {}"}


def step_text(kind, value=None):
    return STEP_TEXT[kind].format(value if value is not None else "")


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


def helper_command(args):
    """this program with --rescue: the .exe / the .app itself, or Python with this script"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--rescue", *args]
    return [sys.executable, str(Path(__file__).resolve()), "--rescue", *args]


def rescue_args(stock, d, progress):
    a = [str(stock), "--write", "--backup-dir", str(d), "--loader", str(d / R.LOADER_NAME),
         "--wait", "300", "--tries", "5", "--progress", str(progress)]
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


RESCUE_STEP = {"backup": "Backup saved: {}", "write": "Writing V15 {}%",
               "done": "Done: the FM-1 runs M-VAVE's V15. Install Wild or NB303 as usual."}


# ------------------------------------------------------------------- window ---

def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:
        backend = I.MidoBackend()
    except ImportError:
        backend = None

    root = tk.Tk()
    root.title("FM-1 Switch")
    mac = sys.platform == "darwin"
    ui_font, mono = ("Helvetica", 13) if mac else ("Segoe UI", 11), ("Menlo", 12) if mac else ("Consolas", 10)
    root.geometry("640x460")
    folders = default_folders()
    state = {"pkgs": [], "busy": False, "rescue": None}

    top = ttk.Frame(root, padding=10)
    top.pack(fill="both", expand=True)
    dev_var = tk.StringVar(value="FM-1: not read yet")
    ttk.Label(top, textvariable=dev_var, font=ui_font + ("bold",)).pack(anchor="w")
    ttk.Label(top, text="Firmware files (highest number first):").pack(anchor="w", pady=(10, 2))
    lst = tk.Listbox(top, height=12, font=mono, activestyle="none")
    lst.pack(fill="both", expand=True)
    bar = ttk.Progressbar(top, maximum=100)
    bar.pack(fill="x", pady=(10, 2))
    status = tk.StringVar(value="Connect the FM-1 with a USB data cable, close other MIDI apps.")
    ttk.Label(top, textvariable=status, wraplength=610, justify="left").pack(anchor="w")
    row = ttk.Frame(top)
    row.pack(fill="x", pady=(10, 0))

    def ui(f, *a):
        root.after(0, f, *a)

    def refresh():
        state["pkgs"] = scan(folders)
        lst.delete(0, "end")
        for p in state["pkgs"]:
            lst.insert("end", f"{p.family:<7} {p.label()}")
        if not state["pkgs"]:
            lst.insert("end", "(no .fwsc files: Add folder…)")

    def busy(on):
        state["busy"] = on
        for b in buttons:
            b.state(["disabled"] if on else ["!disabled"])

    def check_device(secs=8.0):
        busy(True)
        dev_var.set("FM-1: looking…")
        if secs:
            status.set("Looking for the FM-1 on USB, in every mode it can be in (a few seconds)…")

        def work():
            probe_ok = R.uboot_present() is not None
            try:
                state, ident = diagnose(backend, R.uboot_present if probe_ok else None, secs)
            except Exception as e:      # (a port that will not open: another app has it)
                ui(dev_var.set, "FM-1: could not be read")
                ui(status.set, f"Error: {e} (another app using the FM-1's MIDI port?)")
            else:
                head, todo = advice(state, ident, probe_ok)
                ui(dev_var.set, head)
                ui(status.set, todo)
            ui(busy, False)
        threading.Thread(target=work, daemon=True).start()

    def do_rescue():
        if state["rescue"]:                                     # the button is Stop while it waits
            open(state["rescue"] + ".stop", "w").close()
            status.set("Stopping (only while it waits: a write in progress is finished)…")
            return
        d = rescue_dir()
        stock = R.find_stock([str(f) for f in folders] + [str(d)])
        if not messagebox.askyesno(
                "FM-1 Switch – Rescue",
                "Rescue puts M-VAVE's stock firmware (FM-1 V15) back on an FM-1 whose firmware no "
                "longer starts, through the chip's own update mode.\n\n"
                "• The whole flash is saved first, as a backup in:\n   " + str(d) + "\n"
                "• The chip's boot code is never written, and nothing is written unless the chip and "
                "its flash are the FM-1's.\n"
                + ("" if stock else "• V15 is downloaded from M-VAVE's server and checked.\n")
                + "• It needs " + ("an administrator" if sys.platform == "win32" else "your password")
                + " (to take the FM-1 from the system).\n\n"
                "Then switch the FM-1 off and on. When it crashes and goes blank, it is caught "
                "(it waits up to 5 minutes).\n\nStart the rescue?"):
            return
        busy(True)
        rb.state(["!disabled"])
        bar["value"] = 0
        fd, prog = tempfile.mkstemp(prefix="fm1-rescue-", suffix=".jsonl")
        os.close(fd)

        def work():
            try:
                path = stock or R.download_stock(str(d), lambda t: ui(status.set, t.strip()))
                ui(status.set, "Getting JieLi's flash loader…")
                R.get_loader(None, str(d), lambda t: ui(status.set, t.strip()))
                wait = run_elevated(helper_command(rescue_args(path, d, prog)))
            except Exception as e:
                ui(status.set, f"Rescue not started: {e}")
                ui(end_rescue)
                return
            state["rescue"] = prog
            ui(rb.configure, text="Stop")
            ui(status.set, "Waiting for the FM-1 in the chip's update mode: switch it off and on now.")
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
                ui(dev_var.set, "FM-1: runs M-VAVE's V15")
                ui(status.set, RESCUE_STEP["done"])
                ui(messagebox.showinfo, "FM-1 Switch", RESCUE_STEP["done"])
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

    def do_install():
        sel = lst.curselection()
        if not sel or not state["pkgs"]:
            messagebox.showinfo("FM-1 Switch", "Choose a firmware file first.")
            return
        pkg = state["pkgs"][sel[0]]
        if backend is None:
            messagebox.showerror("FM-1 Switch", "Needs mido and python-rtmidi:\npip install mido python-rtmidi")
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

    buttons = [ttk.Button(row, text="Check FM-1", command=check_device),
               ttk.Button(row, text="Install", command=do_install),
               ttk.Button(row, text="Add folder…", command=add_folder),
               ttk.Button(row, text="Refresh", command=refresh)]
    for b in buttons:
        b.pack(side="left", padx=(0, 6))
    rb = ttk.Button(row, text="Rescue…", command=do_rescue)
    rb.pack(side="right")
    buttons.append(rb)

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
    check_device(0)
    root.mainloop()


def rescue_main(argv):
    """--rescue: fm1_rescue in this program, as administrator / root (a windowed .exe has no console)"""
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
    return R.main(argv)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rescue":
        sys.exit(rescue_main(sys.argv[2:]))
    main()
