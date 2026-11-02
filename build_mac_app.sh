#!/bin/bash
# FM-1 Switch for the Mac, the way Noisebud Builder builds apps (its app flow): Builder finds this
# script and src/noisebud-product.json in FM1Switch_mac_ready_vX.zip (tools/export_switch_mac.cmd puts
# it in the SEM folder Builder reads), runs it here, and checks for the DMG in dist/. Also run by
# Build_FM1Switch_Mac.command for a build without Builder.
#
# tools/build_switch_mac.sh does the build (FM1-Switch.app, universal when the Python is, and its
# DMG); this puts the DMG in dist/ and copies it to the setup folder, checked by its SHA-256.
#   NOISEBUD_SETUP_DEST                                            the setup folder (Builder sets it)
#   NOISEBUD_APPLE_SIGN_IDENTITY / NOISEBUD_APPLE_NOTARY_PROFILE   sign / notarize (unsigned without)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
SETUP_DEST="${NOISEBUD_SETUP_DEST:-/Users/macbook/Dropbox/SynthEdit/000 Klara pluggar 000/--- Nya Setupfiler}"
VERSION="$(tr -d ' \r\n' < VERSION)"
MANIFEST_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["releaseVersion"])' \
    src/noisebud-product.json)"
if [[ "$MANIFEST_VERSION" != "$VERSION" ]]; then
    echo "ERROR: src/noisebud-product.json says $MANIFEST_VERSION, VERSION says $VERSION." >&2
    exit 1
fi

echo "FM-1 Switch $VERSION"
bash ./tools/build_switch_mac.sh

DMG="build-mac-switch/FM1Switch_macOS_v$VERSION.dmg"
[[ -f "$DMG" ]] || { echo "ERROR: $DMG was not made." >&2; exit 1; }
rm -rf dist
mkdir -p dist
cp -f "$DMG" dist/

echo "Copying the DMG to the setup folder:"
echo "  $SETUP_DEST"
mkdir -p "$SETUP_DEST"
cp -f "$DMG" "$SETUP_DEST/"
src_hash="$(shasum -a 256 "$DMG" | awk '{print $1}')"
dst_hash="$(shasum -a 256 "$SETUP_DEST/$(basename "$DMG")" | awk '{print $1}')"
[[ "$src_hash" == "$dst_hash" ]] || { echo "ERROR: the copy in the setup folder differs." >&2; exit 1; }

echo ""
echo "Done: dist/$(basename "$DMG")"
