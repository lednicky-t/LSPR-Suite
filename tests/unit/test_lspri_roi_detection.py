"""Coverage for the ROI-detection algorithm itself (processing/roi_detection.py).

Only refresh_roi_metrics had a direct test before this file; detect_rois and
its helpers (_refine_detected_roi, _fit_grid_array, _roi_circular_contrast_score,
_estimate_grid_origin_1d) are the actual "find the sample spots" science code
and had none. A few tests import the underscore-prefixed helpers directly -
that's intentional: the module has no other public seam for pinning down their
exact numeric behavior (e.g. the grid-origin search, the disk/ring contrast
formula) independent of the full detect_rois pipeline.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from math import hypot

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiDetectionSettings
from lspr_imaging_app.processing.roi_detection import (
    _estimate_grid_origin_1d,
    _masked_gaussian_filter,
    _refine_detected_roi,
    _roi_circular_contrast_score,
    detect_rois,
    ignored_pixel_mask,
)


def _disks_image(
    size: int,
    disks: list[tuple[float, float, float]],
    background: float = 100.0,
    peak: float = 900.0,
) -> np.ndarray:
    """A flat background with one or more filled circles ("disks") stamped on it.

    Each entry in ``disks`` is (center_x, center_y, radius).
    """
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    image = np.full((size, size), background, dtype=np.float32)
    for center_x, center_y, radius in disks:
        distance = np.hypot(xx - center_x, yy - center_y)
        image[distance <= radius] = peak
    return image


def _nearest_distance(point: tuple[float, float], candidates: list[tuple[float, float]]) -> float:
    return min(hypot(point[0] - cx, point[1] - cy) for cx, cy in candidates)


class TestDetectRoisEdgeCases(unittest.TestCase):
    def test_empty_image_returns_no_rois(self) -> None:
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)
        self.assertEqual(detect_rois(np.zeros((0, 0), dtype=np.float32), settings), [])

    def test_fully_ignored_pixels_returns_no_rois(self) -> None:
        image = _disks_image(60, [(30.0, 30.0, 8.0)])
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0, ignore_marked_pixels=True)
        external_mask = np.ones((60, 60), dtype=bool)
        self.assertEqual(detect_rois(image, settings, external_mask=external_mask), [])

    def test_intensity_range_excluding_everything_returns_no_rois(self) -> None:
        image = _disks_image(60, [(30.0, 30.0, 8.0)])
        settings = AreaRoiDetectionSettings(
            mode="bright",
            sample_radius_px=8.0,
            intensity_min_value=1_000_000.0,
            intensity_max_value=1_000_001.0,
        )
        self.assertEqual(detect_rois(image, settings), [])

    def test_progress_callback_reaches_100_and_stays_in_range(self) -> None:
        image = _disks_image(80, [(40.0, 40.0, 8.0)])
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)
        calls: list[tuple[int, str]] = []
        detect_rois(image, settings, progress_callback=lambda percent, text: calls.append((percent, text)))
        self.assertTrue(calls)
        self.assertEqual(calls[-1][0], 100)
        self.assertTrue(all(0 <= percent <= 100 for percent, _ in calls))


class TestDetectRoisSingleDisk(unittest.TestCase):
    def test_bright_disk_is_found_near_its_true_center(self) -> None:
        image = _disks_image(100, [(50.0, 50.0, 8.0)])
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)

        rois = detect_rois(image, settings)

        self.assertEqual(len(rois), 1)
        self.assertLess(hypot(rois[0].center_x - 50.0, rois[0].center_y - 50.0), 3.0)
        self.assertGreater(rois[0].score, 0.0)
        self.assertEqual(rois[0].area_roi_id, 1)

    def test_dark_disk_is_found_near_its_true_center(self) -> None:
        # Invert the disk: dark spot on a bright field, mode="dark".
        image = 1000.0 - _disks_image(100, [(50.0, 50.0, 8.0)])
        settings = AreaRoiDetectionSettings(mode="dark", sample_radius_px=8.0)

        rois = detect_rois(image, settings)

        self.assertEqual(len(rois), 1)
        self.assertLess(hypot(rois[0].center_x - 50.0, rois[0].center_y - 50.0), 3.0)
        self.assertGreater(rois[0].score, 0.0)


class TestDetectRoisRejectsNonPositiveScoreCandidates(unittest.TestCase):
    def test_no_intensity_bound_and_no_array_cap_does_not_flood_with_flat_background_maxima(self) -> None:
        # Regression test: with array_rows=array_cols=0 (freeform) and no
        # intensity_min/max_value, expected_count is 0 so max_rois falls back
        # to len(candidates) - i.e. unlimited. Before the score<=0.0 floor was
        # added to this loop, every min-distance-spaced local maximum in the
        # flat background (score exactly 0.0) was accepted right alongside
        # the real disk, returning 36 ROIs instead of 1 for this image. The
        # floor mirrors the one _refine_roi_center already applies below.
        image = _disks_image(100, [(50.0, 50.0, 8.0)])
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)

        rois = detect_rois(image, settings)

        self.assertEqual(len(rois), 1)
        self.assertGreater(rois[0].score, 0.0)


class TestDetectRoisIntensityFiltering(unittest.TestCase):
    def setUp(self) -> None:
        self.weak_center = (25.0, 25.0)
        self.strong_center = (75.0, 75.0)
        self.image = _disks_image(
            100,
            [(*self.weak_center, 8.0)],
            background=100.0,
            peak=400.0,
        )
        # Stamp the strong disk on top at full peak so the two disks have
        # distinctly different filtered heights.
        strong_only = _disks_image(100, [(*self.strong_center, 8.0)], background=100.0, peak=900.0)
        strong_mask = strong_only > 100.0
        self.image[strong_mask] = strong_only[strong_mask]

    def test_both_disks_found_without_any_intensity_bound(self) -> None:
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)
        rois = detect_rois(self.image, settings)
        self.assertEqual(len(rois), 2)

    def test_intensity_min_excludes_the_weaker_disk(self) -> None:
        valid_mask = np.ones((100, 100), dtype=bool)
        filtered, _ = _masked_gaussian_filter(self.image, valid_mask, sigma=max(8.0 / 2.5, 1.0))
        weak_value = float(filtered[int(self.weak_center[1]), int(self.weak_center[0])])
        strong_value = float(filtered[int(self.strong_center[1]), int(self.strong_center[0])])
        midpoint = (weak_value + strong_value) / 2.0

        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0, intensity_min_value=midpoint)
        rois = detect_rois(self.image, settings)

        self.assertEqual(len(rois), 1)
        self.assertLess(hypot(rois[0].center_x - self.strong_center[0], rois[0].center_y - self.strong_center[1]), 3.0)


class TestDetectRoisIgnoreMask(unittest.TestCase):
    def test_external_mask_only_applies_when_ignore_marked_pixels_is_true(self) -> None:
        # This pins down a real (and non-obvious) behavior in
        # ignored_pixel_mask(): the external_mask argument is silently
        # dropped whenever settings.ignore_marked_pixels is False, regardless
        # of what the caller passes in.
        weak_center = (25.0, 25.0)
        strong_center = (75.0, 75.0)
        image = _disks_image(100, [(*weak_center, 8.0), (*strong_center, 8.0)])
        mask_covering_strong_disk = np.zeros((100, 100), dtype=bool)
        mask_covering_strong_disk[60:90, 60:90] = True

        settings_ignoring_off = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0, ignore_marked_pixels=False)
        rois_mask_ignored = detect_rois(image, settings_ignoring_off, external_mask=mask_covering_strong_disk)
        self.assertEqual(len(rois_mask_ignored), 2)

        settings_ignoring_on = replace(settings_ignoring_off, ignore_marked_pixels=True)
        rois_mask_applied = detect_rois(image, settings_ignoring_on, external_mask=mask_covering_strong_disk)
        self.assertEqual(len(rois_mask_applied), 1)
        self.assertLess(hypot(rois_mask_applied[0].center_x - weak_center[0], rois_mask_applied[0].center_y - weak_center[1]), 3.0)


class TestIgnoredPixelMaskRotationFill(unittest.TestCase):
    """rotation_fill_mask (unlike external_mask) is not gated behind
    ignore_marked_pixels: rotation-added padding never had a real measurement,
    so there's no toggle state where counting it is correct - see the
    docstring on ignored_pixel_mask.
    """

    def test_rotation_fill_mask_applies_even_when_ignore_marked_pixels_is_false(self) -> None:
        image = np.zeros((10, 10), dtype=np.float32)
        rotation_fill_mask = np.zeros((10, 10), dtype=bool)
        rotation_fill_mask[0:3, 0:3] = True
        settings = AreaRoiDetectionSettings(ignore_marked_pixels=False)

        result = ignored_pixel_mask(image, settings, rotation_fill_mask=rotation_fill_mask)

        np.testing.assert_array_equal(result, rotation_fill_mask)

    def test_rotation_fill_mask_and_external_mask_combine_when_ignoring_is_on(self) -> None:
        image = np.zeros((10, 10), dtype=np.float32)
        rotation_fill_mask = np.zeros((10, 10), dtype=bool)
        rotation_fill_mask[0:3, 0:3] = True
        external_mask = np.zeros((10, 10), dtype=bool)
        external_mask[7:10, 7:10] = True
        settings = AreaRoiDetectionSettings(ignore_marked_pixels=True)

        result = ignored_pixel_mask(image, settings, external_mask=external_mask, rotation_fill_mask=rotation_fill_mask)

        np.testing.assert_array_equal(result, rotation_fill_mask | external_mask)

    def test_detect_rois_never_places_a_center_inside_the_rotation_fill_mask(self) -> None:
        # A disk straddling the fill boundary: without exclusion, detection
        # could anchor on the flat, high-contrast fill/data edge itself.
        image = _disks_image(60, [(15.0, 30.0, 8.0)])
        rotation_fill_mask = np.zeros((60, 60), dtype=bool)
        rotation_fill_mask[:, :20] = True  # covers the disk's true location
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)

        rois = detect_rois(image, settings, rotation_fill_mask=rotation_fill_mask)

        for roi in rois:
            self.assertFalse(bool(rotation_fill_mask[int(roi.center_y), int(roi.center_x)]))


class TestDetectRoisGridFit(unittest.TestCase):
    def test_two_by_two_array_settings_fit_all_four_grid_positions(self) -> None:
        expected_positions = [(30.0, 30.0), (70.0, 30.0), (30.0, 70.0), (70.0, 70.0)]
        image = _disks_image(100, [(*pos, 6.0) for pos in expected_positions])
        settings = AreaRoiDetectionSettings(
            mode="bright",
            sample_radius_px=6.0,
            array_rows=2,
            array_cols=2,
            array_spacing_px=40,
        )

        rois = detect_rois(image, settings)

        self.assertEqual(len(rois), 4)
        for roi in rois:
            self.assertLess(_nearest_distance((roi.center_x, roi.center_y), expected_positions), 4.0)
        for expected in expected_positions:
            found = [(roi.center_x, roi.center_y) for roi in rois]
            self.assertLess(_nearest_distance(expected, found), 4.0)


class TestRefineDetectedRoi(unittest.TestCase):
    def setUp(self) -> None:
        self.image = _disks_image(80, [(40.0, 40.0, 8.0)])
        self.valid_mask = np.ones((80, 80), dtype=bool)
        self.filtered, _ = _masked_gaussian_filter(self.image, self.valid_mask, sigma=max(8.0 / 2.5, 1.0))

    def test_offset_seed_is_pulled_back_toward_the_true_center(self) -> None:
        seed = AreaRoi(area_roi_id=5, center_x=44.0, center_y=35.0, sample_radius_px=8.0, score=0.0)

        refined = _refine_detected_roi(seed, filtered=self.filtered, valid_mask=self.valid_mask, radius=8.0, mode="bright")

        self.assertLess(hypot(refined.center_x - 40.0, refined.center_y - 40.0), 3.0)
        self.assertGreater(refined.score, 0.0)
        self.assertEqual(refined.area_roi_id, 5)

    def test_no_signal_in_search_region_keeps_original_position(self) -> None:
        empty_mask = np.zeros((80, 80), dtype=bool)
        seed = AreaRoi(area_roi_id=2, center_x=10.0, center_y=10.0, sample_radius_px=8.0, score=0.0, inferred=True)

        refined = _refine_detected_roi(seed, filtered=self.filtered, valid_mask=empty_mask, radius=8.0, mode="bright")

        self.assertEqual((refined.center_x, refined.center_y), (10.0, 10.0))
        self.assertEqual(refined.score, 0.0)
        # inferred stays True: it only carries forward an already-True flag
        # when no signal is found, it does not get set True from scratch.
        self.assertTrue(refined.inferred)

    def test_no_signal_does_not_retroactively_mark_a_confirmed_roi_as_inferred(self) -> None:
        empty_mask = np.zeros((80, 80), dtype=bool)
        seed = AreaRoi(area_roi_id=3, center_x=10.0, center_y=10.0, sample_radius_px=8.0, inferred=False)

        refined = _refine_detected_roi(seed, filtered=self.filtered, valid_mask=empty_mask, radius=8.0, mode="bright")

        self.assertFalse(refined.inferred)


class TestRoiCircularContrastScore(unittest.TestCase):
    def _step_filtered(self, size: int, center: tuple[float, float], radius: float) -> tuple[np.ndarray, np.ndarray]:
        # Build a "filtered" array that is exactly disk_value inside the
        # function's own inner-disk threshold and exactly ring_value inside
        # its own ring band, so the expected mean is exact, not approximate.
        inner_radius = max(radius * 0.85, 1.0)
        outer_radius = max(radius * 1.65, inner_radius + 1.0)
        yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
        distance = np.hypot(xx - center[0], yy - center[1])
        filtered = np.zeros((size, size), dtype=np.float32)
        filtered[distance <= inner_radius] = 500.0
        filtered[(distance >= outer_radius * 0.72) & (distance <= outer_radius)] = 100.0
        valid_mask = np.ones((size, size), dtype=bool)
        return filtered, valid_mask

    def test_bright_mode_score_is_disk_mean_minus_ring_mean(self) -> None:
        filtered, valid_mask = self._step_filtered(60, (30.0, 30.0), 10.0)
        score = _roi_circular_contrast_score(
            filtered=filtered, valid_mask=valid_mask, center_x=30.0, center_y=30.0, radius=10.0, mode="bright"
        )
        self.assertAlmostEqual(score, 400.0, places=3)

    def test_dark_mode_score_is_ring_mean_minus_disk_mean(self) -> None:
        filtered, valid_mask = self._step_filtered(60, (30.0, 30.0), 10.0)
        score = _roi_circular_contrast_score(
            filtered=filtered, valid_mask=valid_mask, center_x=30.0, center_y=30.0, radius=10.0, mode="dark"
        )
        self.assertAlmostEqual(score, -400.0, places=3)

    def test_no_valid_pixels_scores_zero(self) -> None:
        filtered, _ = self._step_filtered(60, (30.0, 30.0), 10.0)
        empty_mask = np.zeros((60, 60), dtype=bool)
        score = _roi_circular_contrast_score(
            filtered=filtered, valid_mask=empty_mask, center_x=30.0, center_y=30.0, radius=10.0, mode="bright"
        )
        self.assertEqual(score, 0.0)


class TestEstimateGridOrigin1d(unittest.TestCase):
    def test_values_exactly_on_grid_recover_the_true_origin(self) -> None:
        origin = _estimate_grid_origin_1d(np.array([10.0, 30.0, 50.0]), spacing=20.0, count=3, image_size=100.0)
        self.assertAlmostEqual(origin, 10.0, places=6)

    def test_noisy_values_still_recover_an_origin_close_to_the_true_one(self) -> None:
        noisy = np.array([11.4, 31.8, 48.6])
        origin = _estimate_grid_origin_1d(noisy, spacing=20.0, count=3, image_size=100.0)
        self.assertLess(abs(origin - 10.0), 3.0)

    def test_empty_values_returns_zero(self) -> None:
        self.assertEqual(_estimate_grid_origin_1d(np.array([]), spacing=20.0, count=3, image_size=100.0), 0.0)

    def test_non_positive_spacing_or_count_returns_zero(self) -> None:
        values = np.array([10.0, 30.0])
        self.assertEqual(_estimate_grid_origin_1d(values, spacing=0.0, count=3, image_size=100.0), 0.0)
        self.assertEqual(_estimate_grid_origin_1d(values, spacing=20.0, count=0, image_size=100.0), 0.0)


if __name__ == "__main__":
    unittest.main()
