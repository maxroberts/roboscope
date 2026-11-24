# Hardware plan: replace the AudioStar handbox with our own motor controller

**Goal:** drive the StarNavigator NG's two motor boards directly from our own
electronics. Every pointing and tracking decision then runs in roboscope, and we get
the motor-level data the handbox hides: encoder counts for both axes, motor PWM and an
encoder-error flag.

**Status:** plan. Nothing here has been verified on the NG yet. Background and sources
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
- **Replaced:** the handbox, by a Raspberry Pi Pico running our firmware on the HBX
  port. It talks USB serial to the Pi that runs roboscope.

```
Pi (roboscope) ──USB──▶ Pico (our firmware) ──level shifter──▶ HBX port ──▶ Az / Alt motor boards
```

## Phase 1: sniff the real handbox (passive, no risk to the mount)

1. **Identify the HBX connector and pinout.**
   - Count the HBX socket's pins; EQMOD-ETX's ETX uses an 8-pin HBX with azimuth on
     "Pin7" and altitude on "Pin5" [2]. Photograph the socket.
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
     analyzers don't state an input tolerance; if the lines are above 5 V, add a divider
     or the level shifter.
4. **Decode** in PulseView [3] and compare with the EQMOD-ETX command table [2].
   - Deliverable: a protocol section in `telescope_interface.md` marked *verified on
     NG*, covering opcodes, byte counts, speed encoding, and the encoder count format
     and wrap.

**Go/no-go:** if the NG traffic matches (or is decodable), go to Phase 2. If not, stop
here. We still have the handbox bridge, and the option of fully custom motor drivers.

## Phase 2: our controller, read-only first

5. **Wire the Pico to the HBX port through the level shifter:**
   - clock and data lines only, plus a common ground;
   - **no** connection to the 12 V pin;
   - the Pico is powered from the Pi's USB.

   The Pico's GPIO is 3.3 V, and the motor bus is expected to be 5 V, so the
   bidirectional shifter goes between them. EQMOD-ETX notes that the ETX-60 needed the
   shifter's pull-up resistors changed from 10k "to 47k or 56k" [2]. Expect to tune
   those.
6. **Firmware v0: listen and query only.** Implement the start/acknowledge handshake
   [1][2], `GetMotorType` (`0x0B`) and `GetStatus` (`0x08`). Turn each axis by hand
   (clutch released) and watch the counts change.
7. **Encoder LED calibration.** The handbox stores per-axis "Calibrate" values that set
   encoder LED brightness [1]. Read ours off the bus during the Phase 1 captures, then
   replicate them with `SetLEDI`/`CalibrateLED` (`0x03`/`0x04`) [2].

## Phase 3: our controller drives the motors

8. **Firmware v1:** speed commands (`SpeedStart`/`SpeedChnge`/`Stop`), with safety built
   into the firmware itself:
   - **Watchdog:** stop both motors if the Pi sends nothing for ~0.5 s.
   - **Soft limits** on cumulative counts. Default azimuth limit is ±45° from start (the
     cable rule) until the user lifts it.
   - A Stop command that pre-empts everything.
9. **Host protocol** (Pico ↔ Pi, our own, simple ASCII lines). For example: set axis
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
| Breakout boards with screw terminals matching the HBX connector (likely RJ45/8P8C; **confirm in step 1**) | 2 | Tap cable / Pico connection | Search: https://www.amazon.com/s?k=rj45+breakout+board+screw+terminal | check |
| Short patch cable matching the HBX connector | 2 | Tap cable to base | Search: https://www.amazon.com/s?k=rj45+patch+cable+1ft | check |
| Raspberry Pi Pico (RP2040; PIO suits bit-banged buses) | 2 (1 spare) | Motor-bus master | https://www.raspberrypi.com/products/raspberry-pi-pico/ | from $4 |
| 4-ch bidirectional level shifter, BSS138 (10k pull-ups, 1.8–10 V) | 2 | 3.3 V Pico ↔ 5 V bus | https://www.adafruit.com/product/757 | $3.95 |
| Through-hole resistor assortment (incl. 47k/56k) | 1 | Pull-up tuning per [2] | Search: https://www.amazon.com/s?k=resistor+assortment+kit | check |
| Jumper wires, breadboard | 1 set | Prototyping | Search: https://www.amazon.com/s?k=breadboard+jumper+wire+kit | check |
| Multimeter | 1 | Pinout/voltage identification | (you likely have one) | n/a |

"check" means no verified price; these are commodity parts.

## Risks
- **The NG's boards differ from the ETX/DS protocol.** Mitigation: Phase 1 is passive,
  with an explicit go/no-go.
- **Wrong voltage on a bus pin damages a motor board.** Mitigation: measure every pin
  first; never wire the 12 V pin; always go through the shifter.
- **Runaway motor from a host crash.** Mitigation: the firmware watchdog and soft
  limits live on the Pico, not in Python.
- **Encoder LED miscalibration** gives bad counts [1]. Mitigation: copy the handbox's
  values from the captures.

## References
1. Cloudy Nights, "Meade DS Motors / encoders bad?" (A. Johansen, Feb 2021). Local:
   `documentation/websites/Meade DS Motors : encoders bad? - Meade Computerized Telescopes - Cloudy Nights.pdf`
2. J. Archbold, EQMOD-ETX (GitHub; no license file, so read, don't copy).
   https://github.com/ozarchie/EQMOD-ETX (`Software/EQG2HBX/EQG2HBXE32/src/ETXProtocol.h`,
   `HBXComms.ino`, README)
3. sigrok, PulseView. https://sigrok.org/wiki/PulseView
4. sigrok, fx2lafw supported devices. https://sigrok.org/wiki/Fx2lafw
