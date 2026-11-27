# StarNavigator NG: how fast can we safely drive it?

Research notes (2026-11-27) on the fastest safe speed for the NG 102 mount driven by our
Uno controller (`firmware/mount_controller`), combining published figures with our own
measurements. The test results that set the current cap are in
[hardware_findings.md](hardware_findings.md) and summarized under "Test results" below.

## Summary
- The motors can do about **4.5–5.5°/s at full power**, depending on battery [1][2].
  That is the handbox's open-loop speed 9, not a regulated speed.
- The safe limit for us is set by our firmware and by stopping distance, not by the
  gears. Plan (2026-11-27): step the regulated speed up to at most **12,000 counts/s**
  (Az 3.9°/s, Alt 2.8°/s) and keep a cap ~15% below the first step that fails to hold
  speed. **Outcome:** no step failed. Regulation held within 0.4% at 12,000 counts/s on
  both axes and directions, and run-on after a stop was ≤ 0.6°. So 12,000 is the
  firmware-imposed ceiling (point 2 below), not a motor limit. **The user chose
  12,000 as the cap (2026-11-27)**, with a standing request to watch for problems at
  these speeds (see "Watch list").

## Findings

| Question | Answer | Source |
|---|---|---|
| Meade's rated top speed for the NG 102 | "1x sidereal to 5.5°/sec in 9 increments". Speed 9 = "Max (maximum speed possible, dependent on battery power)". Speed 8 = 1.5°/s. | [1] p.18, p.43 |
| Speed 9 in Meade's speed table, as used by EQMOD-ETX | `ETXSlew9 1080 // ... 4.5°/sec`. (Its speed 8 is 2.5°/s, not the NG manual's 1.5.) | [2] |
| What our mount did at full open-loop power | 3,000–4,000 counts per 250 ms pulse = 12,000–16,000 counts/s; ~3.9–5.2°/s in Az, and the user saw ~1° per 250 ms (Az). Alt's open-loop maximum was not measured. | [4], [5] |
| Regulated speed accuracy measured | Within 1% at every step up to 6,500 counts/s, both axes and directions (2026-11-27). | [4] |
| Published gear or motor-board speed limits | None found. Meade's speed 9 drives at full power, so mechanically the range up to the open-loop maximum is what the handbox already does. EQMOD-ETX sends speeds without a clamp. | [1], [3] |
| How the most mature open-source driver slews | Regulated GoTo starts at 1°/s. Moves over 8° use the open-loop full-speed command (`0x06`/`0x07`), stopped within 1° of the target; the move then finishes regulated, ramping down to 128× sidereal. | [3] |

## What limits a safe speed for us
1. **Regulation headroom.** Near the open-loop maximum there's no drive margin left,
   so the axis falls behind the command. Worst case: Alt going up against tube
   imbalance (Alt already coasts asymmetrically), and a sagging battery.
2. **The firmware's garbage filter.** `MAX_DELTA` (1,500 counts per 100 ms poll) rejects
   anything faster than 15,000 counts/s as bus garbage. A slow poll (>125 ms) at
   12,000 counts/s would also exceed it, so the cap needs margin below 15,000.
   **`MAX_DELTA` must not be raised to allow more speed.** The known stuck-board
   garbage is exactly 1,949 counts per poll (2026-11-24 false runaway, and again after
   the 2026-11-27 firmware upload). A filter at ≥ 1,949 would accept it as motion.
3. **Stopping distance.** ~0.25° run-on at 6,500 counts/s (2026-11-27). Kinetic energy
   grows with speed squared, so roughly 0.4–1° is expected at 3–4°/s. EQMOD-ETX's
   "stop 1° short at full speed" rule suggests full-speed coast stays under 1°.
4. **Power.** More speed draws more current. A low battery has caused board garbage
   and false "runaways" before (hardware_findings.md, 2026-11-24), and the 3B+
   under-volts at boot.
5. **Cables and the camera on the tube.** A snag is worse at high speed. Scripted
   tests stay within 10° (user rule); steering from the page has no limits.

## Test results
Step-up test, 2026-11-27 (details in [hardware_findings.md](hardware_findings.md),
"step-up test to 12,000 counts/s"):
- Temporary firmware with `MAX_CPS` 12,000; hub stopped; both soft limits ±9.5°; each
  leg at most 5° of travel, out and back.
- Encoder rate over each steady hold was 99.9–100.4% of the command at 6,500, 8,000,
  10,000 and 12,000 counts/s on both axes and directions. No controller events.
- Run-on after STOP at 12,000 counts/s: Az ~1,780 counts (0.58°), Alt ~1,550 (0.37°).
  Motion ends ≤ 0.25 s after STOP (`scripts/mc_speed_test.py`; settle times of
  0.7–0.9 s noted earlier included a 0.5 s stillness check).
- Not tested: holds longer than 1.3 s (Az) or 1.8 s (Alt) at 12,000, and the mount on
  a low battery.

## Watch list (cap 12,000 counts/s since 2026-11-27)
The user asked to keep an eye on high-speed issues. The test only held 12,000 for
1.3 s (Az) and 1.8 s (Alt) and on good power. Look out for, and record in
hardware_findings.md:
- `EVT garbage` or `EVT link ... down` while moving fast. A slow poll at 12,000
  counts/s can exceed `MAX_DELTA`; discarded deltas make the position under-count.
- Speed sagging below the command in long holds, especially Alt going up, or on a
  low battery (compare `/api/mount` degrees over time with the commanded rate).
- Run-on past ~0.6° (Az) / ~0.4° (Alt) after release, or overshoot near soft limits.
- Brownouts: `uncommanded` events, garbage after fast moves, new `throttled` bits on
  the 3B+.
- Mechanical: grinding, slipping, the camera or cables snagging, the tube shifting
  in its mount.
To check, rerun `scripts/mc_speed_test.py` on the 3B+ (hub stopped) and compare with the
baseline below; `--repeat N` exercises sustained use. Baseline (2026-11-27, wall power):
every leg 200–12,000 counts/s within 1.3% with 4 s holds at the slow end; run-on at
12,000 ~0.59° Az, ~0.51° Alt.
If any of these show up, lower `MAX_CPS` (firmware and `roboscope.mount.jog.MAX_CPS`
together); 10,000 was the runner-up choice.

## Open questions
- Alt's open-loop maximum was never measured on its own.
- Where regulation actually gives out is still unknown. It is above 12,000 counts/s,
  and the firmware can't safely go much further (point 2).
- Supply current at speed is unknown.
- Only the EQG2HBXWiFi variant of EQMOD-ETX was read (ETXProtocol.h/.ino,
  EQGProtocol.h/.ino, HBXComms.ino). The E32 and Mega variants were not checked for
  limits.

## References
1. Meade StarNavigator NG instruction manual, local copy
   `../StarNavigator-NG_InstructionManualV001a.pdf`: "Slew Speeds" (p.18) and
   "Specifications" (p.43).
2. EQMOD-ETX, `Software/EQG2HBXWiFi/ETXProtocol.h`, lines 64–72 (speed table):
   https://github.com/ozarchie/EQMOD-ETX/blob/master/Software/EQG2HBXWiFi/ETXProtocol.h
3. EQMOD-ETX, `Software/EQG2HBXWiFi/ETXProtocol.ino`: GoTo and slew logic (lines
   ~44–115), the slow-down to 128× sidereal (~185–260):
   https://github.com/ozarchie/EQMOD-ETX/blob/master/Software/EQG2HBXWiFi/ETXProtocol.ino
4. [hardware_findings.md](hardware_findings.md): 2026-11-24 regulated speed control,
   2026-11-26 evening speed cap, 2026-11-27 speed and coast test.
5. [telescope_interface.md](telescope_interface.md) §5: full-speed pulse counts.
