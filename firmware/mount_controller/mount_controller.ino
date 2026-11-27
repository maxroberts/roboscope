/*
 * mount_controller: Arduino Uno firmware that replaces the AudioStar handbox as master of
 * the StarNavigator NG motor bus, with the safety logic living here, not on the host.
 *
 * Summary:
 *   Accepts simple ASCII commands from the host (roboscope.mount.controller) over USB
 *   serial: set an axis speed in encoder counts/s, stop, read positions, set soft
 *   limits, calibrate encoder LEDs. Between commands it polls both motor boards and
 *   keeps an absolute encoder position for each axis.
 *
 * Details:
 *   Wiring: documentation/wiring_hbx_uno.md (same as firmware/hbx_probe).
 *
 *   Bus protocol (verified on the NG 2026-11-24 with hbx_probe; see
 *   documentation/telescope_interface.md §5 and hardware_findings.md):
 *     - Handshake: clock low, the board pulls data low; clock high, the board releases.
 *     - Bytes go MSB first at ~200 us/bit.
 *     - 0x01 SpeedStart + 24-bit signed speed. The board regulates to it;
 *       speed ~ 429.5 x counts/s.
 *     - 0x00 SpeedChnge: same argument. Used for changes while moving. UNVERIFIED on
 *       the NG until this firmware's tests.
 *     - 0x05 Stop.
 *     - 0x08 GetStatus -> int16 position change since the last read, uint8 PWM, 1
 *       error bit.
 *     - 0x09 GetLEDI; 0x05/0xE4/0x04 LED calibration (must run once per mount
 *       power-up, or no counts are reported).
 *
 *   Safety (enforced here, independent of the host):
 *     - Watchdog: if any axis is moving and no command line has arrived for
 *       WATCHDOG_MS, both axes are stopped ("EVT watchdog").
 *     - Soft limits per axis (counts, relative to this boot's zero or to ZERO).
 *       - Each poll predicts the position LOOKAHEAD_MS ahead; if that's past a limit
 *         in the direction of travel, the axis stops ("EVT limit").
 *       - A SPEED command toward a limit already reached is refused.
 *       - Defaults at boot: Az +-30850 counts (~+-10 deg at 3085 counts/deg),
 *         Alt +-190000 (~+-45 deg at 4230 counts/deg).
 *     - Speed cap: |counts/s| <= MAX_CPS = 12000 (Az 3.89, Alt 2.84 °/s). Raised from
 *       1300 to 6500 on 2026-11-26 (handbox speed 8, 1.5°/s, on both axes), then to
 *       12000 on 2026-11-27 at the user's choice. Regulation held within 0.4% up to
 *       12000 on both axes (hardware_findings.md, "step-up test to 12,000 counts/s";
 *       documentation/mount_speed_limits.md). 12000 × 429.5 fits the 24-bit speed
 *       argument. At 1200 counts per 100 ms poll it is 25% under MAX_DELTA. Don't
 *       raise MAX_DELTA to go faster: stuck boards report exactly 1949 per poll, and
 *       the filter must keep catching that. Long holds at these speeds are untested;
 *       documentation/mount_speed_limits.md keeps a watch list.
 *     - Uncommanded motion: once STOP_GRACE_MS has passed since the axis was last
 *       stopped (or CAL ran), an axis at commanded speed 0 that reports PWM > 0, or
 *       moves more than UNCMD_COUNTS on 2 consecutive polls, is sent Stop on every poll
 *       until it's still, with "EVT uncommanded <axis> ...". The grace period and the
 *       2-poll rule keep normal coast after a stop (tens of counts) from tripping it;
 *       the first version fired on that. When a board's link comes back up after being
 *       down, it is sent Stop first. (Added 2026-11-24 after the Az board was found
 *       running at full drive on its own while the host had commanded nothing. Soft
 *       limits, watchdog and link checks only covered commanded motion. The user saw
 *       no actual movement, and the "motion" was in fact garbage counts, see the next
 *       point. But a real runaway would equally have gone unstopped.)
 *     - Implausible status: a poll reporting |delta| > MAX_DELTA counts (more than an
 *       axis can move in one 100 ms poll even at full slew, ~1200) is treated as a bus
 *       error. It's not added to the position and counts toward link-down. (Added
 *       2026-11-24: an Az board in a bad state, probably a low-battery brownout,
 *       answered polls with deltas in exact multiples of 1949 and PWM 252 while
 *       nothing moved, faking ~119° of travel.)
 *     - Bus resync (RESYNC command and auto-recovery; NOT at boot by default): each axis
 *       clock is held high 500 ms, low 500 ms, then high 250 ms (EQMOD-ETX's motor-reset
 *       sequence). It re-synchronizes a board that is already wedged, but run at boot on
 *       freshly started boards it *causes* the wedge (2026-11-25: 0/24 healthy boots with
 *       it, across quiet-time, clock-flush and post-Stop-delay variants; 5/6 without). (Added 2026-11-24: twice,
 *       the first Uno reset after continuous polling left the boards silent or sending
 *       garbage until the mount was power-cycled.) The night's reset experiments
 *       (5/6 wedged) were run WITH the boot resync. The 2026-11-25 experiments showed
 *       the boot resync was what wedged them; without it, port re-opens were 16/16
 *       healthy. Only the reset during a firmware upload still wedges the boards
 *       (2/2), which auto-recovery clears. Kept from that investigation:
 *       RESYNC_SETTLE_MS of quiet after a resync, and Stop retries spaced
 *       STOP_RETRY_MS apart.
 *     - Auto-recovery: if an axis link has been down (silent or garbage) for
 *       AUTO_RESYNC_AFTER_MS while nothing is commanded to move, the firmware runs the
 *       resync itself (at most every AUTO_RESYNC_EVERY_MS) and reports
 *       "EVT resync linkaz=.. linkalt=..". On already-wedged boards it recovered all
 *       29 wedged trials in the 2026-11-24/25 experiments; one needed a second
 *       attempt (30 resyncs in total). The RESYNC command worked 6/6.
 *     - On every link-up transition the board is sent Stop and its LED setting is
 *       re-read, so `cal=` stays accurate after a recovery.
 *     - Link loss: an axis whose GetStatus goes unacknowledged LINK_FAILS times in a row
 *       is marked down. If either axis is moving at that point, both are stopped
 *       ("EVT link <axis> down"). SPEED is refused while an axis is down; it comes back
 *       up on the next acknowledged poll. (Added after the mount was found powered off
 *       while POS kept reporting stale values.)
 *     - Motion is refused until both axes' encoder LEDs read calibrated (GetLEDI < 250;
 *       they read 253 at mount power-up). Run CAL once per mount power-up. CAL turns
 *       each motor slightly (a few hundred counts).
 *     - Position and limits live in RAM. A reset of the Uno (re-opening its serial
 *       port) zeros positions and restores the default limits, so the host must
 *       re-establish its reference.
 *
 *   Host commands (one per line; every reply is one line starting OK or ERR;
 *   asynchronous events start EVT):
 *     HELLO                      -> OK mount_controller v2
 *     PING                       -> OK (just feeds the watchdog)
 *     CAL                        -> calibrate both encoder LEDs (~9 s, blocks)
 *                                   -> OK cal az=<ledi> alt=<ledi>
 *     SPEED <az|alt> <cps>       -> start/change speed in counts/s (float, signed)
 *     STOP [az|alt]              -> stop one or both axes
 *     POS                        -> OK pos az=<counts> alt=<counts> vaz=<cps> valt=<cps>
 *                                   pwmaz=<n> pwmalt=<n> erraz=<n> erralt=<n>
 *                                   linkaz=<0|1> linkalt=<0|1>
 *                                   (v* = commanded speeds; link = the board answered
 *                                   its recent polls)
 *   Boot line: OK mount_controller v2 calibrated=<0|1> ledi_az=<n> ledi_alt=<n>
 *              linkaz=<0|1> linkalt=<0|1>   (ledi 0 = no answer; 253 = uncalibrated)
 *              After an Uno reset the boards are usually desynced at boot (links 0);
 *              auto-recovery brings them up ~5 s later and re-reads calibration. POS
 *              then reports `cal=<0|1>` with the current calibration state.
 *     ZERO [az|alt]              -> set the position counter(s) to 0
 *     LIMIT <az|alt> <min> <max> -> set soft limits in counts
 *     LIMITS                     -> OK limits az=<min>,<max> alt=<min>,<max>
 *     RESYNC                     -> resync both axes' bus receivers (~2.5 s; only while
 *                                   stopped) -> OK resync linkaz=<0|1> linkalt=<0|1>
 */

#include <stdlib.h>

const uint8_t PIN_CLK_AZ = 2, PIN_CLK_ALT = 3, PIN_CLK_AUX = 4, PIN_DATA = 5;
const unsigned int HALF_BIT_US = 100;
const unsigned long ACK_TIMEOUT_US = 1600;

const uint8_t CMD_SPEED_CHANGE = 0x00, CMD_SPEED_START = 0x01, CMD_CALIBRATE_LED = 0x04,
              CMD_STOP = 0x05, CMD_GET_STATUS = 0x08, CMD_GET_LEDI = 0x09, CMD_SLEEP = 0xE4;

const float SPEED_PER_CPS = 429.5;           // board speed units per count/s (measured)
const float MAX_CPS = 12000.0;
const unsigned long WATCHDOG_MS = 1000;
const unsigned long POLL_MS = 50;            // alternate axes, so each axis every 100 ms
const unsigned long LOOKAHEAD_MS = 300;
const uint8_t LEDI_UNCALIBRATED = 250;
const uint8_t LINK_FAILS = 3;               // consecutive unacknowledged polls -> down
const int16_t UNCMD_COUNTS = 20;            // motion per poll counted as "moving" when stopped
const unsigned long STOP_GRACE_MS = 1500;   // coast allowance after a stop / CAL
const unsigned long RESYNC_SETTLE_MS = 600; // quiet time after the resync pulse
const unsigned int STOP_RETRY_MS = 20;      // spacing between Stop retries
const unsigned long AUTO_RESYNC_AFTER_MS = 2000;
// Whether setup() runs resyncBus(). Default OFF: the 2026-11-25 experiments showed the
// boot resync itself wedges freshly started boards (0/24 healthy at boot with it, 5/6
// without; documentation/hardware_findings.md). Override with -DBOOT_RESYNC=1 only for
// experiments.
#ifndef BOOT_RESYNC
#define BOOT_RESYNC 0
#endif
const unsigned long AUTO_RESYNC_EVERY_MS = 5000;
const int16_t MAX_DELTA = 1500;             // larger per-poll deltas are bus garbage

struct Axis {
  const char *name;
  uint8_t clk;
  long pos;            // absolute counts since boot / ZERO
  float cps;           // commanded speed (0 = stopped)
  long limMin, limMax;
  uint8_t pwm, err;
  bool calibrated;
  uint8_t fails;       // consecutive unacknowledged polls
  bool link;
  int16_t lastDelta;   // position change reported by the latest poll
  bool uncommanded;    // currently stopping an uncommanded motion
  unsigned long stoppedMs;  // when the axis was last stopped (for the coast grace)
  uint8_t movingPolls;      // consecutive polls showing motion while stopped
};

Axis axes[2] = {
  {"az", PIN_CLK_AZ, 0, 0, -30850, 30850, 0, 0, false, 0, false, 0, false, 0, 0},
  {"alt", PIN_CLK_ALT, 0, 0, -190000, 190000, 0, 0, false, 0, false, 0, false, 0, 0},
};

unsigned long lastHostMs = 0, lastPollMs = 0;
unsigned long linkDownSinceMs = 0, lastResyncMs = 0;   // for auto-recovery
uint8_t pollIndex = 0;
char lineBuf[64];
uint8_t lineLen = 0;

// ---------------------------------------------------------------- bus primitives

void dataRelease() { pinMode(PIN_DATA, INPUT_PULLUP); }
void dataLow() { digitalWrite(PIN_DATA, LOW); pinMode(PIN_DATA, OUTPUT); }
bool dataRead() { return digitalRead(PIN_DATA); }

bool handshake(uint8_t clk) {
  dataRelease();
  digitalWrite(clk, LOW);
  unsigned long t0 = micros();
  while (dataRead() && micros() - t0 < ACK_TIMEOUT_US) {}
  delayMicroseconds(3);
  bool acked = !dataRead();
  digitalWrite(clk, HIGH);
  if (!acked) return false;
  t0 = micros();
  while (!dataRead() && micros() - t0 < ACK_TIMEOUT_US) {}
  if (!dataRead()) return false;
  delayMicroseconds(2 * HALF_BIT_US);
  return true;
}

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

bool sendCommand(uint8_t clk, uint8_t cmd) {
  if (!handshake(clk)) return false;
  sendByte(clk, cmd);
  return true;
}

// ---------------------------------------------------------------- axis operations

/* Stop an axis, retrying until acknowledged. Returns false if it never was. */
bool stopAxis(Axis &a) {
  bool ok = false;
  // Space retries out: back-to-back failed handshakes leave partial clock pulses that
  // can knock a board out of bit sync (seen 2026-11-24).
  for (uint8_t i = 0; i < 20 && !ok; i++) {
    if (i) delay(STOP_RETRY_MS);
    ok = sendCommand(a.clk, CMD_STOP);
  }
  if (a.cps != 0) a.stoppedMs = millis();
  a.cps = 0;
  return ok;
}

void stopAll(const char *reason) {
  bool moving = axes[0].cps != 0 || axes[1].cps != 0;
  bool ok = stopAxis(axes[0]) & stopAxis(axes[1]);
  if (reason && moving) {
    Serial.print(F("EVT ")); Serial.print(reason);
    Serial.println(ok ? F(" stopped") : F(" STOP NOT ACKNOWLEDGED - CUT POWER"));
  }
}

/* GetStatus: add the position change to the running total. */
bool pollAxis(Axis &a) {
  if (!sendCommand(a.clk, CMD_GET_STATUS)) {
    if (a.fails < 255) a.fails++;
    if (a.fails >= LINK_FAILS) a.link = false;
    return false;
  }
  uint8_t hi = readBits(a.clk, 8), lo = readBits(a.clk, 8);
  a.pwm = readBits(a.clk, 8);
  a.err = readBits(a.clk, 1);
  int16_t delta = (int16_t)((hi << 8) | lo);
  if (abs(delta) > MAX_DELTA) {             // implausible: treat as a failed poll
    a.lastDelta = 0;
    if (a.fails < 255) a.fails++;
    if (a.fails >= LINK_FAILS) a.link = false;
    Serial.print(F("EVT garbage ")); Serial.print(a.name);
    Serial.print(F(" delta=")); Serial.println(delta);
    return false;
  }
  a.fails = 0;
  a.link = true;
  a.lastDelta = delta;
  a.pos += delta;
  return true;
}

/* Send a speed (counts/s) using SpeedStart from rest, SpeedChnge while moving. */
bool sendSpeed(Axis &a, float cps) {
  long v = lround(cps * SPEED_PER_CPS);
  uint32_t u = (uint32_t)v & 0xFFFFFF;
  if (!handshake(a.clk)) return false;
  sendByte(a.clk, a.cps == 0 ? CMD_SPEED_START : CMD_SPEED_CHANGE);
  sendByte(a.clk, (u >> 16) & 0xFF);
  sendByte(a.clk, (u >> 8) & 0xFF);
  sendByte(a.clk, u & 0xFF);
  a.cps = cps;
  return true;
}

/* Would continuing at the commanded speed cross a limit within LOOKAHEAD_MS? */
bool beyondLimit(const Axis &a) {
  long ahead = a.pos + (long)(a.cps * LOOKAHEAD_MS / 1000.0);
  return (a.cps > 0 && ahead >= a.limMax) || (a.cps < 0 && ahead <= a.limMin);
}

/* EQMOD-ETX's motor reset: long clock pulses resynchronize the board's bit receiver. */
void resyncBus() {
  dataRelease();
  for (Axis &a : axes) digitalWrite(a.clk, HIGH);
  digitalWrite(PIN_CLK_AUX, HIGH);
  delay(500);
  for (Axis &a : axes) digitalWrite(a.clk, LOW);
  delay(500);
  for (Axis &a : axes) digitalWrite(a.clk, HIGH);
  delay(250);
  delay(RESYNC_SETTLE_MS);   // boards need time after the reset pulse before a handshake
}

uint8_t readLedi(Axis &a) {
  if (!sendCommand(a.clk, CMD_GET_LEDI)) return 0;
  return readBits(a.clk, 8);
}

/* After a resync: re-check each board's link and, if it answers, re-read its LED setting
 * so `calibrated` reflects reality. A desynced board keeps its calibration; only a mount
 * power cycle resets it to 253. Positions are kept, but counts lost while the link was
 * down are not recovered. */
void refreshAfterResync() {
  for (Axis &a : axes) {
    a.fails = 0;
    a.link = pollAxis(a);
    if (a.link) {
      uint8_t ledi = readLedi(a);
      a.calibrated = ledi > 0 && ledi < LEDI_UNCALIBRATED;
    }
    a.stoppedMs = millis();
  }
}

// ---------------------------------------------------------------- command handling

Axis *axisNamed(const char *name) {
  for (Axis &a : axes) if (strcmp(name, a.name) == 0) return &a;
  return nullptr;
}

void replyPos() {
  Serial.print(F("OK pos az=")); Serial.print(axes[0].pos);
  Serial.print(F(" alt=")); Serial.print(axes[1].pos);
  Serial.print(F(" vaz=")); Serial.print(axes[0].cps, 1);
  Serial.print(F(" valt=")); Serial.print(axes[1].cps, 1);
  Serial.print(F(" pwmaz=")); Serial.print(axes[0].pwm);
  Serial.print(F(" pwmalt=")); Serial.print(axes[1].pwm);
  Serial.print(F(" erraz=")); Serial.print(axes[0].err);
  Serial.print(F(" erralt=")); Serial.print(axes[1].err);
  Serial.print(F(" linkaz=")); Serial.print(axes[0].link);
  Serial.print(F(" linkalt=")); Serial.print(axes[1].link);
  Serial.print(F(" cal=")); Serial.println(axes[0].calibrated && axes[1].calibrated ? 1 : 0);
}

void cmdCal() {
  stopAll(nullptr);
  for (Axis &a : axes) {
    bool ok = sendCommand(a.clk, CMD_STOP);
    delay(100);
    ok = ok && sendCommand(a.clk, CMD_SLEEP);
    delay(100);
    ok = ok && sendCommand(a.clk, CMD_CALIBRATE_LED);
    if (!ok) { Serial.print(F("ERR cal noack ")); Serial.println(a.name); return; }
    delay(4000);
  }
  for (Axis &a : axes) {
    pollAxis(a);                         // absorb the small calibration motion
    a.pos = 0;
    a.stoppedMs = millis();              // calibration motion may still be coasting
  }
  uint8_t az = readLedi(axes[0]), alt = readLedi(axes[1]);
  axes[0].calibrated = az > 0 && az < LEDI_UNCALIBRATED;
  axes[1].calibrated = alt > 0 && alt < LEDI_UNCALIBRATED;
  Serial.print(F("OK cal az=")); Serial.print(az);
  Serial.print(F(" alt=")); Serial.print(alt);
  Serial.println(F(" (positions zeroed)"));
}

void cmdSpeed(Axis &a, float cps) {
  if (!axes[0].link || !axes[1].link) { Serial.println(F("ERR motor board not answering (mount off?)")); return; }
  if (!axes[0].calibrated || !axes[1].calibrated) { Serial.println(F("ERR not calibrated: run CAL")); return; }
  if (fabs(cps) > MAX_CPS) { Serial.println(F("ERR speed above MAX_CPS")); return; }
  if (cps == 0) { stopAxis(a); Serial.println(F("OK")); return; }
  Axis probe = a;
  probe.cps = cps;
  if (beyondLimit(probe)) { Serial.print(F("ERR at limit ")); Serial.println(a.name); return; }
  if (!sendSpeed(a, cps)) { Serial.println(F("ERR noack speed")); return; }
  Serial.println(F("OK"));
}

void handleLine(char *line) {
  lastHostMs = millis();
  char *cmd = strtok(line, " ");
  if (!cmd) return;
  char *arg1 = strtok(nullptr, " ");
  char *arg2 = strtok(nullptr, " ");
  char *arg3 = strtok(nullptr, " ");

  if (!strcmp(cmd, "HELLO")) { Serial.println(F("OK mount_controller v2")); return; }
  if (!strcmp(cmd, "PING")) { Serial.println(F("OK")); return; }
  if (!strcmp(cmd, "POS")) { replyPos(); return; }
  if (!strcmp(cmd, "CAL")) { cmdCal(); return; }
  if (!strcmp(cmd, "RESYNC")) {
    if (axes[0].cps != 0 || axes[1].cps != 0) { Serial.println(F("ERR stop first")); return; }
    resyncBus();
    refreshAfterResync();
    Serial.print(F("OK resync linkaz=")); Serial.print(axes[0].link);
    Serial.print(F(" linkalt=")); Serial.println(axes[1].link);
    return;
  }
  if (!strcmp(cmd, "LIMITS")) {
    Serial.print(F("OK limits az=")); Serial.print(axes[0].limMin); Serial.print(',');
    Serial.print(axes[0].limMax); Serial.print(F(" alt=")); Serial.print(axes[1].limMin);
    Serial.print(','); Serial.println(axes[1].limMax);
    return;
  }
  if (!strcmp(cmd, "STOP")) {
    if (!arg1) { stopAll(nullptr); Serial.println(F("OK")); return; }
    Axis *a = axisNamed(arg1);
    if (!a) { Serial.println(F("ERR bad axis")); return; }
    Serial.println(stopAxis(*a) ? F("OK") : F("ERR STOP NOT ACKNOWLEDGED - CUT POWER"));
    return;
  }
  if (!strcmp(cmd, "ZERO")) {
    for (Axis &a : axes) if (!arg1 || !strcmp(arg1, a.name)) a.pos = 0;
    Serial.println(F("OK"));
    return;
  }
  Axis *a = arg1 ? axisNamed(arg1) : nullptr;
  if (!a) { Serial.println(F("ERR bad or missing axis")); return; }
  if (!strcmp(cmd, "SPEED") && arg2) { cmdSpeed(*a, atof(arg2)); return; }
  if (!strcmp(cmd, "LIMIT") && arg2 && arg3) {
    long lo = atol(arg2), hi = atol(arg3);
    if (lo >= hi) { Serial.println(F("ERR min >= max")); return; }
    a->limMin = lo; a->limMax = hi;
    Serial.println(F("OK"));
    return;
  }
  Serial.println(F("ERR unknown command"));
}

// ---------------------------------------------------------------- setup / loop

void setup() {
  const uint8_t clocks[] = {PIN_CLK_AZ, PIN_CLK_ALT, PIN_CLK_AUX};
  for (uint8_t pin : clocks) { digitalWrite(pin, HIGH); pinMode(pin, OUTPUT); }
  dataRelease();
  Serial.begin(115200);
  if (BOOT_RESYNC) resyncBus();           // off by default; see BOOT_RESYNC                            // a reset may have cut a transaction mid-byte
  stopAll(nullptr);                       // in case a board woke up moving
  uint8_t ledi[2];
  for (uint8_t i = 0; i < 2; i++) {
    Axis &a = axes[i];
    ledi[i] = readLedi(a);
    a.calibrated = ledi[i] > 0 && ledi[i] < LEDI_UNCALIBRATED;
    a.link = pollAxis(a);                 // also discards any stale count
    a.pos = 0;
  }
  lastHostMs = millis();
  Serial.print(F("OK mount_controller v2 calibrated="));
  Serial.print(axes[0].calibrated && axes[1].calibrated ? 1 : 0);
  Serial.print(F(" ledi_az=")); Serial.print(ledi[0]);
  Serial.print(F(" ledi_alt=")); Serial.print(ledi[1]);
  Serial.print(F(" boot_resync=")); Serial.print((unsigned int)BOOT_RESYNC);
  Serial.print(F(" linkaz=")); Serial.print(axes[0].link);
  Serial.print(F(" linkalt=")); Serial.println(axes[1].link);
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) { lineBuf[lineLen] = 0; handleLine(lineBuf); lineLen = 0; }
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    }
  }
  unsigned long now = millis();
  if ((axes[0].cps != 0 || axes[1].cps != 0) && now - lastHostMs > WATCHDOG_MS) {
    stopAll("watchdog");
  }
  // Auto-recovery from a wedged bus (see header).
  bool down = !axes[0].link || !axes[1].link;
  bool still = axes[0].cps == 0 && axes[1].cps == 0;
  if (!down) linkDownSinceMs = 0;
  else if (linkDownSinceMs == 0) linkDownSinceMs = now;
  if (down && still && now - linkDownSinceMs >= AUTO_RESYNC_AFTER_MS &&
      now - lastResyncMs >= AUTO_RESYNC_EVERY_MS) {
    lastResyncMs = now;
    resyncBus();
    refreshAfterResync();
    Serial.print(F("EVT resync linkaz=")); Serial.print(axes[0].link);
    Serial.print(F(" linkalt=")); Serial.println(axes[1].link);
    now = millis();
  }
  if (now - lastPollMs >= POLL_MS) {
    lastPollMs = now;
    Axis &a = axes[pollIndex];
    pollIndex ^= 1;
    bool wasUp = a.link;
    bool answered = pollAxis(a);
    if (!wasUp && a.link) {                 // board came (back) up: make sure it's still
      if (a.cps == 0) sendCommand(a.clk, CMD_STOP);
      uint8_t ledi = readLedi(a);           // and refresh its calibration state
      if (ledi > 0) a.calibrated = ledi < LEDI_UNCALIBRATED;
      Serial.print(F("EVT link ")); Serial.print(a.name); Serial.println(F(" up"));
    }
    bool inGrace = millis() - a.stoppedMs < STOP_GRACE_MS;
    if (answered && a.cps == 0) {
      a.movingPolls = abs(a.lastDelta) > UNCMD_COUNTS ? a.movingPolls + 1 : 0;
    }
    if (answered && a.cps == 0 && !inGrace && (a.pwm > 0 || a.movingPolls >= 2)) {
      bool ok = sendCommand(a.clk, CMD_STOP);
      if (!a.uncommanded) {
        Serial.print(F("EVT uncommanded ")); Serial.print(a.name);
        Serial.print(F(" delta=")); Serial.print(a.lastDelta);
        Serial.print(F(" pwm=")); Serial.print(a.pwm);
        Serial.println(ok ? F(" stop sent") : F(" STOP NOT ACKNOWLEDGED - CUT POWER"));
      }
      a.uncommanded = true;
    } else if (answered && a.cps == 0) {
      a.uncommanded = false;
    }
    if (wasUp && !a.link) {
      bool moving = axes[0].cps != 0 || axes[1].cps != 0;
      if (moving) stopAll(nullptr);
      Serial.print(F("EVT link ")); Serial.print(a.name);
      Serial.println(moving ? F(" down; stopped all") : F(" down"));
    }
    if (a.cps != 0 && beyondLimit(a)) {
      bool ok = stopAxis(a);
      Serial.print(F("EVT limit ")); Serial.print(a.name);
      Serial.print(F(" pos=")); Serial.print(a.pos);
      Serial.println(ok ? F(" stopped") : F(" STOP NOT ACKNOWLEDGED - CUT POWER"));
    }
  }
}
