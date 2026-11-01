# Changelog

A version is a `VERSION` change with its section below (CI checks the section is there).

## 1.0.0

FM-1 Switch in its own repository (it came from Wild's `tools/`).

- **Install any FM-1 firmware in either direction**, whatever its number (Wild, NB303, n001se,
  FiMba-1), with a progress bar. M-UPGRADE only takes a higher number.
- **Check FM-1** tells you which state the FM-1 is in: running, an install cut off, the chip's own
  update mode, or not found. It also tells you what to do in each state.
- **Rescue…** puts M-VAVE's stock firmware (V15) back on an FM-1 whose firmware no longer starts,
  on Windows and on Mac, with no extra hardware. It saves the whole flash first, and it never
  writes the chip's boot code. It finds V15 among your files, or downloads it from M-VAVE.
- It also looks in `Firmware n001se` and `Firmware Original`.
