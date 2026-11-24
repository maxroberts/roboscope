"""Tests for roboscope.mount.protocol: sexagesimal parsing/formatting and sign conventions.

Details:
    These pin down conversions that are easy to get subtly wrong: rounding carry,
    negative values near zero, the low-precision RA form, and Meade's west-positive
    longitude and inverted UTC offset.
"""

from datetime import date, time

import pytest

from roboscope.mount import protocol as p


@pytest.mark.parametrize("text, expected", [
    ("+45*30'15#", 45 + 30 / 60 + 15 / 3600),
    ("+45\xdf30:15#", 45 + 30 / 60 + 15 / 3600),   # Autostar degree byte
    ("-05\xdf12#", -(5 + 12 / 60)),
    ("-00*30'00", -0.5),                            # sign on a zero-degree value
    ("12:34:56#", 12 + 34 / 60 + 56 / 3600),
    ("12:34.5#", 12 + 34.5 / 60),                   # low-precision RA HH:MM.T
    ("123*45#", 123.75),
])
def test_parse_sexagesimal(text, expected):
    assert p.parse_sexagesimal(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "#", "12:60:00", "1:2:3:4"])
def test_parse_sexagesimal_rejects(bad):
    with pytest.raises(ValueError):
        p.parse_sexagesimal(bad)


def test_format_ra_rounds_with_carry_and_wraps():
    assert p.format_ra(12.999999) == "13:00:00"
    assert p.format_ra(23.99999) == "00:00:00"
    assert p.format_ra(-1.0) == "23:00:00"
    assert p.format_ra(5 + 35 / 60 + 17 / 3600) == "05:35:17"


def test_format_dec_and_alt():
    assert p.format_dec(-0.5) == "-00*30:00"
    assert p.format_dec(-0.00001) == "+00*00:00"   # rounds to zero: no "-0"
    assert p.format_dec(45.5) == "+45*30:00"
    assert p.format_alt(10.25) == "+10*15'00"
    with pytest.raises(ValueError):
        p.format_dec(91)


def test_latitude():
    assert p.format_latitude(37.7749) == "+37*46"
    assert p.format_latitude(-33.87) == "-33*52"


@pytest.mark.parametrize("east, meade", [
    (-122.42, "122*25"),   # San Francisco: west longitude, sent as-is
    (2.35, "357*39"),      # Paris: east longitude, sent as 360 - lon
    (0.0, "000*00"),
])
def test_longitude_roundtrip(east, meade):
    assert p.format_longitude(east) == meade
    assert p.parse_longitude(meade) == pytest.approx(east, abs=1 / 60)


def test_parse_longitude_negative_reply_is_east():
    # :Gg doc: "East Longitudes are expressed as negative"
    assert p.parse_longitude("-002*21#") == pytest.approx(2.35)


def test_utc_offset_sign_conversion():
    assert p.format_meade_utc_offset(-8) == "+08.0"     # PST
    assert p.format_meade_utc_offset(5.5) == "-05.5"    # IST
    assert p.parse_meade_utc_offset("+08#") == -8


def test_time_and_date():
    assert p.format_time(time(19, 32, 5)) == "19:32:05"
    assert p.format_date(date(2026, 11, 24)) == "11/24/26"
    assert p.command("GR") == b":GR#"
