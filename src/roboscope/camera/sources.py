"""Frame sources for the camera server: the real Pi camera, and a simulated one.

Details:
    The server (`server.py`) talks to a source through five methods:
      - `configure(settings)`: apply a `CameraSettings`, reconfiguring the sensor mode
        if it changed;
      - `capture()`: block for the next `Frame`;
      - `set_idle(bool)`: slow the frame rate while nobody uses the camera (the
        server decides when; see server.py);
      - `now_ns()`: the clock that `Frame.start_ns` uses;
      - `info()`: a dict describing the camera.
    Only the server's capture thread calls them, so sources need not be thread-safe.

    `Frame.image` is on the *analysis grid*: the raw Bayer frame with 2×2 cells summed
    (starfind.bin_bayer), uint16, linear, with the black level still in it. `scale`
    is the number of full-sensor pixels per analysis pixel per axis: 2 for the
    full-resolution mode, 4 for the 2×2-binned sensor mode. Coordinates reported to
    clients are always full-sensor pixels (2592×1944 for the OV5647), so they don't
    depend on the mode.

    - `PicameraSource` (Pi only; imports picamera2 when constructed). Manual exposure
      and gain, with AE and AWB off. It reads raw unpacked 10-bit Bayer (SGBRG10), so no
      ISP processing affects the numbers. Measured on the Pi (2026-11-25, picamera2
      from Trixie):
        - the raw buffer is uint8 (h, 2·w); `.view(uint16)` gives (h, w) with values
          right-aligned, 0..1023, so the 2×2-summed clip level is 4092;
        - ExposureTime runs 130 µs to 3.07 s, and AnalogueGain 1 to 63.94;
        - new controls appear in metadata 3 frames after set_controls;
        - SensorTimestamp uses CLOCK_BOOTTIME.
    - `SyntheticSource`: one Gaussian star plus background, shot noise and read noise,
      scaled by exposure × gain. Clipped like the real sensor. New settings take effect
      after a frame of latency, like real hardware. Used by the tests and for running
      the server and UI on the host (`--source synthetic`).
"""

import time
from dataclasses import asdict, dataclass, field

import numpy as np

from .starfind import bin_bayer

MODES = ("binned", "full")


@dataclass
class CameraSettings:
    """Everything a client can change. Units: ms for exposure, linear analogue gain."""
    mode: str = "binned"                   # "binned" (1296×972 sensor mode) | "full"
    exposure_ms: float = 10.0
    gain: float = 1.0
    stretch: str = "auto"                  # display only: "auto" | "manual"
    black: float = 0.0                     # display only (manual stretch), analysis ADU
    white: float = 4000.0
    crosshair: bool = True                 # display only
    preview_fps: float = 2.0               # cap for the MJPEG stream (Pi CPU)
    preview_width: int = 648               # JPEG width for the stream

    def updated(self, changes: dict) -> "CameraSettings":
        """A copy with `changes` applied and validated (not clamped; see CameraServer).

        Raises:
            ValueError: unknown key, unparseable value, or an invalid choice/range.
        """
        d = asdict(self)
        for k, v in changes.items():
            if k not in d:
                raise ValueError(f"unknown setting {k!r}")
            d[k] = type(d[k])(v) if not isinstance(d[k], bool) else _as_bool(v)
        s = CameraSettings(**d)
        if s.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if s.stretch not in ("auto", "manual"):
            raise ValueError("stretch must be 'auto' or 'manual'")
        # Only reject nonsense here; the server clamps to the camera's actual limits.
        if not (s.exposure_ms > 0 and s.gain > 0):
            raise ValueError("exposure_ms and gain must be > 0")
        if not 0.1 <= s.preview_fps <= 30 or not 64 <= s.preview_width <= 2592:
            raise ValueError("preview_fps or preview_width out of range")
        return s

    def to_dict(self) -> dict:
        return asdict(self)


def _as_bool(v) -> bool:
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes", "on")
    return bool(v)


@dataclass
class Frame:
    """One captured frame (see module notes).

    Built from either a ready analysis-grid `image`, or a `raw` Bayer array that is
    binned on first access to `.image` and then cached. Binning lazily matters on the
    Pi Zero: the capture loop runs continuously, but most frames are never looked at,
    and binning every one kept the CPU saturated (measured 2026-11-25).
    """
    image_: np.ndarray | None
    scale: int                 # full-sensor px per analysis px (per axis)
    seq: int
    start_ns: int              # exposure start, in the source's now_ns() clock
    exposure_us: float         # as reported by the camera for this frame
    gain: float
    saturation: float          # analysis-grid value at/above which pixels are clipped
    wall_time: float = field(default_factory=time.time)
    raw: np.ndarray | None = None

    @property
    def image(self) -> np.ndarray:
        """Analysis-grid image (uint16), binned from `raw` on first use."""
        if self.image_ is None:
            self.image_ = bin_bayer(self.raw)   # racing threads at worst bin twice
            self.raw = None
        return self.image_


class SyntheticSource:
    """Simulated camera with one star (see module notes).

    Args:
        star_xy: star position in full-sensor pixels; change `star_xy` at any time.
        flux_per_ms: star electrons (≈ ADU at gain 1) per ms of exposure.
        fwhm_px: star FWHM in full-sensor pixels.
        sky_per_ms: background per sensor pixel per ms.
    """

    SENSOR = (2592, 1944)
    BLACK = 16 * 4             # 2×2-summed black level, like the OV5647's 16/px
    WHITE = 1023 * 4           # 10-bit clip, summed over 4 px

    def __init__(self, star_xy=(1296.0, 972.0), flux_per_ms=2000.0, fwhm_px=8.0,
                 sky_per_ms=0.05, read_noise=2.0, seed=0):
        self.star_xy, self.flux_per_ms, self.fwhm_px = star_xy, flux_per_ms, fwhm_px
        self.sky_per_ms, self.read_noise = sky_per_ms, read_noise
        self.rng = np.random.default_rng(seed)
        self._settings = CameraSettings()
        self._pending: CameraSettings | None = None
        self._seq = 0
        self._t0 = time.monotonic_ns()

    def now_ns(self) -> int:
        return time.monotonic_ns() - self._t0

    def info(self) -> dict:
        return {"model": "synthetic", "sensor_size": list(self.SENSOR),
                "exposure_ms_range": [0.01, 10000], "gain_range": [1.0, 64.0]}

    def configure(self, settings: CameraSettings) -> None:
        self._pending = settings              # takes effect after one more frame

    def set_idle(self, idle: bool) -> None:
        self.idle = idle                      # recorded only; capture speed unchanged

    def capture(self) -> Frame:
        s = self._settings
        scale = 4 if s.mode == "binned" else 2
        w, h = self.SENSOR[0] // scale, self.SENSOR[1] // scale
        start = self.now_ns()
        time.sleep(min(s.exposure_ms / 1000, 2.0) + 0.02)
        sx, sy = self.star_xy
        x, y = (sx + 0.5) / scale - 0.5, (sy + 0.5) / scale - 0.5
        sig = self.fwhm_px / scale / 2.3548
        yy, xx = np.mgrid[:h, :w]
        star = (self.flux_per_ms * s.exposure_ms / (2 * np.pi * sig**2)
                * np.exp(-((xx - x)**2 + (yy - y)**2) / (2 * sig**2)))
        sky = self.sky_per_ms * s.exposure_ms * scale**2
        e = self.rng.poisson(np.maximum(star + sky, 0)).astype(np.float32)
        adu = (e * s.gain + self.BLACK
               + self.rng.normal(0, self.read_noise * scale, (h, w)))
        img = np.clip(adu, 0, self.WHITE).astype(np.uint16)   # like the Pi
        self._seq += 1
        frame = Frame(img, scale, self._seq, start, s.exposure_ms * 1000, s.gain, self.WHITE)
        if self._pending is not None:
            self._settings, self._pending = self._pending, None
        return frame

    def close(self) -> None:
        pass


class PicameraSource:
    """The Pi camera through picamera2: manual exposure, unpacked raw Bayer frames."""

    RAW_SIZES = {"binned": (1296, 972), "full": (2592, 1944)}

    def __init__(self):
        from picamera2 import Picamera2       # Pi only

        self.cam = Picamera2()
        self.props = self.cam.camera_properties
        self._mode = None
        self._settings: CameraSettings | None = None
        self._idle = False
        self._seq = 0
        self.white = 1023 * 4                 # 10-bit clip × 4 pixels per 2×2 cell

    def now_ns(self) -> int:
        return time.clock_gettime_ns(time.CLOCK_BOOTTIME)

    # OV5647 limits measured from an unconfigured Picamera2's camera_controls on the Pi
    # (2026-11-25). Once configured, camera_controls reports limits tied to the current
    # FrameDurationLimits (it showed 66 ms / gain 16), which configure() changes on every
    # exposure, so those values can't be used for clamping.
    EXPOSURE_MS_RANGE = (0.13, 3066.985)
    GAIN_RANGE = (1.0, 63.9375)
    MIN_FRAME_US = 100_000              # ≤ 10 fps; see configure()
    IDLE_FRAME_US = 1_000_000           # 1 fps while nobody is using the camera

    def info(self) -> dict:
        size = max(m["size"] for m in self.cam.sensor_modes)
        return {"model": self.props.get("Model"), "sensor_size": list(size),
                "exposure_ms_range": list(self.EXPOSURE_MS_RANGE),
                "gain_range": list(self.GAIN_RANGE)}

    def configure(self, settings: CameraSettings) -> None:
        if settings.mode != self._mode:
            if self._mode is not None:
                self.cam.stop()
            cfg = self.cam.create_still_configuration(
                main={"size": (320, 240), "format": "YUV420"},   # unused but required
                raw={"size": self.RAW_SIZES[settings.mode], "format": "SGBRG10"},
                buffer_count=2)
            self.cam.configure(cfg)
            self._mode = settings.mode
            self.cam.start()
        self._settings = settings
        self._apply_controls()

    def set_idle(self, idle: bool) -> None:
        """Idle: stretch the frame period to IDLE_FRAME_US (exposure unchanged)."""
        self._idle = idle
        if self._settings is not None:
            self._apply_controls()

    def _apply_controls(self) -> None:
        settings = self._settings
        exp_us = int(settings.exposure_ms * 1000)
        # Frame period: the exposure, but never shorter than MIN_FRAME_US (or
        # IDLE_FRAME_US when idle). Uncapped, the binned mode ran at ~15 fps and the
        # Zero's CPU was saturated by capture alone, which slowed every locate.
        floor = self.IDLE_FRAME_US if self._idle else self.MIN_FRAME_US
        period = max(exp_us, floor)
        self.cam.set_controls({
            "AeEnable": False, "AwbEnable": False,
            "ExposureTime": exp_us, "AnalogueGain": float(settings.gain),
            "FrameDurationLimits": (period, period + 20_000)})

    def capture(self) -> Frame:
        req = self.cam.capture_request()
        try:
            md = req.get_metadata()
            raw = req.make_array("raw")
        finally:
            req.release()
        w = self.RAW_SIZES[self._mode][0]
        self._seq += 1
        exp_us = float(md.get("ExposureTime", 0))
        # SensorTimestamp is CLOCK_BOOTTIME ns (checked on the Pi, 2026-11-25). Whether it
        # marks exposure start or readout start wasn't determined, so subtract the
        # exposure: this never calls a frame fresher than it is, and at worst a fresh
        # frame waits one more frame time.
        start = int(md["SensorTimestamp"]) - int(exp_us * 1000)
        return Frame(None, 2 if self._mode == "full" else 4, self._seq, start, exp_us,
                     float(md.get("AnalogueGain", 0)), self.white,
                     raw=raw.view(np.uint16)[:, :w])     # binned lazily, see Frame

    def close(self) -> None:
        self.cam.stop()
        self.cam.close()
