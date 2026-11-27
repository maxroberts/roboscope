"""Tests for roboscope.mount.controller against SimulatedController.

Details:
    These check the host-side logic: unit conversion, reply parsing, errors raised for
    ERR replies, events kept separate from replies, the calibration gate, soft limits,
    the watchdog, the encoder-feedback `move_by`, and that line fragments are
    never taken as replies. The simulator models the
    firmware's protocol and safety behavior, not the motors' dynamics.
"""

import time

import pytest

from roboscope.mount.controller import (
    ControllerError, MotorController, SimulatedController,
)


@pytest.fixture
def sim():
    return SimulatedController()


@pytest.fixture
def mc(sim):
    m = MotorController(sim, boot_timeout_s=1)
    yield m
    m.close()


def test_boot_and_hello(mc):
    assert mc.boot_line.startswith("OK mount_controller v2")
    assert mc.hello() == "mount_controller v2"


def test_speed_converts_degrees_to_counts(mc, sim):
    mc.set_speed("az", 0.1)
    mc.set_speed("alt", -0.2)
    assert sim.sent[-2:] == ["SPEED az 308.50", "SPEED alt -846.00"]
    mc.stop()
    assert sim.cps == {"az": 0.0, "alt": 0.0}


def test_position_parses_and_converts(mc, sim):
    sim.pos = {"az": 3085.0, "alt": -8460.0}
    p = mc.position()
    assert p.counts == {"az": 3085, "alt": -8460}
    assert p.degrees["az"] == pytest.approx(1.0)
    assert p.degrees["alt"] == pytest.approx(-2.0)


def test_err_reply_raises(mc):
    with pytest.raises(ControllerError, match="above MAX_CPS"):
        mc.set_speed_cps("az", 13000)                # cap 12000


def test_motion_refused_until_calibrated():
    m = MotorController(SimulatedController(calibrated=False), boot_timeout_s=1)
    with pytest.raises(ControllerError, match="not calibrated"):
        m.set_speed("az", 0.1)
    assert m.calibrate() == {"az": 189, "alt": 169}
    m.set_speed("az", 0.1)
    m.close()


def test_limits_roundtrip_and_refusal(mc, sim):
    mc.set_limits_deg("az", -2, 2)
    lo, hi = mc.limits_deg()["az"]
    assert lo == pytest.approx(-2, abs=1e-3) and hi == pytest.approx(2, abs=1e-3)
    sim.pos["az"] = 2 * 3085                     # sitting on the max limit
    with pytest.raises(ControllerError, match="at limit"):
        mc.set_speed("az", 0.1)
    mc.set_speed("az", -0.1)                     # moving away is allowed


def test_limit_stop_arrives_as_event_not_reply(mc, sim):
    mc.set_limits_deg("az", -1, 0.2)
    mc.set_speed("az", 0.4)                      # cap is 1300 counts/s ≈ 0.42°/s Az
    time.sleep(0.5)
    assert any("EVT limit az" in e for e in mc.events)
    assert mc.hello() == "mount_controller v2"   # replies stay in sync after an event


def test_watchdog_stops_when_host_silent(sim):
    sim.watchdog_s = 0.3
    m = MotorController(sim, boot_timeout_s=1)
    m.set_speed("alt", 0.2)
    time.sleep(0.8)
    assert any("watchdog" in e for e in m.events)
    assert sim.cps["alt"] == 0
    m.close()


def test_keepalive_prevents_watchdog(sim):
    sim.watchdog_s = 0.5
    m = MotorController(sim, boot_timeout_s=1)
    m.keepalive(True)
    m.set_speed("alt", 0.2)
    time.sleep(1.2)
    assert not m.events and sim.cps["alt"] != 0
    m.close()


def test_move_by_reaches_target(mc):
    err = mc.move_by("alt", 0.5, deg_per_s=0.3)   # cap ≈ 0.31°/s Alt
    # The simulator has no coast, so it stops up to ~0.15 s of travel (~0.05°) short.
    assert -0.08 < err <= 0.01
    assert mc.position().commanded_cps["alt"] == 0


def test_move_by_raises_when_controller_stops_it(mc):
    mc.set_limits_deg("az", -0.2, 0.2)
    with pytest.raises(ControllerError, match="stopped the move"):
        mc.move_by("az", 1.0, deg_per_s=0.4)


def test_mount_off_is_reported_and_motion_refused():
    m = MotorController(SimulatedController(boards_on=False), boot_timeout_s=1)
    assert "linkaz=0" in m.boot_line and "ledi_az=0" in m.boot_line
    assert m.position().link == {"az": False, "alt": False}
    with pytest.raises(ControllerError, match="not answering"):
        m.set_speed("az", 0.1)
    with pytest.raises(ControllerError, match="noack"):
        m.calibrate()
    m.close()


def test_wait_healthy(mc):
    p = mc.wait_healthy(timeout_s=2)
    assert p.link == {"az": True, "alt": True} and p.calibrated


def test_wait_healthy_times_out_when_mount_off():
    m = MotorController(SimulatedController(boards_on=False), boot_timeout_s=1)
    with pytest.raises(ControllerError, match="not healthy"):
        m.wait_healthy(timeout_s=0.6)
    m.close()


def test_fragments_are_not_taken_as_replies(mc, sim):
    # A line cut short (e.g. stale bytes in the port's buffer when it opens) must not be
    # taken as a reply. Seen on hardware 2026-11-27: "nt" was taken as LIMIT's reply.
    sim._emit("T uncommanded az delta=56 pwm=4 stop sent")
    sim._emit("nt")
    time.sleep(0.3)
    assert mc.hello() == "mount_controller v2"
