# FM-1 Switch's own hook for pyusb, before the contrib one: bundle no libusb found on the build Mac
# (a Homebrew or /usr/local copy has one slice only). The rescue loads libusb-package's universal one.
hiddenimports = ["glob"]
binaries = []
