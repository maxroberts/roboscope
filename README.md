# roboscope

Self-calibrating, camera-guided control for a Meade StarNavigator NG telescope (AudioStar
handbox). The goal is to go from "it's 19:32 on this date in this ZIP code, align
yourself, point at Saturn" to a centered, tracked target, using plate solving to correct
the mount's backlash, slip and alignment errors.

**Status:** Stage 1 in progress. Both axes are driven by our own Arduino-based motor
controller (no handbox): regulated speed, encoder positions and firmware safety limits
are verified on hardware. Absolute pointing (camera, accelerometer) is not built yet.
See [documentation/PLAN.md](documentation/PLAN.md) and
[documentation/hardware_findings.md](documentation/hardware_findings.md).

## Prerequisites
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you)
- For hardware work, both of these:
  - a USB-serial adapter
  - an RJ10↔DB9 cable to the AudioStar's RS-232 port (wiring:
    [documentation/IMG_3502.gif](documentation/IMG_3502.gif))

## Setup
```sh
uv sync --dev
uv run python scripts/fetch_sky_data.py   # ephemeris + star catalog into data/sky (~50 MB, once)
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

  - `controller.py`: **the main path now.** `MotorController` drives our own motor
    controller (an Arduino Uno running `firmware/mount_controller`) over USB serial:
    - per-axis speed in °/s, encoder positions, soft limits, LED calibration;
    - a keepalive for the firmware watchdog;
    - encoder-feedback `move_by`;
    - `SimulatedController` for tests.
  - The handbox path (`driver.py`) is kept for reference and diagnostics. With it, the
    handbox does its own gotos and tracking, and roboscope only corrects it. Commands
    marked *unverified* in `driver.py` still need confirming on hardware.
- `src/roboscope/sky/`: where things are, from a site and time. Camera-agnostic.
  - `Site`: the observing site, from lat/lon or a ZIP code.
  - `Sky`: Skyfield + JPL DE421. Apparent alt/az with refraction for Sun, Moon, planets
    and J2000 directions; alt/az → J2000 (exact inverse); Sun-separation safety check.
  - `StarCatalog`: HYG v4.1 bright stars (V ≤ 6.5, ~8,900 stars, 358 named). Name
    lookup, visibility, stars near a pointing, and alignment-star selection. Picks
    stars that are bright, spread out and *isolated*, so a narrow in-tube camera seeing
    one star can identify it.
  - Data (not committed): `uv run python scripts/fetch_sky_data.py` downloads it into
    `data/sky/`. `scripts/sky_now.py` prints what's up and suggests alignment stars.
- `src/roboscope/align/`: relates mount encoder angles to true sky directions, for
  either camera.
  - `PointingModel`: standard alt-az terms (index offsets, base tilt, collimation,
    axis non-perpendicularity, scale, flex), fitted by weighted least squares, mapping
    both ways. Hardware terms can be saved and kept between sessions.
  - `CameraFrame`: pixel ↔ sky offsets for a tube-fixed camera.
  - `spiral_offsets`: search pattern.
  - `AlignmentSession`: visits isolated alignment stars nearest-first, spiral-searches,
    records observations and refits.
  - Tested end-to-end in simulation (`tests/test_align.py`); not yet on hardware.
    Simulated accuracy is an upper bound, since real errors (backlash, unmodelled
    effects) will dominate.
- `src/roboscope/calibration.py` + **`config/hardware_calibration.json`** (committed):
  the measured properties of the instrument. Each value carries an uncertainty, date
  and method; unmeasured values are explicit nulls with a note on how to measure them.
  - Mount: counts/°, backlash, speed cap, travel limit.
  - Pointing-model hardware terms.
  - Camera frames.
  - IMU (placeholder).
  - `absorb_scale` folds a sky-fitted scale error into counts/°. Sessions load
    hardware terms from here and keep them fixed, so a nightly alignment only solves
    the setup (zero offsets, tilt).
- Planned packages: `motion` (backlash-aware moves, hardware adapters), `camera`,
  `solve`, `safety` (see the plan). The whole package will run on a Raspberry Pi attached to the telescope.
- `tests/`: pytest suite. Protocol conversions, plus the driver against the simulator.
- `scripts/probe_mount.py`: read-only hardware probe and cable comparison.
- `firmware/mount_controller/`: the Uno firmware that replaces the handbox. It is master
  of the NG motor bus, keeps encoder positions, and enforces safety itself (1 s
  watchdog, predictive soft limits, speed cap, calibration gate, stop and refuse on a
  silent motor board, Stop on any uncommanded motion, rejection of implausible status
  replies). It also auto-resyncs the motor boards if they lose bus sync, which happens
  occasionally after a firmware upload. Hosts should call
  `MotorController.wait_healthy()` before moving: usually instant, ~5 s after a
  recovery. Its command protocol
  is in the sketch header. Bench tests: `scripts/mc_bench_test.py`.
- `firmware/hbx_probe/`: diagnostic Uno sketch that drives the NG's motor boards directly
  over the HBX motor bus, with no handbox: handshake, motor type, status, and a
  time-capped `pulse` slew, encoder LED read/calibration, and regulated-speed `run`.
  `scripts/hbx_bridge.py`
  holds the Uno's port open so commands never reset it; `scripts/hbx_probe.py` is a
  standalone query script (mount off only).
  Wiring: `documentation/wiring_hbx_uno.md`.
- `notebooks/`: numbered exploration notebooks for hardware bring-up and experiments.
- `documentation/`:
  - `PLAN.md`: the staged plan.
  - `telescope_interface.md`: every input and output of the telescope (ports, LX200
    commands, motor bus), with what's been verified.
  - `hardware_findings.md`: dated experimental results.
  - `next_session_board_wedge.md`: the motor-board bus-sync ("wedge") investigation.
    Resolved: the boot-time resync was the cause.
  - `wiring_hbx_uno.md`: Uno ↔ HBX motor-bus wiring.
  - `hardware_plan_motor_controller.md`, `hardware_plan_alignment_camera.md`: hardware
    plans and shopping lists.
  - Meade protocol PDFs and the cable wiring diagram.

Key external interface: the Meade LX200/Autostar serial protocol (9600 8N1), documented
in `documentation/TelescopeProtocol_2010-10.pdf`. Earlier proof-of-concept code lives
outside this repo in `../Telescope Control/`.
