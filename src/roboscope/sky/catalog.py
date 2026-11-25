"""Bright-star catalog: lookup, visibility, "what's near this pointing", alignment-star choice.

Details:
    Source: the HYG database v4.1 (astronexus, CC BY-SA 4.0; compiled from Hipparcos,
    Yale Bright Star and Gliese). `build_catalog` downloads the full CSV and keeps stars
    to `mag_limit` (default 6.5, about 8,900 stars; the Sun is dropped) as
    data/sky/bright_stars.csv:
      - columns: hip, name (IAU proper name or empty), ra_hours, dec_deg (J2000), mag
        (V), pm_ra_mas, pm_dec_mas;
      - hip is 0 for entries without a Hipparcos number (e.g. Mizar B, 19″ from Mizar),
        so "HIP 0" lookups are meaningless;
      - neither file is committed (size, and the share-alike licence); both are rebuilt
        by scripts/fetch_sky_data.py.
    Assumption: HYG's pmra is Hipparcos μα·cosδ (what Skyfield's `ra_mas_per_year`
    expects). Even if it isn't, the error over 26 years is <15″ for the fastest bright
    star, far below our needs.

    Designed for both alignment cameras in documentation/PLAN.md:
      - Wide solver camera (~22° field): `near()` lists the catalog stars expected in a
        field, to check a plate solve or guide star detection.
      - Narrow in-tube camera (~0.3°, sees one star): `alignment_candidates()` picks
        bright stars that are *isolated*: no other star within `isolation_deg`
        (pointing uncertainty plus the field) is within `isolation_dmag` magnitudes. A
        single bright star seen near the predicted position can then only be the
        intended one.

    Positions are computed for all stars at once with Skyfield (vectorized), so
    `visible()` over ~9k stars takes well under a second on a Mac. To be measured on
    the Pi.
"""

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from roboscope.sky.sky import AltAz, RaDec, Sky, separation_deg

HYG_URL = ("https://raw.githubusercontent.com/astronexus/HYG-Database/main/hyg/CURRENT/"
           "hygdata_v41.csv")
CATALOG_FILE = "bright_stars.csv"


@dataclass(frozen=True)
class CatalogStar:
    """One catalog entry (J2000 position, V magnitude, proper motion in mas/yr)."""
    hip: int
    name: str
    ra_hours: float
    dec_deg: float
    mag: float
    pm_ra_mas: float = 0.0
    pm_dec_mas: float = 0.0

    @property
    def label(self) -> str:
        """Proper name if it has one, else 'HIP <n>'."""
        return self.name or f"HIP {self.hip}"

    @property
    def radec(self) -> RaDec:
        return RaDec(self.ra_hours, self.dec_deg)


@dataclass(frozen=True)
class SkyStar:
    """A catalog star with its apparent alt/az at some time."""
    star: CatalogStar
    altaz: AltAz


def build_catalog(data_dir: str | Path = "data/sky", mag_limit: float = 6.5) -> Path:
    """Download HYG (if absent) and write the bright-star subset; return its path."""
    import pandas as pd
    import urllib.request

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    raw = data_dir / Path(HYG_URL).name
    if not raw.exists():
        urllib.request.urlretrieve(HYG_URL, raw)
    df = pd.read_csv(raw, low_memory=False)
    df = df[(df["mag"] <= mag_limit) & (df["id"] != 0)]          # id 0 is the Sun
    out = data_dir / CATALOG_FILE
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hip", "name", "ra_hours", "dec_deg", "mag", "pm_ra_mas", "pm_dec_mas"])
        for r in df.itertuples():
            w.writerow([int(r.hip) if r.hip == r.hip else 0,
                        r.proper if isinstance(r.proper, str) else "",
                        f"{r.ra:.7f}", f"{r.dec:.6f}", f"{r.mag:.2f}",
                        f"{0.0 if r.pmra != r.pmra else r.pmra:.2f}",
                        f"{0.0 if r.pmdec != r.pmdec else r.pmdec:.2f}"])
    return out


def _unit_vectors(ra_hours: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    ra, dec = np.radians(ra_hours * 15.0), np.radians(dec_deg)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=1)


class StarCatalog:
    """The bright-star list plus queries against a `Sky` (site + ephemeris)."""

    def __init__(self, stars: list[CatalogStar]):
        self.stars = stars
        self._ra = np.array([s.ra_hours for s in stars])
        self._dec = np.array([s.dec_deg for s in stars])
        self._mag = np.array([s.mag for s in stars])
        self._xyz = _unit_vectors(self._ra, self._dec)
        self._by_name = {s.name.lower(): s for s in stars if s.name}

    @classmethod
    def load(cls, data_dir: str | Path = "data/sky") -> "StarCatalog":
        """Load data/sky/bright_stars.csv (see `build_catalog`)."""
        path = Path(data_dir) / CATALOG_FILE
        with path.open() as f:
            stars = [CatalogStar(int(r["hip"]), r["name"], float(r["ra_hours"]),
                                 float(r["dec_deg"]), float(r["mag"]),
                                 float(r["pm_ra_mas"]), float(r["pm_dec_mas"]))
                     for r in csv.DictReader(f)]
        return cls(stars)

    def __len__(self) -> int:
        return len(self.stars)

    def by_name(self, name: str) -> CatalogStar:
        """Star by IAU proper name, case-insensitive ('Vega'), or 'HIP 91262'.

        Raises:
            KeyError: not found, or 'HIP 0' (the placeholder for no HIP number).
        """
        key = name.strip().lower()
        if key.startswith("hip"):
            hip = int(key[3:])
            if hip == 0:
                raise KeyError(name)
            for s in self.stars:
                if s.hip == hip:
                    return s
            raise KeyError(name)
        return self._by_name[key]

    # -- positions ---------------------------------------------------------------

    def altaz_all(self, sky: Sky, when: datetime | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Apparent (alt_deg, az_deg) arrays for every star, in catalog order."""
        from skyfield.api import Star

        stars = Star(ra_hours=self._ra, dec_degrees=self._dec,
                     ra_mas_per_year=np.array([s.pm_ra_mas for s in self.stars]),
                     dec_mas_per_year=np.array([s.pm_dec_mas for s in self.stars]))
        app = sky.observer.at(sky.time(when)).observe(stars).apparent()
        alt, az, _ = app.altaz(temperature_C=sky.temperature_c, pressure_mbar="standard")
        return alt.degrees, az.degrees

    def altaz(self, star: CatalogStar, sky: Sky, when: datetime | None = None) -> AltAz:
        """Apparent alt/az of one star (with proper motion)."""
        sub = StarCatalog([star])
        alt, az = sub.altaz_all(sky, when)
        return AltAz(float(alt[0]), float(az[0]))

    # -- queries -----------------------------------------------------------------

    def visible(self, sky: Sky, when: datetime | None = None, min_alt_deg: float = 15.0,
                mag_limit: float | None = None) -> list[SkyStar]:
        """Stars above `min_alt_deg` (and at least as bright as `mag_limit`), brightest first."""
        alt, az = self.altaz_all(sky, when)
        ok = alt >= min_alt_deg
        if mag_limit is not None:
            ok &= self._mag <= mag_limit
        idx = np.flatnonzero(ok)
        idx = idx[np.argsort(self._mag[idx])]
        return [SkyStar(self.stars[i], AltAz(float(alt[i]), float(az[i]))) for i in idx]

    def near(self, direction: AltAz, radius_deg: float, sky: Sky,
             when: datetime | None = None, mag_limit: float | None = None
             ) -> list[tuple[SkyStar, float]]:
        """Stars within `radius_deg` of an apparent alt/az direction, nearest first.

        Returns (star with its alt/az, separation in degrees) pairs. Use it for a field
        of view (wide camera) or a pointing-uncertainty circle (narrow camera).
        """
        alt, az = self.altaz_all(sky, when)
        out = []
        for i in range(len(self.stars)):
            if mag_limit is not None and self._mag[i] > mag_limit:
                continue
            here = AltAz(float(alt[i]), float(az[i]))
            sep = separation_deg(direction, here)
            if sep <= radius_deg:
                out.append((SkyStar(self.stars[i], here), sep))
        return sorted(out, key=lambda p: p[1])

    def is_isolated(self, star: CatalogStar, isolation_deg: float,
                    isolation_dmag: float) -> bool:
        """True if no other catalog star within `isolation_deg` is brighter than
        star.mag + isolation_dmag. Uses the catalog (J2000) geometry, so it's
        time-independent."""
        v = _unit_vectors(np.array([star.ra_hours]), np.array([star.dec_deg]))[0]
        cos_r = math.cos(math.radians(isolation_deg))
        close = (self._xyz @ v) >= cos_r
        rivals = close & (self._mag <= star.mag + isolation_dmag)
        # The star itself is always in `rivals`.
        return int(rivals.sum()) <= 1

    def alignment_candidates(self, sky: Sky, when: datetime | None = None, n: int = 3,
                             min_alt_deg: float = 25.0, max_alt_deg: float = 75.0,
                             mag_limit: float = 2.5, min_separation_deg: float = 40.0,
                             isolation_deg: float = 3.0, isolation_dmag: float = 2.0,
                             min_sun_sep_deg: float = 30.0) -> list[SkyStar]:
        """Pick up to `n` stars for alignment, brightest first, greedily spread out.

        Each pick:
          - lies between `min_alt_deg` and `max_alt_deg` (alt-az mounts handle the
            zenith badly);
          - is at least `mag_limit` bright and Sun-safe;
          - is isolated (see `is_isolated`). Set `isolation_deg` to the pointing
            uncertainty plus half the camera field;
          - is at least `min_separation_deg` from every earlier pick, so the picks
            constrain the mount model.

        Returns fewer than `n` if the sky doesn't allow more.
        """
        picks: list[SkyStar] = []
        for s in self.visible(sky, when, min_alt_deg, mag_limit):
            if s.altaz.alt_deg > max_alt_deg:
                continue
            if not sky.is_safe(s.altaz, when, min_sun_sep_deg):
                continue
            if not self.is_isolated(s.star, isolation_deg, isolation_dmag):
                continue
            if any(separation_deg(s.altaz, p.altaz) < min_separation_deg for p in picks):
                continue
            picks.append(s)
            if len(picks) == n:
                break
        return picks
