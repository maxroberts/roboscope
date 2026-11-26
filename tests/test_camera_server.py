"""End-to-end tests: camera server + client over HTTP with the synthetic source (no Pi).

Details:
    Starts `CameraServer(SyntheticSource())` on a free loopback port and drives it with
    `CameraClient`, the same way roboscope drives the Pi (minus the SSH tunnel). It
    checks:
      - locate accuracy in full-sensor pixels, in both sensor modes;
      - freshness: after the star moves or the exposure changes, the next locate
        reflects it;
      - settings validation and clamping;
      - the browser endpoints (page, snapshot, MJPEG stream).
"""

import urllib.request

import pytest

from roboscope.camera.client import CameraClient, CameraError
from roboscope.camera.server import CameraServer
from roboscope.camera.sources import CameraSettings, SyntheticSource


@pytest.fixture
def cam():
    src = SyntheticSource(star_xy=(1000.0, 700.0))
    srv = CameraServer(src, "127.0.0.1", 0, CameraSettings(exposure_ms=5.0)).start()
    client = CameraClient(f"http://127.0.0.1:{srv.address[1]}", timeout_s=10)
    yield src, srv, client
    srv.stop()


def test_locate_full_sensor_coordinates(cam):
    src, _, client = cam
    x, y = client.locate()
    assert abs(x - 1000) < 0.5 and abs(y - 700) < 0.5
    client.set(mode="full")
    x, y = client.locate()
    assert abs(x - 1000) < 0.3 and abs(y - 700) < 0.3


def test_locate_sees_moves_immediately(cam):
    src, _, client = cam
    client.locate()
    src.star_xy = (400.0, 300.0)                 # "mount moved"
    x, y = client.locate()                       # must not return the stale position
    assert abs(x - 400) < 0.5 and abs(y - 300) < 0.5


def test_exposure_change_is_applied_before_locate(cam):
    _, _, client = cam
    client.set(exposure_ms=1.0)
    r1 = client.find_star()
    client.set(exposure_ms=3.0)                   # peak stays below clipping
    r2 = client.find_star()
    assert r2["frame"]["exposure_us"] == pytest.approx(3000)
    assert not r2["saturated"]
    assert r2["flux"] == pytest.approx(3 * r1["flux"], rel=0.1)


def test_no_star_and_saturation(cam):
    src, _, client = cam
    src.flux_per_ms = 0.0
    assert client.locate() is None
    src.flux_per_ms = 1e6
    assert client.find_star()["saturated"] is True


def test_settings_validation_and_clamp(cam):
    _, _, client = cam
    with pytest.raises(CameraError, match="400"):
        client.set(mode="bogus")
    with pytest.raises(CameraError, match="unknown setting"):
        client.set(shutter=3)
    assert client.set(gain=100)["gain"] == 64.0                 # clamped to range
    assert client.set(crosshair="false")["crosshair"] is False


def test_frame_download(cam):
    _, _, client = cam
    img, meta = client.frame()
    assert img.shape == (486, 648) and meta["scale"] == 4
    assert img.max() > 1000                       # the star is in it


def test_browser_endpoints(cam):
    _, srv, client = cam
    base = client.base_url
    client.locate()
    page = urllib.request.urlopen(base + "/").read()
    assert b"roboscope camera" in page
    snap = urllib.request.urlopen(base + "/snapshot.jpg").read()
    assert snap[:2] == b"\xff\xd8"                # JPEG magic
    with urllib.request.urlopen(base + "/stream", timeout=10) as r:
        assert r.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        chunk = r.read(4000)
        assert b"--frame" in chunk and b"\xff\xd8" in chunk
    st = client.status()
    assert st["frame"]["shape"] == [486, 648] and st["fps"] > 0


def test_idle_mode_switches_and_wakes(cam, monkeypatch):
    import roboscope.camera.server as server_mod

    src, srv, client = cam
    monkeypatch.setattr(server_mod, "IDLE_AFTER_S", 0.3)
    srv.last_activity -= 1.0                          # pretend nobody asked for a while
    for _ in range(50):
        if srv.idle:
            break
        __import__("time").sleep(0.05)
    assert srv.idle and src.idle
    # Long timeout again before waking it: with 0.3 s, a slow machine (the 3B+) can
    # legitimately go idle again between locate() and status().
    monkeypatch.setattr(server_mod, "IDLE_AFTER_S", 60.0)
    assert client.locate() is not None                # a request wakes it ...
    assert client.status()["idle"] is False and src.idle is False   # ... at once
