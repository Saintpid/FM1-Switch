# Alternate FM-1 flash: investigation, 2026-11-05

Status: investigation of 1.3.2, followed by the 1.4.0 implementation below.
Actual hardware acceptance of the new paths remains pending. The attached social posts are
reports to investigate, not instructions to bypass device checks.

## Evidence

Some FM-1 units report UCUN UC25HQ80 JEDEC `0xB36014` instead of the Puya
P25Q80H `0x856014`. Both are reported as 1 MiB. A front-panel logo is not
a reliable hardware probe; use the measured ID. These reports do not establish
that every newer unit has the alternate part.

- [Melodee issue 110](https://github.com/keremimo/melodee/issues/110): the
  reporter describes failed saves, refused resource writes and a self-update
  stopping at zero requests. After the change, saves and settings survived
  power cycles and self-update worked. A November 4 follow-up confirms
  persistent saves on 1.6.1. These are upstream hardware reports, not our QA.
- [Melodee commit b8f9f9f](https://github.com/keremimo/melodee/commit/b8f9f9ff839a43b0f80907980b3678e29c0dd0b4)
  changes the persistence gate to accept either exact ID, with a regression
  test that continues rejecting unknown parts. The
  [1.6.0 release notes](https://github.com/keremimo/melodee/releases/tag/v1.6.0)
  include this support. This establishes a concrete software compatibility
  problem, rather than merely a speculative hardware difference.
- [SLOOP issue 238](https://github.com/isod89/sloop-fm1/issues/238) reports
  volatile saves in SLOOP 2.8.4 and Felucca 1.8.0, while stock V15 saves on the
  same device. [SLOOP PR 241](https://github.com/isod89/sloop-fm1/pull/241)
  proposes one shared exact-ID list used by the app, update loader and USB
  recovery. It was displayed as open when inspected; closure of issue 238
  alone is not proof that a published SLOOP binary contains the fix.

The zero-request error in the screenshot is consistent with the Melodee
report, but does not identify the chip or prove that this is the cause on that
particular device. Corrupted presets and sequencer behaviour after switching
to stock firmware are separate, unconfirmed reports: retained storage formats
are another possible explanation. Do not infer that a chip-ID fix repairs them.

## Findings in FM-1 Switch 1.3.2

`tools/fm1_install.py` parses the MIDI model/version identity, not the JEDEC ID.
Successful transfer or a matching running identity does not prove persistent
saves. `tools/fm1_switch.pyw` inspects local packages; it has no verified
catalogue of per-version flash compatibility.

`tools/fm1_rescue.py:rescue()` reads the key, flash type and JEDEC ID through
the RAM loader, but accepts only `(0x980F, 3, 0x856014)`. It therefore refuses
the alternate part before backing up or writing. This is an independent host
check; patching a firmware file cannot change it. The current project policy
in CLAUDE.md explicitly requires this gate, so this investigation leaves it in
place. Its eventual revision must retain all other device and write checks.

UBOOT probing starts a program in device RAM. Treat that as a distinct recovery
operation, not as an invisible, routine MIDI hardware query. Adapter firmware
can also have its own device checks and needs separate verification.

## Can the application adapt firmware?

Yes, for individually analysed versions. Prefer the author's corrected release
or a source rebuild with exact-ID support. A general replacement of bytes
`85 60 14` with `B3 60 14` is not sufficient: constants can be encoded in
instructions, images may be encrypted, package checksums must remain correct,
and more than one gate may exist. Replacing the old ID can also break support
for the original chip.

A future binary patch facility would need an exact input SHA-256 allowlist,
reviewed version-specific transformations, validated package integrity and
output hashes, and a new output file with provenance. It must reject unknown
inputs. Installation and persistence need acceptance on both flash variants.
An already-running firmware that refuses the update handoff may still prevent
delivery of a corrected package; changing the selected file cannot repair the
running program before the update starts.

## Proposed implementation order

1. Explain possible alternate-flash incompatibility in relevant diagnostics,
   while keeping the cause unconfirmed without a measured ID. Record identity,
   update stage, request count and package hash in a useful diagnostic export.
2. Add provenance-backed compatibility information for exact firmware versions;
   unknown files remain unknown. Prefer corrected upstream releases.
3. Design an explicit hardware-information/read-only-backup path. For supported
   recovery sessions, retain key/type/ID alongside backup hash and package
   identity. Do not claim that ordinary MIDI identity reveals the flash chip.
4. Separately implement and test alternate-flash recovery: exact known IDs,
   unknown IDs rejected, two matching full reads and saved-backup verification,
   chip-key and keyboard/calibration checks, V15 head policy, and the existing
   `0x4000..0x92FFF` write boundary. Verify the actual RAM loader and adapter
   paths; passing host simulations is not device acceptance.
5. Consider version-specific patching only where an upstream compatible package
   is unavailable and the transformation has been verified.

The Linux request in the screenshots is a separate packaging/acceptance task.
The existing rescue code has a Mac/Linux USB path, but that alone does not
establish a supported Linux desktop release.

## Implemented in 1.4.0

Hardware / backup explicitly starts the existing checked RAM loader, names the
measured part and saves identification JSON even when a part is refused. Known
FM-1 parts can be backed up without a package: two matching full reads, disk
readback, SHA-256 and metadata. Unknown parts get no flash read or write; the
keyboard flash is refused and calibration is checked after backup. MIDI identity
still does not reveal the JEDEC ID. Multiple UBOOT devices are refused.

UCUN recovery requires a separate explicit experimental opt-in over direct USB.
The default verifies and keeps the backup, then refuses writing. Adapter UCUN
writes remain refused even with opt-in. Existing key/type, app key, V15 head,
keyboard and protected-region checks remain. Update handoff errors explain
possible alternate-flash incompatibility without inferring a hardware diagnosis.

Simulation coverage is provided for both known FM-1 IDs, unknown IDs and wrong
types/keys, the metadata, CLI no-write constraints, UCUN opt-in, adapter refusal,
calibration/head checks and corrupt backup readback. No hardware was probed or
flashed during development. Firmware adaptation, a compatibility catalogue,
automatic preset migration and Linux packaging remain future work.
