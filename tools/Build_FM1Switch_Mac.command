#!/bin/bash
# Double-click on the Mac: unpacks the newest FM1Switch_mac_ready_v*.zip (from tools/export_switch_mac.cmd)
# in the synced SEM folder and builds the app; the DMG is copied back to that folder.
set -euo pipefail

finish() {
    local status=$?
    echo
    if [[ $status -eq 0 ]]; then
        echo "Build finished successfully."
    else
        echo "Build failed with exit code $status."
    fi
    echo "Press Return to close this window."
    read -r _
    exit "$status"
}
trap finish EXIT

SEM_ROOT="/Users/macbook/Resilio Sync/SYNK/SEM"
PROJECT_NAME="FM1 Switch"

cd "$SEM_ROOT"

LATEST_ZIP="$(ls -t FM1Switch_mac_ready_v*.zip 2>/dev/null | head -n 1 || true)"
if [[ -n "$LATEST_ZIP" ]]; then
    echo "Using latest zip: $LATEST_ZIP"
    rm -rf "$PROJECT_NAME"
    unzip -oq "$LATEST_ZIP"
else
    echo "No FM1Switch_mac_ready_v*.zip found."
    echo "Using existing project folder: $SEM_ROOT/$PROJECT_NAME"
fi

PROJECT_DIR="$SEM_ROOT/$PROJECT_NAME"
if [[ ! -f "$PROJECT_DIR/tools/build_switch_mac.sh" ]]; then
    echo "ERROR: Missing build script:"
    echo "  $PROJECT_DIR/tools/build_switch_mac.sh"
    exit 1
fi

cd "$PROJECT_DIR"
chmod +x ./tools/build_switch_mac.sh
FM1_SWITCH_DEST="$SEM_ROOT" ./tools/build_switch_mac.sh
