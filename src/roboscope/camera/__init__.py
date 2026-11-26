"""Camera: the Pi camera server, its host client, and star finding shared by both.

Details:
    The narrow camera (Pi Zero W + OV5647 at the focuser) is owned by one process on
    the Pi, `server.py`, which runs there as roboscope-camera.service. It listens on
    the Pi's loopback interface only, and everything reaches it through an SSH tunnel:
      - `client.py`: `connect()` opens the tunnel and returns a `CameraClient`. It goes
        over the Zero's USB link (10.12.194.1) when present, else Wi-Fi
        (`pick_host`).
        `CameraClient.locate()` is the finder that `align.AlignmentSession` expects.
        It returns full-sensor (x, y) pixels of the star in a frame taken after the
        call, or None.
      - `scripts/camera_view.py`: the same tunnel plus a browser, for the live view and
        settings UI (`static/index.html`).
    Modules:
      - `starfind`: numpy-only star detection and centroid, run on the Pi so only
        coordinates cross the network; also used on the host.
      - `sources`: `CameraSettings`, `Frame`, `PicameraSource` (picamera2, raw 10-bit
        Bayer, manual exposure; Pi only) and `SyntheticSource` (simulated star, for
        tests and running the UI on the host).
      - `server`: capture thread + HTTP API + MJPEG preview.
      - `client`: `CameraClient`, `SshTunnel`, `connect`.

    Deployment: `scripts/deploy_camera_pi.sh` copies this directory to the Pi as
    /opt/roboscope/roboscope_camera. Every module uses relative imports only and needs
    only numpy and the stdlib (plus picamera2/simplejpeg on the Pi), so this package
    must not import anything else from roboscope.

    Data conventions:
      - Frames are linear raw counts with 2×2 Bayer cells summed (no ISP processing).
      - Positions are full-sensor pixel centres (2592×1944; x right, y down) in raw
        sensor orientation, whatever the sensor mode.

    Measured on the Pi Zero (2026-11-25):
      - locate latency ≈ 0.6–0.8 s in binned mode, ≈ 1.5 s at full resolution, with
        1 ms exposures;
      - capture is capped at 10 fps, where the server process uses ~40% of the CPU;
      - after 30 s with no requests and no viewer it idles at 1 fps (~5% CPU), and
        the next request wakes it (first locate ≈ 0.9 s).
    The browser preview is rendered and JPEG-encoded on the Pi. Shipping raw frames
    over Wi-Fi for the host to render would cost the Pi more: SSH over Wi-Fi moves
    ~0.5–1.5 MB/s, and a raw frame is ~8× the size of a preview JPEG.
    - **Over the USB link (SSH tunnel), measured from the 3B+ on 2026-11-26:** a raw
      frame takes ~0.35 s (1.8 MB/s; the Zero's CPU limits SSH), and `locate`
      ~0.3 s.
    - **The open question:** this could change where previews are rendered. Plain TCP
      on the link reaches 14 MB/s, but the server doesn't listen there; it stays
      loopback-only.
"""

from .client import CameraClient, CameraError, SshTunnel, connect
from .starfind import StarFix, bin_bayer, find_star

__all__ = ["CameraClient", "CameraError", "SshTunnel", "StarFix", "bin_bayer", "connect",
           "find_star"]
