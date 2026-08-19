from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.processing.roi_math import (
    reduce_mean,
    reduce_median,
    reduce_plane_fit_reference,
    reduce_sample_and_reference,
    reduce_trimmed_mean,
)


class TestReduceMean(unittest.TestCase):
    def test_plain_average(self) -> None:
        self.assertAlmostEqual(reduce_mean(np.array([1.0, 2.0, 3.0, 4.0])), 2.5, places=9)


class TestReduceMedian(unittest.TestCase):
    def test_odd_count(self) -> None:
        self.assertAlmostEqual(reduce_median(np.array([5.0, 1.0, 3.0])), 3.0, places=9)

    def test_even_count_averages_middle_two(self) -> None:
        self.assertAlmostEqual(reduce_median(np.array([1.0, 2.0, 3.0, 4.0])), 2.5, places=9)

    def test_robust_to_single_outlier(self) -> None:
        # A single very bright pixel (e.g. a cosmic-ray hit) barely moves the
        # median but would swing the mean substantially.
        pixels = np.array([10.0, 10.0, 10.0, 10.0, 10000.0])
        self.assertAlmostEqual(reduce_median(pixels), 10.0, places=9)
        self.assertGreater(reduce_mean(pixels), 100.0)


class TestReduceTrimmedMean(unittest.TestCase):
    def test_matches_plain_mean_at_zero_trim(self) -> None:
        pixels = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertAlmostEqual(reduce_trimmed_mean(pixels, 0.0), reduce_mean(pixels), places=9)

    def test_drops_top_and_bottom_tail(self) -> None:
        # 10 points, 10% trim drops 1 from each tail -> mean of the middle 8.
        pixels = np.arange(1.0, 11.0)
        result = reduce_trimmed_mean(pixels, 0.10)
        self.assertAlmostEqual(result, np.mean(pixels[1:-1]), places=9)

    def test_falls_back_to_mean_when_trim_leaves_nothing(self) -> None:
        pixels = np.array([1.0, 2.0, 3.0])
        result = reduce_trimmed_mean(pixels, 0.45)
        self.assertAlmostEqual(result, reduce_mean(pixels), places=9)

    def test_empty_array_falls_back_to_mean_without_raising(self) -> None:
        result = reduce_trimmed_mean(np.array([]), 0.10)
        self.assertTrue(np.isnan(result))


class TestReducePlaneFitReference(unittest.TestCase):
    def test_recovers_exact_plane(self) -> None:
        # z = 2x + 3y + 10 sampled on a small grid - a plane fit should
        # recover this exactly (no noise) and extrapolate correctly to a
        # point outside the sampled region.
        xx, yy = np.meshgrid(np.arange(0.0, 10.0), np.arange(0.0, 10.0))
        xx = xx.ravel()
        yy = yy.ravel()
        values = 2.0 * xx + 3.0 * yy + 10.0
        sample_x, sample_y = 15.0, -3.0
        expected = 2.0 * sample_x + 3.0 * sample_y + 10.0
        result = reduce_plane_fit_reference(values, xx, yy, sample_x, sample_y)
        self.assertAlmostEqual(result, expected, places=6)

    def test_recovers_approximate_plane_with_noise(self) -> None:
        rng = np.random.default_rng(0)
        xx, yy = np.meshgrid(np.arange(0.0, 20.0), np.arange(0.0, 20.0))
        xx = xx.ravel()
        yy = yy.ravel()
        values = 1.5 * xx - 0.5 * yy + 5.0 + rng.normal(scale=0.05, size=xx.size)
        sample_x, sample_y = 10.0, 10.0
        expected = 1.5 * sample_x - 0.5 * sample_y + 5.0
        result = reduce_plane_fit_reference(values, xx, yy, sample_x, sample_y)
        self.assertAlmostEqual(result, expected, places=1)

    def test_falls_back_to_mean_with_too_few_points(self) -> None:
        values = np.array([1.0, 2.0, 3.0])
        xx = np.array([0.0, 1.0, 2.0])
        yy = np.array([0.0, 0.0, 0.0])
        result = reduce_plane_fit_reference(values, xx, yy, sample_x=5.0, sample_y=5.0)
        self.assertAlmostEqual(result, reduce_mean(values), places=9)

    def test_falls_back_to_mean_with_collinear_points(self) -> None:
        # All points share y=0 (and evenly spaced x) - a plane z=ax+by+c is
        # underdetermined in y from this data alone (singular design matrix).
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        xx = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        yy = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        result = reduce_plane_fit_reference(values, xx, yy, sample_x=10.0, sample_y=10.0)
        self.assertAlmostEqual(result, reduce_mean(values), places=9)


class TestReduceSampleAndReference(unittest.TestCase):
    def _pixels(self):
        sample = np.array([10.0, 12.0, 11.0, 13.0])
        reference = np.array([100.0, 102.0, 101.0, 500.0])  # one outlier
        return sample, reference

    def test_mean_dispatch(self) -> None:
        sample, reference = self._pixels()
        sm, rm = reduce_sample_and_reference(sample, reference, "mean")
        self.assertAlmostEqual(sm, reduce_mean(sample), places=9)
        self.assertAlmostEqual(rm, reduce_mean(reference), places=9)

    def test_median_dispatch(self) -> None:
        sample, reference = self._pixels()
        sm, rm = reduce_sample_and_reference(sample, reference, "median")
        self.assertAlmostEqual(sm, reduce_median(sample), places=9)
        self.assertAlmostEqual(rm, reduce_median(reference), places=9)

    def test_trimmed_mean_dispatch(self) -> None:
        sample, reference = self._pixels()
        sm, rm = reduce_sample_and_reference(sample, reference, "trimmed_mean", trimmed_mean_fraction=0.25)
        self.assertAlmostEqual(sm, reduce_trimmed_mean(sample, 0.25), places=9)
        self.assertAlmostEqual(rm, reduce_trimmed_mean(reference, 0.25), places=9)

    def test_plane_fit_dispatch_uses_sample_mean_and_reference_plane(self) -> None:
        sample, _ = self._pixels()
        xx, yy = np.meshgrid(np.arange(0.0, 6.0), np.arange(0.0, 6.0))
        xx = xx.ravel()
        yy = yy.ravel()
        reference = 4.0 * xx + 1.0 * yy + 50.0
        sample_x, sample_y = 2.0, 2.0
        sm, rm = reduce_sample_and_reference(
            sample,
            reference,
            "plane_fit",
            reference_xx=xx,
            reference_yy=yy,
            sample_x=sample_x,
            sample_y=sample_y,
        )
        self.assertAlmostEqual(sm, reduce_mean(sample), places=9)
        self.assertAlmostEqual(rm, 4.0 * sample_x + 1.0 * sample_y + 50.0, places=6)

    def test_plane_fit_without_coordinates_raises(self) -> None:
        sample, reference = self._pixels()
        with self.assertRaises(ValueError):
            reduce_sample_and_reference(sample, reference, "plane_fit")

    def test_unknown_method_falls_back_to_mean(self) -> None:
        sample, reference = self._pixels()
        sm, rm = reduce_sample_and_reference(sample, reference, "not_a_real_method")
        self.assertAlmostEqual(sm, reduce_mean(sample), places=9)
        self.assertAlmostEqual(rm, reduce_mean(reference), places=9)


if __name__ == "__main__":
    unittest.main()
