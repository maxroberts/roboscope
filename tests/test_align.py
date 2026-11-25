"""Tests for roboscope.align: model maths, camera geometry, spiral search, simulated alignment.

Details:
    The simulated session uses a "truth" PointingModel for the mount's real errors, a
    narrow camera (0.35″/px, 3280×2464 → 0.32°×0.24°, like a Pi camera at the NG 102's
    prime focus), and a finder that reports the brightest catalog star (V ≤ 3) in the
    field. The session starts with a zero model and must find its stars by spiral
    search, then point accurately. Skipped without data/sky (scripts/fetch_sky_data.py).
"""

import math
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest

from roboscope.align import (
    HARDWARE_TERMS, AlignmentSession, CameraFrame, MountAngles, Observation, PointingModel,
    spiral_offsets,
)
from roboscope.sky import AltAz, separation_deg

DATA = Path("data/sky")
needs_sky = pytest.mark.skipif(not (DATA / "bright_stars.csv").exists(),
                               reason="sky data not fetched")
NIGHT = datetime(2026, 1, 15, 4, 0, tzinfo=timezone.utc)
TRUTH = {"IA": 1.2, "IE": -0.4, "AN": 0.2, "AW": -0.15, "CA": 0.05, "SA": 0.01,
         "SE": 0.0, "NPAE": 0.0, "TF": 0.0}


def truth_model() -> PointingModel:
    return PointingModel(dict(TRUTH))


# -- model ---------------------------------------------------------------------------

def test_sky_mount_roundtrip():
    m = truth_model()
    for az, alt in [(10, 20), (200, 60), (359.5, 45), (90, 78)]:
        s = AltAz(alt, az)
        back = m.mount_to_sky(m.sky_to_mount(s))
        assert separation_deg(s, back) * 3600 < 0.01


def test_sky_to_mount_picks_nearest_turn():
    m = PointingModel()
    assert m.sky_to_mount(AltAz(30, 350), near_az_deg=0).az_deg == pytest.approx(-10)
    assert m.sky_to_mount(AltAz(30, 10), near_az_deg=360).az_deg == pytest.approx(370)


def _observations(n, noise_arcsec=2.0, seed=1):
    rng = random.Random(seed)
    truth = truth_model()
    out = []
    for _ in range(n):
        m = MountAngles(rng.uniform(-170, 170), rng.uniform(15, 75))
        s = truth.mount_to_sky(m)
        n_alt, n_az = (rng.gauss(0, noise_arcsec / 3600) for _ in range(2))
        s = AltAz(s.alt_deg + n_alt, s.az_deg + n_az / math.cos(math.radians(s.alt_deg)))
        out.append(Observation(NIGHT, m, s, noise_arcsec / 3600))
    return out


def test_fit_recovers_all_terms_from_many_observations():
    model = PointingModel()
    rep = model.fit(_observations(25), terms=("IA", "IE", "AN", "AW", "CA", "SA"))
    assert rep.rms_arcsec < 4
    for k in ("IA", "IE", "AN", "AW", "CA"):
        assert model.params[k] == pytest.approx(TRUTH[k], abs=10 / 3600), k
    assert model.params["SA"] == pytest.approx(TRUTH["SA"], abs=1e-4)


def test_term_selection_by_count():
    assert PointingModel().fit(_observations(1)).terms == ("IA", "IE")
    assert PointingModel().fit(_observations(2)).terms == ("IA", "IE", "AN", "AW")
    assert PointingModel().fit(_observations(3)).terms == ("IA", "IE", "AN", "AW")
    assert len(PointingModel().fit(_observations(6)).terms) == 9


def test_fit_handles_azimuth_wrap():
    obs = [Observation(NIGHT, MountAngles(359.8, 40), AltAz(40.1, 0.3)),
           Observation(NIGHT, MountAngles(-0.4, 50), AltAz(50.1, 0.1))]
    m = PointingModel()
    m.fit(obs, terms=("IA", "IE"))
    assert m.params["IA"] == pytest.approx(0.5, abs=1e-6)
    assert m.params["IE"] == pytest.approx(0.1, abs=1e-6)


def test_fit_needs_observations():
    with pytest.raises(ValueError):
        PointingModel().fit([])


# -- camera frame -----------------------------------------------------------------

def test_camera_frame_roundtrip_and_orientation():
    f = CameraFrame(0.35, roll_deg=17)
    bore = AltAz(40, 120)
    star = AltAz(40.05, 120.03)
    px = f.pixel_of(star, bore)
    back = f.boresight(star, *px)
    assert separation_deg(back, bore) * 3600 < 0.01
    plain = CameraFrame(0.35)
    above = plain.pixel_of(AltAz(40.05, 120), bore)        # star higher than boresight
    assert above[1] < plain.center[1] and above[0] == pytest.approx(plain.center[0])
    east = plain.pixel_of(AltAz(40, 120.05), bore)          # star at larger azimuth
    assert east[0] > plain.center[0]
    flipped = CameraFrame(0.35, flip_x=True).pixel_of(AltAz(40, 120.05), bore)
    assert flipped[0] < plain.center[0]


def test_fov():
    w, h = CameraFrame(0.35).fov_deg
    assert w == pytest.approx(0.319, abs=1e-3) and h == pytest.approx(0.2396, abs=1e-3)


# -- spiral ---------------------------------------------------------------------------

def test_spiral_starts_at_center_and_covers_disk():
    step, radius = 0.2, 1.0
    pts = list(spiral_offsets(step, radius))
    assert pts[0] == (0.0, 0.0)
    assert all(math.hypot(u, v) <= radius + 1e-9 for u, v in pts)
    rng = random.Random(0)
    for _ in range(300):                                    # coverage of the inner disk
        r, th = (radius - step) * math.sqrt(rng.random()), rng.uniform(0, 2 * math.pi)
        x, y = r * math.cos(th), r * math.sin(th)
        assert min(math.hypot(x - u, y - v) for u, v in pts) <= step * 0.71
    rings = [max(abs(u), abs(v)) for u, v in pts]
    assert rings == sorted(rings)                           # outward order


# -- simulated session ---------------------------------------------------------------

class SimMount:
    def __init__(self):
        self.angles = MountAngles(0.0, 30.0)
        self.moves = 0

    def goto(self, m):
        self.angles = m
        self.moves += 1

    def position(self):
        return self.angles


class SimFinder:
    """Reports the pixel of the brightest V ≤ 3 star in the field, as seen by the truth
    model, with Gaussian centroid noise (default 1 px ≈ 0.35″)."""

    def __init__(self, mount, frame, sky, cat, truth, noise_px=1.0, seed=3):
        self.mount, self.frame, self.sky, self.cat, self.truth = mount, frame, sky, cat, truth
        self.noise_px, self.rng = noise_px, random.Random(seed)

    def locate(self):
        bore = self.truth.mount_to_sky(self.mount.position())
        r = math.hypot(*self.frame.fov_deg) / 2
        for s, _ in sorted(self.cat.near(bore, r, self.sky, NIGHT, mag_limit=3.0),
                           key=lambda p: p[0].star.mag):
            px, py = self.frame.pixel_of(s.altaz, bore)
            if self.frame.contains(px, py):
                return (px + self.rng.gauss(0, self.noise_px),
                        py + self.rng.gauss(0, self.noise_px))
        return None


def _session(model, n_stars, keep=()):
    from roboscope.sky import Site, Sky, StarCatalog

    sky = Sky(Site(40.0, -105.0, 1600.0), data_dir=DATA)
    cat = StarCatalog.load(DATA)
    frame = CameraFrame(0.35)
    mount = SimMount()
    truth = truth_model()
    session = AlignmentSession(sky, cat, mount, SimFinder(mount, frame, sky, cat, truth),
                               frame, model, clock=lambda: NIGHT, search_radius_deg=2.0,
                               keep=keep)
    result = session.run(n_stars=n_stars)
    errs = sorted(separation_deg(truth.mount_to_sky(model.sky_to_mount(s.altaz)), s.altaz)
                  for s in cat.visible(sky, NIGHT, min_alt_deg=20, mag_limit=2.0)[:15])
    return result, errs


@needs_sky
def test_five_star_alignment_from_nothing_points_everywhere():
    model = PointingModel()                                 # knows nothing yet
    result, errs = _session(model, 5)
    assert all(s.found for s in result.stars) and len(result.stars) == 5
    assert errs[-1] < 30 / 3600                             # < 30″ anywhere (1 px noise)
    print(f"5 stars from scratch: frames {[s.frames for s in result.stars]}, "
          f"max error {errs[-1] * 3600:.1f}\"")


@needs_sky
def test_three_star_alignment_with_kept_hardware_terms():
    model = PointingModel({k: (TRUTH[k] if k in HARDWARE_TERMS else 0.0) for k in TRUTH})
    result, errs = _session(model, 3, keep=HARDWARE_TERMS)
    assert all(s.found for s in result.stars)
    assert errs[-1] < 30 / 3600
    print(f"3 stars + kept hardware terms: frames {[s.frames for s in result.stars]}, "
          f"max error {errs[-1] * 3600:.1f}\"")


@needs_sky
def test_three_star_alignment_alone_is_only_locally_good():
    model = PointingModel()
    result, errs = _session(model, 3)
    assert all(s.found for s in result.stars)
    assert errs[len(errs) // 2] < 0.2                       # median OK...
    assert errs[-1] > 0.2                                   # ...but far stars are off


def test_model_save_load(tmp_path):
    m = truth_model()
    m.save(tmp_path / "m.json")
    assert PointingModel.load(tmp_path / "m.json").params == m.params


def test_fit_keep_holds_terms():
    model = PointingModel({k: (TRUTH[k] if k in HARDWARE_TERMS else 0.0) for k in TRUTH})
    rep = model.fit(_observations(3), keep=HARDWARE_TERMS)
    assert set(rep.terms) == {"IA", "IE", "AN", "AW"}
    assert model.params["SA"] == TRUTH["SA"]
    for k in ("IA", "IE", "AN", "AW"):
        assert model.params[k] == pytest.approx(TRUTH[k], abs=10 / 3600), k
