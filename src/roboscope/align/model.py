"""Pointing model: mount encoder angles ↔ true sky direction, fitted from observations.

Details:
    The mount reports *mount angles* (`MountAngles`): encoder degrees relative to the
    controller's zero (roboscope.mount.controller). They differ from the true apparent
    alt/az (`roboscope.sky.AltAz`) by the mount's systematic errors. This module models
    those errors with standard alt-az terms, fits them by linear least squares, and maps
    both ways.

    Terms (all in degrees, or dimensionless for scales). With A = mount az, E = mount alt:
        d_az  = IA + SA·A + CA·sec E + NPAE·tan E + (AN·sin A − AW·cos A)·tan E
        d_alt = IE + SE·E + AN·cos A + AW·sin A + TF·cos E
        true  = mount + d
      - IA, IE: index offsets (encoder zero vs north/level).
      - AN, AW: azimuth-axis tilt (tripod not level), north/west components.
      - CA: collimation (optical axis not perpendicular to the alt axis).
      - NPAE: alt and az axes not perpendicular.
      - SA, SE: scale errors (our counts/° are ±2–3%).
      - TF: tube flex.
    The sign conventions are ours. Every term is fitted, so a convention only affects
    how a value reads, not whether the model works. Trig terms use the mount angles
    (≈ true to within the corrections, which is accurate enough for small corrections).
    Near the zenith tan E blows up; altitudes are clamped to 85° in the trig terms, and
    observations above ~80° should be avoided.

    Fitting (`PointingModel.fit`):
      - Residuals are angular: alt error, and az error × cos(alt), so azimuth isn't
        overweighted near the zenith. Weights are 1/σ².
      - With few observations only the leading terms in `TERM_ORDER` are fitted: 1
        observation → IA, IE; 2 → +AN, AW; then 2·n − 2 terms, leaving redundancy so
        the residuals mean something.
      - Terms the geometry can't determine (rank-deficient) are dropped from the end.

    Inverse (`sky_to_mount`): fixed-point iteration, converging to ≪1″ for corrections of
    a few degrees.

    Hardware vs setup terms: SA/SE (counts/° errors) and CA/NPAE/TF belong to the
    hardware, so they stay put between sessions. IA/IE/AN/AW change with each tripod
    setup. `fit(..., keep=HARDWARE_TERMS)` holds the kept terms at their current
    values (e.g. loaded with `PointingModel.load`) and fits only the rest, so a 3-star
    session can still point well across the sky. Simulation (tests/test_align.py): 3
    stars without kept terms leave up to ~50′ errors far from the stars; 5 stars fit
    everything.

    Backlash is not a model term: it depends on the approach direction, which the motion
    layer controls (always finish a move in the same direction).
"""

import math
from dataclasses import dataclass, field, replace
from datetime import datetime

import numpy as np

from roboscope.sky import AltAz

TERM_ORDER = ("IA", "IE", "AN", "AW", "CA", "SA", "SE", "NPAE", "TF")
HARDWARE_TERMS = ("CA", "SA", "SE", "NPAE", "TF")
MAX_TRIG_ALT = 85.0


@dataclass(frozen=True)
class MountAngles:
    """Encoder-derived mount angles in degrees (controller frame; az may exceed 0..360)."""
    az_deg: float
    alt_deg: float


@dataclass(frozen=True)
class Observation:
    """Where the mount said it was, and where it really pointed.

    `source` is free text ('narrow', 'wide', 'imu', 'sim', …). `sigma_deg` is the 1-σ
    uncertainty of `sky` (e.g. ~0.001° for a centroided star, ~0.5° for a magnetometer).
    """
    when: datetime
    mount: MountAngles
    sky: AltAz
    sigma_deg: float = 0.002
    source: str = ""


def _wrap180(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


def _partials(m: MountAngles) -> tuple[dict, dict]:
    """∂d_az/∂term and ∂d_alt/∂term at mount angles m."""
    a = math.radians(m.az_deg)
    e = math.radians(min(m.alt_deg, MAX_TRIG_ALT))
    t, sec = math.tan(e), 1.0 / math.cos(e)
    d_az = {"IA": 1.0, "SA": m.az_deg, "CA": sec, "NPAE": t,
            "AN": math.sin(a) * t, "AW": -math.cos(a) * t}
    d_alt = {"IE": 1.0, "SE": m.alt_deg, "AN": math.cos(a), "AW": math.sin(a),
             "TF": math.cos(e)}
    return d_az, d_alt


@dataclass
class FitReport:
    """Result of a fit: terms used, RMS and per-observation residuals (arcsec)."""
    terms: tuple
    rms_arcsec: float
    residuals_arcsec: list = field(default_factory=list)   # (d_alt, d_az·cos alt) per obs
    dropped: tuple = ()


@dataclass
class PointingModel:
    """Fitted term values (degrees; SA/SE dimensionless). Unfitted terms are 0."""
    params: dict = field(default_factory=lambda: {k: 0.0 for k in TERM_ORDER})

    def corrections(self, m: MountAngles) -> tuple[float, float]:
        """(d_az, d_alt) in degrees at mount angles m."""
        paz, palt = _partials(m)
        d_az = sum(self.params.get(k, 0.0) * v for k, v in paz.items())
        d_alt = sum(self.params.get(k, 0.0) * v for k, v in palt.items())
        return d_az, d_alt

    def mount_to_sky(self, m: MountAngles) -> AltAz:
        """True apparent direction for mount angles m."""
        d_az, d_alt = self.corrections(m)
        return AltAz(m.alt_deg + d_alt, (m.az_deg + d_az) % 360.0)

    def sky_to_mount(self, s: AltAz, near_az_deg: float = 0.0) -> MountAngles:
        """Mount angles that point at true direction s.

        `near_az_deg` picks which turn of azimuth to use (the controller's az is
        continuous). The result is the solution closest to it, which keeps moves short
        and avoids unwinding the cables.
        """
        m = MountAngles(near_az_deg + _wrap180(s.az_deg - near_az_deg), s.alt_deg)
        for _ in range(8):
            d_az, d_alt = self.corrections(m)
            target_az = near_az_deg + _wrap180(s.az_deg - d_az - near_az_deg)
            m = MountAngles(target_az, s.alt_deg - d_alt)
        return m

    def fit(self, obs: list[Observation], terms: tuple | None = None,
            keep: tuple = ()) -> FitReport:
        """Least-squares fit of `terms`; updates `params` in place.

        Args:
            obs: observations.
            terms: terms to fit. Default: as many as `obs` supports (see the module
                notes), in TERM_ORDER, excluding `keep`.
            keep: terms held at their current values (their effect is subtracted
                first). Terms neither fitted nor kept are set to 0.

        Raises:
            ValueError: no observations.
        """
        if not obs:
            raise ValueError("no observations")
        n = len(obs)
        kept = {k: self.params.get(k, 0.0) for k in keep}
        if terms is None:
            k = 2 * n if n <= 2 else 2 * n - 2
            terms = [t for t in TERM_ORDER if t not in kept][:k]
        terms = [t for t in terms if t not in kept]
        rows, rhs, w = [], [], []
        for o in obs:
            paz, palt = _partials(o.mount)
            cos_alt = math.cos(math.radians(o.sky.alt_deg))
            kept_alt = sum(v * palt.get(k, 0.0) for k, v in kept.items())
            kept_az = sum(v * paz.get(k, 0.0) for k, v in kept.items())
            rows.append([palt.get(t, 0.0) for t in terms])
            rhs.append(o.sky.alt_deg - o.mount.alt_deg - kept_alt)
            rows.append([paz.get(t, 0.0) * cos_alt for t in terms])
            rhs.append((_wrap180(o.sky.az_deg - o.mount.az_deg) - kept_az) * cos_alt)
            w += [1.0 / o.sigma_deg] * 2
        A, b, W = np.array(rows), np.array(rhs), np.array(w)
        dropped = []
        while terms:
            sol, _, rank, _ = np.linalg.lstsq(A * W[:, None], b * W, rcond=None)
            if rank == len(terms):
                break
            dropped.append(terms.pop())                   # least important first
            A = A[:, :len(terms)]
        self.params = {k: 0.0 for k in TERM_ORDER}
        self.params.update(kept)
        for t, v in zip(terms, sol if terms else []):
            self.params[t] = float(v)
        resid = b - A @ np.array([self.params[t] for t in terms]) if terms else b
        res = [(resid[2 * i] * 3600, resid[2 * i + 1] * 3600) for i in range(n)]
        rms = float(np.sqrt(np.mean(resid ** 2))) * 3600
        return FitReport(tuple(terms), rms, res, tuple(dropped))

    def copy(self) -> "PointingModel":
        return replace(self, params=dict(self.params))

    def save(self, path) -> None:
        """Write params as JSON (e.g. data/pointing_model.json)."""
        import json
        from pathlib import Path

        Path(path).write_text(json.dumps(self.params, indent=2))

    @classmethod
    def load(cls, path) -> "PointingModel":
        """Read params written by `save`; missing terms are 0."""
        import json
        from pathlib import Path

        params = {k: 0.0 for k in TERM_ORDER}
        params.update({k: float(v) for k, v in json.loads(Path(path).read_text()).items()
                       if k in TERM_ORDER})
        return cls(params)
