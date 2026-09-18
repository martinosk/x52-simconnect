from x52_simconnect.display import Display


def writes(fake_mfd):
    """(line, text) of every line written, in order."""
    return [args[:2] for name, args in fake_mfd.calls if name == "set_line"]


def run(display, steps):
    """``steps``: (now, lines). One ``show`` per step, like the main loop."""
    for now, lines in steps:
        display.tick(now)
        display.show(lines)


def test_show_clips_and_pads_and_returns_the_lines_wanted(fake_mfd):
    d = Display(fake_mfd, clock=lambda: 0.0)
    assert d.show(["a" * 20, "b"]) == ["a" * 16, "b", ""]
    assert writes(fake_mfd) == [(0, "a" * 16)]
    assert d.on_screen == ["a" * 16, None, None]


def test_tick_uses_the_clock_by_default(fake_mfd):
    times = iter([5.0, 6.0])
    d = Display(fake_mfd, clock=lambda: next(times))
    assert d.now == 5.0
    assert d.tick() == 6.0
    assert d.tick(42.0) == 42.0


def test_banner_overrides_line_1_until_it_expires(fake_mfd):
    d = Display(fake_mfd, banner_seconds=0.8)
    d.tick(100.0)
    d.banner("P2/5 RADIO")
    d.tick(100.5)
    assert d.show(["x", "y", "z"]) == ["P2/5 RADIO", "y", "z"]
    d.tick(100.8)
    assert d.show(["x", "y", "z"]) == ["x", "y", "z"]
    assert d.current_banner() is None


def test_new_banner_replaces_the_old_one(fake_mfd):
    d = Display(fake_mfd, banner_seconds=1)
    d.tick(0)
    d.banner("first")
    d.tick(0.5)
    d.banner("second")
    d.tick(1.2)
    assert d.current_banner() == "second"


def on_screen(fake_mfd, lines):
    """A display that already shows ``lines``, with the fake's log emptied."""
    d = Display(fake_mfd, write_interval=1.0, redraw_delay=0.3)
    run(d, [(-3.0, lines), (-2.0, lines), (-1.0, lines)])
    assert d.on_screen == lines
    fake_mfd.calls.clear()
    return d


def test_never_more_than_one_line_per_show(fake_mfd):
    d = Display(fake_mfd, clock=lambda: 0.0)
    d.urgent()
    d.show(["a", "b", "c"], force=True)
    assert len(writes(fake_mfd)) == 1


def test_changing_values_go_out_one_line_per_interval_longest_waiting_first(fake_mfd):
    d = on_screen(fake_mfd, ["a", "b", "c"])
    run(d, [(t / 4, [f"a{t}", f"b{t}", f"c{t}"]) for t in range(0, 17)])  # 4 s at 4 Hz, everything changing
    assert writes(fake_mfd) == [(0, "a0"), (1, "b4"), (2, "c8"), (0, "a12"), (1, "b16")]


def test_unchanged_lines_cost_nothing_and_do_not_use_up_the_interval(fake_mfd):
    d = on_screen(fake_mfd, ["a", "b", "c"])
    run(d, [(10.0, ["a", "b", "c"]), (10.25, ["a", "B", "c"])])
    assert writes(fake_mfd) == [(1, "B")]


def test_urgent_writes_each_differing_line_once_on_consecutive_ticks_top_first(fake_mfd):
    d = on_screen(fake_mfd, ["a", "b", "c"])
    d.tick(0.0)
    d.banner("P2/5 RADIO")
    run(d, [(t / 4, ["x", f"y{t}", f"z{t}"]) for t in range(0, 7)])  # lines 2 and 3 keep changing
    # three ticks for the page, then back on the interval: the banner's end is the next write, at 1.5 s
    assert writes(fake_mfd) == [(0, "P2/5 RADIO"), (1, "y1"), (2, "z2"), (0, "x")]


def test_urgent_with_nothing_to_write_does_not_linger(fake_mfd):
    d = on_screen(fake_mfd, ["a", "b", "c"])
    d.tick(0.0)
    d.urgent()
    run(d, [(0.0, ["a", "b", "c"]), (0.25, ["a", "B", "c"]), (0.5, ["a", "B", "C"])])
    assert writes(fake_mfd) == [(1, "B")]  # the second change waits for the interval


def test_forced_redraw_waits_for_the_delay_then_rewrites_all_three_lines(fake_mfd):
    d = on_screen(fake_mfd, ["a", "b", "c"])
    d.tick(10.0)
    d.force_redraw_in()
    run(d, [(10.1, ["a2", "b", "c"]), (10.3, ["a2", "b", "c"]), (10.55, ["a2", "b", "c"]), (10.8, ["a2", "b", "c"])])
    assert writes(fake_mfd) == [(0, "a2"), (1, "b"), (2, "c")]
    assert all(args[2] is True for name, args in fake_mfd.calls if name == "set_line")


def test_forget_makes_every_line_due_again(fake_mfd):
    d = on_screen(fake_mfd, ["a", "b", "c"])
    d.forget()
    d.urgent()
    run(d, [(5.0, ["a", "b", "c"]), (5.25, ["a", "b", "c"]), (5.5, ["a", "b", "c"])])
    assert writes(fake_mfd) == [(0, "a"), (1, "b"), (2, "c")]


def test_set_mode_reports_changes_and_ignores_junk(fake_mfd):
    d = Display(fake_mfd)
    assert d.mode == 1
    assert d.set_mode(1) is False
    assert d.set_mode(2) is True
    assert d.set_mode(None) is False
    assert d.set_mode(7) is False
    assert d.mode == 2
