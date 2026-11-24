"""Byte-level serial link to the AudioStar: send commands, read replies, log traffic.

Details:
    `Transport` wraps anything with the pyserial `Serial` interface: a real port opened
    with `Transport.open(device)`, or `sim.SimulatedAutostar` in tests. `driver.Mount`
    is its only intended user.

    The LX200 protocol has no framing or request IDs. Commands may return nothing, one
    character, or a '#'-terminated string, and the caller must know which. So the
    transport offers primitives (`send`, `read_char`, `read_string`) plus one-shot
    helpers that combine them. Multi-part exchanges hold `lock` (re-entrant) so another
    thread can't interleave bytes.

    Reliability measures:
      - Before each command, unread input is discarded, so a late or unexpected reply
        to an earlier command can't be mistaken for this one's reply.
      - A minimum gap between commands (`min_gap_s`). The handbox is slow, and the
        right value is to be measured in Stage 1.
      - A missing reply raises `MountTimeout`. A NAK (0x15) raises `MountBusy`.

    Every byte sent and received is logged at DEBUG on the 'roboscope.mount.transport'
    logger, for diagnosing cables and firmware quirks.
"""

import logging
import threading
import time

from roboscope.mount import protocol

log = logging.getLogger(__name__)


class MountError(Exception):
    """Base class for mount communication and command errors."""


class MountTimeout(MountError):
    """The handbox did not reply (or replied incompletely) within the timeout."""


class MountBusy(MountError):
    """The handbox replied NAK (busy); the command may be retried."""


class Transport:
    """Serialized command/reply access to one handbox connection.

    Args:
        port: an open pyserial-like object (`write`, `read`, `read_until`, `flush`,
            `reset_input_buffer`, `close`, and a settable `timeout` attribute).
        min_gap_s: minimum seconds between the end of one write and the next.
    """

    def __init__(self, port, *, min_gap_s: float = 0.05):
        self.port = port
        self.min_gap_s = min_gap_s
        self.lock = threading.RLock()
        self._last_write = 0.0

    @classmethod
    def open(cls, device: str, *, baudrate: int = 9600, timeout_s: float = 2.0,
             **kwargs) -> "Transport":
        """Open a real serial port at 9600 8N1 (the handbox's default settings)."""
        import serial

        port = serial.Serial(device, baudrate=baudrate, bytesize=8, parity="N",
                             stopbits=1, timeout=timeout_s)
        return cls(port, **kwargs)

    def close(self) -> None:
        """Close the underlying port."""
        self.port.close()

    # -- primitives (callers doing multi-part exchanges should hold `lock`) --

    def send(self, data: bytes) -> None:
        """Discard unread input, wait out the command gap, then write `data`."""
        with self.lock:
            self.port.reset_input_buffer()
            wait = self._last_write + self.min_gap_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            log.debug("TX %r", data)
            self.port.write(data)
            self.port.flush()
            self._last_write = time.monotonic()

    def read_char(self, timeout_s: float | None = None) -> str:
        """Read exactly one reply byte as a character.

        Raises:
            MountTimeout: no byte arrived.
            MountBusy: the byte was NAK.
        """
        with self.lock, self._timeout(timeout_s):
            b = self.port.read(1)
        log.debug("RX %r", b)
        if not b:
            raise MountTimeout("no reply")
        if b == protocol.NAK:
            raise MountBusy("handbox replied NAK")
        return b.decode("latin-1")

    def read_string(self, timeout_s: float | None = None) -> str:
        """Read a '#'-terminated reply and return it without the '#'.

        Raises:
            MountTimeout: the terminator did not arrive (partial data is logged).
            MountBusy: the reply was a bare NAK.
        """
        with self.lock, self._timeout(timeout_s):
            b = self.port.read_until(b"#")
        log.debug("RX %r", b)
        if b == protocol.NAK:
            raise MountBusy("handbox replied NAK")
        if not b.endswith(b"#"):
            raise MountTimeout(f"incomplete reply {b!r}")
        return b[:-1].decode("latin-1")

    # -- one-shot helpers --

    def command(self, body: str) -> None:
        """Send a command that has no reply."""
        self.send(protocol.command(body))

    def query_char(self, body: str, timeout_s: float | None = None) -> str:
        """Send a command and return its one-character reply."""
        with self.lock:
            self.send(protocol.command(body))
            return self.read_char(timeout_s)

    def query_string(self, body: str, timeout_s: float | None = None) -> str:
        """Send a command and return its '#'-terminated reply (without '#')."""
        with self.lock:
            self.send(protocol.command(body))
            return self.read_string(timeout_s)

    def _timeout(self, timeout_s: float | None):
        """Context manager that temporarily overrides the port read timeout."""
        return _TimeoutOverride(self.port, timeout_s)


class _TimeoutOverride:
    """Sets `port.timeout` for the duration of a `with` block; no-op if None."""

    def __init__(self, port, timeout_s):
        self.port, self.timeout_s = port, timeout_s

    def __enter__(self):
        if self.timeout_s is not None:
            self.saved, self.port.timeout = self.port.timeout, self.timeout_s

    def __exit__(self, *exc):
        if self.timeout_s is not None:
            self.port.timeout = self.saved
