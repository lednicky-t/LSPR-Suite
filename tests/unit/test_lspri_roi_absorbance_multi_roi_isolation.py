"""Regression tests for two related bugs in multi-ROI absorbance calculation
(gui/analysis_tasks.py's `_scoped_formula_spectrum_task` - the one
spectrum-compute path, used for every dataset regardless of format; see
apps/LSPRi/eva/docs/bulk_analysis_performance_investigation.md's
"Follow-up #6" for why there's only one now):

1. A selected ROI's own reference ring only excluded its OWN sample circle,
   not any other selected ROI's sample circle. In a dense array, a
   neighboring selected ROI's (often much brighter) sample spot could fall
   inside this ROI's reference ring and bias its reference mean - and
   therefore its absorbance - even though the two ROIs are otherwise
   unrelated.

2. When several ROIs were selected together, the "combined" absorbance value
   (used for the group's sensorgram) was computed by pooling every selected
   ROI's sample pixels into one array and every selected ROI's reference
   pixels into another, then taking one ratio - instead of computing each
   ROI's own absorbance independently (sample vs. its own reference) and
   averaging those per-ROI *results* together. Pooling raw pixels before the
   sample/reference ratio is not the same calculation as averaging each
   ROI's own ratio afterward, and mixes pixels from different physical
   apertures.
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

from lspr_imaging_app.domain.models import (  # noqa: E402
    AreaRoi,
    ImageKey,
    ImageRecord,
    PreprocessingSettings,
)
from lspr_imaging_app.gui.analysis_tasks import _scoped_formula_spectrum_task  # noqa: E402

IMAGE_SHAPE = (60, 80)  # (rows, cols) -> (height, width)
BACKGROUND_VALUE = 100.0
ROI_A_SAMPLE_VALUE = 300.0
ROI_B_SAMPLE_VALUE = 5000.0  # deliberately much brighter, to make contamination obvious


def _make_image(*, include_roi_b_spot: bool) -> np.ndarray:
    image = np.full(IMAGE_SHAPE, BACKGROUND_VALUE, dtype=np.float32)
    yy, xx = np.mgrid[0 : IMAGE_SHAPE[0], 0 : IMAGE_SHAPE[1]]
    # ROI A: sample circle at (30, 30), radius 5.
    image[np.hypot(xx - 30, yy - 30) <= 5] = ROI_A_SAMPLE_VALUE
    if include_roi_b_spot:
        # ROI B: sample circle at (48, 30), radius 5 - 18px from A's center,
        # which sits inside A's reference ring (inner=8, outer=20) below.
        image[np.hypot(xx - 48, yy - 30) <= 5] = ROI_B_SAMPLE_VALUE
    return image


def _run_task(
    tmp_path: Path, *, selected_roi_ids: tuple[int, ...], roi_a: AreaRoi, roi_b: AreaRoi, include_roi_b_spot: bool = True
):
    image_path = tmp_path / "frame.tif"
    tifffile.imwrite(str(image_path), _make_image(include_roi_b_spot=include_roi_b_spot).astype(np.uint16))

    preprocessing = PreprocessingSettings()
    source_rois = [roi_a, roi_b]
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=1), path=image_path)
    # `dataset` is unused by dataset_load_plane_roi whenever `record` is
    # given directly (as here) - the whole plane, matching what TIFF's
    # read-region is per spectrum_read_region, since this test doesn't need
    # a real ImageDataset to exercise that path.
    measurement_payload = [(500.0, None, None, record)]
    box = (0, 0, IMAGE_SHAPE[1], IMAGE_SHAPE[0])

    return _scoped_formula_spectrum_task(
        None,
        1,
        measurement_payload,
        source_rois,
        selected_roi_ids,
        8.0,
        20.0,
        box,
        preprocessing,
        IMAGE_SHAPE,
        roi_mask_cache={},
        roi_mask_cache_lock=threading.Lock(),
        roi_mask_cache_max_size=8,
        mask_state=None,
        reduction_method="mean",
    )


class TestMultiRoiFormulaSpectrumIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.roi_a = AreaRoi(area_roi_id=1, center_x=30.0, center_y=30.0, sample_radius_px=5.0)
        self.roi_b = AreaRoi(area_roi_id=2, center_x=48.0, center_y=30.0, sample_radius_px=5.0)

    def test_roi_a_reference_mean_excludes_a_co_selected_roi_b_sample_circle(self) -> None:
        # Both ROIs exist in the dataset and both are selected together (the
        # real scenario: e.g. a grouped multi-ROI selection). ROI B's sample
        # circle geometrically overlaps ROI A's reference ring - since B is
        # selected too, its pixels must be excluded from A's reference mean,
        # or A's value gets pulled way up toward B's brightness (5000).
        with tempfile.TemporaryDirectory() as tmp:
            together = _run_task(Path(tmp), selected_roi_ids=(1, 2), roi_a=self.roi_a, roi_b=self.roi_b)
            reference_mean_a = float(together.area_roi_results[1].reference_reduced_value[0])
            self.assertAlmostEqual(reference_mean_a, BACKGROUND_VALUE, delta=1.0)

    def test_roi_a_reference_mean_matches_a_clean_image_once_roi_b_is_selected(self) -> None:
        # Two images: one where ROI B's bright spot genuinely doesn't exist
        # (the true clean baseline for A's reference ring), and one where it
        # does but B is selected alongside A. Once B is selected, its pixels
        # must be excluded from A's reference ring - the two results should
        # match, even though the second image actually contains B's spot.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            clean_baseline = _run_task(
                tmp_path, selected_roi_ids=(1,), roi_a=self.roi_a, roi_b=self.roi_b, include_roi_b_spot=False
            )
            together = _run_task(tmp_path, selected_roi_ids=(1, 2), roi_a=self.roi_a, roi_b=self.roi_b)

            reference_mean_clean = float(clean_baseline.area_roi_results[1].reference_reduced_value[0])
            reference_mean_together = float(together.area_roi_results[1].reference_reduced_value[0])
            self.assertAlmostEqual(reference_mean_clean, reference_mean_together, delta=1.0)

    def test_combined_formula_spectrum_is_the_average_of_per_roi_formula_spectra_not_pooled_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = _run_task(tmp_path, selected_roi_ids=(1, 2), roi_a=self.roi_a, roi_b=self.roi_b)

            formula_spectrum_a = float(result.area_roi_results[1].formula_values[0])
            formula_spectrum_b = float(result.area_roi_results[2].formula_values[0])
            combined_formula_spectrum = float(result.formula_values[0])

            self.assertTrue(np.isfinite(formula_spectrum_a))
            self.assertTrue(np.isfinite(formula_spectrum_b))
            self.assertAlmostEqual(combined_formula_spectrum, (formula_spectrum_a + formula_spectrum_b) / 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
