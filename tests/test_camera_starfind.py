"""Tests for roboscope.camera.starfind on synthetic frames (no camera needed).

Details:
    Frames are Gaussian stars on a flat background with Gaussian read noise, plus hot
    pixels where a test needs them. Accuracy targets are loose multiples of the
    theoretical centroid error (~FWHM/SNR), so they test for bias, not luck.
"""

import numpy as np
import pytest

from roboscope.camera.starfind import bin_bayer, find_star, frame_stats, to_sensor_px


def star_frame(x, y, flux=20000.0, fwhm=3.0, shape=(486, 648), bg=200.0, noise=5.0,
               seed=0):
    """Synthetic analysis-grid frame with one Gaussian star at (x, y)."""
    rng = np.random.default_rng(seed)
    sig = fwhm / 2.3548
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    star = flux / (2 * np.pi * sig**2) * np.exp(-((xx - x)**2 + (yy - y)**2) / (2 * sig**2))
    return (bg + star + rng.normal(0, noise, shape)).astype(np.float32)


@pytest.mark.parametrize("x,y", [(320.0, 240.0), (100.3, 400.7), (7.4, 9.6), (640.2, 480.1)])
def test_centroid_accuracy(x, y):
    fix = find_star(star_frame(x, y))
    assert fix is not None
    assert abs(fix.x - x) < 0.05 and abs(fix.y - y) < 0.05
    assert fix.background == pytest.approx(200, abs=1)
    assert fix.noise == pytest.approx(5, rel=0.1)


def test_block_edge_star_not_clipped():
    # Star straddling a block boundary; the re-centring pass must recover the flux.
    fix = find_star(star_frame(16.0, 16.0, fwhm=6.0), block=8, window=12)
    assert abs(fix.x - 16) < 0.1 and abs(fix.y - 16) < 0.1
    assert fix.flux > 0.9 * 20000


def test_hot_pixels_ignored():
    img = star_frame(300.0, 200.0, flux=3000.0)
    for hy, hx in [(10, 10), (50, 600), (400, 20)]:
        img[hy, hx] = 4000.0                              # brighter than the star's peak
    fix = find_star(img)
    assert fix is not None and abs(fix.x - 300) < 0.2 and abs(fix.y - 200) < 0.2


def test_only_hot_pixel_gives_none():
    img = star_frame(0, 0, flux=0.0)
    img[100, 100] = 4000.0
    assert find_star(img) is None


def test_empty_frame_gives_none():
    assert find_star(star_frame(0, 0, flux=0.0)) is None


def test_faint_star_snr_cut():
    faint = star_frame(300.0, 200.0, flux=150.0)          # ~SNR 5 total
    assert find_star(faint, min_snr=20) is None
    assert find_star(star_frame(300.0, 200.0, flux=2000.0), min_snr=20) is not None


def test_saturation_flag():
    img = np.minimum(star_frame(300.0, 200.0, flux=2e5), 4092.0)
    assert find_star(img, saturation=4092.0).saturated
    assert not find_star(star_frame(300.0, 200.0), saturation=4092.0).saturated


def test_bin_bayer_sums_cells():
    raw = np.arange(16, dtype=np.uint16).reshape(4, 4)
    out = bin_bayer(raw)
    assert out.shape == (2, 2)
    assert out[0, 0] == 0 + 1 + 4 + 5 and out[1, 1] == 10 + 11 + 14 + 15
    assert bin_bayer(np.ones((5, 7), np.uint16)).shape == (2, 3)


def test_frame_stats_noise_floor():
    bg, noise = frame_stats(np.full((40, 40), 100.0))
    assert bg == 100 and noise == 0.5


def test_to_sensor_px():
    assert to_sensor_px(0, 0, 2) == (0.5, 0.5)            # covers sensor px 0 and 1
    assert to_sensor_px(10, 20, 4) == (41.5, 81.5)


def test_frame_bins_raw_lazily():
    from roboscope.camera.sources import Frame

    raw = np.arange(16, dtype=np.uint16).reshape(4, 4)
    f = Frame(None, 4, 1, 0, 1000.0, 1.0, 4092.0, raw=raw)
    assert f.image_ is None                       # nothing binned at capture
    assert f.image[0, 0] == 0 + 1 + 4 + 5 and f.raw is None
