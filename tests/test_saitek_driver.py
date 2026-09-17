import uuid

from x52_simconnect.saitek_driver import SHIFT_STATES, decode_shift_state


def test_every_shift_state_decodes_to_its_mode_and_pinkie():
    for guid, expected in SHIFT_STATES.items():
        assert decode_shift_state(guid.bytes_le) == expected


def test_known_live_answer_is_mode_2():
    # captured from driver 8.0.116.0 with the selector on 2
    data = bytes.fromhex("b2dcc99fbf8b2e489dd0c76e22bbc381")
    assert decode_shift_state(data) == (2, False)


def test_unknown_or_short_data_is_none():
    assert decode_shift_state(uuid.uuid4().bytes_le) is None
    assert decode_shift_state(b"\0" * 15) is None
    assert decode_shift_state(b"") is None
