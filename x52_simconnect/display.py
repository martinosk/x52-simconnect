"""The one place that writes text to the MFD. Pure given an ``X52Mfd``-like object and a clock.

Owns what the main loop used to juggle: the current mode (1, 2, 3), the short banner that overrides line 1
after a page or mode change, and the forced full redraw scheduled after a firmware-handled button press.
The loop calls ``tick()`` once per iteration and ``show(lines)`` with what the active app wants on screen;
everything in between (``banner``, ``force_redraw_in``) is timed from that tick."""

import time

from .formatting import clip

MODES = (1, 2, 3)
BANNER_SECONDS = 0.8  # how long "P2/5 RADIO" or "MODE 2 COMMS" is shown after a change
REDRAW_DELAY = 0.3  # forced full redraw this long after a firmware-handled button press


class Display:
    def __init__(self, mfd, banner_seconds=BANNER_SECONDS, redraw_delay=REDRAW_DELAY, clock=time.time):
        self.mfd = mfd
        self.mode = 1
        self.banner_seconds = banner_seconds
        self.redraw_delay = redraw_delay
        self._clock = clock
        self.now = clock()
        self._banner = None
        self._banner_until = 0.0
        self._redraw_at = 0.0

    def tick(self, now=None):
        """Start a loop iteration; ``now`` (default: the clock) is the time for everything until the next tick."""
        self.now = self._clock() if now is None else now
        return self.now

    def set_mode(self, mode):
        """Record the selector position. Returns True when it changed; unknown values are ignored."""
        if mode not in MODES or mode == self.mode:
            return False
        self.mode = mode
        return True

    def banner(self, text):
        """Show ``text`` on line 1 for ``banner_seconds`` (a new banner replaces the old one)."""
        self._banner = clip(text)
        self._banner_until = self.now + self.banner_seconds

    def current_banner(self):
        """The banner text while it is due, else None."""
        return self._banner if self.now < self._banner_until else None

    def force_redraw_in(self, seconds=None):
        """Rewrite all three lines after ``seconds``, to overwrite whatever the stick firmware drew."""
        self._redraw_at = self.now + (self.redraw_delay if seconds is None else seconds)

    def show(self, lines, force=False):
        """Put ``lines`` on the MFD with the banner applied. Returns the three lines actually written."""
        lines = [clip(line) for line in list(lines)[:3]]
        lines += [""] * (3 - len(lines))
        banner = self.current_banner()
        if banner is not None:
            lines[0] = banner
        if self._redraw_at and self.now >= self._redraw_at:
            self._redraw_at = 0.0
            force = True
        self.mfd.set_lines(lines, force)
        return lines
