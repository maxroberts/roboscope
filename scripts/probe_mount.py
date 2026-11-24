"""Read-only hardware probe of the AudioStar, also used to compare serial cables.

Summary:
    Connects to the handbox, prints what it reports (identity, position, site, time,
    alignment status), and optionally hammers it with position queries to
    measure link reliability and latency. It never sends a motion command. The only
    state it may change is display precision: it sends `:U#` once if the handbox is in
    low precision.

Usage:
    uv run python scripts/probe_mount.py --list
    uv run python scripts/probe_mount.py --port /dev/tty.usbserial-XXXX
    uv run python scripts/probe_mount.py --port ... --repeat 500 -v   # cable test

Each query that fails is reported with its exception rather than aborting, because
part of the point is finding out which commands this firmware supports.
"""

import argparse
import logging
import statistics
import time

from serial.tools import list_ports

from roboscope.mount import Mount, MountError, Transport


def list_serial_ports() -> None:
    """Print every serial port with its description and USB vendor/product IDs."""
    for p in list_ports.comports():
        ids = f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else "-"
        print(f"{p.device:40s} {ids:10s} {p.description}")


def report(mount: Mount) -> None:
    """Print one line per read-only query, with its result or the error it raised."""
    queries = {
        "mode (ACK)": mount.ping,
        "info": mount.info,
        "radec": mount.radec,
        "altaz": mount.altaz,
        "site": mount.site,
        "utc offset": mount.utc_offset,
        "local time": mount.local_datetime,
        "sidereal time (h)": mount.sidereal_time,
        "slewing": mount.is_slewing,
        "alignment :GW": mount.alignment_status,
    }
    for name, fn in queries.items():
        try:
            result = fn()
        except (MountError, ValueError) as e:
            result = f"FAILED: {type(e).__name__}: {e}"
        print(f"{name:20s} {result}")


def stress(mount: Mount, rounds: int) -> None:
    """Run `rounds` of radec+altaz queries; report failure counts and latency."""
    latencies, failures = [], {}
    for _ in range(rounds):
        start = time.monotonic()
        try:
            mount.radec()
            mount.altaz()
        except (MountError, ValueError) as e:
            failures[type(e).__name__] = failures.get(type(e).__name__, 0) + 1
            continue
        latencies.append(time.monotonic() - start)
    ok = len(latencies)
    print(f"\n{rounds} rounds (4 queries each): {ok} ok, failures: {failures or 'none'}")
    if ok:
        p95 = sorted(latencies)[int(0.95 * (ok - 1))]
        print(f"round latency ms: median {1000 * statistics.median(latencies):.0f}, "
              f"p95 {1000 * p95:.0f}, max {1000 * max(latencies):.0f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--port", help="serial device of the handbox cable")
    ap.add_argument("--repeat", type=int, default=0, help="stress rounds (cable test)")
    ap.add_argument("--gap", type=float, default=0.05, help="min seconds between commands")
    ap.add_argument("-v", action="store_true", help="log raw serial traffic")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.v else logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    if args.list or not args.port:
        list_serial_ports()
        return
    mount = Mount(Transport.open(args.port, min_gap_s=args.gap))
    with mount:
        try:
            mount.ensure_high_precision()
        except MountError as e:
            print(f"high precision: FAILED: {e}")
        report(mount)
        if args.repeat:
            stress(mount, args.repeat)


if __name__ == "__main__":
    main()
