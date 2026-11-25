"""Hardware calibration file: measured properties of the physical instrument, with provenance.

Details:
    One JSON file (default config/hardware_calibration.json, committed, so its history
    is the calibration history) describes the instrument independently of any one
    night's setup:
      - mount: counts per degree per axis, backlash per axis, speed cap, cable limits;
      - pointing: the pointing-model *hardware* terms (CA, NPAE, TF; SA/SE ideally 0,
        see below) that stay fixed between sessions;
      - cameras: CameraFrame parameters per camera name ("narrow", "wide");
      - imu: accelerometer/magnetometer calibration (placeholder until the sensor is
        mounted).
    Every number is a `Measured`: value, 1-σ uncertainty, date, method, and an optional
    note. Unknown values are stored as null with a note saying how to measure them, so
    gaps are explicit.

    Scale bookkeeping: counts/° and the model's SA/SE terms describe the same error.
    `absorb_scale` folds a fitted SA/SE into counts/° (true = mount·(1 + SA), so the
    corrected counts/° = old / (1 + SA)) and zeroes SA/SE in the model, so the error is
    only stored once.

    Used by: scripts and sessions that build a MotorController (counts_per_degree), an
    AlignmentSession (keep=HARDWARE_TERMS with `pointing_model()`), or a CameraFrame
    (`camera_frame(name)`).
"""

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path("config/hardware_calibration.json")
SCHEMA = 1


@dataclass(frozen=True)
class Measured:
    """A calibrated number with its 1-σ uncertainty and provenance. `value` may be None
    (not yet measured; `note` says how to)."""
    value: float | None
    sigma: float | None = None
    date: str = ""
    method: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None


def _m(d: dict | None) -> Measured:
    return Measured(**d) if d else Measured(None)


class HardwareCalibration:
    """Load, query, update and save the calibration file (see module notes)."""

    def __init__(self, data: dict, path: Path = DEFAULT_PATH):
        if data.get("schema") != SCHEMA:
            raise ValueError(f"unsupported calibration schema {data.get('schema')!r}")
        self.data, self.path = data, Path(path)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "HardwareCalibration":
        return cls(json.loads(Path(path).read_text()), Path(path))

    def save(self, path: str | Path | None = None) -> None:
        Path(path or self.path).write_text(json.dumps(self.data, indent=2) + "\n")

    # -- accessors ----------------------------------------------------------------

    def get(self, *keys: str) -> Measured:
        """Measured at a key path, e.g. get('mount', 'counts_per_degree', 'az')."""
        node = self.data
        for k in keys:
            node = node.get(k) if isinstance(node, dict) else None
        return _m(node)

    def set(self, keys: tuple, value: float | None, sigma: float | None, method: str,
            note: str = "", when: str | None = None) -> None:
        """Record a new measurement at a key path (date defaults to today)."""
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = asdict(Measured(value, sigma, when or date.today().isoformat(),
                                         method, note))

    def counts_per_degree(self) -> dict:
        """{'az': .., 'alt': ..} for MotorController.

        Raises:
            ValueError: if either axis is unmeasured.
        """
        out = {}
        for axis in ("az", "alt"):
            m = self.get("mount", "counts_per_degree", axis)
            if not m.known:
                raise ValueError(f"counts_per_degree for {axis} not calibrated")
            out[axis] = m.value
        return out

    def pointing_model(self):
        """PointingModel with the stored hardware terms (setup terms 0)."""
        from roboscope.align.model import HARDWARE_TERMS, PointingModel, TERM_ORDER

        params = {k: 0.0 for k in TERM_ORDER}
        for k in HARDWARE_TERMS:
            m = self.get("pointing", k)
            if m.known:
                params[k] = m.value
        return PointingModel(params)

    def camera_frame(self, name: str):
        """CameraFrame for camera `name` from its stored parameters.

        Raises:
            ValueError: if the camera or its plate scale isn't calibrated.
        """
        from roboscope.align.camera_frame import CameraFrame

        cam = self.data.get("cameras", {}).get(name)
        if not cam or not _m(cam.get("scale_arcsec_px")).known:
            raise ValueError(f"camera {name!r} not calibrated")
        v = lambda k, d: _m(cam.get(k)).value if _m(cam.get(k)).known else d  # noqa: E731
        bx, by = v("boresight_x_px", None), v("boresight_y_px", None)
        return CameraFrame(v("scale_arcsec_px", None), v("roll_deg", 0.0),
                           bool(v("flip_x", 0)), int(v("width_px", 3280)),
                           int(v("height_px", 2464)),
                           (bx, by) if bx is not None and by is not None else None)

    # -- updates from fits ---------------------------------------------------------------

    def absorb_scale(self, model, method: str, sigma_fraction: dict | None = None) -> dict:
        """Fold the model's SA/SE into counts/° and zero them in `model`.

        Returns the new counts/° per axis. `sigma_fraction` optionally gives the
        relative 1-σ of the fitted scale per axis, e.g. {'az': 0.001}. It's the
        caller's estimate and isn't combined with anything else.

        Changing counts/° changes what every degree reading means. Positions and setup
        terms (IA, IE, …) computed with the old value are stale, so reconnect the
        controller with the new counts/° and refit before relying on them.
        """
        new = {}
        for axis, term in (("az", "SA"), ("alt", "SE")):
            k = model.params.get(term, 0.0)
            old = self.get("mount", "counts_per_degree", axis)
            if not old.known:
                raise ValueError(f"counts_per_degree for {axis} not calibrated")
            value = old.value / (1.0 + k)
            frac = (sigma_fraction or {}).get(axis)
            self.set(("mount", "counts_per_degree", axis), round(value, 2),
                     round(value * frac, 2) if frac else old.sigma, method,
                     note=f"previous {old.value} ({old.date}); fitted {term}={k:+.5f}")
            model.params[term] = 0.0
            new[axis] = value
        return new
