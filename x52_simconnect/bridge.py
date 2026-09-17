"""
MSFS 2024 -> X52 (non-Pro) MFD bridge: the CLI and the main loop.

  python -m x52_simconnect                # connect to the sim (retries until it is up)
  python -m x52_simconnect --demo         # fake flight data, no sim needed
  python -m x52_simconnect --page 2       # start on a given page
  python -m x52_simconnect --cycle 5      # auto-advance pages every 5 s

Paging (defaults): Start/Stop = next page, Reset = previous page. A "P2/5 RADIO" banner
flashes on each change. Remap with --next/--prev/--home (see --list-buttons).

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

from .buttons import BUTTON_NAMES, FIRMWARE_BUTTONS, ButtonReader
from .clock_sync import ClockSync
from .formatting import clip
from .mfd import X52Mfd
from .pages import CLOCK_VARS, PAGES, render
from .sources import DemoSource, SimSource

POLL_HZ = 4
BANNER_SECONDS = 0.8  # how long "P2/5 RADIO" is shown after a page change
REDRAW_DELAY = 0.3  # forced full redraw this long after a firmware-handled button press
USB_FAILURES_BEFORE_REOPEN = 3


class Pager:
    """Current page index plus the short banner shown after a change. Pure; ``now`` is passed in."""

    def __init__(self, count, start=0, banner_seconds=BANNER_SECONDS):
        self.count = count
        self.page = start % count
        self.banner_seconds = banner_seconds
        self.banner_until = 0.0

    def goto(self, index, now):
        """Switch to ``index`` (wrapped). Returns True when the page actually changed."""
        index %= self.count
        if index == self.page:
            return False
        self.page = index
        self.banner_until = now + self.banner_seconds
        return True

    def next(self, now):
        return self.goto(self.page + 1, now)

    def prev(self, now):
        return self.goto(self.page - 1, now)

    def banner(self, now):
        """The banner text while it is due, else None."""
        if now < self.banner_until:
            return clip(f"P{self.page + 1}/{self.count} {PAGES[self.page].title}")
        return None


def waiting_screen(now=None):
    now = now or datetime.now()
    return ["MSFS 2024", "waiting for sim", now.strftime("%H:%M:%S")]


def build_parser():
    ap = argparse.ArgumentParser(
        prog="python -m x52_simconnect", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
    src = DemoSource() if args.demo else SimSource()
    clock = ClockSync(mfd, clock=not args.no_clock, brightness=not args.no_auto_brightness)
    buttons = None
    if not args.no_buttons:
        buttons = ButtonReader()
        buttons.start()

    pager = Pager(len(PAGES), start=args.page - 1)
    last_cycle = time.time()
    last_mode = None
    redraw_at = 0
    usb_failures = 0
    mfd.set_lines(["MSFS -> X52 MFD", "demo mode" if args.demo else "connecting...", ""], force=True)
    print(f"running, Ctrl-C to quit. next={args.next} prev={args.prev} home={args.home or '-'}")

    try:
        while True:
            now = time.time()
            changed = False
            if buttons:
                for name in buttons.presses():
                    if name == args.next:
                        changed |= pager.next(now)
                    elif name == args.prev:
                        changed |= pager.prev(now)
                    elif name == args.home:
                        changed |= pager.goto(0, now)
                    if name in FIRMWARE_BUTTONS:
                        redraw_at = now + REDRAW_DELAY
                if args.mode_pages and buttons.mode and buttons.mode != last_mode:
                    last_mode = buttons.mode
                    changed |= pager.goto(min(buttons.mode - 1, len(PAGES) - 1), now)
            if args.cycle and now - last_cycle >= args.cycle:
                changed |= pager.next(now)
                last_cycle = now
            if changed:
                print("page", pager.page + 1, PAGES[pager.page].title)

            page = PAGES[pager.page]
            values = src.read(page.vars + CLOCK_VARS) if src.ensure() else None
            lines = render(page, values) if values else waiting_screen()
            banner = pager.banner(now)
            if banner:
                lines[0] = banner

            force = bool(redraw_at) and now >= redraw_at
            if force:
                redraw_at = 0
            try:
                mfd.set_lines(lines, force=force)
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
        src.close()
        mfd.set_lines(["MSFS -> X52 MFD", "stopped", ""], force=True)


def main(argv=None):
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("SimConnect").setLevel(logging.ERROR)
    args = parse_args(argv)
    if args.list_buttons:
        print(" ".join(sorted(BUTTON_NAMES)))
        return
    run(args)


if __name__ == "__main__":
    main()
