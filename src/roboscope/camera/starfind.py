"""Find the single brightest star in a frame and measure its centroid (numpy only).

Details:
    Runs on the camera Pi (inside the camera server, so only coordinates cross the
    network) and on the host (tests, saved frames). It needs only numpy, and the package
    uses relative imports only, so the same file works as `roboscope.camera.starfind`
    on the host and `roboscope_camera.starfind` on the Pi.

    Frames are 2-D arrays on the *analysis grid*. On the Pi that's the raw Bayer frame
    with each 2×2 cell summed (`bin_bayer`), so values are linear in light (no ISP: no
    gamma, lens shading or colour gains) and there's no demosaicing. Frames are uint16
    from the cameras; float arrays also work.

    Speed matters: numpy on the Pi Zero's ARMv6 core is slow (benchmarked 2026-11-25).
    Converting a whole 1296×972 frame to float32 costs ~250 ms. So full-frame work stays
    in the native integer dtype, and only subsamples and the small centroid window
    become float. On the Zero, per binned frame: bin_bayer ~70 ms, find_star ~170 ms.

    `find_star` is built for a narrow field with one bright, isolated star (what the
    alignment session's star selection guarantees):
      1. Background and noise come from a 1-in-16 subsample: median and MAD × 1.4826.
         Accurate while the star covers a small part of the frame.
      2. A 2×2 minimum filter suppresses isolated hot pixels, then block sums find the
         brightest region coarsely.
      3. In a window around it, pixels more than k·σ above background (excluding
         isolated single pixels) give an intensity-weighted centroid. The window is
         re-centred on the centroid once, so a star near a block edge isn't clipped.
      4. The detection needs ≥ `min_pixels` pixels and SNR ≥ `min_snr`, where
         SNR = flux / (σ·√npix), a background-limited estimate.
    Coordinates are pixel centres: (0, 0) is the centre of the top-left pixel, x right,
    y down. Converting analysis-grid to full-sensor coordinates is the caller's job
    (`to_sensor_px`).

    Known limits: not deblended (a close double gives the combined centroid), and a
    star brighter than the subsample background estimate can handle (filling >~25% of
    the frame) biases the background.
"""

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class StarFix:
    """A detected star, in the coordinates of the array it was found in."""
    x: float
    y: float
    flux: float          # background-subtracted sum over detected pixels
    peak: float          # highest raw pixel value in the detection window
    npix: int            # pixels above threshold
    snr: float
    background: float
    noise: float         # 1-σ per pixel
    saturated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def bin_bayer(raw: np.ndarray) -> np.ndarray:
    """Sum each 2×2 Bayer cell into one pixel (half the size in each axis), same dtype.

    Each cell holds one R, two G and one B pixel, so the sum is a linear, colour-blind
    brightness. Odd trailing rows or columns are dropped. Integer input must leave
    headroom for ×4: 10-bit raw in uint16 is fine (max 4092); data wider than 14 bits
    would overflow. Rows are added first, then columns, which is ~4× faster on the Pi
    than summing four strided views.
    """
    h, w = (raw.shape[0] // 2) * 2, (raw.shape[1] // 2) * 2
    rows = raw[0:h:2, :w] + raw[1:h:2, :w]
    return rows[:, 0::2] + rows[:, 1::2]


def frame_stats(img: np.ndarray, step: int = 4) -> tuple[float, float]:
    """(background, noise) from a subsample: median, and MAD scaled to 1σ.

    Noise has a floor of 0.5 so quantised, nearly noiseless frames still give a usable
    threshold.
    """
    sub = img[::step, ::step].astype(np.float32)
    bg = float(np.median(sub))
    noise = 1.4826 * float(np.median(np.abs(sub - bg)))
    return bg, max(noise, 0.5)


def find_star(img: np.ndarray, k_sigma: float = 5.0, block: int = 8, window: int = 12,
              min_pixels: int = 3, min_snr: float = 7.0,
              saturation: float | None = None) -> StarFix | None:
    """Centroid of the brightest star in `img`, or None if nothing passes the cuts.

    Args:
        img: 2-D array (analysis grid).
        k_sigma: per-pixel detection threshold above background, in σ.
        block: block size (px) for the coarse search.
        window: half-size (px) of the centroiding window.
        min_pixels: minimum connected-ish pixels above threshold (rejects hot pixels).
        min_snr: minimum flux/(σ·√npix).
        saturation: pixel value at which a pixel is clipped; sets `saturated`.
    """
    img = np.asarray(img)
    h, w = img.shape
    bg, noise = frame_stats(img)

    # Coarse, in the native dtype: a 2×2 minimum filter removes single-pixel spikes,
    # then block sums (uint32 for integer frames; 64 px × 65535 fits).
    m = np.minimum(np.minimum(img[:-1, :-1], img[1:, :-1]),
                   np.minimum(img[:-1, 1:], img[1:, 1:]))
    hb, wb = (m.shape[0] // block) * block, (m.shape[1] // block) * block
    if hb == 0 or wb == 0:
        return None
    acc = np.float64 if img.dtype.kind == "f" else np.uint32
    sums = m[:hb, :wb].reshape(hb // block, block, wb // block, block).sum(axis=(1, 3),
                                                                           dtype=acc)
    by, bx = np.unravel_index(int(np.argmax(sums)), sums.shape)
    cy, cx = by * block + block / 2, bx * block + block / 2

    fix = None
    for _ in range(2):                                    # re-centre once
        y0, y1 = max(0, int(cy) - window), min(h, int(cy) + window + 1)
        x0, x1 = max(0, int(cx) - window), min(w, int(cx) + window + 1)
        win = img[y0:y1, x0:x1].astype(np.float32) - bg
        mask = win > k_sigma * noise
        # Drop isolated pixels: a real star has a neighbour above threshold.
        nb = np.zeros_like(mask)
        nb[1:, :] |= mask[:-1, :]
        nb[:-1, :] |= mask[1:, :]
        nb[:, 1:] |= mask[:, :-1]
        nb[:, :-1] |= mask[:, 1:]
        mask &= nb
        npix = int(mask.sum())
        if npix < min_pixels:
            return None
        wts = np.where(mask, win, 0.0)
        flux = float(wts.sum())
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx, cy = float((wts * xx).sum() / flux), float((wts * yy).sum() / flux)
        peak = float(img[y0:y1, x0:x1].max())
        snr = flux / (noise * np.sqrt(npix))
        fix = StarFix(cx, cy, flux, peak, npix, float(snr), bg, noise,
                      saturation is not None and peak >= saturation)
    if fix is None or fix.snr < min_snr:
        return None
    return fix


def to_sensor_px(x: float, y: float, scale: float) -> tuple[float, float]:
    """Analysis-grid pixel-centre coordinates → full-sensor pixel-centre coordinates.

    An analysis pixel covering `scale` sensor pixels per axis has its centre at sensor
    coordinate (x + 0.5)·scale − 0.5.
    """
    return (x + 0.5) * scale - 0.5, (y + 0.5) * scale - 0.5
