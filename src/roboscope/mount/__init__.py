"""Mount control: talk to the AudioStar handbox over RS-232 with the LX200 protocol.

Details:
    Layers, bottom to top:
      - `protocol`: pure formatting/parsing of LX200 strings and sign conventions
        (east-positive longitude, ISO UTC offset; Meade's opposite conventions are
        converted here).
      - `transport`: serial byte exchange with locking, timeouts, stale-input flushing
        and DEBUG traffic logging. Raises `MountTimeout` / `MountBusy`.
      - `driver`: `Mount`, the typed API (positions, goto, sync, manual motion,
        pulse guide, site/time). It does no safety checks; those belong in a layer above.
      - `sim`: `SimulatedAutostar`, a protocol-level fake handbox used by the tests.

    Typical use:
        with Mount.connect("/dev/ttyUSB0") as m:
            print(m.info(), m.radec())

    Commands marked *unverified* in `driver` still need confirming on the real
    AudioStar (Stage 1 probing; see scripts/probe_mount.py).
"""

from roboscope.mount.driver import (
    AltAz, CommandRejected, Direction, Mount, MountInfo, RaDec, Site, SlewRate,
)
from roboscope.mount.transport import MountBusy, MountError, MountTimeout, Transport

__all__ = [
    "AltAz", "CommandRejected", "Direction", "Mount", "MountBusy", "MountError",
    "MountInfo", "MountTimeout", "RaDec", "Site", "SlewRate", "Transport",
]
