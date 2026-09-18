"""
MSFS 2024 -> X52 (non-Pro) MFD bridge: the CLI and the main loop.

  python -m x52_simconnect                # connect to the sim (retries until it is up)
  python -m x52_simconnect --demo         # fake flight data, no sim needed
  python -m x52_simconnect --page 2       # start on a given page
  python -m x52_simconnect --cycle 5      # auto-advance pages every 5 s
  python -m x52_simconnect --mode 3       # force a mode, ignoring the stick's selector
  python -m x52_simconnect --events-banner  # flash each new event-log entry in the other modes too
  python -m x52_simconnect --demo --no-stick  # no sim, no X52: try the config UI anywhere

While it runs, http://127.0.0.1:8052 is the config UI: edit the mode 1 pages and choose what the mode 3
event log shows (config_server.py; --ui-port, --no-ui, --config FILE).

The rotary mode selector on the stick picks the app on the MFD: 1 = data pages, 2 = comms,
3 = event log (see apps.py). A "MODE 2 COMMS" banner flashes on each change.

Paging in mode 1 (defaults): Start/Stop = next page, Reset = previous page. A "P2/5 RADIO"
banner flashes on each change. Remap with --next/--prev/--home (see --list-buttons).
In mode 3, Start/Stop scrolls to older entries, Reset to newer ones, Reset held 1 s to the newest.

The stick firmware also acts on the three MFD buttons: Function cycles clock 1/2/3 and
toggles the stopwatch view, Start/Stop and Reset drive the stopwatch. That cannot be
disabled over USB, so Function is unmapped by default. Logitech's driver also writes the
name of any pressed button on MFD line 2 and blanks that line on release. The display is
therefore force-redrawn shortly after every press and release, to restore what we drew.
"""

import argparse
import logging
import time
from collections import deque
from datetime import datetime

import usb.core

from . import config as cfg
from .apps import build_apps, configure_apps, feed_vars
from .buttons import BUTTON_NAMES, ButtonReader
from .clock_sync import ClockSync
from .config_server import DEFAULT_PORT, ConfigServer, ConfigStore
from .display import MODES, Display
from .mfd import NullMfd, X52Mfd
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
        self._down = {}  # button name -> time of its press, while it stays held

    def select_mode(self, mode):
        """Switch to ``mode`` if it differs: activate its app and flash a ``MODE n NAME`` banner."""
        if not self.display.set_mode(mode):
            return False
        self.active.on_deactivate()
        self.active = self.apps[mode]
        self.active.on_activate()
        self._down.clear()
        self.display.force_redraw_in()
        self.display.banner(f"MODE {mode} {self.active.name}")
        log.info("mode %d %s", mode, self.active.name)
        return True

    def step(self, presses=(), stick_mode=None, values=None, now=None, held=(), events=()):
        """One loop tick. ``stick_mode`` is the selector as the reader saw it (None before the first report,
        which means "leave it"); ``values`` None means no sim data yet; ``held`` are the buttons currently
        down; ``events`` the sim key events fired since the last tick. Returns the lines written."""
        now = self.display.tick(now)
        mode = self.forced_mode or stick_mode
        if mode is not None:
            self.select_mode(mode)
        for app in self.apps.values():
            app.observe(values, now, events)
        # The firmware (clock buttons) and Logitech's driver (any button: its name on line 2 while held,
        # blank after) both draw on the MFD; redraw everything shortly after each press and release.
        for name in presses:
            self._down[name] = now
            self.active.on_button(name)
            self.display.force_redraw_in()
        for name, since in list(self._down.items()):
            if name in held:
                self.active.on_hold(name, now - since)
            else:
                del self._down[name]
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
    ap.add_argument("--events-banner", action="store_true", help="flash new event-log entries in every mode")
    ap.add_argument("--no-clock", action="store_true", help="leave the firmware clock/date alone")
    ap.add_argument("--no-auto-brightness", action="store_true", help="do not dim MFD/LEDs by time of day")
    ap.add_argument("--list-buttons", action="store_true", help="print valid button names and exit")
    ap.add_argument("--config", metavar="FILE", help="config file (default: config.toml in the per-user app data)")
    ap.add_argument("--ui-port", type=int, default=DEFAULT_PORT, help=f"config UI port (default {DEFAULT_PORT})")
    ap.add_argument("--no-ui", action="store_true", help="do not serve the config UI")
    ap.add_argument("--no-stick", action="store_true", help="run without an X52; the config UI mirrors the MFD")
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


def open_config(args):
    """The ``ConfigStore`` for ``args`` and, unless switched off or the port is taken, the UI serving it."""
    try:
        from .sim_feed import simvar_unit, simvar_units  # the SimConnect package: Windows only

        table = simvar_units()
        store = ConfigStore(
            args.config or cfg.default_path(),
            known=lambda name: simvar_unit(name, table) is not None,
            units=lambda name: simvar_unit(name, table),
        )
    except ImportError:
        store = ConfigStore(args.config or cfg.default_path())
    server = None
    if not args.no_ui:
        try:
            server = ConfigServer(store, port=args.ui_port).start()
            print(f"config UI: {server.url}  (file: {store.path})")
        except OSError as e:
            print(f"config UI not started on port {args.ui_port}: {e}")
    return store, server


def live_status(bridge, lines, values, demo, recent_events):
    """What the config UI mirrors: the MFD as it is now, the newest log entries, the latest key events."""
    return {
        "mfd": lines,
        "mode": bridge.display.mode,
        "app": bridge.active.name,
        "sim": values is not None,
        "demo": demo,
        "values": values,
        "log": [text for _, text in list(bridge.apps[3].history)[:8]],
        "key_events": list(recent_events),
    }


def run(args):
    mfd = NullMfd() if args.no_stick else X52Mfd()
    display = Display(mfd)
    store, server = open_config(args)
    names = feed_vars(store.config)
    src = DemoSource() if args.demo else SimSource(names)
    recent_events = deque(maxlen=12)  # newest first, for the UI's "ignore this one" list
    clock = ClockSync(mfd, clock=not args.no_clock, brightness=not args.no_auto_brightness)
    buttons = selector = None
    if not args.no_buttons and not args.no_stick:
        buttons = ButtonReader()
        buttons.start()
        # Logitech's filter driver hides the selector from HID but answers for it itself (saitek_driver.py).
        try:
            selector = DriverModeReader()
            print("mode selector: read through the Logitech driver")
        except OSError as e:
            print(f"mode selector: read from HID reports ({e})")

    apps = build_apps(
        display,
        events_banner=args.events_banner,
        start=args.page - 1,
        next=args.next,
        prev=args.prev,
        home=args.home,
        cycle=args.cycle,
    )
    configure_apps(apps, store.config)
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
            held = [name for name, down in buttons.state.items() if down] if buttons else []
            stick_mode = selector.read() if selector else None
            if stick_mode is None and buttons:
                stick_mode = buttons.mode
            new_config = store.take()
            if new_config:
                configure_apps(apps, new_config)
                names = feed_vars(new_config)
                src.set_names(names)
                display.banner("CONFIG APPLIED")
                log.info("config applied: %d pages, %d vars", len(new_config.pages), len(names))
            values = src.read(names) if src.ensure() else None
            events = src.events() if values else []
            for name in events:
                if name in recent_events:
                    recent_events.remove(name)
                recent_events.appendleft(name)
            try:
                lines = bridge.step(presses, stick_mode, values, now=now, held=held, events=events)
                store.live = live_status(bridge, lines, values, args.demo, recent_events)
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
        if server:
            server.stop()
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
