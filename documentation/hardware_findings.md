# Hardware findings log

Facts established by experiment on our hardware, with date and evidence. The plan
(`PLAN.md`) and code docstrings should agree with this file. Where the Meade doc and our
hardware disagree, this file wins.

## 2026-11-24: serial link and handbox identity
- The base's AUX port does not answer LX200 queries at any baud from 1200 to 115200.
  Tested with both the PL2303 and FTDI cables. The LX200 interpreter lives in the
  handbox.
- Working link: FTDI FT232R cable (`/dev/cu.usbserial-A9IDYQVP`) into the AudioStar's
  RS-232 jack, 9600 8N1. 800/800 position queries OK, ~60 ms each.
- Handbox: `:GVP` "Autostar", `:GVN` "A4S2", `:GVD` "Sep 21 2018". Starts in high
  precision.
- Replies: degree byte 0xDF, ':' before seconds (e.g. `+00\xdf00:00#`).
- `:GW#` replies 3 characters with **no '#'** (e.g. `AN0`).
- `:GpB#` gets no reply.
- The handbox clock was unset (reported 2019-01-17).

## 2026-11-24: first guarded motion test (`scripts/motion_probe.py`)
Data: `data/motion_probe_20261124_072226.jsonl`. Handbox unaligned (`AN0`).
All speeds are from the handbox's own readback, not independently measured.

**Azimuth**
- Readback updates during motion. `:Me` (east) *decreases* `:GZ` azimuth; `:Mw`
  increases it.
- Steady-state speeds from mid-move samples:
  - `:RG` (guide): ~0.0035–0.0037 °/s, about 0.85× sidereal.
  - `:RC` (center): ~0.36–0.37 °/s.
  - `:RM` (find): ~0.71 °/s.
  - Readback updates lag by about 0.3 s after a start command.
- **Direction asymmetry at guide rate:** after 3 s east (−0.0125°), 3 s west moved only
  +0.0009° and then stopped changing. Possibly backlash take-up hidden from readback.
  Unexplained; needs independent (camera) measurement.
- The east/west pairs did not cancel exactly. Net azimuth error after the whole sequence:
  −0.033°.

**Altitude**
- `:Mn`/`:Ms` at `:RC` and `:RM` produced **no change** in `:GA`.
- Re-run with the user watching (altitude steps only, data
  `data/motion_probe_20261124_083828.jsonl`): the mount **visibly and audibly tilted**.
  The side angle scale moved about 1°, consistent with 3 s at `:RC` ≈ 1.1°. `:GA`
  stayed `+00ß00:00` throughout.
- **Conclusion:** the altitude drive works over serial, but the unaligned handbox does
  not report altitude. Altitude feedback must come from elsewhere (accelerometer,
  camera, or the motor bus).
- Follow-up checks, same day:
  - **Command check:** the driver sends exactly `:GA#` (the documented "Get Telescope
    Altitude"). The reply is all zeros in both high (`+00ß00:00#`) and low
    (`+00ß00#`) precision. `:GR`/`:GD` are also stuck at 0 while unaligned.
  - **Larger moves:** `:RM` + `:Mn#` for 7 s, then `:Ms#` for 7 s. `:GA` stayed 0
    throughout.
  - **Direction and rate:** two identical `:RM` + `:Mn#` 7 s moves with a pause. The
    user saw the mount tilt the (future) tube front **up**, "roughly 5 degrees each
    time" on the side scale (coarse). So **north = up**, ~0.7 °/s at `:RM`, matching
    azimuth (0.71 °/s), and the two moves looked the same size. `:GA` stayed 0 for all
    17 samples.
  - Return and crude backlash check: two `:RM` + `:Ms#` 7 s moves (the first one a
    reversal). The user read "pretty close to 9-10˚ total" down on the side scale,
    matching the ~10° up. **No altitude backlash or slip detectable at the scale's
    ~1° resolution.** Smaller backlash needs the accelerometer or camera.
- Not tested: whether `:GA` starts updating once the handbox is aligned. We don't plan
  to use its alignment.
- Bench condition: no optical tube mounted (mount arm only), so balance and load differ
  from real use.

**Pulse guide**
- `:Mgw2000`/`:Mge2000` produced no readback change. At the 67% sidereal guide rate
  reported for the #497, 2 s would be ~0.0056°, which is resolvable at 1″ readback.
  Treat `:Mg` as not working on A4S2 in this state.

## 2026-11-24: full read-only query sweep
Every "get" command in the protocol doc was sent to the A4S2 handbox.

**Position outputs**
- `:GZ` azimuth, `:GA` altitude, 1″ resolution.
- `:GR`/`:GD` RA/Dec, derived from alt/az and the handbox's site and time.
- `:D` slewing flag; `:GW` status (`AN0`, no '#').
- There is no raw encoder, motor-current, temperature, limit-switch or focuser output.
  These positions are the handbox's estimate. If they come from motor-shaft encoders,
  as I believe (not documented for the NG), they can't see gear backlash, skipped
  teeth or clutch slip.

**Other replies**
- `Gh` high limit `71ß#` (71°); `Go` low limit `-05ß#` (−5°).
- `GT` `+60.1#`; `GH` `0` (no '#'); `Gc` `(24)#`.
- `GM`/`GN` site names; `GO`/`GP` `<AN UNUSED SITE>`.
- `Gb +20.0`, `Gf -05.5`, `GF 015'`, `Gl 000'`, `Gs 000'`, `Gq SU`, `Gy GPDCO`;
  `Lf` "Objects: 0"; `LI` static M31 string.
- `G0`/`G1`/`G2` reply `#` (empty).

**No reply:** `GE`, `Ge`, `Gm`, `GpB`, `GpH`, `GpS`, `h?`, `FB`, `Fp`, `fT`, `fC`, `??`.

## 2026-11-24: HBX port measurements (user, multimeter)
- **+12 V measured between pins 1 and 8.** This matches the expected ETX-60 map (pin 1
  Vbat, pin 8 ground) in its newer numbering direction (telescope_interface.md §5).
- **The "data" pins read as shorted to each other.** Consistent with a single shared
  data line ("the early ETX60 has all data lines joined"; DS mounts use "a common data
  line and 2 clocks"). The other pins were hard to measure.
- Still to confirm: exactly which pins are joined (expected 3, 5, 7); that the clock
  pins (expected 2, 4, 6) are not joined to data or to each other; and the idle voltage
  on pins 2–7. Continuity should be measured with power off.
- Follow-up (user, multimeter):
  - **Continuity (power off):** confirmed as expected. Data pins joined; clock pins
    isolated.
  - **Idle voltage, HBX unplugged, on a ~10.5 V battery:** pins 2–7 read **~5 V below
    pin 1 (Vbat)** but **0 V against pin 8 (ground)**, repeatably.
    - *Interpretation (hypothesis):* the bus lines float when no master is attached,
      because the pull-ups are on the master side. The meter's input drags a floating
      line toward whichever reference it's measured against. Against Vbat, the line
      rises until the motor board's input clamp holds it at about its own logic supply
      (≈5 V + a diode drop), giving ≈10.5 − 5.5 ≈ 5 V below Vbat. That also suggests
      ~5 V logic on the motor boards.
    - *To confirm:* measure pins 2–7 against pin 8 with the handbox connected through a
      breakout. They should idle near 5 V, with clock activity during polling.
  - **On 12 V wall power with the HBX port unplugged, the mount started moving, every
    time** during these tests.
    - *Likely cause (hypothesis):* noise from the AC adapter and the probes reaches the
      floating clock/data lines, and a motor board decodes a stray command.
    - The user reports it **only started when pins were touched**, but **once started,
      the motion persisted through power cycles**.
    - Unexplained. Candidates:
      - a power-off too short to drain capacitors, so the motor-board chip keeps its
        state;
      - immediate re-triggering at power-up from noise on the floating bus;
      - state stored in the motor board's non-volatile memory (no evidence for this).
    - Open questions: the nature of the motion (axis, direction, speed), how long power
      was off, and what finally stopped it.
    - Until resolved: pull the plug if it moves; keep it off for ≥30 s; attach the
      handbox before re-powering; loosen the clutches while probing.
    - **Safety rule:** don't power the base with the HBX port empty. Our controller
      must enable pull-ups and hold the bus idle before the mount is powered on.

## 2026-11-24: standard Ethernet cable on the HBX port shuts the mount off
- The user connected a regular (straight-through) Ethernet cable to the HBX port. The
  base power light went **off immediately** when switched on.
- The handbox was attached at the far end (user).
- **Explanation, supported by community sources:** the stock Meade HBX coil cord is
  reversed, pin 1 ↔ pin 8. Weasner "Autostar cable" compilation
  (http://www.weasner.com/etx/autostar/as_cable3.html):
  - S. Bird 2004: "1-8, 2-7, 3-6, 4-5, 5-4, 6-3, 7-2, 8-1 ... It is easy to get the
    connections wrong, i.e., 1 to 1, 2 to 2, etc., TAKE CARE!"
  - P. Smith 2007: "1 -> 8 / 2 -> 7 / ... / 8 -> 1".
  - R. Seymour: "connect pin 1 to pin 8 of the far end".

  A straight-through replacement therefore puts base +12 V (pin 1) on the handbox's
  ground pin and base ground (pin 8) on its supply pin: reversed polarity, so
  protection trips.
  - Straight Ethernet works only as an *extension in series with* the stock cord (via a
    coupler), which keeps the overall mapping reversed (same page).
  - Dissent: R. Allen (2008, weasner.com/etx/autostar/2008/497HBXcable.html) calls the
    cord "real 1:1". This conflicts with the majority view and is not adopted.
  - This also explains EQMOD-ETX's mirrored pin numbering: one set counts at the
    handbox end, one at the base. Our numbering is at the **base socket** (+12 V on
    pin 1, measured).
- Pending: confirm the handbox still works on its own cord.
- **Rule:** never connect the handbox through a straight-through cable. For our
  controller's cable, identify each conductor by measurement at the cut end; don't trust
  wire colors or pin numbers.

## 2026-11-24: direct motor-bus control from an Arduino Uno (no handbox)
Setup:
- `firmware/hbx_probe` on an Uno, wired to the base HBX socket: pin 6 → D2 (Az clock),
  4 → D3 (Alt clock), 2 → D4 (AUX clock), joined data → D5, each via 820 Ω; pin 8 → GND;
  pin 1 (+12 V) unconnected.
- Power order: Uno running first, then mount on battery. Clutches as found (engaged).
- Host link: a bridge process holds the Uno's port open so it never resets while the
  mount is powered. Opening the port on macOS resets the Uno even with DTR/RTS held low.

Results:
- **Mount off, Uno wired:** data line reads 0. The pull-up current flows into the
  unpowered board's input clamp, so keep this state brief.
- **Mount on:** data line reads 1. **Both motor boards acknowledge the handshake**
  (data pulled low within ~8–52 µs; released within ~32–44 µs).
- `GetMotorType` (`0x0B`): **`0x0C` on both axes**, repeatable.
- `GetStatus` (`0x08`): well-formed replies (delta 0, PWM 0, err 0 at rest). 200
  consecutive polls without error.
- Hand-turning test: inconclusive by design. The encoder is on the motor; with the
  clutch engaged the worm can't be back-driven by hand, and with it released the motor
  doesn't turn.
- **`SlewForward` (`0x07`) for 250 ms then `Stop` (`0x05`):** three runs. Each time the
  Stop was acknowledged on the first try. The user observed azimuth rotate **~1°
  clockwise (viewed from above)** and stop.
  - **But `GetStatus` returned delta 0 and PWM 0 on every read after each pulse.**
  - Hypothesis: the encoder LEDs are unset because no handbox initialized them.
    EQMOD-ETX runs Stop, Sleep `0xE4`, CalibrateLED `0x04`, waits 4 s, then reads
    GetLEDI `0x09`. Johansen says the handbox stores per-axis "Calibrate" values that
    set encoder LED brightness.
  - Next test: read GetLEDI; calibrate if 0; read status mid-pulse.

**Conclusion:** the NG's motor boards speak the ETX/DS motor protocol (handshake,
`0x0B`, `0x08`, `0x07`, `0x05` verified). The handbox is not needed to move the mount.
Encoder readout is not yet working.

## 2026-11-24: encoder readout working (sketch v2)
- With the 47 kΩ pull-ups in place, the Uno was **reset (port re-open) and re-flashed
  (upload) with the mount powered**, the user watching. **Nothing moved.**
- **GetLEDI (`0x09`) at power-up: 253 on both axes** (no handbox).
- **CalibrateLED sequence** (Stop `0x05` → 100 ms → Sleep `0xE4` → 100 ms → CalibrateLED
  `0x04` → 4 s): LED setting becomes **189 (Az)** and **169 (Alt)**. Johansen quotes
  handbox calibrate values of 199 and 183 for a DS mount.
  - The first GetStatus after calibrating showed +316 (Az) and +441 (Alt) counts, and
    Alt's error bit was 1 once. So calibration may turn the motor slightly (whether it
    visibly moved: pending user report).
  - Not yet known whether the setting survives a power cycle (assume not: the handbox
    presumably sets it at every power-up).
- **After calibration, encoder counts work.** 250 ms full-speed pulses (PWM 255 mid-slew):

  | Axis | Direction | Mid-slew delta | Delta after stop | Total |
  |---|---|---|---|---|
  | Az | fwd (CW from above) | +549 | +2488 | +3037 |
  | Az | rev | −1298 | −2574 | −3872 |
  | Alt | fwd (direction not yet observed) | +544 | +2733 | +3277 |
  | Alt | rev | −1249 | −2964 | −4213 |

  - No counts while idle.
  - The sign follows direction.
  - **Reverse runs ~25–30% farther than forward on both axes**, and is ~2× further
    along at mid-slew. Consistent across axes, so probably motor-board behavior
    (spin-up), not mechanics. Unexplained.
  - Counts per degree: not yet calibrated. One 250 ms Az fwd pulse looked like "~1°" by
    eye (3037 counts). Johansen's DS figure (1.369 counts/arcsec) would make it ~0.6°.
    Needs ground truth (camera or accelerometer, or a large move against the setting
    circle).
- Host tooling: the bridge's reply timeout (3 s) was shorter than `calibrate` (~4.3 s),
  so the calibrate replies were lost; the read-back via `ledi` confirmed the result.
  Fixed in `scripts/hbx_bridge.py` (now 15 s, to cover `run`).

## 2026-11-24: backlash vs. direction (azimuth, 250 ms full-speed pulses, encoder counts)

| Order | Pulse | Mid-slew | Total |
|---|---|---|---|
| 1 | rev (after fwd: reversal) | −1175 | −3816 |
| 2 | rev | −1225 | −3802 |
| 3 | fwd (reversal) | **+1323** | **+3985** |
| 4 | fwd | +535 | +2969 |

- **Forward shows a reversal effect:** the first fwd after a rev is ~2.5× further along
  at mid-slew and ~1000 counts longer in total than the next fwd. This is consistent with
  backlash: the motor spins up unloaded through the gear slack before it engages.
- **Reverse runs long whether or not it's a reversal,** so there's also a
  direction-dependent difference (motor drive, or a load that differs by direction).
- The encoder is on the motor, so it can't tell slack from real axis motion. The true
  backlash angle needs ground truth (camera or accelerometer).

## 2026-11-24: regulated speed control (sketch v3 `run`, SpeedStart `0x01`)
- The encoder LED settings **survived an Uno reset/upload** (189/169), since the motor
  boards stayed powered. Persistence across a mount power cycle is untested.
- Speed argument: 24-bit signed two's complement, MSB first; the sign is the direction
  (EQMOD-ETX).
- Expected scaling, from EQMOD-ETX's "ETX-xxx, DS-xxx" gearing (36 vanes × 4 × 205.333
  × 60-tooth worm ≈ 4,928 counts/°) and SIDEREALRATE = 6460.09 × counts/arcsec: speed
  value ≈ 429.5 × counts/s, so 1× sidereal = 8,844.

| Axis | Speed value | Duration | Steady counts/s | Expected | PWM range | Total counts |
|---|---|---|---|---|---|---|
| Az | 8,844 (1×) | 5 s | **20.0** | 20.6 | 0–96 | +101 |
| Az | 88,440 (10×) | 5 s | **205.7** | 206 | 0–192 | +1,064 |
| Az | 530,640 (60×) | 3 s | **1235.3** | 1236 | 0–255 | +3,847 |
| Az | −530,640 | 3 s | **−1235.3** | −1236 | 0–255 | −3,844 |
| Alt | 88,440 | 3 s | **203.6** | 206 | 0–176 | +641 |
| Alt | −88,440 | 3 s | **−204.2** | −206 | 0–176 | −641 |

- **The motor boards regulate speed to the commanded value** (PWM varies, the rate
  holds), matching the formula to within ~1% at 10× and above.
  - Forward and reverse are symmetric under regulated speed. The fwd/rev asymmetry seen
    earlier comes from the open-loop full-speed slew commands (`0x06`/`0x07`).
  - Coast after Stop: ≤ 20 counts.
  - All stops acknowledged on the first try.
- Still unverified: the counts-per-degree figure for the NG itself (the 4,928/° comes
  from EQMOD-ETX's DS table; the NG's gearing may differ). Needs ground truth.

## 2026-11-24: counts per degree from large moves (`scripts/hbx_large_move.py`)
- Out-and-back legs at speed 530,640 (~1,235 counts/s), chained 10 s `run`s, with 10 s
  pauses for the user to read the angle. Raw output in `data/large_move_*.txt`.
- Each 10 s run gave 12,456–12,505 counts (±0.2%) on both axes.

| Axis | Out | Back | Net | User-read angle | **Counts/°** |
|---|---|---|---|---|---|
| Alt | +148,120 | −148,194 | −74 | 35° | **≈ 4,230** (±3% for ±1° reading) |
| Az run 1 | +148,012 | −148,042 | −30 | 48° | **≈ 3,085** (±2%) |
| Az run 2 | +148,058 | −148,087 | −29 | 48° (repeatable) | ≈ 3,085 |

- **The NG's gearing differs from EQMOD-ETX's DS figure (4,928/°) and differs between
  axes.** Cross-check: an earlier single Az pulse of 3,037 counts looked like "~1°"
  (3,085/° → 0.98°).
- Both axes returned to their starting marks (user), consistent with the small encoder
  net. **No slip seen.**
- **Azimuth shows significant backlash** on reversal (user, by eye). It's taken up on
  reversal and returned, so it doesn't show at the end point. Size unmeasured; the
  encoder can't separate slack from motion.
- Derived (with these figures): 1× sidereal (15.041″/s) = **12.9 counts/s (Az)** and
  **17.7 counts/s (Alt)**, i.e. speed values ≈ **5,540 (Az)** and **7,590 (Alt)** at
  ~429.5 per count/s.
- **Safety miss:** the "30°" target used the DS figure, so azimuth actually travelled
  **48° from start, past the user's ±45° cable limit.** Rule since: targets and limits
  use measured counts/° with margin, and a short calibration move comes before any long
  move on unverified scaling.

## 2026-11-24: our motor controller on hardware (firmware/mount_controller + MotorController)
Bench test (`scripts/mc_bench_test.py`; log `data/mc_bench_20261124_215254.log`). Az
was kept within ±3° of boot, inside the user's ±10° rule for this session.

| Test | Result |
|---|---|
| Watchdog (host silent, Az 0.1°/s) | `EVT watchdog stopped` 1.0 s after the last command; moved 0.101° ✓ |
| `0x00` SpeedChnge while moving | Alt +423 → +843 → −419 counts/s vs commanded +423/+846/−423, incl. a direction reversal ✓ (first verification of `0x00`) |
| Sidereal on Az (12.89 cps) | 12.54 cps over 20 s (see the low-speed sweep below: within measurement noise) |
| Predictive soft limit at +0.5° (Az, 0.3°/s) | `EVT limit` and stop at +0.439°; a further SPEED toward the limit refused ✓ |
| `move_by` Alt ±5°, Az ±3° (0.3°/s), ×2 | All errors within ±0.025° (encoder frame) ✓ |
| Net after everything | Az −0.011°, Alt +0.002° (encoder) |

Low-speed sweep (`data/lowspeed_*.txt`), 0.5×–32× sidereal, both signs, both axes:
- 28 measurements, **measured/commanded ratio 0.982–1.016**, no consistent bias.
- That's at the resolution of the measurement: positions update every 100 ms per axis,
  so a 6 s window carries ~±1.7% timing uncertainty.
- Conclusion: the boards' speed regulation is accurate to at least ~±1.5% from 0.5× to
  60× sidereal. Tracking should still close the loop on encoder position.
- Firmware improvement for finer measurement: timestamp each axis's position update in
  the POS reply.

All encoder figures are motor-side; backlash and slip are not visible to them.

## 2026-11-24: mount found unpowered; firmware v2 adds link-health checks
- A two-axis test was refused at boot with "not calibrated". `CAL` then failed with
  "noack az": the motor boards weren't answering. The mount was apparently off, or its
  battery was exhausted after the long session (to confirm with the user).
- Gap found: firmware v1's `POS` kept returning stale positions with no sign that the
  boards had gone silent, and its boot flag conflated "uncalibrated" with "not
  answering".
- **firmware/mount_controller v2:**
  - Tracks per-axis link health: 3 unacknowledged polls in a row mean the link is down.
  - Stops both axes and emits `EVT link <axis> down` if this happens while moving.
  - Refuses SPEED while a board is down.
  - Reports `linkaz/linkalt` in POS.
  - Boots with `ledi_az/ledi_alt/linkaz/linkalt`.
  - Verified with the mount off: boot `calibrated=0 ledi_az=0 ledi_alt=0 linkaz=0
    linkalt=0`, and SPEED refused with "motor board not answering (mount off?)".
- To check at the next power-up: the LED settings should read 253 again, meaning they
  reset on a mount power cycle, so `CAL` is needed every power-up.

## 2026-11-24 ~22:04: false runaway — Az board reporting garbage (incident)
What happened:
- The mount was on battery (user measured 10.5 V earlier → **10.0 V** during this
  testing; the user measured at some point during the window).
- The boards had stopped answering (previous section).
- A monitor (firmware v2, commanded speed 0 on both axes) saw the **Az board come back
  answering at 22:04:18 already reporting full drive**: PWM 252, position falling
  ~7,800 counts/s. The link dropped out every few seconds. The total by 22:05 was
  −368,361 counts ("≈119°").
- **The user saw nothing move.** The deltas were all exact multiples of 1,949 counts
  per poll (−3,898, −5,847, …), i.e. a stuck bit pattern. **These were garbage
  replies, not motion.**
- The most likely cause is a low-battery brownout leaving the Az board's
  microcontroller in a bad state. Probing the HBX pins while measuring may have
  contributed; not confirmed.
- A host STOP was sent. The board then went silent; the user power-cycled the mount.
  After the power cycle both boards answered cleanly, and **GetLEDI read 253 again:
  confirmed, the LED calibration resets on every mount power-up.**

Firmware gaps exposed:
1. Limits, watchdog and link checks only acted on axes the firmware had *commanded*.
   An axis moving (or claiming to move) at commanded speed 0 was never stopped. Had
   this been a real runaway, the cables would have been at risk.
2. Implausible status values were accepted into the position.

Fixes (firmware/mount_controller, same day):
- **Uncommanded-motion guard:** at commanded speed 0, PWM > 0 or |delta| > 20 per poll
  makes the firmware send Stop on every poll and report `EVT uncommanded`. A board
  whose link comes back up is sent Stop first (`EVT link <axis> up`).
- **Plausibility check:** |delta| > 1,500 per poll (an axis can't exceed ~1,200 per
  100 ms poll even at full slew) is treated as a bus error (`EVT garbage`). It's not
  added to the position and counts toward link-down. That would have rejected every
  delta in this incident.
- Verified after upload: clean boot (`ledi 253/253`, both links up), idle status
  clean, no events.

Rules adopted:
- Don't probe the HBX pins while the mount is powered.
- Don't run motion tests on a sagging battery; use fresh cells or the 12 V adapter.

## 2026-11-24 ~22:10–22:15: the board fault recurs; guard tuning; resync attempt
- **Health check after the mount power cycle** (22:10, battery): CAL → 189/169.
  - Az ±0.5° and Alt ±1° `move_by`: errors ≤ 0.03°.
  - Simultaneous two-axis moves: +0.61°/+0.64° vs 0.6° commanded, back to within
    0.03°.
  - No link drops, no garbage. **Motion is healthy.**
  - But the new uncommanded-motion guard **fired 6 false alarms** (deltas 30–95, PWM 0):
    normal coast just after commanded stops and CAL.
  - Fix: a 1.5 s grace after any stop or CAL, and delta-based detection needs 2
    consecutive polls (PWM > 0 still triggers once the grace has passed).
  - Not yet re-verified on hardware, because of the next point.
- **The fault recurred on the next Uno reset** (the firmware upload at ~22:11): Alt
  silent, Az sending the same garbage (delta −1949, PWM 252) ~2 s after boot.
  - The plausibility check rejected all of it: the position stayed 0, the link stayed
    down, motion was refused. ✓
- **Pattern:** both occurrences were at the first Uno reset after a session of
  continuous polling. Resets with the idle `hbx_probe` sketch never caused it.
  - Hypothesis: a reset landing mid-transaction leaves a board's bit receiver out of
    step until power-cycled. Low battery (10.0 V) may contribute.
- **Mitigation tried:** EQMOD-ETX's resync (clock high 500 ms, low 500 ms, high 250 ms)
  at boot and as `RESYNC`.
  - The boot resync did not clear the fault.
  - The `RESYNC` command reported links still down, **then ~1–1.5 s later both boards
    came back clean** (link up, PWM 0).
  - **Inconclusive:** pending whether the user power-cycled the mount at that moment.
- Open: the cause of the board fault; whether resync clears it; whether a clean
  shutdown of polling before a reset (or not resetting the Uno at all: a host that
  keeps the port open) avoids it.

## 2026-11-24 ~22:15–22:30: reset-wedge experiments and auto-recovery
User confirmed **no mount power cycle** happened at ~22:14, so the boards recovered
without one.

Experiment 1 (`data/reset_experiment_*`; read-only, 6 Uno resets while the firmware
polled):
- **5/6 resets left both boards out of bus sync:** links down, Az sending the −1949 /
  PWM 252 garbage, Alt silent.
- **A RESYNC command a few seconds after boot recovered them every time** (0.4–0.9 s
  after it).
- The boards **kept their LED calibration** (Alt still read 169 in trial 2). Their
  microcontrollers aren't rebooting; they lose bit sync.

Experiment 2 (`reset_experiment2_*`): the resync at boot, plus 600 ms of settle time
and Stop retries spaced 20 ms apart → still 5/6 wedged. **Why the boot resync fails
while a later one works is unexplained.**

Experiment 3 (`reset_experiment3_*`): firmware **auto-recovery** (resync when a link
has been down ≥2 s while stopped, at most every 5 s) → **6/6 healthy 4.7–5.3 s after
reset**, one auto-resync each.

Final motion health check (`data/health_*`, after `wait_healthy()` + CAL):
- Az ±0.5°, Alt ±1°: errors ≤ 0.02°.
- Two-axis simultaneous: +0.63/+0.62° out, back to within 0.02°.
- **No uncommanded-motion false alarms** (guard tuning verified), no garbage during
  motion.

Known gaps carried to the next session:
1. **Root cause of the wedge** is unknown: a reset mid-transaction is suspected, but
   the boot resync doesn't clear it.
2. After auto-recovery the firmware still reports `cal=0`: the resync's LED re-read
   ran before the links were back. Fix: re-read the LED setting on each link-up
   transition.
3. Counts lost while a link is down are not recovered, so the position is invalid after
   a wedge. Absolute reference (camera, accelerometer) will fix this; for now, re-zero.

## 2026-11-25 morning: wedge root cause found — the boot resync
Setup: mount on the **12 V adapter** (no battery). Read-only experiments: each builds
`mount_controller` with a compile-time variant, uploads it, then does N port re-opens
(Uno resets while the firmware polls). Logs: `data/wedge_*.txt`.

| Boot variant | Healthy at boot | Notes |
|---|---|---|
| Boot resync (last night's default) | **0/6** | rules out low battery (hypothesis 4) |
| 5 s quiet, then resync | 0/6 | rules out "boards need quiet time" (1, 2) |
| 200 clock bits drained, then resync | 0/6 | 2 trials then self-recovered in <1 s |
| Resync; 100 ms pause after boot Stop | 0/6 | rules out Stop→query timing (6) |
| **No boot resync** | **5/6**, then **11/12** | zero garbage |

- **Root cause of the repeated wedges: the boot resync itself** (EQMOD-ETX's long
  clock pulse: high 500 ms, low 500 ms, high 250 ms). It re-synchronizes a board that
  is already wedged, but on a freshly started board it *causes* the wedge. Why it acts
  differently in the two states is unknown. The resync was added last night as a fix
  (~22:13); every boot after that wedged.
- **Ordinary resets** (port re-open while polling) without the boot resync: **16/16
  healthy**. The "reset mid-transaction" hypothesis is not supported.
- **The reset during a firmware upload** wedged the boards 2/2 times (the first trial
  after each flash). The upload keeps the Uno in its bootloader longer. Auto-recovery
  cleared both (4.3 s, 6.9 s).
- Last night's original wedges came before the boot resync: the 22:03 one during the
  low-battery brownout, the 22:11 one right after an upload. Both are consistent with
  the above.
- **Firmware changes:** `BOOT_RESYNC` now defaults to 0. Resync stays available as
  auto-recovery and the `RESYNC` command, where it works. The LED calibration is
  re-read on every link-up transition, so `cal=` is correct after a recovery. The
  experiment knobs that made no difference were removed.
- **Health check on the fixed firmware** (`data/health_20261125_*`):
  - healthy 0.3 s after boot with no resync;
  - CAL → 189/169, `cal=1`;
  - Az ±0.5° and Alt ±1° moves within 0.011°;
  - two-axis +0.61/+0.60° out and back to within 0.005°;
  - no events.

## 2026-11-25 evening: camera Pi USB link, boot time, 3B+ SD card
Setup: the camera Pi (Pi Zero W, Trixie) powered and connected by one micro-USB data
cable from its "USB" port to the Mac; Wi-Fi up as well. Config:
`hardware/camera_pi/` (deployed with `scripts/deploy_camera_pi.sh`). Throughput and
reboot tests were ad-hoc scripts (not kept); the numbers are their printed output.

**Throughput, Zero → Mac** (10–30 MiB of random data, 2–3 runs each, timed without
the SSH login):

| Link | Plain TCP | SSH | Round trip |
|---|---|---|---|
| Wi-Fi | 0.8–2.6 MB/s (erratic) | 0.47–0.59 MB/s | — |
| USB gadget (CDC-ECM) | **14.1–14.4 MB/s**, Zero CPU 100% | 4.5–4.6 MB/s | 0.8 ms |

- Over USB the Zero's CPU is the limit. That makes ~70 ms of CPU per MB sent
  unencrypted, so a 630 KB analysis frame costs ~45 ms to ship, against ~170 ms for
  `find_star` on the Zero. This is an estimate from throughput, not a measurement
  with the camera server.
- The Zero ran from the Mac's port with no undervoltage (`throttled=0x0`).

**Stock `rpi-usb-gadget` (preinstalled, v1.0.6), first try:**
- On the first boot after enabling it (also the first boot powered from the data
  port), the link was one-way. The Zero received 1 packet while the Mac sent 124. The
  Mac never got a DHCP lease and self-assigned 169.254.x.
- Reloading `g_ether` fixed it. Unloading logged `dwc2_hsotg_ep_stop_xfr: timeout
  GINTSTS.GOUTNAKEFF`, i.e. the OUT (host → Zero) endpoint was stuck.
- The next stock boot worked.
- Its "shared" mode turns on IP forwarding and NAT, and it picks random MACs each
  boot. Both were replaced: `provision.sh` turns the stock setup off and installs our
  own (static 10.12.194.1/28 on `usb0`, dnsmasq gives the host exactly 10.12.194.2
  with no router and no DNS, fixed MACs `02:72:62:00:00:01/02`).
- `g_ether` always fails once at ~29 s ("couldn't find an available UDC"), then binds
  when dwc2 appears at ~44–56 s. This is harmless.

**Boot time (single boots, `systemd-analyze`):**

| Change | Boot |
|---|---|
| As provisioned | 3 min 4 s (NetworkManager.service 77 s) |
| Wi-Fi moved from netplan to a NetworkManager keyfile | 2 min 8 s (NetworkManager 22 s) |
| + swap to zram only, cloud-init disabled | **1 min 16 s** |

- **netplan:** NetworkManager rewrote `/etc/netplan/90-NM-*.yaml` on every boot. Each
  write ran `netplan generate` and a systemd daemon-reload of ~13 s on the Zero, 4
  times, while NetworkManager started.
- **Swap:** `rpi-resize-swap-file` re-ran `mkswap` on a 426 MiB `/var/swap` every
  boot (~21 s).
- **cloud-init:** ~45 s per boot across its stages, after the first.
- **apt-daily was not a cause.** It already runs at most once a day (stamp files),
  and on a boot where it didn't run at all the boot still took 3 min 4 s.

**Our gadget config, reboot tests** (reboot over SSH, then poll until both links
answer):
- **First version: 0/5 boots.** `usb0` stayed down because NetworkManager marks every
  gadget interface unmanaged (`/usr/lib/udev/rules.d/85-nm-unmanaged.rules`:
  `ENV{DEVTYPE}=="gadget", ENV{NM_UNMANAGED}="1"`). The stock tool works around this
  at runtime with `nmcli device set usb0 managed yes`.
- **Fix:** `rootfs/etc/udev/rules.d/86-roboscope-usb0-managed.rules` sets
  `NM_UNMANAGED=0` for `usb0` only.
- **With the fix: 21/21 boots** (5 + 15 + 1 with a never-seen host MAC, which makes
  macOS see a new device).
  - Boot took 1 min 15.0–15.9 s.
  - USB SSH worked 87–113 s after the reboot command, and Wi-Fi SSH at 102–113 s.
    USB was usually ~15 s earlier.
  - The host got 10.12.194.2 every time, and `ip_forward=0` every time.
- **Recovery:** after restarting NetworkManager or the DHCP service, the link was
  back in ~3 s. After reloading `g_ether` it was back in ~6 s.

**Cold power-up (2026-11-26, our config):** the Zero was unplugged and replugged into
the Mac's port. That's 1 of 1 boots OK:
- Times from when monitoring started, shortly after the replug:
  - the Mac's interface appeared at ~29 s;
  - USB SSH and Wi-Fi SSH both worked at ~71 s.
- Boot took 1 min 16.3 s.
- The host got 10.12.194.2, and `ip_forward=0`.
- No dwc2 `GOUTNAKEFF` timeout appeared in dmesg.

**Open:**
- The one-way first boot hasn't recurred in 23 boots. Its cause is unknown.
  - The cold power-up (above) didn't reproduce it.
  - So the remaining unrepeated condition is the stock `rpi-usb-gadget` config.
  - One cold boot is a small sample.
- If it recurs, the signature is Zero `usb0` rx_packets stuck while the host's
  packets-out count rises. Reloading `g_ether` cleared it.
- A Pi (the 3B+) as the host: the link works (2026-11-26 entry below).

**3B+ SD card** (32 GB, named "test"; f3 v10 `f3write` + `f3read` on the Mac):
- 29.61 GB written and verified, 0 sectors lost, corrupted, changed or overwritten.
  The capacity is real.
- Reads ran at 44 MB/s, steady.
- Writes averaged 4.6 MB/s, with a minimum of 0.26 MB/s, over 1 h 49 min. The card
  is sound but slow to write.

**History of the "test" card:** it is the card from the original Pi that went
"read-only" (configuration changes and uploaded files didn't survive between
sessions). It passed f3 above, so it accepts and keeps writes in the Mac. That cause was
never found. Candidates: a read-only overlay (the old OS is gone, so this can't be
checked), or low voltage. The 3B+ entry below found low voltage on every charger tried.

## 2026-11-26: host Pi (3B+) bring-up and its power supply

**Second SD card, for comparison** (31 GB, Raspberry Pi OS Buster from 2019 with a
7.7 GB root partition and 23 GB unpartitioned): a 150 MiB file in the boot partition's
free space, written and read with the Mac's cache bypassed (`F_NOCACHE`), then deleted.
- Sequential write 12.6 MB/s, read 30.9 MB/s.
- 4 KiB random: 374 reads per second; 223 writes per second, each flushed.
- A short test can flatter writes (cards have fast buffers), so 12.6 MB/s is a best
  case. Not used: the 3B+ runs from the "test" card.
- Its boot config has no read-only overlay.

**3B+ setup:** Raspberry Pi OS Lite 64-bit, Trixie, kernel 6.18.50+rpt-rpi-v8,
hostname `roboscope` (192.168.0.197). Python 3.13.5. 905 MiB RAM.
- First boot took 4 min 12 s, mostly cloud-init's one-time setup; later boots took
  1 min 5 s.
- The root filesystem grew to 29 GB on first boot. No SD or filesystem errors in the
  kernel log. Changes made on first boot (hostname, user, resize, cloud-init done) have
  survived about 5 unclean power cuts.
- Attached by USB: the Uno (`/dev/ttyACM0`, `max` already in `dialout`) and the camera
  Zero.

**Zero ↔ 3B+ USB link:** it worked without setup on the first boot. The 3B+ got
10.12.194.2 from the Zero's DHCP, with a round trip of 0.42 ms and no loss (5 pings).
On later boots, the 3B+'s `usb0` was left with no address. It boots faster than the
Zero, so NetworkManager likely gave up on DHCP before the Zero's dnsmasq was up (not
confirmed from logs). The host-Pi setup now gives it a static 10.12.194.2
(`hardware/host_pi`).

**The one-way failure recurred** (first seen 2026-11-25 on the Mac):
- **When:** after both Pis were powered up together on the bench supply (~06:40), with
  the Zero powered from the 3B+'s USB port.
- **What happened:**
  - The Zero enumerated normally (no `GOUTNAKEFF` or other dwc2 error in its dmesg).
  - About 3 minutes later the 3B+'s `cdc_ether` logged
    `NETDEV WATCHDOG: transmit queue 0 timed out` (06:43, 06:50, 06:52).
  - Counters at 07:06: the 3B+ had received 30 packets with no errors, and sent 3,
    with 180 transmit errors and 13 dropped. The Zero had received 1 and sent 30.
    So host → Zero traffic was dead, and Zero → host worked.
- **A host-side reset doesn't fix it.** De-authorizing and re-authorizing the device on
  the 3B+ (`/sys/bus/usb/devices/1-1.1.3/authorized`) failed:
  `can't set config #1, error -110`. The gadget didn't answer, so the stuck end is the
  Zero's.
- **A Zero-side reset does.** `modprobe -r g_ether; modprobe g_ether` on the Zero (over
  Wi-Fi) brought it back in ~10 s. Ping 5/5 at 0.48 ms; SSH from the 3B+ works, and
  the static address came back by itself.
- **Pattern so far:** both failures came at a cold power-up with the Zero powered
  through its data port from the host; 21 warm reboots all worked, and so did 1 Mac
  cold power-up. The 3B+ was also short of voltage at the time. Not yet known: whether
  low voltage, the ordering of power-up, or a dwc2 bug causes it.
- **Not a fix, but a mitigation to consider:** a watchdog on the Zero that reloads
  `g_ether` when `usb0`'s receive count stays flat while the link is up.

**roboscope on the 3B+:** `uv sync --dev` installed 46 packages from aarch64 wheels in
55 s (no compiling). `uv run pytest`: 102 of 103 passed in 5 min 10 s, against 26 s on
the Mac.
- The one failure was a timing assumption in `test_idle_mode_switches_and_wakes`,
  fixed in the test.
- During the run, `get_throttled` showed no under-voltage (159 readings, ~2 s apart):
  normal workloads stay above the threshold, unlike 4-core busy loops.

**Power.** `vcgencmd get_throttled` was `0x50005` (under-voltage now, and throttled):
- The firmware sets the under-voltage bit when the supply falls below about 4.63 V
  *(threshold as commonly cited; not verified against Raspberry Pi documentation)*.
  It only says below or above, not by how much.
- While it is set, the CPU is held at 600 MHz instead of 1.4 GHz.
- The kernel logs "Undervoltage detected" / "Voltage normalised".

Measurements:
- **Idle and load:** idle is the Pi doing nothing. Load is 4 shell busy-loops for
  30–120 s; they touch little memory and no radio, so this understates a real workload.
- **"% low":** the share of `get_throttled` readings with the under-voltage bit set,
  sampled every 3 s (rows 1–5) or at 5 Hz.
- **Pin 2:** the multimeter on GPIO pin 2 (5 V) against a port shell, which reads an
  average.

| # | Supply | Attached | Idle % low | Load % low | Pin 2 |
|---|---|---|---|---|---|
| 1 | First supply | Zero, Uno, keyboard receiver | 100 (10/10) | – | – |
| 2 | Second charger + cable | Zero, Uno, receiver | 70 (7/10) | – | – |
| 3 | Second charger | Zero, Uno | 20 (4/20) | – | – |
| 4 | Second charger | Zero | 15 (3/20) | – | – |
| 5 | Second charger | nothing | 5 (1/20) | 100 (14/14) | – |
| 6 | Second charger | Zero, Uno | 22 | 99 | – |
| 7 | Third charger | Zero, Uno | 38 | 96 | – |
| 8 | Bench supply, 5.10 V, 2.5 A limit | Zero, Uno | 30 | 97 | – |
| 9 | Bench supply, 5.25 V | Zero, Uno | 0 | 94; 89 over 2 min | ~5.0 V idle, ~4.9 V load |
| 10 | Original brick | Zero, Uno | – | 96 | ~4.7 V (taken during the run, probably the load phase) |

- In row 5 (the Pi alone, under load), the CPU sat at 600 MHz at 48–53 °C. The
  slowdown came from voltage, not heat.
- Row 10's idle phase got only 2 samples, because the clock stepped mid-run.
- The charger in rows 2–6 was later found to output far too low a voltage (seen on
  the user's meter).
- The Pi rebooted uncleanly around 06:10, between rows 5 and 6. Most likely it browned out
  as the Zero and Uno were plugged back in, but the journal is volatile, so this is
  unconfirmed.
- During provisioning (apt, Wi-Fi downloads, CPU at 1.4 GHz with the bit clear), the
  bench supply showed **over 1 A**, more than during the busy-loop tests. Those had held
  the CPU at 600 MHz, which caps its draw.

Reading the results:
- **The flag overstates the time spent low.** At 5.25 V the meter averaged ~4.9 V
  under load, above 4.63 V, yet the flag was set ~90% of the time. The likely cause:
  short dips while the CPU draws current in bursts, with the bit held between dips.
  The holding is inferred, not documented. Confirming the dips' depth needs a meter's
  MIN/MAX mode or a scope.
- **The flag saturates under load for every supply.** It can't rank supplies; the
  meter can.
- **The Zero and the Uno are minor loads compared with the supply path.** Removing
  them changed idle dips from 4/20 to 1/20, but the Pi alone still failed under load.

Upshots (user's, 2026-11-26):
- There is a meaningful voltage drop from the supply to the GPIO pins: at least
  0.25 V (5.25 V set, ~5.0 V at pin 2 at idle, ~4.9 V under load).
- Wall power bricks are likely too low in voltage for this.
- The wires in micro-USB cables are very thin.
- Look into powering the Uno and the Zero through some kind of powered USB hub.

Decisions for now:
- Run the 3B+ from the bench supply at 5.25 V and accept the residual risk: SD writes
  during dips, and the CPU slowed under load. A better power solution is being worked
  out in parallel.
- The Uno stays on the Pi's USB power for now. Watch `get_throttled` and the kernel's
  under-voltage messages during Uno and mount operations.

**USB link watchdog (camera Pi, `roboscope-usb-watchdog.service`):**
- **How it works:** every 10 s, while a host has the gadget configured, the Zero pings
  10.12.194.2 over `usb0`. After 3 misses it reloads `g_ether`.
- **Backoff:** the wait after a reload is 60 s, doubling up to 30 min while the link
  stays dead, and resetting on any reply.
- **Tests:**
  1. Healthy link for ~4 min: no action.
  2. The 3B+'s `usb0` taken down: detected after 35 s, and the 3B+ could reach the
     Zero again 45 s after the failure began.
  3. The 3B+'s end left unconfigured for 5 min: reloads at 07:30:35, 07:32:13 and
     07:34:50, with waits of 60, 120 and 240 s. So it backs off rather than looping.

**Cold power-ups with the watchdog** (3B+ USB log times; the Zero's own clock is wrong
until NTP):

| Power-up | Link at first | Watchdog | 3B+ → Zero SSH works |
|---|---|---|---|
| Zero replug 1 (enumerated 08:19:23) | one-way | reload 08:20:24 | 08:20:31 |
| Zero replug 2 (08:23:50) | one-way | reload 08:24:51 | 08:25:03 |
| Zero replug 3 (08:26:16) | one-way | reload 08:27:17 | 08:27:27 |
| Zero replug 4 | one-way | reload | 08:30:32 |
| Full power cycle 1, mount off, Uno unplugged | one-way | reload | 09:19:44 |
| Full power cycle 2, same | **worked** | none | 09:24:07 |

- **The signature is identical every time:** the Zero's `usb0` has rx=1 (tx 25–39),
  then nothing more arrives. The reload fixed every case, giving a working link
  ~60–70 s after enumeration.
- **Score so far:** 7 of 9 cold power-ups came up one-way (counting the first Mac
  boot, the first 3B+ power-up, these 6, and 1 clean Mac cold power-up). Warm
  reboots: 0 of 21.
- **Not caused by low voltage:** full power cycle 1 failed with `throttled=0x0` (no
  under-voltage since boot).
- Replugging the Zero does cause a 3B+ under-voltage event within a few seconds,
  likely the Zero's inrush current.
- Hypothesis at this point: the early `modules-load` load of `g_ether` (before the
  controller exists) was the cause. It was tested below and rejected as the sole cause.

**3B+ after full provisioning** (persistent journal, Bluetooth off, netplan and
cloud-init gone):
- Boot takes **18.3 s** (5.6 s kernel + 12.6 s userspace), down from 1 min 5 s.
- The persistence marker written at 06:41:55 survived both full power cuts.
- `get_throttled` read `0x0` after both boots. The mount was off and the Uno unplugged
  for these, so the Pi's load was lower.


## 2026-11-26 late morning: one-way USB link root cause and fix

**Test method:** each cycle is a real cold power-up of the Zero, made without touching
cables:
- `uhubctl -l 1-1 -p 2 -a off`, 8 s, then `on`. On the 3B+ this switches power to all
  its USB ports together; port-level switching on hub 1-1.1 did *not* cut power (the
  Zero kept its boot).
- So the Uno must be unplugged during these tests; the mount was off.
- **Pass:** the 3B+ can ssh to the Zero over USB (10.12.194.1).
- **Fail:** the Zero's watchdog had to reload `g_ether`.
- `uhubctl` is installed on the 3B+ by `hardware/host_pi/provision.sh`.

**Results** ("gap" is from the gadget binding, when the host enumerates it and can
send, to when the Zero opens `usb0`):

| Zero setup | Gap | Cold power-ups failed |
|---|---|---|
| `g_ether` from modules-load.d (binds ~42 s); NetworkManager opens `usb0` | ~23 s | 3 of 5 (10 of 14 with the earlier manual power-ups, 7 of 9) |
| `g_ether` loaded by udev once the controller exists (binds ~50 s); NetworkManager opens `usb0` | ~13 s | 4 of 8 |
| modules-load.d + udev rule that opens `usb0` when it appears | ~12 s (udev busy at boot) | 5 of 10 |
| udev load + the same open-on-appear rule | ~5 s | 0 of 11 |
| **udev rule that loads `g_ether` and opens `usb0` in one command (kept)** | **0.8–1.2 s** | **0 of 12** |

- With the fix, every cold power-up had a working link 91–96 s after power-on, against
  ~120 s when the watchdog had to step in.
- Warm reboots with the fix: 5 of 5 clean (no watchdog action), at 102–104 s after
  the reboot command.
- No 3B+ under-voltage events during any of these runs.

**Conclusion:**
- **What matters:** how soon the Zero opens `usb0` after the host enumerates the gadget.
  With `usb0` open within ~5 s: 0 of 23 failed. Opened 12–23 s later: 19 of 32 failed
  (all configurations, including the manual power-ups; the first Mac boot was on the
  stock `rpi-usb-gadget` setup).
- **Earlier load alone doesn't matter:** loading `g_ether` before the controller exists
  is fine on its own.
- **Why warm reboots never failed:** timing alone may explain it, since boot ordering
  differs on a warm reboot, but this wasn't checked.
- **Likely mechanism (not proven):**
  - Linux's `usbnet` host driver times out its transmit queue after 5 s (seen on the
    3B+ as `NETDEV WATCHDOG: transmit queue 0 timed out`) and cancels its pending sends.
  - Cancelling an OUT transfer that the Zero is still NAKing, because no receive buffer
    is queued while `usb0` is down, can leave dwc2's OUT endpoint stuck. The Zero then
    receives 1 frame and nothing more.
  - The `GOUTNAKEFF` / `EPDisable` timeouts appear only when `g_ether` is unloaded from
    the stuck endpoint.
  - The Mac failure (2026-11-25) presumably had the same cause.

**Kept in `hardware/camera_pi/rootfs`:**
- `etc/udev/rules.d/87-roboscope-gadget-load.rules`: when UDC `20980000.usb` appears,
  `modprobe g_ether && ip link set usb0 up`, logged as `roboscope-gadget`.
- `88-roboscope-usb0-up-early.rules`: opens `usb0` whenever it appears, for other load
  paths such as the watchdog's reload.
- The modules-load.d file is removed. The watchdog stays as a safety net.

**Not yet tested:** the fix with the Mac as host (cold plug), and with the Uno attached
to the 3B+.

## 2026-11-26 afternoon: camera over the USB link

**Approach:** keep the camera server on the Zero's loopback and reach it through an SSH
tunnel over the USB link (`connect(host="10.12.194.1")`). This needed no server change
and exposes nothing new on either network.

**Measured from the 3B+** (`CameraClient`, binned frames, no optics so `found=False`):

| | USB (SSH tunnel) | Wi-Fi (SSH tunnel) |
|---|---|---|
| Tunnel setup | 0.6–1.0 s | 1.8 s |
| `status` | 53–163 ms | 100–218 ms |
| `locate` | 0.28–0.35 s (median 0.30 s) | 0.48–0.65 s |
| Raw frame (0.63 MB) | 0.31–0.41 s (median 0.40 s, ~1.6 MB/s) | 4.1–22 s (0.2 MB/s at best) |

- Server at 10 fps throughout. No 3B+ under-voltage (`0x0`).
- SSH over USB is limited by the Zero's CPU, which is also running capture; plain TCP
  reached 14 MB/s on 2026-11-25.
- **Route choice:** `client.pick_host()` tries a 0.5 s TCP connect to 10.12.194.1:22
  and falls back to `roboscopeCam.local`. `connect()` and `camera_view.py` use it when
  no host is given.
  - The 3B+ picked USB.
  - The Mac, with the Zero not plugged in, picked Wi-Fi after the 0.5 s probe.
- **Waking from idle:** frames during the first few seconds after the server leaves
  idle mode take 0.7–1.0 s, while capture speeds back up from 1 fps.

**First request after a gap took ~7 s; fixed with linger:**
- **Symptom:** the first `status` through a new tunnel took 6.8 s when no SSH session
  had been open for a while. A second tunnel straight after took 0.44 s.
- **Not the server:** on the Zero itself, `status` after idle took 0.1–0.6 s.
- **Cause:** with systemd linger off, the Zero stops `max`'s user manager
  (`user@1000.service`) after the last session closes and starts it again at the next
  login: 3.1 s and >4 s of CPU. An SSH login after a 60 s gap took 5.2 s; the next one
  took 1.3 s.
- **Fix:** `loginctl enable-linger max` (camera Pi `provision.sh`). After a 60 s gap,
  logins take 1.05 s both times, and the tunnel's first `status` 0.38 s.
