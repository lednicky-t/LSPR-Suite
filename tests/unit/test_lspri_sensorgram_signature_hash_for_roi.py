"""Correctness proof for AnalysisWorkerMixin._sensorgram_point_signature_
hash_cube_context / _sensorgram_point_signature_hash_for_roi: this pairing
replaced calling the original, much more expensive _sensorgram_point_
signature_hash once per ROI (per_roi_sensorgram measured ~630-780ms/cube at
160 ROIs - see docs/bulk_analysis_performance_investigation.md's matching
follow-up) with computing the ROI-independent part once per cube and
reusing it cheaply per ROI. That's only a safe optimization if it produces
the exact same hash the original per-ROI call would have - a mismatch here
would mean already-backed-up rows stop deduping correctly (silent full
re-write) or, worse, two different states hashing to the same key. This
test builds a single, moderately complete fake window/mixin and checks both
code paths agree, across several cubes and ROIs, using the REAL signature_
hash (JSON+SHA256) function - not a stub.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi  # noqa: E402
from lspr_imaging_app.gui.analysis_cache_signature import signature_hash  # noqa: E402
from lspr_imaging_app.gui.analysis_worker_mixin import AnalysisWorkerMixin  # noqa: E402


def _make_mixin():
    mixin = AnalysisWorkerMixin.__new__(AnalysisWorkerMixin)
    rois = [
        AreaRoi(area_roi_id=1, center_x=12.5, center_y=30.0, sample_radius_px=5.0),
        AreaRoi(area_roi_id=2, center_x=48.25, center_y=30.0, sample_radius_px=5.0),
        AreaRoi(area_roi_id=17, center_x=100.0, center_y=64.5, sample_radius_px=7.5),
    ]
    window = SimpleNamespace(
        _state=SimpleNamespace(
            dataset=SimpleNamespace(folder="C:/fake/dataset/folder"),
            area_roi_settings=SimpleNamespace(reference_inner_radius_px=8.0, reference_outer_radius_px=20.0),
            image_exclusions=[],
        ),
        _wavelength_values=[500.0, 520.0, 540.0, 560.0, 580.0],
        # Deterministic stand-in - not the real image-preprocessing logic,
        # just needs to vary by image_key so the test can tell it's being
        # threaded through correctly.
        _preprocessing_signature=lambda image_key: ("preproc", round(image_key[0] * 1000 + image_key[1], 3)),
        _roi_signature=lambda rois: tuple(
            (int(roi.area_roi_id), round(float(roi.center_x), 3), round(float(roi.center_y), 3), round(float(roi.sample_radius_px), 3))
            for roi in rois
        ),
        _analysis_metric_key=lambda: "centroid",
        _analysis_poly_order=lambda: 3,
        _analysis_wavelength_range=lambda: (510.0, 590.0),
    )
    mixin.window = window
    mixin._active_formula_key = lambda: "absorbance"
    mixin._analysis_fit_method_key = lambda: "poly"
    mixin._roi_reduction_signature_elements = lambda: ("mean",)
    mixin._exclusion_signature_for_cube = lambda spectral_cube_index: tuple(
        (spectral_cube_index + i) % 3 == 0 for i in range(len(window._wavelength_values))
    )
    mixin._signature_hash = staticmethod(signature_hash)
    return mixin, rois


class TestSensorgramPointSignatureHashForRoi(unittest.TestCase):
    def test_matches_the_original_per_roi_call_across_cubes_and_rois(self) -> None:
        mixin, rois = _make_mixin()
        for cube_index in (0, 1, 7, 42):
            cube_context = mixin._sensorgram_point_signature_hash_cube_context(cube_index)
            self.assertIsNotNone(cube_context)
            for roi in rois:
                fast = mixin._sensorgram_point_signature_hash_for_roi(cube_context, roi)
                slow = mixin._sensorgram_point_signature_hash(cube_index, (int(roi.area_roi_id),), [roi])
                self.assertEqual(fast, slow, msg=f"cube={cube_index} roi={roi.area_roi_id}")
                self.assertTrue(fast, msg="must be a real, non-empty hash, not the empty-signature fallback")

    def test_different_rois_in_the_same_cube_get_different_hashes(self) -> None:
        mixin, rois = _make_mixin()
        cube_context = mixin._sensorgram_point_signature_hash_cube_context(3)
        hashes = {mixin._sensorgram_point_signature_hash_for_roi(cube_context, roi) for roi in rois}
        self.assertEqual(len(hashes), len(rois))

    def test_same_roi_in_different_cubes_gets_different_hashes(self) -> None:
        mixin, rois = _make_mixin()
        roi = rois[0]
        hashes = {
            mixin._sensorgram_point_signature_hash_for_roi(mixin._sensorgram_point_signature_hash_cube_context(cube_index), roi)
            for cube_index in (0, 1, 2, 3)
        }
        self.assertEqual(len(hashes), 4)

    def test_no_dataset_returns_none(self) -> None:
        mixin, _rois = _make_mixin()
        mixin.window._state.dataset = None
        self.assertIsNone(mixin._sensorgram_point_signature_hash_cube_context(5))


if __name__ == "__main__":
    unittest.main()
