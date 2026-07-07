from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import PreprocessingSettings
from lspr_imaging_app.processing.preprocess import (
    apply_spatial_preprocessing,
    flatten_background,
    raw_bounding_box_for_processed_box,
    resample_raw_patch_to_processed_box,
    spatial_output_shape,
)


def _gradient_image(height: int, width: int) -> np.ndarray:
    # Every pixel has a distinct value derived from its own (row, col), so a
    # coordinate-mapping bug shows up as a wrong *value*, not just a wrong shape.
    yy, xx = np.indices((height, width), dtype=np.float32)
    return (yy * 1000.0 + xx).astype(np.float32)


class TestSpatialOutputShape(unittest.TestCase):
    def test_identity_settings_preserve_shape_and_pixels(self) -> None:
        settings = PreprocessingSettings()
        image = _gradient_image(64, 48)
        self.assertEqual(spatial_output_shape(image.shape, settings), (64, 48))
        processed = apply_spatial_preprocessing(image, settings)
        np.testing.assert_allclose(processed, image, atol=1e-3)

    def test_rotation_changes_reported_shape_consistently_with_actual_output(self) -> None:
        settings = PreprocessingSettings(rotation_angle_deg=33.0)
        image = _gradient_image(80, 60)
        reported_shape = spatial_output_shape(image.shape, settings)
        actual = apply_spatial_preprocessing(image, settings)
        self.assertEqual(actual.shape, reported_shape)


class TestRoiScopedFastPathMatchesFullImage(unittest.TestCase):
    """The ROI-scoped zarr fast path (raw_bounding_box_for_processed_box +
    resample_raw_patch_to_processed_box) must always agree with the
    full-image path (apply_spatial_preprocessing, sliced) -- that agreement
    is the entire point of the optimization. These lock in the geometry so a
    future change can't silently make the fast path wrong while the full
    path still looks fine.
    """

    def _check_box_matches_full_image(self, in_shape, settings: PreprocessingSettings, box) -> None:
        image = _gradient_image(*in_shape)
        x0, y0, x1, y1 = box
        expected = apply_spatial_preprocessing(image, settings)[y0:y1, x0:x1]

        # Scoped path A: pretend the "chunk-aware read" only fetched the
        # bounding box the geometry function says is needed, not the whole image.
        raw_x0, raw_y0, raw_x1, raw_y1 = raw_bounding_box_for_processed_box(in_shape, settings, box)
        raw_patch = image[raw_y0:raw_y1, raw_x0:raw_x1]
        scoped = resample_raw_patch_to_processed_box(raw_patch, (raw_x0, raw_y0), in_shape, settings, box)
        np.testing.assert_allclose(scoped, expected, atol=1e-2)

        # Scoped path B: passing the *whole* image as the "patch" at origin
        # (0, 0) must reduce to exactly the same computation as the full path.
        full_as_patch = resample_raw_patch_to_processed_box(image, (0, 0), in_shape, settings, box)
        np.testing.assert_allclose(full_as_patch, expected, atol=1e-6)

    def test_no_rotation_or_flip(self) -> None:
        self._check_box_matches_full_image((90, 70), PreprocessingSettings(), (10, 15, 40, 45))

    def test_rotation_only(self) -> None:
        settings = PreprocessingSettings(rotation_angle_deg=17.0)
        self._check_box_matches_full_image((90, 70), settings, (5, 5, 50, 50))

    def test_rotation_and_both_flips(self) -> None:
        settings = PreprocessingSettings(rotation_angle_deg=-24.0, flip_horizontal=True, flip_vertical=True)
        self._check_box_matches_full_image((100, 80), settings, (20, 10, 70, 60))

    def test_ninety_degree_rotation(self) -> None:
        settings = PreprocessingSettings(rotation_angle_deg=90.0)
        self._check_box_matches_full_image((60, 100), settings, (0, 0, 30, 40))

    def test_box_touching_the_rotated_canvas_edge(self) -> None:
        # Near the edge-stretch-filled corners is exactly where an off-by-one
        # in the raw bounding box would first show up.
        settings = PreprocessingSettings(rotation_angle_deg=41.0)
        in_shape = (70, 70)
        out_shape = spatial_output_shape(in_shape, settings)
        box = (0, 0, min(20, out_shape[1]), min(20, out_shape[0]))
        self._check_box_matches_full_image(in_shape, settings, box)

    def test_dark_fill_mode_instead_of_nearest(self) -> None:
        settings = PreprocessingSettings(rotation_angle_deg=25.0, rotation_fill_dark=True)
        self._check_box_matches_full_image((80, 80), settings, (5, 5, 45, 45))


class TestFlattenBackgroundRegionScoping(unittest.TestCase):
    def _bumpy_image(self, height: int = 96, width: int = 96) -> np.ndarray:
        yy, xx = np.indices((height, width), dtype=np.float32)
        # Smooth spatial background plus a few sharp "features" to flatten around.
        background = 500.0 + 3.0 * xx + 2.0 * yy
        rng = np.random.default_rng(0)
        noise = rng.normal(0.0, 5.0, size=(height, width)).astype(np.float32)
        image = background + noise
        image[40:50, 40:50] += 2000.0  # a bright feature, like an ROI signal
        return np.clip(image, 0.0, 65535.0).astype(np.float32)

    def test_no_binning_region_matches_full_computation_exactly(self) -> None:
        image = self._bumpy_image()
        region = (20, 20, 70, 70)
        full = flatten_background(image, sigma_px=15.0, binning=1)
        x0, y0, x1, y1 = region
        scoped = flatten_background(image, sigma_px=15.0, binning=1, region=region)
        np.testing.assert_allclose(scoped, full[y0:y1, x0:x1], atol=0.0)

    def test_binned_region_is_close_to_full_computation(self) -> None:
        image = self._bumpy_image()
        region = (20, 20, 70, 70)
        full = flatten_background(image, sigma_px=15.0, binning=3)
        x0, y0, x1, y1 = region
        scoped = flatten_background(image, sigma_px=15.0, binning=3, region=region)
        # The scoped path skips the full-resolution upsample and uses a
        # locally-resized patch instead -- expected to differ slightly from
        # the fully-upsampled reference, but not by much relative to the
        # background's own dynamic range here (a few thousand counts).
        np.testing.assert_allclose(scoped, full[y0:y1, x0:x1], atol=5.0)


if __name__ == "__main__":
    unittest.main()
