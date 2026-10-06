"""Pins for TASK_rotation_fill_handling.md (rotation-created pixels are NaN).
Phase 0 recorded the fill pixels of the old dark mode (counts below); Phase 2
switched the assertions to the NaN pixels, which must be exactly those.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring.

No Qt, no files.

1. `TestFillPixelsArePinned`: at 3/15/33 deg, which pixels are fill.
2. `TestBoundaryStraddlingRoi`: F1 of the audit. xfail until Phase 4.
3. `TestBackgroundIgnoresInvalidRegion`: the background estimate must not
   depend on, nor spread into / fill, the invalid region.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.analysis.tasks import (
        COVERAGE_FLAG_INSUFFICIENT,
        CoverageThresholds,
        WavelengthComputeInput,
        compute_cell,
    )
    from lspr_imaging_app.image_tools.background.estimate import flatten_background
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import (
        apply_spatial_preprocessing,
        apply_spatial_preprocessing_export,
    )
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

# Fill-pixel counts for a 300x400 raw image, recorded 2026-10-03 from the old
# dark-fill HEAD (audit §6: 10.4% / 34.6% / 49.0% of the canvas). Geometry
# only, so they do not depend on the pixel values.
_PINNED_FILL_COUNTS = {3.0: 13914, 15.0: 63050, 33.0: 114732}


def _synthetic_image(shape=(300, 400)) -> np.ndarray:
    # No real zeros anywhere, so a zero after rotation could only be a fill.
    rng = np.random.default_rng(0)
    return (rng.random(shape) * 1000.0 + 100.0).astype(np.float32)


class TestFillPixelsArePinned(unittest.TestCase):
    def test_nan_pixels_are_exactly_the_recorded_fill_and_all_others_finite(self) -> None:
        image = _synthetic_image()
        for angle, expected_count in _PINNED_FILL_COUNTS.items():
            with self.subTest(angle=angle):
                rotated = apply_spatial_preprocessing(image, GeometrySettings(rotation_angle_deg=angle))
                nan = np.isnan(rotated)
                self.assertEqual(int(nan.sum()), expected_count)
                self.assertTrue(np.all(np.isfinite(rotated[~nan])))
                self.assertGreater(float(np.nanmin(rotated)), 0.0)  # nothing was filled with 0

    def test_integer_input_becomes_float_only_when_rotating(self) -> None:
        raw = (np.arange(300 * 400).reshape(300, 400) % 60000 + 1).astype(np.uint16)
        rotated = apply_spatial_preprocessing(raw, GeometrySettings(rotation_angle_deg=15.0))
        self.assertEqual(rotated.dtype, np.float32)
        self.assertTrue(np.isnan(rotated).any())
        self.assertEqual(apply_spatial_preprocessing(raw, GeometrySettings()).dtype, np.uint16)

    def test_export_path_marks_the_same_pixels_as_the_gui_path(self) -> None:
        image = _synthetic_image()
        for angle in (3.0, 33.0):
            with self.subTest(angle=angle):
                settings = GeometrySettings(rotation_angle_deg=angle)
                gui = apply_spatial_preprocessing(image, settings)
                export = apply_spatial_preprocessing_export(image, settings)
                self.assertEqual(gui.shape, export.shape)
                np.testing.assert_array_equal(np.isnan(gui), np.isnan(export))
                np.testing.assert_allclose(gui[~np.isnan(gui)], export[~np.isnan(export)], atol=0.1)


def _straddling_cell(thresholds: CoverageThresholds, shared_dir: Path | None = None):
    """Flat image of 1000, 20 deg rotation, ROI ~2 px inside the data edge so
    its sample circle (r=6) and reference ring (12-18) both overlap the NaN
    corner. Returns the CellResult."""
    image = np.full((300, 400), 1000.0, dtype=np.float32)
    settings = GeometrySettings(rotation_angle_deg=20.0)
    invalid = np.isnan(apply_spatial_preprocessing(image, settings))
    ys, xs = np.nonzero(~invalid)
    top = ys.min()
    cy, cx = int(top) + 2, int(xs[ys == top].mean())
    roi = AreaRoi(area_roi_id=1, center_x=float(cx), center_y=float(cy), sample_diameter_px=12.0)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = shared_dir if shared_dir is not None else Path(tmp)
        wl_input = WavelengthComputeInput(
            wavelength_nm=500.0,
            raw_image=image,
            geometry_settings=settings,
            background_settings=BackgroundSettings(),
            chromatic_affine=np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            resolved_mask=None,
            mask_authored_frame=None,
            mask_scope=None,
        )
        return compute_cell(
            roi, 0, {500.0: wl_input},
            reduction_method="mean",
            trimmed_mean_fraction=0.1,
            default_reference_inner_diameter_px=24.0,
            default_reference_outer_diameter_px=36.0,
            masks_dir=tmp_path, chromatic_dir=tmp_path, settings_dir=tmp_path,
            naming=FrameNamingScheme(cube_digits=3, wavelength_decimals=1),
            coverage_thresholds=thresholds,
        )


class TestBoundaryStraddlingRoi(unittest.TestCase):
    """F1 of the audit: fill pixels used to be averaged into the sample and
    reference (583 instead of 1000 here)."""

    def test_valid_pixels_only_give_flat_sample_and_reference(self) -> None:
        result = _straddling_cell(CoverageThresholds(0.0, 0.0, 0.0))
        self.assertAlmostEqual(result.sample_values[0], 1000.0, places=3)
        self.assertAlmostEqual(result.reference_values[0], 1000.0, places=3)
        coverage = result.coverage[0]
        self.assertLess(coverage.sample_valid_fraction, 1.0)
        self.assertLess(coverage.reference_valid_fraction, 1.0)
        self.assertEqual(coverage.n_sample_nominal - coverage.n_sample_valid > 0, True)
        self.assertLess(coverage.reference_min_sector_fraction, coverage.reference_valid_fraction + 1e-9)
        self.assertEqual(coverage.flag, "")

    def test_below_threshold_is_nan_with_reason_flag(self) -> None:
        result = _straddling_cell(CoverageThresholds(min_sample_valid_fraction=0.99))
        self.assertTrue(np.isnan(result.sample_values[0]))
        self.assertTrue(np.isnan(result.reference_values[0]))
        self.assertEqual(result.coverage[0].flag, COVERAGE_FLAG_INSUFFICIENT)

    def test_thresholds_are_part_of_the_stored_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as shared:
            loose = _straddling_cell(CoverageThresholds(0.0, 0.0, 0.0), shared_dir=Path(shared))
            strict = _straddling_cell(CoverageThresholds(0.5, 0.5, 0.0), shared_dir=Path(shared))
        self.assertNotEqual(loose.provenance.per_wavelength_settings, strict.provenance.per_wavelength_settings)


class TestBackgroundIgnoresInvalidRegion(unittest.TestCase):
    def test_nan_region_neither_spreads_nor_is_filled_in(self) -> None:
        yy, xx = np.indices((120, 160), dtype=np.float32)
        base = 1000.0 + 0.5 * xx + 0.2 * yy  # smooth gradient
        invalid = np.zeros(base.shape, dtype=bool)
        invalid[:, :40] = True
        image = base.copy()
        image[invalid] = np.nan

        for binning in (1, 2):
            with self.subTest(binning=binning):
                flattened = flatten_background(image, sigma_px=10.0, binning=binning)
                self.assertTrue(np.all(np.isnan(flattened[invalid])))  # no value invented
                self.assertTrue(np.all(np.isfinite(flattened[~invalid])))  # NaN did not spread

    def test_estimate_equals_the_one_made_with_the_region_excluded_by_mask(self) -> None:
        yy, xx = np.indices((120, 160), dtype=np.float32)
        base = 1000.0 + 0.5 * xx + 0.2 * yy
        invalid = np.zeros(base.shape, dtype=bool)
        invalid[:, :40] = True
        with_nan = base.copy()
        with_nan[invalid] = np.nan
        garbage = base.copy()
        garbage[invalid] = 60000.0
        via_nan = flatten_background(with_nan, sigma_px=10.0, binning=1)
        via_mask = flatten_background(
            garbage, sigma_px=10.0, binning=1, external_mask=invalid,
            mask_settings=AreaRoiDetectionSettings(ignore_marked_pixels=True),
        )
        np.testing.assert_allclose(via_nan[~invalid], via_mask[~invalid], rtol=0, atol=1e-3)


if __name__ == "__main__":
    unittest.main()
