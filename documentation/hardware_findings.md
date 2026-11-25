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
