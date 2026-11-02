#!/bin/bash
# Double-click on the Mac to build FM-1 Switch without Noisebud Builder. It looks only in its own
# folder, takes the FM1Switch_mac_ready_v*.zip with the highest version (never by date), checks it,
# unpacks it into a fresh folder under ~/Library/Caches/NoisebudBuildTools (never in the synced
# folder) and runs FM1Switch/build_fm1_mac.sh there. Arguments go through (--unsigned-dev, --check).
# Glass when it worked, Basso when it failed.
set -euo pipefail

finish() {
    local status=$?
    trap - EXIT
    echo
    if [[ $status -eq 0 ]]; then
        echo "Build finished successfully."
        sound=/System/Library/Sounds/Glass.aiff
    else
        echo "Build FAILED (exit code $status)."
        sound=/System/Library/Sounds/Basso.aiff
    fi
    if [[ -z "${FM1_SWITCH_SELECT_ONLY:-}" ]]; then
        if [[ -x /usr/bin/afplay ]]; then
            /usr/bin/afplay "$sound" 2>/dev/null || printf '\a'
        elif command -v afplay >/dev/null 2>&1; then
            afplay "$sound" 2>/dev/null || printf '\a'
        else
            printf '\a'
        fi
        echo "Press Return to close this window."
        read -r _ || true
    fi
    exit "$status"
}
trap finish EXIT

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# The highest version among the exports beside this file (numeric, field by field).
LATEST=""
LATEST_VERSION=""
while IFS= read -r line; do
    LATEST_VERSION="${line%% *}"
    LATEST="${line#* }"
done < <(
    for f in FM1Switch_mac_ready_v*.zip; do
        [[ -f "$f" ]] || continue
        v="${f#FM1Switch_mac_ready_v}"
        v="${v%.zip}"
        [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
        echo "$v $f"
    done | sort -t. -k1,1n -k2,2n -k3,3n
)
if [[ -z "$LATEST" ]]; then
    echo "ERROR: no FM1Switch_mac_ready_vX.Y.Z.zip beside this file ($HERE)." >&2
    echo "Export it from the PC first (scripts\\release.ps1). Nothing was built." >&2
    exit 1
fi
echo "Export: $LATEST (version $LATEST_VERSION)"
if [[ -n "${FM1_SWITCH_SELECT_ONLY:-}" ]]; then
    exit 0                                  # (tests: the choice only)
fi

ZIP="$HERE/$LATEST"
ZIP_SHA="$(shasum -a 256 "$ZIP" | awk '{print $1}')"
echo "SHA-256: $ZIP_SHA"
WORK="$HOME/Library/Caches/NoisebudBuildTools/fm1switch-src-$LATEST_VERSION-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$WORK"
cp "$ZIP" "$WORK/export.zip"
if [[ "$(shasum -a 256 "$WORK/export.zip" | awk '{print $1}')" != "$ZIP_SHA" ]]; then
    echo "ERROR: the export changed while it was copied (still syncing?). Try again." >&2
    exit 1
fi

# Unpack safely: one FM1Switch/ root, no absolute paths, no .., no backslashes, no links, its VERSION
# the file name's.
/usr/bin/python3 -E -s - "$WORK/export.zip" "$WORK" "$LATEST_VERSION" <<'PY'
import os, stat, sys, zipfile
from pathlib import Path, PurePosixPath
zpath, dest, want = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
with zipfile.ZipFile(zpath) as z:
    bad = z.testzip()
    if bad:
        sys.exit("damaged entry in the export: " + bad)
    for i in z.infolist():
        n = i.filename
        p = PurePosixPath(n)
        if "\\" in n or p.is_absolute() or ".." in p.parts or not n.startswith("FM1Switch/"):
            sys.exit("unsafe path in the export: " + n)
        if stat.S_ISLNK(i.external_attr >> 16):
            sys.exit("link in the export: " + n)
    if z.read("FM1Switch/VERSION").decode().strip() != want:
        sys.exit("the export's VERSION is not " + want)
    for i in z.infolist():
        z.extract(i, dest)
        mode = (i.external_attr >> 16) & 0o777
        if mode and not i.is_dir():
            os.chmod(dest / i.filename, mode)
print("Unpacked into " + str(dest / "FM1Switch"))
PY

bash "$WORK/FM1Switch/build_fm1_mac.sh" "$@"
