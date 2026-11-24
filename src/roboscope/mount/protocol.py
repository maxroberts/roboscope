"""LX200/Autostar wire formats: build command strings and parse replies.

Details:
    Pure functions with no I/O. `driver.Mount` uses them to turn typed values (degrees,
    hours, datetimes) into the ASCII the AudioStar expects, and to parse its replies.
    Keeping this separate makes the error-prone part (sexagesimal formatting, sign
    conventions) unit-testable without a serial port.

    Source: Meade Telescope Serial Command Protocol rev. 2010.10
    (documentation/TelescopeProtocol_2010-10.pdf), "Autostar" column.

    Conventions used throughout roboscope (and converted here):
      - Angles in decimal degrees; RA in decimal hours.
      - Longitude east-positive (ISO 6709). Meade uses west-positive: the doc says
        ":Gg# ... East Longitudes are expressed as negative". We send `:Sg` as west
        longitude in 0..360, the form other LX200 drivers commonly use. *Unverified on
        this handbox.*
      - UTC offset in the ISO sense (PST = -8). Meade's `:GG`/`:SG` value is "the number
        of hours to add to local time to yield UTC", i.e. the negation.

    Reply quirks handled by `parse_sexagesimal`:
      - The degree separator may be '*' or the byte 0xDF (shown as 'ß' in latin-1),
        which Autostar firmware is commonly reported to send. *Unverified here.*
      - Low-precision RA is "HH:MM.T" (tenths of a minute), not "HH:MM:SS".
"""

import re
from datetime import date, time

ACK = b"\x06"  # Alignment query; the reply is a single mode character.
NAK = b"\x15"  # "Busy, retry" reply documented for LX200GPS; handled defensively.

_FIELD_SPLIT = re.compile(r"[^\d.]+")


def command(body: str) -> bytes:
    """Wrap a command body as ':<body>#' bytes, e.g. command('GR') -> b':GR#'."""
    return f":{body}#".encode("latin-1")


def parse_sexagesimal(text: str) -> float:
    """Parse a Meade angle/time reply into a decimal number of its first unit.

    Accepts 1–3 fields separated by any non-digit characters, with an optional leading
    sign and a trailing '#': "+45*30'15" -> 45.504..., "-05ß12" -> -5.2,
    "12:34:56" -> 12.582..., and low-precision RA "12:34.5" -> 12.575.

    Raises:
        ValueError: if the text has no fields, more than 3, or a minutes/seconds field
            of 60 or more.
    """
    s = text.strip().rstrip("#").strip()
    sign = -1.0 if s.startswith("-") else 1.0
    fields = [f for f in _FIELD_SPLIT.split(s.lstrip("+-")) if f]
    if not 1 <= len(fields) <= 3:
        raise ValueError(f"not a sexagesimal value: {text!r}")
    values = [float(f) for f in fields]
    if any(v >= 60 for v in values[1:]):
        raise ValueError(f"minutes/seconds out of range: {text!r}")
    return sign * sum(v / 60**i for i, v in enumerate(values))


def _dms(value: float) -> tuple[int, int, int]:
    """Split |value| into whole (units, minutes, seconds), rounding to the nearest second
    with carry, so 12.99999 -> (13, 0, 0) rather than (12, 59, 60)."""
    total = round(abs(value) * 3600)
    return total // 3600, (total // 60) % 60, total % 60


def format_ra(ra_hours: float) -> str:
    """RA for `:Sr` in high precision: 'HH:MM:SS', wrapped into 0..24 h."""
    h, m, s = _dms(ra_hours % 24.0)
    return f"{h % 24:02d}:{m:02d}:{s:02d}"


def _signed_dms(deg: float, sec_sep: str) -> str:
    """'sDD*MM<sec_sep>SS' for signed angles within ±90°."""
    if not -90.0 <= deg <= 90.0:
        raise ValueError(f"angle out of ±90° range: {deg}")
    d, m, s = _dms(deg)
    sign = "-" if deg < 0 and (d, m, s) != (0, 0, 0) else "+"
    return f"{sign}{d:02d}*{m:02d}{sec_sep}{s:02d}"


def format_dec(dec_deg: float) -> str:
    """Declination for `:Sd`: 'sDD*MM:SS' (the doc's high-precision form for :Sd)."""
    return _signed_dms(dec_deg, ":")


def format_alt(alt_deg: float) -> str:
    """Altitude for `:Sa`: "sDD*MM'SS" (the doc uses an apostrophe for :Sa)."""
    return _signed_dms(alt_deg, "'")


def format_latitude(lat_deg: float) -> str:
    """Latitude for `:St`: 'sDD*MM' (whole minutes; ~1.8 km resolution)."""
    if not -90.0 <= lat_deg <= 90.0:
        raise ValueError(f"latitude out of range: {lat_deg}")
    total = round(abs(lat_deg) * 60)
    sign = "-" if lat_deg < 0 and total else "+"
    return f"{sign}{total // 60:02d}*{total % 60:02d}"


def format_longitude(lon_east_deg: float) -> str:
    """Longitude for `:Sg`: west-positive 'DDD*MM' in 0..359°59'.

    Converts from roboscope's east-positive convention: 122.42°W (lon_east = -122.42)
    -> '122*25'; 2.35°E -> '357*39'.
    """
    total = round((-lon_east_deg % 360.0) * 60) % (360 * 60)
    return f"{total // 60:03d}*{total % 60:02d}"


def parse_longitude(text: str) -> float:
    """Parse a `:Gg` reply (west-positive) into east-positive degrees in (-180, 180]."""
    east = -parse_sexagesimal(text)
    east = (east + 180.0) % 360.0 - 180.0
    return 180.0 if east == -180.0 else east


def format_meade_utc_offset(utc_offset_hours: float) -> str:
    """`:SG` argument from an ISO UTC offset (PST = -8 -> '+08.0')."""
    return f"{-utc_offset_hours:+05.1f}"


def parse_meade_utc_offset(text: str) -> float:
    """`:GG` reply (hours to add to local to get UTC) -> ISO UTC offset (PST -> -8)."""
    return -float(text.strip().rstrip("#"))


def format_time(t: time) -> str:
    """Local time for `:SL`: 'HH:MM:SS' (24-hour; Autostar keeps a 24 h clock)."""
    return t.strftime("%H:%M:%S")


def format_date(d: date) -> str:
    """Local date for `:SC`: 'MM/DD/YY'."""
    return d.strftime("%m/%d/%y")
