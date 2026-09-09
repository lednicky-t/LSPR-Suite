"""Correctness proof for AnalysisWorkerMixin._sensorgram_point_signature_
hash_cube_context / _sensorgram_point_signature_hash_for_roi: this pairing
replaced calling the original, much more expensive _sensorgram_point_
signature_hash once per ROI with computing a single cube-level hash once
per cube and cheaply combining it with each ROI's own small piece per ROI -
avoiding both recomputing the expensive per-wavelength preprocessing scan
per ROI (~630-780ms/cube at 160 ROIs) and re-serializing/re-hashing that
same scan via JSON+SHA256 per ROI (~52ms/cube) - see docs/bulk_analysis_
performance_investigation.md's matching follow-ups for both measurements.

Unlike the first version of this fix, the resulting hash is deliberately
NOT byte-identical to the original per-ROI _sensorgram_point_signature_hash
call (hashing a pre-hashed summary is a different input, even though it's
just as valid a fingerprint) - what actually matters, and what this file
checks, is that the result is a stable, deterministic function of (cube,
ROI, and every setting that should invalidate it), distinguishing every
case that should be distinguished.
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


def _make_mixin(**overrides):
    mixin = AnalysisWorkerMixin.__new__(AnalysisWorkerMixin)
    defaults = dict(
        dataset_folder="C:/fake/dataset/folder",
        wavelength_values=[500.0, 520.0, 540.0, 560.0, 580.0],
        reference_inner_radius_px=8.0,
        reference_outer_radius_px=20.0,
        reduction_method="mean",
        formula_key="absorbance",
        fit_method_key="poly",
        metric_key="centroid",
        poly_order=3,
        wavelength_range=(510.0, 590.0),
    )
    defaults.update(overrides)
    window = SimpleNamespace(
        _state=SimpleNamespace(
            dataset=SimpleNamespace(folder=defaults["dataset_folder"]),
            area_roi_settings=SimpleNamespace(
                reference_inner_radius_px=defaults["reference_inner_radius_px"],
                reference_outer_radius_px=defaults["reference_outer_radius_px"],
            ),
            image_exclusions=[],
        ),
        _wavelength_values=defaults["wavelength_values"],
        _preprocessing_signature=lambda image_key: ("preproc", round(image_key[0] * 1000 + image_key[1], 3)),
        _roi_signature=lambda rois: tuple(
            (int(roi.area_roi_id), round(float(roi.center_x), 3), round(float(roi.center_y), 3), round(float(roi.sample_radius_px), 3))
            for roi in rois
        ),
        _analysis_metric_key=lambda: defaults["metric_key"],
        _analysis_poly_order=lambda: defaults["poly_order"],
        _analysis_wavelength_range=lambda: defaults["wavelength_range"],
    )
    mixin.window = window
    mixin._active_formula_key = lambda: defaults["formula_key"]
    mixin._analysis_fit_method_key = lambda: defaults["fit_method_key"]
    mixin._roi_reduction_signature_elements = lambda: (defaults["reduction_method"],)
    mixin._exclusion_signature_for_cube = lambda spectral_cube_index: tuple(
        (spectral_cube_index + i) % 3 == 0 for i in range(len(defaults["wavelength_values"]))
    )
    mixin._signature_hash = staticmethod(signature_hash)
    return mixin


_ROIS = [
    AreaRoi(area_roi_id=1, center_x=12.5, center_y=30.0, sample_radius_px=5.0),
    AreaRoi(area_roi_id=2, center_x=48.25, center_y=30.0, sample_radius_px=5.0),
    AreaRoi(area_roi_id=17, center_x=100.0, center_y=64.5, sample_radius_px=7.5),
]


class TestSensorgramPointSignatureHashForRoi(unittest.TestCase):
    def test_no_dataset_returns_none(self) -> None:
        mixin = _make_mixin()
        mixin.window._state.dataset = None
        self.assertIsNone(mixin._sensorgram_point_signature_hash_cube_context(5))

    def test_deterministic_across_repeated_calls(self) -> None:
        mixin = _make_mixin()
        ctx_a = mixin._sensorgram_point_signature_hash_cube_context(3)
        ctx_b = mixin._sensorgram_point_signature_hash_cube_context(3)
        self.assertEqual(ctx_a, ctx_b)
        roi = _ROIS[0]
        self.assertEqual(
            mixin._sensorgram_point_signature_hash_for_roi(ctx_a, roi),
            mixin._sensorgram_point_signature_hash_for_roi(ctx_b, roi),
        )

    def test_different_rois_in_the_same_cube_get_different_hashes(self) -> None:
        mixin = _make_mixin()
        cube_context = mixin._sensorgram_point_signature_hash_cube_context(3)
        hashes = {mixin._sensorgram_point_signature_hash_for_roi(cube_context, roi) for roi in _ROIS}
        self.assertEqual(len(hashes), len(_ROIS))

    def test_same_roi_in_different_cubes_gets_different_hashes(self) -> None:
        mixin = _make_mixin()
        roi = _ROIS[0]
        hashes = {
            mixin._sensorgram_point_signature_hash_for_roi(mixin._sensorgram_point_signature_hash_cube_context(cube_index), roi)
            for cube_index in (0, 1, 2, 3)
        }
        self.assertEqual(len(hashes), 4)

    def test_cube_context_changes_when_any_relevant_setting_changes(self) -> None:
        baseline = _make_mixin()
        baseline_context = baseline._sensorgram_point_signature_hash_cube_context(3)
        variants = {
            "dataset_folder": "C:/different/dataset",
            "wavelength_values": [500.0, 520.0, 540.0, 560.0, 600.0],
            "reference_inner_radius_px": 9.0,
            "reference_outer_radius_px": 22.0,
            "reduction_method": "median",
            "formula_key": "ratio",
            "fit_method_key": "gaussian",
            "metric_key": "maximum",
            "poly_order": 2,
            "wavelength_range": (500.0, 600.0),
        }
        for field, changed_value in variants.items():
            with self.subTest(field=field):
                mixin = _make_mixin(**{field: changed_value})
                changed_context = mixin._sensorgram_point_signature_hash_cube_context(3)
                self.assertNotEqual(changed_context, baseline_context, msg=f"{field} change was not reflected in the hash")

    def test_roi_geometry_changes_the_per_roi_hash(self) -> None:
        mixin = _make_mixin()
        cube_context = mixin._sensorgram_point_signature_hash_cube_context(3)
        roi = AreaRoi(area_roi_id=1, center_x=12.5, center_y=30.0, sample_radius_px=5.0)
        moved_roi = AreaRoi(area_roi_id=1, center_x=13.5, center_y=30.0, sample_radius_px=5.0)
        self.assertNotEqual(
            mixin._sensorgram_point_signature_hash_for_roi(cube_context, roi),
            mixin._sensorgram_point_signature_hash_for_roi(cube_context, moved_roi),
        )


if __name__ == "__main__":
    unittest.main()
