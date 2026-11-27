"""Camera server: owns the camera, streams it to a browser, and answers roboscope's requests.

Details:
    The same server runs in three roles, chosen by `--source`:
      - **Camera Zero, `--source pi`** (`roboscope-camera` system service): owns the
        camera. Binds 0.0.0.0, but its systemd unit filters IP traffic so only the Zero
        itself (SSH tunnels) and the host Pi's USB address (10.12.194.2) get through.
      - **Host Pi (3B+), `--source remote`** (`roboscope-camera` user service): the
        *hub*. It fetches frames from the Zero's `/api/raw` over the USB link
        (`sources.RemoteSource`), and renders the preview, runs locate and serves the
        UI. Loopback only. This is what roboscope and viewers use, so no viewer
        depends on the Zero's Wi-Fi or CPU for previews.
      - **Any machine, `--source synthetic`:** for development and the tests.
    Clients reach the hub (or any of these) locally or through an SSH tunnel
    (`client.connect`, `scripts/camera_view.py`). SSH keys are the only access control.

    Threads:
      - Capture thread: the only code that touches the source. It applies pending
        settings, then captures continuously, publishing each `Frame` as `latest`
        under a Condition. A long exposure blocks it, so a settings change takes
        effect after the frame in progress.
      - Idle mode, to spare the Pi Zero. With no HTTP request for IDLE_AFTER_S (30 s)
        and no open stream, the source drops to its idle frame rate
        (`set_idle(True)`, 1 fps on the Pi). Any request switches it back. The first
        locate after idling can take a few seconds longer while frames already in
        flight at the slow rate drain (controls take effect ~3 frames later: 3 s+ at
        1 fps).
      - Demand-matched capture. A hub fetching `/api/raw` sends its rate (`fps=`).
        While that's recent (DEMAND_TTL_S) and nobody streams from this server
        directly, the capture rate is capped at DEMAND_HEADROOM (1.1) × that rate via
        the source's `set_max_fps`. Capturing 10 fps that nobody used cost the Zero
        ~46% of its CPU. The trade-off is that a locate waits up to one (slower) frame
        period for a fresh frame.
      - HTTP threads (ThreadingHTTPServer): one per request. MJPEG streams share one
        JPEG per frame (cached by sequence number) and are capped at preview_fps.

    HTTP API (JSON unless noted):
      GET  /                     the browser UI (static/index.html)
      GET  /stream               multipart MJPEG preview (display-stretched, overlays);
                                 each part carries X-Seq and X-Wall-Time (the frame's
                                 capture time, from the camera Pi's clock)
      GET  /snapshot.jpg         one preview JPEG of the latest frame
      GET  /api/status           camera info, settings, capture fps, frame stats,
                                 last_error (only while captures are failing)
      GET  /api/settings         current CameraSettings
      POST /api/settings         JSON object of changes → new settings (400 if invalid)
      GET  /api/locate           star centroid in a *fresh* frame (one whose exposure
                                 started after the request, with the current exposure
                                 and gain). Query: k_sigma, min_snr, timeout_s.
      GET  /api/frame.npy        fresh analysis-grid frame as .npy (uint16), with
                                 X-Scale/X-Exposure-Us/X-Gain/X-Seq/X-Start-Ns headers
      GET  /api/raw              the next frame after `after` (query: after,
                                 timeout_s, passive=1, reduce=1|2|4, fps=<the fetcher's
                                 rate, for demand-matched capture>), raw little-endian
                                 uint16 bytes with X-Seq/X-Scale/X-Reduce/X-Start-Ns/
                                 X-Exposure-Us/X-Gain/X-Saturation/X-Wall-Time/X-Shape
                                 headers. reduce=k max-pools k×k analysis pixels
                                 (stars survive) and multiplies X-Scale by k. This is the
                                 feed for a downstream RemoteSource; with passive=1 the
                                 request doesn't count as activity for idle mode.
    Every response carries X-Now-Ns, the source's clock (the one Frame.start_ns uses).
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
      - optional crosshair at the image centre, and a box around the last located star;
      - a frame stamp in the bottom-left corner, "#<seq> <HH:MM:SS.d>" (sequence number
        and capture wall-clock time on the Pi), drawn with a tiny built-in pixel font
        (`draw_stamp`). It's in the image itself because neither MJPEG nor the
        browser exposes which frame is on screen. A viewer can see each update and
        spot dropped frames even when the image barely changes (no optics).
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
from .sources import PREVIEW_SIZES, CameraSettings, Frame
from .starfind import find_star, frame_stats, to_sensor_px

log = logging.getLogger("roboscope.camera.server")
IDLE_AFTER_S = 30.0                 # no requests or viewers for this long → idle mode
# Demand-matched capture: a downstream hub sends its fetch rate with /api/raw (`fps=`).
# While that is recent and nobody streams from this server directly, the capture rate
# is capped at DEMAND_HEADROOM × that rate (slightly faster, so each fetch finds a new
# frame), instead of the source's maximum.
DEMAND_TTL_S = 5.0
DEMAND_HEADROOM = 1.1
STATIC = Path(__file__).with_name("static")


RAW_REDUCE = (1, 2, 4)              # allowed /api/raw size reductions (per axis)


def max_pool(img: np.ndarray, k: int) -> np.ndarray:
    """Max over k×k blocks (edge rows/columns that don't fill a block are dropped).

    Used to shrink frames for the preview (here and in render_preview), because the
    maximum keeps a small star visible where an average or a subsample could lose it.
    Powers of 2 are done by repeated halving with element-wise np.maximum on strided
    rows and columns: on the Pi Zero a k=2 pool took 72 ms the reshape-and-max way
    (2026-11-26). Other k use reshape-and-max.
    """
    if k == 1:
        return img
    h, w = (img.shape[0] // k) * k, (img.shape[1] // k) * k
    img = img[:h, :w]
    if k & (k - 1) == 0:                                   # power of 2
        while k > 1:
            img = np.maximum(img[0::2], img[1::2])
            img = np.maximum(img[:, 0::2], img[:, 1::2])
            k //= 2
        return img
    return img.reshape(h // k, k, w // k, k).max(axis=(1, 3))


def _encode_jpeg(gray: np.ndarray, quality: int = 70) -> bytes:
    """uint8 H×W → JPEG bytes (simplejpeg: from apt on the camera Pi, a main dependency elsewhere)."""
    import simplejpeg

    return simplejpeg.encode_jpeg(np.ascontiguousarray(gray[:, :, None]),
                                  quality=quality, colorspace="GRAY")


# 3×5 pixel glyphs for the frame stamp, rows top to bottom ("#" = lit). Only the
# characters the stamp uses; anything else is drawn as a blank.
_GLYPHS = {
    "0": ("###", "#.#", "#.#", "#.#", "###"), "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"), "3": ("###", "..#", "###", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"), "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"), "7": ("###", "..#", "..#", "..#", "..#"),
    "8": ("###", "#.#", "###", "#.#", "###"), "9": ("###", "#.#", "###", "..#", "###"),
    ":": ("...", ".#.", "...", ".#.", "..."), ".": ("...", "...", "...", "...", ".#."),
    "#": ("#.#", "###", "#.#", "###", "#.#"),
}
_GLYPH_MASKS = {c: np.array([[ch == "#" for ch in row] for row in g]) for c, g in _GLYPHS.items()}
_SCALED: dict[tuple[str, int], np.ndarray] = {}       # (char, scale) -> enlarged mask


def _scaled_glyph(ch: str, scale: int) -> np.ndarray | None:
    """The glyph mask for `ch` enlarged to scale×scale pixels per cell (cached), or None."""
    key = (ch, scale)
    if key not in _SCALED:
        mask = _GLYPH_MASKS.get(ch)
        _SCALED[key] = None if mask is None else mask.repeat(scale, 0).repeat(scale, 1)
    return _SCALED[key]


def draw_stamp(out: np.ndarray, text: str, scale: int = 2) -> None:
    """Draw `text` in white on a black strip in the bottom-left corner of `out`, in place.

    Each glyph is 3×5 cells of scale×scale pixels with one blank cell between glyphs,
    plus a one-cell margin. Text that would run past the right edge is cut off.
    Characters without a glyph are drawn as blanks. Enlarged glyphs are cached, because
    enlarging them on every frame cost ~40 ms on the Pi Zero.
    """
    h, w = out.shape
    cell, adv = scale, 4 * scale
    y0 = h - 7 * cell                                      # 5 rows + 1-cell margins
    if y0 < 0:
        return
    out[y0:h, 0:min(w, len(text) * adv + cell)] = 0
    for i, ch in enumerate(text):
        x = cell + i * adv
        big = _scaled_glyph(ch, cell)
        if big is None or x + 3 * cell > w:
            continue
        out[y0 + cell:y0 + 6 * cell, x:x + 3 * cell][big] = 255


def render_preview(frame: Frame, s: CameraSettings, fix: dict | None) -> np.ndarray:
    """Display image (uint8) for the browser; see module notes."""
    img = frame.image
    # Total reduction asked for by preview_size, less what the source already applied
    # (the hub's frames arrive reduced), and at least enough to fit preview_width.
    k = max(1, PREVIEW_SIZES[s.preview_size] // frame.reduce,
            int(np.ceil(img.shape[1] / s.preview_width)))
    img = max_pool(img, k)                                 # keep small stars
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
    t = time.localtime(frame.wall_time)
    draw_stamp(out, f"#{frame.seq} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"
                    f".{int(frame.wall_time * 10) % 10}")
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
        # The latest capture failure, shown in /api/status and on the page. Cleared by
        # the next successful capture, so it describes a current problem, not an old one
        # (every failure is also in the log, with its traceback).
        self.last_error = ""
        self.last_fix: dict | None = None
        self._jpeg_cache: tuple[int, bytes] = (-1, b"")
        self.last_activity = time.monotonic()  # any HTTP request
        self.streams = 0                       # open MJPEG viewers
        self.idle = False
        self._full_waiters = 0                 # requests waiting for a full-size frame
        self._demand = (0.0, 0.0)              # (fps, monotonic expiry) from /api/raw
        self._capture_cap: float | None = None # current set_max_fps value
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
                if hasattr(self.source, "want_full"):     # RemoteSource: fetch full size
                    self.source.want_full = self._full_waiters > 0
                if hasattr(self.source, "set_max_fps"):   # demand-matched capture
                    fps, until = self._demand
                    cap = (max(1.0, fps * DEMAND_HEADROOM)
                           if fps and time.monotonic() < until and self.streams == 0 else None)
                    if cap != self._capture_cap:
                        self._capture_cap = cap
                        self.source.set_max_fps(cap)
                        log.info("capture rate cap %s", f"{cap:.1f} fps" if cap else "off")
                frame = self.source.capture()
            except Exception as e:                     # keep serving; report in status
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("capture failed")
                time.sleep(1.0)
                continue
            self.last_error = ""                       # recovered: drop the stale message
            now = time.monotonic()
            self.fps = 0.8 * self.fps + 0.2 / max(now - last, 1e-6)
            last = now
            with self.cond:
                self.latest = frame
                self.cond.notify_all()
        self.source.close()

    # -- settings and frames ------------------------------------------------------

    def update_settings(self, changes: dict) -> CameraSettings:
        """Validate, clamp to the camera's limits, and queue for capture.

        Exposure and gain are clamped to the camera's range. If the source has
        `max_preview_fps(size)` (RemoteSource: the measured limit of the Zero's feed),
        preview_fps is clamped to it for the chosen preview size.
        """
        new = self.settings.updated(changes)
        e_lo, e_hi = self.info.get("exposure_ms_range", [0.01, 10000])
        g_lo, g_hi = self.info.get("gain_range", [1.0, 64.0])
        new = new.updated({"exposure_ms": min(max(new.exposure_ms, e_lo), e_hi),
                           "gain": min(max(new.gain, g_lo), g_hi)})
        if hasattr(self.source, "max_preview_fps"):
            cap = self.source.max_preview_fps(new.preview_size)
            new = new.updated({"preview_fps": min(new.preview_fps, cap)})
        self.settings = new
        self._pending = new
        return new

    def _matches_settings(self, f: Frame) -> bool:
        want_us = self.settings.exposure_ms * 1000
        return (abs(f.exposure_us - want_us) <= max(0.05 * want_us, 100.0)
                and abs(f.gain - self.settings.gain) <= 0.05 * self.settings.gain + 0.05)

    def wait_fresh(self, timeout_s: float, full: bool = False) -> Frame:
        """A frame whose exposure started after this call, taken with current settings.

        With `full`, only a frame at the full analysis grid (`reduce == 1`) counts, and
        while this waits the capture loop asks the source for full-size frames (the
        hub's preview frames may be reduced). locate and the raw download use this.

        Raises:
            TimeoutError: none arrived within timeout_s.
        """
        t_req = self.source.now_ns()
        deadline = time.monotonic() + timeout_s
        with self.cond:
            if full:
                self._full_waiters += 1
            try:
                while True:
                    f = self.latest
                    if (f is not None and f.start_ns >= t_req and self._matches_settings(f)
                            and (not full or f.reduce == 1)):
                        return f
                    left = deadline - time.monotonic()
                    if left <= 0 or not self.running:
                        raise TimeoutError("no fresh frame")
                    self.cond.wait(left)
            finally:
                if full:
                    self._full_waiters -= 1

    def wait_next(self, after_seq: int, timeout_s: float) -> Frame | None:
        with self.cond:
            self.cond.wait_for(lambda: self.latest is not None and self.latest.seq > after_seq
                               or not self.running, timeout_s)
            return self.latest

    def locate(self, k_sigma: float = 5.0, min_snr: float = 7.0,
               timeout_s: float | None = None) -> dict:
        # +10 s covers waking from idle (frames in flight at the idle period).
        timeout_s = timeout_s or 3 * self.settings.exposure_ms / 1000 + 10.0
        f = self.wait_fresh(timeout_s, full=True)
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
                     "scale": f.scale, "reduce": f.reduce, "shape": list(f.image.shape),
                     "background": round(bg, 1), "noise": round(noise, 2),
                     "max": float(f.image.max()),
                     "saturated_fraction": float((f.image >= f.saturation).mean())}
        return {"info": self.info, "settings": self.settings.to_dict(),
                "fps": round(self.fps, 2), "idle": self.idle, "frame": frame, "last_error": self.last_error,
                "temperature_c": _cpu_temp(), "last_fix": self.last_fix,
                # per preview size, when the source limits it (the hub); else null
                "max_preview_fps": ({k: self.source.max_preview_fps(k) for k in PREVIEW_SIZES}
                                    if hasattr(self.source, "max_preview_fps") else None)}


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
            # The source's clock (Frame.start_ns), so a RemoteSource downstream can
            # judge frame freshness in this server's terms.
            self.send_header("X-Now-Ns", str(srv.source.now_ns()))
            for k, v in (headers or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if not (url.path == "/api/raw" and q.get("passive") == "1"):
                srv.last_activity = time.monotonic()      # see idle mode
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
                    f = srv.wait_fresh(timeout, full=True)
                    buf = io.BytesIO()
                    np.save(buf, np.clip(f.image, 0, 65535).astype(np.uint16))
                    self._send(200, buf.getvalue(), "application/octet-stream",
                               {"X-Scale": f.scale, "X-Exposure-Us": f.exposure_us,
                                "X-Gain": f.gain, "X-Seq": f.seq, "X-Start-Ns": f.start_ns,
                                "Content-Disposition":
                                    f'attachment; filename="frame_{f.seq}.npy"'})
                elif url.path == "/api/raw":
                    # Next frame after `after` (RemoteSource's feed): analysis-grid
                    # uint16, little-endian, C order, no header in the body.
                    after = int(q.get("after", -1))
                    timeout = float(q.get("timeout_s", 3 * srv.settings.exposure_ms / 1000 + 5))
                    if "fps" in q:                 # the fetcher's rate: capture to match
                        srv._demand = (float(q["fps"]), time.monotonic() + DEMAND_TTL_S)
                    f = srv.wait_next(after, timeout)
                    if f is None or f.seq <= after:
                        raise TimeoutError("no new frame")
                    k = int(q.get("reduce", 1))
                    if k not in RAW_REDUCE:
                        raise ValueError(f"reduce must be one of {RAW_REDUCE}")
                    img = np.ascontiguousarray(max_pool(f.image, k), dtype="<u2")
                    self._send(200, img.tobytes(), "application/octet-stream",
                               {"X-Seq": f.seq, "X-Scale": f.scale * k, "X-Reduce": f.reduce * k,
                                "X-Start-Ns": f.start_ns,
                                "X-Exposure-Us": f.exposure_us, "X-Gain": f.gain,
                                "X-Saturation": f.saturation, "X-Wall-Time": repr(f.wall_time),
                                "X-Shape": f"{img.shape[0]},{img.shape[1]}"})
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
                    # X-Seq / X-Wall-Time identify the frame (browsers ignore them),
                    # so a client can measure how old each preview is on arrival.
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     + f"X-Seq: {f.seq}\r\nX-Wall-Time: {f.wall_time!r}\r\n"
                                       f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                                     + jpg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass                                       # browser went away
            finally:
                srv.streams -= 1
                srv.last_activity = time.monotonic()

    return Handler


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=("pi", "synthetic", "remote"), default="pi")
    p.add_argument("--remote-url", default="http://10.12.194.1:47623",
                   help="upstream camera server for --source remote (default: the Zero over USB)")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default loopback; reach it via an SSH tunnel)")
    p.add_argument("--port", type=int, default=PORT, help="0 picks a free port")
    p.add_argument("--quality", type=int, default=70,
                   help="preview JPEG quality, 0-100 (lower: less Pi CPU and Wi-Fi)")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from .sources import PicameraSource, RemoteSource, SyntheticSource

    if a.source == "pi":
        source = PicameraSource()
    elif a.source == "remote":
        source = RemoteSource(a.remote_url)
    else:
        source = SyntheticSource()
    srv = CameraServer(source, a.host, a.port, quality=a.quality).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
