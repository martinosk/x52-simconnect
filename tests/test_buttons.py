from x52_simconnect.buttons import BUTTON_BITS, BUTTON_NAMES, FIRMWARE_BUTTONS, REPORT_LEN, ButtonReader, decode


def report(*names, report_id=False):
    """Build a 14-byte X52 input report with the given buttons down (15 bytes with a hidapi report id)."""
    bits = 0
    for bit, name in BUTTON_BITS.items():
        if name in names:
            bits |= 1 << bit
    data = bytearray(REPORT_LEN)
    data[8:13] = bits.to_bytes(5, "little")
    return (b"\x00" if report_id else b"") + bytes(data)


def test_decode_maps_every_bit():
    for bit, name in BUTTON_BITS.items():
        state = decode(report(name))
        assert state[name] is True, f"bit {bit}"
        assert sum(state.values()) == 1


def test_decode_strips_hidapi_report_id():
    assert decode(report("RESET", report_id=True))["RESET"] is True


def test_decode_rejects_wrong_sizes():
    assert decode(b"\x00" * 13) is None
    assert decode(b"\x00" * 16) is None
    assert decode(b"") is None


def test_button_name_tables_are_consistent():
    assert FIRMWARE_BUTTONS <= BUTTON_NAMES
    assert {"MODE_1", "MODE_2", "MODE_3", "START_STOP", "RESET", "FUNCTION"} <= BUTTON_NAMES
    assert len(BUTTON_NAMES) == len(BUTTON_BITS)


def test_reader_edge_detects_presses():
    rd = ButtonReader()
    rd.feed(report("START_STOP"))
    rd.feed(report("START_STOP"))  # still held: no second press
    rd.feed(report())  # released
    rd.feed(report("START_STOP", "RESET"))
    assert list(rd.presses()) == ["START_STOP", "START_STOP", "RESET"]
    assert list(rd.presses()) == []
    assert rd.state["START_STOP"] is True


def test_reader_tracks_mode_selector():
    rd = ButtonReader()
    assert rd.mode is None
    rd.feed(report("MODE_2"))
    assert rd.mode == 2
    rd.feed(report())  # no mode bit in this report: keep the last known position
    assert rd.mode == 2
    rd.feed(report("MODE_3", "A"))
    assert rd.mode == 3
    assert list(rd.presses()) == ["MODE_2", "A", "MODE_3"]  # queued in bit order


def test_reader_ignores_malformed_reports():
    rd = ButtonReader()
    rd.feed(b"\x01\x02")
    assert list(rd.presses()) == []
