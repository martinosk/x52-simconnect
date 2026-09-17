"""
MSFS 2024 -> X52 (non-Pro) MFD bridge.

  python mfd_sim.py                # connect to the sim (retries until it is up)
  python mfd_sim.py --demo         # fake flight data, no sim needed
  python mfd_sim.py --page 2       # start on a given page
  python mfd_sim.py --cycle 5      # auto-advance pages every 5 s

Paging (defaults): Start/Stop = next page, Reset = previous page. A "P2/5 RADIO" banner
flashes on each change. Remap with --next/--prev/--home (see --list-buttons).

The stick firmware also acts on the three MFD buttons: Function cycles clock 1/2/3 and
toggles the stopwatch view, Start/Stop and Reset drive the stopwatch. That cannot be
disabled over USB, so Function is unmapped by default and the display is force-redrawn
shortly after any of those buttons is pressed to overwrite what the firmware drew.
"""
import argparse
import logging
import math
import time
from datetime import datetime

import usb.core

from x52_mfd import X52Mfd
from x52_buttons import ButtonReader, BUTTON_NAMES, FIRMWARE_BUTTONS

logging.basicConfig(level=logging.WARNING)
logging.getLogger("SimConnect").setLevel(logging.ERROR)

POLL_HZ = 4

# ---------------------------------------------------------------- formatting helpers

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def deg(rad):
    return int(round(math.degrees(_num(rad)))) % 360


def hdg3(rad):
    return f"{deg(rad):03d}"


def signed(v, width):
    v = int(round(_num(v)))
    return f"{v:+{width}d}"


def freq(v):
    return f"{_num(v):07.3f}"


def latlon(lat, lon):
    lat, lon = _num(lat), _num(lon)
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    lat, lon = abs(lat), abs(lon)
    return (f"{ns}{int(lat):02d} {(lat % 1) * 60:05.2f}",
            f"{ew}{int(lon):03d} {(lon % 1) * 60:05.2f}")


def flag(v):
    """Bool SimVars can come back as denormal garbage (~1e-311) instead of 0. Threshold, never truth-test."""
    return _num(v) > 0.5


def onoff(v, label):
    return label if flag(v) else "-" * len(label)


# ---------------------------------------------------------------- pages
# Each page: (title, [simvar names], render(values) -> 3 strings of <= 16 chars)

def page_flight(v):
    return [f"IAS {_num(v['AIRSPEED_INDICATED']):3.0f} GS {_num(v['GROUND_VELOCITY']):3.0f}",
            f"ALT {_num(v['INDICATED_ALTITUDE']):5.0f} V{signed(v['VERTICAL_SPEED'], 5)}",
            f"HDG {hdg3(v['PLANE_HEADING_DEGREES_MAGNETIC'])}  TRK {hdg3(v['GPS_GROUND_MAGNETIC_TRACK'])}"]


def page_radio(v):
    return [f"COM1 {freq(v['COM_ACTIVE_FREQUENCY:1'])}",
            f"STBY {freq(v['COM_STANDBY_FREQUENCY:1'])}",
            f"NAV1 {freq(v['NAV_ACTIVE_FREQUENCY:1'])[:6]} {int(_num(v['TRANSPONDER_CODE:1'])):04x}"]  # BCO16 -> hex digits


def page_autopilot(v):
    ap = "ON " if flag(v["AUTOPILOT_MASTER"]) else "OFF"
    return [f"AP {ap} {onoff(v['AUTOPILOT_HEADING_LOCK'], 'HDG')} {int(_num(v['AUTOPILOT_HEADING_LOCK_DIR'])):03d}",
            f"{onoff(v['AUTOPILOT_ALTITUDE_LOCK'], 'ALT')} {_num(v['AUTOPILOT_ALTITUDE_LOCK_VAR']):5.0f} V{signed(v['AUTOPILOT_VERTICAL_HOLD_VAR'], 5)}",
            f"{onoff(v['AUTOPILOT_NAV1_LOCK'], 'NAV')} SPD {_num(v['AUTOPILOT_AIRSPEED_HOLD_VAR']):3.0f}"]


def page_engine(v):
    return [f"RPM {_num(v['GENERAL_ENG_RPM:1']):5.0f}",
            f"FUEL {_num(v['FUEL_TOTAL_QUANTITY']):6.1f} GAL",
            f"OAT {signed(v['AMBIENT_TEMPERATURE'], 3)}C TAS {_num(v['AIRSPEED_TRUE']):3.0f}"]


def page_position(v):
    lat, lon = latlon(v["PLANE_LATITUDE"], v["PLANE_LONGITUDE"])
    return [lat, lon, f"AGL {_num(v['PLANE_ALT_ABOVE_GROUND']):5.0f} FT"]


PAGES = [
    ("FLIGHT",
     ["AIRSPEED_INDICATED", "GROUND_VELOCITY", "INDICATED_ALTITUDE", "VERTICAL_SPEED",
      "PLANE_HEADING_DEGREES_MAGNETIC", "GPS_GROUND_MAGNETIC_TRACK"], page_flight),
    ("RADIO",
     ["COM_ACTIVE_FREQUENCY:1", "COM_STANDBY_FREQUENCY:1", "NAV_ACTIVE_FREQUENCY:1", "TRANSPONDER_CODE:1"],
     page_radio),
    ("AUTOPILOT",
     ["AUTOPILOT_MASTER", "AUTOPILOT_HEADING_LOCK", "AUTOPILOT_HEADING_LOCK_DIR",
      "AUTOPILOT_ALTITUDE_LOCK", "AUTOPILOT_ALTITUDE_LOCK_VAR", "AUTOPILOT_VERTICAL_HOLD_VAR",
      "AUTOPILOT_NAV1_LOCK", "AUTOPILOT_AIRSPEED_HOLD_VAR"], page_autopilot),
    ("ENGINE",
     ["GENERAL_ENG_RPM:1", "FUEL_TOTAL_QUANTITY", "AMBIENT_TEMPERATURE", "AIRSPEED_TRUE"], page_engine),
    ("POSITION",
     ["PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALT_ABOVE_GROUND"], page_position),
]


# ---------------------------------------------------------------- data sources

CLOCK_VARS = ["ZULU_TIME", "LOCAL_TIME", "ZULU_DAY_OF_MONTH", "ZULU_MONTH_OF_YEAR", "ZULU_YEAR", "TIME_OF_DAY"]
ALL_VARS = list(dict.fromkeys(n for _, names, _ in PAGES for n in names)) + CLOCK_VARS
STALE_SECONDS = 15        # no packet for this long -> drop the connection and retry


class SimSource:
    """Streaming feed of ALL_VARS via one SimConnect data definition (see sim_feed.py).
    `read(names)` returns a dict of the latest values, or None while there is no data."""

    def __init__(self):
        from sim_feed import SimFeed
        self.feed = SimFeed(ALL_VARS)
        self._next_try = 0

    @property
    def connected(self):
        return self.feed.connected

    def ensure(self):
        if self.feed.connected:
            return True
        if time.time() < self._next_try:
            return False
        self._next_try = time.time() + 5
        try:
            self.feed.connect()
            print(f"connected to sim, streaming {len(ALL_VARS)} vars")
        except Exception as e:
            print(f"sim not available ({e.__class__.__name__}: {e}); retrying")
        return self.feed.connected

    def read(self, names):
        age = self.feed.age()
        if age > STALE_SECONDS:
            if self.feed.connected and age != float("inf"):
                print(f"no data from sim for {age:.0f} s; reconnecting")
                self.feed.close()
            return None
        if age == float("inf"):
            return None
        return self.feed.get(names)

    def close(self):
        self.feed.close()


class ClockSync:
    """Drive the firmware clock/date under the text from sim time, and brightness from time of day.
    Clock 1 = sim Zulu, clock 2 = sim local, clock 3 = real wall-clock time (as an offset)."""

    BRIGHTNESS = {0: 80, 1: 128, 2: 80, 3: 32}      # TIME_OF_DAY: dawn, day, dusk, night

    def __init__(self, mfd, clock=True, brightness=True):
        self.mfd = mfd
        self.clock = clock
        self.brightness = brightness

    @staticmethod
    def _offset_minutes(minutes_a, minutes_b):
        """a - b in minutes, wrapped into [-720, 720)."""
        return (int(round(minutes_a - minutes_b)) + 720) % 1440 - 720

    def update(self, v):
        if v is None or v.get("ZULU_TIME") is None:
            return
        if self.clock:
            zulu = _num(v["ZULU_TIME"]) / 60          # minutes since midnight
            local = _num(v["LOCAL_TIME"]) / 60
            self.mfd.set_clock(int(zulu // 60) % 24, int(zulu % 60))
            self.mfd.set_clock_offset(2, self._offset_minutes(local, zulu))
            now = datetime.now()
            self.mfd.set_clock_offset(3, self._offset_minutes(now.hour * 60 + now.minute, zulu))
            self.mfd.set_date(int(_num(v["ZULU_DAY_OF_MONTH"], 1)), int(_num(v["ZULU_MONTH_OF_YEAR"], 1)),
                              int(_num(v["ZULU_YEAR"], 2000)))
        if self.brightness:
            level = self.BRIGHTNESS.get(int(_num(v["TIME_OF_DAY"], 1)), 128)
            self.mfd.set_brightness(level)
            self.mfd.set_led_brightness(level)


class DemoSource:
    def __init__(self):
        self.t0 = time.time()
        self.connected = True

    def ensure(self):
        return True

    def read(self, names):
        t = time.time() - self.t0
        vals = {
            "AIRSPEED_INDICATED": 118 + 10 * math.sin(t / 5), "GROUND_VELOCITY": 124 + 10 * math.sin(t / 5),
            "INDICATED_ALTITUDE": 3500 + 400 * math.sin(t / 9), "VERTICAL_SPEED": 500 * math.cos(t / 9),
            "PLANE_HEADING_DEGREES_MAGNETIC": math.radians((270 + t * 2) % 360),
            "GPS_GROUND_MAGNETIC_TRACK": math.radians((268 + t * 2) % 360),
            "COM_ACTIVE_FREQUENCY:1": 118.750, "COM_STANDBY_FREQUENCY:1": 121.500,
            "NAV_ACTIVE_FREQUENCY:1": 110.50, "TRANSPONDER_CODE:1": 0x7000,   # BCO16
            "AUTOPILOT_MASTER": 1, "AUTOPILOT_HEADING_LOCK": 1, "AUTOPILOT_HEADING_LOCK_DIR": 270,
            "AUTOPILOT_ALTITUDE_LOCK": 1, "AUTOPILOT_ALTITUDE_LOCK_VAR": 5000,
            "AUTOPILOT_VERTICAL_HOLD_VAR": 700, "AUTOPILOT_NAV1_LOCK": 0, "AUTOPILOT_AIRSPEED_HOLD_VAR": 200,
            "GENERAL_ENG_RPM:1": 2350 + 20 * math.sin(t), "FUEL_TOTAL_QUANTITY": 42.5 - t / 600,
            "AMBIENT_TEMPERATURE": 12.4, "AIRSPEED_TRUE": 130,
            "PLANE_LATITUDE": 55.6180 + t / 20000, "PLANE_LONGITUDE": 12.6508, "PLANE_ALT_ABOVE_GROUND": 3455,
            "ZULU_TIME": (12 * 3600 + t * 60) % 86400, "LOCAL_TIME": (14 * 3600 + t * 60) % 86400,
            "ZULU_DAY_OF_MONTH": 17, "ZULU_MONTH_OF_YEAR": 9, "ZULU_YEAR": 2026, "TIME_OF_DAY": int(t / 10) % 4,
        }
        return {n: vals.get(n) for n in names}

    def close(self):
        pass


# ---------------------------------------------------------------- main loop

def waiting_screen():
    return ["MSFS 2024", "waiting for sim", datetime.now().strftime("%H:%M:%S")]


def render(page, values):
    title, names, fn = page
    try:
        lines = fn(values)
    except Exception as e:                      # never let a formatting bug blank the display
        lines = [title, "format error", str(e)[:16]]
    return [str(l)[:16] for l in lines]


BANNER_SECONDS = 0.8      # how long "P2/5 RADIO" is shown after a page change
REDRAW_DELAY = 0.3        # forced full redraw this long after a firmware-handled button press


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="fake data, no sim")
    ap.add_argument("--page", type=int, default=1, help="start page (1-based)")
    ap.add_argument("--cycle", type=float, default=0, help="auto-advance pages every N seconds")
    ap.add_argument("--no-buttons", action="store_true", help="do not read stick buttons via HID")
    ap.add_argument("--next", default="START_STOP", metavar="BTN", help="button for next page (default START_STOP)")
    ap.add_argument("--prev", default="RESET", metavar="BTN", help="button for previous page (default RESET)")
    ap.add_argument("--home", default="", metavar="BTN", help="button for page 1 (default none)")
    ap.add_argument("--mode-pages", action="store_true", help="mode switch 1/2/3 also selects pages 1-3")
    ap.add_argument("--no-clock", action="store_true", help="leave the firmware clock/date alone")
    ap.add_argument("--no-auto-brightness", action="store_true", help="do not dim MFD/LEDs by time of day")
    ap.add_argument("--list-buttons", action="store_true", help="print valid button names and exit")
    args = ap.parse_args()

    if args.list_buttons:
        print(" ".join(sorted(BUTTON_NAMES)))
        return
    for opt in ("next", "prev", "home"):
        name = getattr(args, opt).upper()
        setattr(args, opt, name)
        if name and name not in BUTTON_NAMES:
            ap.error(f"--{opt}: unknown button {name!r}; see --list-buttons")

    mfd = X52Mfd()
    src = DemoSource() if args.demo else SimSource()
    clock = ClockSync(mfd, clock=not args.no_clock, brightness=not args.no_auto_brightness)
    buttons = None
    if not args.no_buttons:
        buttons = ButtonReader()
        buttons.start()

    page = (args.page - 1) % len(PAGES)
    last_cycle = time.time()
    last_mode = None
    banner_until = 0
    redraw_at = 0
    usb_failures = 0
    mfd.set_lines(["MSFS -> X52 MFD", "demo mode" if args.demo else "connecting...", ""], force=True)
    print(f"running, Ctrl-C to quit. next={args.next} prev={args.prev} home={args.home or '-'}")

    def goto(new_page):
        nonlocal page, banner_until
        if new_page != page:
            page = new_page % len(PAGES)
            banner_until = time.time() + BANNER_SECONDS
            print("page", page + 1, PAGES[page][0])

    try:
        while True:
            t_start = time.time()
            if buttons:
                for name in buttons.presses():
                    if name == args.next:
                        goto(page + 1)
                    elif name == args.prev:
                        goto(page - 1)
                    elif name == args.home:
                        goto(0)
                    if name in FIRMWARE_BUTTONS:
                        redraw_at = t_start + REDRAW_DELAY
                if args.mode_pages and buttons.mode and buttons.mode != last_mode:
                    last_mode = buttons.mode
                    goto(min(buttons.mode - 1, len(PAGES) - 1))
            if args.cycle and t_start - last_cycle >= args.cycle:
                goto(page + 1)
                last_cycle = t_start

            values = None
            if src.ensure():
                values = src.read(PAGES[page][1] + CLOCK_VARS)
            lines = render(PAGES[page], values) if values else waiting_screen()
            if t_start < banner_until:
                lines[0] = f"P{page + 1}/{len(PAGES)} {PAGES[page][0]}"[:16]

            force = bool(redraw_at) and t_start >= redraw_at
            if force:
                redraw_at = 0
            try:
                mfd.set_lines(lines, force=force)
                clock.update(values)
                usb_failures = 0
            except usb.core.USBError as e:
                usb_failures += 1
                print(f"MFD write failed ({usb_failures}): {str(e).strip()[:120]}")
                if usb_failures >= 3:
                    try:
                        mfd.reopen()
                        print("reopened X52")
                    except Exception as e2:
                        print(f"reopen failed: {e2}")
                time.sleep(0.5)

            time.sleep(max(0, 1 / POLL_HZ - (time.time() - t_start)))
    except KeyboardInterrupt:
        pass
    finally:
        if buttons:
            buttons.stop()
        src.close()
        mfd.set_lines(["MSFS -> X52 MFD", "stopped", ""], force=True)


if __name__ == "__main__":
    main()
