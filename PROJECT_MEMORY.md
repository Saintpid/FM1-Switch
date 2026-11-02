# FM-1 Switch project memory

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
