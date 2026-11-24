# Hardware plan: alignment camera and tilt sensor

**Goal:** give roboscope ground truth for where the telescope points, independent of
any motor encoder. Two sensors on the tube:
1. **A wide-field "solver" camera.** Plate solving gives absolute RA/Dec at night;
   image shifts measure real motion in daytime.
2. **An accelerometer.** Gravity gives absolute altitude any time. This matters because
   the handbox does not report altitude (see [hardware_findings.md](hardware_findings.md)).

The main-tube camera (your Pi Zero W rig) stays for viewing and imaging. The solver
camera handles "where am I".

**Status:** plan. Background in [PLAN.md](PLAN.md) §2.

## Design

```
                 ┌──────── on the optical tube (rigid bracket) ────────┐
                 │  HQ camera + 16 mm lens      LSM6DSOX accelerometer │
                 └─────────┬────────────────────────────┬──────────────┘
              camera ribbon│ (crosses alt axis only)    │ I2C (4 wires)
                 ┌─────────▼────────────────────────────▼──────────────┐
                 │  Raspberry Pi on the mount arm (rotates with az)    │
                 │  + 12 V → 5 V converter from the mount's 12 V        │
                 └─────────────────────────────────────────────────────┘
```

- **Field of view:** HQ sensor (6.287 × 4.712 mm) with a 16 mm lens gives about 22° × 17°
  at about 20″/px. Sensor size is from the Sony/RPi specs as I know them; not re-checked.
- **Rigidity matters more than alignment.** The camera needn't point exactly where the
  main tube points. Software measures that offset once (the "boresight"), but it must
  not shift afterwards.
- **Accelerometer on the same bracket,** so it tilts with the tube and the camera.
- **Pi on the mount arm,** so only the camera ribbon and the I2C wires cross the
  altitude axis (0–90°, short flex). Nothing crosses the azimuth axis except mount
  power. Uses the existing Pi 3B+ to start; see the Pi 5 option below.

## Steps

1. **Bench (indoors).**
   - Capture with `rpicam-still` / `picamera2` at manual exposure and gain.
   - Focus the lens on a distant object out a window, lock the focus ring, and mark it.
   - Read the LSM6DSOX over I2C. Calibrate the zero on a level surface and in four
     orientations.
2. **Mount on the tube.**
   - Bracket on the finder shoe or a tube ring.
   - Check that the accelerometer's altitude agrees with the mount's side angle scale
     across 0–60°.
3. **Daytime motion truth (no stars needed).** **Point well away from the Sun.**
   - Aim at distant scenery.
   - Run `motion_probe`-style moves while capturing. Image cross-correlation gives the
     real azimuth/altitude motion: rates per speed, backlash at each reversal, and
     slip. These are the first true measurements of the mount.
4. **First night: plate solving.**
   - Star detection plus cedar-solve (a tetra3 fork) with a database built for our
     field of view.
   - Measure solve rate and time on the Pi.
   - Record frames all night for offline tuning.
5. **Boresight calibration.**
   - Center a bright star (or a distant daytime target) in the main-tube camera or
     eyepiece.
   - Record where it lands in the solver image. That pixel is "where the main tube
     points".
6. **Closed loop.** Goto → solve → correct → repeat (Stage 3 of the plan). The
   accelerometer provides altitude between solves and a sanity check on them.

## Shopping list

**Core** (needed for the steps above):

| Item | Qty | Purpose | Link | Price |
|---|---|---|---|---|
| Raspberry Pi HQ Camera kit: camera + 16 mm f/1.4 C-mount "telephoto" lens + 6 mm f/1.2 wide lens | 1 | Solver camera. The 6 mm lens (63° field) could later serve as an all-sky cloud camera | https://www.canakit.com/raspberry-pi-hq-camera-kit.html | $144.95 |
| *(alternative)* HQ Camera alone, CS variant, includes a C→CS adapter, ¼"-20 tripod thread; add your own C-mount lens of ~12–25 mm, f/1.4–f/2 | 1 | Same, if you source a lens separately | https://www.raspberrypi.com/products/raspberry-pi-high-quality-camera/ | not shown |
| Adafruit LSM6DSOX 6-DoF accelerometer + gyro, I2C, STEMMA QT | 1 | Absolute altitude from gravity | https://www.adafruit.com/product/4438 | $11.95 |
| STEMMA QT/Qwiic → female socket cable (Adafruit 4397 is out of stock; any Qwiic-to-female-jumper cable works) | 1 | LSM6DSOX → Pi GPIO header | https://www.adafruit.com/product/4397 / search: https://www.amazon.com/s?k=qwiic+to+female+jumper+cable | $0.95 |
| Raspberry Pi camera cable, 500 mm (Standard–Standard for a Pi 3B+) | 1 | Camera → Pi across the altitude axis | https://www.raspberrypi.com/products/camera-cable/ | not shown |
| Finder-shoe or tube-ring bracket with a ¼"-20 screw (or 3D-print one) | 1 | Rigid camera + sensor mount on the tube. Check what the NG 102's finder base accepts | Search: https://www.amazon.com/s?k=telescope+finder+shoe+1%2F4-20+adapter | check |
| 12 V → 5 V buck converter, USB output, ≥3 A | 1 | Power the Pi from the mount's 12 V supply | Search: https://www.amazon.com/s?k=12v+to+5v+usb+buck+converter+5a | check |

**Pi 5 upgrade** (recommended before the main camera and the solver share one board):

| Item | Qty | Purpose | Link | Price |
|---|---|---|---|---|
| Raspberry Pi 5 (4 GB is plenty) | 1 | Has "2 × 4-lane MIPI camera/display" connectors, so it runs the solver camera and main camera together. Faster solving | https://www.raspberrypi.com/products/raspberry-pi-5/ | check |
| Camera cables, **Standard–Mini**, 500 mm (Pi 5 has 22-way connectors) | 2 | Both cameras | https://www.raspberrypi.com/products/camera-cable/ | not shown |
| Raspberry Pi Active Cooler | 1 | Pi 5 sustained solving | https://www.raspberrypi.com/products/active-cooler/ | not shown |
| 27 W USB-C supply (5.1 V / 5 A) for the bench; in the field, a 12 V → 5 V 5 A USB-C PD converter | 1 | Pi 5 power | https://www.raspberrypi.com/products/27w-power-supply/ | not shown |

**Later, optional:** a dew heater strip and lens hood for all-night runs.

## Risks
- **Sun:** never point the solver camera near the Sun during daytime tests. The
  software Sun exclusion (planned `safety` module) must exist before any unattended
  daytime motion.
- **Flex:** a bracket that sags as altitude changes shows up as a boresight error that
  varies with altitude. Mitigation: a stiff bracket; check by solving at several
  altitudes on one star.
- **Pi 3B+ speed:** solve time is unmeasured. Mitigation: pass the mount's estimated
  position to the solver to narrow its search, or move to the Pi 5.
- **Dew** on the lens during long runs (hood first, heater if needed).
