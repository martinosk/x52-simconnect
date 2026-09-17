"""
Reads the X52 non-Pro HID input report to detect the three buttons next to the MFD
(Function, Start/Stop, Reset) and the mode selector. Runs in a background thread and
opens the HID device shared, so the sim keeps receiving joystick input.

Report layout from libx52io/parser.c: 14 bytes, buttons are a 40-bit little-endian
field starting at byte 8. hidapi on Windows prefixes a report-id byte, which we strip.

CLI:  python -m x52_simconnect.buttons      # prints presses and the mode, Ctrl-C to stop
"""

import queue
import threading

VID, PID = 0x06A3, 0x075C

# bit index -> name, full non-Pro map from libx52io/parser.c
BUTTON_BITS = {
    0: "TRIGGER", 1: "FIRE", 2: "A", 3: "B", 4: "C", 5: "PINKY", 6: "D", 7: "E",
    8: "T1_UP", 9: "T1_DN", 10: "T2_UP", 11: "T2_DN", 12: "T3_UP", 13: "T3_DN",
    14: "TRIGGER_2",
    15: "POV_1_N", 16: "POV_1_E", 17: "POV_1_S", 18: "POV_1_W",
    19: "POV_2_N", 20: "POV_2_E", 21: "POV_2_S", 22: "POV_2_W",
    23: "MODE_1", 24: "MODE_2", 25: "MODE_3",
    26: "FUNCTION", 27: "START_STOP", 28: "RESET",  # also handled by the stick firmware (clock/stopwatch)
    29: "CLUTCH",
    30: "MOUSE_PRIMARY", 31: "MOUSE_SECONDARY", 32: "MOUSE_SCROLL_DN", 33: "MOUSE_SCROLL_UP",
}  # fmt: skip
BUTTON_NAMES = frozenset(BUTTON_BITS.values())
MODE_BUTTONS = ("MODE_1", "MODE_2", "MODE_3")

# Firmware side effects, observed on the real stick: FUNCTION cycles the MFD clock 1/2/3 and toggles
# stopwatch view (draws "1".."3" over our text); START_STOP and RESET drive the stopwatch.
# The firmware redraws its bits after a press, so callers should force a full redraw shortly after.
FIRMWARE_BUTTONS = frozenset({"FUNCTION", "START_STOP", "RESET"})

REPORT_LEN = 14


def decode(report):
    """HID input report -> {button name: is down}, or None for a report of the wrong size."""
    data = bytes(report)
    if len(data) == REPORT_LEN + 1:  # hidapi report-id prefix
        data = data[1:]
    if len(data) != REPORT_LEN:
        return None
    bits = int.from_bytes(data[8:13], "little")
    return {name: bool(bits >> bit & 1) for bit, name in BUTTON_BITS.items()}


class ButtonReader(threading.Thread):
    """Edge-detects presses. Consume them with ``for name in reader.presses(): ...``.

    ``mode`` is 1, 2 or 3 once the stick has sent a report, None before that.
    ``error`` is set and the thread exits if the HID device cannot be opened."""

    def __init__(self, vid=VID, pid=PID):
        super().__init__(daemon=True)
        self.vid, self.pid = vid, pid
        self.events = queue.Queue()
        self.state = {name: False for name in BUTTON_BITS.values()}
        self.mode = None
        self.error = None
        self._stop = threading.Event()

    def feed(self, report):
        """Process one raw report: queue newly pressed buttons, track held state and the mode selector."""
        new = decode(report)
        if new is None:
            return
        for name, down in new.items():
            if down and not self.state[name]:
                self.events.put(name)
        self.state = new
        for m in MODE_BUTTONS:
            if new[m]:
                self.mode = int(m[-1])

    def run(self):
        try:
            import hid  # hidapi; imported here so the rest of the module works without it

            h = hid.device()
            h.open(self.vid, self.pid)
        except Exception as e:  # noqa: BLE001 - no HID access: paging just won't work
            self.error = e
            return
        try:
            while not self._stop.is_set():
                r = h.read(64, timeout_ms=200)
                if r:
                    self.feed(r)
        finally:
            h.close()

    def stop(self):
        self._stop.set()

    def presses(self):
        """Yield the names queued since the last call, oldest first."""
        while True:
            try:
                yield self.events.get_nowait()
            except queue.Empty:
                return


def main():
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
    finally:
        rd.stop()


if __name__ == "__main__":
    main()
