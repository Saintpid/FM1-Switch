#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/fm1_rescue.py against a simulated FM-1 in the chip's own update mode (UBOOT), no hardware:
the ROM (memory writes, the jump into the loader) and the loader (key, flash id, erase, write, read)
at the level of the 16-byte commands, over the USB transport's xfer() and over Windows' SCSI
pass-through (the struct as Windows reads it, through a fake DeviceIoControl), and through a
simulated FM-1-transporter (its serial line protocol). Checks the write policy: a backup of the
whole flash first, only the differing sectors in [0x4000, 0x93000), high to low, nothing for V15 when
the head differs, nothing when the chip is another one or the firmware is for another chip key, the
FM-1's own head kept for any other firmware, and the final image.
  python3 tests/rescue_test.py"""
import ctypes as C
import json
import os
import random
import struct
import sys
import tempfile
import types
import zlib
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fm1_rescue as R  # noqa: E402
import fm1_flash as FL  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from fm1_fixture import boot_area, open_boot, unit_flash  # noqa: E402

failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<64} {'ok' if cond else 'FAIL'}")
    failed += not cond


LOADER = bytes(random.Random(1).randrange(256) for _ in range(R.LOADER_SIZE))


class FakeUboot:
    """the FM-1 in UBOOT: a 1 MiB flash, the ROM until the loader is started, then the loader"""

    def __init__(self, flash, key=0x980F, fid=0x856014, typ=3):
        self.flash = bytearray(flash)
        self.ram = {}
        self.mode = "rom"
        self.key, self.fid = key, fid
        self.typ = typ
        self.erased, self.writes, self.ran = [], [], False

    def reply(self, cmd, p=b""):
        return (struct.pack(">H", cmd) + p).ljust(16, b"\0")

    def xfer(self, cdb, data_out=None, n_in=0, timeout=5000):
        cdb = bytes(cdb)
        if cdb[0] == 0x12:
            return (b"\0" * 8 + b"WL82    " + b"UBOOT1.00".ljust(16) + b"1.00")[:n_in]
        cmd = struct.unpack(">H", cdb[:2])[0]
        a = cdb[2:]
        rom = {R.CMD_WRITE_MEMORY, R.CMD_READ_MEMORY, R.CMD_JUMP}
        if (cmd in rom) != (self.mode == "rom"):
            raise R.Refused("command failed (status 2)")
        if cmd in (R.CMD_WRITE_MEMORY, R.CMD_WRITE_FLASH):
            addr, n = struct.unpack(">IH", a[:6])
            crc = struct.unpack("<H", a[7:9])[0]
            if data_out is None or len(data_out) != n or R.crc16(data_out) != crc:
                raise R.Refused("command failed (status 2)")
            if cmd == R.CMD_WRITE_MEMORY:
                self.ram[addr] = bytes(data_out)
            else:
                self.writes.append(addr)
                for i, b in enumerate(data_out):
                    self.flash[addr + i] &= b                # flash: only erased bits take a write
            return b""
        if cmd == R.CMD_JUMP:
            addr = struct.unpack(">I", a[:4])[0]
            got = b"".join(self.ram[k] for k in sorted(self.ram))
            if addr == R.LOADER_ADDR and min(self.ram) == R.LOADER_ADDR and got == LOADER:
                self.mode = "loader"
            return self.reply(cmd)
        if cmd == R.CMD_READ_KEY:
            k = bytearray([self.key & 0xFF, self.key >> 8])
            R.crc_cipher(k)
            return self.reply(cmd, bytes([0, 0, 0, 0, k[1], k[0]]))
        if cmd == R.CMD_GET_ONLINE_DEVICE:
            return self.reply(cmd, bytes([self.typ, 0]) + struct.pack("<I", self.fid))
        if cmd == R.CMD_ERASE_SECTOR:
            addr = struct.unpack(">I", a[:4])[0]
            self.erased.append(addr)
            self.flash[addr:addr + R.SECTOR] = b"\xFF" * R.SECTOR
            return self.reply(cmd)
        if cmd == R.CMD_READ_FLASH:
            addr, n = struct.unpack(">IH", a[:6])
            return bytes(self.flash[addr:addr + n])
        if cmd == R.CMD_RUN_APP:
            self.ran = True
            return self.reply(cmd)
        raise R.Refused("command failed (status 2)")


class FakeWin32:
    """DeviceIoControl as Windows' disk driver does it: the struct read from memory, the data
    through its pointer; PhysicalDrive0 is an ordinary disk, PhysicalDrive2 the FM-1"""

    def __init__(self, dev, volumes=None):
        self.C, self.dev, self.calls = C, dev, []
        self.volumes = volumes or {}                           # drive letter -> the disk it is on
        self.closed = []

    def open(self, path, rw):
        if "PhysicalDrive" not in path:
            c = path[4]
            return 1000 + ord(c) if c in self.volumes else None
        n = int(path.rsplit("PhysicalDrive", 1)[1])
        return n + 100 if n in (0, 2) else None

    def ioctl(self, h, code, inaddr, n_in, outaddr, n_out):
        self.calls.append(code)
        if h >= 1000:
            c = chr(h - 1000)
            if code == R.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS:
                C.memmove(outaddr, struct.pack("<I4xI4xqq", 1, self.volumes[c], 0, 1 << 20), 32)
                return 32
            if code in (R.FSCTL_LOCK_VOLUME, R.FSCTL_DISMOUNT_VOLUME):
                self.calls.append((code, c))
                return 0
            raise R.Fail("Windows refused the command (error 1)")
        if code == R.IOCTL_STORAGE_QUERY_PROPERTY:
            v, p = (b"WL82", b"UBOOT1.00") if h == 102 else (b"Samsung", b"SSD 980")
            d = bytearray(64)
            struct.pack_into("<II", d, 0, 1, 64)
            struct.pack_into("<II", d, 12, 36, 44)
            d[36:36 + len(v)], d[44:44 + len(p)] = v, p
            C.memmove(outaddr, bytes(d), len(d))
            return len(d)
        if code != R.IOCTL_SCSI_PASS_THROUGH_DIRECT or h != 102:
            raise R.Fail("Windows refused the command (error 1)")
        SPTD, SPTDS = R._sptd_types()
        s = SPTDS.from_address(inaddr)
        t = s.sptd
        if t.Length != C.sizeof(SPTD) or n_in != C.sizeof(SPTDS) or t.SenseInfoOffset != SPTDS.sense.offset:
            raise R.Fail("Windows refused the command (error 87)")
        cdb = bytes(t.Cdb[:t.CdbLength])
        try:
            if t.DataIn == R.DATA_IN:
                got = self.dev.xfer(cdb, n_in=t.DataTransferLength)
                C.memmove(t.DataBuffer, got, len(got))
            else:
                self.dev.xfer(cdb, data_out=C.string_at(t.DataBuffer, t.DataTransferLength))
            t.ScsiStatus = 0
        except R.Fail:
            t.ScsiStatus = 2
        return n_in

    def close(self, h):
        self.closed.append(h)


def app_head(image, key=0x980F):
    """a valid app_area_head at 0x4000, as the packer writes it (SFC with the chip key)"""
    h = bytearray(random.Random(3).randrange(256) for _ in range(32))
    struct.pack_into("<H", h, 0, R.crc16(bytes(h[2:32])))
    R.enc(h, key)
    image[R.WRITE_MIN:R.WRITE_MIN + 32] = h


def make_fwsc(flash, product, felucca=False):
    """an FM-1 package around a flash image: the UFW header and entry list (ENC), the identity marker
    bytes (the inverse of package_flash_image and product_of)"""
    files = [(0, flash)] + ([(100, b"FELUCCA-LOADER-1" + bytes(64))] if felucca else [])
    off, ents = 0x400, []
    for i, (typ, d) in enumerate(files):
        off = (off + 0xFF) & ~0xFF
        e = bytearray(0x50)
        struct.pack_into("<HHHHII", e, 0, typ, i, R.crc16(d), 0, off, len(d))
        ents.append((e, off, d))
        off += len(d)
    logical = bytearray(b"\xFF" * off)
    hdr = bytearray(0x40)
    struct.pack_into("<IH", hdr, 4, off, len(files))
    hdr[16:22] = b"AC791N"
    R.enc(hdr)
    logical[0:0x40] = hdr
    for i, (e, o, d) in enumerate(ents):
        R.enc(e)
        logical[0x40 + i * 0x50:0x40 + (i + 1) * 0x50] = e
        logical[o:o + len(d)] = d
    raw = bytearray()
    for i in range(20):
        raw += logical[i * 0x2F:(i + 1) * 0x2F] + bytes([(ord(product[i]) + i + 1) & 0xFF if i < len(product) else 0x7D])
    return bytes(raw + logical[20 * 0x2F:])


class FakeTransporter:
    """the FM-1-transporter's data port: its line protocol over a FakeUboot. state: "uboot", "v15"
    (stock V15 runs: the soft key moves it to UBOOT) or "off" (no firmware: rekey, then power-on)"""

    def __init__(self, dev, state="uboot"):
        self.dev, self.state, self.loader = dev, state, False
        self.out, self.inbuf, self.timeout, self.closed, self.lines = b"", b"", 5, False, []

    def reset_input_buffer(self):
        self.out = b""

    def write(self, b):
        self.inbuf += bytes(b)
        while b"\n" in self.inbuf:
            line, rest = self.inbuf.split(b"\n", 1)
            words = line.decode().split()
            need = {"ldr": lambda w: int(w[2]), "wsec": lambda w: R.SECTOR}.get(words[0], lambda w: 0)(words)
            if len(rest) < need:
                return
            data, self.inbuf = rest[:need], rest[need:]
            self.lines.append(words[0])
            self.out += self.answer(words, data)

    def answer(self, w, data):
        up = self.state == "uboot"
        if w[0] == "status":
            return f"OK uboot={int(up)} v15={int(self.state == 'v15')} loader={int(self.loader)}\n".encode()
        if w[0] == "uboot":
            self.state = "uboot"
            return b"OK soft key sent\n"
        if w[0] == "rekey":
            self.rekeyed = True
            return b"OK rebooting\n"
        if not up:
            return b"ERR no uboot\n"
        if w[0] == "ldr":
            if zlib.crc32(data) & 0xFFFFFFFF != int(w[3], 16):
                return b"ERR crc\n"
            self.loader = int(w[1]) + len(data) == R.LOADER_SIZE or self.loader
            return b"OK\n"
        if not self.loader:
            return b"ERR no loader\n"
        if w[0] == "info":
            return f"OK key={self.dev.key:04X} type=3 id={self.dev.fid:06X}\n".encode()
        if w[0] == "read":
            a, n = int(w[1], 0), int(w[2])
            d = bytes(self.dev.flash[a:a + n])
            return f"DATA {n}\n".encode() + d + f"END {zlib.crc32(d) & 0xFFFFFFFF:08X}\n".encode()
        if w[0] == "wsec":
            a = int(w[1], 0)
            if a < R.WRITE_MIN or a >= R.APP_END or zlib.crc32(data) & 0xFFFFFFFF != int(w[2], 16):
                return b"ERR refused\n"
            self.dev.erased.append(a)
            self.dev.writes.append(a)
            self.dev.flash[a:a + R.SECTOR] = data
            return b"OK\n"
        if w[0] == "runapp":
            self.dev.ran = True
            return b"OK\n"
        return b"ERR ?\n"

    def readline(self):
        i = self.out.find(b"\n")
        line, self.out = (self.out, b"") if i < 0 else (self.out[:i + 1], self.out[i + 1:])
        return line

    def read(self, n):
        d, self.out = self.out[:n], self.out[n:]
        return d

    def close(self):
        self.closed = True


def flashes():
    rnd = random.Random(7)
    image = bytearray(rnd.randrange(256) for _ in range(R.APP_END))
    image[:R.WRITE_MIN] = boot_area()                              # M-VAVE's bootloader, working
    app_head(image)
    now = bytearray(image) + bytes(rnd.randrange(256) for _ in range(R.FLASH_SIZE - R.APP_END))
    for a in (0x5000, 0x6000, 0x40000, 0x92000):                  # the crashing firmware's sectors
        now[a + 17] ^= 0x5A
    return bytes(image), bytes(now)


def with_flash_ids(image, ids, key=0x980F):
    """the image with these JEDEC IDs as 32-bit constants in its app, as Felucca's exact-ID gate
    compiles them (encrypted again with the chip key, the app head untouched)"""
    plain = bytearray(R.app_area(image, key))
    for i, fid in enumerate(ids):
        plain[0x9000 + 16 * i:0x9004 + 16 * i] = fid.to_bytes(4, "little")
    out = bytearray(image)
    out[R.WRITE_MIN:R.APP_END] = R.app_area(bytes(R.WRITE_MIN) + bytes(plain), key)
    return bytes(out)


tmp = tempfile.mkdtemp(prefix="fm1-rescue-")

# ---- the struct, as Windows lays it out
SPTD, SPTDS = R._sptd_types()
x64 = C.sizeof(C.c_void_p) == 8
ok(C.sizeof(SPTD) == (56 if x64 else 44) and SPTD.DataBuffer.offset == (24 if x64 else 20),
   f"SCSI_PASS_THROUGH_DIRECT: Windows' size ({C.sizeof(SPTD)})")

for name, make in (("USB", lambda d: d), ("Windows", lambda d: R.WinScsi(r"\\.\PhysicalDrive2", FakeWin32(d)))):
    image, now = flashes()
    dev = FakeUboot(now)
    steps = []
    done = R.rescue(R.Uboot(make(dev)), image, LOADER, True, tmp, say=lambda *a, **k: None,
                    step=lambda k, v=None: steps.append((k, v)))
    backup = [s[1] for s in steps if s[0] == "backup"]
    ok(done and backup and open(backup[0], "rb").read() == now, f"{name}: the whole flash backed up first")
    ok(open(backup[0] + ".sha256").read().split()[0] == R.sha256(now)
       and [s for s in steps if s[0] == "backup_read"] == [("backup_read", 1), ("backup_read", 2)],
       f"{name}: two backup passes and saved checksum")
    ok(bytes(dev.flash) == image + now[R.APP_END:], f"{name}: the flash is V15, the device data kept")
    ok(dev.erased == [0x92000, 0x40000, 0x6000, 0x5000], f"{name}: only the 4 differing sectors, high to low")
    ok(all(R.WRITE_MIN <= a < R.APP_END for a in dev.writes), f"{name}: nothing below 0x4000 or from 0x93000")
    ok(dev.ran and steps[-1] == ("done", None) and ("write", 100) in steps, f"{name}: restarted, progress to 100%")
    os.remove(backup[0])

image, now = flashes()
dev = FakeUboot(now)
ok(R.rescue(R.Uboot(dev), image, LOADER, False, tmp, say=lambda *a, **k: None) is False and not dev.erased,
   "without --write: checked and backed up, nothing written")

# A corrupted second read must fail before a single erase/write.
dev = FakeUboot(now)
wrapped = R.Uboot(dev)
read = wrapped.read
reads = [0]
def unstable_read(addr, size):
    data = read(addr, size)
    reads[0] += 1
    return bytes([data[0] ^ 1]) + data[1:] if reads[0] == 2 else data
wrapped.read = unstable_read
try:
    R.rescue(wrapped, image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "different backup reads: refused")
except R.Fail as e:
    ok("backup reads differ" in str(e) and not dev.erased and not dev.writes,
       "different backup reads: refused before erase/write")

bad = bytearray(now)
bad[0x1234] ^= 1                                                   # in the bootloader: it fails its CRC
dev = FakeUboot(bad)
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "the FM-1's own boot area cannot start it: refused")
except R.Fail as e:
    ok("cannot start it" in str(e) and not dev.erased, "the FM-1's own boot area cannot start it: refused")
mb = bytearray(now)
mb[:R.WRITE_MIN] = boot_area(open_boot("2.4-test"))[:R.WRITE_MIN]   # another working bootloader
dev = FakeUboot(mb)
ok(R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None)
   and bytes(dev.flash) == bytes(mb[:R.WRITE_MIN]) + image[R.WRITE_MIN:] + now[R.APP_END:],
   "V15 under another working bootloader: written, that bootloader kept")

dev = FakeUboot(now, fid=0x854015)
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "another flash chip: refused")
except R.Fail as e:
    ok("not the FM-1" in str(e) and not dev.erased, "another flash chip: refused")

# ---- hardware identification, read-only backup, experimental UCUN recovery
for fid in (0x856014, 0xB36014):
    image, now = flashes()
    dev = FakeUboot(now, fid=fid)
    steps = []
    saved, info, path = R.inspect_backup(R.Uboot(dev), LOADER, tmp, say=lambda *a, **k: None,
                                         step=lambda *a: steps.append(a))
    meta = json.loads(Path(path + ".json").read_text())
    ok(saved == now and not dev.erased and not dev.writes and not dev.ran
       and info["jedec_id"] == f"{fid:06X}" and info["known_fm1_part"]
       and meta["backup_sha256"] == R.sha256(now) and meta["matching_full_reads"] == 2
       and meta["saved_file_verified"] and meta["backup_bytes"] == R.FLASH_SIZE,
       f"{fid:06X}: identify and verify full backup without erase/program/runapp")
    rep = json.loads(Path(steps[0][1]["report"]).read_text())["hardware"]
    ok(all(info[k] == v for k, v in rep.items()) and meta["hardware"] == info
       and info["firmware_compatibility"] == "unknown" and info["bootloader"] == "stock",
       f"{fid:06X}: measured hardware in standalone report and backup metadata")

dev = FakeUboot(now, fid=R.UCUN_FLASH)
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "UCUN write requires opt-in")
except R.Fail as e:
    ok("experimental" in str(e) and "Backup verified" in str(e) and not dev.erased and not dev.writes,
       "UCUN default: backup verified, experimental write refused")
dev = FakeUboot(now, fid=R.UCUN_FLASH)
ok(R.rescue(R.Uboot(dev), image, LOADER, True, tmp, allow_ucun=True, say=lambda *a, **k: None)
   and dev.ran and bytes(dev.flash) == image + now[R.APP_END:]
   and all(R.WRITE_MIN <= a < R.APP_END for a in dev.erased + dev.writes),
   "UCUN opt-in: simulated recovery, protected regions retained")
dev = FakeUboot(now, fid=R.UCUN_FLASH)
try:
    R.rescue(R.Transporter(FakeTransporter(dev)), image, LOADER, True, tmp,
             allow_ucun=True, say=lambda *a, **k: None)
    ok(False, "UCUN adapter writes refused")
except R.Fail as e:
    ok("direct USB" in str(e) and not dev.erased and not dev.writes,
       "UCUN adapter: opt-in still cannot enable unverified adapter writes")

for key, fid in ((0x1234, 0xB36014), (0x980F, 0), (0x980F, 0xFFFFFF), (0x980F, 0xB36015)):
    dev = FakeUboot(now, key=key, fid=fid)
    steps = []
    try:
        R.inspect_backup(R.Uboot(dev), LOADER, tmp, say=lambda *a, **k: None,
                         step=lambda *a: steps.append(a))
        ok(False, f"unknown {key:04X}/{fid:06X} refused")
    except R.Fail:
        report = json.loads(Path(steps[0][1]["report"]).read_text())
        ok(not dev.erased and not dev.writes and not any(k == "backup_read" for k, _ in steps)
           and not report["hardware"]["known_fm1_part"],
           f"unknown {key:04X}/{fid:06X}: report saved, no flash read or write")

# A backup must not be called verified, or permit writes, if disk readback is corrupt.
dev = FakeUboot(now, fid=R.UCUN_FLASH)
real_open = open
def corrupt_saved(path, mode="r", *a, **kw):
    if str(path).endswith(".bin") and mode == "rb":
        import io
        return io.BytesIO(b"corrupt disk readback")
    return real_open(path, mode, *a, **kw)
with patch("builtins.open", side_effect=corrupt_saved):
    try:
        R.rescue(R.Uboot(dev), image, LOADER, True, tmp, allow_ucun=True, say=lambda *a, **k: None)
        ok(False, "UCUN corrupt saved backup refused")
    except R.Fail as e:
        ok("saved backup did not verify" in str(e) and not dev.erased and not dev.writes,
           "UCUN corrupt saved backup: refused before erase/program")

for case in ("type", "head", "key", "calibration"):
    image, now = flashes()
    if case == "head":
        now = bytearray(now)
        now[0x1234] ^= 1
    if case == "key":
        image = bytearray(image)
        app_head(image, key=0x1234)
    if case == "calibration":
        now = bytearray(now)
        now[R.TRIM_AT:R.TRIM_AT + len(R.TRIM_MARK)] = R.TRIM_MARK
    dev = FakeUboot(now, fid=R.UCUN_FLASH, typ=2 if case == "type" else 3)
    try:
        R.rescue(R.Uboot(dev), image, LOADER, True, tmp, allow_ucun=True, say=lambda *a, **k: None)
        ok(False, f"UCUN {case}: safety gate retained")
    except R.Fail:
        ok(not dev.erased and not dev.writes, f"UCUN {case}: safety gate retained with opt-in")

# ---- M-VAVE's SMK-37: the FM-1's chip, but never an FM-1 firmware on it
dev = FakeUboot(now, fid=R.KEYBOARD_FLASH)
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "an SMK-37 (its flash EB6014): refused")
except R.Fail as e:
    ok("SMK-37" in str(e) and not dev.erased, "an SMK-37 (its flash EB6014): refused")
smk = bytearray(now)
smk[R.TRIM_AT:R.TRIM_AT + 8] = R.TRIM_MARK
dev = FakeUboot(smk)
steps = []
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None, strict_head=False,
             step=lambda k, v=None: steps.append((k, v)))
    ok(False, "an SMK-37 with the FM-1's flash part (its calibration): refused, backed up")
except R.Fail as e:
    backups = [v for k, v in steps if k == "backup"]
    ok("SMK-37" in str(e) and not dev.erased and not dev.writes and len(backups) == 1,
       "an SMK-37 with the FM-1's flash part (its calibration): refused, backed up")
    for b in backups:
        os.remove(b)
        os.remove(b + ".sha256")

# ---- the newer FM-1s' flash (UC25HQ80, B36014): rescued as the first ones; an SMK-37 on it still not
image, now = flashes()
dev = FakeUboot(now, fid=0xB36014)
steps = []
ok(R.rescue(R.Uboot(dev), image, LOADER, True, tmp, allow_ucun=True, say=lambda *a, **k: None,
            step=lambda k, v=None: steps.append((k, v))) and bytes(dev.flash) == image + now[R.APP_END:],
   "the newer FM-1s' flash (B36014): rescued with opt-in, the device data kept")
for b in [v for k, v in steps if k == "backup"]:
    os.remove(b)
    os.remove(b + ".sha256")
smk = bytearray(now)
smk[R.TRIM_AT:R.TRIM_AT + 8] = R.TRIM_MARK
dev = FakeUboot(smk, fid=0xB36014)
steps = []
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None, strict_head=False,
             step=lambda k, v=None: steps.append((k, v)))
    ok(False, "an SMK-37 on the newer flash part (its calibration): refused")
except R.Fail as e:
    ok("SMK-37" in str(e) and not dev.erased and not dev.writes, "an SMK-37 on the newer flash part (its calibration): refused")
for b in [v for k, v in steps if k == "backup"]:
    os.remove(b)
    os.remove(b + ".sha256")

# ---- any FM-1 firmware: its app area, the FM-1's own head kept; refused for another chip key
image, now = flashes()
other = bytearray(image)
other[0x100:0x110] = b"another SPL head"                   # a Felucca package's head is the SDK's
dev = FakeUboot(now)
R.rescue(R.Uboot(dev), bytes(other), LOADER, True, tmp, say=lambda *a, **k: None, strict_head=False)
ok(bytes(dev.flash) == now[:R.WRITE_MIN] + image[R.WRITE_MIN:] + now[R.APP_END:] and min(dev.writes) >= R.WRITE_MIN,
   "any firmware: its app area written, the FM-1's own head kept")
dev = FakeUboot(now)
R.rescue(R.Uboot(dev), bytes(other), LOADER, True, tmp, say=lambda *a, **k: None)
ok(bytes(dev.flash)[:R.WRITE_MIN] == now[:R.WRITE_MIN], "V15 too: the package's head is never written")
wrongkey = bytearray(image)
app_head(wrongkey, key=0x1234)
dev = FakeUboot(now)
try:
    R.rescue(R.Uboot(dev), bytes(wrongkey), LOADER, True, tmp, say=lambda *a, **k: None, strict_head=False)
    ok(False, "a firmware for another chip key: refused")
except R.Fail as e:
    ok("not for this chip" in str(e) and not dev.erased, "a firmware for another chip key: refused")
for kind, fel in (("felucca", True), ("other", False)):
    pth = os.path.join(tmp, f"{kind}.fwsc")
    open(pth, "wb").write(make_fwsc(image + bytes(64), "FM-1_846", fel))
    got = R.load_package(pth)
    ok(got[0] == image and got[1:] == (kind, "FM-1_846"), f"load_package: an FM-1 package, {kind}")
pth = os.path.join(tmp, "x.fwsc")
open(pth, "wb").write(make_fwsc(image, "XYZ-2_5"))
try:
    R.load_package(pth)
    ok(False, "load_package: another model refused")
except R.Fail:
    ok(True, "load_package: another model refused")
real = Path("/mnt/project-files/wild-firmware/Wild-0.3.11.fwsc")
if real.exists():                                          # a real Wild package (on the author's setup)
    im, k, prod = R.load_package(str(real))
    ok(k == "felucca" and prod == "FM-1_845" and R.app_key_ok(im, 0x980F) and not R.app_key_ok(im, 0x1234),
       "a real Wild package: Felucca, FM-1_845, the FM-1's chip key")

# ---- EXPERIMENTAL: through an FM-1-transporter (its serial protocol)
for state in ("uboot", "v15", "off"):
    image, now = flashes()
    dev = FakeUboot(now)
    port = FakeTransporter(dev, state)
    reopened = []

    def reopen(port=port):
        reopened.append(1)
        if len(reopened) < 3:                              # the adapter restarting: not there yet
            return None
        port.state = "uboot"                               # the FM-1 switched on: USB_KEY caught it
        return port
    t = R.Transporter(port, reopen=reopen)
    t.ensure_uboot(5, say=lambda *a, **k: None)
    done = R.rescue(t, image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(done and bytes(dev.flash) == image + now[R.APP_END:] and dev.erased == [0x92000, 0x40000, 0x6000, 0x5000]
       and dev.ran and port.lines.count("ldr") == R.LOADER_SIZE // 4096 + 1,
       f"transporter ({state}): loader, info, read, the 4 sectors high to low, runapp")
    ok(("rekey" in port.lines) == (state == "off") and ("uboot" in port.lines) == (state == "v15"),
       f"transporter ({state}): " + {"uboot": "already there", "v15": "V15's soft key",
                                     "off": "rekey, then the FM-1's power-on"}[state])
port = FakeTransporter(FakeUboot(now), "off")
try:
    R.Transporter(port).ensure_uboot(0.3, say=lambda *a, **k: None)
    ok(False, "transporter: it never shows up: a clear failure")
except R.Fail as e:
    ok("did not appear" in str(e), "transporter: it never shows up: a clear failure")
try:
    R.Transporter(FakeTransporter(FakeUboot(now))).write_sector(0x1000, bytes(R.SECTOR))
    ok(False, "transporter: a sector below 0x4000 refused here too")
except R.Fail:
    ok(True, "transporter: a sector below 0x4000 refused here too")
ok(R.find_transporter(["COM7", "COM9"], lambda p: None if p == "COM7" else "port9") == "port9",
   "transporter: the port that answers ping")

# ---- finding it on Windows without an administrator: by the disk's vendor and product
ok(R.windows_uboot_disks(FakeWin32(FakeUboot(now))) == [r"\\.\PhysicalDrive2"],
   "Windows: the UBOOT disk found among the disks")
ok(R.is_uboot_disk("WL82", "") and R.is_uboot_disk("", "WL80UBOOT1.00") and not R.is_uboot_disk("Samsung", "SSD"),
   "Windows: what counts as UBOOT")

# ---- the window's view of a run as root / administrator
pf = R.ProgressFile(os.path.join(tmp, "p.jsonl"))
pf.say("  found it after 1.0 s")
pf.say("\r  writing 3/9", end="")
pf.step("write", 33)
ev, pos = R.read_progress(pf.path)
pf.put(end="ok")
ev2, _ = R.read_progress(pf.path, pos)
ok(ev == [{"say": "found it after 1.0 s"}, {"step": "write", "value": 33}] and ev2 == [{"end": "ok"}],
   "progress file: messages, steps and the end, read on")
ok(not pf.stopped() and (open(pf.path + ".stop", "w").close() or pf.stopped()), "progress file: Stop")

# ---- the files: only M-VAVE's V15 and JieLi's loader as published
p = os.path.join(tmp, "not-v15.fwsc")
open(p, "wb").write(b"x" * 1000)
try:
    R.load_stock(p)
    ok(False, "another .fwsc as V15: refused")
except R.Fail:
    ok(True, "another .fwsc as V15: refused")
ok(R.find_stock([tmp]) is None, "find_stock: not by its name")
lp = os.path.join(tmp, "l.bin")
open(lp, "wb").write(LOADER)
try:
    R.get_loader(lp)
    ok(False, "another loader: refused")
except R.Fail:
    ok(True, "another loader: refused")

# ---- on a real Windows (CI): the kernel32 calls load and look at the disks without an administrator
if sys.platform == "win32":
    ok(R.windows_uboot_disks() == [] and R.uboot_present() is False and R.is_admin() in (True, False),
       "Windows: the disks looked at, no FM-1 among them")

# Approval denial must not signal readiness or start a device wait.
progress = str(Path(tmp) / "denied.jsonl")
with patch.object(R, "load_package", return_value=(image, "v15", "FM-1_015")), \
     patch.object(R, "get_loader", return_value=LOADER), \
     patch.object(R, "get_mflr", return_value=LOADER), \
     patch.object(R, "is_admin", return_value=False), \
     patch.object(R, "open_device") as opened:
    code = R.main(["fixture.fwsc", "--write", "--progress", progress])
events, _ = R.read_progress(progress)
ok(code == 1 and not opened.called and not any(e.get("step") == "ready" for e in events),
   "permission denied: no readiness, device timer or writes")

# Both direct USB backends refuse an ambiguous target before opening it.
fake_usb = types.ModuleType("usb")
fake_usb.core, fake_usb.util = types.ModuleType("usb.core"), types.ModuleType("usb.util")
fake_usb.core.find = lambda **kw: [object(), object()]
with patch.dict(sys.modules, {"usb": fake_usb, "usb.core": fake_usb.core, "usb.util": fake_usb.util}), \
     patch.object(R.sys, "platform", "darwin"), patch.object(R, "backend", return_value=object()), \
     patch.object(R, "Bot") as opened:
    try:
        R.open_device(1, say=lambda *a, **k: None)
        ok(False, "multiple USB UBOOT devices refused")
    except R.Fail as e:
        ok("multiple" in str(e) and not opened.called, "multiple USB UBOOT devices: no target opened")
with patch.object(R.sys, "platform", "win32"), \
     patch.object(R, "windows_uboot_disks", return_value=["device1", "device2"]), \
     patch.object(R, "WinScsi") as opened:
    try:
        R.open_device(1, say=lambda *a, **k: None)
        ok(False, "multiple UBOOT devices refused")
    except R.Fail as e:
        ok("multiple" in str(e) and not opened.called, "multiple UBOOT devices: no arbitrary target opened")
# --inspect uses no package or stock download and cannot be combined with writing.
dev = FakeUboot(now, fid=R.UCUN_FLASH)
progress = str(Path(tmp) / "inspect.jsonl")
with patch.object(R, "get_loader", return_value=LOADER), \
     patch.object(R, "is_admin", return_value=True), \
     patch.object(R.sys, "platform", "win32"), \
     patch.object(R, "open_device", return_value=dev), \
     patch.object(R, "load_package") as loaded, patch.object(R, "download_stock") as downloaded:
    code = R.main(["--inspect", "--backup-dir", tmp, "--progress", progress, "--tries", "1", "--loader-kind", "jieli"])
events, _ = R.read_progress(progress)
ok(code == 0 and not loaded.called and not downloaded.called and not dev.erased and not dev.writes
   and events[-1]["operation"] == "inspect" and Path(events[-1]["backup"]).exists(),
   "CLI inspect: backup without package/download/erase/program")
for extra in (["--write"], ["--ask"], ["--allow-ucun-write"], ["firmware.fwsc"]):
    with patch.object(R, "open_device") as opened:
        try:
            R.main(["--inspect", *extra])
            ok(False, f"inspect rejects {extra}")
        except SystemExit as e:
            ok(e.code == 2 and not opened.called, f"inspect rejects {extra} before device access")

# ---- does the chosen firmware support the measured flash? (the constants of its exact-ID gate)
image, now = flashes()
puya = with_flash_ids(image, [0x856014])
both = with_flash_ids(image, [0x856014, 0xB36014])
ok(R.app_key_ok(puya, 0x980F) and R.app_key_ok(both, 0x980F), "flash IDs placed: the app head still decrypts")
ok(R.flash_support(puya, 0xB36014, "felucca")[0] == "unsupported"
   and R.flash_support(puya, 0x856014, "felucca")[0] == "supported",
   "Puya-only firmware: UCUN unsupported, Puya supported")
ok(R.flash_support(both, 0xB36014, "felucca")[0] == "supported", "firmware with both IDs: UCUN supported")
ok(R.flash_support(image, 0xB36014, "felucca")[0] == "unknown", "no ID constant at all: unknown, not unsupported")
ok(R.flash_support(puya, 0xB36014, "other")[0] == "unknown", "non-Felucca firmware with only 856014: unknown")
ok(R.flash_support(image, 0xB36014, "v15")[0] == "supported", "M-VAVE's V15: UCUN supported (upstream reports)")
ok(R.flash_support(puya, 0xB36014, "felucca", key=0x1234)[0] == "unknown",
   "the app is read with the chip key: another key finds no IDs")

if True:
    dev = FakeUboot(now, fid=0xB36014)
    steps = []
    try:
        R.rescue(R.Uboot(dev), puya, LOADER, True, tmp, allow_ucun=True, kind="felucca",
                 say=lambda *a, **k: None, step=lambda k, v=None: steps.append((k, v)))
        ok(False, "UCUN unit + Puya-only firmware: blocked before any flash read")
    except R.Fail as e:
        ok("does not support" in str(e) and not dev.erased and not dev.writes
           and not [k for k, _ in steps if k in ("backup", "backup_read")]
           and ("compat", {"support": "unsupported", "reason": steps[-1][1]["reason"]}) == steps[-1],
           "UCUN unit + Puya-only firmware: blocked before any flash read")
dev = FakeUboot(now, fid=0xB36014)
steps = []
ok(R.rescue(R.Uboot(dev), both, LOADER, True, tmp, allow_ucun=True, kind="felucca", say=lambda *a, **k: None,
            step=lambda k, v=None: steps.append((k, v))) and bytes(dev.flash) == both + now[R.APP_END:]
   and ("compat", "supported") in [(k, v and v.get("support")) for k, v in steps if k == "compat"],
   "UCUN unit + firmware with UCUN support: rescued")
for b in [v for k, v in steps if k == "backup"]:
    os.remove(b)
    os.remove(b + ".sha256")
dev = FakeUboot(now)
steps = []
ok(R.rescue(R.Uboot(dev), puya, LOADER, True, tmp, kind="felucca", say=lambda *a, **k: None,
            step=lambda k, v=None: steps.append((k, v))) and bytes(dev.flash) == puya + now[R.APP_END:],
   "Puya unit + Puya-only firmware: rescued as before")
for b in [v for k, v in steps if k == "backup"]:
    os.remove(b)
    os.remove(b + ".sha256")
said = []
dev = FakeUboot(now, fid=0xB36014)
ok(R.rescue(R.Uboot(dev), image, LOADER, False, tmp, allow_ucun=True, kind="other", say=said.append) is False
   and any("not known" in str(t) for t in said),
   "UCUN unit + firmware without IDs: warned before the backup")

# ---- M-Flasher's loader (MFLR): started in a free RAM slot, the chip's CRC32 checks the writes
def mflr_image(slot, version=b"2.3"):
    return random.Random(slot).randbytes(0x300) + b"MFLR" + version + bytes(9) + random.Random(slot + 9).randbytes(5000)


MFLR = R.MflrLoader([("mflr-loader-a.bin", 0x01C20000, mflr_image(1)), ("mflr-loader-b.bin", 0x01C28000, mflr_image(2))])


class FakeMflr(FakeUboot):
    """FakeUboot whose ROM also starts M-Flasher's loader: its builds for RAM A and B (uploaded scrambled,
    each 512-byte block on its own), then its own commands as well as the flash ones"""

    def __init__(self, flash, running=None, has_flash=True, **kw):
        super().__init__(flash, **kw)
        self.mflr_at, self.jumps, self.shown, self.crcs = None, [], [], 0
        self.has_flash = has_flash
        if running == "jieli":
            self.mode = "loader"
        elif running:
            self.mode, self.mflr_at = "mflr", running
        self.last_error = 0

    def xfer(self, cdb, data_out=None, n_in=0, timeout=5000):
        cdb = bytes(cdb)
        cmd = struct.unpack(">H", cdb[:2])[0] if cdb[0] != 0x12 else None
        a = cdb[2:]
        mflr_cmds = {R.CMD_MFLR_IDENTIFY, R.CMD_MFLR_CRC32, R.CMD_MFLR_UNIQUE_ID, R.CMD_MFLR_DISPLAY_ON,
                     R.CMD_MFLR_DISPLAY_TEXT, R.CMD_MFLR_DISPLAY_BAR, R.CMD_MFLR_LAST_ERROR, R.CMD_USB_BUFFER_SIZE}
        if cmd == R.CMD_JUMP and self.mode in ("rom", "mflr") and \
                struct.unpack(">I", a[:4])[0] in [at for _, at, _ in MFLR.slots]:
            addr = struct.unpack(">I", a[:4])[0]
            for _, at, img in MFLR.slots:
                if addr == at:
                    got = bytearray(b"".join(self.ram[k] for k in sorted(self.ram) if at <= k < at + 0x8000))
                    for off in range(0, len(got), 512):          # each block scrambled on its own
                        blk = got[off:off + 512]
                        R.crc_cipher(blk)
                        got[off:off + 512] = blk
                    if bytes(got) == img:
                        self.mode, self.mflr_at = "mflr", at
                        self.jumps.append(at)
            return self.reply(cmd)
        if self.mode == "mflr" and cmd == R.CMD_WRITE_MEMORY:
            addr, n = struct.unpack(">IH", a[:6])
            self.ram[addr] = bytes(data_out)
            return b""
        if cmd in mflr_cmds:
            if self.mode != "mflr":
                raise R.Refused("command failed (status 2)")
            if cmd == R.CMD_MFLR_IDENTIFY:
                return self.reply(cmd, b"MFLR2.3\0" + struct.pack(">I", self.mflr_at) + b"\0\xF0")
            if cmd == R.CMD_MFLR_CRC32:
                addr, n = struct.unpack(">II", a[:8])
                self.crcs += 1
                return self.reply(cmd, struct.pack(">I", zlib.crc32(bytes(self.flash[addr:addr + n]))))
            if cmd == R.CMD_MFLR_UNIQUE_ID:
                return self.reply(cmd, bytes.fromhex("0123456789abcdef"))
            if cmd == R.CMD_MFLR_LAST_ERROR:
                e, self.last_error = self.last_error, 0
                return self.reply(cmd, bytes([e]))
            if cmd == R.CMD_USB_BUFFER_SIZE:
                return self.reply(cmd, struct.pack(">I", 4096))
            if cmd == R.CMD_MFLR_DISPLAY_TEXT:
                self.shown.append(bytes(data_out).decode().strip())
                return b""
            return self.reply(cmd)
        if self.mode == "mflr":
            if cmd == R.CMD_READ_FLASH and not self.has_flash:
                self.last_error = R.MFLR_OUT_OF_RANGE
                raise R.Refused("command failed (status 2)")
            mode, self.mode = self.mode, "loader"
            try:
                return super().xfer(cdb, data_out, n_in, timeout)
            finally:
                self.mode = mode
        return super().xfer(cdb, data_out, n_in, timeout)


image, now = flashes()
dev = FakeMflr(now)
up = R.Uboot(dev)
steps = []
ok(R.rescue(up, image, MFLR, True, tmp, say=lambda *a, **k: None, step=lambda k, v=None: steps.append((k, v)))
   and bytes(dev.flash) == image + now[R.APP_END:], "MFLR: rescued through M-Flasher's loader")
ok(dev.jumps == [0x01C20000] and up.mflr == "2.3" and up.read_io == 16384 and up.write_io == 4096,
   "MFLR: started in RAM A, scrambled block by block; large reads")
ok(dev.crcs >= 1 + 4, "MFLR: the chip's CRC32 checks the reads and each written sector")
info = [v for k, v in steps if k == "hardware"][0]
backup = [v for k, v in steps if k == "backup"][0]
meta = json.loads(Path(backup + ".json").read_text())
ok(meta["hardware"]["unique_id"] == "0123456789abcdef" and meta["chip_crc32_checked"]
   and meta["hardware"]["bootloader_text"] == "M-VAVE's (stock)", "MFLR: the flash's unique ID and the analysis saved")
ok(any("DO NOT SWITCH OFF" in t for t in dev.shown) and any("DONE - VERIFIED" in t for t in dev.shown),
   "MFLR: the FM-1's own screen says what happens")
ana = [v for k, v in steps if k == "analysis"]
ok(ana and len(ana[0]["map"]) == 256 and ana[0]["map"][0] == "boot", "MFLR: the flash map for the window")
dev = FakeMflr(now, running=0x01C20000)
up = R.Uboot(dev)
up.start_loader(MFLR)
ok(dev.jumps == [] and up.mflr == "2.3", "MFLR: a loader of ours that runs is used again")
dev = FakeMflr(now, running="jieli")
try:
    R.Uboot(dev).start_loader(MFLR)
    ok(False, "MFLR: refused while JieLi's loader runs")
except R.Fail as e:
    ok("another flash loader" in str(e) and not dev.jumps, "MFLR: refused while JieLi's loader runs")
dev = FakeMflr(now, has_flash=False)
try:
    R.Uboot(dev).start_loader(MFLR)
    ok(False, "MFLR: a loader that found no flash: refused")
except R.Fail as e:
    ok("did not identify itself" in str(e) and dev.jumps == [0x01C20000, 0x01C28000],
       "MFLR: no flash found: started again in RAM B once, then refused")
dev = FakeMflr(now)
up = R.Uboot(dev)
up.start_loader(MFLR)
plain_read = up.read
up.read = lambda addr, n: (lambda d: d[:0x50000] + bytes([d[0x50000] ^ 1]) + d[0x50001:])(plain_read(addr, n))
reads = []
try:
    R.inspect_backup(up, MFLR, tmp, say=lambda *a, **k: None, step=lambda *a: reads.append(a))
    ok(False, "MFLR: reads that disagree with the chip's CRC32: refused")
except R.Fail as e:
    ok("chip's own checksum" in str(e), "MFLR: reads that disagree with the chip's CRC32: refused")

# ---- FM-1 016: a longer image (to 0xA9000), M-VAVE's download either V15 or 016
long_img = bytearray(random.Random(16).randbytes(R.APP_MAX))
long_img[:R.WRITE_MIN] = boot_area()
app_head(long_img)
dev = FakeMflr(now)
ok(R.rescue(R.Uboot(dev), bytes(long_img), MFLR, True, tmp, say=lambda *a, **k: None)
   and bytes(dev.flash) == now[:R.WRITE_MIN] + bytes(long_img[R.WRITE_MIN:]) + now[R.APP_MAX:]
   and max(dev.erased) < R.APP_MAX, "016: written up to 0xA9000, nothing after it")
up = R.Uboot(FakeMflr(now))
up.app_end = R.APP_MAX + 0x1000
try:
    up.start_loader(MFLR)
    up.write_sector(R.APP_MAX, bytes(R.SECTOR))
    ok(False, "016: never past 0xA9000, whatever the image")
except R.Fail as e:
    ok("refusing to write" in str(e), "016: never past 0xA9000, whatever the image")
pkg016 = make_fwsc(bytes(long_img), "FM-1_016")
with patch.object(R, "STOCK_FM1", {R.STOCK_SHA256: "V15", R.sha256(pkg016): "016"}), \
        patch.object(R, "fetch", lambda url, timeout=60: pkg016):
    d = tempfile.mkdtemp(prefix="fm1-016-")
    p016 = R.download_stock(d, say=lambda *a, **k: None)
    img, kind, product = R.load_package(p016)
    ok(os.path.basename(p016) == "FM-1-016.fwsc" and kind == "v15" and product == "FM-1_016"
       and img == bytes(long_img) and R.find_stock([d]) == p016,
       "016: M-VAVE's download taken as 016, its whole image")
with patch.object(R, "fetch", lambda url, timeout=60: b"something else" * 300):
    try:
        R.download_stock(tempfile.mkdtemp(), say=lambda *a, **k: None)
        ok(False, "M-VAVE's download: anything else is refused")
    except R.Fail as e:
        ok("neither V15 nor 016" in str(e), "M-VAVE's download: anything else is refused")
layouts = []
v016 = bytearray(long_img)
dev = FakeMflr(now)
with patch.object(R.FL, "layout", lambda f, key=0x980F: "016" if f[R.APP_END + 5] == v016[R.APP_END + 5] else "015"):
    R.rescue(R.Uboot(dev), bytes(v016), MFLR, False, tmp, say=lambda *a, **k: None,
             step=lambda k, v=None: layouts.append((k, v)))
ok(("layout", {"from": "015", "to": "016"}) in layouts, "015 to 016: the other data layout is pointed out")

# the command line takes M-Flasher's loader by default over USB, JieLi's when asked
for kind_args, want in (([], "mflr"), (["--loader-kind", "jieli"], "jieli")):
    used = []
    dev = FakeMflr(now)
    with patch.object(R, "get_mflr", side_effect=lambda *a, **k: used.append("mflr") or MFLR), \
         patch.object(R, "get_loader", side_effect=lambda *a, **k: used.append("jieli") or LOADER), \
         patch.object(R, "is_admin", return_value=True), patch.object(R.sys, "platform", "win32"), \
         patch.object(R, "open_device", return_value=dev):
        code = R.main(["--inspect", "--backup-dir", tmp, "--tries", "1", *kind_args])
    ok(code == 0 and used == [want], f"CLI: {want} loader {'by default' if not kind_args else 'when asked'}")

# ---- Protect my FM-1: the rescue bootloader in the boot area, nothing else changed
MB = open_boot(R.MBOOT_VERSION)
quiet = lambda *a, **k: None                                              # noqa: E731


def protect_on(flash, write=True, **kw):
    d = FakeMflr(flash, **{k: kw.pop(k) for k in ("fid",) if k in kw})
    up = R.Uboot(d)
    st = []
    try:
        r = R.protect(up, kw.pop("boot", MB), MFLR, write, tmp, say=quiet, step=lambda k, v=None: st.append((k, v)), **kw)
    except R.Fail as e:
        r = e
    return r, d, up, st


_, base = flashes()
r, d, up, st = protect_on(base)
new = bytes(d.flash)
ok(r == "installed" and new[R.WRITE_MIN:] == base[R.WRITE_MIN:], "protect: installed, nothing outside the boot area changed")
ok(FL.bootloader_info(new)[0] == "open" and FL.bootloader_info(new)[2] == R.MBOOT_VERSION
   and FL.check_boot_area(new) is None, "protect: m-bootloader stored, and the boot area can start the FM-1")
ok(new[FL.CONFIG_AT:FL.CONFIG_AT + FL.CONFIG_KEY] == base[0x38D0:0x38D0 + FL.CONFIG_KEY],
   "protect: the FM-1's own chip-key record kept")
ok(d.erased and all(a < R.WRITE_MIN for a in d.erased) and d.erased[-1] == 0 and d.ran,
   "protect: only boot sectors erased, the header's last, then restarted")
plan_ = [v for k, v in st if k == "plan"][0]
ok([k for k, _ in st][-1] == "done" and any(k == "backup" for k, _ in st) and set(plan_["sectors"]) <= {0, 0x1000, 0x2000, 0x3000}
   and len(plan_["map"]) == 256, "protect: backup first, a plan with the sectors and the map, then done")
ok(up.boot_write is False, "protect: the boot-area permission is closed again")
r, d, _, st = protect_on(new)
ok(r == "already" and not d.erased and ("already", R.MBOOT_VERSION) in st, "protect: already protected, nothing written")
r, d, _, st = protect_on(base, write=False)
ok(r is False and not d.erased and any(k == "plan" for k, _ in st), "protect: without --write it checks and writes nothing")
older = R.protect(R.Uboot(FakeMflr(new)), open_boot("2.5-new"), MFLR, True, tmp, say=quiet)
ok(older == "installed", "protect: another rescue bootloader version is replaced")
broken = bytearray(base)
broken[:R.WRITE_MIN] = unit_flash(key_ok=False)[:R.WRITE_MIN]
r, d, _, _ = protect_on(bytes(broken))
ok(isinstance(r, R.Fail) and "chip-key" in str(r) and not d.erased, "protect: a damaged chip-key record is refused")
r, d, _, _ = protect_on(base, boot=open_boot("2.4-x", board="smk37"))
ok(isinstance(r, R.Fail) and "FM-1" in str(r) and not d.erased, "protect: another board's build is refused")
r, d, _, _ = protect_on(base, boot=open_boot("2.4-x", size=0x3F80))
ok(isinstance(r, R.Fail) and not d.erased, "protect: a bootloader that does not fit is refused")
kb = bytearray(base)
kb[R.TRIM_AT:R.TRIM_AT + len(R.TRIM_MARK)] = R.TRIM_MARK
r, d, _, _ = protect_on(bytes(kb))
ok(isinstance(r, R.Fail) and "keyboard" in str(r) and not d.erased, "protect: an SMK-37 is refused")
r, d, _, _ = protect_on(base, fid=R.UCUN_FLASH)
ok(isinstance(r, R.Fail) and "UCUN" in str(r) and not d.erased, "protect: UCUN needs its explicit opt-in")
r, d, _, _ = protect_on(base, fid=R.UCUN_FLASH, allow_ucun=True)
ok(r == "installed", "protect: and installs with it")
try:
    R.protect(R.Uboot(FakeMflr(base, fid=R.UCUN_FLASH)), MB, MFLR, False, tmp, say=quiet, confirm=lambda: True)
    ok(False, "protect: a plain yes does not allow the UCUN flash")
except R.Fail as e:
    ok("UCUN" in str(e), "protect: a plain yes does not allow the UCUN flash")
ok(R.protect(R.Uboot(FakeMflr(base, fid=R.UCUN_FLASH)), MB, MFLR, False, tmp, say=quiet, confirm=lambda: "ucun")
   == "installed", "protect: the window's yes with UCUN allowed installs it")
pfp = str(Path(tmp) / "answer.jsonl")
pf = R.ProgressFile(pfp)
R.answer(pfp)
ok(pf.answer(poll=0) is True, "protect: the window's answer file: go")
R.answer(pfp, ucun=True)
ok(pf.answer(poll=0) == "ucun", "protect: go ucun")
open(pfp + ".stop", "w").close()
ok(pf.answer(poll=0) is False, "protect: stop wins")
up2 = R.Uboot(FakeMflr(base))
try:
    up2.write_boot_sector(0, bytes(R.SECTOR))
    ok(False, "protect: no boot-area write outside protect()")
except R.Fail:
    ok(True, "protect: no boot-area write outside protect()")
try:
    up2.write_sector(0x3000, bytes(R.SECTOR))
    ok(False, "rescue: still never below 0x4000")
except R.Fail:
    ok(True, "rescue: still never below 0x4000")
d = FakeMflr(base)
up3 = R.Uboot(d)
real = up3._program
up3._program = lambda a, data: (_ for _ in ()).throw(R.Fail("sector did not verify")) if a == 0 else real(a, data)
try:
    R.protect(up3, MB, MFLR, True, tmp, say=quiet)
    ok(False, "protect: a sector that will not verify stops it, the permission closed")
except R.Fail as e:
    ok("restore the backup" in str(e) and up3.boot_write is False, "protect: a sector that will not verify stops it, the permission closed")
with patch.object(R, "fetch", return_value=b"not it"):
    try:
        R.get_mbootloader(None, quiet)
        ok(False, "protect: a download that is not m-bootloader 2.4 is refused")
    except R.Fail:
        ok(True, "protect: a download that is not m-bootloader 2.4 is refused")
cache = tempfile.mkdtemp()
with patch.object(R, "fetch", return_value=MB), patch.object(R, "MBOOT_SHA256", R.sha256(MB)):
    got = R.get_mbootloader(cache, quiet)
    with patch.object(R, "fetch", side_effect=AssertionError("fetched twice")):
        ok(got == MB and R.get_mbootloader(cache, quiet) == MB, "protect: the checked download is kept and reused")
for bad in (["--protect", "fixture.fwsc"], ["--protect", "--inspect"], ["--protect", "--via", "transporter"]):
    try:
        with patch("sys.stderr"):
            R.main(bad)
        ok(False, "protect: " + " ".join(bad) + " is refused")
    except SystemExit as e:
        ok(e.code == 2, "protect: " + " ".join(bad) + " is refused")
prog = str(Path(tmp) / "protect.jsonl")
with patch.object(R, "get_mbootloader", return_value=MB), patch.object(R, "get_mflr", return_value=MFLR), \
        patch.object(R, "is_admin", return_value=True), patch.object(R, "open_device", return_value=FakeMflr(base)), \
        patch.dict(sys.modules, {"usb": types.ModuleType("usb"), "usb.core": types.ModuleType("usb.core")}), \
        patch.object(R, "release"):
    code = R.main(["--protect", "--write", "--backup-dir", tmp, "--progress", prog])
ev, _ = R.read_progress(prog)
end = [e for e in ev if "end" in e]
ok(code == 0 and end and end[-1]["operation"] == "protect" and end[-1]["result"] == "installed",
   "protect: the command line installs it and reports so")

# ---- Restore: this FM-1's own backup back, boot area included
def restore_on(flash, backup, write=True, **kw):
    d = FakeMflr(flash, **{k: kw.pop(k) for k in ("fid",) if k in kw})
    up = R.Uboot(d)
    st = []
    try:
        r = R.restore(up, backup, MFLR, write, tmp, say=quiet, step=lambda k, v=None: st.append((k, v)), **kw)
    except R.Fail as e:
        r = e
    return r, d, up, st


saved = bytes(base)                                    # before Protect: M-VAVE's bootloader
protected = R.protect(R.Uboot(FakeMflr(base)), MB, MFLR, True, tmp, say=quiet) and new
changed = bytearray(protected)
changed[0x50000:0x50010] = b"later presets...."[:16]     # and the user's data has changed since
changed[0xC0000] ^= 0xFF
r, d, up, st = restore_on(bytes(changed), saved)
ok(r == "restored" and bytes(d.flash) == saved, "restore: the whole backup is back, boot area included")
ok(d.erased and all(a < FL.KEY_MAC[0] for a in d.erased) and d.erased[-1] == 0
   and all(a >= R.WRITE_MIN for a in d.erased[:-4]) and d.ran, "restore: data first, the boot area last, header last")
ok(up.full_write is False and up.boot_write is False, "restore: the write permissions are closed again")
pl = [v for k, v in st if k == "plan"][0]
ok(pl["boot"] and "m-bootloader" in pl["from"] and "stock" in pl["to"] and len(pl["map"]) == 256,
   "restore: the plan says the bootloader goes back")
r, d, _, _ = restore_on(bytes(changed), saved, boot_only=True)
ok(r == "restored" and bytes(d.flash) == saved[:R.WRITE_MIN] + bytes(changed)[R.WRITE_MIN:],
   "restore: M-VAVE's bootloader alone back, the firmware and data stay")
r, d, _, _ = restore_on(bytes(changed), protected, boot_only=True)
ok(isinstance(r, R.Fail) and "M-VAVE's own bootloader" in str(r) and not d.erased,
   "restore: boot only needs a backup with M-VAVE's bootloader")
r, d, _, st = restore_on(saved, saved)
ok(r == "already" and not d.erased, "restore: the same flash: nothing written")
r, d, _, st = restore_on(bytes(changed), saved, write=False)
ok(r is False and not d.erased and any(k == "plan" for k, _ in st), "restore: without --write it checks only")
other = bytearray(saved)
other[0xFF010] ^= 0x01
r, d, _, _ = restore_on(bytes(changed), bytes(other))
ok(isinstance(r, R.Fail) and "another FM-1" in str(r) and not d.erased, "restore: another FM-1's backup is refused")
r, d, _, _ = restore_on(bytes(changed), saved, meta={"hardware": {"unique_id": "ffff"}})
ok(isinstance(r, R.Fail) and "unique ID" in str(r) and not d.erased, "restore: another unique ID is refused")
r, d, _, _ = restore_on(bytes(changed), saved, meta={"hardware": {"unique_id": "0123456789abcdef"}})
ok(r == "restored", "restore: the same unique ID is taken")
dead = bytearray(saved)
dead[:R.WRITE_MIN] = unit_flash(key_ok=False)[:R.WRITE_MIN]
r, d, _, _ = restore_on(bytes(changed), bytes(dead))
ok(isinstance(r, R.Fail) and "boot area" in str(r) and not d.erased, "restore: a backup that could not start it is refused")
kbb = bytearray(saved)
kbb[R.TRIM_AT:R.TRIM_AT + len(R.TRIM_MARK)] = R.TRIM_MARK
r, d, _, _ = restore_on(bytes(kbb), bytes(kbb[:R.TRIM_AT]) + b"x" + bytes(kbb[R.TRIM_AT + 1:]))
ok(isinstance(r, R.Fail) and "keyboard" in str(r) and not d.erased, "restore: a keyboard is refused")
r, d, _, _ = restore_on(bytes(changed), saved, fid=R.UCUN_FLASH)
ok(isinstance(r, R.Fail) and "UCUN" in str(r) and not d.erased, "restore: UCUN needs its explicit opt-in")
r, d, _, _ = restore_on(bytes(changed), saved[:-1])
ok(isinstance(r, R.Fail) and not d.erased, "restore: a cut-short backup is refused")
up4 = R.Uboot(FakeMflr(base))
try:
    up4.write_any_sector(0x50000, bytes(R.SECTOR))
    ok(False, "restore: no write outside restore()")
except R.Fail:
    ok(True, "restore: no write outside restore()")
up4.full_write = True
try:
    up4.write_any_sector(FL.KEY_MAC[0], bytes(R.SECTOR))
    ok(False, "restore: never the key and MAC sector")
except R.Fail:
    ok(True, "restore: never the key and MAC sector")
bdir = Path(tempfile.mkdtemp())
bf = bdir / "fm1-backup-x.bin"
bf.write_bytes(saved)
try:
    R.read_backup(str(bf))
    ok(False, "restore: a file without its checksum is refused")
except R.Fail as e:
    ok("checksum" in str(e), "restore: a file without its checksum is refused")
(bdir / "fm1-backup-x.bin.sha256").write_text(R.sha256(saved) + "  fm1-backup-x.bin\n")
ok(R.read_backup(str(bf))[0] == saved, "restore: a backup with its checksum is read")
(bdir / "fm1-backup-x.bin.json").write_text('{"backup_sha256": "00", "hardware": {}}')
try:
    R.read_backup(str(bf))
    ok(False, "restore: a backup that changed since is refused")
except R.Fail as e:
    ok("changed" in str(e), "restore: a backup that changed since is refused")
(bdir / "fm1-backup-x.bin.json").write_text('{"backup_sha256": "%s", "hardware": {"unique_id": "u"}}' % R.sha256(saved))
ok(R.read_backup(str(bf))[1]["hardware"]["unique_id"] == "u", "restore: its description comes with it")
for bad in (["--restore", str(bf), "fixture.fwsc"], ["--restore", str(bf), "--protect"],
            ["--restore", str(bf), "--via", "transporter"], ["--boot-only"]):
    try:
        with patch("sys.stderr"):
            R.main(bad)
        ok(False, "restore: " + " ".join(bad[2:] or bad) + " is refused")
    except SystemExit as e:
        ok(e.code == 2, "restore: " + " ".join(bad[2:] or bad) + " is refused")
(bdir / "fm1-backup-x.bin.json").write_text('{"backup_sha256": "%s", "hardware": {}}' % R.sha256(saved))
prog = str(Path(tmp) / "restore.jsonl")
dev = FakeMflr(bytes(changed))
with patch.object(R, "get_mflr", return_value=MFLR), patch.object(R, "is_admin", return_value=True), \
        patch.object(R, "open_device", return_value=dev), patch.object(R, "release"), \
        patch.dict(sys.modules, {"usb": types.ModuleType("usb"), "usb.core": types.ModuleType("usb.core")}):
    code = R.main(["--restore", str(bf), "--write", "--backup-dir", tmp, "--progress", prog])
ev, _ = R.read_progress(prog)
end = [e for e in ev if "end" in e]
ok(code == 0 and end and end[-1]["operation"] == "restore" and end[-1]["result"] == "restored"
   and bytes(dev.flash) == saved, "restore: the command line puts it back and reports so")

# ---- downloads: a certificate the first check cannot follow is tried with the next
import ssl as _ssl  # noqa: E402
import urllib.error as _ue  # noqa: E402
tried = []


class _R:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"got"


def _open(url, timeout=None, context=None):
    tried.append(context)
    if context is None:
        raise _ue.URLError(_ssl.SSLCertVerificationError("unable to get issuer certificate"))
    return _R()


with patch("urllib.request.urlopen", _open):
    ok(R.fetch("https://x", contexts=[None, "system"]) == b"got" and tried == [None, "system"],
       "download: an unknown issuer is tried with the system's own check")
    try:
        R.fetch("https://x", contexts=[None])
        ok(False, "download: when no check trusts it, the certificate error is told")
    except _ue.URLError as e:
        ok("issuer" in str(e), "download: when no check trusts it, the certificate error is told")
first = next(R._trust())
ok(first is not None and first.verify_mode == _ssl.CERT_REQUIRED and first.check_hostname
   and first.verify_flags & getattr(_ssl, "VERIFY_X509_PARTIAL_CHAIN", 0) == getattr(_ssl, "VERIFY_X509_PARTIAL_CHAIN", 0),
   "download: the first check takes a chain that ends at any trusted certificate, and still verifies")
with patch("urllib.request.urlopen", side_effect=_ue.URLError("offline")):
    try:
        R.fetch("https://x", contexts=[None, "system"])
        ok(False, "download: other errors are not retried")
    except _ue.URLError as e:
        ok("offline" in str(e), "download: other errors are not retried")

# ---- Windows: a drive letter mounted on the UBOOT disk is locked and dismounted first
fw = FakeWin32(FakeUboot(base), volumes={"D": 0, "G": 2})
said = []
ws = R.WinScsi(r"\\.\PhysicalDrive2", fw, say=said.append)
ok((R.FSCTL_LOCK_VOLUME, "G") in fw.calls and (R.FSCTL_DISMOUNT_VOLUME, "G") in fw.calls
   and not any(isinstance(x, tuple) and x[1] == "D" for x in fw.calls) and any("G:" in t for t in said),
   "Windows: the FM-1's drive letter is locked and dismounted, the system disk left alone")
ok(1000 + ord("D") in fw.closed and 1000 + ord("G") not in fw.closed, "Windows: only the FM-1's drive stays held")
ws.close()
ok(1000 + ord("G") in fw.closed and 102 in fw.closed, "Windows: closing gives it back")
ok(R.Uboot(R.WinScsi(r"\\.\PhysicalDrive2", FakeWin32(FakeUboot(base)))).inquiry()[0] == "WL82",
   "Windows: with no drive letter, nothing to lock")

ok("Controlled folder access" in R.CFA_HINT and "FM1-Switch.exe" in R.CFA_HINT,
   "Windows: error 5 tells how to allow the app through Controlled folder access")

print("rescue: all ok" if not failed else f"rescue: {failed} FAILED")
sys.exit(failed != 0)
