"""Run the bridge with a flight recorder, to measure how often MFD writes make the X52 stop reporting.

  python tools/trace_bridge.py [bridge options]    # fly as usual, Ctrl-C to stop; prints the report
  python tools/trace_bridge.py --report [FILE]     # the report for the newest (or the given) trace

The trace holds every HID report of the stick and every vendor transfer sent to it, with timestamps, in
``%APPDATA%/x52-simconnect/trace/``. An outage is the stick going silent for ``OUTAGE`` to ``LONGEST`` seconds
while it was busy reporting just before. It is "certain" when an axis jumps across the silence (the pilot kept
moving, or the stick re-calibrated) and "likely" otherwise: a pilot who stops moving looks the same. Outages can
only be seen while something on the stick is being moved."""

import sys
import threading
import time
from pathlib import Path

from x52_simconnect import bridge, config
from x52_simconnect.buttons import ButtonReader
from x52_simconnect.mfd import CLEAR, LINE_CMDS, X52Mfd

OUTAGE, LONGEST = 0.4, 6.0  # seconds without a report
JUMP = (40, 40, 20, 8)  # x, y, twist, throttle: a change across the silence that says the stick kept moving
BUSY = 15  # reports in the second before the silence, so a stick at rest does not count
CLEARS = {cmd | CLEAR for cmd in LINE_CMDS}


def trace_dir():
    return config.default_path().parent / "trace"


def record(argv):
    path = trace_dir() / time.strftime("flight-%Y%m%d-%H%M%S.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    out = path.open("w", buffering=1 << 16)
    lock = threading.Lock()

    def write(kind, data):
        with lock:
            out.write(f"{time.time():.4f},{kind},{data}\n")

    vendor, feed = X52Mfd._vendor, ButtonReader.feed

    def traced_vendor(self, index, value=0):
        write("usb", f"{index:02x}")
        vendor(self, index, value)

    def traced_feed(self, report):
        write("hid", bytes(report).hex())
        feed(self, report)

    X52Mfd._vendor, ButtonReader.feed = traced_vendor, traced_feed
    print(f"tracing to {path}")
    try:
        bridge.main(argv)
    finally:
        out.close()
    return path


def axes(report_hex):
    """x, y, twist and throttle of a raw HID report (hidapi may have prefixed a report id)."""
    data = bytes.fromhex(report_hex)[-14:]
    packed = int.from_bytes(data[0:4], "little")
    return packed & 0x7FF, (packed >> 11) & 0x7FF, (packed >> 22) & 0x3FF, data[4]


def outages(rows):
    """``rows``: (time, kind, data). Yields (start, length, seconds since the last line clear or None, certain)."""
    reports, previous = [], None
    last_clear = clear_before_silence = None  # the latter: the last clear up to the newest report
    for t, kind, data in rows:
        if kind == "usb":
            if int(data, 16) in CLEARS:
                last_clear = t
                if reports and t - reports[-1] <= 0.05:  # sent right as the stick fell silent
                    clear_before_silence = t
            continue
        if reports and OUTAGE < t - reports[-1] <= LONGEST:
            start = reports[-1]
            jumped = any(abs(a - b) > limit for a, b, limit in zip(axes(previous), axes(data), JUMP, strict=True))
            if sum(1 for r in reports[-50:] if start - r <= 1.0) >= BUSY:
                since = None if clear_before_silence is None else max(0.0, start - clear_before_silence)
                yield start, t - start, since, jumped
        reports.append(t)
        previous = data
        clear_before_silence = last_clear


def report(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        t, kind, data = line.split(",", 2)
        rows.append((float(t), kind, data))
    if not rows:
        print("empty trace")
        return
    minutes = (rows[-1][0] - rows[0][0]) / 60
    clears = sum(1 for _, kind, data in rows if kind == "usb" and int(data, 16) in CLEARS)
    transfers = sum(1 for _, kind, _ in rows if kind == "usb")
    found = list(outages(rows))
    certain = sum(1 for o in found if o[3])
    print(f"{path}\n{minutes:.1f} min, {transfers} transfers, {clears} line writes ({clears / minutes:.0f}/min)")
    print(f"{len(found)} outages ({certain} certain, {len(found) - certain} likely)")
    if found:
        print(f"about 1 outage per {clears / len(found):.0f} line writes, 1 every {minutes / len(found):.1f} min")
    for start, length, since, jumped in found:
        after = "no line write before" if since is None else f"{since:5.2f} s after a line write"
        kind = "certain" if jumped else "likely "
        print(f"  {time.strftime('%H:%M:%S', time.localtime(start))}  {length:4.1f} s  {kind}  {after}")


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--report"]:
        traces = sorted(trace_dir().glob("flight-*.csv"))
        target = argv[1] if len(argv) > 1 else (traces[-1] if traces else None)
        if target is None:
            sys.exit(f"no traces in {trace_dir()}")
        report(target)
        return
    report(record(argv))


if __name__ == "__main__":
    main()
