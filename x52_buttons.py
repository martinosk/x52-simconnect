"""
Reads the X52 non-Pro HID input report to detect the three buttons next to the MFD
(Function, Start/Stop, Reset) and the mode selector. Runs in a background thread and
opens the HID device shared, so the sim keeps receiving joystick input.

Report layout from libx52io/parser.c: 14 bytes, buttons are a 40-bit little-endian
field starting at byte 8. hidapi on Windows prefixes a report-id byte, which we strip.
"""
import queue
import threading
import hid

VID, PID = 0x06A3, 0x075C

# bit index -> name, full non-Pro map from libx52io/parser.c
BUTTON_BITS = {
    0: "TRIGGER", 1: "FIRE", 2: "A", 3: "B", 4: "C", 5: "PINKY", 6: "D", 7: "E",
    8: "T1_UP", 9: "T1_DN", 10: "T2_UP", 11: "T2_DN", 12: "T3_UP", 13: "T3_DN",
    14: "TRIGGER_2",
    15: "POV_1_N", 16: "POV_1_E", 17: "POV_1_S", 18: "POV_1_W",
    19: "POV_2_N", 20: "POV_2_E", 21: "POV_2_S", 22: "POV_2_W",
    23: "MODE_1", 24: "MODE_2", 25: "MODE_3",
    26: "FUNCTION", 27: "START_STOP", 28: "RESET",       # also handled by the stick firmware (clock/stopwatch)
    29: "CLUTCH",
    30: "MOUSE_PRIMARY", 31: "MOUSE_SECONDARY", 32: "MOUSE_SCROLL_DN", 33: "MOUSE_SCROLL_UP",
}
BUTTON_NAMES = set(BUTTON_BITS.values())

# Firmware side effects, observed on the real stick: FUNCTION cycles the MFD clock 1/2/3 and toggles
# stopwatch view (draws "1".."3" over our text); START_STOP and RESET drive the stopwatch.
# The firmware redraws its bits after a press, so callers should force a full redraw shortly after.
FIRMWARE_BUTTONS = {"FUNCTION", "START_STOP", "RESET"}


def decode(report):
    data = bytes(report)
    if len(data) == 15:                 # hidapi report-id prefix
        data = data[1:]
    if len(data) != 14:
        return None
    bits = int.from_bytes(data[8:13], "little")
    return {name: bool(bits >> bit & 1) for bit, name in BUTTON_BITS.items()}


class ButtonReader(threading.Thread):
    """Edge-detects presses. Consume them with `for name in reader.presses(): ...`."""

    def __init__(self):
        super().__init__(daemon=True)
        self.events = queue.Queue()
        self.state = {name: False for name in BUTTON_BITS.values()}
        self.mode = None
        self.error = None
        self._stop = threading.Event()

    def run(self):
        try:
            h = hid.device()
            h.open(VID, PID)
        except Exception as e:           # no HID access: paging just won't work
            self.error = e
            return
        try:
            while not self._stop.is_set():
                r = h.read(64, timeout_ms=200)
                if not r:
                    continue
                new = decode(r)
                if new is None:
                    continue
                for name, down in new.items():
                    if down and not self.state[name]:
                        self.events.put(name)
                self.state = new
                for m in ("MODE_1", "MODE_2", "MODE_3"):
                    if new[m]:
                        self.mode = int(m[-1])
        finally:
            h.close()

    def stop(self):
        self._stop.set()

    def presses(self):
        while True:
            try:
                yield self.events.get_nowait()
            except queue.Empty:
                return


if __name__ == "__main__":
    import time
    rd = ButtonReader()
    rd.start()
    print("press Function / Start-Stop / Reset or move the mode switch, Ctrl-C to quit")
    try:
        while True:
            for name in rd.presses():
                print("press:", name, "mode:", rd.mode)
            if rd.error:
                print("HID error:", rd.error)
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
