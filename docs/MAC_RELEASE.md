# Required Mac build and release workflow

Revision 1, 2026-11-02. This is the specification for the next implementation;
the 1.1.0 scripts do not yet satisfy it. It does not authorize hardware flashing.

Status: implemented in 1.2.0 (see PROJECT_MEMORY.md for what is verified and what is not).

## Product and authoritative requirements

FM-1 Switch is free/open source (GPL-3.0). No registration, activation, license
verification, Registration app, Reset app or invented Noisebud product/license
manifest is required. Keep the application's bundle identity and GPL/disclaimer.
The user requires a signed installer like other Noisebud products.

Read the current shared sources before implementation:

- `C:/Projects/AGENTS.md`: shared release, native build tools and SEM handoff rules.
- `C:/Projects/Noisebud Builder/docs/DELIVERY.md` and `docs/FLOWS.md` in that project:
  Builder's actual export contract and static delivery gate.
- `C:/Projects/Noisebud Mac Signing/README.md`: canonical signing runtime and
  certificate/configuration verification.
- `C:/Projects/MAC_NATIVE_EXECUTION_GUIDE.md`: final binary and process acceptance.

These requirements supplement the standalone app's workflow. This is not a
SynthEdit module and must not acquire `synthdeploy.json` or use the module deployer.

## Builder integration without a license manifest

Implement an explicit custom flow. Rename the exported `build_mac_app.sh`, for
example to `build_fm1_mac.sh`, and update its callers. Builder's known app
detector runs before custom flow selection and requires a product/license
manifest; leaving that filename while removing the manifest still fails.
Remove the artificial `src/noisebud-product.json` requirement from the scripts,
tests and instructions when migrating. Do not change Builder to pretend this
free app has registration or route it through the raw-plugin legacy adapter.

The intended export contract is one `FM1Switch/` root containing VERSION, an
explicit marker `FM1SWITCH_BUILD_FLOW.txt`, the renamed build entrypoint, Python
sources, dependencies/configuration, required signing runtime, license/disclaimer
and all release verification tools. Tests must inspect the real exporter output.

Deliver a JSON list such as the following with the implementation:

```json
[
  {
    "id": "fm1-switch-source",
    "name": "FM-1 Switch · free app",
    "kind": "custom",
    "revision": 1,
    "marker": "FM1Switch/FM1SWITCH_BUILD_FLOW.txt",
    "entrypoint": "FM1Switch/build_fm1_mac.sh",
    "arguments": [],
    "environment": {},
    "outputGlobs": ["FM1Switch/dist/FM1Switch_macOS_v*.dmg"]
  }
]
```

This example specifies future files; they do not exist yet. The custom script
runs with the ZIP root as working directory, so resolve resources relative to
the script, not `$PWD`. Honor `NOISEBUD_SETUP_DEST`, put the fresh final DMG in
`FM1Switch/dist/`, and verify any published copy. Merge the same definition into
the Mac user's existing `flows.json`; preserve other flows. Deliver clear setup
instructions: a definition on Windows alone does not register a Mac custom flow.

## Source export and standalone runner

1. Create the final ZIP and runner locally under the project (for example
   `exports/`); use a reviewed file allowlist and preserve historical exports.
2. Write portable ZIP paths with `/`, never Windows backslashes. Write actual
   shell bytes as UTF-8 without BOM and LF with a shebang. Validate the finished
   archive, not a separately reconstructed test archive.
3. Run the shared gate on the actual ZIP, with the delivered custom flow:

   ```powershell
   python "C:/Projects/Noisebud Builder/tools/check_export.py" "C:/Projects/FM1-Switch/exports/FM1Switch_mac_ready_v<NEXT_VERSION>.zip" --flows "C:/Projects/FM1-Switch/builder-flows.json"
   ```

   The names above are the intended implementation paths. Require exit 0 and
   `EXPORT OK`; record SHA-256/flow ID. Also validate the runner and shell syntax.
4. Copy each explicit ZIP and `.command` using `cmd /c copy /Y` with absolute
   paths to `C:/Users/Johannes/Documents/SynthEdit Projects/Mac Export/SEM`.
   Verify target existence, size and SHA-256 against local originals and confirm
   the new product ZIP is the latest matching export. Deliver the flow definition
   as well, with registration instructions.
5. The standalone runner searches only beside itself, chooses the highest
   numeric export version, and stops if none exists. Never fall back to an old
   extracted folder. Bind/verify the chosen archive and extract safely into a
   fresh local cache/work directory; never build inside Resilio/SEM.
6. A top-level EXIT trap preserves the exit status and plays Glass.aiff on
   success or Basso.aiff on failure using afplay, with terminal bell fallback.
   Internal scripts avoid a second sound when the wrapper/Builder owns it.

## App, installer and signing

- Require native build tools on the actual hardware, including when launched
  from Rosetta. Preserve Apple Silicon and Intel support. A failed universal
  build must stop; do not silently ship a single-architecture fallback.
- Plan universal-compatible Python/Tk, PyInstaller, MIDI/USB native dependencies
  and libusb together. Check every final Mach-O executable, extension, framework
  and dylib for both slices. A universal main executable proves only that file.
- Use macOS 11.0 as the default minimum, export MACOSX_DEPLOYMENT_TARGET=11.0
  for native dependency builds, and declare the same minimum in the app. Verify
  actual LC_BUILD_VERSION/LC_VERSION_MIN_MACOSX per slice before signing. If a
  dependency cannot meet it, stop and document the blocker; a higher product
  minimum requires an explicit user decision, not just a plist change.
- Use byte-identical compatible canonical signing runtime/configuration in the
  source export and verify hashes against upstream. Use its common Mac config,
  full SHA-1 certificate selection and G2/team/timestamp/actual-leaf gates.
  No private keys or credentials go into the export.
- Sign nested native code in the proper order, then the app, with required
  runtime/entitlements for the packaged application. Do not rely on `codesign
  --deep` as the signing strategy; retain recursive signature verification.
- Create a real PKG installer for the app, with the applicable Noisebud installer
  presentation and installation choices. Include GPL/disclaimer materials;
  exclude registration, reset and activation steps. Follow the existing shared
  installer implementation where applicable without adding a licensing binding.
- Sign PKG with Developer ID Installer, sign the distribution DMG with Developer
  ID Application, and perform applicable notarization/stapling/trust checks.
  Verify installed/staged payload signatures and final certificates. A DMG
  containing an app alone is not the requested PKG installer.
- Release mode must stop if signing identities, private keys or notarization
  prerequisites are missing or verification fails. An explicitly separate local
  unsigned development build must never be published as the signed release.

## Native launches, tests and acceptance

Audit Finder start, installer start, app subprocesses and elevated rescue
relaunches (`sys.executable`, Popen and osascript). On Apple Silicon the app and
independent helpers must remain arm64 even under a Rosetta parent; on Intel use
x86_64. Apply the shared native launch policy, not only a plist declaration.
Architecture probes must not write firmware or require a real rescue operation.

Preserve all firmware safety limits, backups, explicit risk confirmation and
experimental-path labeling from CLAUDE.md. Run existing simulated tests. Replace
the current reconstruction-based Builder test with tests of actual exported
bytes and the shared gate, including rejection of backslash paths/CRLF/BOM,
missing custom entrypoint and accidental reintroduction of the licensed app flow.
Test mandatory release failures, wrapper version selection and exported helper
imports from a fresh interpreter/unrelated directory without inherited PYTHONPATH.

Before claiming completion, bump VERSION/changelog, update README and tests to
match the final implementation, and record separately:

1. Windows tests and static export verification.
2. Actual Mac compilation and all final slices/minimum-OS gates.
3. Developer ID signing, installer/DMG notarization and trust verification.
4. Installation and installed app/native helper probes, including a Rosetta
   parent on Apple Silicon, the Intel path and the supported minimum OS.

Use Builder's SEM/build-logs/builder diagnostics and the actual failed command
when a Mac run fails. EXPORT OK or Redo is not Mac build/installer acceptance.
