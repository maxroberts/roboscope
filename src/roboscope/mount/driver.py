"""Typed control of the AudioStar handbox: positions, gotos, sync, manual motion, site/time.

Details:
    `Mount` is the API the rest of roboscope uses to talk to the telescope. It turns
    typed requests into LX200 commands via `protocol` and sends them over a
    `transport.Transport`. It holds no pointing model: the handbox does gotos and
    tracking, and higher layers (planned `align`) correct it with `sync_radec` and
    `pulse_guide`.

    Command coverage is limited to what the protocol doc marks as Autostar-supported.
    The exceptions are marked *unverified*: they come from community sources or the
    earlier code, and Stage 1 hardware probing must confirm them.
      - `pulse_guide` (`:Mg`): community report says firmware 30Eb+; no effect seen on A4S2.
      - `press_key` (`:EK`): codes from the earlier telescope.py.

    Not included on purpose:
      - Autostar II-only rate/time commands (`:RA`, `:RE`, `:hI`).
      - Backlash `:GpB`/`:SpB`: no reply from AudioStar A4S2 (2026-11-24).
      - Focus: focus is manual.

    Moving commands do NOT check Sun or horizon safety. A planned `safety` layer must sit
    above this module.

    Time and daylight saving: the handbox computes UTC from local time and its UTC offset.
    It also has a DST flag that this protocol cannot set on an Autostar. `set_datetime`
    sends standard-time offset and local wall-clock time, which is only right if the
    handbox's DST flag matches. *TODO (Stage 1):* check by comparing `sidereal_time()`
    with a computed local sidereal time.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from roboscope.mount import protocol
from roboscope.mount.transport import MountError, Transport

log = logging.getLogger(__name__)


class CommandRejected(MountError):
    """The handbox answered a set/goto command with a failure code."""


@dataclass(frozen=True)
class AltAz:
    """Horizontal coordinates in degrees (azimuth from north through east)."""
    alt_deg: float
    az_deg: float


@dataclass(frozen=True)
class RaDec:
    """Equatorial coordinates of date: RA in hours, Dec in degrees."""
    ra_hours: float
    dec_deg: float


@dataclass(frozen=True)
class Site:
    """Observing site: latitude north-positive, longitude east-positive, in degrees."""
    lat_deg: float
    lon_deg: float


@dataclass(frozen=True)
class MountInfo:
    """Identification strings reported by the handbox (`:GVP/:GVN/:GVD/:GVT`)."""
    product: str
    firmware: str
    firmware_date: str
    firmware_time: str


class Direction(Enum):
    """Manual-motion directions; values are the LX200 letters.

    East/west move azimuth: on our mount (2026-11-24), EAST decreases the `:GZ`
    readback and WEST increases it. NORTH tilts the tube up and SOUTH down (~0.7 °/s
    at FIND, judged by eye on the side scale), but the unaligned handbox keeps `:GA`
    at 0 (see
    documentation/hardware_findings.md).
    """
    NORTH = "n"
    SOUTH = "s"
    EAST = "e"
    WEST = "w"


class SlewRate(Enum):
    """The four rates the LX200 protocol can select (`:RG/:RC/:RM/:RS`).

    How these map onto the handbox's nine speeds is to be measured in Stage 1.
    """
    GUIDE = "G"
    CENTER = "C"
    FIND = "M"
    MAX = "S"


# :EK keycodes, from the earlier telescope.py docstring (decimal ASCII codes).
# *Unverified* on this firmware.
KEYCODES = {
    "enter": 13, "mode": 9, "goto": 71, "scroll_up": 85, "scroll_down": 68, "help": 63,
    "left": 87, "right": 69, "up": 78, "down": 83,
    **{str(n): 48 + n for n in range(10)},
}


class Mount:
    """High-level handbox control over one `Transport`.

    Usable as a context manager. Exiting closes the port but does NOT halt motion;
    call `halt()` yourself if needed.
    """

    def __init__(self, transport: Transport):
        self.t = transport

    @classmethod
    def connect(cls, device: str, **transport_kwargs) -> "Mount":
        """Open `device`, confirm the handbox answers, and switch to high precision.

        Raises:
            MountTimeout: nothing answered the ACK ping.
        """
        mount = cls(Transport.open(device, **transport_kwargs))
        mode = mount.ping()
        log.info("handbox answered ACK with mode %r", mode)
        mount.ensure_high_precision()
        return mount

    def close(self) -> None:
        """Close the serial port."""
        self.t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- identity and status --

    def ping(self) -> str:
        """Send ACK (0x06). Returns the mount mode: 'A' alt-az, 'L' land, 'P' polar,
        'D' downloader."""
        with self.t.lock:
            self.t.send(protocol.ACK)
            return self.t.read_char()

    def info(self) -> MountInfo:
        """Product name and firmware version/date/time."""
        q = self.t.query_string
        return MountInfo(q("GVP"), q("GVN"), q("GVD"), q("GVT"))

    def ensure_high_precision(self) -> None:
        """Make position replies include seconds.

        `:U#` only toggles, so this reads RA first. A low-precision reply ('HH:MM.T')
        has a '.', so toggle once and re-check.
        """
        if "." in self.t.query_string("GR"):
            self.t.command("U")
            if "." in self.t.query_string("GR"):
                raise MountError("could not switch handbox to high precision")

    def alignment_status(self) -> str:
        """`:GW#` status: 3 characters, <mount A/P/G><tracking T/N><stars aligned 0-3>.

        Verified on AudioStar A4S2 (2026-11-24): the reply is 3 characters with NO '#'
        terminator, contrary to the doc. e.g. 'AN0' = alt-az, not tracking, unaligned.
        """
        with self.t.lock:
            self.t.send(protocol.command("GW"))
            return "".join(self.t.read_char() for _ in range(3))

    # -- position --

    def radec(self) -> RaDec:
        """Where the handbox thinks the scope points, in RA/Dec of date."""
        ra = protocol.parse_sexagesimal(self.t.query_string("GR"))
        dec = protocol.parse_sexagesimal(self.t.query_string("GD"))
        return RaDec(ra, dec)

    def altaz(self) -> AltAz:
        """Where the handbox thinks the scope points, in alt/az.

        Likely derived from motor-shaft encoders, so it cannot see backlash or clutch
        slip. On our unaligned AudioStar A4S2, azimuth updates with motion but altitude
        stays at 0 even when the mount tilts (2026-11-24). Don't use `alt_deg` as
        feedback.
        """
        alt = protocol.parse_sexagesimal(self.t.query_string("GA"))
        az = protocol.parse_sexagesimal(self.t.query_string("GZ"))
        return AltAz(alt, az)

    def is_slewing(self) -> bool:
        """True while a goto is in progress.

        `:D#` returns a bar character during a slew and an empty string once it's done.
        """
        return len(self.t.query_string("D")) > 0

    # -- site and time --

    def site(self) -> Site:
        """Current site latitude/longitude (east-positive)."""
        lat = protocol.parse_sexagesimal(self.t.query_string("Gt"))
        lon = protocol.parse_longitude(self.t.query_string("Gg"))
        return Site(lat, lon)

    def set_site(self, site: Site) -> None:
        """Write latitude and longitude into the handbox's current site.

        Raises:
            CommandRejected: the handbox refused a value.
        """
        self._set("St" + protocol.format_latitude(site.lat_deg))
        self._set("Sg" + protocol.format_longitude(site.lon_deg))

    def utc_offset(self) -> float:
        """Handbox UTC offset in hours, ISO sign (PST = -8)."""
        return protocol.parse_meade_utc_offset(self.t.query_string("GG"))

    def local_datetime(self) -> datetime:
        """Handbox local date and time (naive datetime)."""
        t = self.t.query_string("GL")
        d = self.t.query_string("GC")
        return datetime.strptime(f"{d} {t}", "%m/%d/%y %H:%M:%S")

    def sidereal_time(self) -> float:
        """Handbox local sidereal time in hours, for checking time/site/DST setup."""
        return protocol.parse_sexagesimal(self.t.query_string("GS"))

    def set_datetime(self, local: datetime, utc_offset_hours: float) -> None:
        """Set UTC offset, local time, then local date.

        `:SC` makes the handbox recompute planetary positions. It replies '1' followed
        by two '#'-terminated strings, and can take several seconds. All of that is
        consumed here so it can't corrupt the next reply.

        Raises:
            CommandRejected: a value was refused.
        """
        self._set("SG" + protocol.format_meade_utc_offset(utc_offset_hours))
        self._set("SL" + protocol.format_time(local.time()))
        with self.t.lock:
            self.t.send(protocol.command("SC" + protocol.format_date(local.date())))
            if self.t.read_char() != "1":
                raise CommandRejected(f"date {local.date()} rejected")
            self.t.read_string(timeout_s=30)  # "Updating Planetary Data"
            self.t.read_string(timeout_s=30)  # blank line

    # -- goto and sync --

    def goto_radec(self, target: RaDec) -> None:
        """Start a goto to RA/Dec. Returns once accepted; poll `is_slewing()`.

        Raises:
            CommandRejected: target refused (e.g. below horizon), with the handbox's
                message.
        """
        self._set_target(target)
        with self.t.lock:
            self.t.send(protocol.command("MS"))
            code = self.t.read_char()
            if code != "0":
                raise CommandRejected(f"goto refused ({code}): {self.t.read_string()}")

    def goto_altaz(self, target: AltAz) -> None:
        """Start a goto to alt/az (`:Sa` + `:Sz` + `:MA`).

        `:Sz` is documented for "LX 16\" and Autostar II only", so this may be rejected
        on the AudioStar. *Unverified.*
        """
        self._set("Sa" + protocol.format_alt(target.alt_deg))
        az = round((target.az_deg % 360.0) * 60) % (360 * 60)
        self._set(f"Sz{az // 60:03d}*{az % 60:02d}")
        if self.t.query_char("MA") != "0":
            raise CommandRejected("alt/az goto refused")

    def sync_radec(self, actual: RaDec) -> str:
        """Tell the handbox it is actually pointing at `actual` (from a plate solve).

        Sets the target to `actual`, then `:CM#`. Returns the handbox's reply string.
        """
        self._set_target(actual)
        return self.t.query_string("CM")

    def _set_target(self, target: RaDec) -> None:
        self._set("Sr" + protocol.format_ra(target.ra_hours))
        self._set("Sd" + protocol.format_dec(target.dec_deg))

    # -- manual motion --

    def set_slew_rate(self, rate: SlewRate) -> None:
        """Select the rate used by `move()`."""
        self.t.command("R" + rate.value)

    def move(self, direction: Direction) -> None:
        """Start moving at the current rate until `stop()` or `halt()`."""
        self.t.command("M" + direction.value)

    def stop(self, direction: Direction) -> None:
        """Stop motion in one direction."""
        self.t.command("Q" + direction.value)

    def halt(self) -> None:
        """Stop all motion, including gotos (`:Q#`)."""
        self.t.command("Q")

    def pulse_guide(self, direction: Direction, ms: int) -> None:
        """Move at guide rate for `ms` milliseconds (0–9999); there is no reply.

        Observed on AudioStar A4S2, unaligned (2026-11-24): NO motion in readback.
        See documentation/hardware_findings.md.
        """
        if not 0 <= ms <= 9999:
            raise ValueError("pulse length must be 0–9999 ms")
        self.t.command(f"Mg{direction.value}{ms:04d}")

    def park(self) -> None:
        """Slew to the handbox's park position (`:hP#`)."""
        self.t.command("hP")

    def press_key(self, key: str) -> None:
        """Emulate a handbox key press (`:EK<code>#`). Keys are listed in `KEYCODES`.
        *Unverified.*"""
        self.t.command(f"EK{KEYCODES[key]}")

    # -- helpers --

    def _set(self, body: str) -> None:
        """Send a set-command whose reply is '1' (accepted) or '0' (rejected)."""
        reply = self.t.query_char(body)
        if reply != "1":
            raise CommandRejected(f":{body}# rejected (reply {reply!r})")
