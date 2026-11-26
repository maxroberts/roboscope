"""Host-side client for the camera server, plus the SSH tunnel that reaches it.

Details:
    The camera server listens only on the Pi's loopback interface. `connect()` opens an
    SSH local port forward (`ssh -L 47623:127.0.0.1:47623 max@<host>`, key auth,
    BatchMode) and returns a `CameraClient` bound to it. Closing the client closes the
    tunnel.

    With no host given, `pick_host()` chooses the route:
    - **The USB gadget link (`USB_HOST`, 10.12.194.1)** when its SSH port answers within
      0.5 s. That is the 3B+ host Pi's normal case, and the Mac's when the Zero is
      plugged into it.
    - **Otherwise `roboscopeCam.local`** (Wi-Fi).
    - **Why USB first:** measured from the 3B+ on 2026-11-26, a raw frame took ~0.35 s
      over USB against 4-22 s over Wi-Fi, and `locate` 0.3 s against 0.5-0.65 s.
    The tunnel keeps the server off every network, so nothing new listens on the USB
    link. The SSH host key must be known for the chosen address.
    `scripts/deploy_host_pi.sh` records it for both names on the 3B+. `CameraClient(base_url)` also works directly, e.g. against a
    local `--source synthetic` server in the tests.

    `CameraClient.locate()` matches the finder interface `align.AlignmentSession` expects:
    full-sensor pixel (x, y) of the star in a fresh frame, or None. `find_star()` returns
    the whole server result (flux, SNR, saturation, frame metadata).
    Only stdlib + numpy, so it also imports cleanly on the Pi.
"""

import io
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

import numpy as np

DEFAULT_HOST = "roboscopeCam.local"      # Wi-Fi (mDNS); the fallback route
USB_HOST = "10.12.194.1"                 # the Zero's end of the USB gadget link
DEFAULT_USER = "max"
# One fixed, uncommon port (unassigned, checked free on the Mac and the Pi, 2026-11-25),
# used on both ends: the server on the Pi's loopback and the tunnel's local end, so the
# browser URL is always http://127.0.0.1:47623/. 8080 clashes with other local services.
PORT = 47623


class CameraError(RuntimeError):
    """The server returned an error, or couldn't be reached."""


class SshTunnel:
    """SSH local port forward to the server's loopback port; a context manager.

    Raises:
        CameraError: the tunnel didn't come up within `timeout_s` (wrong host, no key,
            or the forward failed).
    """

    def __init__(self, host: str = DEFAULT_HOST, user: str = DEFAULT_USER,
                 remote_port: int = PORT, local_port: int = PORT,
                 timeout_s: float = 15.0):
        self.local_port = local_port or _free_port()
        # Remote `cat` reads our stdin pipe. If this process dies (even SIGKILL, or a
        # `uv run` wrapper that doesn't forward SIGTERM), the pipe closes, cat exits
        # and so does ssh, so the tunnel can't outlive its owner. `-N` would leave it
        # orphaned.
        self.proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             "-o", "ServerAliveInterval=10",
             "-L", f"{self.local_port}:127.0.0.1:{remote_port}", f"{user}@{host}",
             "cat >/dev/null"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise CameraError(f"ssh tunnel exited: {self.proc.stderr.read().decode().strip()}")
            try:
                socket.create_connection(("127.0.0.1", self.local_port), 0.5).close()
                return
            except OSError:
                time.sleep(0.2)
        self.close()
        raise CameraError("ssh tunnel did not come up")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CameraClient:
    """Calls the camera server's HTTP API (see server.py for the endpoints)."""

    def __init__(self, base_url: str, tunnel: SshTunnel | None = None,
                 timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.tunnel = tunnel
        self.timeout_s = timeout_s

    def _req(self, path: str, body: dict | None = None, timeout_s: float | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout_s or self.timeout_s)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", "")
            except ValueError:
                msg = ""
            raise CameraError(f"{path}: HTTP {e.code} {msg}") from None
        except urllib.error.URLError as e:
            raise CameraError(f"{path}: {e.reason}") from None

    def _json(self, path: str, body: dict | None = None, timeout_s: float | None = None):
        with self._req(path, body, timeout_s) as r:
            return json.loads(r.read())

    def status(self) -> dict:
        return self._json("/api/status")

    def settings(self) -> dict:
        return self._json("/api/settings")

    def set(self, **changes) -> dict:
        """Change settings (exposure_ms, gain, mode, …); returns the new settings."""
        return self._json("/api/settings", changes)

    def find_star(self, k_sigma: float = 5.0, min_snr: float = 7.0) -> dict:
        """Full locate result from a fresh frame (see server.py); 'found' may be False."""
        exp_s = self.settings()["exposure_ms"] / 1000
        q = urlencode({"k_sigma": k_sigma, "min_snr": min_snr})
        return self._json(f"/api/locate?{q}", timeout_s=3 * exp_s + 15)

    def locate(self) -> tuple[float, float] | None:
        """Full-sensor pixel of the star in a fresh frame, or None (AlignmentSession finder)."""
        r = self.find_star()
        return (r["x"], r["y"]) if r["found"] else None

    def frame(self) -> tuple[np.ndarray, dict]:
        """A fresh analysis-grid frame (uint16) and its metadata (scale, exposure, …)."""
        exp_s = self.settings()["exposure_ms"] / 1000
        with self._req("/api/frame.npy", timeout_s=3 * exp_s + 30) as r:
            img = np.load(io.BytesIO(r.read()))
            h = r.headers
            meta = {"scale": int(h["X-Scale"]), "exposure_us": float(h["X-Exposure-Us"]),
                    "gain": float(h["X-Gain"]), "seq": int(h["X-Seq"]),
                    "start_ns": int(h["X-Start-Ns"])}
        return img, meta

    def close(self) -> None:
        if self.tunnel:
            self.tunnel.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def pick_host(timeout_s: float = 0.5) -> str:
    """The address to reach the camera Pi at: USB_HOST if its SSH port answers, else
    DEFAULT_HOST.

    A plain TCP connect to port 22, closed at once. It costs at most `timeout_s` when the
    USB link is absent, which is quick because 10.12.194.0/28 is then unrouted. It
    doesn't check that SSH will authenticate; SshTunnel reports that.
    """
    try:
        socket.create_connection((USB_HOST, 22), timeout_s).close()
        return USB_HOST
    except OSError:
        return DEFAULT_HOST


def connect(host: str | None = None, user: str = DEFAULT_USER,
            remote_port: int = PORT, local_port: int = 0) -> CameraClient:
    """Open an SSH tunnel to the camera Pi and return a client using it.

    `host` None (the default) picks the route with `pick_host()`: the USB link if
    present, else Wi-Fi. `local_port` 0 (the default here) picks a free port, so scripts
    can run alongside an open browser view, which holds PORT.
    """
    t = SshTunnel(host or pick_host(), user, remote_port, local_port)
    return CameraClient(f"http://127.0.0.1:{t.local_port}", tunnel=t)
