"""Coverage for processing/roi_histogram.py - the bimodal-histogram peak
analysis that auto-locates the ROI intensity band so the histogram highlight
range can be set without the user dragging it by hand.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.processing.roi_histogram import estimate_roi_intensity_range


def _synthetic_reference_image_values(
    *,
    background_mean: float = 60000.0,
    background_std: float = 800.0,
    background_count: int = 20000,
    roi_mean: float = 35000.0,
    roi_std: float = 1000.0,
    roi_count: int = 1500,
    debris_mean: float = 5000.0,
    debris_std: float = 1500.0,
    debris_count: int = 300,
    seed: int = 0,
) -> np.ndarray:
    """A bimodal-plus-debris population mimicking a real LSPRi reference
    image: a large bright background cluster, a smaller darker ROI cluster,
    and a small very-dark debris cluster near zero."""
    rng = np.random.default_rng(seed)
    background = rng.normal(background_mean, background_std, background_count)
    roi = rng.normal(roi_mean, roi_std, roi_count)
    debris = rng.normal(debris_mean, debris_std, debris_count)
    values = np.concatenate([background, roi, debris])
    return np.clip(values, 0.0, 65535.0)


class TestEstimateRoiIntensityRange(unittest.TestCase):
    def test_clear_bimodal_histogram_brackets_the_roi_peak(self) -> None:
        values = _synthetic_reference_image_values()
        result = estimate_roi_intensity_range(values)
        self.assertIsNotNone(result)
        lower, upper = result
        self.assertLess(lower, 35000.0)
        self.assertGreater(upper, 35000.0)
        # Bounds must isolate the ROI peak: below the background cluster,
        # above the debris cluster.
        self.assertLess(upper, 55000.0)
        self.assertGreater(lower, 15000.0)

    def test_debris_cluster_is_never_mistaken_for_the_roi_peak(self) -> None:
        # A prominent debris cluster right at the edge of the exclusion band -
        # detection must still land on the real ROI peak, not the debris.
        values = _synthetic_reference_image_values(debris_count=5000, debris_mean=4000.0, debris_std=1000.0)
        lower, upper = estimate_roi_intensity_range(values)
        self.assertGreater(lower, 15000.0)

    def test_unimodal_histogram_returns_none(self) -> None:
        rng = np.random.default_rng(1)
        values = rng.normal(40000.0, 1000.0, 5000)
        self.assertIsNone(estimate_roi_intensity_range(values))

    def test_only_debris_and_background_returns_none(self) -> None:
        # No real ROI population - just background and a debris bump. Only
        # one peak survives the debris-ceiling filter, so there is nothing
        # to bracket.
        rng = np.random.default_rng(2)
        background = rng.normal(60000.0, 800.0, 20000)
        debris = rng.normal(5000.0, 1500.0, 300)
        values = np.concatenate([background, debris])
        self.assertIsNone(estimate_roi_intensity_range(values))

    def test_candidate_brighter_than_background_is_rejected(self) -> None:
        # A second peak that is *brighter* than the tallest peak violates the
        # "ROIs are never brighter than background" assumption and must not
        # be reported as the ROI band.
        rng = np.random.default_rng(3)
        background = rng.normal(40000.0, 800.0, 20000)
        brighter_population = rng.normal(60000.0, 800.0, 1500)
        values = np.concatenate([background, brighter_population])
        self.assertIsNone(estimate_roi_intensity_range(values))

    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(estimate_roi_intensity_range(np.array([])))

    def test_degenerate_intensity_bounds_return_none(self) -> None:
        values = _synthetic_reference_image_values()
        self.assertIsNone(estimate_roi_intensity_range(values, intensity_min=100.0, intensity_max=100.0))

    def test_all_nan_input_returns_none(self) -> None:
        self.assertIsNone(estimate_roi_intensity_range(np.full(50, np.nan)))


if __name__ == "__main__":
    unittest.main()
