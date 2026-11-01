# Changelog

A version is a `VERSION` change with its section below (CI checks the section is there).

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
