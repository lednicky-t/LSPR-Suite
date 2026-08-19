"""Coverage for processing/roi_array_geometry.py - the fully-automatic array
finder that infers rows/cols/spacing/radius directly from image content,
with no manual values given up front."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.processing.roi_array_geometry import (
    estimate_array_geometry,
    estimate_reference_ring_radii,
)


def _array_image(
    *,
    size: int,
    rows: int,
    cols: int,
    spacing: float,
    radius: float,
    origin: tuple[float, float] = (0.0, 0.0),
    background: float = 50000.0,
    dip: float = 25000.0,
    debris: list[tuple[float, float, float]] | None = None,
    noise: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    """A flat bright background with a rows x cols grid of darker circular
    disks stamped on it - the periodic array pattern the geometry estimator
    is meant to recover. `noise` adds per-pixel Gaussian sensor noise
    (std dev in the same intensity units as `dip`/`background`) - real
    camera frames always have this, unlike a bare step-function disk image."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    image = np.full((size, size), background, dtype=np.float32)
    origin_x, origin_y = origin
    for row in range(rows):
        for col in range(cols):
            center_x = origin_x + col * spacing
            center_y = origin_y + row * spacing
            distance = np.hypot(xx - center_x, yy - center_y)
            image[distance <= radius] -= dip
    for debris_x, debris_y, debris_radius in debris or []:
        distance = np.hypot(xx - debris_x, yy - debris_y)
        image[distance <= debris_radius] -= dip * 1.5
    if noise > 0.0:
        rng = np.random.default_rng(seed)
        image = image + rng.normal(0.0, noise, image.shape).astype(np.float32)
    return image


class TestEstimateArrayGeometry(unittest.TestCase):
    def test_recovers_a_clean_4x4_array(self) -> None:
        image = _array_image(size=260, rows=4, cols=4, spacing=50.0, radius=10.0, origin=(30.0, 30.0))
        result = estimate_array_geometry(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.rows, 4)
        self.assertEqual(result.cols, 4)
        self.assertAlmostEqual(result.spacing_px, 50.0, delta=3.0)
        self.assertAlmostEqual(result.radius_px, 10.0, delta=2.5)
        self.assertAlmostEqual(result.origin_x, 30.0, delta=3.0)
        self.assertAlmostEqual(result.origin_y, 30.0, delta=3.0)

    def test_recovers_a_non_square_3x5_array(self) -> None:
        image = _array_image(size=340, rows=3, cols=5, spacing=45.0, radius=9.0, origin=(25.0, 25.0))
        result = estimate_array_geometry(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.rows, 3)
        self.assertEqual(result.cols, 5)
        self.assertAlmostEqual(result.spacing_px, 45.0, delta=3.0)

    def test_debris_does_not_corrupt_the_recovered_grid(self) -> None:
        image = _array_image(
            size=260,
            rows=4,
            cols=4,
            spacing=50.0,
            radius=10.0,
            origin=(30.0, 30.0),
            debris=[(130.0, 15.0, 3.0), (15.0, 130.0, 2.0)],
        )
        result = estimate_array_geometry(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.rows, 4)
        self.assertEqual(result.cols, 4)

    def test_recovers_array_under_realistic_sensor_noise(self) -> None:
        # Regression test: a step-function disk image (no per-pixel noise,
        # what every other test here used) let blob_log's default
        # sensitivity through unnoticed - real camera frames have sensor
        # noise, which without denoising fractures into ~2000 spurious
        # single-pixel "blobs" that swamp the real ones and collapse the
        # grid estimate to 1x1. See estimate_array_geometry's gaussian_filter
        # pre-smoothing step.
        image = _array_image(
            size=260, rows=4, cols=4, spacing=50.0, radius=10.0, origin=(30.0, 30.0),
            dip=8000.0, noise=3000.0, seed=7,
        )
        result = estimate_array_geometry(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.rows, 4)
        self.assertEqual(result.cols, 4)

    def test_recovers_array_under_low_contrast_and_heavy_noise(self) -> None:
        image = _array_image(
            size=260, rows=4, cols=4, spacing=50.0, radius=10.0, origin=(30.0, 30.0),
            dip=6000.0, noise=4000.0, seed=3,
        )
        result = estimate_array_geometry(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.rows, 4)
        self.assertEqual(result.cols, 4)

    def test_random_scatter_is_still_rejected_under_noise(self) -> None:
        rng = np.random.default_rng(9)
        image = _array_image(size=300, rows=1, cols=1, spacing=50.0, radius=0.0, origin=(0.0, 0.0), noise=3000.0, seed=9)
        yy, xx = np.mgrid[0:300, 0:300].astype(np.float32)
        for _ in range(16):
            center_x = rng.uniform(20.0, 280.0)
            center_y = rng.uniform(20.0, 280.0)
            distance = np.hypot(xx - center_x, yy - center_y)
            image[distance <= 10.0] -= 20000.0
        result = estimate_array_geometry(image)
        if result is not None:
            self.assertLessEqual(result.rows * result.cols, 16)

    def test_single_spot_returns_none(self) -> None:
        image = _array_image(size=120, rows=1, cols=1, spacing=50.0, radius=10.0, origin=(60.0, 60.0))
        self.assertIsNone(estimate_array_geometry(image))

    def test_single_row_returns_none(self) -> None:
        image = _array_image(size=260, rows=1, cols=4, spacing=50.0, radius=10.0, origin=(30.0, 60.0))
        self.assertIsNone(estimate_array_geometry(image))

    def test_flat_image_returns_none(self) -> None:
        image = np.full((100, 100), 40000.0, dtype=np.float32)
        self.assertIsNone(estimate_array_geometry(image))

    def test_empty_image_returns_none(self) -> None:
        self.assertIsNone(estimate_array_geometry(np.zeros((0, 0), dtype=np.float32)))

    def test_random_scatter_is_not_reported_as_a_grid(self) -> None:
        rng = np.random.default_rng(4)
        image = np.full((300, 300), 50000.0, dtype=np.float32)
        yy, xx = np.mgrid[0:300, 0:300].astype(np.float32)
        # Same blob count/size as the recoverable cases above, but scattered
        # at random positions instead of on a lattice.
        for _ in range(16):
            center_x = rng.uniform(20.0, 280.0)
            center_y = rng.uniform(20.0, 280.0)
            distance = np.hypot(xx - center_x, yy - center_y)
            image[distance <= 10.0] -= 25000.0
        result = estimate_array_geometry(image)
        if result is not None:
            # If it does report something (small arrays can coincidentally
            # look grid-like), it must not confidently claim a large grid.
            self.assertLessEqual(result.rows * result.cols, 16)

    def test_mostly_filled_array_with_a_few_missing_spots_still_recovers(self) -> None:
        # 13/16 cells present (81% occupancy) - a handful of missing spots
        # shouldn't stop the grid from being recognized.
        missing = {(0, 0), (1, 2), (3, 3)}
        yy, xx = np.mgrid[0:260, 0:260].astype(np.float32)
        image = np.full((260, 260), 50000.0, dtype=np.float32)
        for row in range(4):
            for col in range(4):
                if (row, col) in missing:
                    continue
                center_x = 30.0 + col * 50.0
                center_y = 30.0 + row * 50.0
                distance = np.hypot(xx - center_x, yy - center_y)
                image[distance <= 10.0] -= 25000.0
        result = estimate_array_geometry(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.rows, 4)
        self.assertEqual(result.cols, 4)

    def test_sparsely_filled_grid_is_rejected(self) -> None:
        # Only 7/16 cells present (44% occupancy) - too sparse to confidently
        # call a 4x4 array; must refuse rather than guess.
        present = {(0, 3), (1, 2), (1, 3), (2, 0), (2, 1), (3, 3), (2, 2)}
        yy, xx = np.mgrid[0:260, 0:260].astype(np.float32)
        image = np.full((260, 260), 50000.0, dtype=np.float32)
        for row, col in present:
            center_x = 30.0 + col * 50.0
            center_y = 30.0 + row * 50.0
            distance = np.hypot(xx - center_x, yy - center_y)
            image[distance <= 10.0] -= 25000.0
        self.assertIsNone(estimate_array_geometry(image))

    def test_valid_mask_excludes_blobs_outside_it(self) -> None:
        image = _array_image(size=260, rows=4, cols=4, spacing=50.0, radius=10.0, origin=(30.0, 30.0))
        valid_mask = np.ones((260, 260), dtype=bool)
        # Blank out an entire column of the grid by excluding it from the
        # valid mask - detection should fall back to what remains (a 4x3
        # grid), not silently include masked-out blobs.
        valid_mask[:, :55] = False
        result = estimate_array_geometry(image, valid_mask=valid_mask)
        self.assertIsNotNone(result)
        self.assertEqual(result.cols, 3)


class TestEstimateReferenceRingRadii(unittest.TestCase):
    def test_ring_area_matches_sample_circle_area(self) -> None:
        sample_radius = 12.0
        inner, outer = estimate_reference_ring_radii(sample_radius)
        self.assertGreater(inner, sample_radius)
        ring_area = np.pi * (outer**2 - inner**2)
        sample_area = np.pi * sample_radius**2
        self.assertAlmostEqual(ring_area, sample_area, delta=sample_area * 0.01)

    def test_matches_this_apps_existing_manual_defaults_ratio(self) -> None:
        # AreaRoiDetectionSettings defaults: sample_radius_px=10.0,
        # reference_inner_radius_px=14.0, reference_outer_radius_px=18.0.
        inner, outer = estimate_reference_ring_radii(10.0)
        self.assertAlmostEqual(inner, 14.0, delta=0.5)
        self.assertAlmostEqual(outer, 18.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
