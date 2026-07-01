from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_app.domain.processing import centroid_from_curve


def _gaussian(x: np.ndarray, center: float, sigma: float) -> np.ndarray:
    return np.exp(-0.5 * ((x - center) / sigma) ** 2)


class TestCentroidLegacy(unittest.TestCase):
    """Behaviour with threshold_fraction=None (legacy, uses local minimum as reference)."""

    def test_symmetric_peak_returns_center(self) -> None:
        x = np.linspace(500.0, 700.0, 201)
        y = _gaussian(x, center=600.0, sigma=10.0)
        result = centroid_from_curve(x, y, threshold_fraction=None)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 600.0, places=2)

    def test_too_few_points_returns_none(self) -> None:
        self.assertIsNone(centroid_from_curve(np.array([500.0, 600.0]), np.array([0.5, 1.0])))

    def test_all_equal_values_returns_none(self) -> None:
        x = np.linspace(500.0, 700.0, 50)
        y = np.ones(50)
        # all weights become zero after subtracting min → total == 0
        result = centroid_from_curve(x, y, threshold_fraction=None)
        self.assertIsNone(result)


class TestCentroidThresholdCorrected(unittest.TestCase):
    """Behaviour with threshold_fraction provided — baseline-corrected centroid."""

    def test_symmetric_peak_still_returns_center(self) -> None:
        x = np.linspace(500.0, 700.0, 201)
        y = _gaussian(x, center=600.0, sigma=10.0)
        result = centroid_from_curve(x, y, threshold_fraction=0.7)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 600.0, places=2)

    def test_fraction_zero_same_as_legacy(self) -> None:
        """threshold_fraction=0 should give the same result as threshold_fraction=None."""
        x = np.linspace(500.0, 700.0, 201)
        y = _gaussian(x, center=600.0, sigma=15.0) + 0.1
        legacy = centroid_from_curve(x, y, threshold_fraction=None)
        zero_fraction = centroid_from_curve(x, y, threshold_fraction=0.0)
        self.assertIsNotNone(legacy)
        self.assertIsNotNone(zero_fraction)
        self.assertAlmostEqual(legacy, zero_fraction, places=6)

    def test_high_fraction_suppresses_skirt_bias(self) -> None:
        """With an asymmetric baseline, a high threshold fraction should place the
        centroid closer to the true peak than the legacy (fraction=0) centroid."""
        x = np.linspace(500.0, 700.0, 401)
        peak = _gaussian(x, center=600.0, sigma=8.0)
        # add a sloping baseline — biases legacy centroid toward the high-baseline side
        skewed_baseline = 0.05 * (x - 500.0) / 200.0
        y = peak + skewed_baseline

        centroid_legacy = centroid_from_curve(x, y, threshold_fraction=None)
        centroid_corrected = centroid_from_curve(x, y, threshold_fraction=0.7)

        self.assertIsNotNone(centroid_legacy)
        self.assertIsNotNone(centroid_corrected)
        # corrected centroid should be closer to the true peak at 600 nm
        self.assertLess(
            abs(centroid_corrected - 600.0),
            abs(centroid_legacy - 600.0),
            msg=f"corrected={centroid_corrected:.4f} should be closer to 600 than legacy={centroid_legacy:.4f}",
        )

    def test_fraction_one_returns_none_for_flat_peak(self) -> None:
        """A fraction of 1.0 sets reference == max, so all weights are zero → None."""
        x = np.linspace(500.0, 700.0, 50)
        y = _gaussian(x, center=600.0, sigma=10.0)
        result = centroid_from_curve(x, y, threshold_fraction=1.0)
        self.assertIsNone(result)

    def test_fraction_clipped_to_range(self) -> None:
        """Fractions outside [0, 1] are clipped — should not raise."""
        x = np.linspace(500.0, 700.0, 101)
        y = _gaussian(x, center=600.0, sigma=10.0)
        # fraction > 1 clips to 1 → same as test_fraction_one
        result_high = centroid_from_curve(x, y, threshold_fraction=1.5)
        self.assertIsNone(result_high)
        # fraction < 0 clips to 0 → same as legacy
        result_low = centroid_from_curve(x, y, threshold_fraction=-0.5)
        self.assertIsNotNone(result_low)
        self.assertAlmostEqual(result_low, 600.0, places=2)

    def test_nan_values_handled(self) -> None:
        x = np.linspace(500.0, 700.0, 101)
        y = _gaussian(x, center=600.0, sigma=10.0)
        y[30:35] = np.nan
        result = centroid_from_curve(x, y, threshold_fraction=0.5)
        self.assertIsNotNone(result)
        self.assertTrue(math.isfinite(result))


if __name__ == "__main__":
    unittest.main()
