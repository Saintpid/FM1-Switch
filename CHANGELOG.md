# Changelog

A version is a `VERSION` change with its section below (CI checks the section is there).

## 1.5.4

- **Safer advice when writing stopped halfway.** The window now says to leave the FM-1 switched on
  and try again (or put the backup back) right away, while it still waits in update mode, and to
  switch it off only if FM-1 Switch cannot find it. The REC test points to the window's tick, since a
  dark screen alone does not prove it.

## 1.5.3

- **Plain words everywhere.** Every screen was rewritten so you do not need to know what a
  bootloader, a flash sector or a JEDEC ID is: Protect my FM-1 explains itself as a spare key, the
  map shows squares, the chip has a name you can read, and every "power-cycle" now says what to do:
  switch it off, wait 10 seconds, switch it on. The technical details are still shown, last.
- **Clearer words about update mode.** The FM-1's screen stays dark in update mode: the REC steps say
  so, and the start screen no longer says the firmware does not start when the FM-1 is there after REC,
  a backup or an install. Holding PLAY at power-on (the rescue bootloader's menu) is named as the way
  to see on the FM-1 itself that it is protected.

## 1.5.2

- **Try it: hold REC tells the truth.** After writing, the FM-1 may stay in update mode instead of
  restarting by itself. The window now waits until it has left update mode and come back with REC
  held before it says the rescue works, and the FM-1's screen and the window say to switch it off and
  on when it does not restart.

## 1.5.1

- **If writing goes wrong, it is undone at once.** Should a sector not write, the USB link drop or the
  final read differ (it is read a second time first), Protect my FM-1 and Put back… write everything
  they changed back from the backup read moments before, in the same session, and check it: the FM-1
  is then exactly as it was. If that does not finish either, the window keeps the FM-1 in update mode
  and offers to try again, and says plainly what to do if it gave up.
- **Fewer passing USB errors on Windows.** The questions FM-1 Switch asks the chip in update mode are
  asked again after a passing Windows error.

## 1.5.0

- **A new start screen.** The window now opens on four tasks: Protect my FM-1, Install firmware (the
  firmware list and the rescue, which is now Through update mode…), Back up my FM-1 and My FM-1 won't
  start (the guide). The MIDI input and the adapter rescue are under Advanced.
- **Protect my FM-1.** Installs the rescue bootloader (m-bootloader 2.4 by Julius Vitkauskas), step by
  step: afterwards, holding REC while you switch the FM-1 on always opens update mode. It saves a full
  backup first, shows on a map of the flash what changes (only the boot area), keeps the FM-1's own
  chip key, writes only after you confirm, reads everything back and checks it, and lets you try REC
  once. Not on the newer UCUN flash unless you tick that too (experimental).
- **Put a backup back.** Back up my FM-1 lists the backups saved so far, newest first, with their
  firmware and bootloader. Put back… returns the FM-1 to exactly that moment, bootloader, sounds,
  patterns and settings included; M-VAVE's bootloader back… takes only the boot area from a backup
  made before Protect my FM-1. Only the same FM-1's own, checked backups are taken, and what is on it
  now is saved first.
- **No keys to hold to reach the update mode.** When the FM-1 runs (M-VAVE's firmware, Wild, NB303,
  Felucca), Rescue and Hardware / backup restart it into the chip's own update mode over MIDI. If it
  does not answer, the window says which keys get it there. `fm1_install.py --update-mode` does the
  same from the command line.
- **M-Flasher's flash loader.** Rescue and Hardware / backup now run the loader of M-Flasher
  (m-bootloader's v2.3 build, downloaded and checked): the chip checks every read and every written
  sector with its own CRC32, the flash's unique ID is saved with the backup, and the FM-1's screen
  shows what is happening. JieLi's loader stays for the adapter and with `--loader-kind jieli`.
- **See what is on the FM-1.** Hardware / backup names the bootloader (M-VAVE's, m-bootloader and its
  version, or damaged) and the firmware (Wild 0.3.38, FM-1 016, Felucca, ...), and whether it is
  whole.
- **Knows the community firmwares.** The firmware on the FM-1 is named from The Firmware Hub's list
  of about 300 firmwares (OberVave, Felucca, SLOOP, ...), by the identity it carries; several that
  share one are told as "one of: ...". What is not known about a firmware stays "unknown".
- **M-VAVE's FM-1 016.** M-VAVE's download now gives 016; Rescue takes V15 or 016 and writes 016 whole
  (to 0xA9000). It points out when the firmware on the FM-1 keeps its data in another layout.
- **The FM-1 keeps its own bootloader, for V15 too.** A rescue to V15 no longer stops when the FM-1's
  boot area is not V15's (016's, m-bootloader): it must just be one that can start the FM-1.
- Firmware list: [The Firmware Hub](https://thefirmwarehub.com) (CC BY 4.0).
- Thanks to Julius Vitkauskas's [M-Flasher](https://github.com/jvitkauskas/m-flasher) and
  [m-bootloader](https://github.com/jvitkauskas/m-bootloader) (MIT): their loader, formats and checks.

## 1.4.1

- **Firmware checked against the flash before flashing.** The app area of the chosen `.fwsc` is
  decrypted with the chip key and its exact-ID gate read: Felucca-based firmware carries each JEDEC
  ID it accepts as a 32-bit constant. On the published packages Wild from FM-1_864 and NB303 from
  FM-1_908 carry B36014; older Wild, older NB303 and n001se 1.7.1 (FM-1_825/826) carry only 856014.
- **Rescue blocks** firmware that does not support the measured flash right after identification,
  before any flash read or write, even with the UCUN opt-in. M-VAVE's V15 counts as supported
  (upstream reports); firmware without any ID constant is "unknown" and gets a warning.
- **USB-MIDI install warns** (default No) before installing a package that refuses UCUN flash, and
  names the flash of the last FM-1 identified with Hardware / backup. A running firmware does not
  report its flash over MIDI, so this is a warning, not a measurement.

## 1.4.0

- **Hardware / backup…** identifies the measured flash in an explicit UBOOT session and saves two
  matching full reads, a disk-verified backup, SHA-256 sidecar and hardware metadata without flash
  writes or a firmware package. Unknown chips get a hardware report and are refused.
- Names Puya P25Q80H (856014), UCUN UC25HQ80 (B36014) and the known keyboard flash (EB6014).
  Chip identity does not prove firmware compatibility or persistent saves.
- **Experimental UCUN recovery** is available over direct USB only after explicit opt-in. Backup,
  chip key/type, keyboard calibration, V15 head and protected-region checks remain mandatory.
  UCUN writes through the transporter are refused. Actual UCUN device acceptance is pending.
- Recovery refuses multiple UBOOT devices rather than selecting one. Failed update handoffs explain
  possible alternate-flash incompatibility without diagnosing a chip from an error alone.

## 1.3.3

- **Rescue on the newer FM-1s.** FM-1s sold since autumn 2026 (mostly through AliExpress) have
  another flash chip, a UC25HQ80 (flash id B36014) in place of the first units' Puya (856014). Rescue
  refused those as "not the FM-1's chip and flash"; it now accepts both. Everything else is checked
  as before (chip key, a full backup first, SMK-37 keyboards still refused). Not yet tried on a newer
  unit: tell us how it goes.

## 1.3.2

- **M-VAVE's SMK-37 keyboards (experimental).** The SMK-37 Pro, SMK-37 Elite, MKE-P37 and Starrykey
  37 Play have the FM-1's chip and the same update. FM-1 Switch now hears them, lists their firmware
  files and installs a firmware made for that model, M-VAVE's own stock files included (the V16 of
  the Pro and the MKE-P37 too, which are packed differently). Check / unbrick… tells which keyboard
  it is. This is not yet tried on a real keyboard: tell us how it goes.
- **No FM-1 firmware on a keyboard, and the other way round.** An FM-1 file is refused on an SMK-37
  before anything is sent, and an SMK-37 file on an FM-1.
- **Rescue stays FM-1 only.** An SMK-37 in the chip's own update mode looks just like an FM-1 there.
  Rescue now recognises it (its flash, or its factory calibration) and stops before it writes
  anything. The keyboard's calibration and pairing are never touched.

## 1.3.1

- Startup confirmation also checks the normal FM-1 MIDI port after restoring
  stock from firmware that used a manually selected, renamed port.

## 1.3.0

- Recovery requires two identical full flash reads and a saved-file readback before writing. Backups have unique names and SHA-256 sidecars.
- Recovery distinguishes verified flash from confirmed firmware startup over MIDI.
- Select a MIDI input manually for firmware using an unfamiliar USB port name.
- Permission preparation is shown before macOS access requests; recovery waits for helper readiness before device instructions. The device timer starts after administrator approval. Stop is disabled while writing and the window stays open during operations.

## 1.2.3

- Mac DMGs use `/Users/macbook/Desktop/Certificat/Macinstaller.png`, or the
  image explicitly selected by Noisebud Builder through `NOISEBUD_DMG_BACKGROUND`.
  A missing or invalid image stops before compilation/signing; the exported
  sample image is no longer silently used for releases.
- The CI-only unsigned development build selects its bundled test fixture
  explicitly. Firmware, USB, rescue and installer identity behavior is unchanged.

## 1.2.2

- **Install works again.** Installing a firmware stopped right at the start with "got an unexpected
  keyword argument 'value'" (the progress bar's update). The same fault hit Rescue's progress bar. If
  an install stopped this way, the FM-1 waits in its update mode: install the firmware again and it
  finishes.

## 1.2.1

- Mac source delivery now includes the Builder flow definition beside the ZIP
  and runner. Register its entry in Builder's existing flows.json on the Mac.
- Release simulation tests use isolated firmware folders instead of reading
  unrelated firmware from the developer's projects or Downloads.

## 1.2.0

- **Installers.** Windows gets a setup program (FM-1 Switch Setup 1.2.0.exe) that installs it under
  Program Files\Noisebud with a Start menu entry. The Mac gets a signed and notarized installer
  package in a DMG that installs FM-1 Switch in Applications. Both show the licence first.
- **A licence for free software.** FM-1 Switch costs nothing and needs no registration. Using any
  of its functions is entirely at your own risk: neither the author nor Noisebud takes any
  responsibility for what happens to your hardware.
- On a Mac with Apple Silicon, FM-1 Switch and its rescue helper run natively, also when started
  from an Intel (Rosetta) program. It needs macOS 11 or newer, and the app is universal (Apple
  Silicon and Intel).
- The Mac build in Noisebud Builder now uses its own flow for free apps, without a licence manifest.

## 1.1.0

- **A new look.** The window follows your system's dark or light mode in Windows 11's own style, with
  sharp text on scaled screens. The FM-1's state sits on top with a coloured dot (green: running,
  yellow: update mode, red: a problem), the firmware files are listed in columns, and Install is the
  one blue button.
- Run from source without the MIDI packages, the app said "FM-1: not found". Now it says that MIDI is
  missing and how to fix it.
- **Rescue to any firmware.** Before a rescue starts, you choose what goes back on the FM-1. M-VAVE's
  V15 is chosen first, but any FM-1 firmware file works: Wild, NB303, n001se, FiMba-1, another
  M-VAVE version, or other people's firmware (Browse…). The firmware's app is written, and the
  FM-1's own boot part always stays, as in a normal update. A firmware made for another chip is
  refused before anything is written.
- **A clear warning:** a rescue starts only after you tick that it is at your own risk. The first
  time the app starts it shows the disclaimer (also under About…, and in DISCLAIMER.md).
- **Experimental: rescue through an FM-1-transporter adapter** (an RP2040 board wired to the
  FM-1's USB lines), for an FM-1 that shows nothing at all on USB. It is in the guide when a rescue
  never sees the FM-1. It has not been tried on hardware from this app yet.
- The list shows every FM-1 firmware file. Files without Felucca's updater (V15 and others) are
  marked "rescue only".

## 1.0.0

FM-1 Switch in its own repository (it came from Wild's `tools/`).

- **Install any FM-1 firmware in either direction**, whatever its number (Wild, NB303, n001se,
  FiMba-1), with a progress bar. M-UPGRADE only takes a higher number.
- **Check / unbrick…** opens a step-by-step guide. First it listens to the FM-1: is it running,
  waiting for an install that was cut off, or in the chip's own update mode? Only if nothing answers
  does it ask what the screen shows and what happened last. Then it gives you one thing to try, with
  one button: Install, Finish the install, Check again, or Try to unbrick.
- **Rescue…** puts M-VAVE's stock firmware (V15) back on an FM-1 whose firmware no longer starts,
  on Windows and on Mac, with no extra hardware. It saves the whole flash first, and it never
  writes the chip's boot code. It finds V15 among your files, or downloads it from M-VAVE.
- It also looks in `Firmware n001se` and `Firmware Original`.
