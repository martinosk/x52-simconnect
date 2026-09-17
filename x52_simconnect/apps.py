"""The "apps" the mode selector switches between, one per selector position. Pure: each app renders three
lines from a dict of SimVar values and reacts to MFD button presses; the ``Display`` it gets is only used for
banners. Every app keeps its own state (page, scroll position) while another one is showing.

    1  PagesApp     the data pages in pages.py, Start/Stop and Reset page through them
    2  CommsApp     placeholder until spec 05 (tuned station, ATC text)
    3  EventLogApp  placeholder until spec 02 (rolling log of cockpit actions)
"""

import logging

from .formatting import hms
from .pages import CLOCK_VARS, PAGES, render

log = logging.getLogger(__name__)


class App:
    """What the main loop needs from an app. Subclass and override what the app uses."""

    name = "APP"
    vars: tuple[str, ...] = ()  # SimVars this app needs in the feed

    def __init__(self, display):
        self.display = display

    def on_button(self, name):
        """A press of ``name`` (see buttons.BUTTON_NAMES) while this app is showing."""

    def tick(self, now):
        """Once per loop while this app is showing, before ``render``; for timers."""

    def render(self, values):
        """Three lines, at most 16 characters each, from the feed values (None allowed)."""
        return [self.name, "", ""]

    def on_activate(self):
        """The selector was just turned to this app."""


class PagesApp(App):
    """Today's pages: the current index, wrapped paging with a ``P2/5 RADIO`` banner, optional auto-cycle."""

    name = "PAGES"
    vars = tuple(dict.fromkeys(n for p in PAGES for n in p.vars))

    def __init__(self, display, start=0, next="START_STOP", prev="RESET", home="", cycle=0.0):  # noqa: A002
        super().__init__(display)
        self.pages = PAGES
        self.page = start % len(self.pages)
        self.buttons = {next: self.next, prev: self.prev, home: self.home}
        self.buttons.pop("", None)
        self.cycle = cycle
        self._last_cycle = None

    @property
    def title(self):
        return self.pages[self.page].title

    def goto(self, index):
        """Switch to ``index`` (wrapped). Returns True when the page actually changed."""
        index %= len(self.pages)
        if index == self.page:
            return False
        self.page = index
        self.display.banner(f"P{self.page + 1}/{len(self.pages)} {self.title}")
        log.info("page %d %s", self.page + 1, self.title)
        return True

    def next(self):
        return self.goto(self.page + 1)

    def prev(self):
        return self.goto(self.page - 1)

    def home(self):
        return self.goto(0)

    def on_button(self, name):
        action = self.buttons.get(name)
        if action:
            action()

    def tick(self, now):
        if not self.cycle:
            return
        if self._last_cycle is None:
            self._last_cycle = now
        elif now - self._last_cycle >= self.cycle:
            self._last_cycle = now
            self.next()

    def render(self, values):
        return render(self.pages[self.page], values)


class CommsApp(App):
    """Mode 2 placeholder: shows its name and sim time until spec 05 fills it in."""

    name = "COMMS"
    vars = ("ZULU_TIME",)

    def render(self, values):
        return [self.name, "see spec 05", f"Z {hms(values.get('ZULU_TIME'))}"]


class EventLogApp(App):
    """Mode 3 placeholder: shows its name and sim time until spec 02 fills it in."""

    name = "EVENTS"
    vars = ("ZULU_TIME",)

    def render(self, values):
        return [self.name, "see spec 02", f"Z {hms(values.get('ZULU_TIME'))}"]


APP_CLASSES = {1: PagesApp, 2: CommsApp, 3: EventLogApp}

# Everything the feed streams, in a stable order and without duplicates: every app's vars plus the clock's.
ALL_VARS = tuple(dict.fromkeys([n for cls in APP_CLASSES.values() for n in cls.vars] + list(CLOCK_VARS)))


def build_apps(display, **pages_options):
    """One instance per selector position; ``pages_options`` go to ``PagesApp``."""
    return {1: PagesApp(display, **pages_options), 2: CommsApp(display), 3: EventLogApp(display)}
