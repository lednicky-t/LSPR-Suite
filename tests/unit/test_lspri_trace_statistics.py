from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np
from scipy.signal import savgol_filter

from lspr_imaging_app.analysis.settings import StatisticsSettings
from lspr_imaging_app.analysis.statistics import (
    aggregate_traces,
    apply_baseline_with_fit,
    apply_statistics_with_fit,
    filter_traces,
    normalize_to_baseline_window,
    reject_spikes_hampel,
    reject_spikes_running_median,
    smooth_moving_average,
    smooth_savgol,
)


class TestSmoothSavgol(unittest.TestCase):
    def test_matches_scipy_directly_on_clean_data(self) -> None:
        x = np.linspace(0.0, 10.0, 41)
        y = np.sin(x) + 0.01 * x
        result = smooth_savgol(y, window=9, polyorder=2)
        expected = savgol_filter(y, window_length=9, polyorder=2, mode="interp")
        np.testing.assert_allclose(result, expected, atol=1e-9)

    def test_window_larger_than_data_is_clamped_not_raised(self) -> None:
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = smooth_savgol(y, window=101, polyorder=2)
        self.assertEqual(result.size, y.size)
        self.assertTrue(np.all(np.isfinite(result)))

    def test_too_few_points_returns_input_unchanged(self) -> None:
        y = np.array([1.0, 2.0])
        result = smooth_savgol(y, window=9, polyorder=2)
        np.testing.assert_array_equal(result, y)

    def test_nan_gaps_are_preserved_not_invented(self) -> None:
        y = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
        result = smooth_savgol(y, window=5, polyorder=2)
        self.assertTrue(np.isnan(result[2]))
        self.assertTrue(np.all(np.isfinite(np.delete(result, 2))))


class TestSmoothMovingAverage(unittest.TestCase):
    def test_flat_signal_unchanged(self) -> None:
        y = np.full(10, 5.0)
        result = smooth_moving_average(y, window=3)
        np.testing.assert_allclose(result, y, atol=1e-9)

    def test_interior_point_is_plain_box_average(self) -> None:
        y = np.array([0.0, 0.0, 0.0, 9.0, 0.0, 0.0, 0.0])
        result = smooth_moving_average(y, window=3)
        # Middle point's 3-wide window is [0, 9, 0] -> mean 3.
        self.assertAlmostEqual(result[3], 3.0, places=9)

    def test_edges_are_not_biased_toward_zero(self) -> None:
        y = np.full(6, 4.0)
        result = smooth_moving_average(y, window=5)
        # A naive zero-padded convolution would pull edge points down from 4.0;
        # the edge-count correction should keep every point at exactly 4.0.
        np.testing.assert_allclose(result, y, atol=1e-9)

    def test_window_larger_than_data_is_clamped_not_raised(self) -> None:
        y = np.array([1.0, 2.0, 3.0])
        result = smooth_moving_average(y, window=999)
        self.assertEqual(result.size, 3)
        self.assertTrue(np.all(np.isfinite(result)))


class TestRejectSpikesHampel(unittest.TestCase):
    def test_single_spike_is_replaced(self) -> None:
        y = np.array([1.0, 1.0, 1.0, 1.0, 100.0, 1.0, 1.0, 1.0, 1.0])
        result = reject_spikes_hampel(y, window=5, threshold=3.0)
        self.assertLess(result[4], 10.0)

    def test_non_spike_points_are_left_close_to_unchanged(self) -> None:
        y = np.array([1.0, 1.1, 0.9, 1.0, 100.0, 1.0, 1.1, 0.9, 1.0])
        result = reject_spikes_hampel(y, window=5, threshold=3.0)
        for i in (0, 1, 2, 3, 5, 6, 7, 8):
            self.assertAlmostEqual(result[i], y[i], delta=0.15)

    def test_flat_signal_is_unaffected(self) -> None:
        y = np.full(9, 2.0)
        result = reject_spikes_hampel(y, window=5, threshold=3.0)
        np.testing.assert_allclose(result, y, atol=1e-9)

    def test_empty_array_does_not_raise(self) -> None:
        result = reject_spikes_hampel(np.array([]), window=5, threshold=3.0)
        self.assertEqual(result.size, 0)


class TestRejectSpikesRunningMedian(unittest.TestCase):
    def test_single_spike_is_replaced(self) -> None:
        y = np.array([1.0, 1.0, 1.0, 1.0, 100.0, 1.0, 1.0, 1.0, 1.0])
        result = reject_spikes_running_median(y, window=5)
        self.assertLess(result[4], 10.0)

    def test_matches_scipy_median_filter_directly(self) -> None:
        from scipy.ndimage import median_filter

        y = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])
        result = reject_spikes_running_median(y, window=3)
        expected = median_filter(y, size=3, mode="nearest")
        np.testing.assert_allclose(result, expected, atol=1e-9)


class TestNormalizeToBaselineWindow(unittest.TestCase):
    def test_subtracts_window_mean(self) -> None:
        x = np.linspace(0.0, 10.0, 11)
        y = x + 5.0  # constant offset of 5 on top of a ramp
        corrected, baseline = normalize_to_baseline_window(x, y, 0.0, 2.0)
        # Baseline window covers x=0,1,2 -> y=5,6,7 -> mean 6.
        self.assertAlmostEqual(baseline, 6.0, places=9)
        self.assertAlmostEqual(corrected[0], y[0] - 6.0, places=9)

    def test_recenters_window_to_zero(self) -> None:
        x = np.arange(20, dtype=np.float64)
        y = np.full(20, 3.0)
        corrected, baseline = normalize_to_baseline_window(x, y, 5.0, 10.0)
        self.assertAlmostEqual(baseline, 3.0, places=9)
        np.testing.assert_allclose(corrected, 0.0, atol=1e-9)

    def test_none_window_returns_unchanged_with_nan_baseline(self) -> None:
        x = np.arange(5, dtype=np.float64)
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        corrected, baseline = normalize_to_baseline_window(x, y, None, None)
        np.testing.assert_array_equal(corrected, y)
        self.assertTrue(np.isnan(baseline))

    def test_empty_window_returns_unchanged_with_nan_baseline(self) -> None:
        x = np.arange(5, dtype=np.float64)
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        corrected, baseline = normalize_to_baseline_window(x, y, 100.0, 200.0)
        np.testing.assert_array_equal(corrected, y)
        self.assertTrue(np.isnan(baseline))

    def test_reversed_window_bounds_are_normalized(self) -> None:
        x = np.arange(10, dtype=np.float64)
        y = np.arange(10, dtype=np.float64)
        corrected_a, baseline_a = normalize_to_baseline_window(x, y, 2.0, 5.0)
        corrected_b, baseline_b = normalize_to_baseline_window(x, y, 5.0, 2.0)
        self.assertAlmostEqual(baseline_a, baseline_b, places=9)
        np.testing.assert_allclose(corrected_a, corrected_b, atol=1e-9)


class TestAggregateGroupTraces(unittest.TestCase):
    def test_mean_and_sd_of_three_members(self) -> None:
        traces = {
            1: np.array([1.0, 2.0, 3.0]),
            2: np.array([3.0, 2.0, 1.0]),
            3: np.array([2.0, 2.0, 2.0]),
        }
        center, low, high = aggregate_traces(traces, center="mean", band="sd")
        np.testing.assert_allclose(center, [2.0, 2.0, 2.0], atol=1e-9)
        expected_sd = np.std([1.0, 3.0, 2.0])
        self.assertAlmostEqual(high[0] - center[0], expected_sd, places=9)
        self.assertAlmostEqual(center[0] - low[0], expected_sd, places=9)

    def test_median_center(self) -> None:
        traces = {1: np.array([1.0]), 2: np.array([100.0]), 3: np.array([2.0])}
        center, _low, _high = aggregate_traces(traces, center="median", band="sd")
        self.assertAlmostEqual(center[0], 2.0, places=9)

    def test_sem_band_is_smaller_than_sd_band(self) -> None:
        traces = {1: np.array([1.0, 1.0]), 2: np.array([5.0, 5.0]), 3: np.array([3.0, 3.0])}
        _center_sd, low_sd, high_sd = aggregate_traces(traces, band="sd")
        _center_sem, low_sem, high_sem = aggregate_traces(traces, band="sem")
        self.assertLess(high_sem[0] - low_sem[0], high_sd[0] - low_sd[0])

    def test_nan_in_one_member_is_excluded_per_time_point(self) -> None:
        traces = {
            1: np.array([1.0, np.nan, 3.0]),
            2: np.array([3.0, 4.0, 5.0]),
        }
        center, _low, _high = aggregate_traces(traces, center="mean", band="sd")
        # time point 1: only member 2 has a value (4.0) -> nanmean drops the NaN.
        self.assertAlmostEqual(center[1], 4.0, places=9)
        self.assertAlmostEqual(center[0], 2.0, places=9)

    def test_empty_input_returns_empty_arrays(self) -> None:
        center, low, high = aggregate_traces({})
        self.assertEqual(center.size, 0)
        self.assertEqual(low.size, 0)
        self.assertEqual(high.size, 0)


class TestFilterTracesBatch(unittest.TestCase):
    """`filter_traces` (all ROIs at once) must give the numbers of `apply_statistics_with_fit` ROI by ROI; only the speed differs (the Sensorgram's 21 s on 160 ROIs)."""

    def setUp(self) -> None:
        rng = np.random.default_rng(3)
        n = 300
        self.x = np.linspace(0.0, 900.0, n)
        self.traces = 600.0 + np.sin(np.linspace(0, 9, n))[None, :] + rng.normal(0, 0.05, (7, n))
        self.traces[:, 120] += 3.0  # a spike
        self.traces[1, 40:46] = np.nan  # an interior gap
        self.traces[2, 290:] = np.nan  # a trailing gap
        self.traces[3, :] = np.nan  # a ROI nothing is known of
        # like `membership_segments`: steps, cubes in no step (-1), a few one-cube runs (< -1) and a run shorter than the window
        labels = np.repeat(np.arange(30), 10).astype(np.int64)
        labels[::13] = -1
        labels[100:103] = [-2, -3, -4]
        self.segments = labels

    def _compare(self, settings: StatisticsSettings, segments) -> None:
        batch = filter_traces(self.traces, settings, segments)
        for row, trace in enumerate(self.traces):
            expected, _, _ = apply_statistics_with_fit(self.x, trace, settings, segments, None)
            got, _, _ = apply_baseline_with_fit(self.x, batch[row], settings, None)
            np.testing.assert_array_equal(np.isnan(got), np.isnan(expected))
            np.testing.assert_allclose(got, expected, rtol=0, atol=1e-9, equal_nan=True)

    def test_savgol_inside_steps_matches_row_by_row(self) -> None:
        self._compare(StatisticsSettings(smoothing_method="savgol", smoothing_window=15, smoothing_polyorder=2), self.segments)

    def test_savgol_whole_trace_matches_row_by_row(self) -> None:
        self._compare(StatisticsSettings(smoothing_method="savgol", smoothing_window=21, smoothing_polyorder=3), None)

    def test_hampel_then_savgol_matches_row_by_row(self) -> None:
        self._compare(StatisticsSettings(smoothing_method="savgol", spike_rejection_enabled=True), self.segments)

    def test_moving_average_and_running_median_match_row_by_row(self) -> None:
        self._compare(StatisticsSettings(smoothing_method="moving_average", spike_rejection_enabled=True, spike_rejection_method="running_median"), self.segments)

    def test_nothing_on_returns_the_traces(self) -> None:
        np.testing.assert_array_equal(filter_traces(self.traces, StatisticsSettings(), None), self.traces)

    def test_no_rows_and_bad_segments(self) -> None:
        self.assertEqual(filter_traces(np.empty((0, 300)), StatisticsSettings(smoothing_method="savgol"), self.segments).shape, (0, 300))
        with self.assertRaises(ValueError):
            filter_traces(self.traces, StatisticsSettings(smoothing_method="savgol"), self.segments[:-1])


if __name__ == "__main__":
    unittest.main()
