"""Camera server: owns the camera, streams it to a browser, and answers roboscope's requests.

Details:
    Runs on the camera Pi as the `roboscope-camera` systemd service
    (`python3 -m roboscope_camera.server --source pi`), or on the host with
    `--source synthetic` for development and tests. It binds to 127.0.0.1 by default.
    Clients reach it through an SSH tunnel (`client.connect`,
    `scripts/camera_view.py`), so no network port is opened and SSH keys are the only
    access control.

    Threads:
      - Capture thread: the only code that touches the source. It applies pending
        settings, then captures continuously, publishing each `Frame` as `latest`
        under a Condition. A long exposure blocks it, so a settings change takes
        effect after the frame in progress.
      - Idle mode, to spare the Pi Zero. With no HTTP request for IDLE_AFTER_S (30 s)
        and no open stream, the source drops to its idle frame rate
        (`set_idle(True)`, 1 fps on the Pi). Any request switches it back. The first
        locate after idling can take a few seconds longer while frames already in
        flight at the slow rate drain.
      - HTTP threads (ThreadingHTTPServer): one per request. MJPEG streams share one
        JPEG per frame (cached by sequence number) and are capped at preview_fps.

    HTTP API (JSON unless noted):
      GET  /                     the browser UI (static/index.html)
      GET  /stream               multipart MJPEG preview (display-stretched, overlays)
      GET  /snapshot.jpg         one preview JPEG of the latest frame
      GET  /api/status           camera info, settings, capture fps, frame stats
      GET  /api/settings         current CameraSettings
      POST /api/settings         JSON object of changes → new settings (400 if invalid)
      GET  /api/locate           star centroid in a *fresh* frame (one whose exposure
                                 started after the request, with the current exposure
                                 and gain). Query: k_sigma, min_snr, timeout_s.
      GET  /api/frame.npy        fresh analysis-grid frame as .npy (uint16), with
                                 X-Scale/X-Exposure-Us/X-Gain/X-Seq/X-Start-Ns headers
    /api/locate returns {"found": bool, "x", "y" (full-sensor px), and the StarFix
    fields, "frame": {...}}. Coordinates are full-sensor pixel centres (x right,
    y down, raw sensor orientation; no flips applied).

    Streaming follows depth_camera's MjpegServer (depth_camera.common.mjpeg, on its
    GitHub main): stdlib ThreadingHTTPServer, `/` page, `/stream` as
    multipart/x-mixed-replace with boundary `frame`, one JPEG per frame shared by all
    viewers, loopback by default, port 0 for a free port, JPEG `--quality`. It differs
    deliberately in three ways:
      - JPEGs are made only while someone is watching, at most preview_fps, instead of
        on every publish. On the Pi Zero an encode costs ~200 ms, so encoding every
        frame would burn CPU with nobody watching.
      - simplejpeg instead of OpenCV, which is a heavy install on ARMv6.
      - A settings panel instead of forwarding keys and mouse events to the program.

    Display (preview only, never the data):
      - max-pooled down to preview_width, so small stars survive;
      - stretched to 8 bits, either "auto" (0.5th–99.95th percentile of a 1-in-16
        subsample) or "manual" (black/white in analysis ADU), via a lookup table for
        uint16 frames;
      - optional crosshair at the image centre, and a box around the last located star.
"""

import argparse
import io
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from .client import PORT
from .sources import CameraSettings, Frame
from .starfind import find_star, frame_stats, to_sensor_px

log = logging.getLogger("roboscope.camera.server")
IDLE_AFTER_S = 30.0                 # no requests or viewers for this long → idle mode
STATIC = Path(__file__).with_name("static")


def _encode_jpeg(gray: np.ndarray, quality: int = 70) -> bytes:
    """uint8 H×W → JPEG bytes (simplejpeg, installed on the Pi and as a dev dependency)."""
    import simplejpeg

    return simplejpeg.encode_jpeg(np.ascontiguousarray(gray[:, :, None]),
                                  quality=quality, colorspace="GRAY")


def render_preview(frame: Frame, s: CameraSettings, fix: dict | None) -> np.ndarray:
    """Display image (uint8) for the browser; see module notes."""
    img = frame.image
    k = max(1, int(np.ceil(img.shape[1] / s.preview_width)))
    if k > 1:                                              # max-pool: keep small stars
        h, w = (img.shape[0] // k) * k, (img.shape[1] // k) * k
        img = img[:h, :w].reshape(h // k, k, w // k, k).max(axis=(1, 3))
    if s.stretch == "auto":
        # From a 1-in-16 subsample: stars mostly fall outside it and display clipped to
        # white, which is fine for a preview. One hot pixel can't set the white point.
        lo, hi = np.percentile(img[::4, ::4], [0.5, 99.95])
    else:
        lo, hi = s.black, s.white
    hi = max(hi, lo + 1.0)
    if img.dtype == np.uint16:
        # Lookup table: one fancy-index instead of float maths on every pixel (~6× faster
        # on the Pi Zero).
        lut = np.clip((np.arange(65536, dtype=np.float32) - lo) * (255.0 / (hi - lo)),
                      0, 255).astype(np.uint8)
        out = lut[img]
    else:
        out = np.clip((img - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
    h, w = out.shape
    if s.crosshair:                                        # black/white dots: visible
        out[h // 2, ::4], out[h // 2, 2::4] = 255, 0       # on any background
        out[::4, w // 2], out[2::4, w // 2] = 255, 0
    if fix and fix.get("found") and fix["frame"]["seq"] >= frame.seq - 50:
        # Box around the last star (sensor px → preview px), kept for ~50 frames.
        f = frame.scale * k
        cx, cy = int(fix["x"] / f), int(fix["y"] / f)
        r = 6
        y0, y1, x0, x1 = max(cy - r, 0), min(cy + r, h - 1), max(cx - r, 0), min(cx + r, w - 1)
        out[y0, x0:x1 + 1] = out[y1, x0:x1 + 1] = 255
        out[y0:y1 + 1, x0] = out[y0:y1 + 1, x1] = 255
    return out


class CameraServer:
    """Capture loop + HTTP server around one frame source (see module notes)."""

    def __init__(self, source, host: str = "127.0.0.1", port: int = PORT,
                 settings: CameraSettings | None = None, quality: int = 70):
        self.source = source
        self.quality = quality                # preview JPEG quality, 0–100
        self.info = source.info()
        self.settings = settings or CameraSettings()
        self._pending: CameraSettings | None = self.settings
        self.latest: Frame | None = None
        self.cond = threading.Condition()
        self.running = False
        self.fps = 0.0
        self.last_error = ""
        self.last_fix: dict | None = None
        self._jpeg_cache: tuple[int, bytes] = (-1, b"")
        self.last_activity = time.monotonic()  # any HTTP request
        self.streams = 0                       # open MJPEG viewers
        self.idle = False
        self.httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self.httpd.daemon_threads = True
        self.address = self.httpd.server_address

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> "CameraServer":
        self.running = True
        threading.Thread(target=self._capture_loop, name="capture", daemon=True).start()
        threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True).start()
        log.info("serving on http://%s:%d", *self.address)
        return self

    def stop(self) -> None:
        self.running = False
        self.httpd.shutdown()
        self.httpd.server_close()
        with self.cond:
            self.cond.notify_all()

    def _capture_loop(self) -> None:
        last = time.monotonic()
        while self.running:
            try:
                if self._pending is not None:
                    pending, self._pending = self._pending, None
                    self.source.configure(pending)
                idle = (self.streams == 0
                        and time.monotonic() - self.last_activity > IDLE_AFTER_S)
                if idle != self.idle:
                    self.idle = idle
                    self.source.set_idle(idle)
                    log.info("idle mode %s", "on" if idle else "off")
                frame = self.source.capture()
            except Exception as e:                     # keep serving; report in status
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("capture failed")
                time.sleep(1.0)
                continue
            now = time.monotonic()
            self.fps = 0.8 * self.fps + 0.2 / max(now - last, 1e-6)
            last = now
            with self.cond:
                self.latest = frame
                self.cond.notify_all()
        self.source.close()

    # -- settings and frames ------------------------------------------------------

    def update_settings(self, changes: dict) -> CameraSettings:
        """Validate, clamp exposure/gain to the camera's range, and queue for capture."""
        new = self.settings.updated(changes)
        e_lo, e_hi = self.info.get("exposure_ms_range", [0.01, 10000])
        g_lo, g_hi = self.info.get("gain_range", [1.0, 64.0])
        new = new.updated({"exposure_ms": min(max(new.exposure_ms, e_lo), e_hi),
                           "gain": min(max(new.gain, g_lo), g_hi)})
        self.settings = new
        self._pending = new
        return new

    def _matches_settings(self, f: Frame) -> bool:
        want_us = self.settings.exposure_ms * 1000
        return (abs(f.exposure_us - want_us) <= max(0.05 * want_us, 100.0)
                and abs(f.gain - self.settings.gain) <= 0.05 * self.settings.gain + 0.05)

    def wait_fresh(self, timeout_s: float) -> Frame:
        """A frame whose exposure started after this call, taken with current settings.

        Raises:
            TimeoutError: none arrived within timeout_s.
        """
        t_req = self.source.now_ns()
        deadline = time.monotonic() + timeout_s
        with self.cond:
            while True:
                f = self.latest
                if f is not None and f.start_ns >= t_req and self._matches_settings(f):
                    return f
                left = deadline - time.monotonic()
                if left <= 0 or not self.running:
                    raise TimeoutError("no fresh frame")
                self.cond.wait(left)

    def wait_next(self, after_seq: int, timeout_s: float) -> Frame | None:
        with self.cond:
            self.cond.wait_for(lambda: self.latest is not None and self.latest.seq > after_seq
                               or not self.running, timeout_s)
            return self.latest

    def locate(self, k_sigma: float = 5.0, min_snr: float = 7.0,
               timeout_s: float | None = None) -> dict:
        # +10 s covers waking from idle (frames in flight at the idle period).
        timeout_s = timeout_s or 3 * self.settings.exposure_ms / 1000 + 10.0
        f = self.wait_fresh(timeout_s)
        fix = find_star(f.image, k_sigma=k_sigma, min_snr=min_snr,
                        saturation=f.saturation)
        meta = {"seq": f.seq, "exposure_us": f.exposure_us, "gain": f.gain,
                "start_ns": f.start_ns, "wall_time": f.wall_time, "scale": f.scale}
        if fix is None:
            out = {"found": False, "frame": meta}
        else:
            x, y = to_sensor_px(fix.x, fix.y, f.scale)
            out = {"found": True, **fix.to_dict(), "x": x, "y": y, "frame": meta}
        self.last_fix = out
        return out

    def preview_jpeg(self, frame: Frame) -> bytes:
        seq, data = self._jpeg_cache
        if seq != frame.seq:
            data = _encode_jpeg(render_preview(frame, self.settings, self.last_fix),
                                self.quality)
            self._jpeg_cache = (frame.seq, data)
        return data

    def status(self) -> dict:
        f = self.latest
        frame = None
        if f is not None:
            bg, noise = frame_stats(f.image)
            frame = {"seq": f.seq, "age_s": round(time.time() - f.wall_time, 2),
                     "exposure_us": f.exposure_us, "gain": round(f.gain, 3),
                     "scale": f.scale, "shape": list(f.image.shape),
                     "background": round(bg, 1), "noise": round(noise, 2),
                     "max": float(f.image.max()),
                     "saturated_fraction": float((f.image >= f.saturation).mean())}
        return {"info": self.info, "settings": self.settings.to_dict(),
                "fps": round(self.fps, 2), "idle": self.idle, "frame": frame, "last_error": self.last_error,
                "temperature_c": _cpu_temp(), "last_fix": self.last_fix}


def _cpu_temp() -> float | None:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def _make_handler(srv: CameraServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):            # route to logging, quietly
            log.debug("%s " + fmt, self.address_string(), *args)

        def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            srv.last_activity = time.monotonic()
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(),
                               "text/html; charset=utf-8")
                elif url.path == "/api/status":
                    self._json(srv.status())
                elif url.path == "/api/settings":
                    self._json(srv.settings.to_dict())
                elif url.path == "/api/locate":
                    self._json(srv.locate(float(q.get("k_sigma", 5.0)),
                                          float(q.get("min_snr", 7.0)),
                                          float(q["timeout_s"]) if "timeout_s" in q else None))
                elif url.path == "/api/frame.npy":
                    timeout = float(q.get("timeout_s", 3 * srv.settings.exposure_ms / 1000 + 5))
                    f = srv.wait_fresh(timeout)
                    buf = io.BytesIO()
                    np.save(buf, np.clip(f.image, 0, 65535).astype(np.uint16))
                    self._send(200, buf.getvalue(), "application/octet-stream",
                               {"X-Scale": f.scale, "X-Exposure-Us": f.exposure_us,
                                "X-Gain": f.gain, "X-Seq": f.seq, "X-Start-Ns": f.start_ns,
                                "Content-Disposition":
                                    f'attachment; filename="frame_{f.seq}.npy"'})
                elif url.path == "/snapshot.jpg":
                    f = srv.latest or srv.wait_next(-1, 10)
                    if f is None:
                        raise TimeoutError("no frame yet")
                    self._send(200, srv.preview_jpeg(f), "image/jpeg")
                elif url.path == "/stream":
                    self._stream()
                else:
                    self._json({"error": "not found"}, 404)
            except TimeoutError as e:
                self._json({"error": str(e)}, 504)
            except (ValueError, KeyError) as e:
                self._json({"error": str(e)}, 400)

        def do_POST(self):
            srv.last_activity = time.monotonic()
            if urlparse(self.path).path != "/api/settings":
                return self._json({"error": "not found"}, 404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                changes = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(changes, dict):
                    raise ValueError("expected a JSON object")
                self._json(srv.update_settings(changes).to_dict())
            except (ValueError, TypeError) as e:
                self._json({"error": str(e)}, 400)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq, t_last = -1, 0.0
            srv.streams += 1                               # GIL makes += safe enough
            try:
                while srv.running:
                    f = srv.wait_next(seq, 5.0)
                    if f is None or f.seq == seq:
                        continue
                    wait = 1.0 / srv.settings.preview_fps - (time.monotonic() - t_last)
                    if wait > 0:
                        time.sleep(wait)
                        f = srv.latest
                    seq, t_last = f.seq, time.monotonic()
                    jpg = srv.preview_jpeg(f)
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     + f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                                     + jpg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass                                       # browser went away
            finally:
                srv.streams -= 1
                srv.last_activity = time.monotonic()

    return Handler


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=("pi", "synthetic"), default="pi")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default loopback; reach it via an SSH tunnel)")
    p.add_argument("--port", type=int, default=PORT, help="0 picks a free port")
    p.add_argument("--quality", type=int, default=70,
                   help="preview JPEG quality, 0-100 (lower: less Pi CPU and Wi-Fi)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from .sources import PicameraSource, SyntheticSource

    source = PicameraSource() if a.source == "pi" else SyntheticSource()
    srv = CameraServer(source, a.host, a.port, quality=a.quality).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
