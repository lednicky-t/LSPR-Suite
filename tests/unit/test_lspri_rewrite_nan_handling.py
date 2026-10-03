"""NaN handling across the rewrite's pure-computation modules
(TASK_rotation_fill_handling.md §4): pixels without a value (NaN, e.g. created
by rotation) are never used, never spread, and never turned into a number.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring.

No Qt, no dataset. One class per consumer in the task's §4 table.
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
    from lspr_imaging_app.analysis.provenance import ProvenanceRecord
    from lspr_imaging_app.analysis.reduction import (
        reduce_mean,
        reduce_median,
        reduce_plane_fit_reference,
        reduce_trimmed_mean,
        weighted_mean,
    )
    from lspr_imaging_app.analysis.store import read_all_cells, write_cell
    from lspr_imaging_app.analysis.tasks import CellResult, WavelengthCoverage
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.chromatic.landmark_autotrack import prepare_registration_image
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.mask.raster_tools import (
        create_histogram_mask,
        create_local_contrast_mask,
        create_relative_contrast_mask,
    )
    from lspr_imaging_app.image_tools.preprocess import apply_preprocessing
    from lspr_imaging_app.panels.histogram.compute import excluded_pixel_text, population_counts, histogram_edges
    from lspr_imaging_app.panels.image.no_data import format_pixel_value, no_data_overlay_rgba
    from lspr_imaging_app.roi.detection import detect_rois, ignored_pixel_mask
    from lspr_imaging_app.roi.model import AreaRoiDetectionSettings
    from lspr_imaging_app.storage.session import _decode_settings
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _particles_on_dark(shape=(120, 160), centers=((40, 60), (80, 110)), radius=5.0) -> np.ndarray:
    yy, xx = np.indices(shape, dtype=np.float32)
    image = np.full(shape, 100.0, dtype=np.float32)
    for cy, cx in centers:
        image += 900.0 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2.0 * (radius / 2.0) ** 2)))
    return image


class TestReductionsRefuseNaN(unittest.TestCase):
    def test_every_reduction_raises_on_a_nan_pixel(self) -> None:
        pixels = np.array([1.0, 2.0, np.nan, 4.0])
        for func in (reduce_mean, reduce_median, reduce_trimmed_mean):
            with self.subTest(func=func.__name__):
                with self.assertRaises(ValueError):
                    func(pixels)
        with self.assertRaises(ValueError):
            weighted_mean(pixels, np.ones(4))
        with self.assertRaises(ValueError):
            reduce_plane_fit_reference(pixels, np.arange(4.0), np.arange(4.0), 1.0, 1.0)

    def test_no_pixels_is_nan_not_an_error_or_a_zero(self) -> None:
        empty = np.array([], dtype=np.float64)
        self.assertTrue(np.isnan(reduce_mean(empty)))
        self.assertTrue(np.isnan(reduce_median(empty)))
        self.assertTrue(np.isnan(reduce_trimmed_mean(empty)))

    def test_plane_fit_with_too_few_pixels_is_nan(self) -> None:
        self.assertTrue(np.isnan(reduce_plane_fit_reference(np.array([1.0, 2.0, 3.0]), np.arange(3.0), np.arange(3.0), 0.0, 0.0)))


class TestIgnoreMaskDoesNotTouchTheImage(unittest.TestCase):
    def test_masked_pixels_keep_their_values(self) -> None:
        image = _particles_on_dark()
        mask = np.zeros(image.shape, dtype=bool)
        mask[10:30, 10:30] = True
        out = apply_preprocessing(image, GeometrySettings(), BackgroundSettings(), external_mask=mask)
        np.testing.assert_array_equal(out, image)

    def test_nan_corners_survive_a_mask_that_reaches_into_them(self) -> None:
        image = _particles_on_dark()
        settings = GeometrySettings(rotation_angle_deg=20.0)
        rotated = apply_preprocessing(image, settings, BackgroundSettings())
        everything = np.ones(rotated.shape, dtype=bool)  # a mask covering the NaN corners too
        out = apply_preprocessing(image, settings, BackgroundSettings(), external_mask=everything)
        np.testing.assert_array_equal(np.isnan(out), np.isnan(rotated))
        np.testing.assert_array_equal(out[~np.isnan(out)], rotated[~np.isnan(rotated)])

    def test_flatten_background_keeps_nan_and_excludes_the_mask_only_when_asked(self) -> None:
        image = _particles_on_dark()
        settings = GeometrySettings(rotation_angle_deg=20.0)
        mask = np.zeros(apply_preprocessing(image, settings, BackgroundSettings()).shape, dtype=bool)
        mask[40:60, 60:80] = True
        flatten = BackgroundSettings(flatten_background_enabled=True, flatten_background_sigma_px=10.0,
                                     flatten_background_binning=1, flatten_background_exclude_mask=True)
        out = apply_preprocessing(image, settings, flatten, mask_settings=AreaRoiDetectionSettings(ignore_marked_pixels=True),
                                  external_mask=mask)
        nan = np.isnan(out)
        self.assertTrue(nan.any())
        self.assertTrue(np.isfinite(out[~nan]).all())


class TestDetectionIgnoresInvalidPixels(unittest.TestCase):
    def _settings(self) -> AreaRoiDetectionSettings:
        return AreaRoiDetectionSettings(sample_radius_px=5.0)

    def test_non_finite_pixels_are_always_ignored(self) -> None:
        image = _particles_on_dark()
        image[:10, :10] = np.nan
        ignored = ignored_pixel_mask(image, self._settings())
        self.assertTrue(ignored[:10, :10].all())
        self.assertFalse(ignored[10:, 10:].any())

    def test_particles_are_found_and_the_data_edge_is_not(self) -> None:
        image = _particles_on_dark()
        rotated = apply_preprocessing(image, GeometrySettings(rotation_angle_deg=25.0), BackgroundSettings())
        # The rotation keeps the particles inside the data; the data edge (a
        # step from ~100 to NaN) must never become a detection.
        rois = detect_rois(rotated, self._settings())
        self.assertGreaterEqual(len(rois), 1)
        invalid = np.isnan(rotated)
        from scipy import ndimage
        distance = ndimage.distance_transform_edt(~invalid)
        for roi in rois:
            self.assertGreater(distance[int(round(roi.center_y)), int(round(roi.center_x))], 5.0)

    def test_nan_does_not_spread_into_the_search(self) -> None:
        image = _particles_on_dark()
        image[:, :20] = np.nan
        rois = detect_rois(image, self._settings())
        self.assertTrue(len(rois) >= 1)
        self.assertTrue(all(np.isfinite([roi.center_x, roi.center_y, roi.score]).all() for roi in rois))


class TestRegistrationImageIsNaNAware(unittest.TestCase):
    def test_nan_corner_is_neutral_everywhere_it_matters(self) -> None:
        rng = np.random.default_rng(1)
        image = rng.random((100, 120)).astype(np.float32) * 100.0 + 500.0
        reference = prepare_registration_image(image)
        image[:, :30] = np.nan
        prepared = prepare_registration_image(image)
        self.assertTrue(np.isfinite(prepared).all())  # NaN did not spread
        self.assertTrue((prepared[:, :30] == 0.0).all())  # no feature on or next to the invalid region
        self.assertTrue(np.abs(prepared[:, 40:]).max() > 0.0)
        self.assertEqual(reference.shape, prepared.shape)


class TestMaskCreationTools(unittest.TestCase):
    def test_pixels_without_value_are_never_marked(self) -> None:
        image = _particles_on_dark()
        image[:20, :20] = np.nan
        for name, mask in (
            ("histogram", create_histogram_mask(image, min_value=150.0, max_value=None)),
            ("relative", create_relative_contrast_mask(image, sigma_px=8.0, threshold_fraction=0.2)),
            ("local", create_local_contrast_mask(image, sigma_px=8.0, z_threshold=2.0)),
        ):
            with self.subTest(tool=name):
                self.assertFalse(mask[:20, :20].any())

    def test_valid_pixels_far_from_the_nan_region_are_unaffected_by_it(self) -> None:
        image = _particles_on_dark()
        clean = create_relative_contrast_mask(image, sigma_px=4.0, threshold_fraction=0.2)
        image2 = image.copy()
        image2[:, :20] = np.nan
        with_nan = create_relative_contrast_mask(image2, sigma_px=4.0, threshold_fraction=0.2)
        np.testing.assert_array_equal(clean[:, 60:], with_nan[:, 60:])


class TestHistogramAndDisplayHelpers(unittest.TestCase):
    def test_counts_and_excluded_text_ignore_nan(self) -> None:
        base = np.full((100, 100), 1000.0, dtype=np.float32)
        padded = np.full((300, 300), np.nan, dtype=np.float32)
        padded[100:200, 100:200] = base
        edges = histogram_edges(512.0)
        np.testing.assert_array_equal(population_counts(base, edges), population_counts(padded, edges))
        self.assertIsNone(excluded_pixel_text(base))
        text = excluded_pixel_text(padded)
        self.assertIn("80 000 px no data", text)
        self.assertIn("88.9%", text)

    def test_checker_is_opaque_exactly_on_nan_and_readout_says_no_data(self) -> None:
        image = np.ones((20, 30), dtype=np.float32)
        self.assertIsNone(no_data_overlay_rgba(image))
        image[:5, :5] = np.nan
        rgba = no_data_overlay_rgba(image)
        np.testing.assert_array_equal(rgba[..., 3] == 255, np.isnan(image))
        self.assertEqual(format_pixel_value(float("nan")), "no data")
        self.assertEqual(format_pixel_value(12.34), "12.3")


class TestStoreCoverageRoundTrip(unittest.TestCase):
    def _result(self, coverage) -> CellResult:
        provenance = ProvenanceRecord(roi_geometry={"x": 1}, reduction_method="mean", per_wavelength_settings=((500.0, 1),))
        return CellResult((500.0,), (1.0,), (2.0,), provenance, coverage)

    def test_coverage_and_flags_survive_write_and_read(self) -> None:
        cov = (WavelengthCoverage(100, 60, 0.6, 500, 300, 0.6, 0.25, "insufficient_coverage"),)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.h5"
            write_cell(path, 1, 0, self._result(cov))
            self.assertEqual(read_all_cells(path)[(1, 0)].coverage, cov)

    def test_a_cell_without_coverage_reads_back_as_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.h5"
            write_cell(path, 1, 0, self._result(None))
            self.assertIsNone(read_all_cells(path)[(1, 0)].coverage)


class TestOldSessionsStillLoad(unittest.TestCase):
    def test_removed_rotation_fill_key_is_ignored(self) -> None:
        block = {"rotation_angle_deg": 12.5, "rotation_fill_dark": True, "crop": {"x": 1, "y": 2, "width": 3, "height": 4, "enabled": True}}
        settings = _decode_settings(block, GeometrySettings(), {"crop": CropDefinition})
        self.assertEqual(settings.rotation_angle_deg, 12.5)
        self.assertFalse(hasattr(settings, "rotation_fill_dark"))
        self.assertEqual(settings.crop.width, 3)


if __name__ == "__main__":
    unittest.main()
