# roboscope

Self-calibrating, camera-guided control for a Meade StarNavigator NG telescope (AudioStar
handbox). The goal is to go from "it's 19:32 on this date in this ZIP code, align
yourself, point at Saturn" to a centered, tracked target, using plate solving to correct
the mount's backlash, slip and alignment errors.

**Status:** Stage 1 in progress. Both axes are driven by our own Arduino-based motor
controller (no handbox): regulated speed, encoder positions and firmware safety limits
are verified on hardware. The sky model and star alignment work in simulation. The
camera (raw frames, star centroids, browser live view) runs on hardware: the Zero
captures and the host Pi (3B+) serves it. It hasn't seen a star yet because no optics
are attached. Next: the mount adapter that
connects alignment to the real motors.
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

Camera live view and settings in the browser, through an SSH tunnel to the camera hub
on the host Pi (3B+), which gets frames from the camera Zero over USB. Its Mount panel
steers the telescope: hold ▲▼◀▶ (or the arrow keys) to move, release to stop:
```sh
uv run python scripts/camera_view.py            # http://127.0.0.1:47623/
uv run python scripts/camera_view.py --synthetic  # simulated camera and mount, no Pi needed
scripts/camera_view_via_host.sh                  # same tunnel with plain ssh, shared on the LAN
scripts/deploy_camera_pi.sh <Zero IP>            # push Zero config + camera server
scripts/deploy_host_pi.sh --code-only <3B+ IP>   # push code to the 3B+, restart the hub
```

From Python:
```python
from roboscope.camera import connect
with connect() as cam:                     # the hub: direct on the 3B+, else SSH tunnel
    cam.set(exposure_ms=50, gain=4)
    print(cam.find_star())                 # centroid in full-sensor px, SNR, ...
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
  - `jog.py`: `MountJog`, hold-to-move steering for the camera page (the hub's
    `--mount /dev/ttyACM0`). The page sends jog heartbeats while a button is held; an
    axis without one for 0.6 s is stopped. Speed in °/s (default 1, capped at the
    firmware's 6500 counts/s: Az 2.11, Alt 1.54 °/s; raised from 1300 on 2026-11-26).
    The presets are the AudioStar handbox's speeds 1–8 (NG manual). For this supervised tool the
    user lifted both soft limits and chose no Sun check (2026-11-26).
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
- `roboscope.camera`: the narrow camera.
  - One server (`server.py`, HTTP API: `/api/locate`, `/api/settings`,
    `/api/frame.npy`, `/api/raw`, …, plus an MJPEG browser UI) runs in two roles:
    - **On the camera Zero** (`--source pi`): owns the camera (picamera2, raw 10-bit
      Bayer, manual exposure/gain, 2×2-summed linear frames) and offers each frame on
      `/api/raw`. Its systemd unit filters traffic, so only the Zero itself and the
      3B+ over USB can connect.
    - **On the host Pi** (`--source remote`, the *hub*): fetches frames from the Zero
      over USB (`RemoteSource`), at the preview's size and rate (`preview_size`, with
      measured per-size fps limits), renders the preview, finds the star
      (`starfind.py`, numpy only) and serves the UI. Loopback only. With `--mount` it
      also serves `/api/mount*` for the page's Mount panel (`mount/jog.py`), and holds
      the Uno's serial port while it runs.
  - Clients use the hub: `CameraClient` (`client.py`), whose `locate()` is
    `AlignmentSession`'s star finder (`connect()`: direct on the 3B+, SSH tunnel to
    `roboscope.local` elsewhere), and `scripts/camera_view.py` /
    `camera_view_via_host.sh` for the browser. The latter shares its tunnel on the LAN
    by default (no login; `--private` turns that off). No viewer depends on the Zero's
    slow Wi-Fi.
  - The same files deploy to the Pi as package `roboscope_camera`, so they use relative
    imports and need only numpy and the stdlib.
  - `SyntheticSource` simulates the camera for tests and for running the UI without
    the Pis. `tests/test_camera_remote.py` runs the Zero → hub chain in one process.
- Planned packages: `motion` (backlash-aware moves, hardware adapters), `solve`,
  `safety` (see the plan). The whole package will run on a Raspberry Pi attached to the telescope.
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
- `hardware/camera_pi/`: the focuser camera's Pi Zero W (`roboscopeCam.local`, Pi OS
  Lite Trixie, OV5647). This folder is the source of truth for its setup: `rootfs/`
  config files plus `provision.sh`, pushed and applied by `scripts/deploy_camera_pi.sh`
  (key-only SSH, automatic updates, Bluetooth off, a USB gadget link to the host (opened as
  soon as the gadget loads, which fixed a one-way link after cold power-ups, plus a watchdog),
  boot-time trims that took boot from ~3 min to ~1¼ min, and the camera server and its
  systemd unit, which runs `roboscope.camera` copied to /opt/roboscope). Its README covers
  rebuilding the card and the camera's verified state. `legacy_2019/` holds redacted
  copies of the old webcam setup.
- `hardware/host_pi/`: the Raspberry Pi 3B+ that will run roboscope at the telescope
  (`roboscope.local`, Pi OS Lite 64-bit Trixie). It holds the Uno by USB serial and the
  camera Zero by its USB link. As with the camera Pi, `rootfs/` config plus
  `provision.sh` are applied by `scripts/deploy_host_pi.sh`:
  - key-only SSH, automatic updates, Bluetooth off and boot trims;
  - a static address on the camera link;
  - uv, and an SSH key authorized on the Zero;
  - the camera hub, a user service (`roboscope.camera.server --source remote`).

  The same script syncs the repo to `~/roboscope` on the Pi, runs `uv sync` and
  restarts the hub.
  Its power supply is still marginal (see its README).
- `notebooks/`: numbered exploration notebooks for hardware bring-up and experiments.
- `documentation/`:
  - `PLAN.md`: the staged plan.
  - `telescope_interface.md`: every input and output of the telescope (ports, LX200
    commands, motor bus), with what's been verified.
  - `hardware_findings.md`: dated experimental results.
  - `pi_techniques.md`: Raspberry Pi techniques used here, with commands. They include
    USB power switching (`uhubctl`), power-supply checks (`get_throttled`), USB gadget
    networking, udev triggers and boot diagnostics.
  - `next_session_board_wedge.md`: the motor-board bus-sync ("wedge") investigation.
    Resolved: the boot-time resync was the cause.
  - `wiring_hbx_uno.md`: Uno ↔ HBX motor-bus wiring.
  - `camera_pi_state.md`: the camera Pi as found before its 2026-11-25 reinstall.
  - `hardware_plan_motor_controller.md`, `hardware_plan_alignment_camera.md`: hardware
    plans and shopping lists.
  - Meade protocol PDFs and the cable wiring diagram.

Key external interface: the Meade LX200/Autostar serial protocol (9600 8N1), documented
in `documentation/TelescopeProtocol_2010-10.pdf`. Earlier proof-of-concept code lives
outside this repo in `../Telescope Control/`.
