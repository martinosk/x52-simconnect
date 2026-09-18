from x52_simconnect.logitech_driver import installed

X52 = r"\\?\HID#VID_06A3&PID_075C#6&136fde38&0&0000#{0c244c6f-2c78-4f0c-a036-8db0e9012b27}"
OTHER = r"\\?\HID#VID_06A3&PID_0762#6&1&0&0000#{0c244c6f-2c78-4f0c-a036-8db0e9012b27}"


def test_installed_when_the_drivers_interface_is_on_the_stick():
    assert installed([X52])
    assert installed([OTHER, X52.upper()])


def test_not_installed_without_it_or_on_another_stick():
    assert not installed([])
    assert not installed([OTHER])


def test_lookup_runs_on_this_machine():
    assert installed() in (True, False)
