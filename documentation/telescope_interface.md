# StarNavigator NG 102 + AudioStar: interface reference

Every way to get commands into and information out of the telescope, what each does,
and how sure we are. Status key:

| Status | Meaning |
|---|---|
| **verified** | Tested on our AudioStar (firmware A4S2) and behaved as described. |
| **no reply** | Tested; the handbox sent nothing back. |
| **doc** | In Meade's protocol doc [1][2] but not yet tested by us. |
| **community** | From community sources only; untested by us. |

Experimental evidence is in [hardware_findings.md](hardware_findings.md).

## 1. Physical interfaces

| Port | Where | What it carries | Our use |
|---|---|---|---|
| Handbox RS-232 jack (RJ, 4-pin) | Bottom of the AudioStar | LX200 ASCII protocol, 9600 8N1. Wiring: [IMG_3502.gif](IMG_3502.gif) | **Current control link** (FTDI cable, `/dev/cu.usbserial-A9IDYQVP`) |
| HBX port | Base control panel ("A") | Handbox ↔ motor boards. Per-axis clock + data lines; the handbox is bus master [3][5] | Future: drive the motors without the handbox (§5) |
| AUX port | Base control panel ("D") | Accessory bus: "modified I2C", 4 pins, battery and ground on the outer pins [3]. **Not RS-232**: serial levels can damage it [4] | None. It does not answer LX200 (verified) |
| 12 V | Base control panel ("E") | Power | Bench power |

The handbox contains the LX200 interpreter, goto computer, pointing model and clock.
The base contains only motor controllers. This was verified: with the handbox removed,
nothing in the base answers LX200.

## 2. Outputs: what the handbox reports (LX200 queries)

Replies end in `#` unless noted. The degree separator is byte `0xDF`; seconds follow `:`.

### Pointing and motion state
| Command | Returns | Example (ours) | Status |
|---|---|---|---|
| `:GZ#` | Azimuth `DDD°MM:SS` | `359ß58:01#` | verified; updates during motion |
| `:GA#` | Altitude `sDD°MM:SS` | `+00ß00:00#` | verified; **stays at 0 while unaligned** even though the mount physically tilts |
| `:GR#` | RA `HH:MM:SS` (derived from alt/az, site and time) | `00:00:00#` | verified (meaningless unaligned) |
| `:GD#` | Dec `sDD°MM:SS` (derived) | `+00ß00:00#` | verified |
| `:D#` | Slewing indicator: a bar character while slewing, empty when idle. The bar is `0x7F` per [7] | `#` | verified (idle only) |
| `:GW#` | 3 chars, **no `#`**: mount (A/P/G), tracking (T/N), stars aligned (0–3) | `AN0` | verified |
| ACK `0x06` | Single char mode: A alt-az, L land, P polar, D downloader | `A` | verified |

These positions are the handbox's estimate, which most likely comes from encoders on
the motor shafts (§5). They cannot reveal gear backlash, skipped teeth or clutch slip.
**There is no LX200 output for raw encoder counts, motor current/PWM, temperature,
limit switches or focuser position.**

### Clock, site and settings
| Command | Returns | Ours | Status |
|---|---|---|---|
| `:GL#` / `:Ga#` | Local time, 24 h / 12 h | `20:49:06#` / `08:48:58#` | verified (clock unset: date 2019) |
| `:GC#` | Date `MM/DD/YY` | `01/17/19#` | verified |
| `:Gc#` | Clock format | `(24)#` | verified |
| `:GS#` | Local sidereal time | `00:48:02#` | verified |
| `:GG#` | Hours to add to local time to get UTC | `+08#` | verified |
| `:GH#` | DST flag, **no `#`** | `0` | verified |
| `:Gt#` / `:Gg#` | Latitude / longitude (west-positive) | `+34ß03:00#` / `118ß14:00#` | verified |
| `:GM#`…`:GP#` | Site 1–4 names | | verified |
| `:Gh#` / `:Go#` | High / low slew limit | `71ß#` / `-05ß#` | verified |
| `:GT#` | Tracking frequency (Hz, 60.0 = sidereal model) | `+60.1#` | verified |
| `:Gr#` / `:Gd#` | Target RA / Dec | | verified |
| `:GVP#` `:GVN#` `:GVD#` `:GVT#` | Product, firmware, firmware date/time | `Autostar`, `A4S2`, `Sep 21 2018`, `23:35:48` | verified |
| `:Gb#` `:Gf#` `:GF#` `:Gl#` `:Gs#` `:Gq#` `:Gy#` `:Lf#` `:LI#` `:G0-2#` | Catalog search settings / static strings | | verified (not useful) |
| `:GpB#` `:GpH#` `:GpS#` `:h?#` `:FB#` `:Fp#` `:fT#` `:fC#` `:GE#` `:Ge#` `:Gm#` `:??#` | Backlash, home, sensors, focuser, temperature, Moon, meridian, help | | **no reply** |

## 3. Inputs: commands to the handbox

### Motion (used by roboscope)
| Command | Effect | Status |
|---|---|---|
| `:RG#` `:RC#` `:RM#` `:RS#` | Select rate for `:M` moves. Measured in azimuth: RG ≈0.0036 °/s, RC ≈0.37 °/s, RM ≈0.71 °/s; RS untested | verified (RS doc) |
| `:Me#` `:Mw#` | Move azimuth until stopped. East **decreases** `:GZ` | verified |
| `:Mn#` `:Ms#` | Move altitude: **north = up**. ~0.7 °/s at `:RM` (by eye on the side scale). `:GA` doesn't report it | verified |
| `:Qe#` `:Qw#` `:Qn#` `:Qs#` / `:Q#` | Stop one direction / stop everything | verified |
| `:Mg<n/s/e/w><ms 4 digits>#` | Guide pulse; no reply. Lowercase direction, guide rate 67% sidereal on the #497 [6] | sent; **no motion seen** |
| `:R1#`…`:R9#` | Select handbox speeds 1–9 directly. *(source inaccessible, unverified; see Inaccessible sources)* | community |

### Goto, sync and site/time (handbox logic; roboscope avoids these)
| Command | Effect / reply | Status |
|---|---|---|
| `:Sr HH:MM:SS#` `:Sd sDD*MM:SS#` | Set target RA/Dec → `1`/`0` | doc |
| `:MS#` | Goto target → `0`, or `1`/`2` + message `#` | doc |
| `:Sa sDD*MM#` `:Sz DDD*MM#` `:MA#` | Alt-az target and goto. On the #495 `:Sa` "will *always* respond with '1'" [7] | doc/community |
| `:CM#` | Sync to target → string | doc |
| `:St` `:Sg` `:SG` `:SL` `:SC` `:W<n>#` | Site, time, date (`:SC` replies `1` + two strings) | doc |
| `:U#` | Toggle high/low precision (ours starts high) | doc |
| `:T+#` `:T-#` `:TQ#` `:TL#` | Tracking rate adjust / sidereal / lunar | doc |
| `:hP#` | Park | doc |
| `:So` `:Sh` | Low / high slew limit | doc |

### Handbox emulation (community)
| Command | Effect | Status |
|---|---|---|
| `:EK<code>#` | Inject a key press (decimal code): 13 enter, 10 enter-held, 9 mode, 11 mode-held, 71 goto, 25 goto-held, 68 scroll down, 85 scroll up, 63 help, 48–57 digits, 87 left/W, 69 right/E, 78 up/N, 83 down/S [8] | community |
| `:ES<n>#` | Jump to a menu node, e.g. `:ES11034#` = Setup/Targets on #497 firmware v41Ec–43Eg [8] | community; firmware-specific |
| Ctrl-D / `0x04` | Enter the firmware downloader. The doc calls this "propriety" [1]. **Do not use** | doc |

## 4. Research: can we get more out of it?

What people have found, and whether it applies to us:

1. **Memory peek through the RS-232 link.** Andrew Johansen's MyScope app reads raw
   encoder positions "by 'peeking' the data", logged as `:EP<address>#` commands. This
   "only becomes available if you run patched firmware" [10]. He also says: "I would
   need to know the firmware being used to see what the actual vars are" [10].
   - A logged example: `:EP33554496#` → "Peek x02 bytes from x00-0040 = x3C37#" [10].
   - The same log shows an undocumented `:EQ<node>#` that walks the handbox menu tree,
     e.g. `:EQ11098#` → "11114 0 Az/RA Ratio#" [10].

   Our stock A4S2 is unpatched, so `:EP` most likely isn't available. Getting it means
   reflashing the handbox, which ties us to it more. **Not pursuing.**
2. **Handbox debugger, patches, firmware choice.** A `:T#` "In Debugger>" hook [7] and
   StarPatch ROM patches [9] exist for older handsets. StarPatch lists Audiostar A4S1
   and earlier as having a bug in a set command ("set latitude" or "set date") [9].
   On firmware, Johansen says:
   - A3S4 "was introduced by Meade mainly to get the LX80 working. They massively
     changed how it works for tracking, guiding and PEC" [11]. Ours, A4S2, is later
     still. That may explain why `:Mg` did nothing.
   - There are "2 types of Audiostar (they use different memory chips)". Loading the
     wrong firmware can block future updates [11].

   **Not pursuing:** reflashing is risky and deepens reliance on the handbox.
3. **The motor bus behind the HBX port. This is the real source of information.** The
   open EQMOD-ETX project [5] drives Meade ETX-60AT motor boards directly from an ESP32
   or Arduino, with no handbox. Its source documents the command set (§5). The motor
   boards report encoder tick counts, motor PWM and an encoder-error flag. None of that
   is visible through LX200. This fits the "rip out the handbox" goal exactly.
   EQMOD-ETX was developed against ETX-60 motors and the #494 handbox.

   A second, independent source agrees, for the DS-series mounts (the NG's ancestors).
   Johansen writes: "The encoder is read on the motor card and a simple count is then
   sent back on the cables. Only 2 wires are used for ALL the comms and this uses a
   proprietary I2C type protocol" [10]. He also refers to "the x08 comms (encoder
   count)" [10], which matches EQMOD-ETX's `0x08` GetStatus.

   The NG's motor boards are still unconfirmed, but the evidence now points the same way
   from two sources.

## 5. Motor bus protocol (from EQMOD-ETX source [5] and Johansen [10]; not verified on the NG)

**Bus behavior, as Johansen describes it** [10]:
- "Only the Hbx ever drives the clock."
- Acknowledge: "Both clock and data are pulled high, The Hbx sets the data line to
  input and pulls down the clock. If a motor card is listening, it pulls the data line
  low."
- On his DS mount the bus "uses a common data line and 2 clocks", so per-axis clocks
  share one data line.
- The AUX bus "uses the same bitbanging, but ... doesnt use an 'Ack' as such."

**Encoder and gearing (DS-series figures from Johansen; the NG is unconfirmed)** [10]:
- "The mount has a 60 tooth worm wheel, so 6deg per tooth."
- "The motor has a 36 vane encoder with 1.369 encoder transitions per arcsec ie 1 rev
  of the motor gives 36 * 4 = 144 transitions = 105 arcsecs."

So one count is about 0.73″ at the axis. That's very fine resolution, but measured at
the motor, before the worm, clutch and bearings. Backlash and slip stay invisible.

**Handbox EEPROM settings** (listed by Johansen): Alt/Az Ratio, Alt/Az Train, and Az/Dec
"Calibrate", which "will be the encoder LED values (and these are used to set the
encoder LED brightness)" [10]. A sample dump shows "Alt Ratio 3FAF37C0 +1.36888885"
[10]. If we replace the handbox, our controller must do this LED calibration itself
(`0x03`/`0x04` below).

Each axis has its own two-wire bus: a clock driven by the master and a bidirectional
data line. In EQMOD-ETX the azimuth bus is on HBX "Pin7" and altitude on "Pin5". The
bit time is ~200 µs (100 µs low/high), MSB first. A transaction is: master pulls clock
low; the motor board acknowledges by pulling data low; master raises the clock; the
board releases data; then the command byte and its argument or reply bytes follow.

| Opcode | Name | Data | Meaning |
|---|---|---|---|
| `0x00` | SpeedChnge | out "8.16" | Change speed (8-bit + 16-bit fields) |
| `0x01` | SpeedStart | out "8.16" | Start moving at speed |
| `0x02` | SetOffset | out 16-bit (max 0x0020) | Position correction offset |
| `0x03` / `0x09` | SetLEDI / GetLEDI | 8-bit | Encoder LED current |
| `0x04` | CalibrateLED | none | Calibrate encoder LED |
| `0x05` | Stop | none | Stop motor |
| `0x06` / `0x07` | SlewReverse / SlewForward | none | Full-speed slew |
| `0x08` | **GetStatus** | in "16.8.1" (4 bytes read) | **Encoder ticks (16-bit), PWM (8-bit), error flag** |
| `0x0B` | GetMotorType | in 8-bit | Motor type |
| `0xE4` | Sleep | none | Sleep |

Constants in the source:
- "MeadeSidereal = 6460.0900", credited to Andrew Johansen.
- ETX slew speed table: speed 8 = 2.5 °/s and speed 9 = 4.5 °/s. These differ from the
  NG manual's 1.5 °/s and "max".

The README says the project is "experimental and does not claim to be a specification
of the protocol." The repository has no license file, so we can learn from it but
should not copy its code.

**How to confirm on our mount:**
- Record the real AudioStar talking to the NG base with a cheap logic analyzer
  clipped onto the HBX lines. Passive, so no risk to the mount. Johansen: "You can get
  one for about $10 these days, and the cheap knockoffs of the Salae units have good
  software" [10]. The capture app is PulseView (sigrok); he has a decoder app for its
  `*.sr` files [10].
- Compare the traffic to the table above.
- If it matches, a microcontroller (e.g. Pi Pico with level shifting) can replace the
  handbox entirely.

## References
1. Meade, *Telescope Serial Command Protocol* rev. 2010.10. Local:
   `documentation/TelescopeProtocol_2010-10.pdf`
2. Meade, *Telescope Serial Command Protocol* rev. L (2002). Local:
   `documentation/LX200CommandSet.pdf`
3. Weasner ETX site, "AUX port" tech tip (2007): R. Seymour, B. Kauffman.
   http://www.weasner.com/etx/techtips/2007/aux-port.html
4. Weasner ETX site, #494/#506 Q&A (2004): R. Seymour.
   https://www.weasner.com/etx/autostar/2004/494-506.html
5. J. Archbold, EQMOD-ETX (GitHub; no license file). Command and timing definitions in
   `Software/EQG2HBX/EQG2HBXE32/src/ETXProtocol.h`, `HBXComms.h`, `HBXComms.ino`.
   https://github.com/ozarchie/EQMOD-ETX
6. Weasner ETX site, Autostar feedback, Nov 2008: R. Seymour on `:Mg`.
   http://www.weasner.com/etx/archive/nov08/autostar.html
7. Weasner ETX site, Autostar commands (2000): R. Seymour, S. Weed on `:Sa/:Sz/:MA`,
   `:hS/:hF/:hP`, `:T#` debugger, `:D#` = 0x7F (#495, v2.0i).
   http://www.weasner.com/etx/autostar/as_commands.html
8. Weasner ETX site, Autostar commands (2008–2010): `:EK` codes (Seymour via M. Kruse),
   `:ES` menu sequence (J. Farrant). http://www.weasner.com/etx/autostar/2008/commands.html
9. PixSoft StarGPS/StarPatch manual. https://www.stargps.ca/manual.htm
10. Cloudy Nights, "Meade DS Motors / encoders bad?" (rsworden, OzAndrewJ = Andrew
    Johansen; Feb–Mar 2021). Local:
    `documentation/websites/Meade DS Motors : encoders bad? - Meade Computerized Telescopes - Cloudy Nights.pdf`.
    https://www.cloudynights.com/forums/topic/755759-meade-ds-motors-encoders-bad/
11. Cloudy Nights, "Question on AUDIOstar controller firmware" (mjeanneau, OzAndrewJ;
    June 2016). Local:
    `documentation/websites/Question on AUDIOstar controller firmware - Meade Computerized Telescopes - Cloudy Nights.pdf`.
    https://www.cloudynights.com/forums/topic/539432-question-on-audiostar-controller-firmware/

## Inaccessible sources (consider obtaining)
- **R. Seymour, "Meade Autostar / Motor Protocol Information"**
  http://rseymour.home.wolfenet.com/autostar_mtr_2.html (connection refused; archive
  copies blocked). Relied on for nothing yet. Reportedly documents the motor status
  read (16-bit relative position, 8-bit PWM, encoder error flag, counter reset after
  each read). This would independently confirm §5. Worth opening in your browser.
- **`:R1#`–`:R9#` direct speed selection.** Attributed to R. Seymour in a search
  summary, but not found on the page we read [6]. Easy to test on hardware: it sets a
  rate without moving.
