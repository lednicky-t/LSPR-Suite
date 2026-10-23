"""Pure-logic tests for the Sensorgram's numbers (`analysis/scan_trace.py`): the metric per cube from a stored scan, then the statistics per ROI and the
averaging of already-processed traces. A `StoredScan` is built in memory with a Gaussian absorption band whose centre drifts with the cube. No Qt, no
files."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.query import formula_spectrum, metric_value
    from lspr_imaging_app.analysis.scan_store import StoredScan
    from lspr_imaging_app.analysis.scan_trace import (
        DISPLAY_AVERAGE_ALL,
        DISPLAY_BY_GROUP,
        DISPLAY_INDIVIDUAL,
        GroupSpec,
        SensorgramRequest,
        build_series,
        compute_sensorgram,
        compute_traces,
        metric_trace,
        trace_key,
    )
    from lspr_imaging_app.analysis.settings import MetricSettings, StatisticsSettings
    from lspr_imaging_app.analysis.statistics import apply_statistics
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(480.0, 600.0, 25)
N_CUBE = 12
ROI_IDS = (1, 2, 3, 4)


def _centre(roi_id: int, cube: int) -> float:
    return 530.0 + 5.0 * roi_id + 0.8 * cube  # nm: every ROI drifts 0.8 nm per cube, at its own start


def make_scan(unscanned=(), no_pixels=()) -> StoredScan:
    shape = (N_CUBE, len(ROI_IDS), len(WL), 2)
    mean = np.zeros(shape)
    for cube in range(N_CUBE):
        for r, roi_id in enumerate(ROI_IDS):
            band = 0.3 * np.exp(-0.5 * ((WL - _centre(roi_id, cube)) / 18.0) ** 2)
            mean[cube, r, :, 0] = 1000.0 * 10 ** (-band)
            mean[cube, r, :, 1] = 1000.0
    n_valid = np.full(shape, 100, dtype=np.uint32)
    cell_hash = np.ones((N_CUBE, len(ROI_IDS)), dtype=np.uint64)
    for cube in unscanned:
        cell_hash[cube] = 0
    for cube in no_pixels:
        mean[cube, :, :, :] = np.nan
        n_valid[cube] = 0
    return StoredScan(
        path=Path("memory.h5"), attrs={}, cube_indices=tuple(range(N_CUBE)), roi_ids=ROI_IDS, wavelength_nm=tuple(WL.tolist()),
        acquired_at_unix_ms=np.zeros((N_CUBE, len(WL)), dtype=np.int64), values={"mean": mean}, std=np.full(shape, 5.0), n_valid=n_valid,
        n_nominal=np.full(shape, 110, dtype=np.uint32), cell_hash=cell_hash, recipe_id=np.ones((N_CUBE, len(ROI_IDS)), dtype=np.int32),
        layout_id=np.ones(N_CUBE, dtype=np.int32),
    )


GAUSS = MetricSettings(fit_method="gaussian", metric_key="maximum")
NO_STATS = StatisticsSettings()


def request(roi_ids=ROI_IDS, stats=NO_STATS, metric=GAUSS, groups=(), x=None) -> SensorgramRequest:
    return SensorgramRequest(tuple(roi_ids), "absorbance", metric, stats, np.arange(N_CUBE, dtype=float) if x is None else x, tuple(groups))


class MetricTraceTests(unittest.TestCase):
    def test_a_drifting_peak_is_followed_cube_by_cube(self) -> None:
        trace = metric_trace(make_scan(), 2, "absorbance", GAUSS)
        expected = np.array([_centre(2, c) for c in range(N_CUBE)])
        np.testing.assert_allclose(trace, expected, atol=0.05)
        self.assertAlmostEqual(trace[-1] - trace[0], 0.8 * (N_CUBE - 1), delta=0.1)

    def test_it_is_the_query_layers_one_call_answer_for_a_cube(self) -> None:
        stored = make_scan()
        for metric in (GAUSS, MetricSettings(fit_method="polynomial", poly_order=4, metric_key="centroid"), MetricSettings(fit_method="none")):
            trace = metric_trace(stored, 3, "absorbance", metric)
            r = stored.roi_ids.index(3)
            sample, reference = stored.values["mean"][5, r, :, 0], stored.values["mean"][5, r, :, 1]
            x, _ = metric_value(
                formula_spectrum(WL, sample, reference, "absorbance"), metric.fit_method, metric.metric_key,
                poly_order=metric.poly_order, wl_min=metric.fit_wl_min, wl_max=metric.fit_wl_max,
            )
            self.assertEqual(trace[5], x)

    def test_gaps_are_nan_not_filled(self) -> None:
        stored = make_scan(unscanned=(3,), no_pixels=(7,))
        trace = metric_trace(stored, 1, "absorbance", GAUSS)
        self.assertTrue(np.isnan(trace[3]) and np.isnan(trace[7]))
        self.assertEqual(int(np.isfinite(trace).sum()), N_CUBE - 2)
        self.assertTrue(np.all(np.isnan(metric_trace(stored, 99, "absorbance", GAUSS))))  # a ROI that is not in the file

    def test_a_cube_with_too_few_points_for_a_fit_is_a_gap(self) -> None:
        stored = make_scan()
        stored.values["mean"][4, 0, 1:, 0] = np.nan  # ROI 1 has one usable wavelength at cube 4
        trace = metric_trace(stored, 1, "absorbance", GAUSS)
        self.assertTrue(np.isnan(trace[4]))
        self.assertTrue(np.isfinite(trace[5]))

    def test_the_cache_key_reacts_to_what_the_trace_depends_on(self) -> None:
        stored = make_scan()
        base = trace_key(stored, "absorbance", GAUSS)
        self.assertEqual(base, trace_key(stored, "absorbance", replace(GAUSS)))
        self.assertNotEqual(base, trace_key(stored, "ratio", GAUSS))
        self.assertNotEqual(base, trace_key(stored, "absorbance", replace(GAUSS, metric_key="centroid")))
        self.assertNotEqual(base, trace_key(stored, "absorbance", replace(GAUSS, fit_wl_min=500.0)))
        self.assertNotEqual(base, trace_key(make_scan(), "absorbance", GAUSS))  # a reloaded file is a different object


class ComputeTracesTests(unittest.TestCase):
    def test_only_missing_rois_are_computed(self) -> None:
        stored = make_scan()
        known = {1: metric_trace(stored, 1, "absorbance", GAUSS)}
        seen: list[int] = []
        new = compute_traces(stored, (1, 2, 3), "absorbance", GAUSS, known, progress=lambda done, total: seen.append(total))
        self.assertEqual(sorted(new), [2, 3])
        self.assertEqual(seen, [2, 2])

    def test_cancel_returns_what_was_finished(self) -> None:
        stored = make_scan()
        calls = []

        def cancelled() -> bool:
            calls.append(1)
            return len(calls) > 2  # after two ROIs

        series, new = compute_sensorgram(stored, request(), {}, cancelled=cancelled)
        self.assertEqual(series, [])
        self.assertEqual(sorted(new), [1, 2])


class SeriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stored = make_scan()
        self.traces = {r: metric_trace(self.stored, r, "absorbance", GAUSS) for r in ROI_IDS}

    def test_individual_gives_one_series_per_roi_with_statistics_applied_per_roi(self) -> None:
        stats = StatisticsSettings(baseline_enabled=True, baseline_window_start=0.0, baseline_window_end=2.0)
        series = build_series(request(stats=stats), self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        self.assertEqual([s.label for s in series], ["ROI 1", "ROI 2", "ROI 3", "ROI 4"])
        for s, roi_id in zip(series, ROI_IDS):
            expected, baseline = apply_statistics(np.arange(N_CUBE, dtype=float), self.traces[roi_id], stats)
            np.testing.assert_array_equal(s.y, expected)
            self.assertEqual(s.baseline, baseline)
            self.assertAlmostEqual(s.y[0] + 0.0, s.y[0])
        self.assertAlmostEqual(float(np.mean(series[0].y[:3])), 0.0, places=9)  # reads as a shift from the baseline window

    def test_a_spike_is_removed_before_smoothing_and_never_by_averaging_spectra(self) -> None:
        traces = {r: t.copy() for r, t in self.traces.items()}
        traces[1][6] += 40.0  # one bad cube
        stats = StatisticsSettings(spike_rejection_enabled=True, spike_rejection_window=5)
        series = build_series(request(stats=stats), traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        self.assertLess(abs(series[0].y[6] - self.traces[1][6]), 1.0)
        self.assertEqual(series[1].y[6], traces[2][6])  # the other ROIs untouched

    def test_average_all_is_the_mean_of_the_processed_per_roi_traces_with_a_band(self) -> None:
        sd = build_series(request(), self.traces, DISPLAY_AVERAGE_ALL, "mean", "sd")[0]
        stacked = np.vstack([self.traces[r] for r in ROI_IDS])
        np.testing.assert_allclose(sd.y, stacked.mean(axis=0))
        np.testing.assert_allclose(sd.band_high - sd.y, stacked.std(axis=0))
        sem = build_series(request(), self.traces, DISPLAY_AVERAGE_ALL, "mean", "sem")[0]
        np.testing.assert_allclose(sem.band_high - sem.y, stacked.std(axis=0, ddof=1) / 2.0)  # sqrt(4)
        median = build_series(request(), self.traces, DISPLAY_AVERAGE_ALL, "median", "sd")[0]
        np.testing.assert_allclose(median.y, np.median(stacked, axis=0))
        self.assertEqual((sd.n_members, sd.label), (4, "Average of 4 ROIs"))

    def test_the_average_of_traces_is_not_the_trace_of_the_average_spectrum(self) -> None:
        # Pooling before the fit is the bug the layers document records; here the two differ because the ROIs drift from different starts.
        mean_of_traces = build_series(request(metric=GAUSS), self.traces, DISPLAY_AVERAGE_ALL, "mean", "sd")[0].y
        r_ids = [self.stored.roi_ids.index(r) for r in ROI_IDS]
        pooled = self.stored.values["mean"][:, r_ids, :, :].mean(axis=1)
        pooled_trace = []
        for cube in range(N_CUBE):
            x, _ = metric_value(formula_spectrum(WL, pooled[cube, :, 0], pooled[cube, :, 1], "absorbance"), "gaussian", "maximum")
            pooled_trace.append(x)
        self.assertGreater(float(np.max(np.abs(mean_of_traces - np.array(pooled_trace)))), 1e-6)

    def test_by_group_uses_group_members_and_collects_the_rest_as_ungrouped(self) -> None:
        groups = (GroupSpec("Top", "#ff0000", (1, 2)), GroupSpec("Bottom", "#00ff00", (2, 3)))  # ROI 2 is in both: it goes to the first
        series = build_series(request(groups=groups), self.traces, DISPLAY_BY_GROUP, "mean", "sd")
        self.assertEqual([s.label for s in series], ["Top (n=2)", "Bottom (n=1)", "Ungrouped (n=1)"])
        np.testing.assert_allclose(series[0].y, (self.traces[1] + self.traces[2]) / 2.0)
        np.testing.assert_allclose(series[1].y, self.traces[3])
        np.testing.assert_allclose(series[2].y, self.traces[4])

    def test_only_selected_rois_take_part_in_a_group(self) -> None:
        groups = (GroupSpec("Top", "#ff0000", (1, 2, 3)),)
        series = build_series(request(roi_ids=(1, 4), groups=groups), {1: self.traces[1], 4: self.traces[4]}, DISPLAY_BY_GROUP, "mean", "sd")
        self.assertEqual([s.label for s in series], ["Top (n=1)", "Ungrouped (n=1)"])

    def test_a_gap_in_one_member_does_not_blank_the_average(self) -> None:
        traces = {r: t.copy() for r, t in self.traces.items()}
        traces[1][4] = np.nan
        s = build_series(request(), traces, DISPLAY_AVERAGE_ALL, "mean", "sd")[0]
        self.assertTrue(np.isfinite(s.y[4]))
        self.assertAlmostEqual(s.y[4], float(np.mean([traces[2][4], traces[3][4], traces[4][4]])))

    def test_compute_sensorgram_end_to_end_uses_the_statistics_settings(self) -> None:
        stats = StatisticsSettings(sensorgram_display_mode=DISPLAY_AVERAGE_ALL, sensorgram_aggregation="median", sensorgram_band="sem")
        series, new = compute_sensorgram(self.stored, request(stats=stats), {})
        self.assertEqual(len(series), 1)
        self.assertEqual(sorted(new), list(ROI_IDS))
        series2, new2 = compute_sensorgram(self.stored, request(stats=stats), new)
        self.assertEqual(new2, {})  # all from the cache
        np.testing.assert_array_equal(series[0].y, series2[0].y)


if __name__ == "__main__":
    unittest.main()
