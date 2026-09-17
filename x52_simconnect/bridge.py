"""
MSFS 2024 -> X52 (non-Pro) MFD bridge: the CLI and the main loop.

  python -m x52_simconnect                # connect to the sim (retries until it is up)
  python -m x52_simconnect --demo         # fake flight data, no sim needed
  python -m x52_simconnect --page 2       # start on a given page
  python -m x52_simconnect --cycle 5      # auto-advance pages every 5 s
  python -m x52_simconnect --mode 3       # force a mode, ignoring the stick's selector

The rotary mode selector on the stick picks the app on the MFD: 1 = data pages, 2 = comms,
3 = event log (see apps.py). A "MODE 2 COMMS" banner flashes on each change.

Paging in mode 1 (defaults): Start/Stop = next page, Reset = previous page. A "P2/5 RADIO"
banner flashes on each change. Remap with --next/--prev/--home (see --list-buttons).

The stick firmware also acts on the three MFD buttons: Function cycles clock 1/2/3 and
toggles the stopwatch view, Start/Stop and Reset drive the stopwatch. That cannot be
disabled over USB, so Function is unmapped by default and the display is force-redrawn
shortly after any of those buttons is pressed to overwrite what the firmware drew.
"""

import argparse
import logging
import time
from datetime import datetime

import usb.core

from .apps import ALL_VARS, build_apps
from .buttons import BUTTON_NAMES, FIRMWARE_BUTTONS, ButtonReader
from .clock_sync import ClockSync
from .display import MODES, Display
from .mfd import X52Mfd
from .saitek_driver import DriverModeReader
from .sources import DemoSource, SimSource

POLL_HZ = 4
USB_FAILURES_BEFORE_REOPEN = 3

log = logging.getLogger(__name__)


def waiting_screen(now=None):
    now = now or datetime.now()
    return ["MSFS 2024", "waiting for sim", now.strftime("%H:%M:%S")]


class Bridge:
    """The pure part of the main loop: mode switching, button routing, rendering through the ``Display``.

    ``run()`` wraps it with the stick, the feed and error recovery; the tests drive ``step`` directly."""

    def __init__(self, display, apps, forced_mode=None):
        self.display = display
        self.apps = apps
        self.forced_mode = forced_mode
        self.active = apps[display.mode]

    def select_mode(self, mode):
        """Switch to ``mode`` if it differs: activate its app and flash a ``MODE n NAME`` banner."""
        if not self.display.set_mode(mode):
            return False
        self.active = self.apps[mode]
        self.active.on_activate()
        self.display.banner(f"MODE {mode} {self.active.name}")
        log.info("mode %d %s", mode, self.active.name)
        return True

    def step(self, presses=(), stick_mode=None, values=None, now=None):
        """One loop tick. ``stick_mode`` is the selector as the reader saw it (None before the first report,
        which means "leave it"); ``values`` None means no sim data yet. Returns the lines written."""
        now = self.display.tick(now)
        mode = self.forced_mode or stick_mode
        if mode is not None:
            self.select_mode(mode)
        for name in presses:
            self.active.on_button(name)
            if name in FIRMWARE_BUTTONS:
                self.display.force_redraw_in()
        self.active.tick(now)
        lines = self.active.render(values) if values else waiting_screen()
        return self.display.show(lines)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="python -m x52_simconnect", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--demo", action="store_true", help="fake data, no sim")
    ap.add_argument("--mode", type=int, choices=MODES, default=None, help="force a mode, ignore the stick's selector")
    ap.add_argument("--page", type=int, default=1, help="start page in mode 1 (1-based)")
    ap.add_argument("--cycle", type=float, default=0, help="auto-advance pages every N seconds")
    ap.add_argument("--no-buttons", action="store_true", help="do not read stick buttons via HID")
    ap.add_argument("--next", default="START_STOP", metavar="BTN", help="button for next page (default START_STOP)")
    ap.add_argument("--prev", default="RESET", metavar="BTN", help="button for previous page (default RESET)")
    ap.add_argument("--home", default="", metavar="BTN", help="button for page 1 (default none)")
    ap.add_argument("--no-clock", action="store_true", help="leave the firmware clock/date alone")
    ap.add_argument("--no-auto-brightness", action="store_true", help="do not dim MFD/LEDs by time of day")
    ap.add_argument("--list-buttons", action="store_true", help="print valid button names and exit")
    return ap


def parse_args(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    for opt in ("next", "prev", "home"):
        name = getattr(args, opt).upper()
        setattr(args, opt, name)
        if name and name not in BUTTON_NAMES:
            ap.error(f"--{opt}: unknown button {name!r}; see --list-buttons")
    return args


def run(args):
    mfd = X52Mfd()
    display = Display(mfd)
    src = DemoSource() if args.demo else SimSource()
    clock = ClockSync(mfd, clock=not args.no_clock, brightness=not args.no_auto_brightness)
    buttons = selector = None
    if not args.no_buttons:
        buttons = ButtonReader()
        buttons.start()
        # Logitech's filter driver hides the selector from HID but answers for it itself (saitek_driver.py).
        try:
            selector = DriverModeReader()
            print("mode selector: read through the Logitech driver")
        except OSError as e:
            print(f"mode selector: read from HID reports ({e})")

    apps = build_apps(display, start=args.page - 1, next=args.next, prev=args.prev, home=args.home, cycle=args.cycle)
    bridge = Bridge(display, apps, forced_mode=args.mode)
    usb_failures = 0
    display.show(["MSFS -> X52 MFD", "demo mode" if args.demo else "connecting...", ""], force=True)
    print(
        f"running, Ctrl-C to quit. mode={args.mode or 'selector'} "
        f"next={args.next} prev={args.prev} home={args.home or '-'}"
    )

    try:
        while True:
            now = time.time()
            presses = list(buttons.presses()) if buttons else []
            stick_mode = selector.read() if selector else None
            if stick_mode is None and buttons:
                stick_mode = buttons.mode
            values = src.read(ALL_VARS) if src.ensure() else None
            try:
                bridge.step(presses, stick_mode, values, now=now)
                clock.update(values)
                usb_failures = 0
            except usb.core.USBError as e:
                usb_failures += 1
                print(f"MFD write failed ({usb_failures}): {str(e).strip()[:120]}")
                if usb_failures >= USB_FAILURES_BEFORE_REOPEN:
                    try:
                        mfd.reopen()
                        print("reopened X52")
                    except Exception as e2:  # noqa: BLE001 - keep looping, the stick may come back
                        print(f"reopen failed: {e2}")
                time.sleep(0.5)

            time.sleep(max(0, 1 / POLL_HZ - (time.time() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        if buttons:
            buttons.stop()
        if selector:
            selector.close()
        src.close()
        mfd.set_lines(["MSFS -> X52 MFD", "stopped", ""], force=True)


def main(argv=None):
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("SimConnect").setLevel(logging.ERROR)
    logging.getLogger("x52_simconnect").setLevel(logging.INFO)
    args = parse_args(argv)
    if args.list_buttons:
        print(" ".join(sorted(BUTTON_NAMES)))
        return
    run(args)


if __name__ == "__main__":
    main()
