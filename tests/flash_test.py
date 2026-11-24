#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/fm1_flash.py on synthetic flash images: the boot area's file table, the bootloader stored in
it and whether it can start the unit, the boot areas built for a new bootloader (the unit's chip-key
record kept, nothing outside the 16 KiB touched), the app area's table and the sector map.
  python3 tests/flash_test.py"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fm1_flash as F  # noqa: E402

failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<70} {'ok' if cond else 'FAIL'}")
    failed += not cond


def refused(f, *a):
    try:
        f(*a)
        return None
    except F.FormatError as e:
        return str(e)


from fm1_fixture import STOCK_BOOT, bankcb, open_boot, unit_flash  # noqa: E402


# --------------------------------------------------------------- reading ---
stock = unit_flash()
t = F.boot_table(stock)
ok([e.name for e in t] == ["uboot.boot", "isd_config.ini", "app_dir_head", "key_mac"], "the boot area's file table")
ok(F.stored_bootloader(stock) == STOCK_BOOT, "the bootloader stored in it")
ok(F.bootloader_info(stock)[0] == "stock", "M-VAVE's bootloader is told as stock")
ok(refused(F.check_boot_area, stock) is None, "a unit's own boot area can start it")
ok(F.boot_table(b"\0" * F.FLASH_SIZE) == [], "no table when the header is damaged")
ok(F.bootloader_info(b"\0" * F.FLASH_SIZE)[0] == "damaged", "a damaged boot area is told so")
flipped = bytearray(stock)
flipped[F.BOOT_AT + 100] ^= 1
ok(refused(F.check_boot_area, bytes(flipped)) == "its bootloader is damaged or missing",
   "a bootloader with a flipped bit cannot start it")
ok(refused(F.check_boot_area, unit_flash(key_ok=False)) == "its chip-key record is damaged",
   "a damaged chip-key record is refused")

# ------------------------------------------------- m-bootloader (short) ---
mb = open_boot("2.4-395e6cc")
ok(F.bootloader_identity(mb) == ("fm1", "2.4-395e6cc"), "m-bootloader's identity block")
ok(not F.reads_config(mb) and F.reads_config(open_boot("1.3-abc")) and F.reads_config(STOCK_BOOT),
   "1.4 and newer do not read the configuration")
area = F.boot_area_for(stock, mb)
new = area + stock[F.BOOT_AREA:]
ok(len(area) == F.BOOT_AREA, "the new boot area is 16 KiB")
ok(new[F.BOOT_AREA:] == stock[F.BOOT_AREA:], "nothing outside the boot area changes")
ok(new[:32] == stock[:32], "the flash header is kept")
ok(F.stored_bootloader(new) == mb, "the new bootloader is stored")
ok(new[F.CONFIG_AT:F.CONFIG_AT + F.CONFIG_KEY] == stock[0x38D0:0x38D0 + F.CONFIG_KEY],
   "the unit's chip-key record is kept, at 0x3FA0")
ok(new[F.CONFIG_AT + F.CONFIG_KEY:F.BOOT_AREA] == F.CONFIG_SETTINGS, "with the settings older versions need")
ok(refused(F.check_boot_area, new) is None, "the new boot area can start the unit")
kind, text, version = F.bootloader_info(new)
ok(kind == "open" and version == "2.4-395e6cc" and "m-bootloader" in text, "and is told as m-bootloader")
ok(F.changed_sectors(stock, new) == [0, 0x1000, 0x2000, 0x3000], "only the four boot sectors differ")
ok(refused(F.boot_area_for, unit_flash(key_ok=False), mb) == "the unit's chip-key record is damaged",
   "refused over a damaged chip-key record")
ok("too large" in (refused(F.boot_area_for, stock, open_boot("2.4-x", size=0x3F80)) or ""),
   "refused when it does not fit")
# back to stock over it: the stock bootloader needs the full configuration, which is gone
ok(refused(F.check_boot_area, F.build_boot_area(new, STOCK_BOOT) + new[F.BOOT_AREA:])
   == "its boot configuration is incomplete for its bootloader",
   "stock over a cut-short configuration is caught (use the package)")

# --------------------------------------------- bootloaders that read it ---
old = open_boot("1.3-abc", size=0x3000)
a13 = F.boot_area_for(stock, old) + stock[F.BOOT_AREA:]
ok(refused(F.check_boot_area, a13) is None and a13[0x38D0:0x3BD0] == stock[0x38D0:0x3BD0],
   "1.3 keeps the whole configuration in place")
big = bankcb(random.Random(9).randbytes(0x3880))
moved = F.build_boot_area(stock, big) + stock[F.BOOT_AREA:]
e = F.boot_table(moved)[1]
ok(e.addr > 0x38D0 and moved[e.addr:e.addr + e.len] == stock[0x38D0:0x3BD0] and refused(F.check_boot_area, moved) is None,
   "a larger bootloader moves the configuration along")

# --------------------------------------------------------- sector map ---
m = F.sector_map(stock)
ok(m[:4] == [F.BOOT] * 4 and m[255] == F.MAC and len(m) == 256, "the map: boot sectors first, the MAC last")
ok(m[0x93] == F.SETTINGS and m[0xEA] == F.SOUNDS, "an unreadable app table shows the stock layout")

print("all flash tests passed" if not failed else f"{failed} FLASH TESTS FAILED")
sys.exit(1 if failed else 0)
