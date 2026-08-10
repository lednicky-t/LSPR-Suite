from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings
from lspr_imaging_app.processing.chromatic import (
    _choose_trend_consistent_position,
    _landmark_regions,
    _predict_trend_position,
    _recent_step_scale,
    auto_track_landmarks_over_wavelengths,
    default_landmark_anchors,
    detect_regional_spot_landmarks,
    track_spot_landmarks,
)

_IMAGE_SIZE = 200
_ANCHOR_X, _ANCHOR_Y = default_landmark_anchors((_IMAGE_SIZE, _IMAGE_SIZE), 1)[1]


def _blob_image(center_x: float, center_y: float, *, size: int = 200, background: float = 50000.0, spot_value: float = 2000.0, radius: float = 10.0) -> np.ndarray:
    image = np.full((size, size), background, dtype=np.float32)
    yy, xx = np.indices((size, size), dtype=np.float32)
    mask = (xx - center_x) ** 2 + (yy - center_y) ** 2 <= radius**2
    image[mask] = spot_value
    return image


class TestPredictTrendPosition(unittest.TestCase):
    def test_none_with_fewer_than_two_points(self) -> None:
        self.assertIsNone(_predict_trend_position([(470.0, 10.0, 10.0)], 480.0))

    def test_linear_extrapolation_continues_constant_velocity(self) -> None:
        history = [(470.0, 10.0, 20.0), (480.0, 11.0, 22.0)]
        predicted = _predict_trend_position(history, 490.0)
        self.assertIsNotNone(predicted)
        self.assertAlmostEqual(predicted[0], 12.0, places=6)
        self.assertAlmostEqual(predicted[1], 24.0, places=6)

    def test_never_diverges_when_fed_its_own_output_repeatedly(self) -> None:
        # Simulates the real failure mode: every raw match gets rejected, so
        # the trend keeps extrapolating from its own previous predictions.
        # A naive quadratic fit re-applied to its own output can blow up;
        # linear extrapolation plus the hard clamp must stay bounded.
        history = [(470.0, 0.0, 0.0), (480.0, 1.0, 1.0)]
        wavelength = 490.0
        for _ in range(30):
            predicted = _predict_trend_position(history, wavelength)
            self.assertIsNotNone(predicted)
            self.assertTrue(all(np.isfinite(value) for value in predicted))
            history.append((wavelength, predicted[0], predicted[1]))
            wavelength += 10.0
        # A straight-line walk at ~1.4px/step for 30 steps is at most ~45px;
        # nowhere near the hundreds/thousands of px a divergence would produce.
        self.assertLess(float(np.hypot(*history[-1][1:])), 100.0)


class TestRecentStepScale(unittest.TestCase):
    def test_infinite_with_fewer_than_two_points(self) -> None:
        self.assertEqual(_recent_step_scale([(470.0, 0.0, 0.0)]), float("inf"))

    def test_median_of_recent_steps(self) -> None:
        history = [(470.0, 0.0, 0.0), (480.0, 1.0, 0.0), (490.0, 3.0, 0.0), (500.0, 6.0, 0.0)]
        # steps: 1, 2, 3 -> median of last 3 = 2
        self.assertAlmostEqual(_recent_step_scale(history), 2.0, places=6)


class TestChooseTrendConsistentPosition(unittest.TestCase):
    def test_no_candidates_falls_back_to_prediction_then_history(self) -> None:
        history = [(470.0, 5.0, 5.0), (480.0, 6.0, 6.0)]
        predicted = (7.0, 7.0)
        self.assertEqual(_choose_trend_consistent_position([], predicted, history), predicted)
        self.assertEqual(_choose_trend_consistent_position([], None, history), (6.0, 6.0))

    def test_no_trend_yet_averages_multiple_candidates(self) -> None:
        history = [(470.0, 0.0, 0.0)]
        chosen = _choose_trend_consistent_position([(10.0, 10.0), (20.0, 20.0)], None, history)
        self.assertAlmostEqual(chosen[0], 15.0, places=6)
        self.assertAlmostEqual(chosen[1], 15.0, places=6)

    def test_candidate_close_to_trend_is_accepted(self) -> None:
        history = [(470.0, 0.0, 0.0), (480.0, 1.0, 1.0), (490.0, 2.0, 2.0)]
        predicted = (3.0, 3.0)
        candidate = (3.2, 2.9)
        self.assertEqual(_choose_trend_consistent_position([candidate], predicted, history), candidate)

    def test_outlier_candidate_is_rejected_in_favor_of_prediction(self) -> None:
        history = [(470.0, 0.0, 0.0), (480.0, 1.0, 1.0), (490.0, 2.0, 2.0)]
        predicted = (3.0, 3.0)
        outlier = (150.0, -80.0)
        self.assertEqual(_choose_trend_consistent_position([outlier], predicted, history), predicted)

    def test_picks_the_candidate_closest_to_trend(self) -> None:
        history = [(470.0, 0.0, 0.0), (480.0, 1.0, 1.0), (490.0, 2.0, 2.0)]
        predicted = (3.0, 3.0)
        close = (3.1, 3.1)
        far_but_within_tolerance = (4.5, 4.5)
        self.assertEqual(
            _choose_trend_consistent_position([far_but_within_tolerance, close], predicted, history),
            close,
        )


class TestSpotLandmarks(unittest.TestCase):
    def test_detect_regional_spot_landmarks_finds_the_blob(self) -> None:
        # Place the blob a few px off the actual anchor position (rather than
        # guessing/hardcoding where the anchor lands for feature_count=1), so
        # this exercises the "found signal nearby" refinement, not a coincidence.
        blob_x, blob_y = _ANCHOR_X + 3.0, _ANCHOR_Y + 2.0
        image = _blob_image(blob_x, blob_y)
        detected = detect_regional_spot_landmarks(image, 1, spot_radius_px=10.0, spot_mode="dark")
        self.assertEqual(len(detected), 1)
        x, y = next(iter(detected.values()))
        self.assertLess(float(np.hypot(x - blob_x, y - blob_y)), 5.0)

    def test_track_spot_landmarks_follows_a_shifted_blob(self) -> None:
        reference = _blob_image(100.0, 80.0)
        target = _blob_image(103.0, 81.0)
        tracked = track_spot_landmarks(
            reference, target, {1: (100.0, 80.0)}, spot_radius_px=10.0, spot_mode="dark", search_radius_px=12,
        )
        x, y = tracked[1]
        self.assertLess(float(np.hypot(x - 103.0, y - 81.0)), 3.0)


class TestSpotLandmarksWithAreaRoiSettings(unittest.TestCase):
    def test_detect_regional_spot_landmarks_finds_a_real_particle_within_its_region(self) -> None:
        # Place a blob within the feature_count=1 anchor's own region, but
        # away from the anchor's literal (x, y) -- the full array-aware
        # detector should still land precisely on it via the region match.
        region_x0, region_x1, region_y0, region_y1 = _landmark_regions((_IMAGE_SIZE, _IMAGE_SIZE), 1)[1]
        blob_x = region_x0 + (region_x1 - region_x0) * 0.8
        blob_y = region_y0 + (region_y1 - region_y0) * 0.8
        image = _blob_image(blob_x, blob_y)
        settings = AreaRoiDetectionSettings(mode="dark", sample_radius_px=10.0)
        detected = detect_regional_spot_landmarks(
            image, 1, spot_radius_px=10.0, spot_mode="dark", area_roi_settings=settings
        )
        x, y = next(iter(detected.values()))
        self.assertLess(float(np.hypot(x - blob_x, y - blob_y)), 3.0)

    def test_detect_regional_spot_landmarks_does_not_reach_into_another_regions_particle(self) -> None:
        # A real particle exists, but far outside the feature_count=1
        # anchor's own (small) region -- it must NOT be claimed; staying
        # spread out (or, with nothing in its own region, falling back to a
        # region-confined local search) matters more than every point
        # landing on *a* real particle regardless of where.
        region_x0, region_x1, region_y0, region_y1 = _landmark_regions((_IMAGE_SIZE, _IMAGE_SIZE), 1)[1]
        blob_x, blob_y = 150.0, 150.0
        self.assertFalse(region_x0 <= blob_x < region_x1 and region_y0 <= blob_y < region_y1)
        image = _blob_image(blob_x, blob_y)
        settings = AreaRoiDetectionSettings(mode="dark", sample_radius_px=10.0)
        detected = detect_regional_spot_landmarks(
            image, 1, spot_radius_px=10.0, spot_mode="dark", area_roi_settings=settings
        )
        x, y = next(iter(detected.values()))
        self.assertGreater(float(np.hypot(x - blob_x, y - blob_y)), 20.0)
        self.assertTrue(region_x0 <= x < region_x1 and region_y0 <= y < region_y1)

    def test_track_spot_landmarks_uses_full_detector_when_settings_given(self) -> None:
        reference = _blob_image(100.0, 80.0)
        target = _blob_image(103.0, 81.0)
        settings = AreaRoiDetectionSettings(mode="dark", sample_radius_px=10.0)
        tracked = track_spot_landmarks(
            reference,
            target,
            {1: (100.0, 80.0)},
            spot_radius_px=10.0,
            spot_mode="dark",
            search_radius_px=12,
            area_roi_settings=settings,
        )
        x, y = tracked[1]
        self.assertLess(float(np.hypot(x - 103.0, y - 81.0)), 3.0)

    def test_track_spot_landmarks_picks_the_nearer_of_two_real_spots(self) -> None:
        # Two real particles exist in the target frame; the tracker must pick
        # the one nearer the predicted position, not just whichever
        # detect_spots happens to return first.
        reference = _blob_image(100.0, 80.0)
        target = np.full((200, 200), 50000.0, dtype=np.float32)
        yy, xx = np.indices((200, 200), dtype=np.float32)
        target[(xx - 103.0) ** 2 + (yy - 81.0) ** 2 <= 10.0**2] = 2000.0
        target[(xx - 160.0) ** 2 + (yy - 150.0) ** 2 <= 10.0**2] = 2000.0
        settings = AreaRoiDetectionSettings(mode="dark", sample_radius_px=10.0)
        tracked = track_spot_landmarks(
            reference,
            target,
            {1: (100.0, 80.0)},
            spot_radius_px=10.0,
            spot_mode="dark",
            search_radius_px=12,
            area_roi_settings=settings,
        )
        x, y = tracked[1]
        self.assertLess(float(np.hypot(x - 103.0, y - 81.0)), 3.0)


class TestSpotLandmarkRegionSpread(unittest.TestCase):
    def test_landmarks_stay_in_their_own_region_even_when_spots_cluster_unevenly(self) -> None:
        # One region gets many densely-packed real particles (simulating
        # e.g. a crop/background-heavy area where most of the real signal
        # happens to sit), every other region gets exactly one. A plain
        # "nearest detected spot" match with no region boundary would let
        # the dense region's cluster greedily absorb landmarks whose sector
        # is actually elsewhere in the frame -- this is the exact bug
        # reported: landmarks all collapsing into one area instead of
        # spreading out to sample the aberration across the whole image.
        size = 300
        regions = _landmark_regions((size, size), 5)
        image = np.full((size, size), 50000.0, dtype=np.float32)
        yy, xx = np.indices((size, size), dtype=np.float32)
        rng = np.random.default_rng(0)
        dense_region_id = next(iter(regions))
        single_spot_positions: dict[int, tuple[float, float]] = {}
        for feature_id, (x0, x1, y0, y1) in regions.items():
            margin = 8.0
            lo_x, hi_x = x0 + margin, max(x1 - margin, x0 + margin + 1)
            lo_y, hi_y = y0 + margin, max(y1 - margin, y0 + margin + 1)
            if feature_id == dense_region_id:
                for _ in range(30):
                    cx, cy = rng.uniform(lo_x, hi_x), rng.uniform(lo_y, hi_y)
                    image[(xx - cx) ** 2 + (yy - cy) ** 2 <= 6.0**2] = 2000.0
            else:
                cx = min(max(lo_x, x0 + 5.0), hi_x)
                cy = min(max(lo_y, y0 + 5.0), hi_y)
                image[(xx - cx) ** 2 + (yy - cy) ** 2 <= 6.0**2] = 2000.0
                single_spot_positions[feature_id] = (cx, cy)

        settings = AreaRoiDetectionSettings(mode="dark", sample_radius_px=6.0)
        detected = detect_regional_spot_landmarks(
            image, 5, spot_radius_px=6.0, spot_mode="dark", area_roi_settings=settings
        )
        self.assertEqual(len(detected), 5)
        for feature_id, (x, y) in detected.items():
            x0, x1, y0, y1 = regions[feature_id]
            self.assertTrue(
                x0 <= x < x1 and y0 <= y < y1,
                f"feature {feature_id} at ({x:.1f},{y:.1f}) landed outside its own region {regions[feature_id]}",
            )
        # Every single-spot region's landmark must be that specific spot,
        # not something dragged in from the dense region.
        for feature_id, (expected_x, expected_y) in single_spot_positions.items():
            x, y = detected[feature_id]
            self.assertLess(float(np.hypot(x - expected_x, y - expected_y)), 3.0)


class TestAutoTrackLandmarksOverWavelengths(unittest.TestCase):
    def test_centroid_tracking_follows_a_smoothly_drifting_blob(self) -> None:
        # The blob's wavelength-0 position must be reachable from the
        # feature_count=1 anchor (see detect_regional_spot_landmarks), since
        # that anchor is where the first frame's detection search starts from.
        wavelengths = [470.0 + 10.0 * index for index in range(10)]
        images = [
            (wl, _blob_image(_ANCHOR_X + 2.0 + 0.5 * index, _ANCHOR_Y + 2.0 + 0.3 * index))
            for index, wl in enumerate(wavelengths)
        ]
        trajectories = auto_track_landmarks_over_wavelengths(images, 1, kind="centroid", spot_radius_px=10.0, spot_mode="dark")
        self.assertEqual(len(trajectories), 1)
        traj = trajectories[1]
        for index, wl in enumerate(wavelengths):
            expected_x, expected_y = _ANCHOR_X + 2.0 + 0.5 * index, _ANCHOR_Y + 2.0 + 0.3 * index
            x, y = traj[wl]
            self.assertLess(float(np.hypot(x - expected_x, y - expected_y)), 3.0)

    def test_a_decoy_blob_does_not_permanently_derail_tracking(self) -> None:
        # The true blob drifts smoothly. One frame in the middle also gets a
        # bright, easy-to-match decoy blob planted far away, simulating a
        # mis-track (e.g. onto a neighboring particle). Trend-consistency
        # rejection should keep the final trajectory near the true path
        # instead of permanently jumping to and following the decoy.
        wavelengths = [470.0 + 10.0 * index for index in range(10)]
        images = []
        for index, wl in enumerate(wavelengths):
            true_x, true_y = _ANCHOR_X + 2.0 + 0.5 * index, _ANCHOR_Y + 2.0 + 0.3 * index
            image = _blob_image(true_x, true_y)
            if index == 4:
                yy, xx = np.indices(image.shape, dtype=np.float32)
                decoy_mask = (xx - 160.0) ** 2 + (yy - 150.0) ** 2 <= 10.0**2
                image[decoy_mask] = 500.0
            images.append((wl, image))
        trajectories = auto_track_landmarks_over_wavelengths(images, 1, kind="centroid", spot_radius_px=10.0, spot_mode="dark")
        final_x, final_y = trajectories[1][wavelengths[-1]]
        true_final_x, true_final_y = _ANCHOR_X + 2.0 + 0.5 * 9, _ANCHOR_Y + 2.0 + 0.3 * 9
        self.assertLess(float(np.hypot(final_x - true_final_x, final_y - true_final_y)), 15.0)


if __name__ == "__main__":
    unittest.main()
