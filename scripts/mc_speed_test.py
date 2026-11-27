"""Speed test of the mount on hardware: drives a ladder of speeds and measures how well each holds.

Summary:
    For each axis and each speed (counts/s) in a ladder up to the firmware cap, drives
    the axis out and back and reports the speed held against the command, the run-on
    after STOP and how long the axis takes to stand still, plus any controller events
    and new under-voltage. It is the repeatable form of the 2026-11-27 speed tests
    (documentation/hardware_findings.md) and the tool for the high-speed watch list in
    documentation/mount_speed_limits.md.

Details:
    - Each leg: SPEED for `hold` seconds, sampling POS at ~10 Hz; then STOP, sampling
      until the position is unchanged for 0.5 s (4 s at most). The hold is the shorter
      of --max-hold and the time to cover --max-leg degrees, so fast legs are short.
      - Speed held = encoder rate between the first sample after --skip seconds (the
        motor board's ramp-up) and the last sample, ÷ the command. It needs at least
        3 samples in that window; otherwise the leg reports no rate.
      - Run-on = travel from the last POS before STOP until still, in the direction of
        motion. It includes up to one POS round trip of travel before STOP lands
        (~50 ms; ~600 counts at 12,000 counts/s), so it overstates true coast.
      - "Still after" = STOP to the first sample at the final position (the 0.5 s of
        unchanged samples that confirm it are not counted).
      - Default hold 4 s: at 200 counts/s, 2.2 s holds scattered 96.5–100.5% (the first
        leg after the port opens read low twice); 4 s holds were within 1.3%
        (2026-11-27). Fast legs are bounded by --max-leg instead.
    - Each speed runs + then −, so the axis ends near its start. Speeds are tested in
      ladder order. If a leg logs controller events, that axis stops climbing.
    - Safety:
      - Both axes' firmware soft limits are set to ±--limit around the position when
        the port opens (it resets the Uno and zeroes positions). The user's rule since
        2026-11-27 (tube mounted) is that test motions stay within 10°, so --limit and
        --max-leg are refused above 10, and --max-leg must be below --limit.
      - Every SPEED stops itself: the firmware watchdog stops both axes 1 s after the
        last command, and this script commands every ~0.1 s while a leg runs.
      - Ctrl-C or any error closes the controller, which stops both axes.
      - It refuses to start while the camera hub on this machine has the Uno open
        (`/api/mount` says connected); stop it first with
        `systemctl --user stop roboscope-camera`, and restart it afterwards.
    - Counts per degree and the cap come from config/hardware_calibration.json
      (`roboscope.calibration`). The cap must match the firmware's MAX_CPS, or the
      firmware refuses the top step ("ERR speed above MAX_CPS").
    - --repeat N runs the whole ladder N times, to look for drift in sustained use
      (battery sag, heating).
    - Output: stdout and data/mc_speed_<time>.log. The last lines are a summary table;
      a leg FAILs if speed held is off by more than --tolerance or it logged events.
      Exit status 1 if any leg failed.
    - Runs where the Uno is attached (normally the 3B+, port /dev/ttyACM0). Reads
      `vcgencmd get_throttled` before and after when available.

Usage (on the 3B+, from ~/roboscope):
    systemctl --user stop roboscope-camera
    .venv/bin/python scripts/mc_speed_test.py [--axes az alt] [--speeds 1300 6500 12000]
        [--repeat 1] [--cal]
    systemctl --user start roboscope-camera
"""

import argparse
import json
import logging
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from roboscope.calibration import HardwareCalibration
from roboscope.mount.controller import MotorController

USER_MAX_DEG = 10.0              # user rule (2026-11-27): scripted test motions within 10°
HUB_URL = "http://127.0.0.1:47623/api/mount"
DEFAULT_LADDER = (200.0, 1300.0, 3000.0, 6500.0, 9000.0)   # the cap is appended


@dataclass
class Leg:
    """One leg's measurements; `rate` is None when the steady window was too short."""

    axis: str
    cps: float                   # signed command, counts/s
    hold_s: float
    rate: float | None           # measured counts/s over the steady window
    samples: int                 # POS samples in the steady window
    run_on: int                  # counts past the last POS before STOP, + = onward
    settle_s: float              # STOP to standing still
    events: list[str]

    def held(self) -> float | None:
        return None if self.rate is None else self.rate / self.cps

    def ok(self, tolerance: float) -> bool:
        h = self.held()
        return h is not None and abs(h - 1) <= tolerance and not self.events


def hold_time(cps: float, cpd: float, max_leg_deg: float, max_hold_s: float) -> float:
    """Seconds to run a leg: at most `max_hold_s`, and at most `max_leg_deg` of travel."""
    return min(max_hold_s, max_leg_deg / (abs(cps) / cpd))


def run_leg(mc: MotorController, axis: str, cps: float, hold_s: float,
            skip_s: float = 0.5, poll_s: float = 0.08) -> Leg:
    """Drive `axis` at `cps` (signed) for `hold_s`, STOP, and measure the leg (see module notes)."""
    n_events = len(mc.events)
    samples = []
    mc.set_speed_cps(axis, cps)
    try:
        t_end = time.monotonic() + hold_s
        while time.monotonic() < t_end:
            samples.append((time.monotonic(), mc.position().counts[axis]))
            time.sleep(poll_s)
        last = mc.position().counts[axis]
    finally:
        t_stop = time.monotonic()
        mc.stop(axis)
    track = [(t_stop, last)]
    still_since = None
    while time.monotonic() - t_stop < 4.0:
        time.sleep(0.05)
        now, x = time.monotonic(), mc.position().counts[axis]
        if x == track[-1][1]:
            still_since = still_since or track[-1][0]
            if now - still_since >= 0.5:
                break
        else:
            still_since = None
        track.append((now, x))
    steady = [s for s in samples if s[0] - samples[0][0] >= skip_s] if samples else []
    rate = None
    if len(steady) >= 3:
        rate = (steady[-1][1] - steady[0][1]) / (steady[-1][0] - steady[0][0])
    direction = 1 if cps > 0 else -1
    return Leg(axis, cps, hold_s, rate, len(steady), (track[-1][1] - last) * direction,
               (still_since or track[-1][0]) - t_stop, mc.events[n_events:])


def throttled() -> str:
    """`vcgencmd get_throttled` output, or '' where there is no vcgencmd (not a Pi)."""
    try:
        return subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def hub_holds_port() -> bool:
    """True if the camera hub on this machine reports a connected mount (it has the Uno open)."""
    try:
        with urllib.request.urlopen(HUB_URL, timeout=2) as r:
            return bool(json.load(r).get("connected"))
    except (OSError, ValueError):
        return False


def format_leg(leg: Leg, cpd: float) -> str:
    held = "  n/a " if leg.held() is None else f"{leg.held():6.1%}"
    rate = "    n/a" if leg.rate is None else f"{leg.rate:+7.0f}"
    s = (f"{leg.axis:3s} {leg.cps:+7.0f} cps ({leg.cps / cpd:+6.3f}°/s) hold {leg.hold_s:4.2f} s: "
         f"measured {rate} = {held} ({leg.samples} samples); run-on {leg.run_on:+5d} counts "
         f"({leg.run_on / cpd:+.3f}°), still after {leg.settle_s:.2f} s")
    return s + (f"; EVENTS {leg.events}" if leg.events else "")


def run_ladder(mc: MotorController, axes, speeds, cpd: dict, max_leg_deg: float,
               max_hold_s: float, skip_s: float, say) -> list[Leg]:
    """Run every speed, + then −, on each axis; stop climbing on an axis after events."""
    legs = []
    for axis in axes:
        for cps in speeds:
            hold = hold_time(cps, cpd[axis], max_leg_deg, max_hold_s)
            pair = [run_leg(mc, axis, d * cps, hold, skip_s) for d in (1, -1)]
            for leg in pair:
                say(format_leg(leg, cpd[axis]))
            legs += pair
            if any(leg.events for leg in pair):
                say(f"{axis}: controller events at {cps:.0f} counts/s; not going higher")
                break
    return legs


def main(argv=None) -> int:
    cal = HardwareCalibration.load()
    cap = cal.get("mount", "max_counts_per_s").value
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--axes", nargs="+", choices=("az", "alt"), default=["az", "alt"])
    ap.add_argument("--speeds", nargs="+", type=float, metavar="CPS",
                    default=[*DEFAULT_LADDER, cap],
                    help=f"counts/s, ascending (default {[*DEFAULT_LADDER, cap]})")
    ap.add_argument("--max-leg", type=float, default=5.0, help="degrees per leg (default 5)")
    ap.add_argument("--max-hold", type=float, default=4.0, help="seconds per leg at most")
    ap.add_argument("--skip", type=float, default=0.5, help="ramp-up seconds left out of the rate")
    ap.add_argument("--limit", type=float, default=9.5, help="soft limits ±deg from start")
    ap.add_argument("--tolerance", type=float, default=0.03, help="pass if within this fraction")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--cal", action="store_true", help="run encoder calibration (CAL) first")
    a = ap.parse_args(argv)
    if a.limit > USER_MAX_DEG or a.max_leg >= a.limit:
        ap.error(f"need --max-leg < --limit <= {USER_MAX_DEG}° (test-motion rule)")
    if max(a.speeds) > cap or min(a.speeds) <= 0:
        ap.error(f"speeds must be in (0, {cap:.0f}] counts/s (the firmware cap)")
    if hub_holds_port():
        ap.error("the camera hub has the Uno open: systemctl --user stop roboscope-camera")

    log_path = Path("data") / f"mc_speed_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_path.parent.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(log_path)])
    say = logging.getLogger("speed").info
    cpd = cal.counts_per_degree()
    say(f"speed test: axes {a.axes}, speeds {a.speeds}, repeat {a.repeat}, cpd {cpd}, "
        f"limits ±{a.limit}°, legs ≤ {a.max_leg}°; {throttled()}")

    mc = MotorController.open(a.port, counts_per_degree=cpd)
    legs = []
    try:
        p = mc.wait_healthy()
        say(f"boot: {mc.boot_line}")
        if a.cal or not p.calibrated:
            say(f"CAL: {mc.calibrate()}")
        for axis in ("az", "alt"):
            mc.set_limits_deg(axis, -a.limit, a.limit)
        say(f"limits: {mc.limits_deg()}")
        for i in range(a.repeat):
            if a.repeat > 1:
                say(f"-- pass {i + 1}/{a.repeat}")
            legs += run_ladder(mc, a.axes, a.speeds, cpd, a.max_leg, a.max_hold, a.skip, say)
        p = mc.position()
        say(f"end: az {p.degrees['az']:+.3f}°, alt {p.degrees['alt']:+.3f}° from start; "
            f"{throttled()}")
    finally:
        mc.close()

    failed = [g for g in legs if not g.ok(a.tolerance)]
    say(f"summary: {len(legs) - len(failed)}/{len(legs)} legs within ±{a.tolerance:.0%} "
        f"and without events")
    for g in failed:
        say("FAIL " + format_leg(g, cpd[g.axis]))
    say(f"log: {log_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
