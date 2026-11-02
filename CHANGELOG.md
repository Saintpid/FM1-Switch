# Changelog

A version is a `VERSION` change with its section below (CI checks the section is there).

## 1.3.0

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
