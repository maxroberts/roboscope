"""Large out-and-back moves on each axis to measure encoder counts per degree.

Summary:
    Drives one or both axes a fixed number of encoder counts out and then back, using
    the hbx_probe sketch's capped `run` command. A person reads the true angle at each
    pause (phone inclinometer for altitude, setting circle or tape marks for azimuth).
    Counts per degree = counts moved / degrees read.

Details:
    - Talks to the sketch through scripts/hbx_bridge.py: it writes commands to the
      bridge's FIFO and reads the replies from the bridge's log. The Uno is never reset.
    - Each leg is a chain of `run` commands, each at most 10 s (the sketch's cap) at
      --speed (default 530640 ≈ 60× sidereal, ~1235 counts/s measured). The last run of
      a leg is shortened so the leg's total lands near the target. The return leg
      targets the exact count the outward leg reached, so the encoder ends near zero.
    - Pauses: --pause seconds (default 10) before the first move, between out and back,
      and between axes. Each pause prints "READ NOW" with the leg just finished and the
      encoder total.
    - The target is given in degrees and converted per axis with --alt-cpd / --az-cpd.
      The defaults are our measured NG values (2026-11-24, ±2–3%): 4230 counts/° (alt),
      3085 counts/° (az). An earlier run used EQMOD-ETX's DS figure (4928) for both,
      which sent azimuth 48° instead of 30°, past the ±45° cable limit.
      documentation/hardware_findings.md has the details.
    - Safety: every `run` stops itself inside the sketch. If any reply isn't "OK run",
      the script stops issuing commands and exits. Ctrl-C also stops after the current
      run.
    - Results print as a table and are appended to the bridge log.

Usage:
    uv run python scripts/hbx_large_move.py --fifo <cmd.fifo> --log <replies.log> \
        --axes alt az --degrees 30 --alt-sign 1 --az-sign 1
"""

import argparse
import re
import time
from pathlib import Path

RUN_MAX_MS = 10000


class Bridge:
    """Send commands via the bridge FIFO; wait for the matching reply in its log."""

    def __init__(self, fifo: str, log: str):
        self.fifo, self.log = fifo, Path(log)

    def ask(self, command: str, timeout_s: float = 20) -> str:
        start = len(self.log.read_text().splitlines())
        with open(self.fifo, "w") as f:
            f.write(command + "\n")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for line in self.log.read_text().splitlines()[start:]:
                if f"'{command}' ->" in line:
                    return line.split("->", 1)[1].strip()
            time.sleep(0.2)
        raise TimeoutError(f"no reply to {command!r}")


def run_leg(bridge: Bridge, axis: str, target_counts: int, speed: int, cps: float) -> int:
    """Move `axis` until the summed encoder total reaches `target_counts` (signed).

    Returns the counts actually moved. Raises RuntimeError on any non-OK reply.
    """
    sign = 1 if target_counts > 0 else -1
    moved = 0
    while abs(target_counts - moved) > cps * 0.3:      # within ~0.3 s of travel: done
        remaining_s = abs(target_counts - moved) / cps
        ms = int(min(RUN_MAX_MS, max(300, remaining_s * 1000)))
        reply = bridge.ask(f"run {axis} {sign * speed} {ms}")
        if not reply.startswith("OK run"):
            raise RuntimeError(f"stopping: {reply}")
        total = int(re.search(r"total=(-?\d+)", reply).group(1))
        moved += total
        print(f"  run {axis} {sign * speed:+d} {ms} ms -> {total:+d} (leg {moved:+d})",
              flush=True)
    return moved


def pause(seconds: float, note: str) -> None:
    print(f"\n*** {time.strftime('%H:%M:%S')} READ NOW: {note} — next move in "
          f"{seconds:.0f} s ***\n", flush=True)
    time.sleep(seconds)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fifo", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--axes", nargs="+", choices=["alt", "az"], default=["alt", "az"])
    ap.add_argument("--degrees", type=float, default=30.0)
    ap.add_argument("--alt-cpd", type=float, default=4230.0, help="alt counts per degree")
    ap.add_argument("--az-cpd", type=float, default=3085.0, help="az counts per degree")
    ap.add_argument("--speed", type=int, default=530640)
    ap.add_argument("--cps", type=float, default=1235.0, help="measured counts/s at --speed")
    ap.add_argument("--pause", type=float, default=10.0)
    ap.add_argument("--alt-sign", type=int, choices=[1, -1], default=1,
                    help="direction of the outward altitude leg (+1 = 'fwd')")
    ap.add_argument("--az-sign", type=int, choices=[1, -1], default=1,
                    help="direction of the outward azimuth leg (+1 = 'fwd' = CW from above)")
    args = ap.parse_args()

    bridge = Bridge(args.fifo, args.log)
    results = []
    pause(args.pause, "starting positions")
    try:
        for i, axis in enumerate(args.axes):
            sign = args.alt_sign if axis == "alt" else args.az_sign
            cpd = args.alt_cpd if axis == "alt" else args.az_cpd
            target = int(args.degrees * cpd)
            print(f"=== {axis}: out {sign * target:+d} counts "
                  f"(~{args.degrees}° at {cpd:.0f} counts/°)")
            out = run_leg(bridge, axis, sign * target, args.speed, args.cps)
            pause(args.pause, f"{axis} after OUT leg ({out:+d} counts)")
            print(f"=== {axis}: back {-out:+d} counts")
            back = run_leg(bridge, axis, -out, args.speed, args.cps)
            results.append((axis, out, back))
            note = f"{axis} after BACK leg ({back:+d} counts; net {out + back:+d})"
            if i < len(args.axes) - 1:
                pause(args.pause, note)
            else:
                print(f"\n*** {time.strftime('%H:%M:%S')} READ NOW: {note} — done ***")
    except KeyboardInterrupt:
        print("interrupted after the current run (the sketch stops each run itself)")
    print("\naxis   out_counts  back_counts  net")
    for axis, out, back in results:
        print(f"{axis:5s} {out:+11d} {back:+12d} {out + back:+5d}")


if __name__ == "__main__":
    main()
