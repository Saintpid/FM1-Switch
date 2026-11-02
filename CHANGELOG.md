# Changelog

A version is a `VERSION` change with its section below (CI checks the section is there).

## 1.1.0

- **A new look.** The window follows your system's dark or light mode in Windows 11's own style, with
  sharp text on scaled screens. The FM-1's state sits on top with a coloured dot (green: running,
  yellow: update mode, red: a problem), the firmware files are listed in columns, and Install is the
  one blue button.
- The Mac version can be built in **Noisebud Builder**. The Mac export appears in its list, and the DMG
  ends up in the setup folder.
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
