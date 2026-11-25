"""Star alignment: go to stars, search if needed, record observations, refit the model.

Details:
    `AlignmentSession` runs the narrow-camera alignment flow, which works equally with a
    wide camera's star finder:
      1. Pick isolated, bright, well-spread stars (`StarCatalog.alignment_candidates`)
         and visit them nearest-first from the current pointing. Short hops keep the
         errors the model doesn't know yet small: scale errors grow with slew length.
      2. For each star:
         - predict its true alt/az now;
         - convert to mount angles with the current model;
         - move there and look.
         If the star isn't found, step through `spiral_offsets` around the prediction
         until it is, or the search radius is exhausted.
      3. Record an `Observation` (mount angles ↔ boresight true direction, computed
         from the star's pixel position with `CameraFrame`), then refit the model. Each
         star makes the next prediction better.

    Interfaces (duck-typed, so hardware and simulation plug in the same way):
      - `mount.goto(MountAngles)` moves and blocks until settled;
        `mount.position() -> MountAngles`. The motion layer is responsible for safety
        limits and backlash handling.
      - `finder.locate() -> (px, py) | None`: pixel of the single bright star in the
        current frame, or None. For a narrow camera that's "brightest blob above a
        threshold"; isolation is what makes that unambiguous.
      - `clock() -> datetime` (timezone-aware): the time used for star positions.

    Sun safety: each target and each search step is checked with `sky.is_safe` before
    any move; unsafe steps are skipped.

    Seeding the model: pass a `PointingModel` with IA/IE preset from the user's setup
    (pointed north and level → 0, 0) or from the magnetometer and accelerometer. The
    search radius must cover the seed's error.
"""

import logging
from dataclasses import dataclass, field

from roboscope.align.camera_frame import CameraFrame
from roboscope.align.model import FitReport, Observation, PointingModel
from roboscope.align.search import spiral_offsets
from roboscope.sky import AltAz, Sky, StarCatalog

log = logging.getLogger(__name__)


@dataclass
class StarResult:
    """Outcome for one alignment star."""
    label: str
    found: bool
    frames: int                          # camera frames taken for this star
    observation: Observation | None = None


@dataclass
class AlignmentResult:
    """All per-star outcomes plus the final fit (None if no star was found)."""
    stars: list[StarResult] = field(default_factory=list)
    fit: FitReport | None = None


class AlignmentSession:
    """Runs star alignment (see module notes).

    Args:
        sky, catalog: the sky model.
        mount: object with goto(MountAngles) and position().
        finder: object with locate() -> (px, py) | None.
        frame: the camera's CameraFrame.
        model: starting PointingModel (seeded or zeros); refined in place.
        keep: model terms held fixed during refits (e.g. HARDWARE_TERMS loaded from a
            previous session); see PointingModel.fit.
        clock: returns the current timezone-aware datetime.
        search_radius_deg: how far to spiral before giving up on a star.
        step_fraction: spiral step as a fraction of the smaller field dimension.
        star_sigma_deg: uncertainty assigned to star observations.
    """

    def __init__(self, sky: Sky, catalog: StarCatalog, mount, finder, frame: CameraFrame,
                 model: PointingModel, clock, search_radius_deg: float = 2.0,
                 step_fraction: float = 0.8, star_sigma_deg: float = 0.002,
                 keep: tuple = ()):
        self.sky, self.catalog, self.mount, self.finder = sky, catalog, mount, finder
        self.frame, self.model, self.clock = frame, model, clock
        self.search_radius_deg = search_radius_deg
        self.step_deg = step_fraction * min(frame.fov_deg)
        self.star_sigma_deg = star_sigma_deg
        self.keep = tuple(keep)
        self.observations: list[Observation] = []

    def observe(self, star) -> StarResult:
        """Find one catalog star (spiral search if needed) and record an observation."""
        frames = 0
        for u, v in spiral_offsets(self.step_deg, self.search_radius_deg):
            now = self.clock()
            pred = self.catalog.altaz(star, self.sky, now)
            target = _offset(pred, u, v)
            if target.alt_deg < 5 or not self.sky.is_safe(target, now):
                continue
            here_az = self.mount.position().az_deg
            self.mount.goto(self.model.sky_to_mount(target, near_az_deg=here_az))
            frames += 1
            hit = self.finder.locate()
            if hit is None:
                continue
            now = self.clock()                               # time of the frame
            star_now = self.catalog.altaz(star, self.sky, now)
            obs = Observation(now, self.mount.position(),
                              self.frame.boresight(star_now, *hit),
                              self.star_sigma_deg, f"star {star.label}")
            self.observations.append(obs)
            log.info("found %s after %d frames", star.label, frames)
            return StarResult(star.label, True, frames, obs)
        log.warning("did not find %s within %.1f°", star.label, self.search_radius_deg)
        return StarResult(star.label, False, frames)

    def run(self, n_stars: int = 3, **candidate_kwargs) -> AlignmentResult:
        """Observe up to `n_stars` alignment stars, refitting after each one found.

        `candidate_kwargs` go to `StarCatalog.alignment_candidates`. Set
        isolation_deg to at least the search radius plus half the field.
        """
        kw = {"isolation_deg": self.search_radius_deg + max(self.frame.fov_deg) / 2}
        kw.update(candidate_kwargs)
        result = AlignmentResult()
        picks = self.catalog.alignment_candidates(self.sky, self.clock(), n=n_stars, **kw)
        here = self.model.mount_to_sky(self.mount.position())
        for cand in _nearest_first(picks, here):
            r = self.observe(cand.star)
            result.stars.append(r)
            if r.found:
                result.fit = self.model.fit(self.observations, keep=self.keep)
                log.info("fit after %d stars: %s rms %.1f\"", len(self.observations),
                         result.fit.terms, result.fit.rms_arcsec)
        return result


def _nearest_first(picks: list, start: AltAz) -> list:
    """Order alignment picks as a greedy nearest-neighbour path from `start`.

    Short hops limit how far errors the model doesn't know yet can grow before the next
    refit. A 1% counts/° error, for example, is 1.4° after a 140° slew but 0.4° after
    40°.
    """
    from roboscope.sky import separation_deg

    rest, order, here = list(picks), [], start
    while rest:
        nxt = min(rest, key=lambda p: separation_deg(here, p.altaz))
        order.append(nxt)
        rest.remove(nxt)
        here = nxt.altaz
    return order


def _offset(p: AltAz, u: float, v: float) -> AltAz:
    """Apply a small tangent-plane offset (u = Δaz·cos alt, v = Δalt; degrees)."""
    import math

    alt = p.alt_deg + v
    return AltAz(alt, (p.az_deg + u / max(1e-6, math.cos(math.radians(alt)))) % 360.0)
