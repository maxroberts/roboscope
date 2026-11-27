"""First motion characterization of the mount through the handbox bridge (small, guarded moves).

Summary:
    Runs a fixed sequence of short timed moves at the three slower LX200 rates
    (`:RG/:RC/:RM`) plus `:Mg` guide pulses. It records the handbox's alt/az readback
    before, during and after each move, to learn:
      - whether readback tracks motion while the handbox is unaligned;
      - how fast each rate actually moves;
      - which direction letter moves which way;
      - whether pulse guiding works.
    Results print as a table and are saved as JSON lines in data/.

Safety (the scope has cables attached):
    - Each move lasts at most 3 s (1 s at the fastest rate used, `:RM`). `:RS` (max)
      is never used.
    - Azimuth/altitude are polled about 4×/s during every move. If either drifts more
      than AZ_LIMIT_DEG / ALT_LIMIT_DEG from its starting value, everything halts and
      the script aborts.
    - `:Q#` (halt all) is sent after every move and on any exit, including Ctrl-C and
      errors.
    - Each east move is followed by an equal west move (same for north/south), so the
      scope ends near where it started.
    Caveat: the limit check trusts the handbox readback. If readback does not change
    while moving, the only protection is the time budget: at most 3 s per move, at
    rates no faster than `:RM`.

Usage:
    uv run python scripts/motion_probe.py --port /dev/cu.usbserial-A9IDYQVP [--axis alt]
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from roboscope.mount import Direction, Mount, SlewRate, Transport

AZ_LIMIT_DEG = 10.0   # the user's 10° test-motion rule (2026-11-27) is the ceiling
ALT_LIMIT_DEG = 8.0
SETTLE_S = 1.5

# (rate, direction, seconds) for timed moves; ("pulse", direction, ms) for :Mg.
SEQUENCE = [
    (SlewRate.GUIDE, Direction.EAST, 3), (SlewRate.GUIDE, Direction.WEST, 3),
    (SlewRate.CENTER, Direction.EAST, 3), (SlewRate.CENTER, Direction.WEST, 3),
    (SlewRate.FIND, Direction.EAST, 1), (SlewRate.FIND, Direction.WEST, 1),
    (SlewRate.CENTER, Direction.NORTH, 3), (SlewRate.CENTER, Direction.SOUTH, 3),
    (SlewRate.FIND, Direction.NORTH, 1), (SlewRate.FIND, Direction.SOUTH, 1),
    ("pulse", Direction.EAST, 2000), ("pulse", Direction.WEST, 2000),
]


class LimitExceeded(Exception):
    """Readback moved outside the allowed envelope."""


def wrap180(deg: float) -> float:
    """Wrap an angle difference into [-180, 180)."""
    return (deg + 180.0) % 360.0 - 180.0


def read(mount: Mount) -> tuple[float, float, float]:
    """(time, alt, az) from the handbox readback."""
    p = mount.altaz()
    return time.monotonic(), p.alt_deg, p.az_deg


def check(home: tuple[float, float, float], now: tuple[float, float, float]) -> None:
    """Raise LimitExceeded if `now` is outside the envelope around `home`."""
    d_alt, d_az = now[1] - home[1], wrap180(now[2] - home[2])
    if abs(d_az) > AZ_LIMIT_DEG or abs(d_alt) > ALT_LIMIT_DEG:
        raise LimitExceeded(f"d_alt={d_alt:+.2f}, d_az={d_az:+.2f}")


def run_step(mount: Mount, home, step) -> dict:
    """Execute one step of SEQUENCE while polling readback; return the measurements."""
    kind, direction, amount = step
    before = read(mount)
    samples = []
    if kind == "pulse":
        mount.pulse_guide(direction, amount)
        end = time.monotonic() + amount / 1000
    else:
        mount.set_slew_rate(kind)
        mount.move(direction)
        end = time.monotonic() + amount
    try:
        while time.monotonic() < end:
            samples.append(read(mount))
            check(home, samples[-1])
    finally:
        if kind != "pulse":
            mount.halt()
    time.sleep(SETTLE_S)
    after = read(mount)
    check(home, after)
    return {
        "rate": kind if kind == "pulse" else kind.name, "dir": direction.name,
        "amount": amount, "before": before[1:], "after": after[1:],
        "d_alt": after[1] - before[1], "d_az": wrap180(after[2] - before[2]),
        "samples": [(round(t - before[0], 3), a, z) for t, a, z in samples],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", required=True)
    ap.add_argument("--axis", choices=["all", "az", "alt"], default="all",
                    help="run only the azimuth (E/W) or altitude (N/S) steps")
    args = ap.parse_args()
    alt_dirs = (Direction.NORTH, Direction.SOUTH)
    steps = [s for s in SEQUENCE
             if args.axis == "all" or (s[1] in alt_dirs) == (args.axis == "alt")]

    out = Path("data") / f"motion_probe_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    out.parent.mkdir(exist_ok=True)
    mount = Mount(Transport.open(args.port))
    try:
        mount.ensure_high_precision()
        home = read(mount)
        print(f"start alt={home[1]:.4f} az={home[2]:.4f}  (status {mount.alignment_status()})")
        print(f"{'rate':7s} {'dir':6s} {'amt':>5s} {'d_alt°':>9s} {'d_az°':>9s} {'°/s':>8s}")
        with out.open("w") as f:
            for step in steps:
                r = run_step(mount, home, step)
                f.write(json.dumps(r) + "\n")
                secs = r["amount"] / 1000 if r["rate"] == "pulse" else r["amount"]
                moved = max(abs(r["d_alt"]), abs(r["d_az"]))
                print(f"{r['rate']:7s} {r['dir']:6s} {r['amount']:5g} "
                      f"{r['d_alt']:+9.4f} {r['d_az']:+9.4f} {moved / secs:8.4f}")
        end = read(mount)
        print(f"end   alt={end[1]:.4f} az={end[2]:.4f}  "
              f"net d_alt={end[1] - home[1]:+.4f} d_az={wrap180(end[2] - home[2]):+.4f}")
        print(f"saved {out}")
    finally:
        mount.halt()
        mount.close()


if __name__ == "__main__":
    main()
