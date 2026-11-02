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
import os
import random
import struct
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fm1_rescue as R  # noqa: E402

failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<64} {'ok' if cond else 'FAIL'}")
    failed += not cond


LOADER = bytes(random.Random(1).randrange(256) for _ in range(R.LOADER_SIZE))


class FakeUboot:
    """the FM-1 in UBOOT: a 1 MiB flash, the ROM until the loader is started, then the loader"""

    def __init__(self, flash, key=0x980F, fid=0x856014):
        self.flash = bytearray(flash)
        self.ram = {}
        self.mode = "rom"
        self.key, self.fid = key, fid
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
            raise R.Fail("command failed (status 2)")
        if cmd in (R.CMD_WRITE_MEMORY, R.CMD_WRITE_FLASH):
            addr, n = struct.unpack(">IH", a[:6])
            crc = struct.unpack("<H", a[7:9])[0]
            if data_out is None or len(data_out) != n or R.crc16(data_out) != crc:
                raise R.Fail("command failed (status 2)")
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
            return self.reply(cmd, bytes([3, 0]) + struct.pack("<I", self.fid))
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
        raise R.Fail("command failed (status 2)")


class FakeWin32:
    """DeviceIoControl as Windows' disk driver does it: the struct read from memory, the data
    through its pointer; PhysicalDrive0 is an ordinary disk, PhysicalDrive2 the FM-1"""

    def __init__(self, dev):
        self.C, self.dev, self.calls = C, dev, []

    def open(self, path, rw):
        n = int(path.rsplit("PhysicalDrive", 1)[1])
        return n + 100 if n in (0, 2) else None

    def ioctl(self, h, code, inaddr, n_in, outaddr, n_out):
        self.calls.append(code)
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
        pass


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
    app_head(image)
    now = bytearray(image) + bytes(rnd.randrange(256) for _ in range(R.FLASH_SIZE - R.APP_END))
    for a in (0x5000, 0x6000, 0x40000, 0x92000):                  # the crashing firmware's sectors
        now[a + 17] ^= 0x5A
    return bytes(image), bytes(now)


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
    ok(bytes(dev.flash) == image + now[R.APP_END:], f"{name}: the flash is V15, the device data kept")
    ok(dev.erased == [0x92000, 0x40000, 0x6000, 0x5000], f"{name}: only the 4 differing sectors, high to low")
    ok(all(R.WRITE_MIN <= a < R.APP_END for a in dev.writes), f"{name}: nothing below 0x4000 or from 0x93000")
    ok(dev.ran and steps[-1] == ("done", None) and ("write", 100) in steps, f"{name}: restarted, progress to 100%")
    os.remove(backup[0])

image, now = flashes()
dev = FakeUboot(now)
ok(R.rescue(R.Uboot(dev), image, LOADER, False, tmp, say=lambda *a, **k: None) is False and not dev.erased,
   "without --write: checked and backed up, nothing written")

bad = bytearray(now)
bad[0x1234] ^= 1
dev = FakeUboot(bad)
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "a different head (below 0x4000): refused")
except R.Fail as e:
    ok("not writing" in str(e) and not dev.erased, "a different head (below 0x4000): refused")

dev = FakeUboot(now, fid=0x854015)
try:
    R.rescue(R.Uboot(dev), image, LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "another flash chip: refused")
except R.Fail as e:
    ok("not the FM-1" in str(e) and not dev.erased, "another flash chip: refused")

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
    ok("SMK-37" in str(e) and not dev.erased and not dev.writes and steps and steps[0][0] == "backup",
       "an SMK-37 with the FM-1's flash part (its calibration): refused, backed up")
    os.remove(steps[0][1])

# ---- any FM-1 firmware: its app area, the FM-1's own head kept; refused for another chip key
image, now = flashes()
other = bytearray(image)
other[0x100:0x110] = b"another SPL head"                   # a Felucca package's head is the SDK's
dev = FakeUboot(now)
R.rescue(R.Uboot(dev), bytes(other), LOADER, True, tmp, say=lambda *a, **k: None, strict_head=False)
ok(bytes(dev.flash) == now[:R.WRITE_MIN] + image[R.WRITE_MIN:] + now[R.APP_END:] and min(dev.writes) >= R.WRITE_MIN,
   "any firmware: its app area written, the FM-1's own head kept")
dev = FakeUboot(now)
try:
    R.rescue(R.Uboot(dev), bytes(other), LOADER, True, tmp, say=lambda *a, **k: None)
    ok(False, "V15 (strict head): a different head is still refused")
except R.Fail:
    ok(not dev.erased, "V15 (strict head): a different head is still refused")
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

print("rescue: all ok" if not failed else f"rescue: {failed} FAILED")
sys.exit(failed != 0)
