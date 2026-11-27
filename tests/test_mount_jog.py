"""Tests of manual mount steering: MountJog, and its /api/mount endpoints on the camera server.

Details:
    Uses `SimulatedController` (roboscope.mount.controller) behind a real
    `MotorController`, so no hardware is needed. It checks:
      - on connect, both soft limits are lifted;
      - a jog moves the axis, and the speed is clamped to the firmware cap;
      - the dead man's switch: an axis stops once its heartbeats stop, and keeps moving
        while they continue;
      - stop() stops at once;
      - not connected (no device) and firmware refusals (uncalibrated) are reported;
      - calibrate() runs CAL;
      - the camera server: /api/status says whether there is a mount, /api/mount*
        work with one (400 for bad input, 409 for refusals) and 404 without.
"""

import json
import time
import urllib.request

import pytest

from roboscope.camera.server import CameraServer
from roboscope.camera.sources import SyntheticSource
from roboscope.mount.controller import ControllerError, MotorController, SimulatedController
from roboscope.mount.jog import MAX_CPS, UNLIMITED_COUNTS, MountJog

CPD = {"az": 3085.0, "alt": 4230.0}


def make_jog(sim=None, **kw):
    sim = sim or SimulatedController()
    jog = MountJog(lambda: MotorController(sim, counts_per_degree=CPD), poll_s=0.05, **kw)
    deadline = time.monotonic() + 5
    while jog.pos is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return jog, sim


@pytest.fixture
def jog():
    j, sim = make_jog()
    yield j, sim
    j.close()


def test_connect_lifts_both_soft_limits(jog):
    _, sim = jog
    assert sim.lim["az"] == (-UNLIMITED_COUNTS, UNLIMITED_COUNTS)
    assert sim.lim["alt"] == (-UNLIMITED_COUNTS, UNLIMITED_COUNTS)


def test_jog_moves_and_clamps_speed(jog):
    j, sim = jog
    s = j.jog("az", 1, 5.0)                                    # above the cap
    assert s["speed_dps"]["az"] == pytest.approx(MAX_CPS / CPD["az"])
    assert sim.cps["az"] == pytest.approx(MAX_CPS, abs=0.01)
    j.jog("alt", -1, 0.1)
    assert sim.cps["alt"] == pytest.approx(-0.1 * CPD["alt"], abs=0.01)
    time.sleep(0.3)
    assert sim.pos["az"] > 0 and sim.pos["alt"] < 0


def test_dead_mans_switch(jog):
    j, sim = jog
    for _ in range(8):                                        # held: heartbeats every 0.2 s
        j.jog("az", -1, 0.2)
        time.sleep(0.2)
    assert sim.cps["az"] != 0                                  # still moving while held
    time.sleep(j.hold_s + 0.3)                                 # heartbeats stop
    assert sim.cps["az"] == 0 and j.status()["speed_dps"]["az"] == 0


def test_stop_is_immediate(jog):
    j, sim = jog
    j.jog("az", 1, 0.2)
    j.jog("alt", 1, 0.2)
    j.stop()
    assert sim.cps == {"az": 0.0, "alt": 0.0}


def test_refusals_and_not_connected():
    j, _ = make_jog(SimulatedController(calibrated=False))
    try:
        assert j.status()["calibrated"] is False
        with pytest.raises(ControllerError, match="not calibrated"):
            j.jog("az", 1, 0.1)
        assert j.calibrate()["calibrated"] is True             # CAL fixes it
        j.jog("az", 1, 0.1)
        with pytest.raises(ValueError):
            j.jog("ra", 1, 0.1)
    finally:
        j.close()

    def no_device():
        raise OSError("no such device /dev/ttyACM0")
    j = MountJog(no_device, retry_s=0.1)
    try:
        time.sleep(0.2)
        assert j.status()["connected"] is False and "no such device" in j.status()["error"]
        with pytest.raises(ControllerError, match="not connected"):
            j.jog("az", 1, 0.1)
    finally:
        j.close()


def _post(url, body):
    r = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_mount_endpoints():
    j, sim = make_jog()
    srv = CameraServer(SyntheticSource(), "127.0.0.1", 0, mount=j).start()
    base = f"http://127.0.0.1:{srv.address[1]}"
    try:
        st = json.loads(urllib.request.urlopen(base + "/api/status").read())
        assert st["mount"] is True
        code, s = _post(base + "/api/mount/jog", {"axis": "az", "direction": 1, "speed": 0.2})
        assert code == 200 and s["speed_dps"]["az"] == pytest.approx(0.2)
        assert sim.cps["az"] > 0
        code, s = _post(base + "/api/mount/stop", {})
        assert code == 200 and sim.cps["az"] == 0
        assert _post(base + "/api/mount/jog", {"axis": "x", "direction": 1, "speed": 1})[0] == 400
        sim.calibrated = False
        code, s = _post(base + "/api/mount/jog", {"axis": "az", "direction": 1, "speed": 0.2})
        assert code == 409 and "not calibrated" in s["error"]
        m = json.loads(urllib.request.urlopen(base + "/api/mount").read())
        assert m["connected"] and m["max_speed_dps"]["alt"] == pytest.approx(MAX_CPS / CPD["alt"], abs=1e-3)
    finally:
        srv.stop()                                         # closes the mount too


def test_server_without_mount_has_no_mount_api():
    srv = CameraServer(SyntheticSource(), "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{srv.address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(base + "/api/status").read())["mount"] is False
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/mount")
        assert e.value.code == 404
        assert _post(base + "/api/mount/jog", {"axis": "az", "direction": 1, "speed": 1})[0] == 404
    finally:
        srv.stop()
