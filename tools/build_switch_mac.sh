#!/usr/bin/env bash
# FM-1 Switch for macOS: FM1-Switch.app (universal when the Python is) in a DMG, built on the Mac the
# way the Noisebud apps are: tools/export_switch_mac.cmd sends the sources over, Build_FM1Switch_Mac.command
# runs this. Needs Python 3 from python.org (its universal2 installer, with Tk) and Xcode's command line tools.
#   NOISEBUD_APPLE_SIGN_IDENTITY / NOISEBUD_APPLE_NOTARY_PROFILE   signs / notarizes (unsigned without:
#                                                                  the first time, right-click the app, Open)
#   FM1_SWITCH_DEST                                                 a folder to copy the DMG to
set -euo pipefail

APP_NAME="FM1-Switch"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d ' \r\n' < "$PROJECT_DIR/VERSION")"
BUILD_DIR="$PROJECT_DIR/build-mac-switch"
VENV="$BUILD_DIR/venv"
FINAL_APP="$BUILD_DIR/dist/$APP_NAME.app"
DMG_STAGE="$BUILD_DIR/dmg-stage"
DMG="$BUILD_DIR/FM1Switch_macOS_v$VERSION.dmg"
PYTHON="${PYTHON:-python3}"

for tool in "$PYTHON" lipo hdiutil plutil; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: $tool was not found. Python 3: python.org; the rest: xcode-select --install" >&2
        exit 1
    fi
done
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo "ERROR: $PYTHON has no Tk. Install Python 3 from python.org (its macOS installer)." >&2
    exit 1
fi

echo "Project: $PROJECT_DIR"
echo "Version: $VERSION"
echo "Cleaning previous Mac build..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "Installing the build packages (mido, python-rtmidi, pyusb, libusb-package, pyserial, sv-ttk, PyInstaller) in $VENV..."
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --upgrade mido python-rtmidi pyusb libusb-package pyserial sv-ttk pyinstaller

build_app() {
    "$VENV/bin/python" -m PyInstaller --noconfirm --windowed --name "$APP_NAME" \
        --osx-bundle-identifier se.noisebud.FM1Switch "$@" \
        --paths "$PROJECT_DIR/tools" --hidden-import mido.backends.rtmidi \
        --hidden-import usb.backend.libusb1 --collect-all libusb_package \
        --hidden-import serial.tools.list_ports --collect-data sv_ttk --add-data "$PROJECT_DIR/VERSION:." \
        --distpath "$BUILD_DIR/dist" --workpath "$BUILD_DIR/work" --specpath "$BUILD_DIR" \
        "$PROJECT_DIR/tools/fm1_switch.pyw"
}
echo "Building $APP_NAME.app (Apple Silicon and Intel)..."
if ! build_app --target-arch universal2; then
    echo "NOTE: no universal build (the Python or python-rtmidi is not universal2): building for $(uname -m) only."
    build_app
fi

PLIST="$FINAL_APP/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "FM-1 Switch" "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace LSMinimumSystemVersion -string "10.13" "$PLIST"

if [[ -n "${NOISEBUD_APPLE_SIGN_IDENTITY:-}" ]]; then
    echo "Signing $APP_NAME.app..."
    codesign --force --deep --options runtime --timestamp \
        --sign "$NOISEBUD_APPLE_SIGN_IDENTITY" "$FINAL_APP"
    codesign --verify --deep --strict --verbose=2 "$FINAL_APP"
else
    echo "NOTE: NOISEBUD_APPLE_SIGN_IDENTITY is not set; the app will be unsigned."
fi

echo "Creating DMG..."
rm -f "$DMG"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp -R "$FINAL_APP" "$DMG_STAGE/"
hdiutil create -volname "FM-1 Switch" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG"

if [[ -n "${NOISEBUD_APPLE_NOTARY_PROFILE:-}" ]]; then
    if [[ -z "${NOISEBUD_APPLE_SIGN_IDENTITY:-}" ]]; then
        echo "ERROR: Notarization requires NOISEBUD_APPLE_SIGN_IDENTITY." >&2
        exit 1
    fi
    echo "Submitting DMG for notarization..."
    xcrun notarytool submit "$DMG" --keychain-profile "$NOISEBUD_APPLE_NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
fi

echo "Checking architecture..."
ARCH_INFO="$(lipo -info "$FINAL_APP/Contents/MacOS/$APP_NAME")"
echo "$ARCH_INFO"
if [[ "$ARCH_INFO" != *"x86_64"* || "$ARCH_INFO" != *"arm64"* ]]; then
    echo "NOTE: not universal: this app runs only on Macs like this one."
fi

if [[ -n "${FM1_SWITCH_DEST:-}" ]]; then
    echo "Copying the DMG to: $FM1_SWITCH_DEST"
    mkdir -p "$FM1_SWITCH_DEST"
    cp -f "$DMG" "$FM1_SWITCH_DEST/"
fi

echo ""
echo "Done."
echo "App:  $FINAL_APP"
echo "DMG:  $DMG"
