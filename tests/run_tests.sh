#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# FM-1 Switch's tests, against a simulated FM-1 (no hardware, no Tk, no MIDI or USB packages needed):
# the installer (USB-MIDI), the window's core and its diagnosis, and the rescue through the chip's own
# update mode (over USB and over Windows' SCSI pass-through).
#   tests/run_tests.sh
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
FAIL=0
for t in install switch rescue; do
    if "$PY" "tests/${t}_test.py" > "/tmp/fm1-switch-$t.log" 2>&1 || { cat "/tmp/fm1-switch-$t.log"; false; }; then
        echo "  ok   $t"
    else
        echo "  FAIL $t"
        FAIL=1
    fi
done
[ $FAIL = 0 ] && echo "all tests passed" || echo "TESTS FAILED"
exit $FAIL
