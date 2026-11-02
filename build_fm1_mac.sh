#!/bin/bash
# FM-1 Switch's Mac build entry point: Noisebud Builder's custom flow "fm1-switch-source"
# (builder-flows.json) runs it with the ZIP root as working directory; Build_FM1Switch_Mac.command runs
# it after unpacking. Everything is found relative to this script, never $PWD.
#   build_fm1_mac.sh                  signed, notarized release to dist/ and NOISEBUD_SETUP_DEST
#   build_fm1_mac.sh --unsigned-dev   local ad hoc test build to dist-dev/, never published
#   build_fm1_mac.sh --check          checks only
# No completion sound here: Builder or the .command wrapper plays it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_DEST="${NOISEBUD_SETUP_DEST:-}"
export NOISEBUD_SETUP_DEST="$SETUP_DEST"
export MACOSX_DEPLOYMENT_TARGET=11.0

# Build tools run native for the hardware: a shell translated by Rosetta on Apple Silicon starts again
# as arm64 (Intel Macs stay x86_64). The product is universal either way.
if [[ "$(/usr/sbin/sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" == "1" ]]; then
    if [[ -n "${FM1_SWITCH_BUILD_NATIVE:-}" ]]; then
        echo "ERROR: still translated after restarting as arm64." >&2
        exit 1
    fi
    echo "Running under Rosetta: starting the build again as arm64."
    exec /usr/bin/env FM1_SWITCH_BUILD_NATIVE=1 /usr/bin/arch -arm64 /bin/bash "$HERE/build_fm1_mac.sh" "$@"
fi

PYTHON="${FM1_SWITCH_PYTHON:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3}"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: $PYTHON was not found. Install Python 3.12 from python.org (macOS 64-bit universal2)," >&2
    echo "or set FM1_SWITCH_PYTHON to such a Python." >&2
    exit 1
fi
unset PYTHONPATH
exec "$PYTHON" -E -s "$HERE/mac/build_release.py" --root "$HERE" "$@"
