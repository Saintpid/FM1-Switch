#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""FM-1 Switch for the Mac: a universal FM-1 Switch.app, in a signed PKG installer, in a signed and
notarized DMG (docs/MAC_RELEASE.md). Run by build_fm1_mac.sh (Noisebud Builder's custom flow, or
Build_FM1Switch_Mac.command); it needs the Mac, python.org's universal2 Python 3.12 and Xcode's tools.

  build_fm1_mac.sh                 the release: Developer ID (shared Noisebud signing runtime in
                                   mac/signing), notarized, to dist/ and NOISEBUD_SETUP_DEST
  build_fm1_mac.sh --unsigned-dev  a local test build, ad hoc signed, to dist-dev/ only: never published
  build_fm1_mac.sh --check         the checks before a build (tools, Python, identities), nothing built

Order: hardware-native build tools -> simulated tests -> universal wheels (each native dependency's
arm64 and x86_64 wheels joined with delocate-merge) -> PyInstaller universal2 -> every Mach-O has both
slices and needs macOS 11.0 at most -> signed inside out -> signature tree, native process probes
(also under an x86_64 parent) -> component PKG -> product PKG, productsign, notarized -> DMG with the
Noisebud installer look, signed, notarized -> dist/ and the setup folder, SHA-256 checked.
No step is skipped to get a result: any failure stops the build. Never flashes hardware.
"""
import argparse
import hashlib
import html
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "signing"))
sys.path.insert(0, str(HERE / "noisebud-mac"))
import noisebud_signing as signing  # noqa: E402
from package_common import macho_arches, validate_macos_compatibility  # noqa: E402

APP_NAME = "FM-1 Switch"
EXE_NAME = "FM1-Switch"
BUNDLE_ID = "se.noisebud.FM1Switch"
PKG_ID = "se.noisebud.FM1Switch.app"
MINIMUM = "11.0"
PYTHON_VERSION = (3, 12)               # python-rtmidi 1.5.8 has wheels for 3.8-3.12
ARCHES = ("arm64", "x86_64")
# Exact versions; the native ones are joined from their arm64 and x86_64 wheels.
PURE = ["mido==1.3.3", "packaging==24.2", "pyusb==1.3.1", "pyserial==3.5", "sv-ttk==2.6.1"]
NATIVE = {"python-rtmidi": "1.5.8", "libusb-package": "1.0.30.0"}
NATIVE_PLATFORMS = {"arm64": "macosx_11_0_arm64", "x86_64": "macosx_10_9_x86_64"}
TOOLS = ["pyinstaller==6.22.3", "delocate==0.13.0"]
SETUP_FALLBACK = "/Users/macbook/Dropbox/SynthEdit/000 Klara pluggar 000/--- Nya Setupfiler"
ENTITLEMENTS = {"com.apple.security.cs.allow-unsigned-executable-memory": True}

INSTALLER_STYLE = """
html, body, h2, p, div {
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif;
}
body { font-size: 13px; line-height: 1.45; margin: 16px; }
h2 { font-size: 18px; font-weight: 700; margin: 0 0 16px; }
p { margin: 0 0 14px; }
.license-text { white-space: pre-wrap; overflow-wrap: break-word; }
""".strip()


class Fail(Exception):
    pass


def run(*args, **kw):
    print("+ " + shlex.join(str(a) for a in args), flush=True)
    try:
        return subprocess.run([str(a) for a in args], check=True, **kw)
    except subprocess.CalledProcessError as e:
        for out in (e.stdout, e.stderr):
            if out:
                print(out if isinstance(out, str) else out.decode("utf-8", "replace"), flush=True)
        raise


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def version_of(root):
    v = (Path(root) / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise Fail("VERSION is not X.Y.Z: " + v)
    return v


def dmg_name(version, dev=False):
    return f"FM1Switch_macOS_v{version}" + ("-UNSIGNED-DEV" if dev else "") + ".dmg"


# ------------------------------------------------------------ configuration ---

def packager_lines(path=None):
    """the Mac's existing ID_and_paths.txt (non-secret lines only): 7 setup folder, 14 notary profile"""
    p = Path(path or os.environ.get("NOISEBUD_PACKAGER_CONFIG", str(Path.home() / "Desktop/Certificat/ID_and_paths.txt")))
    if not p.is_file():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8-sig").splitlines() if ln.strip() and not ln.startswith("#")]


def setup_dest(env=os.environ, lines=()):
    """NOISEBUD_SETUP_DEST (Builder sets it), else the packager config's, else the Noisebud setup folder"""
    return env.get("NOISEBUD_SETUP_DEST") or (lines[7] if len(lines) > 7 else "") or SETUP_FALLBACK


def release_config(env=os.environ, lines=(), settings=None):
    """the signing selectors and the notary profile; a release stops when one is missing"""
    sel = settings() if settings else signing.settings()
    notary = env.get("NOISEBUD_APPLE_NOTARY_PROFILE") or (lines[14] if len(lines) > 14 else "")
    if not notary:
        raise Fail("No notary profile: set NOISEBUD_APPLE_NOTARY_PROFILE or line 15 of "
                   "~/Desktop/Certificat/ID_and_paths.txt. A release is never published unsigned "
                   "(--unsigned-dev makes a local test build).")
    return {"team": sel["team"], "application": sel["application"], "installer": sel["installer"], "notary": notary}


# ------------------------------------------------------------------ checks ---

def sysctl_int(name):
    r = subprocess.run(["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None


def check_native_host(sysctl=sysctl_int, machine=None):
    """build tools run native: never under Rosetta on Apple Silicon (build_fm1_mac.sh restarts native)"""
    import platform
    hw = "arm64" if sysctl("hw.optional.arm64") == 1 else "x86_64"
    me = machine or platform.machine()
    if sysctl("sysctl.proc_translated") == 1 or me != hw:
        raise Fail(f"The build runs as {me} on {hw} hardware (translated). Start it natively.")
    return hw


def check_python(exe=sys.executable, version=sys.version_info, platform_tag=None):
    """python.org's universal2 framework Python 3.12: both slices, so PyInstaller can make universal2"""
    import sysconfig
    tag = platform_tag or sysconfig.get_platform()
    if tuple(version[:2]) != PYTHON_VERSION:
        raise Fail(f"Needs Python {PYTHON_VERSION[0]}.{PYTHON_VERSION[1]} from python.org (universal2), "
                   f"not {version[0]}.{version[1]}: python-rtmidi has no wheels for it")
    if not tag.endswith("universal2"):
        raise Fail(f"{exe} is not universal2 ({tag}): install Python 3.12 from python.org "
                   "(its macOS 64-bit universal2 installer)")
    arches = macho_arches(Path(exe).resolve())
    if not set(ARCHES) <= arches:
        raise Fail(f"{exe} lacks a slice ({sorted(arches)}): a universal build is required")


def check_tools(names=("xcrun", "codesign", "pkgbuild", "productbuild", "productsign", "pkgutil", "hdiutil",
                       "spctl", "lipo", "ditto")):
    missing = [n for n in names if not shutil.which(n)]
    if missing:
        raise Fail("Missing Mac tools: " + ", ".join(missing) + " (xcode-select --install)")


# --------------------------------------------------------------- the build ---

def build_env(work):
    """a venv on this Python; the pure packages and the build tools; the native ones joined universal"""
    venv = work / "venv"
    run(sys.executable, "-m", "venv", venv)
    py = venv / "bin/python"
    pip = [py, "-m", "pip", "--disable-pip-version-check", "--no-input"]
    run(*pip, "install", "--quiet", *PURE, *TOOLS)
    wheels, merged = work / "wheels", work / "wheels-universal2"
    abi = f"cp{PYTHON_VERSION[0]}{PYTHON_VERSION[1]}"
    for name, ver in NATIVE.items():
        got = []
        for arch, plat in NATIVE_PLATFORMS.items():
            d = wheels / arch / name
            run(*pip, "download", "--quiet", "--no-deps", "--only-binary=:all:", "--platform", plat,
                "--python-version", f"{PYTHON_VERSION[0]}.{PYTHON_VERSION[1]}", "--implementation", "cp",
                "--abi", abi, "--abi", "none", "--abi", "abi3", f"{name}=={ver}", "-d", d)
            w = sorted(d.glob("*.whl"))
            if len(w) != 1:
                raise Fail(f"{name} {ver}: expected one {arch} wheel, found {len(w)}")
            got.append(w[0])
        out = merged / name
        run(venv / "bin/delocate-merge", *got, "-w", out)
        u = sorted(out.glob("*universal2*.whl"))
        if len(u) != 1:
            raise Fail(f"{name}: delocate-merge made no universal2 wheel")
        run(*pip, "install", "--quiet", "--no-deps", u[0])
    return py


def run_tests(root, py):
    for t in ("install", "switch", "rescue"):           # (the export test needs the git checkout)
        run(py, "-E", "-s", root / "tests" / f"{t}_test.py", cwd=root)


def pyinstaller(root, py, work):
    run(py, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", EXE_NAME, "--target-arch", "universal2",
        "--osx-bundle-identifier", BUNDLE_ID, "--icon", HERE / "assets/Noisebud.icns",
        "--paths", root / "tools", "--hidden-import", "mido.backends.rtmidi",
        "--hidden-import", "usb.backend.libusb1", "--collect-all", "libusb_package",
        "--hidden-import", "serial.tools.list_ports", "--collect-data", "sv_ttk",
        "--add-data", f"{root / 'VERSION'}:.", "--add-data", f"{root / 'LICENSE'}:.",
        "--add-data", f"{root / 'DISCLAIMER.md'}:.",
        "--distpath", work / "dist", "--workpath", work / "pyi", "--specpath", work,
        root / "tools/fm1_switch.pyw", cwd=work)
    app = work / "dist" / f"{EXE_NAME}.app"
    if not app.is_dir():
        raise Fail("PyInstaller made no app")
    return app


def app_plist(info, version):
    """FM-1 Switch's own keys on PyInstaller's Info.plist"""
    info.update({"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
                 "CFBundleShortVersionString": version, "CFBundleVersion": version,
                 "LSMinimumSystemVersion": MINIMUM, "LSRequiresNativeExecution": True,
                 "NSHighResolutionCapable": True,
                 "NSHumanReadableCopyright": "Free software (GPL-3.0). No warranty: see DISCLAIMER.md."})
    return info


def set_plist(app, version):
    p = app / "Contents/Info.plist"
    with p.open("rb") as f:
        info = plistlib.load(f)
    with p.open("wb") as f:
        plistlib.dump(app_plist(info, version), f)


def macho_files(app):
    return [p for p in sorted(app.rglob("*")) if p.is_file() and not p.is_symlink() and macho_arches(p)]


def check_slices(app, files=None):
    """every Mach-O: both slices, macOS 11.0 at most per slice (before any signing)"""
    files = macho_files(app) if files is None else files
    if not files:
        raise Fail("no Mach-O code in " + str(app))
    for p in files:
        a = macho_arches(p)
        if not set(ARCHES) <= a:
            raise Fail(f"missing a slice ({sorted(a)}): {p}")
        validate_macos_compatibility(p, (11, 0, 0))
    print(f"Native slices and macOS {MINIMUM}: {len(files)} Mach-O files verified", flush=True)
    return len(files)


def sign_order(app, files):
    """inside out: the deepest code first, the app's own executable and the app last"""
    main_exe = app / "Contents/MacOS" / EXE_NAME
    nested = [p for p in files if p != main_exe]
    nested.sort(key=lambda p: (-len(p.relative_to(app).parts), str(p)))
    bundles = sorted({q for p in nested for q in p.parents
                      if q.suffix in (".framework", ".app", ".bundle") and q != app and app in q.parents},
                     key=lambda p: -len(p.parts))
    return nested + bundles + [app]


def sign_app(app, identity, work):
    ent = work / "entitlements.plist"
    with ent.open("wb") as f:
        plistlib.dump(ENTITLEMENTS, f)
    for p in sign_order(app, macho_files(app)):
        args = ["codesign", "--force", "--timestamp", "--options", "runtime", "--sign", identity]
        if p == app:
            args += ["--entitlements", ent]
        run(*args, p)
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", app)


def native_gates(app, root):
    gate = HERE / "noisebud-mac/verify_native_mac.py"
    exe = app / "Contents/MacOS" / EXE_NAME
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    run(sys.executable, "-E", "-s", gate, "--root", app, "--minimum", MINIMUM, "--app", app,
        "--probe", exe, "--translated-parent", env=env, cwd=Path(tempfile.gettempdir()))


def installer_document(title, body):
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8" /><title>' + html.escape(title)
            + "</title><style>" + INSTALLER_STYLE + "</style></head><body>" + body + "</body></html>\n")


def write_resources(res, root, version):
    res.mkdir(parents=True, exist_ok=True)
    eula = (root / "installer/EULA.txt").read_text(encoding="utf-8-sig")
    shutil.copyfile(root / "installer/EULA.txt", res / "EULA.txt")
    (res / "EULA.html").write_text(installer_document(
        "FM-1 Switch License and Disclaimer", '<div class="license-text">' + html.escape(eula) + "</div>"), "utf-8")
    welcome = ("<h2>FM-1 Switch " + html.escape(version) + "</h2><p>FM-1 Switch installs and rescues firmware on "
               "the M-VAVE FM-1. It is free, open-source software (GPL-3.0): no registration, no activation.</p>"
               "<p><b>Everything it does is at your own risk.</b> Writing firmware can leave a device unusable for "
               "good, and neither the author nor Noisebud takes any responsibility for what happens to your "
               "hardware. Read the licence on the next page.</p><p>It installs FM-1 Switch in Applications.</p>")
    done = ("<h2>FM-1 Switch is installed</h2><p>Open it from Applications. It shows the disclaimer the first time "
            "it starts.</p><p>Source code: https://github.com/Saintpid/FM1-Switch</p>")
    (res / "Welcome.html").write_text(installer_document("Welcome", welcome), "utf-8")
    (res / "Conclusion.html").write_text(installer_document("Installed", done), "utf-8")


def distribution_xml(version):
    root = ET.Element("installer-gui-script", minSpecVersion="2")
    ET.SubElement(root, "title").text = f"{APP_NAME} {version}"
    for tag, f in (("license", "EULA.html"), ("welcome", "Welcome.html"), ("conclusion", "Conclusion.html")):
        ET.SubElement(root, tag, file=f, **{"mime-type": "text/html"})
    ET.SubElement(root, "options", customize="never", **{"require-scripts": "false",
                                                          "hostArchitectures": "x86_64,arm64"})
    ET.SubElement(root, "domains", enable_anywhere="false", enable_currentUserHome="false", enable_localSystem="true")
    ET.SubElement(ET.SubElement(root, "allowed-os-versions"), "os-version", min=MINIMUM)
    outline = ET.SubElement(root, "choices-outline")
    ET.SubElement(outline, "line", choice="app")
    choice = ET.SubElement(root, "choice", id="app", title=APP_NAME, visible="false")
    ET.SubElement(choice, "pkg-ref", id=PKG_ID)
    ET.SubElement(root, "pkg-ref", id=PKG_ID, version=version, onConclusion="none").text = "app.pkg"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def component_pkg(app, work, version):
    payload = work / "payload/Applications"
    payload.mkdir(parents=True)
    run("ditto", app, payload / f"{APP_NAME}.app")
    comp = work / "components.plist"
    run("pkgbuild", "--analyze", "--root", work / "payload", comp)
    with comp.open("rb") as f:
        entries = plistlib.load(f)
    for e in entries:
        e.update(BundleIsRelocatable=False, BundleHasStrictIdentifier=True, BundleIsVersionChecked=False,
                 BundleOverwriteAction="upgrade")
    with comp.open("wb") as f:
        plistlib.dump(entries, f)
    pkgs = work / "packages"
    pkgs.mkdir()
    run("pkgbuild", "--root", work / "payload", "--install-location", "/", "--identifier", PKG_ID,
        "--version", version, "--ownership", "recommended", "--component-plist", comp, pkgs / "app.pkg")
    return payload / f"{APP_NAME}.app", pkgs


def notarize(path, profile, log):
    r = run("xcrun", "notarytool", "submit", path, "--keychain-profile", profile, "--wait",
            "--output-format", "json", capture_output=True, text=True)
    log.write_text(r.stdout, encoding="utf-8")
    status = json.loads(r.stdout)
    if status.get("status") != "Accepted":
        if status.get("id"):
            run("xcrun", "notarytool", "log", status["id"], "--keychain-profile", profile, log.with_suffix(".details.json"))
        raise Fail("Notarization was not accepted: see " + str(log))
    run("xcrun", "stapler", "staple", path)
    run("xcrun", "stapler", "validate", path)


def make_dmg(stage, dmg, version):
    """the Noisebud installer look (as Follower's packager): background, volume icon, the PKG in place"""
    if not shutil.which("create-dmg"):
        if not shutil.which("brew"):
            raise Fail("create-dmg is missing and there is no Homebrew to install it")
        run("brew", "install", "create-dmg")
    pkg = next(stage.glob("*.pkg")).name
    run("create-dmg", "--volname", f"FM-1 Switch {version}", "--window-pos", "200", "120",
        "--window-size", "500", "500", "--icon-size", "100", "--icon", pkg, "250", "340",
        "--background", HERE / "assets/MacInstaller.png", "--volicon", HERE / "assets/Noisebud.icns", dmg, stage)


def publish(src, dest):
    """copy beside, check the hash, rename into place, check again"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fm1switch-", dir=dest.parent)
    os.close(fd)
    tmp = Path(tmp)
    try:
        shutil.copyfile(src, tmp)
        if sha256(tmp) != sha256(src):
            raise Fail("copy hash mismatch: " + str(dest))
        tmp.replace(dest)
        if sha256(dest) != sha256(src):
            raise Fail("published hash mismatch: " + str(dest))
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"  {dest}  SHA-256 {sha256(dest)}", flush=True)


def build(root, dev=False, check_only=False):
    root = Path(root).resolve()
    version = version_of(root)
    print(f"{APP_NAME} {version} ({'UNSIGNED DEVELOPMENT build' if dev else 'release'})", flush=True)
    if sys.platform != "darwin":
        raise Fail("The Mac build runs on a Mac")
    hw = check_native_host()
    check_python()
    check_tools()
    lines = packager_lines()
    config = None
    if not dev:
        config = release_config(lines=lines)
        signing.validate_identity(config, "application")
        signing.validate_identity(config, "installer")
    if check_only:
        print(f"Checks passed on {hw}: tools, Python, " + ("no signing (dev)" if dev else "signing identities"))
        return None
    out = root / ("dist-dev" if dev else "dist")
    shutil.rmtree(out, ignore_errors=True)
    cache = Path.home() / "Library/Caches/NoisebudBuildTools"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"fm1switch-{version}-", dir=cache))
    print("Work folder and logs: " + str(work), flush=True)
    os.environ["MACOSX_DEPLOYMENT_TARGET"] = MINIMUM

    py = build_env(work)
    run_tests(root, py)
    app = pyinstaller(root, py, work)
    set_plist(app, version)
    check_slices(app)
    identity = "-" if dev else config["application"]
    sign_app(app, identity, work)
    if not dev:
        signing.verify_code(app, config, tree=True)
    native_gates(app, root)

    installed, pkgs = component_pkg(app, work, version)
    check_slices(installed)
    if not dev:
        signing.verify_code(installed, config, tree=True)
    res = work / "resources"
    write_resources(res, root, version)
    dist_xml = work / "distribution.xml"
    dist_xml.write_bytes(distribution_xml(version))
    stage = work / "dmg-stage"
    stage.mkdir()
    pkg = stage / f"Install FM-1 Switch {version}.pkg"
    unsigned = work / "unsigned.pkg"
    run("productbuild", "--distribution", dist_xml, "--resources", res, "--package-path", pkgs, unsigned)
    if dev:
        shutil.copyfile(unsigned, pkg)
    else:
        run("productsign", "--sign", config["installer"], "--timestamp", unsigned, pkg)
        run("pkgutil", "--check-signature", pkg)
        signing.verify_pkg(pkg, config)
        notarize(pkg, config["notary"], work / "pkg-notary.json")
        run("spctl", "--assess", "--type", "install", "--verbose=2", pkg)
    shutil.copyfile(root / "installer/EULA.txt", stage / "License and Disclaimer.txt")
    dmg = work / dmg_name(version, dev)
    make_dmg(stage, dmg, version)
    if not dev:
        run("codesign", "--timestamp", "--sign", config["application"], dmg)
        signing.verify_code(dmg, config)
        notarize(dmg, config["notary"], work / "dmg-notary.json")
        run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "--verbose=2", dmg)
    print("Publishing:", flush=True)
    publish(dmg, out / dmg.name)
    if dev:
        print("UNSIGNED DEVELOPMENT build: not copied to the setup folder, not for anyone else.", flush=True)
    else:
        publish(dmg, Path(setup_dest(lines=lines)).expanduser() / dmg.name)
    print(f"\nDONE: {out / dmg.name}\nSHA-256: {sha256(out / dmg.name)}", flush=True)
    print("Still to check by hand: install it, open it from Finder (arm64 on Apple Silicon, x86_64 on Intel), "
          "and on the oldest supported macOS.", flush=True)
    return out / dmg.name


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=HERE.parent)
    ap.add_argument("--unsigned-dev", action="store_true", help="local ad hoc test build to dist-dev/, never published")
    ap.add_argument("--check", action="store_true", help="the checks only; builds nothing")
    ap.add_argument("--check-imports", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.check_imports:                 # (tests: the exported helpers import from a fresh interpreter)
        print("imports ok: " + signing.VERSION)
        return 0
    started = time.time()
    try:
        build(a.root, a.unsigned_dev, a.check)
    except (Fail, ValueError, OSError, KeyError, subprocess.CalledProcessError) as e:
        print(f"\nMac build FAILED: {e}", file=sys.stderr, flush=True)
        return 1
    print(f"({time.time() - started:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
