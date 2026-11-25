# Wiring: Arduino Uno ↔ StarNavigator NG HBX port (motor bus)

This is the single reference for how the Uno connects to the mount's motor bus. The
sketch header (`firmware/hbx_probe/hbx_probe.ino`) and
`hardware_plan_motor_controller.md` point here.

**Status (2026-11-24):**
- **Installed and verified:** the series resistors, ground, and the pin map.
- **47 kΩ pull-ups installed the same day.** With them, the Uno reset once with the mount
  on (serial port re-open). The user watched and **nothing moved**. Communication
  afterwards was unchanged: `type` → `0x0C` on both axes, clean status, ack 8–60 µs.
  A full upload with the mount on was then also verified (user watching; no motion).

## 1. HBX socket pins (base side)

Numbering is at the **base socket**. +12 V was measured between pins 1 and 8. The stock
handbox coil cord reverses the order (pin 1 ↔ 8), so never use a straight cable in place
of it. See `telescope_interface.md` §5 and `hardware_findings.md`.

| HBX pin | Signal | Notes |
|---|---|---|
| 1 | +12 V (battery) | **Never connect** |
| 2 | AUX clock | Held high only (unused bus) |
| 3, 5, 7 | Data (joined inside the base) | Use any one of them |
| 4 | Alt clock | |
| 6 | Az clock | |
| 8 | Ground | |

## 2. Connections

| HBX pin | Signal | Series R | Pull-up to Uno 5 V | Uno pin | Uno mode |
|---|---|---|---|---|---|
| 6 | Az clock | 820 Ω | 47 kΩ | D2 | output, idle HIGH |
| 4 | Alt clock | 820 Ω | 47 kΩ | D3 | output, idle HIGH |
| 2 | AUX clock | 820 Ω | 47 kΩ | D4 | output, held HIGH |
| 3 (or 5 or 7) | Data | 820 Ω | 47 kΩ | D5 | open-drain: `INPUT_PULLUP` or driven LOW |
| 8 | Ground | none | n/a | GND | direct |
| 1 | +12 V | n/a | n/a | n/a | **taped off, unconnected** |

Per signal line:

```
                     Uno 5V
                       │
                     [47k]          (holds the line high while the Uno resets)
                       │
 Uno D2 ───────────────┼────[820Ω]──────────── HBX pin 6 (Az clock)
                                                   │
                                           motor board input
```

The same arrangement applies to D3 → pin 4, D4 → pin 2, and D5 → pin 3/5/7, plus
Uno GND → HBX pin 8.

## 3. Why each part is there

- **820 Ω series resistors** (installed; 1 kΩ also fine). They limit current if the Uno
  and a motor board drive a line in opposite directions at once. They also limit the
  current into an unpowered board's input clamps while the mount is off.
- **47 kΩ pull-ups to the Uno's 5 V** (installed 2026-11-24).
  - The bus floats without a master, and a floating bus has caused unexpected motion
    (`hardware_findings.md`).
  - During an upload or reset the Uno's pins float for ~1.5–2 s, but its 5 V rail stays
    up (USB power). The pull-ups hold the clocks high and the data line released through
    that window. **Verified with the mount on for a port re-open and a full upload**
    (no motion).
  - Why 47k: EQMOD-ETX reports that 10 kΩ pull-ups were "too strong" on the ETX-60's
    joined data lines and broke communication, while 47–56 kΩ worked.
  - On the Uno side of the series resistor, the pull-up also sets the level the motor
    board sees through the 820 Ω, which is fine at this value.
- **The sketch also enables the Uno's internal pull-up** (~20–50 kΩ) on data. Together
  with the external 47k that's roughly 15–25 kΩ, still well above the 10k EQMOD-ETX
  found too strong.

## 4. Power order

Original rule (before the pull-ups):
1. **Mount off.** Upload the sketch / open the serial port. Leave the port held open
   (bridge process).
2. **Mount on** (battery preferred).
3. Off in reverse: mount off first, then release the Uno.

Keep the "Uno wired, mount off" state brief. The Uno's 5 V then feeds small currents into
the unpowered boards' input clamps.

**With the pull-ups (2026-11-24):** port re-opens and uploads with the mount on are
verified safe (no motion, user watching). The mount-off rule no longer applies. Still
use battery power, and keep someone near the power switch during motion tests.
