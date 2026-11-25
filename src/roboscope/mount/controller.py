"""Host driver for our own motor controller (firmware/mount_controller on an Arduino Uno).

Details:
    This replaces the AudioStar handbox as the way roboscope moves the mount. The Uno is
    master of the NG's motor bus; this module talks to it over USB serial with the
    line protocol documented in the firmware header.

    Units: the firmware works in encoder counts; this driver converts with per-axis
    counts-per-degree. Measured 2026-11-24 (hardware_findings.md, ±2–3%): Az ≈ 3085,
    Alt ≈ 4230. Degrees here are *mount* angles relative to the controller's zero, not
    sky coordinates. Turning them into alt/az is the job of a higher layer, once the
    camera gives absolute references.

    Threads:
      - A reader thread splits incoming lines. "EVT ..." lines go to `events` and the
        log: watchdog or limit stops, `link <axis> down/up`, `uncommanded <axis>`
        (the firmware is stopping motion it didn't command), and `garbage <axis>`
        (an implausible status reply was rejected). Every other line is the reply to
        the command in flight.
      - An optional keepalive thread sends PING every 0.3 s so the firmware's 1 s
        watchdog doesn't stop a long move. With keepalive off, the mount stops ~1 s
        after the host goes quiet, by design.
      - All command traffic is serialized by a lock.

    Opening the real port resets the Uno: positions restart at 0 at the current
    position, and the firmware restores its default limits (Az ±30850, Alt ±190000
    counts). After a reset the boards normally answer within a fraction of a second.
    Occasionally (e.g. after a firmware upload) they lose bus sync, and the firmware
    recovers them automatically within ~5 s. Either way, call `wait_healthy()` before
    commanding motion. `Position.link`/`.calibrated` report the current state. The boot
    line (`boot_line`) is only a snapshot from before recovery. The
    firmware refuses motion, and stops motion, when a board stops answering (e.g. the
    mount is off). The 47 kΩ pull-ups keep the bus idle during that reset
    (wiring_hbx_uno.md).

    Used by: scripts/mc_*.py hardware tests. Tested against `SimulatedController`
    below.
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

COUNTS_PER_DEGREE = {"az": 3085.0, "alt": 4230.0}   # measured 2026-11-24, ±2–3%
AXES = ("az", "alt")


class ControllerError(Exception):
    """The controller replied ERR, or didn't reply in time."""


@dataclass(frozen=True)
class Position:
    """Snapshot from POS: counts, degrees (via counts/deg), commanded speeds, PWM, error
    bits, and link (True if that axis's motor board answered its recent polls)."""
    counts: dict
    degrees: dict
    commanded_cps: dict
    pwm: dict
    err: dict
    link: dict
    calibrated: bool


class MotorController:
    """Command interface to the mount_controller firmware.

    Args:
        port: pyserial-like object with `write` and `readline` (timeout-based). Use
            `MotorController.open(device)` for real hardware.
        counts_per_degree: per-axis scaling; defaults to the measured values.
        boot_timeout_s: how long to wait for the firmware's boot line (0 = don't wait).
    """

    def __init__(self, port, counts_per_degree=None, boot_timeout_s: float = 4.0):
        self.port = port
        self.cpd = dict(counts_per_degree or COUNTS_PER_DEGREE)
        self.events: list[str] = []
        self.boot_line = ""
        self._replies: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._running = True
        self._keepalive: threading.Thread | None = None
        self._keepalive_on = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        if boot_timeout_s:
            try:
                self.boot_line = self._replies.get(timeout=boot_timeout_s)
            except queue.Empty:
                self.boot_line = ""
            log.info("boot: %s", self.boot_line)

    @classmethod
    def open(cls, device: str, **kwargs) -> "MotorController":
        """Open the Uno's USB serial port (this resets the Uno; see module notes)."""
        import serial

        return cls(serial.Serial(device, 115200, timeout=0.2), **kwargs)

    # -- plumbing ---------------------------------------------------------------

    def _read_loop(self) -> None:
        buf = b""
        while self._running:
            try:
                chunk = self.port.readline()
            except Exception as e:                   # port closed under us
                if self._running:                    # not an error during close()
                    log.error("reader stopped: %s", e)
                return
            if not chunk:
                continue
            buf += chunk
            if not buf.endswith(b"\n"):
                continue
            line, buf = buf.decode(errors="replace").strip(), b""
            if not line:
                continue
            if line.startswith("EVT"):
                self.events.append(line)
                log.warning("controller event: %s", line)
            else:
                self._replies.put(line)

    def command(self, line: str, timeout_s: float = 2.0) -> str:
        """Send one command line and return its reply (without the leading 'OK').

        Raises:
            ControllerError: on an ERR reply or a timeout.
        """
        with self._lock:
            while not self._replies.empty():          # drop anything stale
                self._replies.get_nowait()
            self.port.write((line + "\n").encode())
            try:
                reply = self._replies.get(timeout=timeout_s)
            except queue.Empty:
                raise ControllerError(f"no reply to {line!r}") from None
        log.debug("%s -> %s", line, reply)
        if not reply.startswith("OK"):
            raise ControllerError(f"{line!r}: {reply}")
        return reply[2:].strip()

    def close(self) -> None:
        """Stop both axes (best effort), stop the threads, close the port."""
        self.keepalive(False)
        try:
            self.stop()
        except ControllerError as e:
            log.error("stop on close failed: %s", e)
        self._running = False
        time.sleep(0.25)                             # let the reader's readline time out
        self.port.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def keepalive(self, on: bool = True) -> None:
        """Start/stop a background PING every 0.3 s to feed the firmware watchdog."""
        if on:
            self._keepalive_on.set()
            if not self._keepalive or not self._keepalive.is_alive():
                self._keepalive = threading.Thread(target=self._ping_loop, daemon=True)
                self._keepalive.start()
        else:
            self._keepalive_on.clear()

    def _ping_loop(self) -> None:
        while self._running and self._keepalive_on.is_set():
            try:
                self.command("PING")
            except ControllerError as e:
                log.error("keepalive: %s", e)
            time.sleep(0.3)

    # -- commands ---------------------------------------------------------------

    def hello(self) -> str:
        return self.command("HELLO")

    def calibrate(self) -> dict:
        """Calibrate both encoder LEDs (~9 s; turns each motor slightly; zeros positions).

        Returns:
            The LED settings, e.g. {'az': 189, 'alt': 169}.
        """
        reply = self.command("CAL", timeout_s=15)
        return {k: int(v) for k, v in (f.split("=") for f in reply.split() if "=" in f)}

    def set_speed(self, axis: str, deg_per_s: float) -> None:
        """Run `axis` at `deg_per_s` (signed) until changed or stopped.

        The firmware stops it on a soft limit or if the watchdog isn't fed.
        """
        self.set_speed_cps(axis, deg_per_s * self.cpd[axis])

    def set_speed_cps(self, axis: str, cps: float) -> None:
        """Same as `set_speed`, in encoder counts per second."""
        self.command(f"SPEED {axis} {cps:.2f}")

    def stop(self, axis: str | None = None) -> None:
        """Stop one axis, or both."""
        self.command("STOP" if axis is None else f"STOP {axis}")

    def position(self) -> Position:
        """Current encoder positions (counts and degrees) and status."""
        f = dict(p.split("=") for p in self.command("POS").split()[1:])
        counts = {a: int(f[a]) for a in AXES}
        return Position(
            counts=counts,
            degrees={a: counts[a] / self.cpd[a] for a in AXES},
            commanded_cps={"az": float(f["vaz"]), "alt": float(f["valt"])},
            pwm={"az": int(f["pwmaz"]), "alt": int(f["pwmalt"])},
            err={"az": int(f["erraz"]), "alt": int(f["erralt"])},
            link={"az": f.get("linkaz") == "1", "alt": f.get("linkalt") == "1"},
            calibrated=f.get("cal") == "1",
        )

    def wait_healthy(self, timeout_s: float = 12.0) -> Position:
        """Wait until both motor boards answer cleanly; return that Position.

        After the port opens (an Uno reset) the boards normally answer within ~0.3 s.
        If they've lost bus sync (seen after firmware uploads), the firmware's
        auto-resync recovers them in ~5 s (measured 2026-11-24/25). Call this before
        commanding motion.

        Raises:
            ControllerError: if they're not healthy within `timeout_s` (mount off,
                wiring fault, or a fault the resync can't clear).
        """
        t_end = time.monotonic() + timeout_s
        while True:
            p = self.position()
            if all(p.link.values()) and not any(p.pwm.values()):
                return p
            if time.monotonic() > t_end:
                raise ControllerError(f"motor boards not healthy after {timeout_s} s: {p}")
            time.sleep(0.25)

    def zero(self, axis: str | None = None) -> None:
        """Set the position counter(s) to 0 at the current position."""
        self.command("ZERO" if axis is None else f"ZERO {axis}")

    def set_limits_deg(self, axis: str, min_deg: float, max_deg: float) -> None:
        """Set soft limits in degrees relative to the current zero."""
        c = self.cpd[axis]
        self.command(f"LIMIT {axis} {round(min_deg * c)} {round(max_deg * c)}")

    def limits_deg(self) -> dict:
        """Current soft limits as {axis: (min_deg, max_deg)}."""
        out = {}
        for part in self.command("LIMITS").split()[1:]:
            axis, rng = part.split("=")
            lo, hi = (int(x) for x in rng.split(","))
            out[axis] = (lo / self.cpd[axis], hi / self.cpd[axis])
        return out

    def move_by(self, axis: str, degrees: float, deg_per_s: float = 0.3,
                poll_s: float = 0.1, timeout_s: float | None = None) -> float:
        """Move `axis` by `degrees` (signed) using encoder feedback; return the error (deg).

        Runs at `deg_per_s` and stops when the remaining distance is under ~0.15 s of
        travel; coast covers the rest. Keepalive runs for the duration.

        Raises:
            ControllerError: if the controller stops the axis itself (limit/watchdog
                event) or the move times out.
        """
        start = self.position().degrees[axis]
        target = start + degrees
        rate = abs(deg_per_s) * (1 if degrees > 0 else -1)
        timeout_s = timeout_s or abs(degrees / deg_per_s) * 1.5 + 5
        n_events = len(self.events)
        self.keepalive(True)
        try:
            self.set_speed(axis, rate)
            t_end = time.monotonic() + timeout_s
            while True:
                time.sleep(poll_s)
                pos = self.position()
                remaining = target - pos.degrees[axis]
                if len(self.events) > n_events:
                    raise ControllerError(f"controller stopped the move: {self.events[-1]}")
                # Remaining distance in the direction of travel, compared with ~0.15 s
                # of travel at the commanded rate.
                ahead = remaining if rate > 0 else -remaining
                if ahead <= abs(rate) * 0.15:
                    break
                if time.monotonic() > t_end:
                    raise ControllerError(f"move_by timed out ({remaining:+.3f}° left)")
            self.stop(axis)
            time.sleep(0.4)                                 # let it coast and settle
            return self.position().degrees[axis] - target
        finally:
            try:
                self.stop(axis)
            finally:
                self.keepalive(False)


class SimulatedController:
    """In-memory stand-in for the Uno running mount_controller, for tests.

    It behaves like a serial port: `write` takes command lines and `readline` returns
    reply lines. It models what the host-side logic depends on: positions that
    integrate the commanded speed over time, soft limits (refusal plus predictive
    stop), the watchdog, the calibration gate, boards not answering (`boards_on=False`,
    as when the mount is off), and the reply formats. Speed
    regulation, coast and backlash are not modelled.
    """

    def __init__(self, calibrated: bool = True, watchdog_s: float = 1.0,
                 boards_on: bool = True):
        self.calibrated = calibrated
        self.boards_on = boards_on
        self.watchdog_s = watchdog_s
        self.pos = {"az": 0.0, "alt": 0.0}
        self.cps = {"az": 0.0, "alt": 0.0}
        self.lim = {"az": (-30850, 30850), "alt": (-190000, 190000)}
        self.sent: list[str] = []
        self._out: queue.Queue[bytes] = queue.Queue()
        self._t = time.monotonic()
        self._last_host = self._t
        ledi = (189 if calibrated else 253) if boards_on else 0
        link = int(boards_on)
        self._out.put((f"OK mount_controller v2 calibrated={int(calibrated and boards_on)} "
                       f"ledi_az={ledi} ledi_alt={ledi} linkaz={link} "
                       f"linkalt={link}\n").encode())

    # serial-like surface
    def write(self, data: bytes) -> int:
        for line in data.decode().splitlines():
            if line.strip():
                self.sent.append(line.strip())
                self._handle(line.strip())
        return len(data)

    def readline(self) -> bytes:
        self._advance()
        try:
            return self._out.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self) -> None:
        pass

    # behaviour
    def _emit(self, text: str) -> None:
        self._out.put((text + "\n").encode())

    def _advance(self) -> None:
        now = time.monotonic()
        dt, self._t = now - self._t, now
        for a in AXES:
            if not self.cps[a]:
                continue
            self.pos[a] += self.cps[a] * dt
            lo, hi = self.lim[a]
            ahead = self.pos[a] + self.cps[a] * 0.3
            if (self.cps[a] > 0 and ahead >= hi) or (self.cps[a] < 0 and ahead <= lo):
                self.cps[a] = 0
                self._emit(f"EVT limit {a} pos={round(self.pos[a])} stopped")
        if any(self.cps.values()) and now - self._last_host > self.watchdog_s:
            self.cps = {"az": 0.0, "alt": 0.0}
            self._emit("EVT watchdog stopped")

    def _handle(self, line: str) -> None:
        self._advance()
        self._last_host = time.monotonic()
        p = line.split()
        cmd, args = p[0], p[1:]
        if cmd == "HELLO":
            self._emit("OK mount_controller v2")
        elif cmd == "PING":
            self._emit("OK")
        elif cmd == "CAL":
            if not self.boards_on:
                self._emit("ERR cal noack az")
                return
            self.calibrated, self.pos = True, {"az": 0.0, "alt": 0.0}
            self._emit("OK cal az=189 alt=169 (positions zeroed)")
        elif cmd == "POS":
            self._emit(f"OK pos az={round(self.pos['az'])} alt={round(self.pos['alt'])} "
                       f"vaz={self.cps['az']:.1f} valt={self.cps['alt']:.1f} pwmaz=0 "
                       f"pwmalt=0 erraz=0 erralt=0 linkaz={int(self.boards_on)} "
                       f"linkalt={int(self.boards_on)} cal={int(self.calibrated)}")
        elif cmd == "STOP":
            for a in (args or AXES):
                self.cps[a] = 0.0
            self._emit("OK")
        elif cmd == "ZERO":
            for a in (args or AXES):
                self.pos[a] = 0.0
            self._emit("OK")
        elif cmd == "LIMITS":
            self._emit(f"OK limits az={self.lim['az'][0]},{self.lim['az'][1]} "
                       f"alt={self.lim['alt'][0]},{self.lim['alt'][1]}")
        elif cmd == "LIMIT" and len(args) == 3 and args[0] in AXES:
            lo, hi = int(args[1]), int(args[2])
            if lo >= hi:
                self._emit("ERR min >= max")
            else:
                self.lim[args[0]] = (lo, hi)
                self._emit("OK")
        elif cmd == "SPEED" and len(args) == 2 and args[0] in AXES:
            a, cps = args[0], float(args[1])
            lo, hi = self.lim[a]
            ahead = self.pos[a] + cps * 0.3
            if not self.boards_on:
                self._emit("ERR motor board not answering (mount off?)")
            elif not self.calibrated:
                self._emit("ERR not calibrated: run CAL")
            elif abs(cps) > 1300:
                self._emit("ERR speed above MAX_CPS")
            elif (cps > 0 and ahead >= hi) or (cps < 0 and ahead <= lo):
                self._emit(f"ERR at limit {a}")
            else:
                self.cps[a] = cps
                self._emit("OK")
        else:
            self._emit("ERR unknown command")
