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
