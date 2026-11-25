"""Alignment: relate mount encoder angles to true sky directions, for any camera.

Details:
    - `model`: `PointingModel`, standard alt-az error terms (index offsets, base tilt,
      collimation, axis non-perpendicularity, scale, flex), fitted by weighted least
      squares from `Observation`s (mount angles ↔ true direction ± σ). Maps both ways.
      Hardware terms can be kept between sessions (save/load + fit(keep=...)), so a
      quick 3-star setup only solves the setup terms.
    - `camera_frame`: `CameraFrame`, pixel ↔ small sky offsets for a tube-fixed camera
      (scale, roll, mirror flip, boresight pixel).
    - `search`: `spiral_offsets`, an outward square spiral for when a target isn't in
      the field.
    - `session`: `AlignmentSession`, the star-alignment loop: pick isolated stars, go,
      search, observe, refit. Mount and star finder are duck-typed, so the hardware
      (MotorController + camera) and the simulation in tests/test_align.py plug in
      the same way.

    Observation sources are interchangeable: a narrow camera's star centroid (via
    CameraFrame), a wide camera's plate solve (a direction directly), or the tube's
    accelerometer and magnetometer (coarse, large σ). They all become `Observation`s.

    Not yet here: backlash handling (motion layer), automatic camera-frame calibration
    (scale/roll from two exposures with a known move between them), and the hardware
    adapters.
"""

from roboscope.align.camera_frame import CameraFrame
from roboscope.align.model import (
    HARDWARE_TERMS, TERM_ORDER, FitReport, MountAngles, Observation, PointingModel,
)
from roboscope.align.search import spiral_offsets
from roboscope.align.session import AlignmentResult, AlignmentSession, StarResult

__all__ = [
    "AlignmentResult", "AlignmentSession", "CameraFrame", "FitReport", "HARDWARE_TERMS",
    "MountAngles",
    "Observation", "PointingModel", "StarResult", "TERM_ORDER", "spiral_offsets",
]
