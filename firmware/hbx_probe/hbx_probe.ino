/*
 * hbx_probe: probe of the StarNavigator NG motor bus (HBX port) from an Arduino Uno.
 *
 * Summary:
 *   Acts as bus master in place of the AudioStar handbox, to test whether the NG's motor
 *   boards speak the ETX/DS protocol described in documentation/telescope_interface.md
 *   §5.
 *   - v0 (verified 2026-11-24): handshake, GetMotorType (both axes reply 0x0C),
 *     GetStatus, bus reset.
 *   - v1 adds ONE motion command, `pulse`: a full-speed slew that the sketch itself always
 *     stops within 500 ms, so the host can't leave a motor running. Verified: azimuth
 *     moves ~1 deg clockwise (from above) per 250 ms "fwd", but GetStatus showed no
 *     encoder counts.
 *   - v2 (verified 2026-11-24): LED calibration fixes the missing counts. GetLEDI reads
 *     253 at power-up and 189 (Az) / 169 (Alt) after `calibrate`; then GetStatus
 *     reports signed encoder counts. Commands:
 *     - `ledi`: read the encoder LED setting.
 *     - `calibrate`: the encoder LED calibration sequence.
 *     - `pulse` now also reads status halfway through the slew.
 *   - v3 adds `run`: SpeedStart (0x01) at a signed 24-bit speed, for a capped duration,
 *     polling GetStatus every 250 ms, then Stop. Verified 2026-11-24: the boards regulate
 *     to the commanded speed (counts/s ~ speed/430, 1x-60x sidereal, both axes).
 *
 * Details:
 *   Wiring: see documentation/wiring_hbx_uno.md (the single reference). In short:
 *     Az clock (HBX 6) -> D2, Alt clock (HBX 4) -> D3, AUX clock (HBX 2) -> D4,
 *     data (HBX 3/5/7, joined) -> D5, each via 820 Ohm; HBX 8 -> GND; HBX 1 (+12 V)
 *     unconnected. 47 kOhm pull-ups to 5 V keep the bus idle while the Uno resets
 *     (verified with the mount on for a port re-open and a full upload).
 *
 *   Protocol, as described by EQMOD-ETX's source and A. Johansen (not copied code):
 *   - The master drives each clock; idle is clock high, data pulled up.
 *   - Handshake: clock low; the motor board answers by pulling data low; clock high; the
 *     board releases data.
 *   - Master -> board: 8 bits, MSB first. Data is set while the clock is high, then the
 *     clock pulses low.
 *   - Board -> master: the master pulses the clock low and samples data mid-pulse, MSB
 *     first.
 *   - Bit time ~200 us (100 us low / 100 us high).
 *   - Commands used:
 *     - 0x0B GetMotorType: 1 byte back.
 *     - 0x08 GetStatus: 3 bytes back (signed 16-bit position change since the last
 *       read, then PWM) plus 1 error bit.
 *
 *   Data is driven open-drain style: LOW = output low; HIGH = released to INPUT_PULLUP.
 *   We never actively drive data high, so we can't fight a board pulling it low.
 *   (EQMOD-ETX drives it high; switch to that only if the open-drain style proves too
 *   slow.)
 *
 *   Serial: 115200 baud, newline-terminated commands. Every reply is one line starting
 *   with "OK" or "ERR":
 *     hello                 -> banner
 *     lines                 -> current data-line level
 *     type <az|alt>         -> handshake timing + GetMotorType byte
 *     status <az|alt>       -> handshake timing + position delta, PWM, error bit
 *     reset <az|alt>        -> long clock pulse (500 ms high, 500 ms low, 250 ms high)
 *                              to resynchronize a board's receiver
 *     pulse <az|alt> <fwd|rev> <ms>
 *                           -> SlewForward (0x07) or SlewReverse (0x06), wait ms
 *                              (1..500, else rejected), then Stop (0x05). Halfway
 *                              through, GetStatus is read and reported (mid_delta,
 *                              mid_pwm, mid_err). The Stop is retried until
 *                              acknowledged (up to 20 tries) and the outcome reported.
 *                              The whole sequence runs inside one command, so the stop
 *                              doesn't depend on the host.
 *     run <az|alt> <speed> <ms>
 *                           -> SpeedStart (0x01) with a signed 24-bit speed (two's
 *                              complement, MSB first; the sign is the direction), |speed|
 *                              <= 600000, ms <= 10000. Polls GetStatus every 250 ms,
 *                              then Stop (retried until acknowledged) and one final
 *                              status. Reports total counts, counts during the steady
 *                              part (after the first 500 ms) and their rate, and the
 *                              PWM range seen.
 *     ledi <az|alt>         -> GetLEDI (0x09): 1 byte, the encoder LED current setting.
 *     calibrate <az|alt>    -> Stop (0x05); 100 ms; Sleep (0xE4); 100 ms; CalibrateLED
 *                              (0x04); 4 s; then GetLEDI. This is the sequence
 *                              EQMOD-ETX runs at setup. As far as its source shows it
 *                              doesn't move the motor, but that's UNVERIFIED on the NG.
 *                              Blocks for ~4.3 s.
 *   The host side is scripts/hbx_probe.py.
 */

const uint8_t PIN_CLK_AZ = 2;
const uint8_t PIN_CLK_ALT = 3;
const uint8_t PIN_CLK_AUX = 4;
const uint8_t PIN_DATA = 5;

const unsigned int HALF_BIT_US = 100;          // clock low or high time
const unsigned long ACK_TIMEOUT_US = 1600;     // 8 bit times, as in the reference design

const uint8_t CMD_STOP = 0x05;
const uint8_t CMD_SLEW_REVERSE = 0x06;
const uint8_t CMD_SLEW_FORWARD = 0x07;
const uint8_t CMD_GET_STATUS = 0x08;
const uint8_t CMD_CALIBRATE_LED = 0x04;
const uint8_t CMD_GET_LEDI = 0x09;
const uint8_t CMD_SLEEP = 0xE4;
const unsigned int PULSE_MAX_MS = 500;   // hard cap on full-speed `pulse`
const uint8_t CMD_SPEED_START = 0x01;
// `run` caps. With EQMOD-ETX's DS gearing (4928 counts/deg), a speed value of 8844 is
// 1x sidereal, so 600000 is ~68x sidereal (~0.28 deg/s). 10 s at that cap is under 3 deg.
const long RUN_MAX_SPEED = 600000;
const unsigned long RUN_MAX_MS = 10000;
const uint8_t CMD_GET_MOTOR_TYPE = 0x0B;

// --- data line helpers (open-drain emulation) -------------------------------------

void dataRelease() { pinMode(PIN_DATA, INPUT_PULLUP); }

void dataLow() {
  digitalWrite(PIN_DATA, LOW);
  pinMode(PIN_DATA, OUTPUT);
}

bool dataRead() { return digitalRead(PIN_DATA); }

// --- bus primitives ---------------------------------------------------------------

/* Handshake on one axis clock. Returns true if the board acknowledged. Reports how long
 * the board took to pull data low and to release it, for the log. */
bool handshake(uint8_t clk, unsigned long &tLow, unsigned long &tRelease) {
  dataRelease();
  digitalWrite(clk, LOW);
  unsigned long t0 = micros();
  while (dataRead() && micros() - t0 < ACK_TIMEOUT_US) {}
  tLow = micros() - t0;
  delayMicroseconds(3);                         // ignore a single glitch
  bool acked = !dataRead();
  digitalWrite(clk, HIGH);
  if (!acked) return false;
  t0 = micros();
  while (!dataRead() && micros() - t0 < ACK_TIMEOUT_US) {}
  tRelease = micros() - t0;
  if (!dataRead()) return false;                // board never released data
  delayMicroseconds(2 * HALF_BIT_US);
  return true;
}

/* Send one byte, MSB first: set data while the clock is high, then pulse the clock low. */
void sendByte(uint8_t clk, uint8_t value) {
  for (int8_t bit = 7; bit >= 0; bit--) {
    if (value & (1 << bit)) dataRelease(); else dataLow();
    delayMicroseconds(HALF_BIT_US / 2);
    digitalWrite(clk, LOW);
    delayMicroseconds(HALF_BIT_US);
    digitalWrite(clk, HIGH);
    delayMicroseconds(HALF_BIT_US / 2);
  }
  dataRelease();
  delayMicroseconds(2 * HALF_BIT_US);
}

/* Read `bits` bits (MSB first) from the board: pulse the clock low, sample mid-pulse. */
uint8_t readBits(uint8_t clk, uint8_t bits) {
  dataRelease();
  uint8_t value = 0;
  for (uint8_t i = 0; i < bits; i++) {
    digitalWrite(clk, LOW);
    delayMicroseconds(HALF_BIT_US / 2);
    value = (value << 1) | (dataRead() ? 1 : 0);
    delayMicroseconds(HALF_BIT_US / 2);
    digitalWrite(clk, HIGH);
    delayMicroseconds(HALF_BIT_US);
  }
  delayMicroseconds(2 * HALF_BIT_US);
  return value;
}

// --- command handlers -------------------------------------------------------------

int8_t clockFor(const String &axis) {
  if (axis == "az") return PIN_CLK_AZ;
  if (axis == "alt") return PIN_CLK_ALT;
  return -1;
}

void reportNoAck(const char *what, const String &axis, unsigned long tLow) {
  Serial.print(F("ERR noack "));
  Serial.print(what); Serial.print(' '); Serial.print(axis);
  Serial.print(F(" waited_us=")); Serial.println(tLow);
}

void cmdType(const String &axis, uint8_t clk) {
  unsigned long tLow = 0, tRel = 0;
  if (!handshake(clk, tLow, tRel)) { reportNoAck("type", axis, tLow); return; }
  sendByte(clk, CMD_GET_MOTOR_TYPE);
  uint8_t type = readBits(clk, 8);
  Serial.print(F("OK type ")); Serial.print(axis);
  Serial.print(F(" ack_low_us=")); Serial.print(tLow);
  Serial.print(F(" ack_release_us=")); Serial.print(tRel);
  Serial.print(F(" value=0x")); Serial.println(type, HEX);
}

void cmdStatus(const String &axis, uint8_t clk) {
  unsigned long tLow = 0, tRel = 0;
  if (!handshake(clk, tLow, tRel)) { reportNoAck("status", axis, tLow); return; }
  sendByte(clk, CMD_GET_STATUS);
  uint8_t hi = readBits(clk, 8);
  uint8_t lo = readBits(clk, 8);
  uint8_t pwm = readBits(clk, 8);
  uint8_t err = readBits(clk, 1);
  int16_t delta = (int16_t)((hi << 8) | lo);
  Serial.print(F("OK status ")); Serial.print(axis);
  Serial.print(F(" ack_low_us=")); Serial.print(tLow);
  Serial.print(F(" ack_release_us=")); Serial.print(tRel);
  Serial.print(F(" delta=")); Serial.print(delta);
  Serial.print(F(" pwm=")); Serial.print(pwm);
  Serial.print(F(" err=")); Serial.println(err);
}

/* Send a command byte with no data. Returns false if the board didn't acknowledge. */
bool sendCommand(uint8_t clk, uint8_t command) {
  unsigned long tLow, tRel;
  if (!handshake(clk, tLow, tRel)) return false;
  sendByte(clk, command);
  return true;
}

void cmdPulse(const String &axis, uint8_t clk, const String &args) {
  int sp = args.indexOf(' ');
  String dir = sp < 0 ? args : args.substring(0, sp);
  long ms = sp < 0 ? 0 : args.substring(sp + 1).toInt();
  if ((dir != "fwd" && dir != "rev") || ms < 1 || ms > PULSE_MAX_MS) {
    Serial.println(F("ERR usage: pulse <az|alt> <fwd|rev> <1..500 ms>"));
    return;
  }
  if (!sendCommand(clk, dir == "fwd" ? CMD_SLEW_FORWARD : CMD_SLEW_REVERSE)) {
    Serial.print(F("ERR noack pulse start ")); Serial.println(axis);
    return;
  }
  // Read status mid-slew (counts and PWM while the motor runs), then finish the pulse.
  delay(ms / 2);
  unsigned long tLow, tRel;
  bool midOk = handshake(clk, tLow, tRel);
  uint8_t hi = 0, lo = 0, pwm = 0, err = 0;
  if (midOk) {
    sendByte(clk, CMD_GET_STATUS);
    hi = readBits(clk, 8); lo = readBits(clk, 8); pwm = readBits(clk, 8); err = readBits(clk, 1);
  }
  delay(ms - ms / 2);
  uint8_t tries = 0;
  bool stopped = false;
  while (!stopped && tries < 20) { stopped = sendCommand(clk, CMD_STOP); tries++; }
  Serial.print(stopped ? F("OK pulse ") : F("ERR STOP NOT ACKNOWLEDGED - CUT POWER "));
  Serial.print(axis); Serial.print(' '); Serial.print(dir);
  Serial.print(F(" ms=")); Serial.print(ms);
  Serial.print(F(" stop_tries=")); Serial.print(tries);
  if (midOk) {
    Serial.print(F(" mid_delta=")); Serial.print((int16_t)((hi << 8) | lo));
    Serial.print(F(" mid_pwm=")); Serial.print(pwm);
    Serial.print(F(" mid_err=")); Serial.println(err);
  } else {
    Serial.println(F(" mid_status=noack"));
  }
}

/* GetStatus helper for `run`: returns false on no-ack; fills delta and pwm. */
bool readStatus(uint8_t clk, int16_t &delta, uint8_t &pwm) {
  if (!sendCommand(clk, CMD_GET_STATUS)) return false;
  uint8_t hi = readBits(clk, 8), lo = readBits(clk, 8);
  pwm = readBits(clk, 8);
  readBits(clk, 1);
  delta = (int16_t)((hi << 8) | lo);
  return true;
}

void cmdRun(const String &axis, uint8_t clk, const String &args) {
  int sp = args.indexOf(' ');
  long speed = sp < 0 ? 0 : args.substring(0, sp).toInt();
  long ms = sp < 0 ? 0 : args.substring(sp + 1).toInt();
  if (speed == 0 || labs(speed) > RUN_MAX_SPEED || ms < 1 || (unsigned long)ms > RUN_MAX_MS) {
    Serial.println(F("ERR usage: run <az|alt> <speed, 1..600000 signed> <1..10000 ms>"));
    return;
  }
  unsigned long tLow, tRel;
  if (!handshake(clk, tLow, tRel)) { Serial.print(F("ERR noack run start ")); Serial.println(axis); return; }
  sendByte(clk, CMD_SPEED_START);
  uint32_t v = (uint32_t)speed & 0xFFFFFF;           // 24-bit two's complement
  sendByte(clk, (v >> 16) & 0xFF);
  sendByte(clk, (v >> 8) & 0xFF);
  sendByte(clk, v & 0xFF);

  unsigned long t0 = millis(), steadyStart = 0;
  long total = 0, steady = 0;
  uint8_t pwmMin = 255, pwmMax = 0;
  uint16_t misses = 0;
  while (millis() - t0 < (unsigned long)ms) {
    delay(250);
    int16_t d; uint8_t pwm;
    if (!readStatus(clk, d, pwm)) { misses++; continue; }
    total += d;
    if (millis() - t0 >= 500) {
      if (steadyStart == 0) steadyStart = millis();
      else steady += d;                               // counts after the first steady sample
      if (pwm < pwmMin) pwmMin = pwm;
      if (pwm > pwmMax) pwmMax = pwm;
    }
  }
  unsigned long steadyMs = steadyStart ? millis() - steadyStart : 0;
  uint8_t tries = 0; bool stopped = false;
  while (!stopped && tries < 20) { stopped = sendCommand(clk, CMD_STOP); tries++; }
  delay(300);
  int16_t d; uint8_t pwm;
  long coast = readStatus(clk, d, pwm) ? d : 0;
  Serial.print(stopped ? F("OK run ") : F("ERR STOP NOT ACKNOWLEDGED - CUT POWER "));
  Serial.print(axis); Serial.print(F(" speed=")); Serial.print(speed);
  Serial.print(F(" ms=")); Serial.print(ms);
  Serial.print(F(" total=")); Serial.print(total + coast);
  Serial.print(F(" coast=")); Serial.print(coast);
  Serial.print(F(" steady_counts=")); Serial.print(steady);
  Serial.print(F(" steady_ms=")); Serial.print(steadyMs);
  Serial.print(F(" steady_cps=")); Serial.print(steadyMs ? steady * 1000.0 / steadyMs : 0.0, 1);
  Serial.print(F(" pwm=")); Serial.print(pwmMin); Serial.print('-'); Serial.print(pwmMax);
  Serial.print(F(" misses=")); Serial.print(misses);
  Serial.print(F(" stop_tries=")); Serial.println(tries);
}

void cmdLedi(const String &axis, uint8_t clk) {
  if (!sendCommand(clk, CMD_GET_LEDI)) { Serial.print(F("ERR noack ledi ")); Serial.println(axis); return; }
  uint8_t v = readBits(clk, 8);
  Serial.print(F("OK ledi ")); Serial.print(axis); Serial.print(F(" value=")); Serial.println(v);
}

void cmdCalibrate(const String &axis, uint8_t clk) {
  const uint8_t steps[] = {CMD_STOP, CMD_SLEEP, CMD_CALIBRATE_LED};
  for (uint8_t i = 0; i < 3; i++) {
    if (!sendCommand(clk, steps[i])) {
      Serial.print(F("ERR noack calibrate step=0x")); Serial.print(steps[i], HEX);
      Serial.print(' '); Serial.println(axis);
      return;
    }
    delay(i < 2 ? 100 : 4000);
  }
  if (!sendCommand(clk, CMD_GET_LEDI)) { Serial.print(F("ERR noack calibrate readback ")); Serial.println(axis); return; }
  uint8_t v = readBits(clk, 8);
  Serial.print(F("OK calibrate ")); Serial.print(axis); Serial.print(F(" ledi=")); Serial.println(v);
}

void cmdReset(const String &axis, uint8_t clk) {
  digitalWrite(clk, HIGH); delay(500);
  digitalWrite(clk, LOW);  delay(500);
  digitalWrite(clk, HIGH); delay(250);
  Serial.print(F("OK reset ")); Serial.println(axis);
}

// --- setup / loop -----------------------------------------------------------------

void setup() {
  // Hold every clock high and the data line pulled up from the first moment, so the bus
  // never floats while we're attached.
  const uint8_t clocks[] = {PIN_CLK_AZ, PIN_CLK_ALT, PIN_CLK_AUX};
  for (uint8_t pin : clocks) {
    digitalWrite(pin, HIGH);
    pinMode(pin, OUTPUT);
  }
  dataRelease();
  Serial.begin(115200);
  Serial.println(F("OK hbx_probe v3 (type/status/reset/pulse/ledi/calibrate/run)"));
}

void loop() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  int sp = line.indexOf(' ');
  String cmd = sp < 0 ? line : line.substring(0, sp);
  String rest = sp < 0 ? "" : line.substring(sp + 1);
  int sp2 = rest.indexOf(' ');
  String axis = sp2 < 0 ? rest : rest.substring(0, sp2);
  String args = sp2 < 0 ? "" : rest.substring(sp2 + 1);

  if (cmd == "hello") { Serial.println(F("OK hbx_probe v3 (type/status/reset/pulse/ledi/calibrate/run)")); return; }
  if (cmd == "lines") { Serial.print(F("OK lines data=")); Serial.println(dataRead()); return; }

  int8_t clk = clockFor(axis);
  if (clk < 0) { Serial.print(F("ERR bad axis '")); Serial.print(axis); Serial.println('\''); return; }
  if (cmd == "type") cmdType(axis, clk);
  else if (cmd == "status") cmdStatus(axis, clk);
  else if (cmd == "reset") cmdReset(axis, clk);
  else if (cmd == "pulse") cmdPulse(axis, clk, args);
  else if (cmd == "ledi") cmdLedi(axis, clk);
  else if (cmd == "run") cmdRun(axis, clk, args);
  else if (cmd == "calibrate") cmdCalibrate(axis, clk);
  else { Serial.print(F("ERR unknown command '")); Serial.print(cmd); Serial.println('\''); }
}
