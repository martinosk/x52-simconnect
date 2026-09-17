"""X52Mfd against a fake USB device: encodings, caching and retry logic, no hardware."""

import pytest
import usb.core

from x52_simconnect import mfd as m


class FakeDevice:
    """Stands in for a pyusb device: records (wValue, wIndex) of every control transfer."""

    idVendor, idProduct, bus, address = m.VID, m.PID, 1, 2

    def __init__(self, fail_first=0):
        self.transfers = []
        self.fail_first = fail_first  # raise USBError on this many transfers, then succeed

    def ctrl_transfer(self, bm_request_type, b_request, w_value, w_index, data, timeout):
        assert bm_request_type == m.BM_OUT_VENDOR
        assert b_request == m.REQ
        assert data is None
        if self.fail_first:
            self.fail_first -= 1
            raise usb.core.USBError("A device attached to the system is not functioning")
        self.transfers.append((w_index, w_value))


@pytest.fixture
def dev():
    return FakeDevice()


@pytest.fixture
def mfd(dev, monkeypatch):
    monkeypatch.setattr(m.X52Mfd, "RETRY_DELAY", 0)
    return m.X52Mfd(dev=dev)


# ---------------------------------------------------------------- pure encodings


def test_pack_chars_little_endian():
    assert m.pack_chars("A", "B") == 0x4241


def test_pack_clock():
    assert m.pack_clock(13, 45) == (1 << 15) | (13 << 8) | 45
    assert m.pack_clock(1, 5, h24=False) == (1 << 8) | 5


@pytest.mark.parametrize(
    "minutes, negative, magnitude",
    [(120, 0, 120), (-120, 1, 120), (0, 0, 0), (1023, 0, 1023), (1024, 1, 416), (-1100, 0, 340)],
)
def test_pack_clock_offset_wraps_like_libx52(minutes, negative, magnitude):
    assert m.pack_clock_offset(minutes) == (1 << 15) | (negative << 10) | magnitude


def test_pack_date():
    assert m.pack_date_ddmm(17, 9) == (9 << 8) | 17
    assert m.pack_year(2026) == 26


@pytest.mark.parametrize("level, expected", [(-5, 0), (0, 0), (64.9, 64), (128, 128), (300, 128)])
def test_clamp_brightness(level, expected):
    assert m.clamp_brightness(level) == expected


# ---------------------------------------------------------------- device writes


def test_set_line_clears_then_writes_eight_pairs(mfd, dev):
    mfd.set_line(0, "AB")
    assert dev.transfers[0] == (m.LINE_CMDS[0] | m.CLEAR, 0)
    pairs = dev.transfers[1:]
    assert len(pairs) == 8
    assert pairs[0] == (m.LINE_CMDS[0], m.pack_chars("A", "B"))
    assert pairs[1:] == [(m.LINE_CMDS[0], m.pack_chars(" ", " "))] * 7


def test_set_line_truncates_to_16_chars(mfd, dev):
    mfd.set_line(2, "0123456789ABCDEFXYZ")
    assert len(dev.transfers) == 9
    assert dev.transfers[-1] == (m.LINE_CMDS[2], m.pack_chars("E", "F"))


def test_unchanged_line_is_not_rewritten(mfd, dev):
    mfd.set_lines(["one", "two", "three"])
    n = len(dev.transfers)
    mfd.set_lines(["one", "two", "three"])
    assert len(dev.transfers) == n
    mfd.set_lines(["one", "TWO", "three"])
    assert len(dev.transfers) == n + 9
    mfd.set_lines(["one", "TWO", "three"], force=True)
    assert len(dev.transfers) == n + 9 + 27


def test_transient_usb_error_is_retried(mfd, dev):
    dev.fail_first = 2
    mfd.set_line(0, "ok")
    assert len(dev.transfers) == 9
    assert mfd._shown[0] == "ok".ljust(16)


def test_persistent_usb_error_raises_and_forgets_the_line(mfd, dev):
    mfd.set_line(0, "before")
    dev.fail_first = m.X52Mfd.RETRIES
    with pytest.raises(usb.core.USBError):
        mfd.set_line(0, "after")
    assert mfd._shown[0] is None  # so the next write goes through even if the text is the same
    dev.transfers.clear()
    mfd.set_line(0, "after")
    assert len(dev.transfers) == 9


def test_clock_date_brightness_are_cached(mfd, dev):
    mfd.set_clock(10, 30)
    mfd.set_clock_offset(2, 120)
    mfd.set_clock_offset(3, -60)
    mfd.set_date(17, 9, 2026)
    mfd.set_brightness(500)
    mfd.set_led_brightness(32)
    assert dev.transfers == [
        (m.TIME_CLOCK1, m.pack_clock(10, 30)),
        (m.OFFS_CLOCK2, m.pack_clock_offset(120)),
        (m.OFFS_CLOCK3, m.pack_clock_offset(-60)),
        (m.DATE_DDMM, m.pack_date_ddmm(17, 9)),
        (m.DATE_YEAR, 26),
        (m.MFD_BRIGHTNESS, 128),
        (m.LED_BRIGHTNESS, 32),
    ]
    dev.transfers.clear()
    mfd.set_clock(10, 30)
    mfd.set_date(17, 9, 2026)
    mfd.set_brightness(128)
    assert dev.transfers == []
    mfd.set_clock(10, 31)
    assert dev.transfers == [(m.TIME_CLOCK1, m.pack_clock(10, 31))]


def test_indicators(mfd, dev):
    mfd.set_shift(True)
    mfd.set_blink(False)
    assert dev.transfers == [(m.SHIFT_INDICATOR, m.INDICATOR_ON), (m.BLINK_INDICATOR, m.INDICATOR_OFF)]


def test_reopen_forgets_cache_and_finds_the_device_again(mfd, dev, monkeypatch):
    replacement = FakeDevice()
    monkeypatch.setattr(m, "find_device", lambda backend: replacement)
    mfd.set_line(0, "text")
    mfd.set_brightness(64)
    mfd.reopen()
    assert mfd.dev is replacement
    mfd.set_line(0, "text")
    mfd.set_brightness(64)
    assert len(replacement.transfers) == 10  # 9 for the line, 1 for the brightness


def test_missing_device_gives_a_helpful_error(monkeypatch):
    monkeypatch.setattr(m, "get_backend", lambda name: None)
    monkeypatch.setattr(usb.core, "find", lambda **kw: None)
    with pytest.raises(RuntimeError, match="libusb0 filter"):
        m.X52Mfd()
