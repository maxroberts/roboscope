"""Download the sky-model data files into data/sky (run once per machine, e.g. before field use).

Summary:
    Fetches JPL's DE421 ephemeris (~17 MB, via Skyfield) and the HYG v4.1 star database
    (~34 MB, CC BY-SA 4.0), then writes the bright-star subset data/sky/bright_stars.csv
    used by roboscope.sky.StarCatalog. None of these files are committed (data/ is
    git-ignored).

Usage:
    uv run python scripts/fetch_sky_data.py [--mag-limit 6.5] [--data-dir data/sky]
"""

import argparse

from skyfield.api import Loader

from roboscope.sky import StarCatalog, build_catalog


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", default="data/sky")
    ap.add_argument("--mag-limit", type=float, default=6.5)
    args = ap.parse_args()

    Loader(args.data_dir)("de421.bsp")
    path = build_catalog(args.data_dir, args.mag_limit)
    cat = StarCatalog.load(args.data_dir)
    named = sum(1 for s in cat.stars if s.name)
    print(f"ephemeris OK; {path}: {len(cat)} stars to V {args.mag_limit} ({named} named)")


if __name__ == "__main__":
    main()
