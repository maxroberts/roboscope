"""Spiral search pattern for finding a target that isn't in the camera's field.

Details:
    `spiral_offsets` yields sky offsets (u = Δaz·cos alt, v = Δalt, degrees) on a square
    spiral: (0, 0) first, then ring after ring outward, every point within `max_radius`.
    `step_deg` should be ≤ the camera's smaller field dimension. 80% of it leaves
    overlap between frames.

    Cost: the number of frames grows as (radius / step)². For the narrow in-tube camera
    (~0.24° short side → step ~0.19°) a 2° search radius is ~350 frames, a 5° radius
    ~2,200. So the narrow approach needs a good first guess: a careful north/level
    setup, the magnetometer and accelerometer, or a first target bigger than the field,
    like the Moon. Used by align.session.
"""

import math
from collections.abc import Iterator


def spiral_offsets(step_deg: float, max_radius_deg: float) -> Iterator[tuple[float, float]]:
    """Yield (u, v) offsets in degrees on an outward square spiral (see module notes)."""
    if step_deg <= 0:
        raise ValueError("step must be positive")
    yield 0.0, 0.0
    ring = 1
    while ring * step_deg <= max_radius_deg * math.sqrt(2):   # corners reach the circle
        # walk the square ring at Chebyshev distance `ring`
        pts = []
        for i in range(-ring, ring + 1):
            pts += [(i, -ring), (ring, i), (-i, ring), (-ring, -i)]
        seen = set()
        for i, j in pts:
            if (i, j) in seen:
                continue
            seen.add((i, j))
            u, v = i * step_deg, j * step_deg
            if math.hypot(u, v) <= max_radius_deg:
                yield u, v
        ring += 1
