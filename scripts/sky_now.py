"""Print what's up: Sun/Moon/planets and suggested alignment stars for a site and time.

Summary:
    A human-readable check of roboscope.sky, and the first piece of "it's 19:32 on this
    date in this ZIP code, align on a few bright stars, then point at Saturn". It moves
    nothing.

Details:
    - Site: --zip (US postal code, looked up with pgeocode; needs network once), or
      --lat/--lon[/--elev].
    - Time: --time as ISO 8601 with a zone offset (e.g. 2026-11-25T19:32-07:00), or
      --local "YYYY-MM-DD HH:MM" plus --tz (IANA name, e.g. America/Denver).
      Default: now.
    - Alignment-star options reflect the camera:
      - --camera narrow (in-tube, ~0.3° field): default isolation 3°, rivals within
        2 mag;
      - --camera wide (solver camera): isolation is irrelevant (the plate solve
        identifies stars), so it's switched off.

Usage:
    uv run python scripts/sky_now.py --lat 40 --lon -105 --elev 1600
    uv run python scripts/sky_now.py --zip 80301 --local "2026-11-25 19:32" --tz America/Denver
"""

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from roboscope.sky import BODIES, Site, Sky, StarCatalog


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--elev", type=float, default=0.0)
    ap.add_argument("--time", help="ISO 8601 with zone offset")
    ap.add_argument("--local", help='"YYYY-MM-DD HH:MM" local time (use with --tz)')
    ap.add_argument("--tz", help="IANA zone, e.g. America/Denver")
    ap.add_argument("--camera", choices=["narrow", "wide"], default="narrow")
    ap.add_argument("-n", type=int, default=3, help="number of alignment stars")
    args = ap.parse_args()

    if args.zip:
        site = Site.from_zip(args.zip, elevation_m=args.elev)
    elif args.lat is not None and args.lon is not None:
        site = Site(args.lat, args.lon, args.elev)
    else:
        ap.error("give --zip or --lat/--lon")
    if args.time:
        when = datetime.fromisoformat(args.time)
    elif args.local:
        if not args.tz:
            ap.error("--local needs --tz")
        when = datetime.strptime(args.local, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(args.tz))
    else:
        when = None

    sky = Sky(site)
    cat = StarCatalog.load()
    t = sky.time(when)
    print(f"site: {site.name or ''} lat {site.lat_deg:.4f} lon {site.lon_deg:.4f} "
          f"elev {site.elevation_m:.0f} m   time: {t.utc_iso()}")
    sun_alt = sky.sun_altitude_deg(when)
    sky_state = ("daylight" if sun_alt > -6 else "twilight" if sun_alt > -12
                 else "dark enough for bright stars" if sun_alt > -18 else "night")
    print(f"sun altitude {sun_alt:+.1f}° ({sky_state})\n")
    print("body       alt      az    sun-sep")
    for name in BODIES:
        aa = sky.body_altaz(name, when)
        sep = "" if name == "sun" else f"{sky.sun_separation_deg(aa, when):7.1f}°"
        print(f"{name:8s} {aa.alt_deg:+6.1f}° {aa.az_deg:6.1f}°  {sep}{'  (up)' if aa.alt_deg > 0 else ''}")
    iso = dict(isolation_deg=3.0, isolation_dmag=2.0) if args.camera == "narrow" else \
          dict(isolation_deg=0.0, isolation_dmag=-99.0)
    picks = cat.alignment_candidates(sky, when, n=args.n, **iso)
    print(f"\nalignment stars ({args.camera} camera):")
    for p in picks:
        print(f"  {p.star.label:14s} V {p.star.mag:+.2f}  alt {p.altaz.alt_deg:5.1f}°  az {p.altaz.az_deg:5.1f}°")
    if len(picks) < args.n:
        print(f"  (only {len(picks)} met the criteria)")


if __name__ == "__main__":
    main()
