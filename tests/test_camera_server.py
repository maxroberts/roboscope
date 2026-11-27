"""End-to-end tests: camera server + client over HTTP with the synthetic source (no Pi).

Details:
    Starts `CameraServer(SyntheticSource())` on a free loopback port and drives it with
    `CameraClient`, the same way roboscope drives the Pi (minus the SSH tunnel). It
    checks:
      - locate accuracy in full-sensor pixels, in both sensor modes;
      - freshness: after the star moves or the exposure changes, the next locate
        reflects it;
      - settings validation and clamping;
      - the browser endpoints (page, snapshot, MJPEG stream, whose parts carry
        X-Seq and X-Wall-Time);
      - idle mode switching and waking;
      - the preview's frame stamp (drawn bottom-left, changes with the frame number);
      - `client.connect()` on the host Pi going straight to the local hub;
      - `preview_size` in `render_preview` (shrinks by max-pool, no double reduction for
        frames that arrive reduced) and in settings validation.
"""

import time
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
        head = chunk.split(b"\r\n\r\n", 1)[0].decode()      # first part's headers
        assert "X-Seq: " in head
        wall = float(head.split("X-Wall-Time: ")[1].split("\r\n")[0])
        assert abs(time.time() - wall) < 5               # a capture time, not a placeholder
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


def test_preview_frame_stamp():
    """render_preview stamps "#seq HH:MM:SS.d" bottom-left; the stamp changes with seq."""
    import numpy as np

    from roboscope.camera.server import draw_stamp, render_preview
    from roboscope.camera.sources import CameraSettings, Frame

    img = np.full((486, 648), 100, dtype=np.uint16)
    s = CameraSettings(crosshair=False, stretch="manual", black=0, white=1000)

    def frame(seq):
        return Frame(img, scale=4, seq=seq, start_ns=0, exposure_us=1e4, gain=1.0,
                     saturation=1023 * 4, wall_time=1_000_000.0)

    a, b = render_preview(frame(7), s, None), render_preview(frame(8), s, None)
    corner = (slice(486 - 14, 486), slice(0, 120))         # 2× scale: 14 px strip
    assert (a[corner] == 255).any() and (a[corner] == 0).any()   # white text on black
    assert not np.array_equal(a[corner], b[corner])               # seq 7 vs 8 differ
    assert (a[:400] == b[:400]).all() and a[200, 300] == 25       # image itself untouched
    tiny = np.zeros((10, 10), dtype=np.uint8)
    draw_stamp(tiny, "#1")                                  # too short: no-op, no error
    assert not tiny.any()


def test_connect_on_host_pi_goes_direct(monkeypatch):
    """connect() on the host Pi uses the local hub directly, without an SSH tunnel."""
    import socket

    import roboscope.camera.client as client_mod

    monkeypatch.setattr(socket, "gethostname", lambda: client_mod.HOST_PI)
    c = client_mod.connect()
    assert c.base_url == f"http://127.0.0.1:{client_mod.PORT}" and c.tunnel is None


def test_preview_size_in_render_and_settings():
    """render_preview shrinks by preview_size (max-pool); invalid sizes are rejected."""
    import numpy as np

    from roboscope.camera.server import max_pool, render_preview
    from roboscope.camera.sources import CameraSettings, Frame

    img = np.zeros((486, 648), dtype=np.uint16)
    img[100, 200] = 4000                                      # one bright pixel ("star")
    f = Frame(img, scale=4, seq=1, start_ns=0, exposure_us=1e4, gain=1.0, saturation=4092)
    s = CameraSettings(crosshair=False, stretch="manual", black=0, white=4000)
    assert render_preview(f, s, None).shape == (486, 648)
    half = render_preview(f, s.updated({"preview_size": "half"}), None)
    assert half.shape == (243, 324) and half[50, 100] == 255   # the star survives pooling
    assert render_preview(f, s.updated({"preview_size": "quarter"}), None).shape == (121, 162)
    # Already-reduced frames (from the hub's source) aren't reduced twice.
    fq = Frame(max_pool(img, 4), scale=16, seq=1, start_ns=0, exposure_us=1e4, gain=1.0,
               saturation=4092, reduce=4)
    assert render_preview(fq, s.updated({"preview_size": "quarter"}), None).shape == (121, 162)
    with pytest.raises(ValueError):
        s.updated({"preview_size": "tiny"})
