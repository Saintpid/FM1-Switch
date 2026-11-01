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
  2. the chip key must be 980F and the flash 856014 (1 MiB), or nothing is done;
  3. the whole flash is read and saved as a backup before anything else;
  4. only 4 KiB sectors in [0x4000, 0x93000) that differ from V15 are written (the bootloader below
     0x4000 and the device data above 0x93000 are never touched; a difference below 0x4000 stops
     it), each erased, written and read back;
  5. a final full read must equal the expected image; then the FM-1 restarts into V15.
"""
import argparse
import hashlib
import os
import struct
import sys
import time
import urllib.request

VID, PID = 0x4C4A, 0x8057
STOCK_SHA256 = "db1642b2b6fa5c2cccb11ffd13878068bb28601678d3644049f99dc40e7edb8a"
STOCK_URL = "https://yms-file-store.oss-cn-hongkong.aliyuncs.com/software/firmware/FM-1.fwsc"
STOCK_NAME = "FM-1-V15.fwsc"
LOADER_NAME = "wl82loader.bin"
LOADER_URL = ("https://raw.githubusercontent.com/kagaimiq/jl-uboot-tool/"
              "c32b28e7a04f873f526ba929cb1bae9f05442fe9/data/loaderblobs/usb/wl82loader.bin")
LOADER_SHA256 = "d41da6126760c9d66660bcc0cac8d27d221806c5e369a8036921efe68dca5376"
LOADER_ADDR, LOADER_ARG = 0x1C02000, 0x0001

FLASH_SIZE = 0x100000
APP_END = 0x93000          # the .fwsc flash entry is raw flash [0, APP_END)
WRITE_MIN = 0x4000         # flash header, SPL, isd_config: never written
SECTOR = 0x1000
IO = 512

# ROM UBOOT1.00
CMD_WRITE_MEMORY, CMD_READ_MEMORY, CMD_JUMP = 0xFB06, 0xFD07, 0xFB08
# loader
CMD_ERASE_SECTOR, CMD_WRITE_FLASH, CMD_READ_FLASH = 0xFB01, 0xFB04, 0xFD05
CMD_READ_KEY, CMD_GET_ONLINE_DEVICE, CMD_RUN_APP = 0xFC09, 0xFC0A, 0xFC0C


class Fail(Exception):
    pass


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
            raise Fail(f"command failed (status {status})")
        return got


def cdb_for(cmd, args=b""):
    return (struct.pack(">H", cmd) + bytes(args)).ljust(16, b"\xFF")


# ----------------------------------------------------------------- device ---
class Uboot:
    def __init__(self, bot):
        self.bot = bot

    def inquiry(self):
        r = self.bot.xfer(bytes([0x12, 0, 0, 0, 36, 0]), n_in=36)
        return r[8:16].decode(errors="replace").strip(), r[16:32].decode(errors="replace").strip()

    def cmd(self, cmd, args=b""):
        r = self.bot.xfer(cdb_for(cmd, args), n_in=16)
        if struct.unpack(">H", r[0:2])[0] != cmd:
            raise Fail(f"command {cmd:04X} answered as {r[0:2].hex()}")
        return r[2:]

    def write_block(self, cmd, addr, data):
        args = struct.pack(">IH", addr, len(data)) + b"\0" + struct.pack("<H", crc16(data))
        self.bot.xfer(cdb_for(cmd, args), data_out=data)

    def start_loader(self, blob):
        for off in range(0, len(blob), IO):                  # shipped already ciphered: send raw
            self.write_block(CMD_WRITE_MEMORY, LOADER_ADDR + off, blob[off:off + IO])
        self.cmd(CMD_JUMP, struct.pack(">IH", LOADER_ADDR, LOADER_ARG))

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
            k = min(IO, n - len(out))
            out += self.bot.xfer(cdb_for(CMD_READ_FLASH, struct.pack(">IH", addr + len(out), k)), n_in=k)
        return bytes(out)

    def write_sector(self, addr, data):
        if addr < WRITE_MIN or addr >= APP_END or addr % SECTOR or len(data) != SECTOR:
            raise Fail(f"refusing to write {addr:#x}")
        self.cmd(CMD_ERASE_SECTOR, struct.pack(">I", addr))
        for off in range(0, SECTOR, IO):
            self.write_block(CMD_WRITE_FLASH, addr + off, data[off:off + IO])
        if self.read(addr, SECTOR) != data:
            raise Fail(f"sector {addr:#07x} did not verify")

    def run_app(self):
        try:
            self.cmd(CMD_RUN_APP, struct.pack(">I", 1))
        except Exception:
            pass                                             # it may reset before it answers




# ---------------------------------------------------------------- windows ---
# Windows gives UBOOT to its own USB disk driver. The commands reach it through that driver's SCSI
# pass-through (as JieLi's own tools do): an administrator, but no driver to swap (no Zadig).
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
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


class WinScsi:
    """the same xfer() as Bot, through IOCTL_SCSI_PASS_THROUGH_DIRECT on the UBOOT disk"""

    def __init__(self, path, win=None):
        self.win = win or Win32()
        self.path = path
        self.h = self.win.open(path, True)
        if self.h is None:
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
            raise Fail(f"command failed (status {s.sptd.ScsiStatus})")
        return buf.raw[:n] if n_in else b""

    def close(self):
        if self.h is not None:
            self.win.close(self.h)
            self.h = None


# ------------------------------------------------------------------- flow ---
class Stopped(Fail):
    pass


def rescue(dev, image, loader, write, backup_dir, say=print, confirm=None, step=None, owner=None):
    """Everything in one session: the FM-1 leaves UBOOT (and crashes again) as soon as it is let go.
    step(kind, value): "backup" path, "write" percent, "done" (FM-1 Switch's progress)."""
    step = step or (lambda *a: None)
    vendor, product = dev.inquiry()
    say(f"  device: {vendor} {product}")
    if vendor != "WL82":
        raise Fail(f"not a WL82 UBOOT ({vendor!r})")
    dev.start_loader(loader)
    key, typ, fid = dev.chip()
    say(f"  chip key {key:04X}, flash type {typ}, id {fid:06X}")
    if (key, typ, fid) != (0x980F, 3, 0x856014):
        raise Fail("this is not the FM-1's chip and flash (expected key 980F, type 3, id 856014); nothing written")
    say("  reading the whole flash ...")
    t0 = time.time()
    now = dev.read(0, FLASH_SIZE)
    path = os.path.join(backup_dir, time.strftime("fm1-backup-%Y%m%d-%H%M%S.bin"))
    with open(path, "wb") as f:
        f.write(now)
    if owner:                                                # written as root: give it to the user
        try:
            os.chown(path, *owner)
        except (OSError, AttributeError):
            pass
    say(f"  backup saved: {path} ({time.time() - t0:.1f} s)")
    step("backup", path)
    diff = [a for a in range(0, APP_END, SECTOR) if image[a:a + SECTOR] != now[a:a + SECTOR]]
    low = [a for a in diff if a < WRITE_MIN]
    if low:
        raise Fail("the protected head of the flash differs from V15 (sectors "
                   + " ".join(f"{a:#06x}" for a in low) + "): not writing; send this output and the backup")
    say(f"  {len(diff)} of {APP_END // SECTOR} firmware sectors differ from V15")
    if not write and confirm is not None:
        write = keep_alive(dev, confirm)
    if not write:
        say("  check passed. Nothing written: run again with --write to put V15 back.")
        return False
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
    say("")
    say("  final read ...")
    expect = image + now[APP_END:]
    final = dev.read(0, FLASH_SIZE)
    if final != expect:
        bad = [a for a in range(0, FLASH_SIZE, SECTOR) if final[a:a + SECTOR] != expect[a:a + SECTOR]]
        raise Fail("final read differs in sectors " + " ".join(f"{a:#07x}" for a in bad)
                   + "; run the rescue again")
    say("  flash now holds V15. Restarting the FM-1 ...")
    dev.run_app()
    step("done", None)
    return True


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


def fetch(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


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


def find_stock(folders):
    """M-VAVE's V15 among the .fwsc files in the folders (by its SHA-256, whatever its name)"""
    for d in folders:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if n.lower().endswith(".fwsc") and os.path.isfile(p) and os.path.getsize(p) < (4 << 20):
                try:
                    if sha256(open(p, "rb").read()) == STOCK_SHA256:
                        return p
                except OSError:
                    pass
    return None


def download_stock(dest_dir, say=print):
    """M-VAVE's V15 from M-VAVE's own server, checked, as dest_dir/FM-1-V15.fwsc"""
    say("downloading M-VAVE's stock firmware (FM-1 V15) ...")
    raw = fetch(STOCK_URL)
    if sha256(raw) != STOCK_SHA256:
        raise Fail("the downloaded file is not M-VAVE's FM-1 V15 (sha256 mismatch); nothing was done")
    path = os.path.join(dest_dir, STOCK_NAME)
    with open(path, "wb") as f:
        f.write(raw)
    return path


def load_stock(path):
    raw = open(path, "rb").read()
    if sha256(raw) != STOCK_SHA256:
        raise Fail(f"{path} is not M-VAVE's FM-1 V15 package (sha256 mismatch)")
    image = package_flash_image(raw)
    if len(image) != APP_END:
        raise Fail("unexpected flash entry size")
    return image


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
            if disks:
                say(f"found it after {time.time() - t0:.1f} s ({disks[0]})")
                try:
                    t = WinScsi(disks[0])
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
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=be)
        if dev is not None:
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
        if isinstance(t, WinScsi):
            t.close()
        else:
            import usb.util
            usb.util.dispose_resources(t.dev)
    except Exception:
        pass


def ask():
    print("\n  Everything checks out, and the backup is saved.")
    try:
        return input("  Type yes and press Return to put the stock firmware back: ").strip().lower() == "yes"
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
    ap.add_argument("package", nargs="?", help="M-VAVE's FM-1 V15 .fwsc")
    ap.add_argument("--write", action="store_true", help="write V15 (without it: check and back up only)")
    ap.add_argument("--ask", action="store_true", help="after the check and the backup, ask before writing")
    ap.add_argument("--download", action="store_true", help="fetch V15 from M-VAVE (checked) if no package is given")
    ap.add_argument("--loader", help="a local wl82loader.bin instead of downloading it")
    ap.add_argument("--backup-dir", help="where the backup goes (default: beside the package)")
    ap.add_argument("--wait", type=float, default=120, help="seconds to wait for the FM-1 (default 120)")
    ap.add_argument("--tries", type=int, default=5, help="attempts if it drops out mid-way (default 5)")
    ap.add_argument("--progress", help=argparse.SUPPRESS)          # FM-1 Switch: events to this file
    ap.add_argument("--owner", help=argparse.SUPPRESS)             # uid:gid to give the backup to
    args = ap.parse_args(argv)
    pf = ProgressFile(args.progress) if args.progress else None
    say = pf.say if pf else (lambda *a, **k: print(*a, **k, flush=True))
    try:
        pkg = args.package
        if not pkg:
            if not args.download:
                ap.error("give the V15 package, or --download")
            pkg = download_stock(os.getcwd(), say)
        image = load_stock(pkg)
        backup_dir = args.backup_dir or os.path.dirname(os.path.abspath(pkg))
        loader = get_loader(args.loader, backup_dir, say)
        if not is_admin():
            raise Fail("run it as administrator (Windows)" if sys.platform == "win32"
                       else "run it with sudo: the system has to give the device up")
        if sys.platform != "win32":
            try:
                import usb.core  # noqa: F401
            except ImportError:
                raise Fail("pyusb is missing: ~/fm1-rescue/bin/pip install pyusb libusb-package")
        owner = tuple(int(x) for x in args.owner.split(":")) if args.owner else None
        for attempt in range(1, args.tries + 1):
            t = open_device(args.wait, say, pf.stopped if pf else None)
            try:
                rescue(Uboot(t), image, loader, args.write, backup_dir, say=say,
                       confirm=ask if args.ask else None, step=pf.step if pf else None, owner=owner)
                if pf:
                    pf.put(end="ok")
                return 0
            except Stopped:
                raise
            except Exception as e:                           # (a USB error: it dropped out)
                say(f"  attempt {attempt}: {e}")
                if isinstance(e, Fail) and ("not writing" in str(e) or "not the FM-1" in str(e)):
                    if pf:
                        pf.put(end="fail", error=str(e))
                    return 1
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
