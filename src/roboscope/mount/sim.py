"""Simulated AudioStar that speaks the LX200 protocol, for tests without hardware.

Details:
    `SimulatedAutostar` implements the parts of the pyserial `Serial` interface that
    `transport.Transport` uses. It parses each command written to it and queues the
    reply bytes the doc says a real handbox would send. Tests (and later, dry runs of
    higher layers) plug it in with `Mount(Transport(SimulatedAutostar(), min_gap_s=0))`.

    Fidelity is protocol-level only:
      - There is no astronomy and no motion over time. RA/Dec and Alt/Az are independent
        stored values. A goto jumps straight to the target after reporting "slewing" to
        one `:D#` poll.
      - Unknown commands get no reply, which is what a real handbox does for commands it
        doesn't implement. They're recorded in `unknown` so tests can assert on them.
      - Like the real handbox in low precision, it starts that way and toggles on `:U#`.
      - Reply formats match what AudioStar firmware A4S2 actually sent on 2026-11-24:
        0xDF degree byte, ':' before seconds, `:GW#` with no '#'.
        `:GpB#` gets no reply.
    Its behavior encodes our reading of the doc. Where the hardware turns out to differ,
    update this file so the tests stay honest.

    Test hooks: `goto_refusal` (make `:MS` fail with a message), `silent` (stop replying
    entirely), `sent` (every command received, in order).
"""

import re
from datetime import datetime

from roboscope.mount import protocol

DEG = "\xdf"  # Autostar's degree-sign byte (latin-1 'ß').


class SimulatedAutostar:
    """In-memory stand-in for a serial port connected to an AudioStar."""

    def __init__(self):
        self.timeout = 1.0
        self.is_open = True
        self.high_precision = False
        self.ra_hours, self.dec_deg = 5.5, 20.0
        self.alt_deg, self.az_deg = 0.0, 0.0
        self.target_ra, self.target_dec = 0.0, 0.0
        self.target_alt, self.target_az = 0.0, 0.0
        self.lat_deg, self.lon_west_deg = 37.0, 122.0
        self.meade_utc_offset = 8.0
        self.local = datetime(2026, 11, 24, 19, 32, 0)
        self.slew_polls_left = 0
        self.goto_refusal: str | None = None
        self.silent = False
        self.sent: list[str] = []
        self.unknown: list[str] = []
        self._out = bytearray()
        self._in = ""

    # -- pyserial-compatible surface --

    def write(self, data: bytes) -> int:
        """Accept command bytes; complete commands are handled immediately."""
        self._in += data.decode("latin-1")
        while self._in:
            if self._in[0] == "\x06":
                self._in = self._in[1:]
                self.sent.append("\x06")
                self._reply("A")
                continue
            end = self._in.find("#")
            if end < 0:
                break
            cmd, self._in = self._in[:end].lstrip(":"), self._in[end + 1:]
            self.sent.append(cmd)
            self._handle(cmd)
        return len(data)

    def read(self, n: int = 1) -> bytes:
        out, self._out = bytes(self._out[:n]), self._out[n:]
        return out

    def read_until(self, expected: bytes = b"\n") -> bytes:
        i = self._out.find(expected)
        n = len(self._out) if i < 0 else i + len(expected)
        return self.read(n)

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    # -- command handling --

    def _reply(self, text: str) -> None:
        if not self.silent:
            self._out += text.encode("latin-1")

    def _angle(self, value: float, width: int = 2, signed: bool = True) -> str:
        d, m, s = protocol._dms(value)
        sign = ("-" if value < 0 else "+") if signed else ""
        tail = f":{s:02d}" if self.high_precision else ""
        return f"{sign}{d:0{width}d}{DEG}{m:02d}{tail}#"

    def _ra(self) -> str:
        if self.high_precision:
            return protocol.format_ra(self.ra_hours) + "#"
        tenths = round(self.ra_hours % 24 * 600)
        return f"{tenths // 600:02d}:{tenths // 10 % 60:02d}.{tenths % 10}#"

    def _handle(self, cmd: str) -> None:
        r = self._reply
        fixed = {
            "GVP": "Autostar#", "GVN": "43Eg#", "GVD": "Jan 01 2016#",
            "GVT": "12:00:00#", "CM": " M31 EX GAL MAG 3.5 SZ178.0'#",
        }
        if cmd in fixed:
            r(fixed[cmd])
        elif cmd == "GR":
            r(self._ra())
        elif cmd == "GD":
            r(self._angle(self.dec_deg))
        elif cmd == "GA":
            r(self._angle(self.alt_deg))
        elif cmd == "GZ":
            r(self._angle(self.az_deg, width=3, signed=False))
        elif cmd == "Gt":
            r(self._angle(self.lat_deg))
        elif cmd == "Gg":
            r(self._angle(self.lon_west_deg, width=3, signed=False))
        elif cmd == "GG":
            r(f"{self.meade_utc_offset:+03.0f}#")
        elif cmd == "GL":
            r(self.local.strftime("%H:%M:%S#"))
        elif cmd == "GC":
            r(self.local.strftime("%m/%d/%y#"))
        elif cmd == "GS":
            r("01:02:03#")
        elif cmd == "GW":
            r("AN0")  # real A4S2 reply: no '#' terminator
        elif cmd == "U":
            self.high_precision = not self.high_precision
        elif cmd == "D":
            r("\x7f#" if self.slew_polls_left else "#")
            self.slew_polls_left = max(0, self.slew_polls_left - 1)
        elif cmd[:2] in ("Sr", "Sd", "Sa", "Sz", "St", "Sg", "SG", "SL"):
            r("1" if self._set(cmd[:2], cmd[2:]) else "0")
        elif cmd.startswith("SC"):
            try:
                d = datetime.strptime(cmd[2:], "%m/%d/%y").date()
            except ValueError:
                r("0")
            else:
                self.local = datetime.combine(d, self.local.time())
                r("1Updating Planetary Data#                                #")
        elif cmd == "MS":
            if self.goto_refusal:
                r(f"1{self.goto_refusal}#")
            else:
                self.ra_hours, self.dec_deg = self.target_ra, self.target_dec
                self.slew_polls_left = 1
                r("0")
        elif cmd == "MA":
            self.alt_deg, self.az_deg = self.target_alt, self.target_az
            r("0")
        elif re.fullmatch(r"M[nsew]|Mg[nsew]\d{4}|Q[nsew]?|R[GCMS]|hP|EK\d+", cmd):
            pass  # motion commands: accepted silently (no motion simulated)
        else:
            self.unknown.append(cmd)

    def _set(self, kind: str, arg: str) -> bool:
        """Apply a set-command; return False if the argument doesn't parse."""
        try:
            if kind == "SG":
                self.meade_utc_offset = float(arg)
            elif kind == "SL":
                t = datetime.strptime(arg, "%H:%M:%S").time()
                self.local = datetime.combine(self.local.date(), t)
            else:
                value = protocol.parse_sexagesimal(arg)
                attr = {"Sr": "target_ra", "Sd": "target_dec", "Sa": "target_alt",
                        "Sz": "target_az", "St": "lat_deg", "Sg": "lon_west_deg"}[kind]
                setattr(self, attr, value)
        except ValueError:
            return False
        return True
