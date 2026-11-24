# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic FM-1 flash parts for the tests: a boot area as a unit holds it (flash header, file table,
a bootloader container, isd_config with the chip-key record) and m-bootloader-like builds."""
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fm1_flash as F  # noqa: E402


def entry(name, addr, length, data_crc=0, attr=0):
    e = bytearray(32)
    struct.pack_into("<HIIB", e, 2, data_crc, addr, length, attr)
    e[16:16 + len(name)] = name.encode()
    struct.pack_into("<H", e, 0, F.crc16(bytes(e[2:])))
    return bytes(e)


def bankcb(payload):
    """a uboot.boot container: bank 1, the payload after the 16-byte header"""
    h = bytearray(16)
    struct.pack_into("<HHII", h, 0, 1, len(payload), 0, 16)
    struct.pack_into("<H", h, 12, F.crc16(payload))
    struct.pack_into("<H", h, 14, F.crc16(bytes(h[:14])))
    return bytes(h) + payload


def open_boot(version, size=0x3000, board="fm1"):
    """an m-bootloader build: its identity block at 0x40 of the payload"""
    p = bytearray(random.Random(version).randbytes(size))
    p[0x40:0x48] = b"AC79BLID"
    p[0x48:0x50] = board.encode().ljust(8, b"\0")
    p[0x50:0x70] = version.encode().ljust(32, b"\0")
    return bankcb(bytes(p))


STOCK_BOOT = bankcb(random.Random(7).randbytes(0x3800))


def unit_flash(boot=STOCK_BOOT, isd_at=0x38D0, isd_len=0x300, key_ok=True):
    """a whole flash as a unit holds it: header, table, bootloader, isd_config with the chip-key
    record, and some data after the boot area"""
    f = bytearray(random.Random(11).randbytes(F.FLASH_SIZE))
    f[:F.BOOT_AREA] = b"\xFF" * F.BOOT_AREA
    cfg = bytearray(random.Random(5).randbytes(isd_len))
    struct.pack_into("<H", cfg, 32, F.crc16(bytes(cfg[:32])) ^ (0 if key_ok else 1))
    hdr = bytearray(32)
    hdr[4:8] = b"JLFH"
    struct.pack_into("<H", hdr, 0, F.crc16(bytes(hdr[2:])))
    f[0:32] = F.enc(hdr)
    for i, e in enumerate((entry("uboot.boot", F.BOOT_AT, len(boot), F.crc16(boot)),
                           entry("isd_config.ini", isd_at, isd_len, F.crc16(bytes(cfg))),
                           entry("app_dir_head", F.BOOT_AREA, 0x8F000),
                           entry("key_mac", 0xFF000, 0x1000))):
        f[0x20 + 32 * i:0x40 + 32 * i] = F.enc(e)
    f[F.BOOT_AT:F.BOOT_AT + len(boot)] = F.enc(boot[:16]) + F.enc(boot[16:])
    f[isd_at:isd_at + isd_len] = cfg
    return bytes(f)


_AREAS = {}


def boot_area(boot=STOCK_BOOT):
    """just the 16 KiB boot area of unit_flash()"""
    if boot not in _AREAS:
        _AREAS[boot] = unit_flash(boot)[:F.BOOT_AREA]
    return _AREAS[boot]


def with_app(flash, text, key=F.CHIP_KEY):
    """flash with an app area holding one app.bin of these plain bytes (its table, ENC with the chip key)"""
    f = bytearray(flash)
    body = bytes(text) + b"\0" * (-len(text) % 32)
    plain = entry("app.bin", 0x200, len(text), F.crc16(bytes(text)), 0x82).ljust(0x200, b"\0") + body
    for a in range(0, len(plain), 32):
        f[F.BOOT_AREA + a:F.BOOT_AREA + a + 32] = F.enc(plain[a:a + 32], key ^ ((a >> 2) & 0xFFFF))
    return bytes(f)
