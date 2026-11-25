"""Host side of the read-only HBX motor-bus probe (firmware/hbx_probe on an Arduino Uno).

Summary:
    Sends text commands to the Arduino sketch and prints its one-line replies. The goal is
    to find out whether the NG's motor boards speak the ETX/DS motor protocol
    (documentation/telescope_interface.md §5). This script only sends type/status
    queries. The sketch's `pulse` command (v1, guarded motion) is not used here.

Details:
    Default run, in order:
      - `hello` and `lines`: the data line should read 1 (pulled up).
      - `type az`, `type alt`: GetMotorType. A handshake plus a repeatable byte means the
        pin map and signaling match.
      - `status az`, `status alt` three times each: GetStatus. Position deltas should be 0
        while nothing moves.
    `--watch AXIS` polls `status AXIS` about 5×/s and prints a running sum of the deltas.
    Note: turning an axis by hand can't move the encoder. The encoder is on the motor;
    with the clutch engaged the worm can't be back-driven, and with it released the
    motor doesn't turn. Counts only change when the motor runs.

    Every reply is also appended, timestamped, to data/hbx_probe_<time>.log.

    Opening the port resets the Uno (its bootloader runs ~1.5 s, even with DTR/RTS held
    low on macOS), so the script waits for the sketch's banner before sending anything.
    While the Uno resets its pins float, so **only run this script with the mount
    powered off**. With the mount on, use a process that already holds the port open.

Usage:
    uv run python scripts/hbx_probe.py --port /dev/cu.usbmodem113301
    uv run python scripts/hbx_probe.py --port ... --watch az
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

import serial


class Probe:
    """Line-oriented connection to the hbx_probe sketch, logging every exchange."""

    def __init__(self, port: str, log_path: Path):
        self.s = serial.Serial(port, 115200, timeout=3)
        self.log = log_path.open("a")
        banner = self.s.readline().decode(errors="replace").strip()  # sketch boots
        self._record("boot", banner)

    def ask(self, command: str) -> str:
        """Send one command and return its reply line ('' on timeout)."""
        self.s.reset_input_buffer()
        self.s.write((command + "\n").encode())
        reply = self.s.readline().decode(errors="replace").strip()
        self._record(command, reply)
        return reply

    def _record(self, command: str, reply: str) -> None:
        line = f"{datetime.now():%H:%M:%S.%f} {command!r} -> {reply}"
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()


def watch(probe: Probe, axis: str) -> None:
    """Poll status for one axis and print the running total of position deltas."""
    total = 0
    print("Polling status; counts change only while the motor runs. Ctrl-C to stop.")
    try:
        while True:
            reply = probe.ask(f"status {axis}")
            fields = dict(f.split("=") for f in reply.split() if "=" in f)
            if "delta" in fields:
                total += int(fields["delta"])
                print(f"    running total = {total}")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print(f"final total = {total}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", required=True)
    ap.add_argument("--watch", choices=["az", "alt"])
    args = ap.parse_args()

    log_path = Path("data") / f"hbx_probe_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_path.parent.mkdir(exist_ok=True)
    probe = Probe(args.port, log_path)
    if args.watch:
        watch(probe, args.watch)
        return
    for cmd in ["hello", "lines", "type az", "type alt",
                *["status az", "status alt"] * 3]:
        probe.ask(cmd)
    print(f"log: {log_path}")


if __name__ == "__main__":
    main()
