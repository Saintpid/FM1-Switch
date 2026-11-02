# FM-1 Switch project instructions

Read `PROJECT_MEMORY.md`, `CLAUDE.md` and `docs/MAC_RELEASE.md` before changing
the project. Shared instructions in `C:/Projects/AGENTS.md` also apply.

FM-1 Switch is a free GPL-3.0 application. It must not require a Noisebud
registration/license manifest, activation, Registration or Reset app. Signing
and notarization are distribution requirements independent of licensing.

The required next Mac release workflow is specified in `docs/MAC_RELEASE.md`:
an explicitly registered Builder custom flow, a verified portable source ZIP,
universal native app, signed PKG installer in a signed/notarized DMG, and actual
Mac acceptance. 1.2.0 implements it (`build_fm1_mac.sh`, `mac/build_release.py`,
`tools/export_mac.py`, `scripts/release.ps1`); the signed build on a real Mac is still to be
accepted (see `PROJECT_MEMORY.md`).

Use the canonical signing runtime from `C:/Projects/Noisebud Mac Signing` and
the shared native execution policy; do not invent a separate signing system or
weaken checks to make an export pass. Do not apply the SynthEdit module deploy
script to this standalone application.

Preserve firmware/rescue safety checks in `CLAUDE.md`. Tests must use simulation;
do not flash hardware as part of build verification. Keep project text in English.
Follow the project's branch/PR/CI workflow. Bump `VERSION` and add its changelog
section when implementing the build changes; documentation of pending work alone
does not claim a new built product release.
