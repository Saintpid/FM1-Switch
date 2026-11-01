# FM-1 Switch: working on it

- The app is `tools/fm1_switch.pyw` (Tk window; its core has no Tk so the tests can drive it) over
  `tools/fm1_install.py` (Felucca's USB-MIDI update) and `tools/fm1_rescue.py` (the chip's own update
  mode, UBOOT: pyusb on Mac/Linux, SCSI pass-through on Windows). Wild and NB303 keep their own
  copies of `fm1_install.py`; a fix to the update protocol belongs in both.
- Never write below 0x4000 or from 0x93000 in the rescue, and keep its checks (chip key 980F, flash
  856014, the app area decrypting with the chip key, V15's head exactly for V15, a full backup first,
  the explicit risk tick in the window). Any other firmware keeps the FM-1's own head.
- The FM-1-transporter path (`--via transporter`) is experimental until tried on hardware: say so
  wherever it shows.
- Tests: `tests/run_tests.sh` (simulated FM-1). Add a check for anything new and make sure it can fail.
- Texts in English. Branch from `main`, PR, CI green. Bump `VERSION` and add its `## X.Y.Z` section to
  `CHANGELOG.md` for a change a user would notice.
