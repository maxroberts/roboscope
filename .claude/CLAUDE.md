# roboscope

Python control software for a Meade StarNavigator NG alt-az telescope with an AudioStar
handbox, talking over RS-232 (Meade LX200/Autostar protocol, 9600 8N1). The goal is
closed-loop pointing and tracking via camera plate solving, eventually running on a
Raspberry Pi reached over SSH. The staged plan is in `documentation/PLAN.md`.

## Hardware
- Mount: StarNavigator NG 102 (102 mm refractor, 660 mm f/6.5), AudioStar handbox.
- Main camera: the user's existing Pi Zero W + Pi camera rig at the focuser (~0.3° field).
- Controller (planned): Pi 3B+, 64-bit Pi OS. Planned solver camera: Pi HQ + 16–25 mm lens.
- Serial link that works: the FTDI FT232R cable (`/dev/cu.usbserial-A9IDYQVP`) into the
  AudioStar's own RS-232 jack. AudioStar firmware A4S2 (Sep 21 2018). 800/800 queries
  OK, ~60 ms per query (2026-11-24). The second cable (PL2303) is untested on the
  handbox jack. The base's AUX port does not answer LX200; the interpreter is in the
  handbox.
- Focus is manual.

## Commands
- `uv sync --dev`: install
- `uv run pytest`: tests
- `uv run python scripts/probe_mount.py --port <dev> [--repeat N] [-v]`: read-only
  hardware probe and cable test (`-v` logs raw serial bytes).
- Notebooks in `notebooks/` (numbered, `01_...`) for hardware experiments.

## Conventions
- Angles in degrees, RA in hours, longitude east-positive, UTC offset ISO sign. Meade's
  opposite conventions are converted only in `mount/protocol.py`.
- When hardware behavior differs from the simulator, update `mount/sim.py` so the
  tests keep matching reality.

## Reference material
- Protocol: `documentation/TelescopeProtocol_2010-10.pdf` (newer) and
  `documentation/LX200CommandSet.pdf`. AudioStar = "Autostar" column; Autostar II-only
  commands (e.g. `:RA`, `:RE`, `:hI`) are not expected to work.
- Mount manual: `../StarNavigator-NG_InstructionManualV001a.pdf`
- Earlier code: `../Telescope Control/telescope.py` (old serial wrapper, partly broken)
- Old captures: `../Telescope Pictures/`

## Hardware safety
- Any command that moves the mount needs the user's go-ahead unless they've said the
  scope is set up and clear. Always know how to send `:Q#` (halt all).
- Never point at or near the Sun. Software must enforce a Sun exclusion zone.
- Current bench setup (2026-11-24): wires are attached to the telescope. Azimuth motion
  must stay within ±45° of the position at the start of the session until the user
  lifts this.
- Don't send bytes to a port until it's confirmed what is on the other end. The base's
  AUX port is a Meade accessory port, not the handbox's RS-232 port. Community sources
  (weasner.com, R. Seymour) describe it as a 4-pin modified I2C bus with battery power
  on one pin, and warn that RS-232 levels can damage it. Don't send serial data into it.
- Project direction (2026-11-24): the AudioStar handbox will NOT be used. All pointing
  and tracking logic is to be ours.
