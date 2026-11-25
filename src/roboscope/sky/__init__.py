"""Sky model: where the Sun, Moon, planets and bright stars are, from our site and time.

Details:
    - `site.Site`: observer location (lat/lon/elevation), optionally from a ZIP code.
    - `sky.Sky`: Skyfield + DE421 for one site. Apparent alt/az (with refraction) of
      bodies and J2000 directions; alt/az → J2000 conversion; Sun-separation safety
      check (`is_safe`).
    - `catalog.StarCatalog`: bright stars (HYG v4.1, V ≤ 6.5) with name lookup,
      visibility, stars-near-a-direction, and alignment-star selection that supports
      both a wide solver camera and a narrow in-tube camera (isolation criterion).

    Camera-agnostic by design: images become directions in the camera/solve layers;
    this package only says where things are.

    Data files live in data/sky (not committed). Fetch them with
    `uv run python scripts/fetch_sky_data.py`.

    Typical use:
        sky = Sky(Site(40.0, -105.0, 1600))
        cat = StarCatalog.load()
        print(sky.body_altaz("saturn"))
        for s in cat.alignment_candidates(sky):
            print(s.star.label, s.altaz)
"""

from roboscope.sky.catalog import CatalogStar, SkyStar, StarCatalog, build_catalog
from roboscope.sky.site import Site
from roboscope.sky.sky import BODIES, AltAz, RaDec, Sky, separation_deg

__all__ = [
    "AltAz", "BODIES", "CatalogStar", "RaDec", "Site", "Sky", "SkyStar", "StarCatalog",
    "build_catalog", "separation_deg",
]
