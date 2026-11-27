"""Hold-to-move manual steering of the mount, for the camera page's mount panel.

Details:
    `MountJog` owns a `MotorController` (the Uno on the host Pi) for the camera hub
    (`roboscope.camera.server --mount DEVICE`). The browser sends `jog(axis, direction,
    speed)` repeatedly while a direction button is held (heartbeats, every ~0.2 s), and
    `stop()` on release. It exists so a person can steer the telescope by eye from the
    live view. It is not pointing logic: positions are mount degrees from the
    controller's zero, not sky coordinates.

    Safety, as specified by the user for this manual tool (2026-11-26):
      - **Dead man's switch.** An axis whose last heartbeat is older than HOLD_S
        (0.6 s) is stopped by this module's loop, so a closed page, a dropped network or
        a lost "release" stops the mount within ~0.7 s plus coast. If the hub process
        itself dies, the firmware's 1 s watchdog stops it (this loop's POS polls are
        what feed that watchdog while moving).
      - **No soft limits.** On connect, both axes' soft limits are opened to
        ±UNLIMITED_COUNTS (effectively none). The user lifted the ±45° azimuth cable
        rule for this supervised tool (it still applies to unattended tests), and the
        altitude limit too: the firmware default (±190000 counts ≈ ±45°) counts from
        wherever the mount was at the Uno's last reset (every hub restart), so it
        didn't match anything physical and stopped a normal move (2026-11-26). The
        person steering is what keeps the tube off its hard stops.
      - **No Sun check.** The user chose none for this manual tool. Nothing here knows
        where the telescope points, so nothing here could check it anyway.
      - The firmware's own checks still apply: speed cap, links, uncommanded motion.

    Speeds are in degrees/s per axis, clamped to the firmware's cap (MAX_CPS counts/s
    ÷ counts per degree: Az ≈ 3.89°/s, Alt ≈ 2.84°/s with the 2026-11-24 calibration).
    The cap was raised from 1300 counts/s to 6500 on 2026-11-26 (the AudioStar
    handbox's speed 8, 1.5°/s; the page's presets are the handbox's speed ladder from
    the NG manual), then to 12000 on 2026-11-27 after a step-up test showed regulation
    holding there (documentation/mount_speed_limits.md). Coast after a stop at the cap
    is ~0.4-0.6°. It must match the firmware's MAX_CPS, or the firmware refuses the
    clamped speed.

    Connection: a background loop opens the port (which resets the Uno: positions
    re-zero, default limits return), then polls POS every POLL_S. If the motor boards
    don't answer (mount off), the port stays open and the status says so; the firmware
    refuses motion until they do. If the port itself fails (Uno unplugged), it's closed
    and reopened every RETRY_S. Encoder LED calibration (`calibrate()`, CAL) is needed
    after every mount power-up; the status reports `calibrated`.

    Used by: roboscope.camera.server (the /api/mount endpoints). Tested with
    `SimulatedController` (tests/test_mount_jog.py).
"""

import logging
import threading
import time
from typing import Callable

from .controller import AXES, ControllerError, MotorController

log = logging.getLogger(__name__)

HOLD_S = 0.6                    # heartbeat age at which a held axis is stopped
POLL_S = 0.1                    # position poll (also feeds the firmware watchdog)
RETRY_S = 5.0                   # reopen interval when the port has failed
MAX_CPS = 12000.0               # firmware MAX_CPS (firmware/mount_controller; was 6500)
UNLIMITED_COUNTS = 1_000_000_000      # ~236,000° or more: no soft limit (fits the int32)
DEFAULT_SPEED_DPS = 1.0         # the user asked for a default at the fast end (1°/s)


class MountJog:
    """Hold-to-move steering on top of a MotorController (see module notes).

    Args:
        open_controller: returns a connected MotorController; called by the loop (and
            again after a port failure). E.g. `lambda: MotorController.open(dev, cpd)`.
        hold_s, poll_s, retry_s: see the module constants.

    Thread safety: HTTP threads call jog/stop/calibrate/status while the loop thread
    polls; `_lock` guards the jog state, and MotorController serializes the serial
    traffic itself.
    """

    def __init__(self, open_controller: Callable[[], MotorController],
                 hold_s: float = HOLD_S, poll_s: float = POLL_S, retry_s: float = RETRY_S):
        self._open = open_controller
        self.hold_s, self.poll_s, self.retry_s = hold_s, poll_s, retry_s
        self.mc: MotorController | None = None
        self.error = "connecting"
        self.pos = None                       # latest controller Position
        self.busy = ""                        # "calibrating" while CAL runs
        self._speed = {a: 0.0 for a in AXES}  # what we commanded, deg/s (0 = stopped)
        self._sent = {a: 0.0 for a in AXES}   # monotonic time of that command
        self._deadline = {a: 0.0 for a in AXES}
        self._lock = threading.Lock()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mount-jog")
        self._thread.start()

    # -- requests from the page -------------------------------------------------

    def max_speed(self, axis: str) -> float:
        """The firmware's speed cap for `axis`, in degrees/s."""
        mc = self.mc
        cpd = mc.cpd[axis] if mc else 1.0
        return MAX_CPS / cpd

    def jog(self, axis: str, direction: int, speed_dps: float) -> dict:
        """Run `axis` in `direction` (+1/-1) at `speed_dps` (clamped), for HOLD_S more.

        Call it repeatedly while the button is held. A new speed or direction is sent at
        once; a repeat only extends the deadline.

        Raises:
            ValueError: bad axis, direction or speed.
            ControllerError: not connected, calibrating, or the firmware refused (e.g.
                motor board down, not calibrated).
        """
        if axis not in AXES or direction not in (1, -1):
            raise ValueError("axis must be 'az' or 'alt', direction +1 or -1")
        if not speed_dps > 0:
            raise ValueError("speed must be > 0")
        mc = self.mc
        if mc is None:
            raise ControllerError(f"mount not connected: {self.error}")
        if self.busy:
            raise ControllerError(f"mount busy: {self.busy}")
        v = direction * min(float(speed_dps), self.max_speed(axis))
        with self._lock:
            if v != self._speed[axis]:
                mc.set_speed(axis, v)
                self._speed[axis], self._sent[axis] = v, time.monotonic()
            self._deadline[axis] = time.monotonic() + self.hold_s
        return self.status()

    def stop(self, axis: str | None = None) -> dict:
        """Stop one axis or both, now (the page's release and Stop button)."""
        mc = self.mc
        with self._lock:
            for a in AXES if axis is None else (axis,):
                self._speed[a], self._deadline[a] = 0.0, 0.0
            if mc is not None:
                mc.stop(axis)
        return self.status()

    def calibrate(self) -> dict:
        """Run the encoder LED calibration (CAL, ~9 s; turns each motor slightly).

        Needed after every mount power-up. Blocks the calling HTTP thread meanwhile.
        """
        mc = self.mc
        if mc is None:
            raise ControllerError(f"mount not connected: {self.error}")
        self.stop()
        self.busy = "calibrating"
        try:
            leds = mc.calibrate()
            log.info("mount calibrated: %s", leds)
            self.pos = mc.position()          # so the reply shows calibrated, zeroed
        finally:
            self.busy = ""
        return self.status()

    def status(self) -> dict:
        """Connection, health, position (mount degrees from zero) and motion, as JSON."""
        p = self.pos
        mc = self.mc
        return {
            "connected": mc is not None, "error": self.error, "busy": self.busy,
            "calibrated": p.calibrated if p else None,
            "link": p.link if p else None,
            "degrees": {a: round(v, 4) for a, v in p.degrees.items()} if p else None,
            "speed_dps": dict(self._speed),
            "max_speed_dps": {a: round(self.max_speed(a), 3) for a in AXES} if mc else None,
            "default_speed_dps": DEFAULT_SPEED_DPS,
            "events": (mc.events[-3:] if mc else []),
        }

    def close(self) -> None:
        """Stop the loop and the mount, and close the port."""
        self.running = False
        self._thread.join(timeout=2)
        if self.mc is not None:
            self.mc.close()                   # stops both axes first
            self.mc = None

    # -- background loop --------------------------------------------------------

    def _connect(self) -> None:
        mc = self._open()
        # Lift both soft limits (see module notes).
        for a in AXES:
            mc.command(f"LIMIT {a} {-UNLIMITED_COUNTS} {UNLIMITED_COUNTS}")
        self.mc = mc
        log.info("mount connected: %s", mc.boot_line)

    def _loop(self) -> None:
        while self.running:
            if self.mc is None:
                try:
                    self._connect()
                except Exception as e:             # no device, permission, no reply
                    self.error = f"{type(e).__name__}: {e}"
                    log.warning("mount connect failed: %s", self.error)
                    time.sleep(self.retry_s)
                    continue
            mc = self.mc
            try:
                now = time.monotonic()
                with self._lock:                   # dead man's switch
                    for a in AXES:
                        if self._speed[a] and now > self._deadline[a]:
                            mc.stop(a)
                            self._speed[a] = 0.0
                            log.info("mount %s stopped: no heartbeat for %.1f s", a, self.hold_s)
                t_read = time.monotonic()
                p = mc.position()
                with self._lock:
                    # The firmware stopped an axis itself (limit, link): forget our speed,
                    # so the next heartbeat sends it again (and reports a refusal).
                    for a in AXES:
                        if self._speed[a] and t_read > self._sent[a] and p.commanded_cps[a] == 0:
                            self._speed[a] = 0.0
                self.pos = p
                self.error = ("" if all(p.link.values())
                              else "motor board not answering (mount off?): "
                                   + ", ".join(a for a in AXES if not p.link[a]))
            except ControllerError as e:           # a bad reply: keep the port
                self.error = str(e)
            except Exception as e:                 # the port failed (unplugged)
                self.error = f"{type(e).__name__}: {e}"
                log.error("mount port failed: %s", self.error)
                try:
                    mc.port.close()
                except Exception:
                    pass
                self.mc, self.pos = None, None
                time.sleep(self.retry_s)
                continue
            time.sleep(self.poll_s)
