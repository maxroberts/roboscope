"""Open the camera Pi's live view in a browser, through an SSH tunnel.

Details:
    The camera server (roboscope.camera.server, running on the Pi as
    roboscope-camera.service) only listens on the Pi's loopback interface. This script
    opens an SSH port forward, over the USB link if present, else Wi-Fi
    (`client.pick_host`), unless `--host` is given (`-L 47623:127.0.0.1:47623`, key auth; the local port can
    be changed with `--port`), prints and opens http://127.0.0.1:47623/, and keeps the
    tunnel up until Ctrl-C. The page shows the
    stream, exposure/gain/mode and display settings, star locate, and snapshot/raw
    downloads.

    `--synthetic` instead runs a local server with the simulated camera, for trying the
    UI without the Pi.

Usage:
    uv run python scripts/camera_view.py [--host 10.12.194.1|roboscopeCam.local] [--port N]
    uv run python scripts/camera_view.py --synthetic
"""

import argparse
import signal
import sys
import time
import webbrowser

from roboscope.camera.client import DEFAULT_USER, PORT, SshTunnel, pick_host


def main() -> None:
    p = argparse.ArgumentParser(description="Camera Pi live view in the browser.")
    p.add_argument("--host", default=None,
                   help="camera Pi address (default: USB link if present, else roboscopeCam.local)")
    p.add_argument("--user", default=DEFAULT_USER)
    p.add_argument("--port", type=int, default=PORT,
                   help=f"local port (default {PORT}, same every run; 0 = any free port)")
    p.add_argument("--synthetic", action="store_true", help="local simulated camera")
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()

    # Fixed port by default, so the URL is the same every run; --port 0 picks a free one.
    if a.synthetic:
        from roboscope.camera.server import CameraServer
        from roboscope.camera.sources import SyntheticSource

        srv = CameraServer(SyntheticSource(), "127.0.0.1", a.port).start()
        port, closer = srv.address[1], srv.stop
    else:
        host = a.host or pick_host()
        print(f"camera Pi via {host}")
        tunnel = SshTunnel(host, a.user, local_port=a.port)
        port, closer = tunnel.local_port, tunnel.close
    # SIGTERM (e.g. pkill) must also close the tunnel, not just Ctrl-C.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    url = f"http://127.0.0.1:{port}/"
    print(f"camera view at {url}  (Ctrl-C to close)", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        closer()


if __name__ == "__main__":
    main()
