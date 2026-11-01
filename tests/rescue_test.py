#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/fm1_rescue.py against a simulated FM-1 in the chip's own update mode (UBOOT), no hardware:
the ROM (memory writes, the jump into the loader) and the loader (key, flash id, erase, write, read)
at the level of the 16-byte commands, over the USB transport's xfer() and over Windows' SCSI
pass-through (the struct as Windows reads it, through a fake DeviceIoControl). Checks the write
policy: a backup of the whole flash first, only the differing sectors in [0x4000, 0x93000), high to
low, nothing when the head differs or the chip is another one, and the final image.
  python3 tests/rescue_test.py"""
import ctypes as C
import os
import random
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fm1_rescue as R  # noqa: E402

failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<64} {'ok' if cond else 'FAIL'}")
    failed += not cond


LOADER = bytes(random.Random(1).randrange(256) for _ in range(1500))


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


def flashes():
    rnd = random.Random(7)
    image = bytearray(rnd.randrange(256) for _ in range(R.APP_END))
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
