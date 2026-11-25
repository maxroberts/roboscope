"""Hold the Arduino's serial port open and relay commands to the hbx_probe sketch.

Summary:
    Opening the Uno's serial port resets it (also with DTR/RTS low on macOS), which
    briefly floats the motor bus. This bridge opens the port once and keeps it open, so
    tests with the mount powered never reset the Uno. Other processes send commands
    through a named pipe.

Details:
    - Commands: one per line, written to the FIFO (e.g. `echo "status az" > cmd.fifo`).
      `quit` stops the bridge.
    - Each reply (one line from the sketch) is appended to the log file with a timestamp
      and the command that produced it.
    - The reply timeout is 15 s, longer than the slowest sketch command (`run`, up to
      ~10.6 s).
    - If the Uno is unplugged, the bridge exits with a serial error; restarting it
      resets the Uno once.
    - With the 47 kΩ pull-ups installed (documentation/wiring_hbx_uno.md), that reset is
      verified safe with the mount on.

Usage:
    mkfifo /tmp/hbx.fifo
    uv run python scripts/hbx_bridge.py --port /dev/cu.usbmodem113301 \
        --fifo /tmp/hbx.fifo --log data/hbx_bridge.log
"""

import argparse
from datetime import datetime

import serial


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", required=True)
    ap.add_argument("--fifo", required=True, help="named pipe to read commands from")
    ap.add_argument("--log", required=True, help="file to append replies to")
    args = ap.parse_args()

    s = serial.Serial(args.port, 115200, timeout=15)
    out = open(args.log, "a", buffering=1)

    def record(cmd: str, reply: str) -> None:
        out.write(f"{datetime.now():%H:%M:%S.%f} {cmd!r} -> {reply}\n")

    record("boot", s.readline().decode(errors="replace").strip())
    record("ready", "bridge holding port open")
    while True:
        with open(args.fifo) as f:  # blocks until a writer opens the FIFO
            for line in f:
                cmd = line.strip()
                if not cmd:
                    continue
                if cmd == "quit":
                    return
                s.reset_input_buffer()
                s.write((cmd + "\n").encode())
                record(cmd, s.readline().decode(errors="replace").strip())


if __name__ == "__main__":
    main()
