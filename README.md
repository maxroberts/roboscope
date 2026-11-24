# roboscope

Self-calibrating, camera-guided control for a Meade StarNavigator NG telescope (AudioStar
handbox). The goal is to go from "it's 19:32 on this date in this ZIP code, align
yourself, point at Saturn" to a centered, tracked target, using plate solving to correct
the mount's backlash, slip and alignment errors.

**Status:** Stage 1 in progress. The mount driver is written and tested against a
simulated handbox, but not yet run on the real telescope. See
[documentation/PLAN.md](documentation/PLAN.md) for the staged plan.

## Prerequisites
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you)
- For hardware work, both of these:
  - a USB-serial adapter
  - an RJ10↔DB9 cable to the AudioStar's RS-232 port (wiring:
    [documentation/IMG_3502.gif](documentation/IMG_3502.gif))

## Setup
```sh
uv sync --dev
uv run pytest
```

## Usage
Read-only hardware probe. It never moves the scope, but may switch the handbox to
high-precision display:
```sh
uv run python scripts/probe_mount.py --list                      # find the cable's port
uv run python scripts/probe_mount.py --port /dev/tty.usbserial-XXXX
uv run python scripts/probe_mount.py --port ... --repeat 500     # cable reliability test
```

From Python:
```python
from roboscope.mount import Mount
with Mount.connect("/dev/tty.usbserial-XXXX") as m:
    print(m.info(), m.radec(), m.altaz())
```

## Codebase architecture
- `src/roboscope/mount/`: talks to the AudioStar handbox (LX200 protocol, 9600 8N1).
  Layers, bottom to top:
  - `protocol.py`: pure string formatting/parsing. Converts Meade's west-positive
    longitude and inverted UTC offset to standard conventions.
  - `transport.py`: serial I/O with locking, timeouts, stale-input flushing, and raw
    traffic logging.
  - `driver.py`: the typed `Mount` API (position, goto, sync, manual motion, pulse
    guide, site/time).
  - `sim.py`: a protocol-level simulated handbox for tests.

  The handbox does gotos and tracking itself; roboscope corrects it via sync and pulse
  guiding. There are no safety checks at this layer yet. Commands marked *unverified*
  in `driver.py` still need confirming on hardware.
- Planned packages: `motion`, `sky`, `camera`, `solve`, `align`, `safety` (see the
  plan). The whole package will run on a Raspberry Pi attached to the telescope.
- `tests/`: pytest suite. Protocol conversions, plus the driver against the simulator.
- `scripts/probe_mount.py`: read-only hardware probe and cable comparison.
- `notebooks/`: numbered exploration notebooks for hardware bring-up and experiments.
- `documentation/`:
  - `PLAN.md`: the staged plan.
  - `telescope_interface.md`: every input and output of the telescope (ports, LX200
    commands, motor bus), with what's been verified.
  - `hardware_findings.md`: dated experimental results.
  - Meade protocol PDFs and the cable wiring diagram.

Key external interface: the Meade LX200/Autostar serial protocol (9600 8N1), documented
in `documentation/TelescopeProtocol_2010-10.pdf`. Earlier proof-of-concept code lives
outside this repo in `../Telescope Control/`.
