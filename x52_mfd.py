"""
Driver for the MFD of a non-Pro Saitek/Logitech X52 (USB 06A3:075C).

Protocol (reverse-engineered by the libx52 project, https://github.com/nirenjan/libx52):
  USB vendor control transfer, bmRequestType 0x40, bRequest 0x91,
  wIndex = command, wValue = payload.
    line 1/2/3 select : 0xD1 / 0xD2 / 0xD4
    | 0x08            : clear that line
    | 0x00            : append two chars packed little-endian in wValue
    0xB1              : MFD brightness (0..128)
  Each line is 16 characters.

Windows needs the libusb-win32 *filter* driver on the stick (see README.md) and
pyusb's libusb0 backend. The libusb1 backend is kept for the record: it fails
while the stick is bound to HidUsb, because that backend rejects vendor requests.

CLI:  python x52_mfd.py [libusb0|libusb1] "line 1" "line 2" "line 3"
"""
import sys
import time
import usb.core
import usb.util

VID, PID = 0x06A3, 0x075C          # X52 non-Pro (Pro is 0x0762 and uses DirectOutput instead)
REQ = 0x91
LINE_CMDS = (0xD1, 0xD2, 0xD4)
CLEAR = 0x08
MFD_BRIGHTNESS = 0xB1
LED_BRIGHTNESS = 0xB2
TIME_CLOCK1 = 0xC0
OFFS_CLOCK2 = 0xC1
OFFS_CLOCK3 = 0xC2
DATE_DDMM = 0xC4
DATE_YEAR = 0xC8
SHIFT_INDICATOR = 0xFD
BLINK_INDICATOR = 0xB4
LINE_LEN = 16
BM_OUT_VENDOR = usb.util.build_request_type(
    usb.util.CTRL_OUT, usb.util.CTRL_TYPE_VENDOR, usb.util.CTRL_RECIPIENT_DEVICE)


def get_backend(name="libusb0"):
    if name == "libusb1":
        import libusb                      # pip package bundling libusb-1.0.dll
        import usb.backend.libusb1 as b
        return b.get_backend(find_library=lambda _: libusb.dll._name)
    if name == "libusb0":
        import usb.backend.libusb0 as b
        return b.get_backend()
    raise ValueError(f"unknown backend {name}")


class X52Mfd:
    """Three 16-character lines. Writes are cached, so unchanged lines cost nothing."""

    RETRIES = 3               # per control transfer
    RETRY_DELAY = 0.05

    def __init__(self, backend="libusb0"):
        self._backend_name = backend
        self.dev = None
        self._shown = [None, None, None]
        self.open()

    def open(self):
        self.dev = usb.core.find(idVendor=VID, idProduct=PID, backend=get_backend(self._backend_name))
        if self.dev is None:
            raise RuntimeError("X52 (06A3:075C) not found; is the libusb0 filter installed and loaded?")
        self._shown = [None, None, None]         # nothing is known to be on screen any more
        self._state = {}                          # cached clock/date/brightness writes

    def reopen(self):
        """Drop the handle and find the stick again. Call after repeated USBError."""
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
        self.dev = None
        self.open()

    def _vendor(self, index, value=0):
        # The stick occasionally fails one transfer with Windows error 31 "A device attached to the
        # system is not functioning" (seen right after a firmware-handled button press). Retry briefly.
        for attempt in range(self.RETRIES):
            try:
                self.dev.ctrl_transfer(BM_OUT_VENDOR, REQ, value, index, None, timeout=1000)
                return
            except usb.core.USBError:
                if attempt == self.RETRIES - 1:
                    raise
                time.sleep(self.RETRY_DELAY)

    def set_line(self, line, text, force=False):
        text = str(text)[:LINE_LEN].ljust(LINE_LEN)
        if not force and self._shown[line] == text:
            return
        cmd = LINE_CMDS[line]
        self._shown[line] = None                  # partial writes leave the line unknown
        self._vendor(cmd | CLEAR)
        for i in range(0, LINE_LEN, 2):
            self._vendor(cmd, (ord(text[i]) & 0xFF) | ((ord(text[i + 1]) & 0xFF) << 8))
        self._shown[line] = text

    def set_lines(self, lines, force=False):
        for i, text in enumerate(list(lines)[:3]):
            self.set_line(i, text, force)

    # ------------------------------------------------------------ clock, date, brightness
    # Encodings from libx52 control.c. All cached: repeated identical writes are skipped.

    def _cached(self, key, index, value):
        if self._state.get(key) == (index, value):
            return
        self._vendor(index, value)
        self._state[key] = (index, value)

    def set_clock(self, hour, minute, h24=True):
        """Clock 1 (the firmware clock shown under the text). The firmware keeps it ticking."""
        self._cached("clock1", TIME_CLOCK1, (int(bool(h24)) << 15) | ((hour & 0x7F) << 8) | (minute & 0xFF))

    def set_clock_offset(self, clock, offset_minutes, h24=True):
        """Clock 2 or 3 as an offset from clock 1, in minutes (max +/- 1023, wrapped like libx52)."""
        offset = int(offset_minutes)
        negative = offset < 0
        offset = abs(offset)
        while offset > 1023:
            offset -= 1440
        if offset < 0:
            negative = not negative
            offset = -offset
        index = {2: OFFS_CLOCK2, 3: OFFS_CLOCK3}[clock]
        self._cached(f"clock{clock}", index, (int(bool(h24)) << 15) | (int(negative) << 10) | (offset & 0x3FF))

    def set_date(self, day, month, year):
        """DD-MM-YY layout on the firmware date display."""
        self._cached("date_ddmm", DATE_DDMM, ((month & 0xFF) << 8) | (day & 0xFF))
        self._cached("date_year", DATE_YEAR, year % 100)

    def set_brightness(self, level):
        self._cached("mfd_brightness", MFD_BRIGHTNESS, max(0, min(128, int(level))))

    def set_led_brightness(self, level):
        self._cached("led_brightness", LED_BRIGHTNESS, max(0, min(128, int(level))))

    # Individual LED colours (0xB8) are X52 Pro only. The non-Pro has just these two indicators:
    def set_shift(self, on):
        """The SHIFT indicator on the MFD."""
        self._cached("shift", SHIFT_INDICATOR, 0x51 if on else 0x50)

    def set_blink(self, on):
        """Blink the POV hat and clutch LEDs."""
        self._cached("blink", BLINK_INDICATOR, 0x51 if on else 0x50)

    def clear(self):
        self.set_lines(["", "", ""], force=True)


def main():
    backend_name = sys.argv[1] if len(sys.argv) > 1 else "libusb0"
    lines = sys.argv[2:] or ["Hello from", "Claude Code", "MSFS 2024 spike"]
    mfd = X52Mfd(backend_name)
    dev = mfd.dev
    print(f"backend={backend_name} device={dev.idVendor:04x}:{dev.idProduct:04x} bus={dev.bus} addr={dev.address}")
    for i, text in enumerate(lines[:3]):
        mfd.set_line(i, text, force=True)
        print(f"line {i + 1}: {text!r}")


if __name__ == "__main__":
    main()
