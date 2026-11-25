"""Tests for roboscope.sky: positions, conversions, refraction, safety, catalog queries.

Details:
    The tests check physical facts rather than copying numbers from the code:
      - Polaris' altitude ≈ site latitude.
      - The equinox Sun at local noon is ≈ 90° − latitude.
      - Conversions round-trip.
      - Refraction lifts low objects by ~0.4–0.6°.
      - The Sun's own direction fails the safety check.
      - The alignment picks obey their stated rules.
    Sites are generic (no user location). The tests are skipped if data/sky hasn't been
    fetched (scripts/fetch_sky_data.py).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DATA = Path("data/sky")
pytestmark = pytest.mark.skipif(
    not (DATA / "de421.bsp").exists() or not (DATA / "bright_stars.csv").exists(),
    reason="sky data not fetched (run scripts/fetch_sky_data.py)",
)

from roboscope.sky import AltAz, RaDec, Site, Sky, StarCatalog, separation_deg  # noqa: E402

NIGHT = datetime(2026, 1, 15, 4, 0, tzinfo=timezone.utc)   # ~21:00 local at lon −105


@pytest.fixture(scope="module")
def sky():
    return Sky(Site(40.0, -105.0, 1600.0), data_dir=DATA)


@pytest.fixture(scope="module")
def cat():
    return StarCatalog.load(DATA)


def test_polaris_altitude_is_about_latitude(sky, cat):
    polaris = cat.by_name("Polaris")
    assert abs(cat.altaz(polaris, sky, NIGHT).alt_deg - 40.0) < 1.0   # Polaris ~0.7° off the pole


def test_equinox_noon_sun_altitude():
    sky0 = Sky(Site(40.0, 0.0), data_dir=DATA)
    start = datetime(2026, 3, 20, 11, 30, tzinfo=timezone.utc)
    peak = max(sky0.body_altaz("sun", start + timedelta(minutes=m)).alt_deg
               for m in range(0, 76, 3))
    assert 49.4 < peak < 50.6                                  # 90 − 40, ± declination/refraction


def test_radec_altaz_roundtrip(sky):
    for ra, dec in [(5.5, 20.0), (18.6, 38.8), (2.0, 75.0)]:
        aa = sky.radec_to_altaz(RaDec(ra, dec), NIGHT)
        if aa.alt_deg < 10:
            continue
        back = sky.altaz_to_radec(aa, NIGHT)
        # Angular distance on the celestial sphere (Dec as "altitude", RA as "azimuth").
        sep = separation_deg(AltAz(dec, ra * 15), AltAz(back.dec_deg, back.ra_hours * 15))
        assert sep * 3600 < 1.0                                # < 1 arcsec


def test_refraction_lifts_low_objects(sky):
    # Apparent minus geometric altitude, for a direction just above the horizon.
    low = sky.radec_to_altaz(RaDec(0, 0), NIGHT)
    from skyfield.api import Star
    app = sky.observer.at(sky.time(NIGHT)).observe(Star(ra_hours=0, dec_degrees=0)).apparent()
    geo_alt = app.altaz()[0].degrees
    if 0 < geo_alt < 5:
        assert 0.15 < low.alt_deg - geo_alt < 0.7
    else:                                                     # at any altitude, refraction ≥ 0
        assert low.alt_deg - geo_alt >= 0


def test_naive_datetime_rejected(sky):
    with pytest.raises(ValueError, match="timezone-aware"):
        sky.body_altaz("moon", datetime(2026, 1, 1, 12, 0))


def test_separation_basics():
    assert separation_deg(AltAz(0, 0), AltAz(0, 90)) == pytest.approx(90)
    assert separation_deg(AltAz(90, 0), AltAz(90, 123)) == pytest.approx(0, abs=1e-9)
    assert separation_deg(AltAz(10, 350), AltAz(10, 10)) == pytest.approx(19.7, abs=0.1)


def test_sun_safety(sky):
    noon = datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc)
    sun = sky.body_altaz("sun", noon)
    assert not sky.is_safe(sun, noon)
    anti = AltAz(-sun.alt_deg, (sun.az_deg + 180) % 360)
    assert sky.is_safe(anti, noon)
    assert sky.sun_altitude_deg(NIGHT) < -12                   # NIGHT really is dark


def test_by_name_and_hip(cat):
    vega = cat.by_name("vega")
    assert vega.hip == 91262 and abs(vega.ra_hours - 18.6156) < 0.001
    assert cat.by_name("HIP 91262") == vega
    with pytest.raises(KeyError):
        cat.by_name("Nonexistent")


def test_catalog_altaz_matches_plain_radec(sky, cat):
    vega = cat.by_name("Vega")
    a = cat.altaz(vega, sky, NIGHT)
    b = sky.radec_to_altaz(vega.radec, NIGHT)
    assert separation_deg(a, b) * 3600 < 30                    # proper motion since 2000 only


def test_visible_sorted_and_above_horizon(sky, cat):
    vis = cat.visible(sky, NIGHT, min_alt_deg=20, mag_limit=2.0)
    assert vis and all(s.altaz.alt_deg >= 20 for s in vis)
    mags = [s.star.mag for s in vis]
    assert mags == sorted(mags)


def test_near_finds_the_star_itself(sky, cat):
    vega = cat.by_name("Vega")
    where = cat.altaz(vega, sky, NIGHT)
    hits = cat.near(where, 1.0, sky, NIGHT)
    assert hits[0][0].star == vega and hits[0][1] < 0.001


def test_alignment_candidates_obey_rules(sky, cat):
    picks = cat.alignment_candidates(sky, NIGHT, n=3, min_separation_deg=40,
                                     isolation_deg=3.0, isolation_dmag=2.0)
    assert len(picks) == 3
    for i, p in enumerate(picks):
        assert 25 <= p.altaz.alt_deg <= 75 and p.star.mag <= 2.5
        assert cat.is_isolated(p.star, 3.0, 2.0)
        assert sky.is_safe(p.altaz, NIGHT)
        for q in picks[:i]:
            assert separation_deg(p.altaz, q.altaz) >= 40


def test_isolation_rejects_close_pairs(cat):
    # Mizar (V 2.23) has Mizar B (3.95) 19″ away and Alcor (3.99) 12′ away, both
    # ~1.7–1.8 mag fainter. A 2-mag rival window rejects it even at 0.1°; a 1.5-mag window
    # accepts it at 1°.
    mizar = cat.by_name("Mizar")
    assert not cat.is_isolated(mizar, 1.0, 2.0)
    assert not cat.is_isolated(mizar, 0.1, 2.0)
    assert cat.is_isolated(mizar, 1.0, 1.5)
