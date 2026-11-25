"""Where things are in the sky from our site: positions, coordinate conversions, Sun safety.

Details:
    `Sky` wraps Skyfield with the JPL DE421 ephemeris (valid 1899–2053) for one `Site`.
    It is camera-agnostic: it answers "where is X at time t" and "what sky direction is
    this alt/az". The camera and solver layers turn images into directions; the
    alignment layer relates them to mount encoder positions.

    Conventions:
      - Directions are `AltAz` (degrees; azimuth from north through east) or `RaDec`
        (ICRS/J2000: RA in hours, Dec in degrees). Catalogs and plate solvers report
        J2000, so that's the frame used throughout.
      - `AltAz` values are *apparent*: they include atmospheric refraction, using
        standard pressure and `temperature_c` (default 10 °C). That's where a telescope
        must actually point. Refraction lifts objects ~0.5° at the horizon and ~1′ at 45°.
      - Times are timezone-aware `datetime`s (any zone) or None for "now". Naive
        datetimes are rejected, because a silent local/UTC mix-up would be a large
        pointing error.

    Safety: `sun_separation_deg` and `is_safe` implement the Sun exclusion zone. Every
    goto or move layer must check it, and nothing here enforces it on its own.

    Data: DE421 is read from `data_dir` (default "data/sky", relative to the working
    directory); Skyfield downloads it there on first use. For offline field use, copy
    data/sky to the Pi (`scripts/fetch_sky_data.py` fetches everything).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from skyfield.api import Loader, Star, wgs84
from skyfield.earthlib import refraction

from roboscope.sky.site import Site

# Friendly name -> DE421 target. Planets use their barycenters (differences are far below
# our pointing accuracy).
BODIES = {
    "sun": "sun", "moon": "moon", "mercury": "mercury", "venus": "venus",
    "mars": "mars barycenter", "jupiter": "jupiter barycenter",
    "saturn": "saturn barycenter", "uranus": "uranus barycenter",
    "neptune": "neptune barycenter",
}


@dataclass(frozen=True)
class AltAz:
    """Apparent horizontal direction in degrees (azimuth from north through east)."""
    alt_deg: float
    az_deg: float


@dataclass(frozen=True)
class RaDec:
    """ICRS/J2000 direction: RA in hours, Dec in degrees."""
    ra_hours: float
    dec_deg: float


def separation_deg(a: AltAz, b: AltAz) -> float:
    """Great-circle angle between two alt/az directions, in degrees."""
    a1, z1, a2, z2 = map(math.radians, (a.alt_deg, a.az_deg, b.alt_deg, b.az_deg))
    c = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(z1 - z2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


class Sky:
    """Positions of solar-system bodies and catalog stars as seen from `site`.

    Args:
        site: observer location.
        data_dir: where de421.bsp lives (downloaded there on first use).
        temperature_c: air temperature for the refraction model.
    """

    def __init__(self, site: Site, data_dir: str | Path = "data/sky",
                 temperature_c: float = 10.0):
        self.site = site
        self.temperature_c = temperature_c
        load = Loader(str(data_dir), verbose=False)
        self.ts = load.timescale()
        self.eph = load("de421.bsp")
        self._topos = wgs84.latlon(site.lat_deg, site.lon_deg, elevation_m=site.elevation_m)
        self.observer = self.eph["earth"] + self._topos

    # -- time --------------------------------------------------------------------

    def time(self, when: datetime | None = None):
        """Skyfield Time for a timezone-aware datetime, or now if None.

        Raises:
            ValueError: for a naive datetime (see module notes).
        """
        if when is None:
            when = datetime.now(timezone.utc)
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware (e.g. ZoneInfo or UTC)")
        return self.ts.from_datetime(when)

    # -- positions ---------------------------------------------------------------

    def _apparent_altaz(self, target, when) -> AltAz:
        app = self.observer.at(self.time(when)).observe(target).apparent()
        alt, az, _ = app.altaz(temperature_C=self.temperature_c, pressure_mbar="standard")
        return AltAz(alt.degrees, az.degrees)

    def body_altaz(self, name: str, when: datetime | None = None) -> AltAz:
        """Apparent alt/az of the Sun, Moon or a planet (names in `BODIES`).

        Raises:
            KeyError: unknown body name.
        """
        return self._apparent_altaz(self.eph[BODIES[name.lower()]], when)

    def radec_to_altaz(self, radec: RaDec, when: datetime | None = None) -> AltAz:
        """Apparent alt/az of a fixed J2000 direction (no proper motion)."""
        return self._apparent_altaz(Star(ra_hours=radec.ra_hours, dec_degrees=radec.dec_deg),
                                    when)

    def _raw_radec(self, altaz: AltAz, t) -> RaDec:
        """One-step inverse: remove refraction, rotate alt/az to ICRS. Leaves the
        apparent-place effects (aberration ~20″, light deflection) in."""
        # Skyfield's forward model converges to apparent = true + R(apparent), with
        # "standard" pressure 1010 mbar * exp(-elevation / 9.1 km), so the exact inverse
        # of the refraction is true = apparent - R(apparent) at that same pressure.
        pressure = 1010.0 * math.exp(-self.site.elevation_m / 9.1e3)
        geo = altaz.alt_deg - refraction(altaz.alt_deg, self.temperature_c, pressure)
        pos = self.observer.at(t).from_altaz(alt_degrees=geo, az_degrees=altaz.az_deg)
        ra, dec, _ = pos.radec()
        return RaDec(ra.hours, dec.degrees)

    def altaz_to_radec(self, altaz: AltAz, when: datetime | None = None) -> RaDec:
        """J2000 (ICRS) RA/Dec of an apparent alt/az direction.

        Exact inverse of `radec_to_altaz`, to well under 1″ above ~5° altitude. A
        one-step rotation leaves aberration in (~20″, enough to matter for a narrow
        camera), so the result is refined by iterating against the forward model.
        """
        t = self.time(when)
        target_raw = self._raw_radec(altaz, t)
        guess = target_raw
        for _ in range(3):
            forward = self.radec_to_altaz(guess, when)
            raw = self._raw_radec(forward, t)
            d_ra = (target_raw.ra_hours - raw.ra_hours + 12) % 24 - 12
            guess = RaDec((guess.ra_hours + d_ra) % 24,
                          guess.dec_deg + target_raw.dec_deg - raw.dec_deg)
        return guess

    # -- safety ------------------------------------------------------------------

    def sun_separation_deg(self, altaz: AltAz, when: datetime | None = None) -> float:
        """Angle between a pointing and the Sun, in degrees."""
        return separation_deg(altaz, self.body_altaz("sun", when))

    def is_safe(self, altaz: AltAz, when: datetime | None = None,
                min_sun_sep_deg: float = 30.0) -> bool:
        """True if `altaz` is at least `min_sun_sep_deg` from the Sun.

        Use this for every target and along slew paths. 30° is a conservative default
        for a refractor with a camera at the focus.
        """
        return self.sun_separation_deg(altaz, when) >= min_sun_sep_deg

    def sun_altitude_deg(self, when: datetime | None = None) -> float:
        """Sun's apparent altitude (negative below the horizon; < −12° is dark enough
        for bright stars, < −18° astronomical night)."""
        return self.body_altaz("sun", when).alt_deg
