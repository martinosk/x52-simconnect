from x52_simconnect.display import Display


def written(fake_mfd):
    return [args for name, args in fake_mfd.calls if name == "set_lines"]


def test_show_clips_pads_and_passes_lines_through(fake_mfd):
    d = Display(fake_mfd, clock=lambda: 0.0)
    assert d.show(["a" * 20, "b"]) == ["a" * 16, "b", ""]
    assert written(fake_mfd) == [(["a" * 16, "b", ""], False)]


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


def test_forced_redraw_fires_once_after_the_delay(fake_mfd):
    d = Display(fake_mfd, redraw_delay=0.3)
    d.tick(10.0)
    d.force_redraw_in()
    for now in (10.1, 10.3, 10.6):
        d.tick(now)
        d.show(["", "", ""])
    assert [force for _, force in written(fake_mfd)] == [False, True, False]


def test_explicit_force(fake_mfd):
    d = Display(fake_mfd, clock=lambda: 0.0)
    d.show(["", "", ""], force=True)
    assert written(fake_mfd) == [(["", "", ""], True)]


def test_set_mode_reports_changes_and_ignores_junk(fake_mfd):
    d = Display(fake_mfd)
    assert d.mode == 1
    assert d.set_mode(1) is False
    assert d.set_mode(2) is True
    assert d.set_mode(None) is False
    assert d.set_mode(7) is False
    assert d.mode == 2
