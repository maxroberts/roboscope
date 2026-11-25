# Hardware plan: replace the AudioStar handbox with our own motor controller

**Goal:** drive the StarNavigator NG's two motor boards directly from our own
electronics. Every pointing and tracking decision then runs in roboscope, and we get
the motor-level data the handbox hides: encoder counts for both axes, motor PWM and an
encoder-error flag.

**Status (2026-11-24):** Phase 1 sniffing was skipped. An Arduino Uno connected
directly (Phase 2 wiring) showed the NG's motor boards speak the ETX/DS protocol:
handshake, `0x0B`, `0x08`, `0x07`, `0x06` and `0x05` all work, and the mount moved
under our control with no handbox. **Encoder readout works after LED calibration**
(`0x05`→`0xE4`→`0x04`, step 7). **Regulated speed control works** (`0x01` SpeedStart:
1× to 60× sidereal measured, within ~1%, symmetric). Next: counts per degree against
ground truth, `0x00` SpeedChnge for smooth rate changes, and the firmware watchdog and
limits (Phase 3) before any long-running motion. Details in hardware_findings.md. Background and sources
are in [telescope_interface.md](telescope_interface.md) §4–5.

## Why this is feasible
- The handbox is only the bus master. Each axis's motor board has its own
  microcontroller that reads the motor encoder and reports "a simple count ... sent back
  on the cables" over "a proprietary I2C type protocol" [1].
- An open project (EQMOD-ETX) already drives Meade ETX-60 motor boards from an
  ESP32/Arduino with no handbox. Its source defines the command set, e.g. `0x08`
  GetStatus → encoder ticks, PWM, error flag [2].
- **Unknown:** whether the NG's boards speak the same dialect. Phase 1 answers this
  passively, before we send a single byte.

## What we keep and what changes
- **Kept:** Meade motors, encoders, gearboxes and motor boards inside the base. Nothing
  inside the base is modified. The handbox can be plugged back in at any time.
- **Replaced:** the handbox, by a microcontroller running our firmware on the HBX port.
  It talks USB serial to the Pi that runs roboscope.

```
Pi (roboscope) ──USB──▶ 5 V Arduino (our firmware) ──1 kΩ series R──▶ HBX port ──▶ Az / Alt motor boards
```

**Controller choice: a 5 V Arduino (Uno/Nano/Mega; the user has one).**
- EQMOD-ETX's Arduino Mega build wires the HBX clock and data lines directly to Arduino
  pins, with no level shifter [2].
- Its README raises level translators only for 3.3 V boards, which needed their 10k
  pull-ups changed "to 47k or 56k" because otherwise "the pullup is too strong and the
  communications fail" [2]. A 5 V board avoids that problem.
- Condition: Phase 1 must confirm the NG's bus lines idle at 5 V.
- Fallback: a 3.3 V board such as the Pi Pico, which needs a bidirectional level shifter
  with tuned pull-ups.

## Phase 1: sniff the real handbox (passive, no risk to the mount)

1. **Identify the HBX connector and pinout.**
   - Count the HBX socket's pins and photograph it. The expected 8-pin map (ETX-60)
     runs 1 Vbat, 2/3 AUX clock/data, 4/5 Alt clock/data, 6/7 Az clock/data, 8 ground
     [2]. Full table and caveats in `telescope_interface.md` §5.
   - Pin numbering direction differs between sources (pin n ↔ 9 − n), so find the Vbat
     and ground ends by measurement and count from there.
   - **Progress (2026-11-24):** +12 V confirmed between pins 1 and 8; the data pins
     read shorted together (shared data line). Remaining: which pins are joined, clock
     pins isolated, idle voltages. If the data line is shared, the Arduino needs one
     data pin and three clock pins, and the analyzer needs 4 channels plus ground.
   - With the base powered and the handbox *unplugged*, measure each pin to ground with
     a multimeter.
   - Expect ground, a 12 V supply pin (battery voltage), and logic lines idling high
     (probably 5 V; to be measured).
   - **Mark the 12 V pin and never connect it to anything else.**
2. **Make a tap cable.** Two breakout boards for the HBX connector type are wired
   pin-to-pin. The handbox plugs into one, a short patch cable runs from the other to
   the base, and the screw terminals give clip points for the analyzer.
3. **Record with a logic analyzer.**
   - Probe the clock lines, data line(s) and ground. Johansen taps "the 5 lines required
     plus ground" on his DS [1].
   - Capture these handbox actions, one per file:
     - power-on;
     - "Calibrate Motor";
     - each slew speed, both directions, per axis;
     - stop;
     - idle polling.
   - Confirm the logic level is safe for the analyzer inputs before connecting. Cheap
     analyzers don't state an input tolerance; if the lines are above 5 V, add a
     resistor divider on each probe.
4. **Decode** in PulseView [3] and compare with the EQMOD-ETX command table [2].
   - Deliverable: a protocol section in `telescope_interface.md` marked *verified on
     NG*, covering opcodes, byte counts, speed encoding, and the encoder count format
     and wrap.

**Go/no-go:** if the NG traffic matches (or is decodable), go to Phase 2. If not, stop
here. We still have the handbox bridge, and the option of fully custom motor drivers.

## Phase 2: our controller, read-only first

5. **Wire the Arduino to the HBX port** (full diagram: [wiring_hbx_uno.md](wiring_hbx_uno.md)):
   - clock and data lines only, each through a ~1 kΩ series resistor, plus a common
     ground;
   - **no** connection to the 12 V pin;
   - the Arduino is powered from the Pi's USB.

   The series resistors are our own precaution, not from the sources. EQMOD-ETX drives
   the data line high when sending ("HDATalk" writes HIGH, then sets the pin to output)
   [2], while the motor board pulls data low to acknowledge. If both ever drive at once,
   the resistor limits the current. Data is read with the internal pull-up enabled
   (`INPUT_PULLUP`), as in EQMOD-ETX [2].

   **The pull-ups are our job.** Measurements suggest the bus lines float when no master
   is attached (see hardware_findings.md, HBX measurements). The mount moved on its own
   on wall power with the port empty. So:
   - the Arduino keeps the clocks driven high and the data line pulled up whenever it is
     running;
   - 47 kΩ external pull-ups to the Arduino's 5 V (proposed) keep the bus idle even
     while the Arduino resets;
   - power-up order is **Arduino first, then mount power**;
   - power-down order is the reverse;
   - never run the base with the HBX port empty.
6. **Arduino firmware v0: listen and query only.** Implement the start/acknowledge handshake
   [1][2], `GetMotorType` (`0x0B`) and `GetStatus` (`0x08`). Turn each axis by hand
   (clutch released) and watch the counts change.
7. **Encoder LED calibration.** The handbox stores per-axis "Calibrate" values that set
   encoder LED brightness [1]. Read ours off the bus during the Phase 1 captures, then
   replicate them with `SetLEDI`/`CalibrateLED` (`0x03`/`0x04`) [2].

## Phase 3: our controller drives the motors

8. **Arduino firmware v1:** speed commands (`SpeedStart`/`SpeedChnge`/`Stop`), with safety built
   into the Arduino itself:
   - **Watchdog:** stop both motors if the Pi sends nothing for ~0.5 s.
   - **Soft limits** on cumulative counts. Default azimuth limit is ±45° from start (the
     cable rule) until the user lifts it.
   - A Stop command that pre-empts everything.
9. **Host protocol** (Arduino ↔ Pi, our own, simple ASCII lines). For example: set axis
   speed in counts/s; stop; read counts, PWM and error per axis; set limits.
   `roboscope.mount` gets a new driver class behind the same interface as the handbox
   bridge, plus a simulator for tests.
10. **Characterize against ground truth** (camera and accelerometer; see the
    alignment-camera plan):
    - counts per degree per axis;
    - backlash at each reversal;
    - periodic error per worm tooth (DS worm wheels are 60-tooth, "6deg per tooth"
      [1]);
    - slip detection, from encoder counts disagreeing with the camera.

## Shopping list

| Item | Qty | Purpose | Link | Price |
|---|---|---|---|---|
| USB logic analyzer, 8 ch, Saleae-compatible (FX2-based) | 1 | Phase 1 sniffing. Pick one listed as supported by sigrok's fx2lafw firmware (e.g. "MCU123 Saleae Logic clone") [4] | Search: https://www.amazon.com/s?k=usb+logic+analyzer+8+channel+24mhz | "about $10" [1] |
| PulseView (software) | n/a | Capture and decode; open source [3] | https://sigrok.org/wiki/PulseView | free |
| Breakout boards with screw terminals matching the HBX connector (likely RJ45/8P8C; **confirm in step 1**) | 2 | Tap cable / Arduino connection | Search: https://www.amazon.com/s?k=rj45+breakout+board+screw+terminal | check |
| Short patch cable matching the HBX connector | 2 | Tap cable to base | Search: https://www.amazon.com/s?k=rj45+patch+cable+1ft | check |
| 5 V Arduino (Uno/Nano/Mega) | 1 | Motor-bus master | (you have one) | n/a |
| Through-hole resistor assortment (incl. 1k; 47k/56k for the fallback) | 1 | Series protection resistors | Search: https://www.amazon.com/s?k=resistor+assortment+kit | check |
| *(fallback only)* Raspberry Pi Pico (3.3 V) | 1 | Motor-bus master if the bus isn't 5 V | https://www.raspberrypi.com/products/raspberry-pi-pico/ | from $4 |
| *(fallback only)* 4-ch bidirectional level shifter, BSS138 (10k pull-ups, 1.8–10 V) | 1 | 3.3 V Pico ↔ 5 V bus; pull-ups may need changing to 47k/56k [2] | https://www.adafruit.com/product/757 | $3.95 |
| Jumper wires, breadboard | 1 set | Prototyping | Search: https://www.amazon.com/s?k=breadboard+jumper+wire+kit | check |
| Multimeter | 1 | Pinout/voltage identification | (you likely have one) | n/a |

"check" means no verified price; these are commodity parts.

## Risks
- **The NG's boards differ from the ETX/DS protocol.** Mitigation: Phase 1 is passive,
  with an explicit go/no-go.
- **Wrong voltage on a bus pin damages a motor board.** Mitigation: measure every pin
  first; never wire the 12 V pin; series resistors on every bus line.
- **Runaway motor from a host crash.** Mitigation: the firmware watchdog and soft
  limits live on the Arduino, not in Python.
- **Spurious motion from a floating bus.** Observed: the mount moved on wall power with
  the HBX port empty. Mitigation: pull-ups and power-up order (step 5); keep the clutch
  loose or the handbox attached during bench work with an empty port.
- **Encoder LED miscalibration** gives bad counts [1]. Mitigation: copy the handbox's
  values from the captures.

## References
1. Cloudy Nights, "Meade DS Motors / encoders bad?" (A. Johansen, Feb 2021). Local:
   `documentation/websites/Meade DS Motors : encoders bad? - Meade Computerized Telescopes - Cloudy Nights.pdf`
2. J. Archbold, EQMOD-ETX (GitHub; no license file, so read, don't copy).
   https://github.com/ozarchie/EQMOD-ETX
   - ESP32 build: `Software/EQG2HBX/EQG2HBXE32/src/ETXProtocol.h`, `HBXComms.ino`
   - Arduino Mega build: `Software/EQG2HBX/EQG2HBXMega/HBXComms.h`, `HBXComms.ino`
     (direct pin wiring, `INPUT_PULLUP`)
   - README: level-translator note
3. sigrok, PulseView. https://sigrok.org/wiki/PulseView
4. sigrok, fx2lafw supported devices. https://sigrok.org/wiki/Fx2lafw
