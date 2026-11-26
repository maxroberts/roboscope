"""Camera image geometry: pixel offsets ↔ small sky offsets, for a camera fixed to the tube.

Details:
    On an alt-az mount a tube-mounted camera turns with the tube, so its image axes stay
    fixed relative to the local altitude/azimuth directions; there's no field rotation
    in this frame. A star's pixel offset (dx right, dy down) from the boresight pixel
    maps to a sky offset (east-of-boresight along the horizon × cos alt, up in
    altitude) by:
        [u, v] = scale · R(roll) · [dx·(−1 if flip_x else 1), −dy]
    with u = Δaz·cos(alt), v = Δalt in degrees, and scale in arcsec/px converted to °.

    Parameters, measured during camera calibration:
      - `scale_arcsec_px`: from optics. The NG 102 at prime focus with the user's v1
        Pi camera (OV5647, 1.4 µm pixels) gives ≈ 0.44″/px. An HQ camera with a 16 mm
        lens gives ≈ 20″/px. The default width/height (3280×2464, a v2 camera) are
        placeholders; real values come from the calibration file (2592×1944).
      - `roll_deg`: camera rotation about the optical axis.
      - `flip_x`: a mirror image (a star diagonal flips it).
      - `boresight_px`: the pixel the model treats as "where the mount points", usually
        the image centre for the main camera. For a separate solver camera it's a fixed
        offset measured once.
    Used by align.session for narrow-camera observations. The wide camera can bypass it
    by reporting plate-solved directions directly.
"""

import math
from dataclasses import dataclass

from roboscope.sky import AltAz


@dataclass(frozen=True)
class CameraFrame:
    """Small-angle pixel ↔ sky mapping for a tube-fixed camera (see module notes)."""
    scale_arcsec_px: float
    roll_deg: float = 0.0
    flip_x: bool = False
    width_px: int = 3280
    height_px: int = 2464
    boresight_px: tuple[float, float] | None = None     # default: image centre

    @property
    def center(self) -> tuple[float, float]:
        return self.boresight_px or (self.width_px / 2, self.height_px / 2)

    @property
    def fov_deg(self) -> tuple[float, float]:
        """Full field of view (width, height) in degrees."""
        s = self.scale_arcsec_px / 3600
        return self.width_px * s, self.height_px * s

    def _to_sky(self, dx: float, dy: float) -> tuple[float, float]:
        s = self.scale_arcsec_px / 3600
        x = -dx if self.flip_x else dx
        y = -dy
        r = math.radians(self.roll_deg)
        u = s * (x * math.cos(r) - y * math.sin(r))
        v = s * (x * math.sin(r) + y * math.cos(r))
        return u, v

    def _to_px(self, u: float, v: float) -> tuple[float, float]:
        s = self.scale_arcsec_px / 3600
        r = math.radians(self.roll_deg)
        x = (u * math.cos(r) + v * math.sin(r)) / s
        y = (-u * math.sin(r) + v * math.cos(r)) / s
        return (-x if self.flip_x else x), -y

    def boresight(self, star: AltAz, px: float, py: float) -> AltAz:
        """True direction of the boresight, given a star (true alt/az) seen at pixel (px, py)."""
        cx, cy = self.center
        u, v = self._to_sky(px - cx, py - cy)
        alt = star.alt_deg - v
        cos_alt = max(1e-6, math.cos(math.radians(alt)))
        return AltAz(alt, (star.az_deg - u / cos_alt) % 360.0)

    def pixel_of(self, star: AltAz, boresight: AltAz) -> tuple[float, float]:
        """Pixel where `star` appears when the boresight points at `boresight`."""
        d_az = (star.az_deg - boresight.az_deg + 180.0) % 360.0 - 180.0
        u = d_az * math.cos(math.radians(boresight.alt_deg))
        v = star.alt_deg - boresight.alt_deg
        dx, dy = self._to_px(u, v)
        cx, cy = self.center
        return cx + dx, cy + dy

    def contains(self, px: float, py: float, margin_px: float = 0.0) -> bool:
        """True if (px, py) falls on the sensor (at least `margin_px` from the edges)."""
        return (margin_px <= px <= self.width_px - margin_px
                and margin_px <= py <= self.height_px - margin_px)
