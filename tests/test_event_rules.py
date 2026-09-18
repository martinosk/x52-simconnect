"""The rule engine behind the event log: every policy, every rule's wording, no sim."""

import pytest

from x52_simconnect import event_rules as er
from x52_simconnect.apps import ALL_VARS
from x52_simconnect.sources import DEMO_TIMELINE, demo_values

MAX_TEXT = 12  # the rule texts are kept this short


def engine(*rules):
    return er.RuleEngine(rules or er.RULES)


def feed(eng, timeline):
    """Run ``[(now, {var: value}), ...]`` through ``eng``; returns ``[(now, lines)]`` for ticks that logged."""
    out = []
    for now, values in timeline:
        lines = eng.update(values, now)
        if lines:
            out.append((now, lines))
    return out


# ------------------------------------------------------------------------------------------ policies
def test_first_value_is_a_baseline_not_an_event():
    eng = engine(er.Rule("FLAPS_HANDLE_INDEX", er.rint, lambda n: f"FLAPS {n}", er.change()))
    assert eng.update({"FLAPS_HANDLE_INDEX": 2}, 0) == []
    assert eng.update({"FLAPS_HANDLE_INDEX": 2}, 1) == []
    assert eng.update({"FLAPS_HANDLE_INDEX": 3}, 2) == ["FLAPS 3"]
    assert eng.update({"FLAPS_HANDLE_INDEX": 3}, 3) == []


def test_change_logs_every_transition_once():
    eng = engine(er.Rule("GEAR_HANDLE_POSITION", er.flag, er.either("GEAR DOWN", "GEAR UP"), er.change()))
    timeline = [(0, {"GEAR_HANDLE_POSITION": 1}), (1, {"GEAR_HANDLE_POSITION": 0}), (2, {"GEAR_HANDLE_POSITION": 0})]
    timeline += [(3, {"GEAR_HANDLE_POSITION": 1})]
    assert feed(eng, timeline) == [(1, ["GEAR UP"]), (3, ["GEAR DOWN"])]


def test_bools_are_thresholded_not_truth_tested():
    eng = engine(er.Rule("AUTOPILOT_NAV1_LOCK", er.flag, er.toggle("AP NAV"), er.change()))
    assert eng.update({"AUTOPILOT_NAV1_LOCK": 1.4e-311}, 0) == []  # denormal "false", seen live
    assert eng.update({"AUTOPILOT_NAV1_LOCK": 0.0}, 1) == []  # still false: no line
    assert eng.update({"AUTOPILOT_NAV1_LOCK": 1.0}, 2) == ["AP NAV ON"]


def test_none_resets_the_baseline():
    eng = engine(er.Rule("FLAPS_HANDLE_INDEX", er.rint, lambda n: f"FLAPS {n}", er.change()))
    eng.update({"FLAPS_HANDLE_INDEX": 0}, 0)
    assert eng.update({"FLAPS_HANDLE_INDEX": None}, 1) == []  # flight loading
    assert eng.update(None, 2) == []  # sim gone
    assert eng.update({"FLAPS_HANDLE_INDEX": 2}, 3) == []  # new baseline, not "FLAPS 2"
    assert eng.update({"FLAPS_HANDLE_INDEX": 3}, 4) == ["FLAPS 3"]


def test_settled_waits_until_the_value_stops_moving():
    eng = engine(er.Rule("AUTOPILOT_HEADING_LOCK_DIR", er.rint, lambda d: f"HDG BUG {d:03d}", er.settled(0.5)))
    turning = [(0, {"AUTOPILOT_HEADING_LOCK_DIR": 270})]
    turning += [(0.25 * i, {"AUTOPILOT_HEADING_LOCK_DIR": 270 + i}) for i in range(1, 9)]  # knob turning 2 s
    still = [(2.0 + 0.25 * i, {"AUTOPILOT_HEADING_LOCK_DIR": 278}) for i in range(1, 4)]
    assert feed(eng, turning + still) == [(2.5, ["HDG BUG 278"])]
    assert not eng.settling


def test_settled_back_to_the_logged_value_logs_nothing():
    eng = engine(er.Rule("ELEVATOR_TRIM_PCT", er.num, lambda s: f"TRIM {s * 100:+.0f}%", er.step(0.01, 0.5)))
    timeline = [(0, {"ELEVATOR_TRIM_PCT": 0.10}), (0.25, {"ELEVATOR_TRIM_PCT": 0.13})]
    assert feed(eng, timeline) == []
    assert eng.settling
    timeline = [(0.5, {"ELEVATOR_TRIM_PCT": 0.10}), (1.0, {"ELEVATOR_TRIM_PCT": 0.10})]
    assert feed(eng, timeline + [(2.0, {"ELEVATOR_TRIM_PCT": 0.1})]) == []
    assert not eng.settling


def test_step_quantises_and_debounces():
    eng = engine(er.Rule("GENERAL_ENG_THROTTLE_LEVER_POSITION:1", er.num, lambda s: f"THR {s:.0f}%", er.step(5, 0.5)))
    thr = "GENERAL_ENG_THROTTLE_LEVER_POSITION:1"
    jitter = [(0, {thr: 50}), (0.25, {thr: 51.2}), (0.5, {thr: 49.1}), (1.0, {thr: 52.4})]  # all within 50 %
    assert feed(eng, jitter) == []
    assert not eng.settling
    push = [(2.0, {thr: 61}), (2.25, {thr: 68}), (2.5, {thr: 74}), (2.75, {thr: 75}), (3.0, {thr: 75.4})]
    push += [(3.25, {thr: 75.2}), (3.5, {thr: 74.9})]
    assert feed(eng, push) == [(3.0, ["THR 75%"])]  # one line, once the 75 % step has held 0.5 s


def test_step_without_debounce_logs_each_quantised_change():
    eng = engine(er.Rule("SPOILERS_HANDLE_POSITION", er.num, lambda s: f"SPOILER {s * 100:.0f}%", er.step(0.1, 0)))
    timeline = [(0, {"SPOILERS_HANDLE_POSITION": 0}), (1, {"SPOILERS_HANDLE_POSITION": 0.04})]
    timeline += [(2, {"SPOILERS_HANDLE_POSITION": 0.08}), (3, {"SPOILERS_HANDLE_POSITION": 0.5})]
    assert feed(eng, timeline) == [(2, ["SPOILER 10%"]), (3, ["SPOILER 50%"])]


def test_step_does_not_flap_on_a_boundary():
    eng = engine(er.Rule("GENERAL_ENG_THROTTLE_LEVER_POSITION:1", er.num, lambda s: f"THR {s:.0f}%", er.step(5, 0.5)))
    thr = "GENERAL_ENG_THROTTLE_LEVER_POSITION:1"
    timeline = [(0, {thr: 80}), (1, {thr: 82.6}), (2, {thr: 82.4}), (3, {thr: 82.7}), (4, {thr: 82.3})]
    assert feed(eng, timeline) == []  # seen live: THR 85, 80, 85 from a lever nobody touched
    timeline = [(5, {thr: 84}), (6, {thr: 84}), (7, {thr: 82.4}), (8, {thr: 82.4})]
    assert feed(eng, timeline) == [(6, ["THR 85%"])]  # and once on 85 it stays there


def test_a_burst_of_changes_is_one_line():
    eng = engine()
    eng.update(demo_values(0), 0)
    restarted = dict(demo_values(0), LIGHT_NAV=0, LIGHT_TAXI=1, LIGHT_STROBE=1, LIGHT_BEACON=1, SIM_ON_GROUND=0)
    restarted["GENERAL_ENG_THROTTLE_LEVER_POSITION:1"] = 100  # still settling when the burst is seen
    assert eng.update(restarted, 1.0) == ["5 CHANGES"]
    assert eng.update(restarted, 2.0) == []  # the throttle was part of the same jump
    assert not eng.settling
    assert eng.update(dict(restarted, LIGHT_NAV=1), 3.0) == ["NAV LTS ON"]


def test_quantise_turns_negative_zero_into_zero():
    assert str(er.step(0.01).quantise(-0.004)) == "0.0"
    assert er.step(0.01).quantise(True) is True  # bools untouched
    assert er.change().quantise(118.7) == 118.7


# ------------------------------------------------------------------------------------------ the rule table
def test_rule_table_is_consistent():
    assert len(er.VARS) == len(set(er.VARS))
    assert set(er.VARS) <= set(ALL_VARS)
    assert set(v for _, v, _ in DEMO_TIMELINE) <= set(er.VARS)
    assert set(er.VARS) <= set(demo_values(0))


def test_loggable_key_events_are_discrete_and_known_to_the_sim():
    assert er.loggable("TOGGLE_ALTERNATE_STATIC") and er.loggable("FLAPS_INCR")
    assert not er.loggable("AXIS_THROTTLE_SET") and not er.loggable("THROTTLE_SET")
    assert not er.loggable("COM_RADIO_SET_HZ")  # *_SET_*: a bound knob fires it continuously
    assert not any(er.loggable(name) for name in er.REJECTED_EVENTS)
    assert not er.loggable("PAUSE_TOGGLE")  # the sim's own controls are not cockpit actions
    assert not er.loggable("Not supported")  # a placeholder row in Python-SimConnect's table


SAMPLES = {  # realistic raw values per SimVar; bools and anything unlisted get the defaults
    "FLAPS_HANDLE_INDEX": [0, 4, 10],
    "SPOILERS_HANDLE_POSITION": [0, 0.5, 1],
    "ELEVATOR_TRIM_PCT": [-1, -0.12, 0, 0.5, 1],
    "GENERAL_ENG_THROTTLE_LEVER_POSITION:1": [0, 45, 100],
    "AUTOPILOT_HEADING_LOCK_DIR": [0, 270, 359.6],
    "AUTOPILOT_ALTITUDE_LOCK_VAR": [0, 5000, 35000, 45000],
    "COM_ACTIVE_FREQUENCY:1": [118.0, 136.975],
    "COM_STANDBY_FREQUENCY:1": [118.0, 136.975],
    "NAV_ACTIVE_FREQUENCY:1": [108.0, 117.95],
    "NAV_STANDBY_FREQUENCY:1": [108.0, 117.95],
    "TRANSPONDER_CODE:1": [0, 0x7000, 0x7777],
    "KOHLSMAN_SETTING_MB": [948, 1013.25, 1050],
    "GENERAL_ENG_PROPELLER_LEVER_POSITION:1": [0, 45.4, 100],
    "GENERAL_ENG_MIXTURE_LEVER_POSITION:1": [0, 40.1, 100],
    "FUEL_TANK_SELECTOR:1": [0, 1, 2, 3, 19],
}


@pytest.mark.parametrize("rule", er.RULES, ids=[r.var for r in er.RULES])
def test_every_rule_fits_next_to_the_age_column(rule):
    for raw in SAMPLES.get(rule.var, [0, 1, 1.4e-311]):
        text = rule.text(rule.policy.quantise(rule.key(raw)))
        assert isinstance(text, str) and len(text) <= MAX_TEXT, (rule.var, raw, text)


def test_known_wordings():
    lines = {}
    for rule in er.RULES:
        lines[rule.var] = rule.text(rule.policy.quantise(rule.key(demo_values(63)[rule.var])))
    assert lines["FLAPS_HANDLE_INDEX"] == "FLAPS 2"
    assert lines["GEAR_HANDLE_POSITION"] == "GEAR DOWN"
    assert lines["BRAKE_PARKING_POSITION"] == "PARK BRK OFF"
    assert lines["ELEVATOR_TRIM_PCT"] == "TRIM +12%"
    assert lines["GENERAL_ENG_THROTTLE_LEVER_POSITION:1"] == "THR 100%"
    assert lines["AUTOPILOT_HEADING_LOCK_DIR"] == "HDG BUG 300"
    assert lines["AUTOPILOT_ALTITUDE_LOCK_VAR"] == "ALT SEL 5000"
    assert lines["COM_STANDBY_FREQUENCY:1"] == "STBY 121.900"
    assert lines["TRANSPONDER_CODE:1"] == "SQK 7000"
    assert lines["SIM_ON_GROUND"] == "AIRBORNE"
    assert lines["ALTERNATE_STATIC_SOURCE_OPEN"] == "NORM STATIC"
    assert lines["NAV_ACTIVE_FREQUENCY:1"] == "NAV1 110.500"
    assert lines["KOHLSMAN_SETTING_MB"] == "QNH 1013"
    assert lines["FUEL_TANK_SELECTOR:1"] == "TANK ALL"
    assert er._alt_sel(35000) == "ALT SEL35000"


def test_full_engine_with_all_none_and_with_demo_data():
    eng = engine()
    assert eng.update(dict.fromkeys(ALL_VARS), 0) == []
    assert eng.update(demo_values(0), 1) == []  # baseline
    assert eng.update(demo_values(3), 2) == ["BATTERY ON"]


def test_demo_timeline_logs_one_line_per_step_in_rule_order():
    eng = engine()
    eng.update(demo_values(0), 0)
    lines = []
    for t in range(1, 92):
        lines += eng.update(demo_values(t), float(t))
    assert len(lines) == len(DEMO_TIMELINE)
    assert lines[:5] == ["BATTERY ON", "BEACON ON", "THR 45%", "FLAPS 1", "PARK BRK OFF"]
    assert eng.update(demo_values(92), 92.0) == []  # the loop wraps silently
