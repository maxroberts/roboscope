"""Tests for roboscope.mount.driver and transport against SimulatedAutostar.

Details:
    Each test drives a `Mount` wired to a fresh simulator. The tests check the commands
    sent, the parsing of replies, error handling (rejections, timeouts), and that
    multi-part replies are fully consumed so later replies aren't corrupted.
"""

from datetime import datetime

import pytest

from roboscope.mount import (
    CommandRejected, Direction, Mount, MountTimeout, RaDec, Site, Transport,
)
from roboscope.mount.sim import SimulatedAutostar


@pytest.fixture
def sim():
    return SimulatedAutostar()


@pytest.fixture
def mount(sim):
    return Mount(Transport(sim, min_gap_s=0))


def test_ping_and_info(mount):
    assert mount.ping() == "A"
    info = mount.info()
    assert info.product == "Autostar" and info.firmware == "43Eg"


def test_ensure_high_precision_toggles_once(mount, sim):
    mount.ensure_high_precision()
    assert sim.high_precision
    mount.ensure_high_precision()          # already high: must not toggle back
    assert sim.high_precision
    assert sim.sent.count("U") == 1


def test_radec_reads_both_precisions(mount, sim):
    sim.ra_hours, sim.dec_deg = 12.5, -30.25
    low = mount.radec()                    # HH:MM.T and DD*MM
    assert low.ra_hours == pytest.approx(12.5)
    assert low.dec_deg == pytest.approx(-30.25)
    mount.ensure_high_precision()
    sim.ra_hours, sim.dec_deg = 1 + 2 / 60 + 3 / 3600, 4 + 5 / 60 + 6 / 3600
    high = mount.radec()
    assert high.ra_hours == pytest.approx(sim.ra_hours, abs=1e-6)
    assert high.dec_deg == pytest.approx(sim.dec_deg, abs=1e-6)


def test_goto_sends_target_then_slews(mount, sim):
    mount.goto_radec(RaDec(5 + 35 / 60, -5.5))
    assert sim.sent[-3:] == ["Sr05:35:00", "Sd-05*30:00", "MS"]
    assert mount.is_slewing() is True
    assert mount.is_slewing() is False
    assert sim.ra_hours == pytest.approx(5 + 35 / 60)


def test_goto_refusal_raises_with_message(mount, sim):
    sim.goto_refusal = "Object Below Horizon"
    with pytest.raises(CommandRejected, match="Below Horizon"):
        mount.goto_radec(RaDec(1, -80))


def test_sync_sets_target_then_cm(mount, sim):
    reply = mount.sync_radec(RaDec(10, 20))
    assert sim.sent[-1] == "CM" and "M31" in reply


def test_set_site_converts_longitude(mount, sim):
    mount.set_site(Site(37.7749, -122.4194))
    assert "St+37*46" in sim.sent and "Sg122*25" in sim.sent
    site = mount.site()
    assert site.lat_deg == pytest.approx(37.7749, abs=1 / 60)
    assert site.lon_deg == pytest.approx(-122.4194, abs=1 / 60)


def test_set_datetime_consumes_sc_reply(mount, sim):
    mount.set_datetime(datetime(2026, 12, 1, 21, 5, 9), utc_offset_hours=-8)
    assert sim.sent[-3:] == ["SG+08.0", "SL21:05:09", "SC12/01/26"]
    assert mount.local_datetime() == datetime(2026, 12, 1, 21, 5, 9)
    assert mount.utc_offset() == -8


def test_rejected_set_raises(mount, sim):
    sim._set = lambda kind, arg: False
    with pytest.raises(CommandRejected):
        mount.set_site(Site(0, 0))


def test_silent_handbox_times_out(mount, sim):
    sim.silent = True
    with pytest.raises(MountTimeout):
        mount.radec()


def test_stale_reply_is_discarded(mount, sim):
    sim._out += b"garbage#"                # e.g. a late reply to an earlier command
    sim.ra_hours, sim.dec_deg = 3.0, 0.0
    assert mount.radec().ra_hours == pytest.approx(3.0)


def test_motion_commands(mount, sim):
    mount.move(Direction.NORTH)
    mount.stop(Direction.NORTH)
    mount.pulse_guide(Direction.WEST, 250)
    mount.halt()
    assert sim.sent[-4:] == ["Mn", "Qn", "Mgw0250", "Q"]
    assert not sim.unknown
    with pytest.raises(ValueError):
        mount.pulse_guide(Direction.EAST, 10000)


def test_alignment_status_has_no_terminator(mount):
    assert mount.alignment_status() == "AN0"
    assert mount.radec()                   # next query still parses cleanly
