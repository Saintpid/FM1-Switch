# Third-party code and files

FM-1 Switch is GPL-3.0. It uses, with thanks, work by others under the MIT License:

| What | From | How |
| --- | --- | --- |
| The flash formats, the boot-area checks and builders, the device analysis and the sector map in `tools/fm1_flash.py`; the MFLR loader protocol in `tools/fm1_rescue.py` | [M-Flasher](https://github.com/jvitkauskas/m-flasher) (`src/formats.rs`, `src/boards.rs`, `src/sectormap.rs`, `src/jl.rs`) | ported from Rust to Python |
| The flash loader `mflr-loader-a.bin` and `mflr-loader-b.bin` (v2.3) | [m-bootloader](https://github.com/jvitkauskas/m-bootloader) releases | downloaded at run time and checked against their SHA-256; not part of this repository |
| The UBOOT protocol, after [jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool) (MIT, (c) 2023 Andrey Grigoryev), and its `wl82loader.bin` | jl-uboot-tool | downloaded at run time and checked |

M-Flasher and m-bootloader, both (c) 2026 Julius Vitkauskas:

```
MIT License

Copyright (c) 2026 Julius Vitkauskas

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## The Firmware Hub's list

The firmware list in `tools/fm1_firmwares.py` (each firmware's id, name, maker, repository, identity
pattern, build identity, flash chip and base) is The Firmware Hub's catalogue,
https://thefirmwarehub.com/catalog.json, under the
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/) licence:
"Firmware list: The Firmware Hub – https://thefirmwarehub.com". It is shortened to those fields; no
card texts or pictures are taken. Each firmware is its maker's, under its own licence, at the
repository the list names.
