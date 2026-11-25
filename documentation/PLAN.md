# roboscope: plan of attack (draft for discussion)

Goal: a StarNavigator NG that can be told "it's 19:32 on 2026-11-24 in 94110, align
yourself, then point at Saturn", and does it with camera feedback, without a human
centering stars. It should also self-calibrate (backlash, slip, camera/eyepiece offsets)
and track for long periods unattended.

## 1. What we have (from the docs and the earlier code)

**Mount.** StarNavigator NG: single-arm motorized alt-az on a tripod, driven by the
AudioStar handbox [1]. Relevant facts from the manual [1]:
- Nine slew speeds: 1x, 2x, 8x, 16x, 64x sidereal, then 0.5, 1, 1.5 °/s, and "max"
  (spec: up to 5.5 °/s) [1, p.18, p.43].
- Backlash is handled inside the handbox ("Az/Alt Percent") and by "Train Drive", which
  must be redone every 3–6 months [1, p.29, p.48].
- The altitude lock doubles as the motor clutch, and azimuth holds by friction of the
  tripod-base knob [1, p.7–8, p.14]. **Slip is therefore expected in both axes**, and
  the handbox can't see it.
- "Cord Wrap" setting exists (off by default) [1, p.28].
- Alignment assumes a level tube pointing at true north ("home position"), then Easy
  Align on two stars [1, p.19–22].
- **Ours is the NG 102:** 102 mm achromatic refractor, 660 mm focal length, f/6.5
  [1, p.43].

**Control link.** The handbox has an RS-232 port (RJ10/4P4C). Pinout: RJ10 pin 1 RX,
pin 2 TX, pin 4 GND to DB9 pins 3, 2, 5 [4]. 9600 8N1 [5]. The command set is the
Meade LX200/Autostar protocol [2][3]. Commands that matter for us and that the doc
marks as Autostar-supported [2]:
- Read position: `:GA#` `:GZ#` (alt/az), `:GR#` `:GD#` (RA/Dec), `:GW#` (alignment and
  tracking status), `:U#` (high-precision readout).
- Go to: `:Sr`/`:Sd` then `:MS#` (RA/Dec), `:Sa`… then `:MA#` (alt-az).
- Sync: `:CM#` (sync current position to target coordinates), which is the hook for
  feeding plate-solve results back into the handbox.
- Manual motion: `:Mn/s/e/w#` + `:Q#`, rates `:RG/RC/RM/RS#`; pulse guide
  `:Mg<dir><ms>#` (Autostar firmware ≥30Eb, guide rate 67% sidereal) [10].
- Keypad emulation: `:EK<code>#` [5], which can drive any handbox menu, including
  alignment and Train Drive.
- Backlash read/write: `:GpB#`/`:SpB#` [2].
- Time/site: `:SL` `:SC` `:St` `:Sg` `:SG` `:W<n>#` [2].
- Not available on Autostar: arbitrary per-axis rates `:RA/:RE` (Autostar II only),
  `:hI` remote time init (Autostar II only) [2]. **This means we cannot do our own
  alt-az tracking by commanding axis rates. Tracking must come from the handbox's own
  model, and we correct it.**

**Earlier code** [5] already has connect/ACK, string responses, slew/halt, speeds, focus,
and keypad. Known issues:
- `setTime` uses `:hI`, which Autostar doesn't support.
- `setLocation` is marked "NOT working"; likely causes are the `:Sg` format (needs
  `DDD*MM`) and site selection.
- `set_azimuth_rate` uses `:RA`, which is Autostar II only.

**Camera.** The 2022 Jupiter/Moon recordings came from a Raspberry Pi camera on a Pi Zero
W, in a custom mount on the 102's focuser. It works and stays as the main-tube camera.
Which camera module it is still needs confirming.

**Fields of view on the 660 mm tube.** Pixel scale = pixel size / focal length (my
arithmetic, from published sensor sizes):

| Camera module | Field of view | Pixel scale |
|---|---|---|
| v1 (OV5647) | 0.33° × 0.24° | 0.44″/px |
| v2 (IMX219) | 0.32° × 0.24° | 0.35″/px |
| HQ (IMX477) | 0.55° × 0.41° | 0.48″/px |

The Moon (~0.5°) overfills a v1/v2 frame. These fields are too narrow to solve quickly
with no prior position, which is why §2 adds a separate wide-field solver camera.

**Computers available:** Pi Zero W (several) and Pi 3B+ (several).
- **Zero W:** single-core ARMv6, 32-bit only. Fine as a camera streamer, too weak for
  solving.
- **3B+:** quad-core and runs 64-bit Pi OS. That's the controller. Solves will be slower
  than the ~1/s PiFinder gets on a 4B [6]; to be measured, but a few seconds is fine for
  our loop.

**Serial cables:** two RJ10→DB9 + USB-serial setups (one homemade, one bought). Stage 1
compares them.

## 2. Architecture

```
 Mac (you + Claude over SSH) ──wifi/ssh──▶ Pi 3B+ "controller" on the mount arm
                                            ├─ USB-serial ─▶ AudioStar RS-232 (RJ10)
                                            └─ CSI ────────▶ HQ camera + 16–50 mm lens
                                                             ("solver", on the tube)
                                 wifi ◀──── Pi Zero W + Pi camera at the 102's focuser
                                            ("main", existing rig; streams frames)
```

The Pis each have one camera port, so each camera gets its own Pi. The Zero W keeps its
proven job; the 3B+ owns the mount and does the heavy computing.

- **Pi on the rotating arm with its own battery.** The arm carries the batteries and
  control panel [1, p.14], so if the Pi, cameras and a 12 V pack ride on it too, no cable
  crosses the azimuth axis. That removes cable wrap by construction. (To verify on your
  unit.) Software still tracks cumulative azimuth and refuses moves beyond ±270°, as a
  backstop.
- **Our own Python stack, not INDI/KStars.** INDI has an Autostar driver and Ekos does
  solve-and-sync, but the whole point here is custom calibration and closed-loop
  behavior. A plain Python package is easier for us to instrument and iterate on. We can
  expose an INDI or Alpaca interface later if you want Stellarium/KStars on top.
- **The handbox does coarse goto and tracking; we close the loop.** The cycle is: goto →
  capture → plate solve → compute error → `:CM#` sync and/or pulse-correct → repeat until
  inside tolerance. During tracking, solve every N seconds and pulse-guide out drift.

### Key recommendation: add a wide-field solver camera

The main tube's ~0.3° field makes blind ("lost-in-space") solving slow or impossible with
the fast solvers. PiFinder pairs a Pi HQ camera with tetra3 and solves in about 150 ms on
a Pi 4B (100 ms star extraction + 44 ms solve) [6]. The Pi HQ camera (IMX477) [12] on
the tube with a small CS/C-mount lens gives:

| Lens | Field of view | Pixel scale |
|---|---|---|
| 16 mm | 22° × 17° | 20″/px |
| 25 mm | 14° × 11° | 13″/px |
| 50 mm | 7.2° × 5.4° | 6.4″/px |

Wider fields solve more reliably with fewer, brighter stars. Narrower fields pin the
position more precisely. Centroiding to a fraction of a pixel puts even the 16 mm lens
well inside the main tube's 0.3° field, so 16 or 25 mm is a good start. Benefits:
- **Alignment becomes "solve 2–3 random patches of sky"** instead of finding named
  bright stars. Level/north setup only needs to be rough.
- **Missed targets mostly disappear**, because every frame says exactly where we are.
  Spiral search becomes a fallback for clouds and trees, not the main strategy.
- **Camera/eyepiece offset is a one-time calibration:** center a star in the eyepiece (or
  main camera) once and record its pixel position in the solver camera. Your flip-mirror
  idea still fits for main-camera ↔ eyepiece. The solver camera handles "where am I".

Solver options:
- **cedar-solve:** Apache-2.0 Python fork of ESA tetra3 [7].
- **cedar-detect:** fast Rust centroider exposed over gRPC; source-available FSL-1.1
  license, not pip [8].
- **astrometry.net / ASTAP** as a fallback for narrow fields (local index files).

## 3. Proposed modules

| Module | Responsibility |
|---|---|
| `mount` | Serial transport, typed command wrappers, response parsing, a fake-mount simulator for tests |
| `motion` | Backlash-aware moves (always finish approaching from one direction), az wrap limits, slip detection (encoder vs solve) |
| `sky` | Time + ZIP → lat/lon (offline table), Skyfield [9] ephemerides for Moon/planets, Hipparcos bright stars, OpenNGC later |
| `camera` | Capture abstraction (Pi camera, LPI-G/ToupTek, or UVC), exposure control, frame logging |
| `solve` | Star detection, plate solve → RA/Dec + roll; pixel ↔ sky transforms; camera-to-optical-axis offsets |
| `align` | Session bring-up (set time/site, run handbox align via keypad, then solve-and-sync), centering loop, tracking/guiding loop, spiral search |
| `safety` | Sun exclusion zone (hard refusal), horizon/altitude limits, park at dawn |
| `cli` | `roboscope "point at Saturn"`-style entry point; later the LLM/voice front end |

## 4. Stages (one at a time, review between each)

**Stage 0: inventory.** Done (see §1), apart from the open items in §6.

**Stage 1: mount driver + bench characterization (indoors/daytime, no sky).**
- ~~Mount driver with tests against a simulated mount~~. Done: `roboscope.mount`,
  written fresh from the protocol doc rather than ported.
- Run `scripts/probe_mount.py` on hardware. Confirm the commands marked *unverified*,
  the reply formats (degree byte, precision), and that time/site/DST setup gives the
  right sidereal time.
- Compare the two cables: run each against the handbox, compare query failures and
  garbled replies over many round-trips, and keep the better one. This works from the
  Mac first, then from the 3B+.
- Probe what this firmware actually accepts: `:GVN#` version, `:Mg`, `:GpB`, `:RA`,
  `:MA`, `:U` precision.
- Characterize with `:GA/:GZ` readback: rate per speed, settle time, repeatability, and
  the backlash the handbox reports.
- Point a camera at a distant terrestrial target (daytime) and measure **real** motion by
  image cross-correlation vs commanded motion. This gives backlash, slip and rate
  calibration independent of the encoders, which is the most valuable early data.

**Stage 2: camera + solving (first night).** Capture pipeline, frame logging, star
detection, plate solve, solver-camera FOV/roll calibration. Pure observation; the mount
just sits.

**Stage 3: closed-loop goto.**
- Session bring-up from time + ZIP.
- Goto → solve → sync/correct loop. Measure pointing error before and after.
- Spiral search fallback. Moon and bright planets as first targets.

**Stage 4: tracking + unattended operation.**
- Periodic solve + pulse-guide.
- Slip detection and recovery.
- Az wrap accounting, Sun-safe park at dawn, all-night logging for analysis.

**Stage 5: optics.**
- Main-camera/eyepiece offset calibration (flip mirror).
- Focus metric (HFR/FWHM, or Bahtinov).
- Autofocus, if we add a focus motor: Autostar's `:F+/:F-` drive an AUX-port focuser [2],
  or a DIY stepper on the knob.

**Stage 6: natural-language front end.** A local model turns text/voice into the `cli`
commands.

## 5. Risks and unknowns
- **Handbox alignment via keypad emulation is brittle.** Menu flows depend on firmware.
  Stage 1 tests whether we can skip "home position" prompts and just sync from solves.
- **Pointing accuracy floor.** The handbox's own model and encoders limit open-loop
  accuracy. Closed loop makes this matter less, but slip during tracking still needs
  detection.
- **Pi 3B+ performance:** solve time is unmeasured. If it's too slow, we can give the
  solver a position hint from the mount's own readout to narrow the search, or move up
  to a Pi 4/5.
- **Power** for all-night runs: 8×AA won't last. The mount (12 V) and two Pis (5 V)
  need a battery solution (you're working on it).
- **Sun safety:** daytime calibration and dawn need a hard software Sun exclusion, since
  the camera or an eye at the eyepiece could be damaged [1].

## Progress note (2026-11-25)
- **Sky module done** (`roboscope.sky`).
  - Design decision: keep it camera-agnostic, so the wide solver camera and the narrow
    in-tube camera share it. The user expects to test the narrow approach first.
  - For a narrow camera that sees one star, alignment stars must be *isolated*: no
    comparably bright star within the pointing-uncertainty radius. `alignment_candidates`
    enforces that.

- **Alignment layer done in simulation** (`roboscope.align`).
  - Findings from simulation:
    - visit stars nearest-first, because scale errors grow with slew length;
    - 3 stars only fit index + tilt, so far-sky errors reach ~50′ unless the hardware
      terms (scale, collimation) are kept from an earlier session or ≥5 stars are
      used;
    - the narrow camera's first star can take ~200 frames of spiral search with a
      ~1–2° initial error. Seed IA/IE from the magnetometer and accelerometer, or start
      on the Moon.
  - Next: hardware adapters (MotorController → `goto`/`position`; Pi camera → `locate`),
    camera-frame calibration, backlash handling.

## 6. Decisions and open questions

Decided (2026-11-24):
- Mount is the NG 102.
- The main-tube camera stays the existing Pi Zero W + Pi camera rig.
- A Pi 3B+ is the controller.
- Try a Pi HQ camera as the solver.
- Focus stays manual for now.
- Ground truth for position: the camera (image shift in daytime, plate solving at
  night) for both axes, plus an accelerometer on the tube for absolute altitude. The
  handbox's alt/az readback is only an estimate (see telescope_interface.md).
- The handbox is used only as a dumb motor bridge (move/stop/readback). Long term,
  replace it by driving the motor bus directly (telescope_interface.md §5). Two
  independent sources now describe that bus (EQMOD-ETX code; A. Johansen on DS mounts).
  Next concrete step: sniff the real handbox ↔ NG base traffic with a ~$10 logic
  analyzer and PulseView. If it matches, a microcontroller replaces the handbox and
  gives us raw encoder counts (~0.73″/count on DS mounts), motor PWM and an
  encoder-error flag.
- Don't reflash the AudioStar. Peek access (`:EP`) needs patched firmware, and A3S4+
  firmware changed tracking/guiding.

Cameras and boards on hand (2026-11-24):
- Several 5 MP Pi cameras (presumably OV5647) and many 1 MP Pi cameras (model unknown).
- Willing to buy a Pi 4/5.

Plan for the solver camera:
- Try an OV5647 with an 8–16 mm M12 lens first. Fields of view: 8 mm 26°×19°, 12 mm
  18°×13°, 16 mm 13°×10°. Buy the HQ only if it struggles.
- If the 1 MP cameras are monochrome global-shutter (e.g. OV9281), they're worth trying
  too.

Pi 5 vs 3B+: the Pi 5 has two camera connectors, so one board could run the solver
camera, the main camera and the serial link. That removes the Zero W frame streaming,
and it's also faster at solving. A Pi 4 has one camera port and doesn't change the
design. Not needed for Stage 1; decide before Stage 2.

Open:
1. Which Pi camera module is on the Zero W rig (v1, v2, HQ)? Does it sit at prime focus
   (no eyepiece) or behind an eyepiece? This changes the scale in the §1 table.
2. Solver lens focal length: see the camera plan above. Model number of the 1 MP
   cameras?
4. Buy a Pi 5 before Stage 2, or measure 3B+ solve times first?
3. **Motor focus:** this matters for the main tube, not the solver lens. A solver focused
   once at infinity can be locked and left alone. A motorized lens for the HQ camera
   would not help main-tube focus. That needs a motor on the 102's focuser knob, or a
   focuser that the handbox's `:F` commands drive through the AUX port [2]. I haven't
   checked which focusers fit the 102. Revisit at Stage 5.

## References
1. Meade, *StarNavigator NG Series Instruction Manual* (2016). Local:
   `../StarNavigator-NG_InstructionManualV001a.pdf`
2. Meade, *Telescope Serial Command Protocol*, rev. 2010.10. Local:
   `documentation/TelescopeProtocol_2010-10.pdf`
3. Meade, *Telescope Serial Command Protocol*, rev. L (2002). Local:
   `documentation/LX200CommandSet.pdf`
4. RJ10 ↔ DB9 wiring diagram. Local: `documentation/IMG_3502.gif`
5. Earlier control code and notebook. Local: `../Telescope Control/telescope.py`,
   `../Telescope Control/Talking to the Telescope.ipynb`
6. Raspberry Pi Magazine, "Properly pointing telescopes with PiFinder" (timings, tetra3).
   https://magazine.raspberrypi.com/articles/properly-pointing-telescopes-with-pifinder
7. cedar-solve (fork of esa/tetra3, Apache-2.0). https://github.com/oakamil/cedar-solve
8. cedar-detect (Rust centroider, gRPC, FSL-1.1-MIT). https://github.com/smroid/cedar-detect
9. Skyfield (astronomy library for Python). https://rhodesmill.org/skyfield/
10. Weasner ETX site, Autostar feedback, Nov 2008: R. Seymour on `:Mg` support (≥30Eb) and
    67% guide rate. https://www.weasner.com/etx/archive/nov08/autostar.html
11. (Removed: concerned the LPI-G, which turned out not to be the camera in use.)
12. Raspberry Pi High Quality Camera product page.
    https://www.raspberrypi.com/products/raspberry-pi-high-quality-camera/
    Sensor figures used here: IMX477, 6.287 × 4.712 mm, 1.55 µm pixels. These come from
    Sony/RPi documentation as I know it, not re-checked against this page.
    *(unverified)*
