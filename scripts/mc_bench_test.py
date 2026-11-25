"""Bench test of the mount_controller firmware and the MotorController driver on hardware.

Summary:
    Exercises each safety feature and motion primitive once and prints a result per
    test:
      - boot and default limits;
      - watchdog;
      - speed change while moving (SpeedChnge), including a direction reversal;
      - sidereal-rate tracking;
      - predictive soft limits;
      - encoder-feedback moves (`move_by`) and their repeatability.
    Every test returns its axis to the starting position.

Details:
    - Azimuth stays within ±3° of where it is at boot. The firmware's boot limit is
      ±10°, the user's rule for this bench session. Altitude moves up to +5° from boot.
    - Positions are encoder counts converted with measured counts/° (±2–3%); they are
      motor-side, so backlash and slip don't show (hardware_findings.md).
    - Opening the port resets the Uno once (safe with the pull-ups; wiring_hbx_uno.md).
    - Output: stdout, and data/mc_bench_<time>.log.

Usage:
    uv run python scripts/mc_bench_test.py --port /dev/cu.usbmodem113301
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

from roboscope.mount.controller import ControllerError, MotorController


def rate_over(mc: MotorController, axis: str, seconds: float) -> float:
    """Measured counts/s on `axis` over `seconds` (keepalive must be on)."""
    c0, t0 = mc.position().counts[axis], time.monotonic()
    time.sleep(seconds)
    c1, t1 = mc.position().counts[axis], time.monotonic()
    return (c1 - c0) / (t1 - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", required=True)
    args = ap.parse_args()

    log_path = Path("data") / f"mc_bench_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_path.parent.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(log_path)])
    say = logging.getLogger("bench").info

    mc = MotorController.open(args.port)
    try:
        say(f"boot: {mc.boot_line}")
        say(f"hello: {mc.hello()}  limits: {mc.limits_deg()}")
        if "calibrated=0" in mc.boot_line:
            say(f"calibrating: {mc.calibrate()}")
        say(f"start: {mc.position()}")

        # 1. Watchdog: a speed with no keepalive should stop within ~1 s.
        say("TEST watchdog: az 0.1°/s, host silent")
        n = len(mc.events)
        mc.set_speed("az", 0.1)
        time.sleep(2.0)
        p = mc.position()
        say(f"  events: {mc.events[n:]}  commanded az: {p.commanded_cps['az']}  "
            f"az moved {p.degrees['az']:+.3f}°")
        mc.move_by("az", -p.degrees["az"], 0.1)

        # 2. SpeedChnge while moving, then a reversal.
        say("TEST speed changes on alt: +0.1 -> +0.2 -> -0.1 °/s")
        mc.keepalive(True)
        for dps in (0.1, 0.2, -0.1):
            mc.set_speed("alt", dps)
            time.sleep(1.0)                       # settle
            cps = rate_over(mc, "alt", 2.0)
            say(f"  set {dps:+.2f}°/s = {dps * mc.cpd['alt']:+.1f} cps -> measured "
                f"{cps:+.1f} cps")
        mc.stop("alt")
        mc.keepalive(False)
        time.sleep(0.5)
        mc.move_by("alt", -mc.position().degrees["alt"], 0.2)

        # 3. Sidereal rate (15.041"/s) on azimuth for 20 s.
        sid = 15.041 / 3600
        say(f"TEST sidereal on az: {sid:.5f}°/s = {sid * mc.cpd['az']:.2f} cps")
        mc.keepalive(True)
        mc.set_speed("az", sid)
        time.sleep(1.0)
        cps = rate_over(mc, "az", 20.0)
        say(f"  measured {cps:.2f} cps (expected {sid * mc.cpd['az']:.2f})")
        mc.stop("az")
        mc.keepalive(False)
        mc.move_by("az", -mc.position().degrees["az"], 0.1)

        # 4. Predictive soft limit: az limit at +0.5°, drive toward it at 0.3°/s.
        say("TEST soft limit: az limits -0.5..+0.5°, drive +0.3°/s")
        mc.set_limits_deg("az", -0.5, 0.5)
        n = len(mc.events)
        mc.keepalive(True)
        mc.set_speed("az", 0.3)
        t_end = time.monotonic() + 6
        while len(mc.events) == n and time.monotonic() < t_end:
            time.sleep(0.1)
        time.sleep(0.5)
        say(f"  events: {mc.events[n:]}  stopped at {mc.position().degrees['az']:+.3f}°")
        try:
            mc.set_speed("az", 0.3)
            say("  !! speed toward the limit was accepted")
        except ControllerError as e:
            say(f"  refused toward limit: {e}")
        mc.keepalive(False)
        mc.stop("az")
        mc.set_limits_deg("az", -10, 10)
        mc.move_by("az", -mc.position().degrees["az"], 0.1)

        # 5. move_by accuracy and repeatability (encoder frame).
        say("TEST move_by: alt ±5° (0.3°/s) and az ±3° (0.3°/s), twice each")
        for axis, deg in (("alt", 5.0), ("az", 3.0)):
            for rep in range(2):
                e1 = mc.move_by(axis, deg, 0.3)
                e2 = mc.move_by(axis, -deg, 0.3)
                say(f"  {axis} rep {rep}: out err {e1:+.4f}°, back err {e2:+.4f}°, "
                    f"now {mc.position().degrees[axis]:+.4f}°")

        say(f"end: {mc.position()}")
        say(f"all events: {mc.events}")
    finally:
        mc.close()
        say(f"log: {log_path}")


if __name__ == "__main__":
    main()
