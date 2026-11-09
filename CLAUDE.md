# FM-1 Switch: working on it

- Read `AGENTS.md`, `PROJECT_MEMORY.md` and `docs/MAC_RELEASE.md` first. The Mac
  release specification there is normative. 1.2.0 implements it; the signed Mac build, its
  installation and native probes on real Macs are still to be accepted (PROJECT_MEMORY.md).
- FM-1 Switch is free/open source. It needs no Noisebud registration/license
  manifest. Implement a Builder custom flow with a renamed exported entrypoint,
  portable verified ZIP, mandatory shared release signing and a signed PKG in
  a signed/notarized DMG. Follow `docs/MAC_RELEASE.md`; do not bypass its gates.

- The app is `tools/fm1_switch.pyw` (Tk window; its core has no Tk so the tests can drive it) over
  `tools/fm1_install.py` (Felucca's USB-MIDI update) and `tools/fm1_rescue.py` (the chip's own update
  mode, UBOOT: pyusb on Mac/Linux, SCSI pass-through on Windows). Wild and NB303 keep their own
  copies of `fm1_install.py`; a fix to the update protocol belongs in both.
- Never write below 0x4000 or from 0x93000 in the rescue, and keep its checks (chip key 980F, flash
  type 3 and exact flash ID 856014 or B36014, the app area decrypting with the chip key,
  V15's head exactly for V15, a full verified backup first,
  the explicit risk tick in the window). Any other firmware keeps the FM-1's own head.
- B36014 (UCUN) writes are experimental and need explicit opt-in, over direct USB only. Unknown
  chips and keyboard/calibration matches are refused. Hardware / backup is an explicit RAM-loader
  session, never a hidden MIDI probe; it must not erase/program flash. Do not weaken these gates.
- Rescue refuses firmware whose exact-ID gate (`flash_support`: the flash ID constants in its
  decrypted app) excludes the measured flash, before reading or writing. The USB-MIDI install can
  only warn: the running firmware does not report its flash. Keep both.
- The FM-1-transporter path (`--via transporter`) is experimental until tried on hardware: say so
  wherever it shows.
- Tests: `tests/run_tests.sh` (simulated FM-1). Add a check for anything new and make sure it can fail.
- Texts in English. Branch from `main`, PR, CI green. Bump `VERSION` and add its
  `## X.Y.Z` section to `CHANGELOG.md` for a change a user would notice. There is
  no product/licence manifest (removed in 1.2.0): `tests/export_test.py` refuses one.
- Releases: `scripts\release.ps1` on Windows (installer to the setup folder, Mac export checked by
  Builder's `check_export.py`, then SEM); on the Mac, Builder's `fm1-switch-source` flow or
  `Build_FM1Switch_Mac.command`. The shared runtime in `mac/` is byte-identical to upstream
  (`mac/SOURCE_HASHES.json`): import, never patch.
