"""Shared fakes. Nothing here touches the stick or the sim."""

import pytest


class FakeMfd:
    """Records every call made on the X52Mfd interface that ClockSync and the bridge use."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("set_"):

            def record(*args, **kwargs):
                self.calls.append((name, args))

            return record
        raise AttributeError(name)


@pytest.fixture
def fake_mfd():
    return FakeMfd()
