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
- The rescue never writes below 0x4000 (the boot area) and never from 0x93000, except M-VAVE's
  own 016, which it writes to its end at 0xA9000 and never past. It refuses a unit whose own boot
  area cannot start it (`fm1_flash.check_boot_area`) and never writes the head: every firmware keeps
  the FM-1's own. Keep its checks (chip key 980F, flash type 3 and exact flash ID 856014 or B36014,
  the app area decrypting with the chip key, a full backup checked against the chip's own CRC32
  first, the explicit risk tick in the window).
- The boot area (below 0x4000) may only be written by a dedicated bootloader or restore flow, never
  by the rescue: after a full verified backup, built with `fm1_flash.boot_area_for` (the unit's
  chip-key record kept), checked with `check_boot_area` before writing and verified after. Not to
  be released before m-bootloader has been tried on hardware with Wild and NB303.
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
