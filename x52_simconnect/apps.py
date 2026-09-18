"""The "apps" the mode selector switches between, one per selector position. Pure: each app renders three
lines from a dict of SimVar values and reacts to MFD button presses; the ``Display`` it gets is only used for
banners. Every app keeps its own state (page, scroll position) while another one is showing.

    1  PagesApp     the data pages in pages.py, Start/Stop and Reset page through them
    2  CommsApp     placeholder until spec 05 (tuned station, ATC text)
    3  EventLogApp  rolling log of what was last triggered in the cockpit (event_rules.py decides what)
"""

import logging
from collections import deque
from itertools import islice

from .event_rules import RULES, VARS, RuleEngine
from .formatting import clip
from .pages import CLOCK_VARS, PAGES, render

log = logging.getLogger(__name__)


class App:
    """What the main loop needs from an app. Subclass and override what the app uses."""

    name = "APP"
    vars: tuple[str, ...] = ()  # SimVars this app needs in the feed

    def __init__(self, display):
        self.display = display

    def observe(self, values, now, events=()):
        """Every loop tick, showing or not: the feed values (None while there is no sim data) and the names
        of the sim key events fired since the last tick. For apps that collect history in the background."""

    def on_button(self, name):
        """A press of ``name`` (see buttons.BUTTON_NAMES) while this app is showing."""

    def on_hold(self, name, seconds):
        """Every tick while ``name`` stays pressed, with the time since the press (after ``on_button``)."""

    def tick(self, now):
        """Once per loop while this app is showing, before ``render``; for timers."""

    def render(self, values):
        """Three lines, at most 16 characters each, from the feed values (None allowed)."""
        return [self.name, "", ""]

    def on_activate(self):
        """The selector was just turned to this app."""

    def on_deactivate(self):
        """The selector was just turned away from this app."""


class PagesApp(App):
    """Today's pages: the current index, wrapped paging with a ``P2/5 RADIO`` banner, optional auto-cycle."""

    name = "PAGES"
    vars = tuple(dict.fromkeys(n for p in PAGES for n in p.vars))

    def __init__(self, display, start=0, next="START_STOP", prev="RESET", home="", cycle=0.0):  # noqa: A002
        super().__init__(display)
        self.pages = PAGES  # the built-in ones, until a config replaces them (``set_pages``)
        self.page = start % len(self.pages)
        self.buttons = {next: self.next, prev: self.prev, home: self.home}
        self.buttons.pop("", None)
        self.cycle = cycle
        self._last_cycle = None

    def set_pages(self, pages):
        """Swap in another set of pages (from the config), staying on the same position where it exists."""
        self.pages = tuple(pages)
        self.page = min(self.page, len(self.pages) - 1)

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
    """Mode 2 placeholder: shows its name until spec 05 fills it in."""

    name = "COMMS"

    def render(self, values):
        return [self.name, "see spec 05", ""]


class EventLogApp(App):
    """Mode 3: the last cockpit actions, newest on top::

        FLAPS 2
        PARK BRK OFF
        GEAR DOWN

    No ages or times: the screen must only change when something happens, because line writes are rationed
    (see display.py).

    Lines come from ``event_rules.RuleEngine`` (SimVar changes) and, when no rule explains a key event within
    ``KEY_EVENT_GRACE`` seconds, from the event itself as ``EV FLAPS_INCR``. The log keeps collecting while
    another app is showing. Start/Stop scrolls to older entries, Reset to newer ones, Reset held for
    ``HOLD_SECONDS`` jumps back to the newest. New entries while scrolled do not move the view; a ``+3 NEW``
    marker goes in front of line 1 instead. With ``mirror`` the newest entry also flashes as a
    banner while another app is showing."""

    name = "EVENTS"
    vars = VARS
    HISTORY = 100
    VISIBLE = 3
    KEY_EVENT_GRACE = 0.3  # a key event waits this long for a rule to explain it before it is logged as EV
    KEY_EVENT_REPEAT = 1.0  # the same unexplained key event within this window makes no second line
    HOLD_SECONDS = 1.0

    def __init__(self, display, mirror=False, older="START_STOP", newer="RESET", rules=RULES):
        super().__init__(display)
        self.engine = RuleEngine(rules)
        self.history = deque(maxlen=self.HISTORY)  # newest first: (time, text)
        self.scroll = 0  # index of the entry on line 1
        self.unseen = 0  # entries added while scrolled away from the newest
        self.mirror = mirror
        self.older, self.newer = older, newer
        self.showing = False
        self._now = 0.0
        self._pending = {}  # key event name -> time first seen, waiting for a rule to explain it
        self._ev_logged = {}  # key event name -> when it was last logged as EV, or repeated since
        self._held_done = False
        self.key_events = True  # log key events no rule explains, as EV NAME
        self.ignored_key_events = frozenset()

    def configure(self, hidden=(), key_events=True, ignored_key_events=()):
        """What to log (``config.EventsConfig``): rules to hide, by SimVar, and which key events make a line."""
        self.engine.hidden = frozenset(hidden)
        self.key_events = key_events
        self.ignored_key_events = frozenset(ignored_key_events)

    # ------------------------------------------------------------------ collecting
    def observe(self, values, now, events=()):
        self._now = now
        lines = self.engine.update(values, now)
        for name in events:
            if now - self._ev_logged.get(name, float("-inf")) < self.KEY_EVENT_REPEAT:
                self._ev_logged[name] = now  # a held hat repeating an event already logged: one line
            else:
                self._pending.setdefault(name, now)
        if self.engine.fired or self.engine.settling:
            self._pending.clear()  # a state change explains whatever was pressed, shown or hidden
        else:
            for name, since in list(self._pending.items()):
                if now - since >= self.KEY_EVENT_GRACE:
                    del self._pending[name]
                    self._ev_logged[name] = now
                    if self.key_events and name not in self.ignored_key_events:
                        lines.append(f"EV {name}")
        for text in lines:
            self.add(now, text)

    def add(self, now, text):
        self.history.appendleft((now, text))
        if self.scroll:
            self.scroll = min(self.scroll + 1, self.max_scroll)  # keep what is on screen where it is
            self.unseen += 1
        if self.showing:
            self.display.urgent()
        elif self.mirror:
            self.display.banner(text)
        log.info("event: %s", text)

    # ------------------------------------------------------------------ scrolling
    @property
    def max_scroll(self):
        return max(0, len(self.history) - self.VISIBLE)

    def scroll_by(self, delta):
        self.scroll = max(0, min(self.scroll + delta, self.max_scroll))
        if not self.scroll:
            self.unseen = 0
        self.display.urgent()

    def newest(self):
        self.scroll = self.unseen = 0
        self.display.urgent()

    def on_button(self, name):
        if name == self.older:
            self.scroll_by(+1)
        elif name == self.newer:
            self.scroll_by(-1)
            self._held_done = False

    def on_hold(self, name, seconds):
        if name == self.newer and seconds >= self.HOLD_SECONDS and not self._held_done:
            self._held_done = True
            self.newest()

    def on_activate(self):
        self.showing = True
        self.newest()

    def on_deactivate(self):
        self.showing = False

    # ------------------------------------------------------------------ rendering
    def render(self, values):
        if not self.history:
            return [self.name, "no events yet", ""]
        lines = []
        for i, (_, text) in enumerate(islice(self.history, self.scroll, self.scroll + self.VISIBLE)):
            if i == 0 and self.scroll and self.unseen:
                text = f"+{min(self.unseen, self.scroll)} NEW {text}"
            lines.append(clip(text))
        return lines + [""] * (self.VISIBLE - len(lines))


APP_CLASSES = {1: PagesApp, 2: CommsApp, 3: EventLogApp}

# Everything the feed streams, in a stable order and without duplicates: every app's vars plus the clock's.
ALL_VARS = tuple(dict.fromkeys([n for cls in APP_CLASSES.values() for n in cls.vars] + list(CLOCK_VARS)))


def feed_vars(config):
    """``ALL_VARS`` plus whatever the configured pages read: what the feed has to stream for ``config``."""
    return tuple(dict.fromkeys(ALL_VARS + config.vars))


def configure_apps(apps, config):
    """Apply a ``config.Config`` to running apps: the mode 1 pages and what the mode 3 log shows."""
    apps[1].set_pages(config.build_pages())
    events = config.events
    apps[3].configure(events.hidden, events.key_events, events.ignored_key_events)


def build_apps(display, events_banner=False, **pages_options):
    """One instance per selector position; ``pages_options`` go to ``PagesApp``."""
    return {
        1: PagesApp(display, **pages_options),
        2: CommsApp(display),
        3: EventLogApp(display, mirror=events_banner),
    }
