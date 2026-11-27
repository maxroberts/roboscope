"""Tests of scripts/mc_speed_test.py (the hardware speed test) against the simulated controller.

Details:
    The script lives in scripts/, not the package, so it is loaded from its path. A real
    `MotorController` drives `SimulatedController`, which models the firmware's protocol,
    cap and limits but not motor dynamics (coast is ~0). Checks:
      - hold_time caps legs by duration and by travel, and a leg too short for the
        steady window reports no rate (and fails);
      - a ladder measures each speed both ways, holds it within tolerance and ends near
        the start;
      - an axis stops climbing after a leg with controller events, and that leg fails;
      - main() refuses option values that break the 10° test-motion rule, and speeds
        above the calibrated cap.
"""

import importlib.util
from pathlib import Path

import pytest

from roboscope.mount.controller import MotorController, SimulatedController

_spec = importlib.util.spec_from_file_location(
    "mc_speed_test", Path(__file__).parents[1] / "scripts" / "mc_speed_test.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

CPD = {"az": 3085.0, "alt": 4230.0}


@pytest.fixture
def mc():
    m = MotorController(SimulatedController(), counts_per_degree=CPD, boot_timeout_s=1)
    yield m
    m.close()


def test_hold_time_limits_duration_and_travel():
    assert st.hold_time(1300, 3085, 5.0, 2.2) == 2.2                       # slow: time-bound
    assert st.hold_time(12000, 3085, 5.0, 2.2) == pytest.approx(5.0 / (12000 / 3085))


def test_too_short_leg_has_no_rate(mc):
    leg = st.run_leg(mc, "az", 12000, 0.5, skip_s=0.3)
    assert leg.rate is None and not leg.ok(0.03)


def test_ladder_measures_both_directions(mc):
    lines = []
    legs = st.run_ladder(mc, ["az", "alt"], [1300.0, 12000.0], CPD, max_leg_deg=4.0,
                         max_hold_s=1.0, skip_s=0.3, say=lines.append)
    assert [(g.axis, g.cps) for g in legs] == [
        ("az", 1300), ("az", -1300), ("az", 12000), ("az", -12000),
        ("alt", 1300), ("alt", -1300), ("alt", 12000), ("alt", -12000)]
    assert all(g.ok(0.03) for g in legs), lines
    assert len(lines) == 8
    p = mc.position()
    assert abs(p.degrees["az"]) < 0.2 and abs(p.degrees["alt"]) < 0.2    # leg timing jitter


def test_events_stop_the_climb(mc):
    real = st.run_leg

    def leg_with_event(m, axis, cps, hold_s, skip_s=0.5):
        leg = real(m, axis, cps, hold_s, skip_s)
        if abs(cps) > 2000:                         # an event arriving during the leg
            leg.events.append("EVT garbage az delta=1949")
        return leg

    st.run_leg = leg_with_event
    try:
        legs = st.run_ladder(mc, ["az"], [1300.0, 6500.0, 12000.0], CPD, 4.0, 0.8, 0.2,
                             say=lambda s: None)
    finally:
        st.run_leg = real
    assert [g.cps for g in legs] == [1300, -1300, 6500, -6500]
    assert legs[0].ok(0.03) and not legs[2].ok(0.03)


@pytest.mark.parametrize("args", [["--limit", "12"], ["--max-leg", "9.5"],
                                  ["--speeds", "20000"]])
def test_main_refuses_unsafe_options(args):
    with pytest.raises(SystemExit):
        st.main(args)
