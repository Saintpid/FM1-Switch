#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""fm1_rescue: put stock firmware back on an FM-1 through the chip's own update mode (the
mask-ROM "UBOOT"), from a Mac or a Windows PC, with no extra hardware.

For an FM-1 whose firmware crashes at start-up. Wild falls back to its safe mode after two failed
starts and to UBOOT after four; X0X 0.2-beta crashes at once and enters UBOOT after two. UBOOT shows
up on USB as "WL80UBOOT1.00" (4C4A:8057), a disk. macOS then upsets it, and it reboots into the
crash a few seconds later; this script waits for it, takes it (hence sudo, or an administrator on
Windows) and keeps it. FM-1 Switch runs it from its Rescue button (--progress).

  python3 -m venv ~/fm1-rescue && ~/fm1-rescue/bin/pip install pyusb libusb-package
  sudo ~/fm1-rescue/bin/python fm1_rescue.py FM-1.fwsc            # check and back up, write nothing
  sudo ~/fm1-rescue/bin/python fm1_rescue.py FM-1.fwsc --write    # then write stock
  python fm1_rescue.py FM-1.fwsc --write                          # Windows, as administrator: no pyusb,
                                                                  # no driver change (SCSI pass-through)

FM-1.fwsc is M-VAVE's FM-1 V15 (m-vave.com/download, PC Firmware); only that exact file is
accepted (--download fetches it from M-VAVE and checks it). The protocol and the write policy are
FM-1-transporter's (github.com/kurogedelic/FM-1-transporter, docs/PROTOCOL.md), which were verified
on hardware:

  1. JieLi's flash loader (wl82loader.bin, from github.com/kagaimiq/jl-uboot-tool, downloaded
     and checked against its hash) is loaded into RAM and started;
  2. the chip key must be 980F and the flash a known FM-1 1 MiB part;
     UCUN writes require explicit experimental opt-in and direct USB;
  3. the whole flash is read and saved as a backup before anything else;
  4. only 4 KiB sectors in [0x4000, 0x93000) that differ from V15 are written (0xA9000 for M-VAVE's
     016; the boot area below 0x4000 and the device data after the image are never touched, and the
     FM-1's own boot area must be one that can start it), each erased, written and verified;
  5. a final full read must equal the expected image; then the FM-1 restarts into V15.

--protect is the one operation that writes the boot area: it puts the rescue bootloader (m-bootloader,
downloaded and checked) there, built over the FM-1's own boot area with its chip-key record kept, after
the same checks and backup, and changes nothing outside those 16 KiB:

  sudo ~/fm1-rescue/bin/python fm1_rescue.py --protect --write
"""
import argparse
import hashlib
import json
import os
import struct
import sys
import time
import tempfile
import threading
import urllib.request
import uuid
import zlib

import fm1_flash as FL
from fm1_flash import crc32

VID, PID = 0x4C4A, 0x8057
STOCK_SHA256 = "db1642b2b6fa5c2cccb11ffd13878068bb28601678d3644049f99dc40e7edb8a"      # V15
STOCK_016_SHA256 = "d017fb65e70aca97ee29d944470c3515a74581df5c0576bf70de917c7b2b61d5"  # 016 (M-Flasher's docs)
# M-VAVE's own FM-1 packages, by their SHA-256 (kind "v15" below stands for either)
STOCK_FM1 = {STOCK_SHA256: "V15", STOCK_016_SHA256: "016"}
# M-VAVE's download, which served V15 and now serves 016 (the newest): either is taken, checked
STOCK_URL = "https://yms-file-store.oss-cn-hongkong.aliyuncs.com/software/firmware/FM-1.fwsc"
STOCK_NAME = "FM-1-V15.fwsc"
STOCK_NAMES = {"V15": STOCK_NAME, "016": "FM-1-016.fwsc"}
LOADER_NAME = "wl82loader.bin"
LOADER_URL = ("https://raw.githubusercontent.com/kagaimiq/jl-uboot-tool/"
              "c32b28e7a04f873f526ba929cb1bae9f05442fe9/data/loaderblobs/usb/wl82loader.bin")
LOADER_SHA256 = "d41da6126760c9d66660bcc0cac8d27d221806c5e369a8036921efe68dca5376"
LOADER_ADDR, LOADER_ARG = 0x1C02000, 0x0001
# M-Flasher's flash loader (MFLR), from m-bootloader's releases (github.com/jvitkauskas/m-bootloader,
# MIT, (c) 2026 Julius Vitkauskas; see third_party/NOTICE.md), downloaded and checked like JieLi's. It
# adds the chip's own CRC32 of what was written, the flash's unique ID and text on the FM-1's screen.
# Built for two RAM slots, so that a new one never overwrites one that is running. Plain: scrambled
# for the ROM as it is uploaded. v2.3's build, the one M-Flasher ships and that ran on units.
MFLR_URL = "https://github.com/jvitkauskas/m-bootloader/releases/download/v2.3/"
MFLR = (("mflr-loader-a.bin", 0x01C20000, "ae0cdf4965cc2438a362e933ccf11d41159322d67f2dc630a61ab84cc58e04d7"),
        ("mflr-loader-b.bin", 0x01C28000, "6f9d97fcd3b16a74dd2b056ebca5e516c56a1c789a4b46b5afdd824d42fd684f"))
# m-bootloader for the FM-1 (same release family, MIT): the rescue bootloader that "Protect my FM-1"
# puts in the boot area. Hold REC at power-on: update mode; PLAY: its menu; a damaged firmware is not
# started. Downloaded and checked like the loaders, never part of this repository.
MBOOT_URL = "https://github.com/jvitkauskas/m-bootloader/releases/download/v2.4/fm1-uboot.boot"
MBOOT_NAME = "fm1-uboot.boot"
MBOOT_SHA256 = "677ae52a11721b1811b2688b45f6f0cfae51882810e0a4f4b409da08ce5b49e4"
MBOOT_VERSION = "2.4-395e6cc"
CMD_MFLR_IDENTIFY, CMD_MFLR_CRC32, CMD_MFLR_UNIQUE_ID = 0xFC40, 0xFC41, 0xFC42
CMD_MFLR_DISPLAY_ON, CMD_MFLR_DISPLAY_TEXT, CMD_MFLR_DISPLAY_BAR, CMD_MFLR_LAST_ERROR = 0xFC43, 0xFC44, 0xFC45, 0xFC46
CMD_USB_BUFFER_SIZE = 0xFC14
MFLR_OUT_OF_RANGE = 3                                        # FC46: an address outside the flash
SCREEN_WHITE, SCREEN_AMBER, SCREEN_MINT = 0xFFFF, 0xFDA0, 0x7F57

FLASH_SIZE = 0x100000
FM1_FLASH = {0x856014: "Puya P25Q80H", 0xB36014: "UCUN UC25HQ80"}
UCUN_FLASH = 0xB36014
# M-VAVE's SMK-37 (Pro, Elite, MKE-P37) has the FM-1's chip, key and UBOOT, but another flash map, its
# factory calibration at 0xC1000 (marker "trimdat" at 0xC1224) and, on the unit tried, flash EB6014
KEYBOARD_FLASH = 0xEB6014
# the FM-1's parts: Puya P25Q80H on the first units, UCUN UC25HQ80 on those sold since autumn 2026
# (1 MiB, the same commands; Felucca 7435517, fm1-base #35). An SMK-37 on either is still told by its calibration.
FLASH_IDS = tuple(FM1_FLASH)
TRIM_MARK, TRIM_AT = b"trimdat\0", 0xC1224
KEYBOARD = ("this is an M-VAVE SMK-37 keyboard (or an MKE-P37), not an FM-1: Rescue is for the FM-1 "
            "only, and an FM-1 firmware must never go on the keyboard; nothing written")
APP_END = 0x93000          # the .fwsc flash entry is raw flash [0, APP_END) (FM-1 015, Felucca-based)
APP_MAX = 0xA9000          # ... or longer, up to here: FM-1 016's image ends at 0xA9000
WRITE_MIN = 0x4000         # flash header, SPL, isd_config: never written by the rescue (only by protect())
SECTOR = 0x1000
IO = 512

# ROM UBOOT1.00
CMD_WRITE_MEMORY, CMD_READ_MEMORY, CMD_JUMP = 0xFB06, 0xFD07, 0xFB08
# loader
CMD_ERASE_SECTOR, CMD_WRITE_FLASH, CMD_READ_FLASH = 0xFB01, 0xFB04, 0xFD05
CMD_READ_KEY, CMD_GET_ONLINE_DEVICE, CMD_RUN_APP = 0xFC09, 0xFC0A, 0xFC0C


class Fail(Exception):
    pass


class Refused(Fail):
    """the device answered, and refused the command (a command the ROM alone does not know)"""


# ------------------------------------------------------------------ maths ---
def crc16(data, crc=0):
    """CRC-16/XMODEM (poly 0x1021, no reflection)"""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


MAGIC = bytes([0xC3, 0xCF, 0xC0, 0xE8, 0xCE, 0xD2, 0xB0, 0xAE,
               0xC4, 0xE3, 0xA3, 0xAC, 0xD3, 0xF1, 0xC1, 0xD6])


def crc_cipher(buf):
    """JieLi's CrcDecode cipher, key 0xFFFFFFFF (in place)"""
    crc = crc16(b"\xFF\xFF", 0xFFFF)
    for i in range(len(buf)):
        crc = crc16(MAGIC[i % 16:i % 16 + 1], crc)
        buf[i] ^= crc & 0xFF


def enc(buf, key=0xFFFF):
    """JieLi ENC stream cipher (in place)"""
    for i in range(len(buf)):
        buf[i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF


# ---------------------------------------------------------------- package ---
def package_flash_image(raw):
    """The .fwsc's type-0 entry: raw flash [0, APP_END). Layout as tools/fm1pkg_make.py writes it:
    the logical image is the .fwsc without the marker byte after each of its first 20 0x2F-byte
    blocks; it starts with a 0x40-byte header and a list of 0x50-byte entries, each ENC'd."""
    logical = bytearray()
    for i in range(20):
        logical += raw[i * 0x30:i * 0x30 + 0x2F]
    logical += raw[20 * 0x30:]
    hdr = bytearray(logical[0:0x40])
    enc(hdr)
    n = struct.unpack_from("<H", hdr, 8)[0]
    if hdr[16:22] != b"AC791N" or not 0 < n < 16:
        raise Fail("not an FM-1 package (header)")
    for i in range(n):
        e = bytearray(logical[0x40 + i * 0x50:0x40 + (i + 1) * 0x50])
        enc(e)
        typ, _, dcrc, _, off, size = struct.unpack_from("<HHHHII", e, 0)
        if typ == 0:
            data = bytes(logical[off:off + size])
            if len(data) != size or crc16(data) != dcrc:
                raise Fail("package flash entry: bad CRC")
            return data
    raise Fail("package has no flash entry")


# -------------------------------------------------------------- transport ---
class Bot:
    """USB mass storage bulk-only transport: one SCSI-style command per call."""

    def __init__(self, dev, usb):
        self.dev, self.usb, self.tag = dev, usb, 1
        cfg = dev.get_active_configuration() if self._configured() else None
        if cfg is None:
            dev.set_configuration()
            cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        self.intf = intf.bInterfaceNumber
        util = usb.util
        self.ep_out = util.find_descriptor(intf, custom_match=lambda e: util.endpoint_direction(
            e.bEndpointAddress) == util.ENDPOINT_OUT and util.endpoint_type(e.bmAttributes) == util.ENDPOINT_TYPE_BULK)
        self.ep_in = util.find_descriptor(intf, custom_match=lambda e: util.endpoint_direction(
            e.bEndpointAddress) == util.ENDPOINT_IN and util.endpoint_type(e.bmAttributes) == util.ENDPOINT_TYPE_BULK)
        if self.ep_out is None or self.ep_in is None:
            raise Fail("no bulk endpoints")
        util.claim_interface(dev, self.intf)

    def _configured(self):
        try:
            self.dev.get_active_configuration()
            return True
        except self.usb.core.USBError:
            return False

    def reset_recovery(self):
        """Bulk-only mass storage reset, then clear both halts (what a host does after a stall)."""
        try:
            self.dev.ctrl_transfer(0x21, 0xFF, 0, self.intf, None, timeout=2000)
        except self.usb.core.USBError:
            pass
        for ep in (self.ep_in, self.ep_out):
            try:
                self.dev.clear_halt(ep)
            except self.usb.core.USBError:
                pass

    def xfer(self, cdb, data_out=None, n_in=0, timeout=5000):
        tag = self.tag
        self.tag = (self.tag + 1) & 0xFFFFFFFF
        length = len(data_out) if data_out is not None else n_in
        flags = 0x80 if n_in else 0x00
        cbw = struct.pack("<4sIIBBB", b"USBC", tag, length, flags, 0, len(cdb)) + bytes(cdb).ljust(16, b"\0")
        self.ep_out.write(cbw, timeout)
        got = b""
        if data_out is not None and len(data_out):
            self.ep_out.write(bytes(data_out), timeout)
        elif n_in:
            got = bytes(self.ep_in.read(n_in, timeout))
        csw = bytes(self.ep_in.read(13, timeout))
        sig, ctag, _, status = struct.unpack("<4sIIB", csw)
        if sig != b"USBS" or ctag != tag:
            raise Fail(f"bad status block ({csw.hex()})")
        if status != 0:
            raise Refused(f"command failed (status {status})")
        return got


def cdb_for(cmd, args=b""):
    return (struct.pack(">H", cmd) + bytes(args)).ljust(16, b"\xFF")


# ----------------------------------------------------------------- device ---
class Uboot:
    """the chip's UBOOT and a flash loader in its RAM: JieLi's (wl82loader.bin, the one the
    FM-1-transporter path uses too) or M-Flasher's (MFLR: on-chip CRC32, unique ID, the FM-1's screen)"""

    def __init__(self, bot):
        self.bot = bot
        self.mflr = None                                     # the MFLR version running, if it is ours
        self.read_io, self.write_io = IO, IO
        self.screen_on = False
        self.app_end = APP_END                               # write_sector's upper bound (the package's end)
        self.boot_write = False                              # write_boot_sector: protect() only, once checked
        self.full_write = False                              # write_any_sector: restore() only, once checked

    def inquiry(self):
        r = self.bot.xfer(bytes([0x12, 0, 0, 0, 36, 0]), n_in=36)
        return r[8:16].decode(errors="replace").strip(), r[16:32].decode(errors="replace").strip()

    def cmd(self, cmd, args=b""):
        r = self.bot.xfer(cdb_for(cmd, args), n_in=16)
        if struct.unpack(">H", r[0:2])[0] != cmd:
            raise Refused(f"command {cmd:04X} answered as {r[0:2].hex()}")
        return r[2:]

    def write_block(self, cmd, addr, data):
        args = struct.pack(">IH", addr, len(data)) + b"\0" + struct.pack("<H", crc16(data))
        self.bot.xfer(cdb_for(cmd, args), data_out=data)

    def start_loader(self, blob):
        if isinstance(blob, MflrLoader):
            return self.start_mflr(blob)
        for off in range(0, len(blob), IO):                  # shipped already ciphered: send raw
            self.write_block(CMD_WRITE_MEMORY, LOADER_ADDR + off, blob[off:off + IO])
        self.cmd(CMD_JUMP, struct.pack(">IH", LOADER_ADDR, LOADER_ARG))

    # ---- M-Flasher's loader (the protocol of M-Flasher's src/jl.rs)
    def identify(self):
        """(version, RAM address) of an MFLR loader that answers, else None"""
        try:
            r = self.cmd(CMD_MFLR_IDENTIFY)
        except Refused:
            return None
        if r[:4] != b"MFLR":
            return None
        return r[4:7].rstrip(b"\0").decode(errors="replace"), struct.unpack(">I", r[8:12])[0]

    def start_mflr(self, loader):
        running = self.identify()
        if running is None:
            try:
                self.cmd(CMD_GET_ONLINE_DEVICE)
                other = True
            except Refused:
                other = False
            if other:                                        # JieLi's loader: jumping through it is not safe
                raise Fail("another flash loader is running on the FM-1: switch it off and on in update mode, "
                           "then try again; nothing written")
        if running and running[0] == loader.version:
            self.mflr = running[0]
        else:
            self._start_mflr_slot(loader, running[1] if running else None)
        if not self._mflr_has_flash():                       # it reads the flash's size once, at its start
            self._start_mflr_slot(loader, (self.identify() or (None, None))[1])
            if not self._mflr_has_flash():
                fid = struct.unpack_from("<I", self.cmd(CMD_GET_ONLINE_DEVICE), 2)[0]
                raise Fail(f"the flash chip did not identify itself to the loader (its ID now reads {fid:06X}), "
                           "so the flash cannot be read. Nothing was written: switch the FM-1 off and on and "
                           "connect it again in update mode")
        self.read_io = 16384
        try:
            n = struct.unpack(">I", self.cmd(CMD_USB_BUFFER_SIZE)[:4])[0]
            self.write_io = max(256, min(4096, n))
        except Refused:
            pass

    def _start_mflr_slot(self, loader, running_at):
        name, addr, image = loader.slot(running_at)
        for off in range(0, len(image), IO):                 # plain: scrambled for the ROM, block by block
            block = bytearray(image[off:off + IO])
            crc_cipher(block)
            self.write_block(CMD_WRITE_MEMORY, addr + off, bytes(block))
        for attempt in range(3):
            try:
                self.cmd(CMD_JUMP, struct.pack(">IH", addr, LOADER_ARG))
                time.sleep(0.1)
                break
            except Fail:
                time.sleep(0.25 * (attempt + 1))
                got = self.identify()
                if got and got[1] == addr:                   # it started, its answer was lost
                    break
        got = self.identify()
        if not got:
            raise Fail("the flash loader did not start: switch the FM-1 off and on in update mode, then try "
                       "again; nothing written")
        self.mflr = got[0]

    def _mflr_has_flash(self):
        """a loader that found no flash refuses every read as out of range (FC46 says so)"""
        try:
            self.cmd(CMD_MFLR_LAST_ERROR)
            try:
                self.bot.xfer(cdb_for(CMD_READ_FLASH, struct.pack(">IH", 0, 32)), n_in=32)
            except Fail:
                pass
            return self.cmd(CMD_MFLR_LAST_ERROR)[0] != MFLR_OUT_OF_RANGE
        except Refused:
            return True

    def crc32(self, addr, n):
        """CRC32 (IEEE) of n flash bytes at addr, computed on the chip; None without MFLR"""
        if not self.mflr:
            return None
        return struct.unpack(">I", self.cmd(CMD_MFLR_CRC32, struct.pack(">II", addr, n))[:4])[0]

    def unique_id(self):
        """the flash chip's factory-unique ID (hex), None if it has none or without MFLR"""
        if not self.mflr:
            return None
        try:
            r = self.cmd(CMD_MFLR_UNIQUE_ID)[:8]
        except Fail:
            return None
        return r.hex() if any(b != 0xFF for b in r) and any(r) else None

    def show(self, row=None, text="", color=SCREEN_WHITE, bar=None):
        """a line (or a progress bar) on the FM-1's own screen: best effort, never stops a write"""
        if not self.mflr:
            return
        try:
            if not self.screen_on:
                self.cmd(CMD_MFLR_DISPLAY_ON, b"\x01")
                self.screen_on = True
            if bar is not None:
                self.cmd(CMD_MFLR_DISPLAY_BAR, bytes([bar]) + struct.pack(">H", color))
            if row is not None:
                t = "".join(c if c.isalnum() or c in " -./:<>+?" else " " for c in text.upper())[:20].ljust(20)
                t = t.encode("ascii", "replace")
                self.bot.xfer(cdb_for(CMD_MFLR_DISPLAY_TEXT, bytes([row]) + struct.pack(">HHH", color, 0, len(t))),
                              data_out=t)
        except Exception:
            pass

    # ---- both loaders
    def chip(self):
        p = self.cmd(CMD_READ_KEY, struct.pack(">I", 0x00AC6900))
        k = bytearray([p[5], p[4]])
        crc_cipher(k)
        key = k[0] | k[1] << 8
        p = self.cmd(CMD_GET_ONLINE_DEVICE)
        return key, p[0], struct.unpack_from("<I", p, 2)[0]

    def read(self, addr, n):
        out = bytearray()
        while len(out) < n:
            k = min(self.read_io, n - len(out))
            got = self.bot.xfer(cdb_for(CMD_READ_FLASH, struct.pack(">IH", addr + len(out), k)), n_in=k)
            if len(got) != k:
                raise Fail(f"short read at {addr + len(out):#x}: {len(got)} of {k} bytes")
            out += got
        return bytes(out)

    def write_sector(self, addr, data):
        if addr < WRITE_MIN or addr >= min(self.app_end, APP_MAX) or addr % SECTOR or len(data) != SECTOR:
            raise Fail(f"refusing to write {addr:#x}")
        self._program(addr, data)

    def write_boot_sector(self, addr, data):
        """one of the boot area's four sectors: only after protect() has checked the area it belongs to"""
        if not self.boot_write or addr >= WRITE_MIN or addr % SECTOR or len(data) != SECTOR:
            raise Fail(f"refusing to write {addr:#x}")
        self._program(addr, data)

    def write_any_sector(self, addr, data):
        """any sector but the chip's own key and MAC: only while restore() writes a checked backup of this
        same FM-1"""
        if (not self.full_write or addr >= FL.KEY_MAC[0] or addr >= FLASH_SIZE or addr % SECTOR
                or len(data) != SECTOR):
            raise Fail(f"refusing to write {addr:#x}")
        self._program(addr, data)

    def _program(self, addr, data):
        self.cmd(CMD_ERASE_SECTOR, struct.pack(">I", addr))
        for off in range(0, SECTOR, self.write_io):
            chunk = data[off:off + self.write_io]
            if self.mflr and chunk == b"\xFF" * len(chunk):
                continue                                     # erased already
            self.write_block(CMD_WRITE_FLASH, addr + off, chunk)
        if self.mflr:
            ok = self.crc32(addr, SECTOR) == crc32(data)
        else:
            ok = self.read(addr, SECTOR) == data
        if not ok:
            raise Fail(f"sector {addr:#07x} did not verify")

    def run_app(self):
        try:
            self.cmd(CMD_RUN_APP, struct.pack(">I", 1))
        except Exception:
            pass                                             # it may reset before it answers


class MflrLoader:
    """M-Flasher's loader in its two builds: (name, RAM address, plain image) each"""

    def __init__(self, slots):
        self.slots = slots
        a = slots[0][2]
        at = a.find(b"MFLR")
        self.version = a[at + 4:at + 7].decode(errors="replace") if at >= 0 else "?"

    def slot(self, running_at):
        """the build for the slot the running loader (at running_at) does not use"""
        a, b = self.slots
        return b if running_at is not None and running_at != b[1] else a


# ---------------------------------------------------------------- windows ---
# Windows gives UBOOT to its own USB disk driver. The commands reach it through that driver's SCSI
# pass-through (as JieLi's own tools do): an administrator, but no driver to swap (no Zadig).
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x560000
FSCTL_LOCK_VOLUME, FSCTL_DISMOUNT_VOLUME = 0x90018, 0x90020
DATA_OUT, DATA_IN = 0, 1


def _sptd_types():
    import ctypes as C

    class SPTD(C.Structure):                                 # SCSI_PASS_THROUGH_DIRECT (ULONG is 32 bits)
        _fields_ = [("Length", C.c_uint16), ("ScsiStatus", C.c_uint8), ("PathId", C.c_uint8),
                    ("TargetId", C.c_uint8), ("Lun", C.c_uint8), ("CdbLength", C.c_uint8),
                    ("SenseInfoLength", C.c_uint8), ("DataIn", C.c_uint8),
                    ("DataTransferLength", C.c_uint32), ("TimeOutValue", C.c_uint32),
                    ("DataBuffer", C.c_void_p), ("SenseInfoOffset", C.c_uint32), ("Cdb", C.c_uint8 * 16)]

    class SPTDS(C.Structure):                                # ... with its sense buffer after it
        _fields_ = [("sptd", SPTD), ("fill", C.c_uint32), ("sense", C.c_uint8 * 32)]
    return SPTD, SPTDS


def disk_ids(desc):
    """(vendor, product) from a STORAGE_DEVICE_DESCRIPTOR"""
    def at(off):
        if not 0 < off < len(desc):
            return ""
        return desc[off:desc.index(b"\0", off) if b"\0" in desc[off:] else len(desc)].decode(
            "latin-1").strip()
    vo, po = struct.unpack_from("<II", desc, 12)
    return at(vo), at(po)


def is_uboot_disk(vendor, product):
    return vendor.upper() == "WL82" or "UBOOT" in product.upper()


class Win32:
    """the few kernel32 calls, typed (64-bit handles)"""

    def __init__(self):
        import ctypes as C
        from ctypes import wintypes as W
        self.C = C
        k = C.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        k.CreateFileW.restype = W.HANDLE
        k.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.c_void_p, W.DWORD,
                                      C.POINTER(W.DWORD), C.c_void_p]
        k.DeviceIoControl.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]
        self.k, self.W = k, W

    def open(self, path, rw):
        h = self.k.CreateFileW(path, 0xC0000000 if rw else 0, 3, None, 3, 0, None)   # share r/w, existing
        return None if h in (None, self.C.c_void_p(-1).value) else h

    def ioctl(self, h, code, inbuf, n_in, outbuf, n_out):
        got = self.W.DWORD(0)
        if not self.k.DeviceIoControl(h, code, inbuf, n_in, outbuf, n_out, self.C.byref(got), None):
            raise Fail(f"Windows refused the command (error {self.C.get_last_error()})")
        return got.value

    def close(self, h):
        self.k.CloseHandle(h)


def windows_uboot_disks(win=None):
    """\\\\.\\PhysicalDriveN paths that are the FM-1's UBOOT (asking needs no administrator)"""
    win = win or Win32()
    C, out = win.C, []
    for n in range(32):
        path = rf"\\.\PhysicalDrive{n}"
        h = win.open(path, False)
        if h is None:
            continue
        try:
            q = C.create_string_buffer(struct.pack("<II4x", 0, 0), 12)   # StorageDeviceProperty, standard
            d = C.create_string_buffer(1024)
            try:
                got = win.ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, C.addressof(q), 12, C.addressof(d), 1024)
            except Fail:
                continue
            if got >= 32 and is_uboot_disk(*disk_ids(d.raw[:got])):
                out.append(path)
        finally:
            win.close(h)
    return out


def volumes_on(disk, win):
    """[(letter, handle)] of the drive letters Windows mounted on PhysicalDrive<disk> (the UBOOT disk
    may get one, G: say): each handle open, for locking"""
    C, out = win.C, []
    for c in "DEFGHIJKLMNOPQRSTUVWXYZ":
        h = win.open(rf"\\.\{c}:", True)
        if h is None:
            continue
        keep = False
        try:
            buf = C.create_string_buffer(8 + 24 * 8)          # VOLUME_DISK_EXTENTS, up to 8 extents
            try:
                win.ioctl(h, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, None, 0, C.addressof(buf), len(buf))
            except Fail:
                continue
            n = min(8, struct.unpack_from("<I", buf.raw, 0)[0])
            if disk in [struct.unpack_from("<I", buf.raw, 8 + 24 * i)[0] for i in range(n)]:
                out.append((c, h))
                keep = True
        finally:
            if not keep:
                win.close(h)
    return out


class WinScsi:
    """the same xfer() as Bot, through IOCTL_SCSI_PASS_THROUGH_DIRECT on the UBOOT disk. A drive
    letter Windows mounted on it is locked and dismounted first (while one is mounted, Windows refuses
    the chip's commands with error 5), and stays locked until close()"""

    def __init__(self, path, win=None, say=None):
        self.win = win or Win32()
        self.path = path
        self.locked = []
        try:
            disk = int(path.rsplit("PhysicalDrive", 1)[1])
        except (IndexError, ValueError):
            disk = None
        if disk is not None:
            for c, h in volumes_on(disk, self.win):
                for code in (FSCTL_LOCK_VOLUME, FSCTL_DISMOUNT_VOLUME):
                    try:
                        self.win.ioctl(h, code, None, 0, None, 0)
                    except Fail as e:
                        if say:
                            say(f"  (drive {c}: could not be {'locked' if code == FSCTL_LOCK_VOLUME else 'dismounted'}: {e})")
                self.locked.append(h)
                if say:
                    say(f"  drive {c}: (the FM-1's update mode) taken from Windows until this ends")
        self.h = self.win.open(path, True)
        if self.h is None:
            self.close()
            raise Fail(f"cannot open {path} (run as administrator)")

    def reset_recovery(self):
        pass                                                 # the disk driver does its own

    def xfer(self, cdb, data_out=None, n_in=0, timeout=5000):
        C = self.win.C
        SPTD, SPTDS = _sptd_types()
        n = len(data_out) if data_out is not None else n_in
        buf = C.create_string_buffer(max(n, 1))
        if data_out is not None:
            C.memmove(buf, bytes(data_out), n)
        s = SPTDS()
        s.sptd.Length = C.sizeof(SPTD)
        s.sptd.CdbLength = len(cdb)
        s.sptd.SenseInfoLength = len(s.sense)
        s.sptd.DataIn = DATA_IN if n_in else DATA_OUT
        s.sptd.DataTransferLength = n
        s.sptd.TimeOutValue = max(1, (timeout + 999) // 1000)
        s.sptd.DataBuffer = C.addressof(buf)
        s.sptd.SenseInfoOffset = SPTDS.sense.offset
        C.memmove(s.sptd.Cdb, bytes(cdb), len(cdb))
        self.win.ioctl(self.h, IOCTL_SCSI_PASS_THROUGH_DIRECT, C.addressof(s), C.sizeof(s),
                       C.addressof(s), C.sizeof(s))
        if s.sptd.ScsiStatus != 0:
            raise Refused(f"command failed (status {s.sptd.ScsiStatus})")
        return buf.raw[:n] if n_in else b""

    def close(self):
        if getattr(self, "h", None) is not None:
            self.win.close(self.h)
            self.h = None
        for h in getattr(self, "locked", []):
            self.win.close(h)                                # (closing unlocks; Windows mounts it again)
        self.locked = []


# ------------------------------------------------------------ transporter ---
# EXPERIMENTAL (not tried on hardware here): for an FM-1 that shows nothing at all on USB. An
# FM-1-transporter (github.com/kurogedelic/FM-1-transporter, MIT) is an RP2040 board wired to the
# FM-1's USB lines (D+, D-, GND; never VBUS). It sends the USB_KEY that wakes the chip's update mode
# at power-on, talks to UBOOT itself and gives the computer a serial port with a line protocol
# (its tools/fm1t.py): ping/PONG, status, uboot (V15's soft key), rekey, ldr, info, read, wsec,
# runapp. Its firmware refuses any sector outside [0x4000, 0x93000) as well. No administrator needed.
TRANSPORTER_VID = 0x2E8A                                     # Raspberry Pi: only those ports are asked
LOADER_SIZE = 24064


def transporter_ports():
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports() if p.vid == TRANSPORTER_VID]


def open_transporter_port(path):
    """the port if it is the transporter's data port (it answers ping with PONG), else None"""
    import serial
    s = serial.Serial(path, 115200, timeout=0.3)
    s.reset_input_buffer()
    s.write(b"ping\n")
    end, buf = time.time() + 1.0, b""
    while time.time() < end:
        buf += s.read(64)
        if b"PONG" in buf:
            s.timeout = 5
            return s
    s.close()
    return None


def find_transporter(ports=None, opener=None):
    for p in (transporter_ports() if ports is None else ports):
        try:
            s = (opener or open_transporter_port)(p)
        except Exception:
            continue
        if s is not None:
            return s
    return None


class Transporter:
    """rescue()'s device, through the transporter: the same calls as Uboot"""

    def __init__(self, port, reopen=None):
        self.s, self.reopen = port, reopen

    def line(self, timeout):
        self.s.timeout = timeout
        return self.s.readline().decode(errors="replace").strip()

    def request(self, text, timeout=30):
        self.s.reset_input_buffer()
        self.s.write(text.encode() + b"\n")
        r = self.line(timeout)
        if not r:
            raise Fail(f"the adapter did not answer {text.split()[0]!r}")
        return r

    def status(self):
        r = self.request("status")
        if not r.startswith("OK"):
            raise Fail(f"adapter: {r}")
        return dict(kv.split("=", 1) for kv in r.split()[1:] if "=" in kv)

    def ensure_uboot(self, wait, say=print, stop=None):
        """the FM-1 in UBOOT: there already, V15's soft key, or USB_KEY at the next power-on"""
        st = self.status()
        if st.get("uboot") == "1":
            return
        if st.get("v15") == "1":
            say("  M-VAVE's V15 runs: the adapter sends it to its update mode")
            r = self.request("uboot")
            if not r.startswith("OK"):
                raise Fail(f"adapter: {r}")
        else:
            say("  the adapter restarts to wake the chip at power-on: switch the FM-1 OFF now, then ON")
            try:
                self.request("rekey", 5)
            except Fail:
                pass                                         # it may restart before it answers
            try:
                self.s.close()
            except Exception:
                pass
            self.s = None
        end = time.time() + wait
        while time.time() < end:
            if stop and stop():
                raise Stopped("stopped: nothing was written")
            if self.s is None:
                self.s = self.reopen() if self.reopen else None
                if self.s is None:
                    time.sleep(0.5)
                    continue
            try:
                if self.status().get("uboot") == "1":
                    say("  the FM-1 is in its update mode")
                    return
            except Fail:
                pass
            time.sleep(0.5)
        raise Fail("the FM-1 did not appear in update mode through the adapter (wired right? switched "
                   "off and on after the adapter restarted?)")

    def inquiry(self):
        return "WL82", "through FM-1-transporter"

    def start_loader(self, blob):
        if self.status().get("loader") == "1":
            return
        if len(blob) != LOADER_SIZE:
            raise Fail(f"wl82loader.bin is {len(blob)} bytes, the adapter takes {LOADER_SIZE}")
        for off in range(0, len(blob), 4096):
            chunk = blob[off:off + 4096]
            self.s.reset_input_buffer()
            self.s.write(f"ldr {off} {len(chunk)} {zlib.crc32(chunk) & 0xFFFFFFFF:08X}\n".encode() + chunk)
            r = self.line(5)
            if r != "OK":
                raise Fail(f"adapter: loader upload failed at {off}: {r or 'no reply'}")

    def chip(self):
        r = self.request("info")
        if not r.startswith("OK"):
            raise Fail(f"adapter: {r}")
        f = dict(kv.split("=", 1) for kv in r.split()[1:] if "=" in kv)
        try:
            return int(f["key"], 16), int(f["type"]), int(f["id"], 16)
        except (KeyError, ValueError):
            raise Fail(f"adapter: {r}")

    def read(self, addr, n):
        r = self.request(f"read {addr:#x} {n}")
        if r != f"DATA {n}":
            raise Fail(f"adapter: {r}")
        data = bytearray()
        self.s.timeout = 10
        while len(data) < n:
            chunk = self.s.read(min(65536, n - len(data)))
            if not chunk:
                raise Fail(f"adapter: the read stalled at {len(data):#x}")
            data += chunk
        end = self.line(10)
        if end != f"END {zlib.crc32(data) & 0xFFFFFFFF:08X}":
            raise Fail(f"adapter: the read did not check ({end})")
        return bytes(data)

    def write_sector(self, addr, data):
        if addr < WRITE_MIN or addr >= APP_END or addr % SECTOR or len(data) != SECTOR:
            raise Fail(f"refusing to write {addr:#x}")
        self.s.reset_input_buffer()
        self.s.write(f"wsec {addr:#x} {zlib.crc32(data) & 0xFFFFFFFF:08X}\n".encode() + bytes(data))
        r = self.line(20)                                    # erased, written and read back there
        if r != "OK":
            raise Fail(f"sector {addr:#07x}: {r or 'no reply'}")

    def run_app(self):
        try:
            self.request("runapp", 5)
        except Fail:
            pass

    def cmd(self, cmd, args=b""):                            # keep_alive(): the adapter keeps UBOOT busy
        self.status()
        return b""

    def close(self):
        try:
            if self.s is not None:
                self.s.close()
        except Exception:
            pass


# ------------------------------------------------------------------- flow ---
class Stopped(Fail):
    pass


def flash_info(key, typ, fid):
    """Identification only: a known part does not establish firmware compatibility."""
    return {"chip_key": f"{key:04X}", "flash_type": typ, "jedec_id": f"{fid:06X}",
            "flash_name": FM1_FLASH.get(fid, "M-VAVE keyboard flash" if fid == KEYBOARD_FLASH else "Unknown"),
            "known_fm1_part": key == 0x980F and typ == 3 and fid in FM1_FLASH,
            "capacity_bytes": FLASH_SIZE if fid in FM1_FLASH or fid == KEYBOARD_FLASH else None,
            "experimental": fid == UCUN_FLASH, "firmware_compatibility": "unknown"}


def save_report(path, data, owner=None):
    with open(path, "x", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    give_owner(path, owner)


def give_owner(path, owner):
    if owner:
        try:
            os.chown(path, *owner)
        except (OSError, AttributeError):
            pass


def inspect_backup(dev, loader, backup_dir, say=print, step=None, owner=None, image=None, kind=None):
    """Explicit UBOOT operation: RAM loader, identification, two reads, saved-file verification.
    No flash erase/program or run-app command. Unknown chips get an identification report only."""
    step = step or (lambda *a: None)
    vendor, product = dev.inquiry()
    say(f"  device: {vendor} {product}")
    if vendor != "WL82":
        raise Fail(f"not a WL82 UBOOT ({vendor!r})")
    dev.start_loader(loader)
    key, typ, fid = dev.chip()
    info = flash_info(key, typ, fid)
    say(f"  chip key {key:04X}, flash type {typ}, id {fid:06X}: {info['flash_name']}")
    if isinstance(dev, Uboot) and dev.mflr:
        say(f"  flash loader: M-Flasher's {dev.mflr}")
        uid = dev.unique_id()
        if uid:
            info["unique_id"] = uid                          # tells this FM-1 from another (its backups)
    report = os.path.join(backup_dir, f"fm1-hardware-{uuid.uuid4().hex}.json")
    save_report(report, {"schema": 1, "hardware": info,
                        "transport": "transporter" if isinstance(dev, Transporter) else "usb",
                        "loader_sha256": loader_sha256(loader),
                        "purpose": "identification; no flash written by this probe"}, owner)
    step("hardware", {**info, "report": report})
    say(f"  hardware report: {report}")
    if fid == KEYBOARD_FLASH:
        raise Fail(KEYBOARD)
    if not info["known_fm1_part"]:
        raise Fail("this is not the FM-1's known chip and flash (expected key 980F, type 3, "
                   f"id 856014 or B36014; found {key:04X}/{typ}/{fid:06X}); nothing written")
    if image is not None and not app_key_ok(image, key):
        raise Fail("this firmware is not for this chip (its app area does not decrypt with key "
                   f"{key:04X}); nothing written")
    if image is not None:
        support, why = flash_support(image, fid, kind, key)
        info["firmware_compatibility"] = support
        step("compat", {"support": support, "reason": why})
        if support == "unsupported":
            raise Fail(f"this firmware does not support this FM-1's flash, {info['flash_name']} ({fid:06X}): "
                       f"{why}. Choose a version that supports it (Wild from FM-1_864, NB303 from "
                       "FM-1_908, or M-VAVE's V15); nothing read or written")
        if support == "unknown" and fid != 0x856014:
            say(f"  warning: {why}; whether it keeps its saves on this flash is not known")
    say("  reading the whole flash ...")
    t0 = time.time()
    step("backup_read", 1)
    now = dev.read(0, FLASH_SIZE)
    step("backup_read", 2)
    again = dev.read(0, FLASH_SIZE)
    if len(now) != FLASH_SIZE or now != again:
        raise Fail("backup reads differ or are incomplete; nothing written")
    chip_crc = dev.crc32(0, FLASH_SIZE) if isinstance(dev, Uboot) else None
    if chip_crc is not None and chip_crc != crc32(now):
        raise Fail("the reads do not match the chip's own checksum of the flash; nothing written")
    found = FL.analyse(now, key)
    info.update({k: found[k] for k in ("bootloader", "bootloader_text", "bootloader_version", "firmware",
                                       "firmware_hub", "firmware_status", "layout", "boot_area_ok")})
    say(f"  bootloader: {found['bootloader_text']}; firmware: {found['firmware']} "
        f"({found['firmware_status_text']})")
    if found["firmware_hub"]:
        say(f"  ({FL.FW.CATALOG_CREDIT})")
    step("analysis", {**found, "map": FL.sector_map(now, key)})
    fd, path = tempfile.mkstemp(prefix=time.strftime("fm1-backup-%Y%m%d-%H%M%S-"),
                                suffix=".bin", dir=backup_dir)
    with os.fdopen(fd, "wb") as f:
        f.write(now)
        f.flush()
        os.fsync(f.fileno())
    with open(path, "rb") as f:
        if f.read() != now:
            raise Fail("saved backup did not verify; nothing written")
    with open(path + ".sha256", "x", encoding="ascii") as f:
        f.write(sha256(now) + "  " + os.path.basename(path) + "\n")
    for p in (path, path + ".sha256"):
        give_owner(p, owner)
    save_report(path + ".json", {"schema": 1, "hardware": info, "hardware_report": report,
                               "backup_file": os.path.basename(path), "backup_bytes": len(now),
                               "backup_sha256": sha256(now), "matching_full_reads": 2,
                               "chip_crc32_checked": chip_crc is not None,
                               "saved_file_verified": True}, owner)
    say(f"  backup saved: {path} ({time.time() - t0:.1f} s)")
    step("backup", path)
    if now[TRIM_AT:TRIM_AT + len(TRIM_MARK)] == TRIM_MARK or found["keyboard"]:  # the same flash part as an FM-1's
        raise Fail(f"{KEYBOARD} (the backup is kept)")
    return now, info, path


def rescue(dev, image, loader, write, backup_dir, say=print, confirm=None, step=None, owner=None,
           strict_head=True, allow_ucun=False, kind=None):
    """Back up before writing only application sectors. UCUN writes are experimental and opt-in.
    The FM-1's own boot area always stays (as an update leaves it, M-VAVE's V15 and 016 included: they
    run under m-bootloader and under 016's bootloader), and must be one that can start it. strict_head
    is kept for callers; it no longer changes anything, as the package's boot area is never written."""
    step = step or (lambda *a: None)
    now, info, _ = inspect_backup(dev, loader, backup_dir, say, step, owner, image, kind)
    if info["experimental"] and (write or confirm is not None):
        if isinstance(dev, Transporter):
            raise Fail("UCUN recovery through the adapter is not supported; use direct USB. "
                       "The backup is kept; nothing written")
        if not allow_ucun:
            raise Fail("UCUN UC25HQ80 detected. Backup verified; nothing written. Recovery on this chip "
                       "is experimental: review the backup, then explicitly allow UCUN recovery "
                       "(--allow-ucun-write in the CLI). Use firmware with UCUN support.")
        say("  EXPERIMENTAL UCUN recovery: flash verification does not prove persistent saves.")
    end = len(image)
    if not APP_END <= end <= APP_MAX or end % SECTOR:
        raise Fail(f"the firmware image ends at {end:#x}, outside the firmware area; nothing written")
    try:
        FL.check_boot_area(now)
    except FL.FormatError as e:
        raise Fail(f"the FM-1's own boot area cannot start it ({e}): writing firmware would not help, and "
                   "the boot area is not written here. Nothing written; keep the backup and ask for help")
    if now[:WRITE_MIN] != image[:WRITE_MIN]:
        say(f"  the FM-1's boot area stays ({info.get('bootloader_text', 'its own')}), as an update leaves it")
    old_layout = FL.layout(now)
    new_layout = FL.layout(image + now[end:])
    if old_layout and new_layout and old_layout != new_layout:
        say(f"  note: the firmware now on the FM-1 keeps its data in another layout ({old_layout}) than this "
            f"one ({new_layout}): what it leaves behind may confuse the new firmware (wrong presets, no "
            "sound) until it is reset, or a backup from before is restored")
        step("layout", {"from": old_layout, "to": new_layout})
    diff = [a for a in range(WRITE_MIN, end, SECTOR) if image[a:a + SECTOR] != now[a:a + SECTOR]]
    say(f"  {len(diff)} of {(end - WRITE_MIN) // SECTOR} firmware sectors differ from the package")
    if not write and confirm is not None:
        write = keep_alive(dev, confirm)
    if not write:
        say("  check passed. Nothing written: run again with --write to put it back.")
        return False
    if isinstance(dev, Uboot):
        dev.app_end = end
        dev.show(3, " INSTALL FIRMWARE")
        dev.show(5, " WRITING")
        dev.show(12, " DO NOT SWITCH OFF", SCREEN_AMBER)
    # high to low: X0X's start-up code (low in the app area: its watchdog and its crash counter,
    # which bring it back to update mode) is the last thing replaced if the write is cut short
    for i, a in enumerate(reversed(diff)):
        for attempt in range(3):
            try:
                dev.write_sector(a, image[a:a + SECTOR])
                break
            except Fail as e:
                if attempt == 2:
                    raise Fail(f"{e}; the FM-1 is still in update mode: run the rescue again")
        say(f"\r  writing {i + 1}/{len(diff)}", end="")
        step("write", (i + 1) * 100 // len(diff))
        if isinstance(dev, Uboot):
            dev.show(bar=(i + 1) * 100 // len(diff), color=SCREEN_MINT)
    if not diff:
        say("  nothing to write: the flash already holds this firmware")
        step("write", 100)
    say("")
    say("  final read ...")
    expect = now[:WRITE_MIN] + image[WRITE_MIN:end] + now[end:]
    final = dev.read(0, FLASH_SIZE)
    if final != expect:
        bad = [a for a in range(0, FLASH_SIZE, SECTOR) if final[a:a + SECTOR] != expect[a:a + SECTOR]]
        raise Fail("final read differs in sectors " + " ".join(f"{a:#07x}" for a in bad)
                   + "; run the rescue again")
    if isinstance(dev, Uboot):
        dev.show(5, " DONE - VERIFIED", SCREEN_MINT)
        dev.show(12, " RESTARTING")
    say("  the flash now holds the firmware. Restarting the FM-1 ...")
    dev.run_app()
    step("done", None)
    return True


def protect(dev, boot, loader, write, backup_dir, say=print, confirm=None, step=None, owner=None,
            allow_ucun=False):
    """Protect my FM-1: m-bootloader in the boot area. A full verified backup first; the new boot area is
    built over the FM-1's own (its chip-key record kept), must pass the bootable check, and nothing outside
    the 16 KiB boot area may change. -> "installed", "already" or False (checked, nothing written)"""
    step = step or (lambda *a: None)
    ident = FL.bootloader_identity(boot)
    if ident is None or ident[0] != "fm1":
        raise Fail("this is not m-bootloader's build for the FM-1; nothing written")
    version = ident[1]
    if not isinstance(dev, Uboot):
        raise Fail("the rescue bootloader is installed over direct USB only; nothing written")
    now, info, _ = inspect_backup(dev, loader, backup_dir, say, step, owner)
    kind, text, have = FL.bootloader_info(now)
    if kind == "open" and have == version and FL.stored_bootloader(now) == boot:
        say(f"  the FM-1 already has the rescue bootloader ({text}); nothing to write. Restarting it ...")
        dev.run_app()
        step("already", version)
        return "already"
    try:
        area = FL.boot_area_for(now, boot)
        new = area + now[WRITE_MIN:]
        FL.check_boot_area(new)
    except FL.FormatError as e:
        raise Fail(f"the new boot area would not work ({e}); nothing written")
    if new[WRITE_MIN:] != now[WRITE_MIN:] or len(new) != FLASH_SIZE:
        raise Fail("the new boot area would change more than the boot area; nothing written")
    diff = [a for a in range(0, WRITE_MIN, SECTOR) if new[a:a + SECTOR] != now[a:a + SECTOR]]
    say(f"  rescue bootloader {version} over {text}: {len(diff)} of 4 boot sectors change; "
        "the firmware and its data stay")
    if info["firmware_status"] != FL.APP_OK:
        say("  note: the firmware on the FM-1 is not whole: the rescue bootloader will not start it, it "
            "waits for a new one (hold REC at power-on for update mode)")
    step("plan", {"from": text, "to": version, "sectors": diff,
                  "map": FL.sector_map(new), "firmware_ok": info["firmware_status"] == FL.APP_OK})
    if not write and confirm is not None:
        write = keep_alive(dev, confirm)
        allow_ucun = allow_ucun or write == "ucun"
    if not write:
        say("  check passed. Nothing written: run again with --write to install it.")
        return False
    if info["experimental"] and not allow_ucun:
        raise Fail("UCUN UC25HQ80 detected. Backup verified; nothing written. Writing on this chip is "
                   "experimental: allow it explicitly (--allow-ucun-write in the CLI).")
    dev.show(3, " PROTECT FM-1")
    dev.show(5, " WRITING BOOT AREA")
    dev.show(12, " DO NOT SWITCH OFF", SCREEN_AMBER)
    dev.boot_write = True
    try:
        # the sector with the flash header and the file table last: until then the old table is whole
        for i, a in enumerate(sorted(diff, reverse=True)):
            for attempt in range(3):
                try:
                    dev.write_boot_sector(a, new[a:a + SECTOR])
                    break
                except Fail as e:
                    if attempt == 2:
                        raise Fail(f"{e}; the FM-1 is still in update mode: keep it connected and restore "
                                   "the backup (or run this again)")
            step("write", (i + 1) * 100 // len(diff))
            dev.show(bar=(i + 1) * 100 // len(diff), color=SCREEN_MINT)
    finally:
        dev.boot_write = False
    say("  final read ...")
    final = dev.read(0, FLASH_SIZE)
    if final != new or dev.crc32(0, FLASH_SIZE) not in (None, crc32(new)):
        bad = [a for a in range(0, FLASH_SIZE, SECTOR) if final[a:a + SECTOR] != new[a:a + SECTOR]]
        raise Fail("final read differs in sectors " + " ".join(f"{a:#07x}" for a in bad)
                   + "; the FM-1 is still in update mode: restore the backup")
    dev.show(5, " DONE - VERIFIED", SCREEN_MINT)
    dev.show(12, " RESTARTING")
    say(f"  the rescue bootloader {version} is installed and verified. Restarting the FM-1 ...")
    dev.run_app()
    step("done", version)
    return "installed"


def read_backup(path):
    """a backup file FM-1 Switch saved, checked: (flash, its description or {}). Its .sha256 or the
    .json beside it must match: a file changed since, or not saved here, is refused"""
    try:
        data = open(path, "rb").read()
    except OSError as e:
        raise Fail(f"cannot read the backup ({e})")
    if len(data) != FLASH_SIZE:
        raise Fail(f"{os.path.basename(path)} is not a whole FM-1 backup ({len(data)} bytes, not {FLASH_SIZE})")
    sums, meta = [], {}
    try:
        sums.append(open(path + ".sha256", encoding="ascii").read().split()[0].lower())
    except (OSError, IndexError):
        pass
    try:
        meta = json.loads(open(path + ".json", encoding="utf-8").read())
        if meta.get("backup_sha256"):
            sums.append(str(meta["backup_sha256"]).lower())
    except (OSError, ValueError, AttributeError):
        meta = {}
    if not sums:
        raise Fail("the backup has no checksum beside it (.sha256 or .json): it is not one this program "
                   "saved, so it is not written back")
    if any(x != sha256(data) for x in sums):
        raise Fail("the backup does not match its checksum: it has changed since it was saved; nothing written")
    return data, meta if isinstance(meta, dict) else {}


def restore(dev, backup, loader, write, backup_dir, say=print, confirm=None, step=None, owner=None,
            allow_ucun=False, meta=None, boot_only=False):
    """Put this FM-1's own backup back, boot area included (boot_only: the boot area alone, for M-VAVE's
    bootloader from a backup made before Protect). A fresh full verified backup first; the backup must be
    this same FM-1's (its key and MAC, and its unique ID when both have one), must start it
    (check_boot_area) and must not be a keyboard's. Firmware and data sectors first, high to low; the
    boot sectors last, header last. -> "restored", "already" or False (checked, nothing written)"""
    step = step or (lambda *a: None)
    meta = meta or {}
    if not isinstance(dev, Uboot):
        raise Fail("a backup is put back over direct USB only; nothing written")
    if len(backup) != FLASH_SIZE:
        raise Fail("not a whole FM-1 backup; nothing written")
    now, info, _ = inspect_backup(dev, loader, backup_dir, say, step, owner)
    lo, hi = FL.KEY_MAC
    if backup[lo:hi] != now[lo:hi]:
        raise Fail("this backup is another FM-1's (its key and MAC area differ): an FM-1 only takes its own "
                   "backup back; nothing written")
    theirs = (meta.get("hardware") or {}).get("unique_id")
    if theirs and info.get("unique_id") and theirs != info["unique_id"]:
        raise Fail("this backup is another FM-1's (its flash's unique ID differs); nothing written")
    if FL.is_keyboard(backup):
        raise Fail(KEYBOARD)
    try:
        FL.check_boot_area(backup)
    except FL.FormatError as e:
        raise Fail(f"the backup's boot area could not start the FM-1 ({e}); nothing written")
    new = backup[:WRITE_MIN] + now[WRITE_MIN:] if boot_only else backup
    if boot_only and FL.bootloader_info(new)[0] != "stock":
        raise Fail("this backup does not hold M-VAVE's own bootloader; nothing written")
    diff = [a for a in range(0, FLASH_SIZE, SECTOR) if new[a:a + SECTOR] != now[a:a + SECTOR]]
    if any(a >= lo for a in diff):
        raise Fail("the key and MAC area would change; nothing written")
    _, was, _ = FL.bootloader_info(now)
    _, to, _ = FL.bootloader_info(new)
    boot = [a for a in diff if a < WRITE_MIN]
    say(f"  {len(diff)} of {FLASH_SIZE // SECTOR} sectors differ from the backup ({len(boot)} in the boot area); "
        f"bootloader {was} -> {to}")
    if not diff:
        say("  the FM-1 already holds this backup; nothing to write. Restarting it ...")
        dev.run_app()
        step("already", None)
        return "already"
    step("plan", {"from": was, "to": to, "sectors": diff, "map": FL.sector_map(new),
                  "firmware": FL.firmware_info(new), "boot": bool(boot), "boot_only": boot_only})
    if not write and confirm is not None:
        write = keep_alive(dev, confirm)
        allow_ucun = allow_ucun or write == "ucun"
    if not write:
        say("  check passed. Nothing written: run again with --write to put it back.")
        return False
    if info["experimental"] and not allow_ucun:
        raise Fail("UCUN UC25HQ80 detected. Backup verified; nothing written. Writing on this chip is "
                   "experimental: allow it explicitly (--allow-ucun-write in the CLI).")
    dev.show(3, " RESTORE BACKUP")
    dev.show(5, " WRITING")
    dev.show(12, " DO NOT SWITCH OFF", SCREEN_AMBER)
    order = sorted((a for a in diff if a >= WRITE_MIN), reverse=True) + sorted(boot, reverse=True)
    dev.full_write = dev.boot_write = True
    try:
        for i, a in enumerate(order):
            for attempt in range(3):
                try:
                    (dev.write_boot_sector if a < WRITE_MIN else dev.write_any_sector)(a, new[a:a + SECTOR])
                    break
                except Fail as e:
                    if attempt == 2:
                        raise Fail(f"{e}; the FM-1 is still in update mode: keep it connected and put the "
                                   "backup back again")
            step("write", (i + 1) * 100 // len(order))
            dev.show(bar=(i + 1) * 100 // len(order), color=SCREEN_MINT)
    finally:
        dev.full_write = dev.boot_write = False
    say("  final read ...")
    final = dev.read(0, FLASH_SIZE)
    if final != new or dev.crc32(0, FLASH_SIZE) not in (None, crc32(new)):
        bad = [a for a in range(0, FLASH_SIZE, SECTOR) if final[a:a + SECTOR] != new[a:a + SECTOR]]
        raise Fail("final read differs in sectors " + " ".join(f"{a:#07x}" for a in bad)
                   + "; the FM-1 is still in update mode: put the backup back again")
    dev.show(5, " DONE - VERIFIED", SCREEN_MINT)
    dev.show(12, " RESTARTING")
    say("  the backup is back and verified. Restarting the FM-1 ...")
    dev.run_app()
    step("done", None)
    return "restored"


def keep_alive(dev, confirm):
    """Ask while the loader is kept busy (it resets the chip after ~3 s without a command)."""
    import threading
    stop, lost = threading.Event(), []

    def run():
        while not stop.wait(1.0):
            try:
                dev.cmd(CMD_GET_ONLINE_DEVICE)
            except Exception as e:
                lost.append(e)
                return
    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        answer = confirm()
    finally:
        stop.set()
        t.join()
    if lost:
        raise Fail(f"the FM-1 dropped out while waiting ({lost[0]}); run it again")
    return answer


# ------------------------------------------------------------------ files ---
def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _trust():
    """TLS contexts to try, in order: Python's own (taking a partial chain), the system's verifier (truststore: on Windows it
    fetches an intermediate certificate the local store has not seen yet, as browsers do), certifi's
    list. Every download is checked by its SHA-256 as well."""
    import ssl
    ctx = ssl.create_default_context()
    # a chain that ends at any trusted certificate, not only a self-signed root (Python 3.13+ does this by
    # default): Windows may offer a cross-signed copy of a trusted root whose own root it lacks
    ctx.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    yield ctx
    try:
        import truststore
        yield truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi
        yield ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass


def _cert_error(e):
    import ssl
    return isinstance(e, ssl.SSLCertVerificationError) or isinstance(getattr(e, "reason", None),
                                                                     ssl.SSLCertVerificationError)


def fetch(url, timeout=60, contexts=None):
    err = None
    for ctx in (contexts if contexts is not None else _trust()):
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                return r.read()
        except Exception as e:
            if not _cert_error(e):
                raise
            err = e
    raise err


def get_loader(path=None, cache_dir=None, say=print):
    """wl82loader.bin: a given file, a checked copy in cache_dir, or downloaded (and kept there)"""
    cached = os.path.join(cache_dir, LOADER_NAME) if cache_dir else None
    if path:
        blob = open(path, "rb").read()
    elif cached and os.path.isfile(cached) and sha256(open(cached, "rb").read()) == LOADER_SHA256:
        return open(cached, "rb").read()
    else:
        say("downloading JieLi's flash loader (wl82loader.bin) ...")
        blob = fetch(LOADER_URL, 30)
    if sha256(blob) != LOADER_SHA256:
        raise Fail("wl82loader.bin: unexpected contents")
    if cached and not path:
        try:
            with open(cached, "wb") as f:
                f.write(blob)
        except OSError:
            pass
    return blob


def get_mflr(cache_dir=None, say=print, folder=None):
    """M-Flasher's loader (both builds): from folder, a checked copy in cache_dir, or downloaded from
    m-bootloader's release (and kept there); each checked against its SHA-256"""
    slots = []
    for name, addr, want in MFLR:
        blob = None
        for d in (folder, cache_dir):
            p = os.path.join(d, name) if d else None
            if p and os.path.isfile(p):
                b = open(p, "rb").read()
                if sha256(b) == want:
                    blob = b
                    break
                if d == folder:
                    raise Fail(f"{name}: unexpected contents")
        if blob is None:
            say(f"downloading M-Flasher's flash loader ({name}) ...")
            blob = fetch(MFLR_URL + name, 30)
            if sha256(blob) != want:
                raise Fail(f"{name}: unexpected contents")
            if cache_dir:
                try:
                    with open(os.path.join(cache_dir, name), "wb") as f:
                        f.write(blob)
                except OSError:
                    pass
        slots.append((name, addr, blob))
    return MflrLoader(slots)


def get_mbootloader(cache_dir=None, say=print):
    """m-bootloader's FM-1 build (v2.4): a checked copy in cache_dir, or downloaded (and kept there)"""
    p = os.path.join(cache_dir, MBOOT_NAME) if cache_dir else None
    if p and os.path.isfile(p):
        b = open(p, "rb").read()
        if sha256(b) == MBOOT_SHA256:
            return b
    say(f"downloading the rescue bootloader (m-bootloader {MBOOT_VERSION}) ...")
    b = fetch(MBOOT_URL, 30)
    if sha256(b) != MBOOT_SHA256:
        raise Fail(f"{MBOOT_NAME}: unexpected contents")
    if p:
        try:
            with open(p, "wb") as f:
                f.write(b)
        except OSError:
            pass
    return b


def loader_sha256(loader):
    return ",".join(sha256(b) for _, _, b in loader.slots) if isinstance(loader, MflrLoader) else sha256(loader)


def find_stock(folders):
    """M-VAVE's V15 or 016 among the .fwsc files in the folders (by SHA-256, whatever its name; V15 first)"""
    found = {}
    for d in folders:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if n.lower().endswith(".fwsc") and os.path.isfile(p) and os.path.getsize(p) < (4 << 20):
                try:
                    v = STOCK_FM1.get(sha256(open(p, "rb").read()))
                    if v:
                        found.setdefault(v, p)
                except OSError:
                    pass
    return found.get("V15") or found.get("016")


def download_stock(dest_dir, say=print):
    """M-VAVE's FM-1 firmware from M-VAVE's own server, checked (V15 or 016), as dest_dir/FM-1-V15.fwsc
    or FM-1-016.fwsc"""
    say("downloading M-VAVE's stock firmware for the FM-1 ...")
    raw = fetch(STOCK_URL)
    v = STOCK_FM1.get(sha256(raw))
    if not v:
        raise Fail("the downloaded file is not an M-VAVE FM-1 firmware this version knows (neither V15 nor 016: "
                   "sha256 mismatch); nothing was done")
    say(f"  it is M-VAVE's FM-1 {v}")
    path = os.path.join(dest_dir, STOCK_NAMES[v])
    with open(path, "wb") as f:
        f.write(raw)
    return path


def load_stock(path):
    raw = open(path, "rb").read()
    if sha256(raw) not in STOCK_FM1:
        raise Fail(f"{path} is not M-VAVE's FM-1 V15 or 016 package (sha256 mismatch)")
    image = package_flash_image(raw)
    if not APP_END <= len(image) <= APP_MAX:
        raise Fail("unexpected flash entry size")
    return image


def app_key_ok(image, key):
    """the package's app area decrypts with this chip's key (the first 32 bytes, app_area_head,
    carry their own CRC): the update loader's check, so a package for another key is refused"""
    head = bytearray(image[WRITE_MIN:WRITE_MIN + 32])
    if len(head) != 32:
        return False
    enc(head, key ^ 0)                                       # SFC: ENC with key ^ (offset >> 2), offset 0
    return crc16(bytes(head[2:32])) == struct.unpack_from("<H", head, 0)[0]


def app_area(image, key=0x980F):
    """the package's app area [WRITE_MIN, APP_END) decrypted as the chip's SFC reads it: each 32-byte
    block ENC'd with key ^ (its offset >> 2)"""
    out = bytearray(image[WRITE_MIN:APP_END])
    for b in range(0, len(out), 32):
        blk = out[b:b + 32]
        enc(blk, key ^ (b >> 2))
        out[b:b + 32] = blk
    return bytes(out)


# Felucca-based firmware (Wild, NB303, n001se ...) compares the measured JEDEC ID with constants in its
# app (main_fm1.c: fl_jedec_ram() == 0x856014u): firmware without the UCUN constant keeps its sequences
# in RAM only on a UCUN unit and its update loader may refuse it (Melodee #110, SLOOP #238). Measured on
# the published packages: Wild from FM-1_864, NB303 from FM-1_908 carry B36014 as well.
STOCK_FLASH_SUPPORT = {0x856014: "supported", 0xB36014: "supported"}   # V15: upstream reports save on UCUN


def flash_support(image, fid, kind=None, key=0x980F):
    """Can this firmware use flash part fid? -> ("supported" | "unsupported" | "unknown", reason).
    supported: M-VAVE's V15, or the app carries fid's 32-bit constant. unsupported: a Felucca-based
    app that carries another FM-1 part's constant but not fid's (its exact-ID gate refuses this part).
    unknown: anything else (a gate can be coded without a plain constant)."""
    if kind == "v15":
        return STOCK_FLASH_SUPPORT.get(fid, "unknown"), "M-VAVE's V15"
    app = app_area(image, key)
    has = {f: f.to_bytes(4, "little") in app for f in FM1_FLASH}
    name = FM1_FLASH.get(fid, f"{fid:06X}")
    if has.get(fid):
        return "supported", f"the firmware accepts {name} ({fid:06X})"
    others = [f"{f:06X}" for f, h in has.items() if h and f != fid]
    if others and kind in (None, "felucca"):
        return "unsupported", (f"the firmware accepts only {', '.join(others)}, not {name} ({fid:06X}): "
                               "on this flash it does not keep its saves and its update may be refused")
    return "unknown", f"no flash ID found in the firmware for {name} ({fid:06X})"


def product_of(raw):
    """the package identity ("FM-1_906"): one marker byte after each of the first 20 blocks"""
    return "".join(chr((m - i - 1) & 0xFF) for i in range(20)
                   if i * 0x30 + 0x2F < len(raw) and (m := raw[i * 0x30 + 0x2F]) != 0x7D)


def load_package(path):
    """-> (image of flash [0, end), kind, identity) for any FM-1 package; end is the package's own
    (0x93000, or up to 0xA9000 for FM-1 016). kind: "v15" (M-VAVE's own, V15 or 016, by its SHA-256),
    "felucca" (a package with Felucca's update loader: Wild, NB303, n001se, FiMba-1...) or "other"
    (another FM-1 firmware)"""
    raw = open(path, "rb").read()
    product = product_of(raw)
    if sha256(raw) in STOCK_FM1:
        kind = "v15"
        product = product or f"FM-1_{STOCK_FM1[sha256(raw)].lstrip('V').zfill(3)}"
    else:
        if product.split("_")[0] != "FM-1":
            raise Fail(f"{path} is not an FM-1 package (identity {product!r})")
        kind = "felucca" if b"FELUCCA-LOADER-1" in raw else "other"
    image = package_flash_image(raw)
    if len(image) < APP_END:
        raise Fail(f"{path}: its flash image is shorter than the firmware area")
    if kind != "v15":
        image = image[:APP_END]                              # as before: the firmware area of 015's layout
    if len(image) > APP_MAX or len(image) % SECTOR:
        raise Fail(f"{path}: its flash image ({len(image):#x} bytes) reaches past the firmware area")
    return image, kind, product or "V15"


# ----------------------------------------------------------------- device ---
def backend(usb):
    """libusb: pip's libusb-package (no Homebrew needed), else the system's or Homebrew's"""
    try:
        import libusb_package
        b = libusb_package.get_libusb1_backend()
        if b is not None:
            return b
    except ImportError:
        pass
    import usb.backend.libusb1 as lb
    for path in (None, "/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib"):
        b = lb.get_backend() if path is None else (lb.get_backend(find_library=lambda _: path)
                                                   if os.path.exists(path) else None)
        if b is not None:
            return b
    raise Fail("libusb not found: pip install libusb-package")


def uboot_present():
    """is an FM-1 in UBOOT on USB right now? (no administrator needed; None: cannot tell)"""
    try:
        if sys.platform == "win32":
            return bool(windows_uboot_disks())
        import usb.core
        return usb.core.find(idVendor=VID, idProduct=PID, backend=backend(usb)) is not None
    except Exception:
        return None


def is_admin():
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def open_device(timeout, say=print, stop=None):
    """Wait for UBOOT and take it: on Windows its disk, elsewhere 4C4A:8057 from the OS's mass
    storage driver. stop(): True gives up (FM-1 Switch's Stop)."""
    say("waiting for the FM-1 in update mode (switch it on, or wait for it to cycle) ...")
    win = sys.platform == "win32"
    if not win:
        import usb.core
        import usb.util
        be = backend(usb)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if stop and stop():
            raise Stopped("stopped: nothing was written")
        if win:
            disks = windows_uboot_disks()
            if len(disks) > 1:
                raise Fail("multiple UBOOT devices found: connect only the target device; nothing written")
            if disks:
                say(f"found it after {time.time() - t0:.1f} s ({disks[0]})")
                try:
                    t = WinScsi(disks[0], say=say)
                    try:
                        Uboot(t).inquiry()
                    except Fail:
                        pass
                    return t
                except Fail as e:
                    say(f"  could not open it ({e}); waiting for the next cycle")
                    time.sleep(1.0)
                    continue
            time.sleep(0.05)
            continue
        devices = list(usb.core.find(idVendor=VID, idProduct=PID, backend=be, find_all=True) or [])
        if len(devices) > 1:
            raise Fail("multiple UBOOT devices found: connect only the target device; nothing written")
        if devices:
            dev = devices[0]
            say(f"found it after {time.time() - t0:.1f} s")
            try:
                try:
                    if dev.is_kernel_driver_active(0):
                        dev.detach_kernel_driver(0)
                except (NotImplementedError, usb.core.USBError) as e:
                    if getattr(e, "errno", None) not in (None, 2):
                        say(f"  (could not detach the system's driver: {e})")
                bot = Bot(dev, usb)
                try:
                    Uboot(bot).inquiry()
                except Exception:
                    bot.reset_recovery()
                return bot
            except Exception as e:
                say(f"  could not open it ({e}); waiting for the next cycle")
                time.sleep(1.0)
                continue
        time.sleep(0.02)
    raise Fail("the FM-1 did not appear in update mode")


def release(t):
    try:
        if isinstance(t, (WinScsi, Transporter)):
            t.close()
        else:
            import usb.util
            usb.util.dispose_resources(t.dev)
    except Exception:
        pass


def ask(what="put the stock firmware back"):
    print("\n  Everything checks out, and the backup is saved.")
    try:
        return input(f"  Type yes and press Return to {what}: ").strip().lower() == "yes"
    except EOFError:
        return False


class ProgressFile:
    """FM-1 Switch's view of a rescue run elsewhere (as root or administrator): one JSON object a
    line in a file the window reads; the window asks it to stop by creating FILE.stop"""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "a", encoding="utf-8")

    def put(self, **kw):
        import json
        self.f.write(json.dumps(kw) + "\n")
        self.f.flush()

    def say(self, *a, end="\n", **_):
        text = " ".join(str(x) for x in a).strip("\r\n ")
        if text and not text.startswith("writing"):
            self.put(say=text)

    def step(self, kind, value=None):
        self.put(step=kind, value=value)

    def stopped(self):
        return os.path.exists(self.path + ".stop")

    def answer(self, poll=0.2, stop=None):
        """the window's answer to the question the run waits on: FILE.go holds "go" or "go ucun"
        (write; the second also allows the UCUN flash), FILE.stop: no. -> True, "ucun" or False"""
        stop = stop or threading.Event()
        while not stop.is_set():
            if self.stopped():
                return False
            try:
                with open(self.path + ".go", encoding="ascii") as f:
                    words = f.read().split()
            except OSError:
                words = []
            if words[:1] == ["go"]:
                return "ucun" if "ucun" in words[1:] else True
            time.sleep(poll)
        return False


def answer(path, ucun=False):
    """the window says yes to a run waiting on FILE.go (see ProgressFile.answer)"""
    with open(path + ".go", "w", encoding="ascii") as f:
        f.write("go ucun\n" if ucun else "go\n")


def read_progress(path, pos=0):
    """-> (new events, new position); for the window"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            f.seek(pos)
            data = f.read()
    except OSError:
        return [], pos
    end = data.rfind("\n") + 1
    out = []
    for line in data[:end].splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out, pos + len(data[:end].encode("utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", nargs="?", help="the firmware to put back: M-VAVE's FM-1 V15 .fwsc "
                                               "(any other FM-1 package with --any-firmware)")
    ap.add_argument("--write", action="store_true", help="write it (without it: check and back up only)")
    ap.add_argument("--ask", action="store_true", help="after the check and the backup, ask before writing")
    ap.add_argument("--inspect", action="store_true",
                    help="identify the flash and save a verified backup without a firmware package; no flash writes")
    ap.add_argument("--protect", action="store_true",
                    help="install the rescue bootloader (m-bootloader " + MBOOT_VERSION + ", downloaded and "
                         "checked) in the boot area: the FM-1's own chip-key record kept, nothing else "
                         "changed; with --write or --ask (without: check and back up only)")
    ap.add_argument("--restore", metavar="BACKUP",
                    help="put this FM-1's own backup (a .bin this program saved, with its .sha256 or .json) "
                         "back, boot area included; with --write or --ask (without: check and back up only)")
    ap.add_argument("--boot-only", action="store_true",
                    help="with --restore: only the boot area, to put M-VAVE's own bootloader back from a "
                         "backup made before Protect my FM-1")
    ap.add_argument("--allow-ucun-write", action="store_true",
                    help="explicitly allow EXPERIMENTAL UCUN UC25HQ80 recovery over direct USB")
    ap.add_argument("--download", action="store_true", help="fetch V15 from M-VAVE (checked) if no package is given")
    ap.add_argument("--any-firmware", action="store_true",
                    help="allow another FM-1 package than V15 (Wild, NB303, n001se, FiMba-1, another "
                         "M-VAVE version...): its app area is written, the FM-1's own boot head stays")
    ap.add_argument("--via", choices=("usb", "transporter"), default="usb",
                    help="usb: the chip's update mode on this computer's USB (default); transporter: "
                         "EXPERIMENTAL, through an FM-1-transporter (RP2040) adapter, for an FM-1 that "
                         "shows nothing on USB (needs pyserial, no administrator)")
    ap.add_argument("--loader", help="a local wl82loader.bin instead of downloading it (with --loader-kind jieli)")
    ap.add_argument("--loader-kind", choices=("mflr", "jieli"), default="mflr",
                    help="mflr: M-Flasher's flash loader, checks writes with the chip's CRC32 (default over "
                         "USB); jieli: JieLi's wl82loader.bin, as before 1.5.0 (always with --via transporter)")
    ap.add_argument("--mflr-dir", help="a folder holding mflr-loader-a.bin and -b.bin instead of downloading them")
    ap.add_argument("--backup-dir", help="where the backup goes (default: beside the package)")
    ap.add_argument("--wait", type=float, default=120, help="seconds to wait for the FM-1 (default 120)")
    ap.add_argument("--tries", type=int, default=5, help="attempts if it drops out mid-way (default 5)")
    ap.add_argument("--progress", help=argparse.SUPPRESS)          # FM-1 Switch: events to this file
    ap.add_argument("--owner", help=argparse.SUPPRESS)             # uid:gid to give the backup to
    args = ap.parse_args(argv)
    if args.inspect and (args.write or args.ask or args.package or args.download or args.allow_ucun_write):
        ap.error("--inspect cannot be combined with a package, --download, --write, --ask or --allow-ucun-write")
    if args.protect and (args.inspect or args.package or args.download or args.any_firmware or args.via != "usb"):
        ap.error("--protect cannot be combined with a package, --download, --any-firmware, --inspect or --via")
    if args.restore and (args.protect or args.inspect or args.package or args.download or args.any_firmware
                         or args.via != "usb"):
        ap.error("--restore cannot be combined with a package, --download, --any-firmware, --protect, "
                 "--inspect or --via")
    if args.boot_only and not args.restore:
        ap.error("--boot-only goes with --restore")
    pf = ProgressFile(args.progress) if args.progress else None
    say = pf.say if pf else (lambda *a, **k: print(*a, **k, flush=True))
    try:
        pkg = args.package
        image, kind, product = None, None, None
        boot, saved, meta = None, None, None
        if args.restore:
            saved, meta = read_backup(args.restore)
            say(f"  backup: {args.restore} (checked)")
        elif args.protect:
            boot = get_mbootloader(args.backup_dir or os.getcwd(), say)
        elif not pkg and not args.inspect:
            if not args.download:
                ap.error("give the V15 package, or --download")
            pkg = download_stock(os.getcwd(), say)
        if not args.inspect and not args.protect and not args.restore:
            image, kind, product = load_package(pkg)
            if kind != "v15" and not args.any_firmware:
                raise Fail(f"{pkg} is {product}, not M-VAVE's V15: --any-firmware puts another FM-1 firmware back")
            say(f"  firmware: {product} ({'M-VAVE V15' if kind == 'v15' else kind})")
        if kind == "other":
            say("  (not V15 and not Felucca-based: if it does not start, it may not fall back to the update "
                "mode by itself)")
        backup_dir = args.backup_dir or (os.getcwd() if args.inspect or args.protect
                                         else os.path.dirname(os.path.abspath(args.restore)) if args.restore
                                         else os.path.dirname(os.path.abspath(pkg)))
        via = args.via
        if via == "usb" and args.loader_kind == "mflr" and not args.loader:
            loader = get_mflr(backup_dir, say, args.mflr_dir)
        else:
            loader = get_loader(args.loader, backup_dir, say)
        if via == "transporter":
            try:
                import serial  # noqa: F401
            except ImportError:
                raise Fail("pyserial is missing: pip install pyserial")
        else:
            if not is_admin():
                raise Fail("run it as administrator (Windows)" if sys.platform == "win32"
                           else "run it with sudo: the system has to give the device up")
            if sys.platform != "win32":
                try:
                    import usb.core  # noqa: F401
                except ImportError:
                    raise Fail("pyusb is missing: ~/fm1-rescue/bin/pip install pyusb libusb-package")
        owner = tuple(int(x) for x in args.owner.split(":")) if args.owner else None
        if pf:
            pf.step("ready", None)
        stop = pf.stopped if pf else None
        for attempt in range(1, args.tries + 1):
            if via == "transporter":
                port = find_transporter()
                if port is None:
                    raise Fail("no FM-1-transporter found (an RP2040 board with its firmware, on USB)")
                dev = Transporter(port, reopen=find_transporter)
                t = dev
            else:
                t = open_device(args.wait, say, stop)
                dev = Uboot(t)
            try:
                if via == "transporter":
                    dev.ensure_uboot(args.wait, say, stop)
                details = {}
                if args.restore:
                    confirm = None
                    if args.ask:
                        confirm = pf.answer if pf else (lambda: ask("put the backup back"))
                    result = restore(dev, saved, loader, args.write, backup_dir, say=say, confirm=confirm,
                                     step=pf.step if pf else None, owner=owner,
                                     allow_ucun=args.allow_ucun_write, meta=meta, boot_only=args.boot_only)
                    details = {"result": result or "checked"}
                elif args.protect:
                    confirm = None
                    if args.ask:
                        confirm = pf.answer if pf else (lambda: ask("install the rescue bootloader"))
                    result = protect(dev, boot, loader, args.write, backup_dir, say=say,
                                     confirm=confirm, step=pf.step if pf else None,
                                     owner=owner, allow_ucun=args.allow_ucun_write)
                    details = {"result": result or "checked", "version": MBOOT_VERSION}
                elif args.inspect:
                    _, info, path = inspect_backup(dev, loader, backup_dir, say=say,
                                                  step=pf.step if pf else None, owner=owner)
                    details = {"hardware": info, "backup": path}
                    say("  Backup verified. No flash written. Power-cycle before using the FM-1 again.")
                else:
                    rescue(dev, image, loader, args.write, backup_dir, say=say,
                           confirm=ask if args.ask else None, step=pf.step if pf else None, owner=owner,
                           strict_head=kind == "v15", allow_ucun=args.allow_ucun_write, kind=kind)
                if pf:
                    pf.put(end="ok", product=product, operation="restore" if args.restore else
                           "protect" if args.protect else
                           "inspect" if args.inspect else "rescue", **details)
                return 0
            except Stopped:
                raise
            except Exception as e:                           # (a USB error: it dropped out)
                say(f"  attempt {attempt}: {e}")
                if attempt < args.tries:
                    time.sleep(1.5)                          # (Windows may still be setting the disk up)
                if isinstance(e, Fail) and ("not writing" in str(e) or "not the FM-1" in str(e)
                                            or "nothing written" in str(e) or "did not appear" in str(e)):
                    raise
            finally:
                release(t)
        raise Fail("gave up after several attempts; send this whole output")
    except Fail as e:
        if pf:
            pf.put(end="fail", error=str(e))
        print(f"fm1_rescue: {e}", file=sys.stderr)
        return 1
    except Exception as e:                                   # (no network, a file that will not open)
        if pf:
            pf.put(end="fail", error=f"{type(e).__name__}: {e}")
        print(f"fm1_rescue: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
