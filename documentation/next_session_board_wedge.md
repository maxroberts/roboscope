# Next session: the motor-board "wedge" after an Uno reset

**Problem:** resetting the Uno (opening its serial port, or uploading) while
`mount_controller` is polling the bus leaves both motor boards out of sync in ~5/6
cases:
- Alt is silent.
- Az answers with garbage: delta −1949 / PWM 252 on every poll.
- The boards keep their LED calibration, so they aren't rebooting.

Current workaround: the firmware auto-resyncs (clock high 500 ms, low 500 ms, high
250 ms) after a link has been down 2 s, and the boards are healthy ~5 s after reset.
Evidence: `hardware_findings.md`, sections from 22:04 onward.

## What's known
- Never seen with `hbx_probe`, which only talks to the bus on command.
- An identical resync **at boot** doesn't clear it, even with 600 ms of settle time
  afterwards. A resync **a few seconds after boot** always does.
- Polling the wedged boards doesn't clear it (garbage persisted >15 s under polling).
- It happened on battery (10.0 V and sagging). Not yet tested on the 12 V adapter.

## Hypotheses to test (cheapest first)
1. **The reset itself isn't the trigger; our boot sequence is.** Something in `setup()`
   (the clock pulses as pins switch to OUTPUT, the Stop burst, the GetLEDI reads) runs
   too soon after the bootloader and wedges the boards.
   - Test: boot firmware that touches nothing for 5 s, then resyncs and polls once.
   - If the wedge disappears, the trigger is in our boot.
2. **The boards need a long quiet period after an interrupted transaction** before a
   resync takes.
   - Test: vary the boot-time delay before the resync (0.5 / 1 / 2 / 3 / 5 s) and
     count wedges per delay.
3. **Bootloader activity:** the Uno's bootloader runs ~1.5 s after reset, with the
   pins floating against the 47k pull-ups.
   - Test: watch the clock and data lines through a reset with a logic analyzer, or
     with the `hbx_probe` "lines" approach from a second microcontroller.
4. **Supply:** repeat experiment 1 on the 12 V adapter.

## Ways to avoid it altogether
- **Don't reset the Uno during a session:** a long-running host process keeps the port
  open (the bridge pattern).
- **Disable auto-reset on open.** A 10 µF capacitor between the Uno's RESET and GND
  blocks the DTR reset (it must be removed for uploads), or cut the RESET-EN jumper.
  Then opening the port no longer resets the Uno.
- **Stop polling cleanly before planned resets:** add a `QUIET` command and send it
  before uploads.

## Also fix
- Re-read the LED setting on every link-up transition (`cal` stays 0 after
  auto-recovery today).
- Model the wedge in `SimulatedController` and test that `wait_healthy()` rides
  through it.
