# FM-1 Switch project memory

## 2026-11-05 — 1.4.0 hardware identification and experimental UCUN recovery

Implemented Hardware / backup (CLI --inspect), an explicit UBOOT RAM-loader
session with no flash writes or package requirement. Measured key/type/ID,
named Puya/UCUN/known keyboard part, standalone hardware JSON even on rejection,
and two matching reads + disk readback + backup SHA-256/metadata for known FM-1
parts. Unknown parts and keyboards remain refused. Multiple UBOOT devices stop
before a target is opened. MIDI identity still gives no JEDEC ID.

UCUN B36014 recovery requires explicit experimental opt-in (--allow-ucun-write),
direct USB only. Default keeps the verified backup and refuses writes; adapter
writes remain refused with opt-in. Existing key/type, app-key, calibration,
V15 head and protected range gates remain. The GUI explains possible alternate
flash incompatibility on update handoff errors without inferring a chip ID.

Simulation tests cover these paths; real UCUN writes, persistent saves, actual
Mac packaging/installed native architecture and permission UX remain acceptance
work. No firmware patching, settings erasure or real-device operations added
to verification. See docs/HARDWARE_COMPATIBILITY.md and README.md.

Local validation: install/switch/rescue/export suites passed on Windows; the
export suite also passed Builder's gate on its generated source archive.
The new dialogs were visually checked with device access mocked out. This is
not an installer deployment or acceptance on an actual Mac/device.

## 2026-11-05 — Alternate flash investigation; documentation only

See `docs/HARDWARE_COMPATIBILITY.md` for primary sources and proposed work.
UCUN UC25HQ80 (JEDEC B36014) compatibility is backed by Melodee's exact-ID
gate fix, release 1.6.0 and upstream hardware reports (including 1.6.1).
SLOOP PR 241 covers app, update loader and USB recovery; do not infer a fixed
release from a closed issue. A zero-request update failure can share this
cause, but the screenshot does not prove it. Stock preset/sequence corruption
after a downgrade remains a separate unconfirmed report.

FM-1 Switch 1.3.2 MIDI identity exposes no flash ID; rescue still requires
856014 and refuses B36014 before backup/write. No safety gate was relaxed.
Version-specific firmware adaptation is feasible, but universal byte replacement
is not a verified solution. Prefer corrected upstream packages; keep recovery
support and firmware persistence compatibility as separate acceptance tasks.
Documentation only: VERSION remains 1.3.2; no build, export or hardware operation.

## 2026-11-02 — Distribution decision

GitHub is for source code; ready-made installers are distributed through Patreon.
Do not commit compiled applications/installers or upload GitHub Release assets
or Actions artifacts containing them. CI retains build verification but its
installer/DMG upload steps have been removed. This is distribution configuration
and documentation, with no new built product version claimed.

## 2026-11-02 — 1.3.0 recovery confidence

Implementation: two matching full reads plus saved-backup readback and SHA-256
sidecar before writing; unique backup filenames. GUI distinguishes flash
verification from confirmed running MIDI identity. Explicit MIDI input selection
supports unfamiliar port names in install/diagnosis/recovery confirmation.
macOS access preparation precedes MIDI/folder access. Rescue displays system
approval before launching its helper; the helper signals ready only after
privilege/dependency checks. Device wait starts after that. Stop is disabled
during writing, and closing is blocked during an operation.

Tests use simulated devices only. Real Mac permission UX, installed native
architecture, signed packaging and real-device recovery remain acceptance work.
Transporter stays experimental; no Flipper/softkey/bootloader/settings writes
were added. The protected ranges and V15 head policy remain unchanged.

## 2026-11-02 — 1.2.3, correct Mac-local DMG background

Builder's synced 1.2.2 log showed create-dmg using the exported
mac/assets/MacInstaller.png. The user requires
/Users/macbook/Desktop/Certificat/Macinstaller.png. The release script now
honors NOISEBUD_DMG_BACKGROUND, with that exact Mac-local default. Missing or
non-PNG input stops before compilation/signing; no bundled fallback. CI's
unpublished unsigned build explicitly selects its test fixture. Simulation
tests cover selection, missing/invalid/default failures and the actual
create-dmg argument. Shared pinned snapshots and all firmware code are unchanged.
Actual signed Mac packaging/visual/native acceptance of 1.2.3 remains pending.

## 2026-11-02 — 1.2.1, actual Builder handoff

The user's screenshot still selects the historical 1.1.0 ZIP with backslash
entries. The checkout already contained the merged 1.2.0 implementation, but
its new export had not replaced that handoff in the screenshot. Preserve 1.1.0;
deliver a fresh version and register fm1-switch-source on the Mac.

Running the normative scripts/release.ps1 -SkipWindowsInstaller on the actual
PC exposed a simulation test reading real firmware from C:/Projects through
PROJECTS (and potentially Downloads). The test now mocks those discovery roots
to fixture directories while retaining the same scan/install assertions. App
and firmware/rescue logic are unchanged. release.ps1 also copies its custom
flow definition to SEM/FM1Switch_builder-flows.json, size/SHA-256 verified.

This is a Mac source handoff, not a Windows installer rebuild or proof of a
signed Mac package. Use 1.2.1 after registering its flow; actual Mac compilation,
signing/notarization, installation and native acceptance remain required.

## 2026-11-02 — 1.2.0: installers, the custom Builder flow (implementation)

User decision (same day): Windows installers like PER and Pre Flight (Inno Setup), with an EULA
for free software that disclaims any responsibility of the author or Noisebud for anyone's
hardware; the same disk locations as those projects on Windows and Mac.

Implemented in 1.2.0 (branch claude/project-thread-039t1i, PR #2):

- Windows: `scripts/build-installer.ps1` (PyInstaller one-folder app, Inno Setup 6,
  `installer/FM1Switch.iss`, `installer/EULA.txt`, optional signing by
  NOISEBUD_WINDOWS_CERT_THUMBPRINT) and `scripts/release.ps1` (tests, installer to
  `D:/Dropbox/SynthEdit/000 Klara pluggar 000/--- Nya Setupfiler`, Mac export, Builder gate, SEM,
  all cmd-copied and SHA-256 checked). CI builds the installer unsigned.
- Mac: Builder custom flow `fm1-switch-source` (`builder-flows.json`, marker
  `FM1SWITCH_BUILD_FLOW.txt`, entrypoint `build_fm1_mac.sh`); `build_mac_app.sh` and
  `src/noisebud-product.json` removed. `tools/export_mac.py` writes the ZIP from git HEAD under
  `exports/` (portable paths, LF, modes) and checks its bytes. `mac/build_release.py`: native build
  host, python.org universal2 Python 3.12, arm64+x86_64 wheels joined by delocate-merge, PyInstaller
  universal2, every Mach-O both slices and minOS <= 11.0 before signing, shared G2 signing runtime
  (byte-identical copies, `mac/SOURCE_HASHES.json`), inside-out codesign, verify-code --tree,
  verify_native_mac.py probes incl. an x86_64 parent, component PKG -> productbuild -> productsign
  -> notarize -> DMG (Follower's background/icon) -> codesign -> notarize -> dist/ and setup folder.
  `--unsigned-dev` builds an ad hoc test DMG in dist-dev/ only. The runner .command selects the
  highest version beside itself, unpacks safely into ~/Library/Caches/NoisebudBuildTools, Glass/Basso.
- App: `--native-process-probe`; restarts itself native when started translated; the rescue helper is
  launched through `/usr/bin/arch -<hardware>`. LSRequiresNativeExecution, LSMinimumSystemVersion 11.0.

Verified: simulated tests, export bytes and refusals (tests/export_test.py), CI (Windows installer
build, unsigned Mac dev build on GitHub's macOS runner when green). NOT verified yet: release.ps1 on
the user's PC, Builder's check_export.py on the real export from the PC, the Builder flow registered
on the Mac, the signed/notarized release on the Mac, installation, Finder launch, Intel and
macOS 11 acceptance.


## 2026-11-02 — Required Mac delivery workflow; documentation only

User decision: FM-1 Switch is free/open source and needs no Noisebud license or
registration manifest. It must still have an installer and be signed like other
Noisebud products. The next implementation must follow `docs/MAC_RELEASE.md`
and the shared Builder delivery/native/signing guides referenced there.

Inspection of source version 1.1.0 and its actual SEM export found:

- Builder rejects the ZIP before building: its original ZIP entries contain
  Windows backslashes. The export uses PowerShell Compress-Archive. The test
  constructs another ZIP with Python, so it does not test the delivered bytes.
- `src/noisebud-product.json` was added to satisfy Builder's licensed app flow;
  that is not the intended licensing model. Use an explicit custom flow instead.
  Builder detects `build_mac_app.sh` before custom markers: rename the exported
  entrypoint as part of the migration, rather than just deleting the manifest.
- Current packaging makes a DMG containing an app, not a PKG installer. Signing
  and notarization are optional environment-variable branches and do not use the
  canonical Noisebud signing runtime/certificate gates.
- Failed universal2 builds fall back to one architecture. Only the main
  executable's architecture is reported, after packaging. The plist says 10.13;
  no recursive final binary minimum-OS/native-process gates establish support.
- The standalone wrapper uses a hardcoded SEM path, picks by file date, unpacks
  and builds in the sync folder, can reuse old sources if the ZIP is absent,
  and lacks the required completion sounds.
- Export staging is under TEMP rather than the project, and the SEM copies lack
  size/SHA-256 verification. The existing export is not a verified Builder handoff.

Only documentation was requested and changed in this step. Existing sources,
manifest, tests, VERSION 1.1.0 and historical ZIPs remain unchanged. The build
problems above are still pending; no new export, signing, installer or Mac QA
has been performed. Do not describe the README/changelog's existing Builder
support claim as verified acceptance. Use this memory and the normative release
specification to implement the next version, then update the README/tests together.
