# FM-1 Switch

A small window for the M-VAVE FM-1 that installs any FM-1 firmware file (`.fwsc`), in either
direction and whatever its number, and that checks and rescues an FM-1 that seems bricked. It runs
on Windows and on the Mac.

M-VAVE's M-UPGRADE only takes a firmware with a higher number than the one installed. FM-1 Switch
takes any Felucca-based package for the FM-1 model, such as Wild, NB303, n001se or FiMba-1, so you
can go from one to the other and back.

## Use

1. Put `FM1-Switch.exe` (Windows) or `FM1-Switch.app` (Mac) in a folder. It lists the `.fwsc` files
   in its own folder, in `Firmware Wild`, `Firmware NB303`, `Firmware n001se` and `Firmware
   Original` beside it, and in Downloads. **Add folder…** adds more. The highest number is listed
   first.
2. Connect the FM-1 with a USB data cable, straight into the computer, and close other MIDI apps.
3. Choose a file and press **Install**. Do not unplug the FM-1 while it writes.

## A bricked FM-1

Press **Check FM-1**. It watches USB for a few seconds and tells you which state the FM-1 is in:

| It says | What it means | What to do |
|---|---|---|
| runs FM-1_N | the firmware answers (also in Wild's safe mode) | Install |
| in update mode | an install was cut off, and the FM-1 waits for it | Install any package to finish it |
| in the chip's own update mode | the firmware does not start (UBOOT, USB 4C4A:8057) | Rescue… |
| not found | nothing answers | see below |

When it is not found, try these in order:

1. Use a data cable with no hub, switch the FM-1 on, and close other MIDI apps.
2. If the FM-1 shows a picture but does not answer, hold OCT− and OCT+ for 5 seconds. That is the
   update mode, and Install works from there.
3. If the screen stays dark or the FM-1 restarts over and over, press **Rescue…** and switch the
   FM-1 off and on. A firmware that keeps crashing ends up in the chip's own update mode, and that
   is where Rescue catches it.
4. If nothing at all shows on USB, the FM-1 needs an
   [FM-1-transporter](https://github.com/kurogedelic/FM-1-transporter), an RP2040 board wired to
   the USB lines.

**Rescue…** puts M-VAVE's stock firmware (FM-1 V15) back through the chip's own update mode, with
no extra hardware:

- It finds V15 among your `.fwsc` files by its SHA-256, whatever the file is called. If it is not
  there, it downloads V15 from M-VAVE's server and checks it. It also downloads JieLi's flash loader
  (`wl82loader.bin`, from [jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool)) and checks
  that. Both are kept in `FM-1 Rescue` beside the program.
- It needs an administrator on Windows (Windows asks) or your password on the Mac, so that it can
  take the FM-1 away from the system.
- It saves the whole flash (1 MiB) in `FM-1 Rescue` before anything else.
- It writes only the parts of the firmware area (0x4000–0x93000) that differ from V15. The chip's
  boot code below 0x4000 and the device data above 0x93000 are never written. Nothing is written if
  the boot code differs from V15's, or if the chip and its flash are not the FM-1's.
- **Stop** gives up while it waits. A write that has begun is finished.

After a rescue, install Wild, NB303 or another firmware as usual.

On Windows it talks to the FM-1 through the Windows disk driver (SCSI pass-through), so no driver
has to be installed. On the Mac it uses libusb, which is bundled in the app. The protocol and the
write policy are FM-1-transporter's, which were verified on hardware. **The Windows path has not
been tried on an FM-1 yet.**

Installing firmware is at your own risk.

## Build

Windows: `tools/build_switch_exe.bat` (Python 3 from python.org) makes `tools/dist/FM1-Switch.exe`.
CI also builds it on every push, under the Actions run's artifacts (FM1-Switch-windows).

Mac, the same way as the Noisebud apps: `tools/export_switch_mac.cmd` on the PC puts
`FM1Switch_mac_ready_vX.zip` and `Build_FM1Switch_Mac.command` in the Mac Export folder that syncs to
the Mac. Double-click the `.command` there. It runs `tools/build_switch_mac.sh`, which makes a
universal `FM1-Switch.app` and `FM1Switch_macOS_vX.dmg`. `NOISEBUD_APPLE_SIGN_IDENTITY` and
`NOISEBUD_APPLE_NOTARY_PROFILE` sign and notarize it. Without them the app is unsigned: the first
time, right-click it and choose Open.

From source: `pip install -r requirements.txt`, then `python tools/fm1_switch.pyw`. The parts also
work on their own from the command line:

```
python3 tools/fm1_install.py PACKAGE.fwsc        # install over USB-MIDI
python3 tools/fm1_install.py --info              # what the FM-1 runs
sudo python3 tools/fm1_rescue.py FM-1.fwsc       # Mac/Linux: check and back up, write nothing
sudo python3 tools/fm1_rescue.py FM-1.fwsc --write
python tools/fm1_rescue.py --download --write    # Windows, as administrator (V15 from M-VAVE)
```

## Tests

`tests/run_tests.sh` (or each `python tests/*_test.py`) runs everything against a simulated FM-1,
with no hardware, no Tk and no MIDI or USB packages:

- `install_test.py`: the USB-MIDI update.
- `switch_test.py`: the window's core, the diagnosis and the command that Rescue runs as
  administrator or root.
- `rescue_test.py`: the rescue against a simulated UBOOT (the ROM and JieLi's loader at the level of
  their commands), over USB and over a simulated Windows pass-through.

CI runs them on Linux, Windows and macOS.

## Credits and licence

GPL-3.0 (see `LICENSE`). The installer is Felucca's (`fm1_install.py`, Leo Kuroshita / Hügelton
Instruments). The rescue protocol and write policy come from
[FM-1-transporter](https://github.com/kurogedelic/FM-1-transporter) (MIT) and the X0X firmware's
`fm1_rescue.py`. The flash loader is from [jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool)
(MIT). It is downloaded, not included. Not affiliated with M-VAVE or JieLi.
