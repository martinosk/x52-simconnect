"""The one place that writes text to the MFD. Pure given an ``X52Mfd``-like object and a clock.

Owns what the main loop used to juggle: the current mode (1, 2, 3), the short banner that overrides line 1
after a page or mode change, the forced full redraw scheduled after a firmware-handled button press, and the
pacing of line writes. The loop calls ``tick()`` once per iteration and ``show(lines)`` with what the active app
wants on screen; everything in between (``banner``, ``urgent``, ``force_redraw_in``) is timed from that tick.

The pacing exists because of the stick's firmware: line writes that come close together can make the whole X52
stop reporting for a second or two (see the ``x52-mfd`` skill). So ``show`` never writes more than one line per
call, that is per loop tick. Lines that merely follow changing values go out ``write_interval`` seconds apart,
the longest-waiting first. What the pilot asked for (a page, a mode, a scroll), new events and the forced redraw
go out on consecutive ticks, top line first."""

import time

from .formatting import clip

MODES = (1, 2, 3)
BANNER_SECONDS = 0.8  # how long "P2/5 RADIO" or "MODE 2 COMMS" is shown after a change
REDRAW_DELAY = 0.3  # forced full redraw this long after a firmware-handled button press
WRITE_INTERVAL = 1.0  # seconds between line writes that only follow changing values
LINES = 3


class Display:
    def __init__(
        self,
        mfd,
        banner_seconds=BANNER_SECONDS,
        redraw_delay=REDRAW_DELAY,
        write_interval=WRITE_INTERVAL,
        clock=time.time,
    ):
        self.mfd = mfd
        self.mode = 1
        self.banner_seconds = banner_seconds
        self.redraw_delay = redraw_delay
        self.write_interval = write_interval
        self._clock = clock
        self.now = clock()
        self._banner = None
        self._banner_until = 0.0
        self._redraw_at = 0.0
        self._urgent = set()  # lines that need not wait for the interval, each served once
        self._next_write = float("-inf")
        self.forget()

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
        """Show ``text`` on line 1 for ``banner_seconds`` (a new banner replaces the old one), without waiting."""
        self._banner = clip(text)
        self._banner_until = self.now + self.banner_seconds
        self._urgent = set(range(LINES))

    def current_banner(self):
        """The banner text while it is due, else None."""
        return self._banner if self.now < self._banner_until else None

    def urgent(self):
        """Do not wait for ``write_interval``: the pilot did something, or an event came in. Each line that
        differs is written once, on consecutive ticks, top line first."""
        self._urgent = set(range(LINES))

    def force_redraw_in(self, seconds=None):
        """Rewrite all three lines after ``seconds``, to overwrite whatever the stick firmware drew. Nothing is
        written until then: it would be drawn over."""
        self._redraw_at = self.now + (self.redraw_delay if seconds is None else seconds)

    def forget(self):
        """Nothing is known to be on the MFD any more (start-up, the firmware drew on it, the stick was reopened)."""
        self.on_screen = [None] * LINES
        self._written_at = [float("-inf")] * LINES

    def show(self, lines, force=False):
        """Bring the MFD one line closer to ``lines`` with the banner applied, if a write is due. ``force``
        rewrites all three lines, over the next ticks. Returns the three lines wanted; ``on_screen`` holds what
        has actually been written."""
        lines = [clip(line) for line in list(lines)[:LINES]]
        lines += [""] * (LINES - len(lines))
        banner = self.current_banner()
        if banner is not None:
            lines[0] = banner
        if self._redraw_at and not force:
            if self.now < self._redraw_at:
                return lines
            force = True
        if force:
            self._redraw_at = 0.0
            self.forget()
            self._urgent = set(range(LINES))
        changed = [i for i in range(LINES) if lines[i] != self.on_screen[i]]
        self._urgent.intersection_update(changed)
        if self._urgent:
            self._write(min(self._urgent), lines)
        elif changed and self.now >= self._next_write:
            self._write(min(changed, key=lambda i: self._written_at[i]), lines)
        return lines

    def _write(self, line, lines):
        self.mfd.set_line(line, lines[line], True)  # we decide what is stale, not the driver's cache
        self.on_screen[line] = lines[line]
        self._written_at[line] = self.now
        self._next_write = self.now + self.write_interval
        self._urgent.discard(line)
