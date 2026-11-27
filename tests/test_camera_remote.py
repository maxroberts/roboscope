"""End-to-end tests of the two-server camera chain: upstream (Zero) → RemoteSource → hub (3B+).

Details:
    Both servers run in this process on free loopback ports. The upstream is a
    `CameraServer(SyntheticSource())`, standing in for the camera Zero. The hub is a
    `CameraServer(RemoteSource(upstream_url))`, standing in for the host Pi, and
    `CameraClient` talks to the hub as roboscope does. It checks:
      - the `/api/raw` feed's format and headers;
      - locate through the hub, in both sensor modes, with full-sensor coordinates;
      - freshness: a star move is seen by the next locate, despite the extra hop;
      - sensor settings reach the upstream, while display settings stay on the hub;
      - the hub numbers frames itself, always increasing;
      - the hub recovers by itself when the upstream server restarts (new numbering,
        default settings, which the hub sends again), and its status reports the
        outage while it lasts and clears the error afterwards;
      - an idle hub's passive fetches let the upstream go idle too;
      - preview_size: the hub's preview frames arrive reduced, preview_fps is capped at
        the source's limit for that size, and locate and the raw download still get
        full-size frames, with full-accuracy star positions;
      - demand-matched capture: the upstream caps its capture rate at about the hub's
        fetch rate, and lifts the cap while someone streams from it directly.
"""

import time
import urllib.request

import numpy as np
import pytest

import roboscope.camera.server as server_mod
from roboscope.camera.client import CameraClient
from roboscope.camera.server import CameraServer
from roboscope.camera.sources import CameraSettings, RemoteSource, SyntheticSource


@pytest.fixture
def chain():
    src = SyntheticSource(star_xy=(1000.0, 700.0))
    up = CameraServer(src, "127.0.0.1", 0, CameraSettings(exposure_ms=5.0)).start()
    up_url = f"http://127.0.0.1:{up.address[1]}"
    hub = CameraServer(RemoteSource(up_url), "127.0.0.1", 0,
                       CameraSettings(exposure_ms=5.0)).start()
    client = CameraClient(f"http://127.0.0.1:{hub.address[1]}", timeout_s=15)
    yield src, up, up_url, hub, client
    hub.stop()
    up.stop()


def test_raw_feed_format(chain):
    _, up, up_url, _, _ = chain
    with urllib.request.urlopen(up_url + "/api/raw?after=-1", timeout=10) as r:
        h, body = r.headers, r.read()
    shape = tuple(int(v) for v in h["X-Shape"].split(","))
    img = np.frombuffer(body, dtype="<u2").reshape(shape)
    assert shape == (486, 648) and int(h["X-Scale"]) == 4
    assert img.max() > img.min()                       # a real frame, not zeros
    assert int(h["X-Seq"]) >= 1 and int(h["X-Now-Ns"]) >= int(h["X-Start-Ns"])


def test_locate_through_hub(chain):
    _, _, _, _, client = chain
    x, y = client.locate()
    assert abs(x - 1000) < 0.5 and abs(y - 700) < 0.5
    client.set(mode="full")
    x, y = client.locate()
    assert abs(x - 1000) < 0.3 and abs(y - 700) < 0.3


def test_hub_sees_moves_immediately(chain):
    src, _, _, _, client = chain
    client.locate()
    src.star_xy = (400.0, 300.0)                       # "mount moved"
    x, y = client.locate()
    assert abs(x - 400) < 0.5 and abs(y - 300) < 0.5


def test_sensor_settings_reach_upstream_display_settings_do_not(chain):
    _, up, _, hub, client = chain
    client.set(exposure_ms=3.0, gain=2.0, crosshair=False, preview_fps=5.0)
    r = client.find_star()
    assert r["frame"]["exposure_us"] == pytest.approx(3000)
    assert up.settings.exposure_ms == pytest.approx(3.0) and up.settings.gain == 2.0
    assert up.settings.crosshair is True and up.settings.preview_fps == 2.0
    assert hub.settings.crosshair is False


def test_hub_sequence_numbers_increase(chain):
    _, _, _, hub, client = chain
    seqs = []
    for _ in range(5):
        client.locate()
        seqs.append(hub.latest.seq)
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_hub_recovers_from_upstream_restart(chain):
    src, up, _, hub, client = chain
    client.set(exposure_ms=3.0)                        # differs from the upstream default
    for _ in range(3):
        client.locate()                                # upstream seq now well above 1
    seq_before = hub.latest.seq
    # A long-running upstream has numbered far past anything a restarted one will reach
    # soon (the real Zero: thousands of frames at 10 fps; this synthetic source: ~40 fps).
    hub.source._up_seq = 10**9
    port = up.address[1]
    up.stop()
    time.sleep(0.5)                                    # hub fetches fail meanwhile
    deadline = time.monotonic() + 5
    while not hub.last_error and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "refused" in client.status()["last_error"]  # the outage is reported...
    up2 = CameraServer(SyntheticSource(star_xy=(500.0, 400.0)), "127.0.0.1", port,
                       CameraSettings(exposure_ms=5.0)).start()   # numbering restarts at 1
    try:
        x, y = client.locate()                         # must not wait on old numbers
        assert abs(x - 500) < 0.5 and abs(y - 400) < 0.5
        assert hub.latest.seq > seq_before
        assert up2.settings.exposure_ms == pytest.approx(3.0)    # settings re-sent
        assert client.status()["last_error"] == ""      # ...and cleared once frames flow
    finally:
        up2.stop()


def test_idle_hub_lets_upstream_idle(chain, monkeypatch):
    _, up, _, hub, client = chain
    monkeypatch.setattr(server_mod, "IDLE_AFTER_S", 0.3)
    client.locate()
    hub.last_activity -= 1.0                           # nobody has used the hub
    deadline = time.monotonic() + 10
    while not (hub.idle and up.idle) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert hub.idle and up.idle                        # passive pulls don't wake it
    monkeypatch.setattr(server_mod, "IDLE_AFTER_S", 60.0)
    assert client.locate() is not None                 # a request wakes both
    assert hub.idle is False


def test_preview_size_reduces_hub_frames_and_caps_fps(chain):
    _, _, _, hub, client = chain
    caps = RemoteSource.MAX_PREVIEW_FPS
    s = client.set(preview_size="quarter", preview_fps=30)       # the validation maximum
    assert s["preview_fps"] == caps["quarter"]                 # clamped to the measured limit
    deadline = time.monotonic() + 10
    while not (hub.latest and hub.latest.reduce == 4) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert hub.latest.reduce == 4 and hub.latest.image.shape == (121, 162)
    assert hub.latest.scale == 16                             # binned (4) × quarter (4)
    s = client.set(preview_size="full")
    assert s["preview_fps"] == caps["full"]                   # re-clamped for the new size
    assert client.status()["max_preview_fps"] == caps


def test_locate_and_raw_download_stay_full_size_with_small_preview(chain):
    src, _, _, hub, client = chain
    client.set(preview_size="quarter")
    src.star_xy = (1234.0, 567.0)
    x, y = client.locate()
    assert abs(x - 1234) < 0.5 and abs(y - 567) < 0.5         # full-grid accuracy
    img, meta = client.frame()
    assert img.shape == (486, 648)


def test_upstream_capture_matches_hub_demand(chain):
    _, up, up_url, hub, client = chain
    client.set(preview_fps=3.0)
    deadline = time.monotonic() + 10
    while up._capture_cap != pytest.approx(3.3) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert up._capture_cap == pytest.approx(3.3)              # 1.1 × the hub's rate
    time.sleep(2.0)
    assert 2.5 < up.fps < 4.5                                 # capture follows the cap
    # Someone streaming from the upstream directly gets its full rate again.
    with urllib.request.urlopen(up_url + "/stream", timeout=10) as r:
        r.read(2000)
        deadline = time.monotonic() + 5
        while up._capture_cap is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert up._capture_cap is None
