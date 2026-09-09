"""Regression guard for `_scoped_formula_spectrum_task`'s bulk-sweep
reduction scope (gui/analysis_tasks.py): with `compute_all_reduction_
methods=False` (a bulk "Start analysis" sweep of more than one cube - see
gui/analysis_worker_mixin.py's `compute_all_reduction_methods = len(
spectral_cubes) <= 1`), only the *active* Reduction method is computed and
saved for each ROI - every other method's entry stays NaN.

A version of this that computed mean/median/trimmed_mean unconditionally
(reasoning: they're each individually cheap, unlike plane_fit's own
coordinate scan + least-squares solve) was tried and reverted - see
apps/LSPRi/eva/docs/bulk_analysis_performance_investigation.md's Follow-up
#13/#14: even those "cheap" methods measured +349ms/cube at a real 160-ROI
scale (dominated by `reduce_median`'s and `reduce_trimmed_mean`'s own
per-call cost multiplied by thousands of ROI/wavelength pairs per cube),
too much added cost for a real bulk sweep. This test exists so that
regression doesn't silently reappear.

Reuses the same synthetic-image harness pattern as
test_lspri_roi_absorbance_multi_roi_isolation.py.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi, ImageKey, ImageRecord, PreprocessingSettings  # noqa: E402
from lspr_imaging_app.gui.analysis_tasks import _scoped_formula_spectrum_task  # noqa: E402
from lspr_imaging_app.processing.roi_math import REDUCTION_METHODS  # noqa: E402

IMAGE_SHAPE = (60, 80)  # (rows, cols) -> (height, width)
BACKGROUND_VALUE = 100.0
SAMPLE_VALUE = 300.0


def _make_image() -> np.ndarray:
    image = np.full(IMAGE_SHAPE, BACKGROUND_VALUE, dtype=np.float32)
    yy, xx = np.mgrid[0 : IMAGE_SHAPE[0], 0 : IMAGE_SHAPE[1]]
    image[np.hypot(xx - 30, yy - 30) <= 5] = SAMPLE_VALUE
    return image


def _run_task(tmp_path: Path, *, reduction_method: str, compute_all_reduction_methods: bool):
    image_path = tmp_path / "frame.tif"
    tifffile.imwrite(str(image_path), _make_image().astype(np.uint16))

    preprocessing = PreprocessingSettings()
    roi = AreaRoi(area_roi_id=1, center_x=30.0, center_y=30.0, sample_radius_px=5.0)
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=1), path=image_path)
    measurement_payload = [(500.0, None, None, record)]
    box = (0, 0, IMAGE_SHAPE[1], IMAGE_SHAPE[0])

    return _scoped_formula_spectrum_task(
        None,
        1,
        measurement_payload,
        [roi],
        (1,),
        8.0,
        20.0,
        box,
        preprocessing,
        IMAGE_SHAPE,
        roi_mask_cache={},
        roi_mask_cache_lock=threading.Lock(),
        roi_mask_cache_max_size=8,
        mask_state=None,
        reduction_method=reduction_method,
        compute_all_reduction_methods=compute_all_reduction_methods,
    )


class TestBulkSweepReductionScope(unittest.TestCase):
    def test_only_the_active_method_is_real_during_a_bulk_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_task(Path(tmp), reduction_method="mean", compute_all_reduction_methods=False)
        reduced = result.area_roi_results[1].reduced_values_by_method
        self.assertEqual(set(reduced.keys()), set(REDUCTION_METHODS))
        sample_values, reference_values = reduced["mean"]
        self.assertTrue(np.all(np.isfinite(sample_values)))
        self.assertTrue(np.all(np.isfinite(reference_values)))
        for method in ("median", "trimmed_mean", "plane_fit"):
            sample_values, reference_values = reduced[method]
            self.assertTrue(np.all(np.isnan(sample_values)), msg=method)
            self.assertTrue(np.all(np.isnan(reference_values)), msg=method)

    def test_switching_the_active_method_is_the_only_thing_that_changes_which_entry_is_real(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_task(Path(tmp), reduction_method="plane_fit", compute_all_reduction_methods=False)
        reduced = result.area_roi_results[1].reduced_values_by_method
        sample_values, reference_values = reduced["plane_fit"]
        self.assertTrue(np.all(np.isfinite(sample_values)))
        self.assertTrue(np.all(np.isfinite(reference_values)))
        for method in ("mean", "median", "trimmed_mean"):
            sample_values, reference_values = reduced[method]
            self.assertTrue(np.all(np.isnan(sample_values)), msg=method)
            self.assertTrue(np.all(np.isnan(reference_values)), msg=method)

    def test_single_cube_preview_path_still_computes_every_method(self) -> None:
        # compute_all_reduction_methods=True (live single-cube preview) is
        # unaffected by the bulk-sweep-only scoping above - unchanged
        # behavior, so switching Reduction interactively on one cube is
        # still an instant re-projection, not a re-read.
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_task(Path(tmp), reduction_method="mean", compute_all_reduction_methods=True)
        reduced = result.area_roi_results[1].reduced_values_by_method
        for method in REDUCTION_METHODS:
            sample_values, reference_values = reduced[method]
            self.assertTrue(np.all(np.isfinite(sample_values)), msg=method)
            self.assertTrue(np.all(np.isfinite(reference_values)), msg=method)


if __name__ == "__main__":
    unittest.main()
