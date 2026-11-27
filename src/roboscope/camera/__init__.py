"""Camera: the camera server (on both Pis), its client, and star finding.

Details:
    The narrow camera (Pi Zero W + OV5647 at the focuser) is served in two layers, both
    by `server.py`:
      - **Camera Zero** (`--source pi`, system service `roboscope-camera`): owns the
        camera, captures, bins, and offers each frame on `/api/raw`. It listens on
        0.0.0.0, but a systemd IP filter admits only the Zero itself and the host Pi's
        USB address.
      - **Host Pi, 3B+** (`--source remote`, user service `roboscope-camera`): the
        *hub*. `sources.RemoteSource` fetches frames from the Zero over the USB link at
        the preview's size and rate. `preview_size` is full, half or quarter (the Zero
        max-pools before sending), and `preview_fps` is capped per size at the measured
        limits: 6, 7 and 8 fps. The Zero captures only ~1.1× as fast as the hub fetches
        (demand-matched capture). Locate and raw downloads always fetch full-size frames.
        The hub renders the browser preview, runs locate and serves the UI on the 3B+'s
        loopback.
    Clients:
      - `client.py`: `connect()` returns a `CameraClient` for the hub, directly on the
        3B+ and through an SSH tunnel to `roboscope.local` elsewhere.
        `CameraClient.locate()` is the finder that `align.AlignmentSession` expects:
        full-sensor (x, y) pixels of the star in a frame taken after the call, or None.
      - `scripts/camera_view.py` and `scripts/camera_view_via_host.sh` (plain ssh): the
        same tunnel plus a browser, for the live view and settings UI
        (`static/index.html`). The shell script shares the tunnel on the LAN by default
        (no login).
    Modules:
      - `starfind`: numpy-only star detection and centroid (on the hub, and on the Zero
        for its own locate).
      - `sources`: `CameraSettings`, `Frame`, `PicameraSource` (picamera2, raw 10-bit
        Bayer, manual exposure; Zero only), `SyntheticSource` (simulated star, for tests
        and running the UI anywhere) and `RemoteSource` (frames from another server).
      - `server`: capture thread + HTTP API + MJPEG preview.
      - `client`: `CameraClient`, `SshTunnel`, `connect`.

    Deployment:
      - `scripts/deploy_camera_pi.sh` copies this directory to the Zero as
        /opt/roboscope/roboscope_camera. Every module uses relative imports only and
        needs only numpy and the stdlib (plus picamera2/simplejpeg there), so this
        package must not import anything else from roboscope.
      - `scripts/deploy_host_pi.sh` puts the whole repo on the 3B+ and restarts the hub.

    Data conventions:
      - Frames are linear raw counts with 2×2 Bayer cells summed (no ISP processing).
      - Positions are full-sensor pixel centres (2592×1944; x right, y down) in raw
        sensor orientation, whatever the sensor mode.

    Measured (2026-11-26, binned mode, hub pulling 4 fps, one viewer):
      - **Zero:** capture is capped at 10 fps. The server process used 65% of the CPU
        (24% idle). Each frame shipped costs about what rendering a preview frame used
        to.
      - **Zero idle:** with no hub activity for 30 s the hub fetches passively, the Zero
        drops to 1 fps, and its server uses ~12% CPU.
      - **3B+:** the hub uses ~10% CPU.
      - **From the Mac through a tunnel to the hub:** round trip 30 ms, preview 2 fps,
        frame age at the hub 0.2–0.45 s, locate ~0.5 s, raw frame 0.4–0.6 s.
      - **Before the hub** (preview rendered on the Zero, viewers tunnelled to it), the
        Zero's server used 51% with one viewer. But any viewer over the Zero's Wi-Fi saw
        1.6 s average pings until its power save was turned off.
"""

from .client import CameraClient, CameraError, SshTunnel, connect
from .starfind import StarFix, bin_bayer, find_star

__all__ = ["CameraClient", "CameraError", "SshTunnel", "StarFix", "bin_bayer", "connect",
           "find_star"]
