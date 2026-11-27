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
      - `controller`: `MotorController`, the driver for our own Uno motor controller
        (firmware/mount_controller), which replaces the handbox. This is the main
        motion path; `SimulatedController` is its test double.
      - `jog`: `MountJog`, hold-to-move manual steering on top of `MotorController`,
        served by the camera hub (`roboscope.camera.server --mount`) for the camera
        page's Mount panel: a dead man's switch on browser heartbeats, speed clamped
        to the firmware cap, no soft limits (user's choice for this supervised tool).

    Typical use (main path, our controller):
        with MotorController.open("/dev/cu.usbmodem113301") as mc:
            mc.move_by("alt", 5.0, deg_per_s=0.3)
            print(mc.position().degrees)

    Handbox path (diagnostics):
        with Mount.connect("/dev/cu.usbserial-A9IDYQVP") as m:
            print(m.info(), m.radec())

    Commands marked *unverified* in `driver` still need confirming on the real
    AudioStar (see scripts/probe_mount.py).
"""

from roboscope.mount.driver import (
    AltAz, CommandRejected, Direction, Mount, MountInfo, RaDec, Site, SlewRate,
)
from roboscope.mount.controller import (
    ControllerError, MotorController, SimulatedController,
)
from roboscope.mount.transport import MountBusy, MountError, MountTimeout, Transport

__all__ = [
    "ControllerError", "MotorController", "SimulatedController",
    "AltAz", "CommandRejected", "Direction", "Mount", "MountBusy", "MountError",
    "MountInfo", "MountTimeout", "RaDec", "Site", "SlewRate", "Transport",
]
