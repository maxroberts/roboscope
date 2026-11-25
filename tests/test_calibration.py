"""Tests for roboscope.calibration: load/save, accessors, scale absorption, camera frames.

Details:
    Uses the committed config/hardware_calibration.json (read-only) and temporary copies
    for updates.
"""

import json
import shutil

import pytest

from roboscope.align import PointingModel
from roboscope.calibration import DEFAULT_PATH, HardwareCalibration


def test_committed_file_loads_and_has_measured_cpd():
    cal = HardwareCalibration.load(DEFAULT_PATH)
    cpd = cal.counts_per_degree()
    assert cpd == {"az": 3085.0, "alt": 4230.0}
    assert cal.get("mount", "counts_per_degree", "az").sigma == 75.0
    assert not cal.get("mount", "backlash_counts", "az").known      # explicit gap


def test_absorb_scale_folds_sa_into_counts(tmp_path):
    path = tmp_path / "cal.json"
    shutil.copy(DEFAULT_PATH, path)
    cal = HardwareCalibration.load(path)
    model = PointingModel()
    model.params.update(SA=0.02, SE=-0.01, IA=1.0)
    new = cal.absorb_scale(model, "test fit")
    # true = counts/cpd_old * (1 + SA)  ==  counts/cpd_new
    assert new["az"] == pytest.approx(3085.0 / 1.02)
    assert new["alt"] == pytest.approx(4230.0 / 0.99)
    assert model.params["SA"] == 0 and model.params["SE"] == 0 and model.params["IA"] == 1.0
    cal.save()
    again = HardwareCalibration.load(path)
    m = again.get("mount", "counts_per_degree", "az")
    assert m.value == pytest.approx(3085.0 / 1.02, abs=0.01) and "previous 3085.0" in m.note


def test_pointing_model_uses_known_hardware_terms(tmp_path):
    path = tmp_path / "cal.json"
    shutil.copy(DEFAULT_PATH, path)
    cal = HardwareCalibration.load(path)
    cal.set(("pointing", "CA"), 0.05, 0.01, "test")
    model = cal.pointing_model()
    assert model.params["CA"] == 0.05 and model.params["IA"] == 0.0


def test_camera_frame_requires_scale(tmp_path):
    path = tmp_path / "cal.json"
    shutil.copy(DEFAULT_PATH, path)
    cal = HardwareCalibration.load(path)
    with pytest.raises(ValueError, match="not calibrated"):
        cal.camera_frame("narrow")
    cal.set(("cameras", "narrow", "scale_arcsec_px"), 0.35, 0.005, "test")
    cal.set(("cameras", "narrow", "roll_deg"), 12.0, 0.5, "test")
    f = cal.camera_frame("narrow")
    assert f.scale_arcsec_px == 0.35 and f.roll_deg == 12.0 and f.boresight_px is None


def test_schema_checked(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": 99}))
    with pytest.raises(ValueError, match="schema"):
        HardwareCalibration.load(bad)
