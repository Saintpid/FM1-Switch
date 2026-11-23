#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Parts ported from M-Flasher (github.com/jvitkauskas/m-flasher, src/formats.rs, src/boards.rs and
# src/sectormap.rs), MIT License, (c) 2026 Julius Vitkauskas: see third_party/NOTICE.md.
"""fm1_flash: what a whole FM-1 flash image holds, without touching a device.

The FM-1's flash (1 MiB, the JieLi AC791N's SPI NOR):

  0x00000  boot area, 16 KiB: flash header (32 bytes), file table (32-byte entries from 0x20),
           the bootloader (uboot.boot) at 0xA0, isd_config.ini after it (0x38D0; 0x3938 in FM-1
           016). The header, the table and the bootloader are ENC with key 0xFFFF.
  0x04000  app area: its own file table (app.bin, cfg_tool.bin and the region descriptors VM,
           BTIF, USR, ...), ENC per 32-byte block with the chip key ^ (offset in the area >> 2)
  ...      the regions the app's table declares
  0xFF000  key_mac: the Bluetooth address and the flash's efuse page

All CRCs are CRC16/XMODEM, except the loader's on-chip check (CRC32, as zlib).

Read-only here: building a new boot area returns bytes, and nothing in this module writes flash.
"""
import struct
import zlib

FLASH_SIZE = 0x100000
SECTOR = 0x1000
BOOT_AREA = 0x4000
BOOT_AT = 0xA0
CHIP_KEY = 0x980F
KEY_MAC = (0xFF000, 0x100000)

# what m-bootloader 1.4 and newer keep of the boot configuration, which they do not read: the
# chip-key record firmware installers need, then the settings 1.3 and older require
CONFIG_AT = 0x3FA0
CONFIG_KEY = 34
CONFIG_SIZE = BOOT_AREA - CONFIG_AT
CONFIG_SETTINGS = (b"\x04OSC\0OSC0"
                   b"\x04OSC_FREQ\0\x00\x36\x6E\x01"
                   b"\x04SYS_CLK\0\x00\x2A\x75\x15"
                   b"\x04HSB_DIV\0\x01\0\0\0"
                   b"\x04LSB_DIV\0\x02\0\0\0")
assert len(CONFIG_SETTINGS) == CONFIG_SIZE - CONFIG_KEY


class FormatError(Exception):
    pass


# ------------------------------------------------------------------ maths ---
def crc16(data, crc=0):
    """CRC-16/XMODEM (poly 0x1021, no reflection)"""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc32(data):
    """CRC32 (IEEE), the check M-Flasher's loader computes on the chip"""
    return zlib.crc32(bytes(data)) & 0xFFFFFFFF


def enc(data, key=0xFFFF):
    """JieLi's ENC stream cipher, its own inverse -> new bytes"""
    out = bytearray(data)
    for i in range(len(out)):
        out[i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF
    return bytes(out)


def _u16(b, at):
    return struct.unpack_from("<H", b, at)[0]


def _u32(b, at):
    return struct.unpack_from("<I", b, at)[0]


def _cstr(b):
    b = bytes(b)
    return b.split(b"\0", 1)[0].decode("latin-1")


# ------------------------------------------------------------ file tables ---
class Entry:
    """a 32-byte file table entry: head CRC, data CRC, address, length, attribute, index, name"""

    def __init__(self, raw):
        self.raw = bytes(raw[:32])
        self.data_crc = _u16(self.raw, 2)
        self.addr = _u32(self.raw, 4)
        self.len = _u32(self.raw, 8)
        self.attr = self.raw[12]
        self.name = _cstr(self.raw[16:32])
        self.valid = crc16(self.raw[2:]) == _u16(self.raw, 0)

    def __repr__(self):
        return f"Entry({self.name!r}, {self.addr:#x}, {self.len:#x}, attr {self.attr:#x})"


def _entry_bytes(raw, **fields):
    """raw with some of its fields replaced and its head CRC renewed (plain, not ENC)"""
    e = bytearray(raw)
    for name, (at, fmt) in {"data_crc": (2, "<H"), "addr": (4, "<I"), "len": (8, "<I")}.items():
        if name in fields:
            struct.pack_into(fmt, e, at, fields[name])
    struct.pack_into("<H", e, 0, crc16(bytes(e[2:])))
    return bytes(e)


def flash_header_valid(flash):
    if len(flash) < 32:
        return False
    h = enc(flash[:32])
    return crc16(h[2:]) == _u16(h, 0)


def boot_table(flash):
    """the boot area's file table (uboot.boot, isd_config.ini, app_dir_head, key_mac), or [] if the
    flash header is damaged"""
    if len(flash) < BOOT_AREA or not flash_header_valid(flash):
        return []
    out = []
    for a in range(0x20, BOOT_AT, 0x20):
        e = Entry(enc(flash[a:a + 32]))
        if not e.valid:
            break
        out.append(e)
    return out


def app_read(flash, base, off, n, key):
    """n plain bytes at offset off of the app area at base"""
    out = bytearray()
    a = off - off % 32
    while a < off + n:
        start = base + a
        if start + 32 > len(flash):
            break
        out += enc(flash[start:start + 32], key ^ ((a >> 2) & 0xFFFF))
        a += 32
    s = off % 32
    return bytes(out[s:s + n])


def app_table(flash, key=CHIP_KEY):
    """the app area's file table: files (app.bin, cfg_tool.bin; addresses in the area, attr 0x82) and
    region descriptors (VM, BTIF, ...; absolute addresses, attr 0x12 or 0x92)"""
    out = []
    for i in range(16):
        raw = app_read(flash, BOOT_AREA, 32 * i, 32, key)
        if len(raw) < 32:
            break
        e = Entry(raw)
        if not e.valid:
            break
        out.append(e)
    return out


APP_OK, APP_BAD_CRC, APP_INCOMPLETE, APP_NOT_FOUND = "ok", "bad_crc", "incomplete", "not_found"
APP_TEXT = {APP_OK: "OK", APP_BAD_CRC: "damaged (its checksum fails; it may still start)",
            APP_INCOMPLETE: "incomplete (partly erased: an interrupted write)", APP_NOT_FOUND: "none installed"}


def app_files_status(flash, key=CHIP_KEY):
    """as m-bootloader's menu reports it: a damaged file with erased sectors in it is an interrupted write"""
    files = [e for e in app_table(flash, key) if e.attr == 0x82]
    if not files:
        return APP_NOT_FOUND
    for e in files:
        start, end = BOOT_AREA + e.addr, BOOT_AREA + e.addr + e.len
        if end > len(flash):
            return APP_BAD_CRC
        if crc16(app_read(flash, BOOT_AREA, e.addr, e.len, key)) != e.data_crc:
            for s in range(start - start % SECTOR, end, SECTOR):
                if flash[s:s + SECTOR] == b"\xFF" * SECTOR:
                    return APP_INCOMPLETE
            return APP_BAD_CRC
    return APP_OK


def app_end(flash, key=CHIP_KEY):
    """where the firmware's code ends (its files, or the protected code area PRCT), 0 if none"""
    table = app_table(flash, key)
    files = [e for e in table if e.attr == 0x82]
    if not files:
        return 0
    end = BOOT_AREA + max(e.addr + e.len for e in files)
    for e in table:
        if e.name == "PRCT" and e.addr == 0 and e.len <= len(flash):
            end = max(end, e.len)
    return end


def regions(flash, key=CHIP_KEY):
    """{name: (start, end)} of the regions the firmware's table declares (VM, BTIF, USR, SEQ64, ...)"""
    return {e.name: (e.addr, e.addr + e.len) for e in app_table(flash, key)
            if e.attr in (0x12, 0x92) and e.len and e.addr + e.len <= FLASH_SIZE}


# ---------------------------------------------------- bootloader (BANKCB) ---
def bankcb_check(boot):
    """-> the payload of a valid BANKCB container (a uboot.boot file), else FormatError"""
    if len(boot) < 16:
        raise FormatError("bootloader file too short")
    bank, size, offset, pcrc, hcrc = _u16(boot, 0), _u16(boot, 2), _u32(boot, 8), _u16(boot, 12), _u16(boot, 14)
    if not (bank == 1 and crc16(boot[:14]) == hcrc and offset + size <= len(boot)) \
            or crc16(boot[offset:offset + size]) != pcrc:
        raise FormatError("not a valid bootloader container (BANKCB)")
    return boot[offset:offset + size]


def bootloader_identity(boot):
    """(board, version) of an m-bootloader build; None for M-VAVE's or another"""
    try:
        p = bankcb_check(boot)
    except FormatError:
        return None
    if len(p) < 0x70 or p[0x40:0x48] != b"AC79BLID":
        return None
    return _cstr(p[0x48:0x50]), _cstr(p[0x50:0x70])


def stored_bootloader(flash):
    """the uboot.boot container stored in the boot area (plain), or None"""
    t = boot_table(flash)
    if not t:
        return None
    e = t[0]
    if e.name != "uboot.boot" or e.addr != BOOT_AT or BOOT_AT + e.len > len(flash):
        return None
    s = flash[BOOT_AT:BOOT_AT + e.len]
    if len(s) < 16:
        return None
    boot = enc(s[:16]) + enc(s[16:])
    return boot if crc16(boot) == e.data_crc else None


def reads_config(boot):
    """whether this bootloader reads the unit's configuration: anything but m-bootloader 1.4 or newer"""
    ident = bootloader_identity(boot)
    if ident is None:
        return True
    number = ident[1].split("-")[0]
    try:
        major, minor = (int(x) for x in number.split(".")[:2])
    except ValueError:
        return True
    return (major, minor) < (1, 4)


def config_cut_short(flash):
    """the configuration is one of those m-bootloader leaves (or 2.0's shorter one), not the stock one"""
    e = next((e for e in boot_table(flash) if e.name == "isd_config.ini"), None)
    return e is None or e.len <= CONFIG_SIZE


def config_sys_clk(flash):
    """the system clock the unit's own boot configuration asks for, in Hz, or None"""
    e = next((e for e in boot_table(flash) if e.name == "isd_config.ini"), None)
    if e is None:
        return None
    c = flash[e.addr:e.addr + e.len]
    at = c.find(b"SYS_CLK\0")
    if at < 0 or at + 12 > len(c):
        return None
    return _u32(c, at + 8)


def check_boot_area(flash):
    """FormatError unless this boot area can start a unit: a bootloader that passes its checksum, and
    the configuration it reads, whole, after it, with the chip-key record intact. Custom firmware
    packages may carry only a stand-in here (Felucca's builder puts JieLi's SDK bootloader there with a
    failing checksum and only the chip key): their own installers never write it, nor may we."""
    boot = stored_bootloader(flash)
    if boot is None:
        raise FormatError("its bootloader is damaged or missing")
    e = next((e for e in boot_table(flash) if e.name == "isd_config.ini"), None)
    if e is None:
        raise FormatError("it has no boot configuration")
    if e.addr < BOOT_AT + len(boot) or e.len < CONFIG_KEY or e.len > max(0, BOOT_AREA - e.addr):
        raise FormatError("its boot configuration is not where the bootloader can read it")
    config = flash[e.addr:e.addr + e.len]
    if crc16(config) != e.data_crc:
        raise FormatError("its boot configuration is damaged")
    if crc16(config[:32]) != _u16(config, 32):
        raise FormatError("its chip-key record is damaged")
    if reads_config(boot) and config_cut_short(flash):
        raise FormatError("its boot configuration is incomplete for its bootloader")


def _stored(boot):
    """a uboot.boot as JieLi's packager stores it: the 16-byte header and the payload each ENC 0xFFFF"""
    return enc(boot[:16]) + enc(boot[16:])


def build_boot_area(flash, boot):
    """the 16 KiB boot area with its bootloader replaced by one that reads the configuration (stock,
    m-bootloader before 1.4). Keeps the flash header, the file table and the unit's own isd_config;
    moves the configuration further in only when a larger bootloader needs the room."""
    bankcb_check(boot)
    if len(flash) < BOOT_AREA:
        raise FormatError("the boot area is incomplete")
    area = bytearray(flash[:BOOT_AREA])
    t = boot_table(area)
    if len(t) < 3 or t[0].name != "uboot.boot" or t[0].addr != BOOT_AT:
        raise FormatError("the boot area has no valid file table")
    isd_at, isd_len = t[1].addr, t[1].len
    if t[1].name != "isd_config.ini" or isd_at < BOOT_AT or isd_at > BOOT_AREA or isd_len == 0 \
            or isd_len > BOOT_AREA - isd_at or t[0].len > isd_at - BOOT_AT:
        raise FormatError("isd_config is not safely contained in the boot area")
    if len(boot) > BOOT_AREA - BOOT_AT - isd_len:
        raise FormatError(f"bootloader too large: {len(boot)} bytes, room for {BOOT_AREA - BOOT_AT - isd_len}")
    boot_end = BOOT_AT + len(boot)
    new_at = (boot_end + 3) & ~3 if boot_end > isd_at else isd_at
    if new_at + isd_len > BOOT_AREA:
        raise FormatError("no room for the bootloader and the unit's configuration in the boot area")
    if new_at != isd_at:
        if any(b != 0xFF for b in area[isd_at + isd_len:new_at + isd_len]):
            raise FormatError("moving isd_config would overwrite other boot-area data")
        config = bytes(area[isd_at:isd_at + isd_len])
        area[isd_at:isd_at + isd_len] = b"\xFF" * isd_len
        area[new_at:new_at + isd_len] = config
        area[0x40:0x60] = enc(_entry_bytes(t[1].raw, addr=new_at))
    area[0x20:0x40] = enc(_entry_bytes(t[0].raw, data_crc=crc16(boot), addr=BOOT_AT, len=len(boot)))
    area[BOOT_AT:new_at] = _stored(boot).ljust(new_at - BOOT_AT, b"\xFF")
    return bytes(area)


def build_short_boot_area(flash, boot):
    """the boot area for a bootloader that does not read the configuration (m-bootloader 1.4 and
    newer): the bootloader, then erased flash up to what is left of the configuration at CONFIG_AT:
    the unit's chip-key record and the settings older versions need. Keeps the flash header and the
    file table. Refused if the record is damaged: it is the only part kept, and installers need it."""
    bankcb_check(boot)
    if len(flash) < BOOT_AREA:
        raise FormatError("the boot area is incomplete")
    if len(boot) > CONFIG_AT - BOOT_AT:
        raise FormatError(f"bootloader too large: {len(boot)} bytes, room for {CONFIG_AT - BOOT_AT}")
    t = boot_table(flash)
    if len(t) < 3 or t[0].name != "uboot.boot" or t[0].addr != BOOT_AT:
        raise FormatError("the boot area has no valid file table")
    isd_at, isd_len = t[1].addr, t[1].len
    if t[1].name != "isd_config.ini" or isd_at < 0x38D0 or isd_at > BOOT_AREA or isd_len < CONFIG_KEY \
            or isd_len > BOOT_AREA - isd_at:
        raise FormatError("isd_config is not safely contained in the boot area")
    if any(e.addr < BOOT_AREA for e in t[2:]):
        raise FormatError("other data in the boot area would be overwritten")
    key = flash[isd_at:isd_at + CONFIG_KEY]
    if crc16(key[:32]) != _u16(key, 32):
        raise FormatError("the unit's chip-key record is damaged")
    blob = bytes(key) + CONFIG_SETTINGS
    area = bytearray(b"\xFF" * BOOT_AREA)
    area[:BOOT_AT] = flash[:BOOT_AT]
    area[0x20:0x40] = enc(_entry_bytes(t[0].raw, data_crc=crc16(boot), len=len(boot)))
    area[0x40:0x60] = enc(_entry_bytes(t[1].raw, data_crc=crc16(blob), addr=CONFIG_AT, len=CONFIG_SIZE))
    area[BOOT_AT:BOOT_AT + len(boot)] = _stored(boot)
    area[CONFIG_AT:] = blob
    return bytes(area)


def boot_area_for(flash, boot):
    """the boot area to write for bootloader boot over this flash (either builder, as it needs)"""
    return build_boot_area(flash, boot) if reads_config(boot) else build_short_boot_area(flash, boot)


# --------------------------------------------------------------- packages ---
FWSC_BLOCK, FWSC_KEEP = 0x30, 0x2F


class Package:
    """an M-VAVE firmware package (.fwsc): a JieLi UFW image with the product identity woven in. The file
    is cut into 48-byte blocks; byte 47 of block i holds identity character i as (char + i + 1) & 0xFF,
    0x7D once it has ended (20 blocks, or more while 0x7D runs on). Without those bytes: a 64-byte
    header and the file list (80 bytes a file), both ENC 0xFFFF. flash.bin (type 0) is the flash image
    from address 0: the boot area as stored, then the app area, already in the chip key's cipher."""

    def __init__(self, raw):
        bad = FormatError("not a firmware package (.fwsc)")
        raw = bytes(raw)
        if len(raw) < 0x1000:
            raise bad
        n = 0
        while n < 20 or raw[n * FWSC_BLOCK + FWSC_KEEP] == 0x7D:
            n += 1
            if (n + 1) * FWSC_BLOCK > len(raw):
                raise bad
        ident = ""
        for i in range(n):
            m = raw[i * FWSC_BLOCK + FWSC_KEEP]
            if m == 0x7D:
                break
            ident += chr((m - i - 1) & 0xFF)
        image = b"".join(raw[i * FWSC_BLOCK:i * FWSC_BLOCK + FWSC_KEEP] for i in range(n)) + raw[n * FWSC_BLOCK:]
        head = enc(image[:0x40])
        if crc16(head[2:]) != _u16(head, 0):
            raise FormatError("not a firmware package, or a damaged one (header CRC)")
        count = _u16(head, 8)
        if 0x40 + 0x50 * count > len(image):
            raise FormatError("damaged firmware package (file list)")
        lst = image[0x40:0x40 + 0x50 * count]
        if crc16(lst) != _u16(head, 2):
            raise FormatError("damaged firmware package (file list CRC)")
        self.files = {}
        for i in range(count):
            e = enc(lst[i * 0x50:(i + 1) * 0x50])
            typ, dcrc, off, size = _u16(e, 0), _u16(e, 4), _u32(e, 8), _u32(e, 12)
            if off + size > len(image):
                raise FormatError("damaged firmware package (file outside the image)")
            self.files[_cstr(e[0x40:])] = (typ, dcrc, image[off:off + size])
        f = self.files.get("flash.bin")
        if f is None or f[0] != 0:
            raise FormatError("the package has no flash image (flash.bin)")
        if crc16(f[2]) != f[1]:
            raise FormatError("damaged firmware package (flash.bin CRC)")
        if len(f[2]) <= BOOT_AREA or len(f[2]) % SECTOR:
            raise FormatError(f"unexpected flash image size {len(f[2])}")
        self.identity, self.flash = ident, f[2]

    def model(self):
        """"FM-1", "SMK-37 Pro", ... (the identity without its version)"""
        return self.identity.rsplit("_", 1)[0] if "_" in self.identity else self.identity


# ---------------------------------------------------------------- analysis ---
SMK_TRIM_MARK = (0xC1224, b"trimdat\0")
SMK_GUARD_COPY = (0xFF000, b"TRIMCOPY")          # m-bootloader's copy of the SMK-37 calibration
MODELS = (b"FM-1", b"MiniFM", b"SMK-37 Pro", b"SMK-37 Elite", b"SMK-37+VA")


def _has(flash, at_mark):
    at, mark = at_mark
    return flash[at:at + len(mark)] == mark


def _identities(text):
    """("FM-1", "016") for every "MODEL_NNN" in the text"""
    out = []
    for m in MODELS:
        i = text.find(m + b"_")
        while i >= 0:
            d = text[i + len(m) + 1:i + len(m) + 4]
            if len(d) == 3 and d.isdigit():
                out.append((m.decode(), d.decode()))
            i = text.find(m + b"_", i + 1)
    return out


def _after(text, prefix, ok):
    """the run of bytes passing ok right after the first prefix that is followed by a version"""
    i = text.find(prefix)
    while i >= 0:
        s = e = i + len(prefix)
        while e < len(text) and ok(text[e]):
            e += 1
        v = text[s:e]
        if v[:1].isdigit() and b"." in v:
            return v.decode().rstrip(".")
        i = text.find(prefix, i + 1)
    return None


def bootloader_info(flash):
    """("stock" | "open" | "damaged" | "unknown", text, version or None)"""
    boot = stored_bootloader(flash)
    if boot is None:
        return ("damaged", "damaged or missing", None) if not boot_table(flash) else ("unknown", "unreadable", None)
    ident = bootloader_identity(boot)
    if ident is None:
        return "stock", "M-VAVE's (stock)", None
    board, version = ident
    name = "m-bootloader" if version[:1].isdigit() and "." in version else "earlier open bootloader"
    return "open", f"{name} {version} ({board})", version


def firmware_info(flash, key=CHIP_KEY):
    """a name for the installed firmware ("Wild 0.3.38", "FM-1 016 (stock)", "Felucca", ...)"""
    if app_files_status(flash, key) == APP_NOT_FOUND:
        return "none"
    table = app_table(flash, key)
    app = next((e for e in table if e.name == "app.bin"), None)
    if app is None or BOOT_AREA + app.addr + app.len > len(flash):
        return "unknown"
    text = app_read(flash, BOOT_AREA, app.addr, app.len, key)
    idents = sorted(set(_identities(text)), key=lambda x: x[1])
    baud_girl = any(e.name == "VM" and e.addr == 0xD9000 for e in table)
    digits = lambda c: chr(c).isdigit() or c == ord(".")                     # noqa: E731
    for prefix, name in ((b"Wild ", "Wild"), (b"NB303 ", "NB303"), (b"n001se ", "n001se")):
        v = _after(text, prefix, digits)
        if v:
            return f"{name} {v}"
    v = _after(text, b"SLOOP ", digits)
    if v:
        return f"SLOOP {v}" + (" (Snoop37)" if idents and idents[-1][0].startswith("SMK") else "")
    if b"Felucca" in text:
        return "Felucca"
    if idents:
        model, version = idents[-1]
        n = f"{model} {version}"
        if model == "SMK-37+VA" or baud_girl:
            return n + " (Baud Girl)"
        if version not in ("900", "000") and len(idents) == 1:
            return n + " (stock)"
        return n
    return "unknown"


def layout(flash, key=CHIP_KEY):
    """which FM-1 data layout the firmware declares: "016" (VM 0xBA000, SEQ64 0xDA000, BTIF 0xEC000),
    "015" (VM 0x93000 or BTIF 0xE9000, USR 0xEA000), "smk37" (USRTRIM 0xC1000) or "other"/None"""
    r = regions(flash, key)
    at = {k: v[0] for k, v in r.items()}
    if at.get("USRTRIM") == 0xC1000:
        return "smk37"
    if at.get("VM") == 0xBA000 and at.get("SEQ64") == 0xDA000 and at.get("BTIF") == 0xEC000:
        return "016"
    if (at.get("VM") == 0x93000 or at.get("BTIF") == 0xE9000) and at.get("USR") == 0xEA000:
        return "015"
    return "other" if r else None


def is_keyboard(flash, key=CHIP_KEY):
    """an M-VAVE SMK-37 keyboard's flash (its calibration, m-bootloader's copy of it, its layout or its
    m-bootloader build), whatever the flash chip"""
    boot = stored_bootloader(flash)
    ident = bootloader_identity(boot) if boot else None
    return bool(_has(flash, SMK_TRIM_MARK) or _has(flash, SMK_GUARD_COPY) or layout(flash, key) == "smk37"
                or (ident and ident[0] == "smk37"))


def analyse(flash, key=CHIP_KEY):
    """-> dict: what the flash image holds, for the report and the window"""
    kind, text, version = bootloader_info(flash)
    status = app_files_status(flash, key)
    return {"bootloader": kind, "bootloader_text": text, "bootloader_version": version,
            "firmware": firmware_info(flash, key), "firmware_status": status,
            "firmware_status_text": APP_TEXT[status], "layout": layout(flash, key),
            "keyboard": is_keyboard(flash, key),
            "boot_area_ok": _boot_ok(flash)}


def _boot_ok(flash):
    try:
        check_boot_area(flash)
        return True
    except FormatError:
        return False


# ------------------------------------------------------------- sector map ---
BOOT, FIRMWARE, SETTINGS, PAIRINGS, CALIBRATION, PRESETS, SEQUENCES, SOUNDS, MAC, FREE = (
    "boot", "firmware", "settings", "pairings", "calibration", "presets", "sequences", "sounds", "mac", "free")
REGION_TEXT = {BOOT: "Bootloader", FIRMWARE: "Firmware", SETTINGS: "Settings", PAIRINGS: "Bluetooth pairings",
               CALIBRATION: "Factory calibration", PRESETS: "Presets", SEQUENCES: "Sequences",
               SOUNDS: "Synth sounds", MAC: "Bluetooth address", FREE: "Free / not declared"}
_REGION_OF = {"VM": SETTINGS, "BTIF": PAIRINGS, "USRTRIM": CALIBRATION, "USRFLASH": PRESETS,
              "SEQ64": SEQUENCES, "USR": SOUNDS}
# M-VAVE's FM-1 015 layout, for a flash whose firmware table cannot be read
_STOCK_FM1 = ((FIRMWARE, 0x04000, 0x93000), (SETTINGS, 0x93000, 0xE9000), (PAIRINGS, 0xE9000, 0xEA000),
              (SOUNDS, 0xEA000, 0xFC000))


def sector_map(flash, key=CHIP_KEY):
    """a region name for each of the 256 sectors of 4 KiB, from the firmware's own tables"""
    n = len(flash) // SECTOR
    out = [FREE] * n

    def mark(r, start, end):
        for s in range(start // SECTOR, min(n, (end + SECTOR - 1) // SECTOR)):
            out[s] = r
    end = app_end(flash, key)
    if end:
        mark(FIRMWARE, BOOT_AREA, end)
        for e in app_table(flash, key):
            r = _REGION_OF.get(e.name)
            if r and e.attr in (0x12, 0x92) and e.len and e.addr + e.len <= len(flash):
                mark(r, e.addr, e.addr + e.len)
    else:
        for r, a, b in _STOCK_FM1:
            mark(r, a, b)
    if is_keyboard(flash, key):
        mark(CALIBRATION, 0xC1000, 0xC2000)
    mark(BOOT, 0, BOOT_AREA)
    mark(MAC, *KEY_MAC)
    return out


def changed_sectors(old, new):
    """addresses of the 4 KiB sectors where two images differ"""
    return [a for a in range(0, min(len(old), len(new)), SECTOR) if old[a:a + SECTOR] != new[a:a + SECTOR]]
