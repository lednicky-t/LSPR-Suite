"""Statistics of the experiment steps (`lspri_imaging_app.analysis.step_stats`): which cubes belong to a step, and mean / std / sem / slope / noise per ROI and
per group, checked against plain numpy. No Qt."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np

    from lspr_imaging_app.analysis import step_stats as ss
    from lspr_imaging_app.analysis.scan_trace import SensorgramRequest, build_series
    from lspr_imaging_app.analysis.settings import MetricSettings, StatisticsSettings
    from lspr_imaging_app.analysis.statistics import apply_statistics, smooth_moving_average, smooth_savgol
    from lspr_imaging_app.plan.edits import EffectiveStep
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

T0 = 1_700_000_000_000  # unix ms of t = 0 s
PERIOD_S, CUBE_S = 10.0, 6.0  # a cube starts every 10 s and its frames span 6 s


def _ms(seconds: float) -> int:
    return T0 + int(round(seconds * 1000))


def _step(number: int, start_s: float, end_s: float) -> EffectiveStep:
    return EffectiveStep(
        step=number, label=f"step {number}", start_unix_ms=_ms(start_s), end_unix_ms=_ms(end_s), color=None,
        measured_label=f"step {number}", measured_start_unix_ms=_ms(start_s), measured_end_unix_ms=_ms(end_s),
    )


def _cubes(count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """First-frame times, last-frame times (unix ms) and the time in seconds of `count` cubes."""
    t = np.arange(count) * PERIOD_S
    return T0 + t * 1000.0, T0 + (t + CUBE_S) * 1000.0, t


class AssignCubesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first, self.last, self.t = _cubes(100)  # 0 .. 990 s

    def test_a_cube_belongs_to_the_step_it_lies_wholly_inside(self) -> None:
        m = ss.assign_cubes([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)
        np.testing.assert_array_equal(np.flatnonzero(m.used[0]), np.arange(30))  # cubes at 0 .. 290 s
        np.testing.assert_array_equal(np.flatnonzero(m.used[1]), np.arange(30, 60))  # a cube that starts exactly on the boundary is in the later step
        np.testing.assert_array_equal(m.n_partial, [0, 0])
        np.testing.assert_array_equal(m.n_cubes, [30, 30])

    def test_a_cube_across_a_boundary_is_left_out_of_both_steps_and_counted(self) -> None:
        m = ss.assign_cubes([_step(1, 0, 305), _step(2, 305, 600)], self.first, self.last)  # cube 30 spans 300-306 s
        self.assertFalse(m.used[:, 30].any())
        np.testing.assert_array_equal(m.n_partial, [1, 1])
        self.assertEqual(m.n_cubes[0], 30)
        self.assertEqual(m.n_cubes[1], 29)

    def test_a_cube_whose_last_frame_is_on_the_end_is_not_inside(self) -> None:
        m = ss.assign_cubes([_step(1, 0, 296)], self.first, self.last)  # end is excluded; cube 29 ends at 296 s
        self.assertFalse(m.used[0, 29])
        self.assertEqual(m.n_partial[0], 1)

    def test_cubes_without_timing_are_in_no_step(self) -> None:
        first, last = self.first.copy(), self.last.copy()
        first[3], last[3] = np.nan, np.nan
        first[4], last[4] = np.nan, np.nan
        last[5] = np.nan  # only the last frame unknown: treated as a point at the first frame
        m = ss.assign_cubes([_step(1, 0, 300)], first, last)
        self.assertFalse(m.used[0, 3] or m.used[0, 4])
        self.assertTrue(m.used[0, 5])
        self.assertEqual(m.n_cubes[0], 28)
        self.assertEqual(m.n_partial[0], 0)

    def test_steps_are_taken_in_time_order_and_gaps_between_steps_are_fine(self) -> None:
        m = ss.assign_cubes([_step(2, 500, 600), _step(1, 0, 100)], self.first, self.last)
        self.assertEqual([s.step for s in m.steps], [1, 2])
        np.testing.assert_array_equal(m.n_cubes, [10, 10])
        self.assertFalse(m.used[:, 20:50].any())

    def test_window_skip_start_skip_end_and_last_seconds(self) -> None:
        step = [_step(1, 0, 300)]
        skip = ss.assign_cubes(step, self.first, self.last, ss.StepWindow(skip_start_s=60))
        np.testing.assert_array_equal(np.flatnonzero(skip.used[0]), np.arange(6, 30))
        both = ss.assign_cubes(step, self.first, self.last, ss.StepWindow(skip_start_s=60, skip_end_s=30))
        np.testing.assert_array_equal(np.flatnonzero(both.used[0]), np.arange(6, 27))  # the cube at 260 s ends at 266 s < 270 s
        last = ss.assign_cubes(step, self.first, self.last, ss.StepWindow(last_s=100))
        np.testing.assert_array_equal(np.flatnonzero(last.used[0]), np.arange(20, 30))
        last_after_skip = ss.assign_cubes(step, self.first, self.last, ss.StepWindow(skip_end_s=50, last_s=100))  # window is 0-250 s, its last 100 s is 150-250 s
        np.testing.assert_array_equal(np.flatnonzero(last_after_skip.used[0]), np.arange(15, 25))  # cubes at 150 .. 240 s (the one at 240 s ends at 246 s)
        np.testing.assert_allclose(last.window_start_unix_ms, [_ms(200)])
        np.testing.assert_allclose(last.window_end_unix_ms, [_ms(300)])

    def test_a_cube_cut_by_the_window_edge_is_counted_as_partial(self) -> None:
        m = ss.assign_cubes([_step(1, 0, 300)], self.first, self.last, ss.StepWindow(skip_start_s=65))  # cube 6 spans 60-66 s
        self.assertFalse(m.used[0, 6])
        self.assertEqual(m.n_partial[0], 1)

    def test_an_empty_window_selects_nothing(self) -> None:
        m = ss.assign_cubes([_step(1, 0, 300)], self.first, self.last, ss.StepWindow(skip_start_s=200, skip_end_s=200))
        self.assertEqual(m.n_cubes[0], 0)
        self.assertEqual(m.n_partial[0], 0)

    def test_no_steps_and_mismatched_arrays(self) -> None:
        m = ss.assign_cubes([], self.first, self.last)
        self.assertEqual(m.used.shape, (0, 100))
        with self.assertRaises(ValueError):
            ss.assign_cubes([_step(1, 0, 300)], self.first, self.last[:-1])


class StepStatisticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first, self.last, self.t = _cubes(100)
        self.membership = ss.assign_cubes([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)

    def test_matches_numpy_on_a_drifting_noisy_trace(self) -> None:
        rng = np.random.default_rng(7)
        y = 650.0 + 0.004 * self.t + rng.normal(0.0, 0.05, self.t.size)
        stats = ss.step_statistics([5], y[None, :], self.membership)
        for s, cubes in enumerate((slice(0, 30), slice(30, 60))):
            v, t = y[cubes], self.t[cubes]
            slope, intercept = np.polyfit(t, v, 1)
            residual = v - (slope * t + intercept)
            self.assertEqual(stats.n[0, s], 30)
            self.assertAlmostEqual(stats.mean[0, s], v.mean(), places=10)
            self.assertAlmostEqual(stats.std[0, s], v.std(ddof=1), places=10)
            self.assertAlmostEqual(stats.sem[0, s], v.std(ddof=1) / np.sqrt(30), places=10)
            self.assertAlmostEqual(stats.slope_per_s[0, s], slope, places=10)
            self.assertAlmostEqual(stats.noise[0, s], residual.std(ddof=2), places=10)

    def test_noise_does_not_count_a_steady_drift_but_std_does(self) -> None:
        ramp = 600.0 + 0.02 * self.t  # a perfectly straight drift, no noise at all
        stats = ss.step_statistics([1], ramp[None, :], self.membership)
        self.assertLess(stats.noise[0, 0], 1e-8)
        self.assertAlmostEqual(stats.slope_per_s[0, 0], 0.02, places=10)
        self.assertGreater(stats.std[0, 0], 0.05)  # 30 cubes over 290 s: std of a ramp is ~0.02 * 290 / sqrt(12)

    def test_for_a_flat_step_noise_and_std_agree(self) -> None:
        rng = np.random.default_rng(3)
        y = 640.0 + rng.normal(0.0, 0.1, self.t.size)
        stats = ss.step_statistics([1], y[None, :], self.membership)
        self.assertAlmostEqual(stats.noise[0, 0] / stats.std[0, 0], 1.0, delta=0.1)

    def test_sem_shrinks_with_more_cubes_but_noise_does_not(self) -> None:
        rng = np.random.default_rng(11)
        y = 640.0 + rng.normal(0.0, 0.1, self.t.size)
        short = ss.step_statistics([1], y[None, :], ss.assign_cubes([_step(1, 0, 100)], self.first, self.last))
        long = ss.step_statistics([1], y[None, :], ss.assign_cubes([_step(1, 0, 900)], self.first, self.last))
        self.assertLess(long.sem[0, 0], short.sem[0, 0] / 2)
        self.assertAlmostEqual(long.noise[0, 0], short.noise[0, 0], delta=0.05)

    def test_gaps_are_skipped_and_counted(self) -> None:
        rng = np.random.default_rng(5)
        y = 640.0 + 0.003 * self.t + rng.normal(0.0, 0.05, self.t.size)
        y[[2, 3, 17, 40]] = np.nan  # 3 gaps in step 1, 1 in step 2
        stats = ss.step_statistics([1], y[None, :], self.membership)
        ok = np.isfinite(y[:30])
        slope, intercept = np.polyfit(self.t[:30][ok], y[:30][ok], 1)
        self.assertEqual(stats.n[0, 0], 27)
        self.assertEqual(stats.n[0, 1], 29)
        self.assertAlmostEqual(stats.mean[0, 0], y[:30][ok].mean(), places=10)
        self.assertAlmostEqual(stats.std[0, 0], y[:30][ok].std(ddof=1), places=10)
        self.assertAlmostEqual(stats.slope_per_s[0, 0], slope, places=10)
        self.assertAlmostEqual(stats.noise[0, 0], (y[:30][ok] - (slope * self.t[:30][ok] + intercept)).std(ddof=2), places=10)

    def test_too_few_values_give_nan_not_zero(self) -> None:
        y = np.full(100, np.nan)
        y[0] = 1.0  # step 1: one value
        y[30], y[31] = 2.0, 4.0  # step 2: two values
        stats = ss.step_statistics([1], y[None, :], self.membership)
        self.assertEqual(stats.n[0].tolist(), [1, 2])
        self.assertEqual(stats.mean[0, 0], 1.0)
        self.assertTrue(np.isnan([stats.std[0, 0], stats.sem[0, 0], stats.slope_per_s[0, 0], stats.noise[0, 0]]).all())
        self.assertAlmostEqual(stats.mean[0, 1], 3.0)
        self.assertAlmostEqual(stats.std[0, 1], np.sqrt(2.0))
        self.assertAlmostEqual(stats.slope_per_s[0, 1], 0.2)  # 2 nm in 10 s
        self.assertTrue(np.isnan(stats.noise[0, 1]))  # a line through two points leaves nothing to measure noise on

    def test_a_step_with_no_cubes_or_no_values_is_all_nan_with_n_zero(self) -> None:
        membership = ss.assign_cubes([_step(1, 0, 300), _step(2, 5000, 6000)], self.first, self.last)
        stats = ss.step_statistics([1], np.full((1, 100), np.nan), membership)
        self.assertEqual(stats.n.tolist(), [[0, 0]])
        self.assertTrue(np.isnan(stats.mean).all() and np.isnan(stats.noise).all())

    def test_each_roi_gives_the_same_alone_or_in_a_batch(self) -> None:
        rng = np.random.default_rng(1)
        values = 640.0 + rng.normal(0.0, 0.1, (4, 100))
        values[1, 5] = np.nan
        batch = ss.step_statistics([10, 11, 12, 13], values, self.membership)
        for row in range(4):
            alone = ss.step_statistics([10 + row], values[row : row + 1], self.membership)
            for name in ("n", "mean", "std", "sem", "slope_per_s", "noise"):
                np.testing.assert_allclose(getattr(alone, name)[0], getattr(batch, name)[row], rtol=1e-12, atol=0, err_msg=name)

    def test_large_absolute_time_does_not_cost_precision(self) -> None:
        y = 600.0 + 0.02 * self.t
        stats = ss.step_statistics([1], y[None, :], self.membership)  # unix ms ~1.7e12: times are re-based inside a step
        self.assertAlmostEqual(stats.slope_per_s[0, 0], 0.02, places=12)

    def test_wrong_shape_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ss.step_statistics([1, 2], np.zeros((1, 100)), self.membership)
        with self.assertRaises(ValueError):
            ss.step_statistics([1], np.zeros((1, 99)), self.membership)

    def test_no_rois_and_no_steps(self) -> None:
        self.assertEqual(ss.step_statistics([], np.empty((0, 100)), self.membership).mean.shape, (0, 2))
        none = ss.step_statistics([1], np.zeros((1, 100)), ss.assign_cubes([], self.first, self.last))
        self.assertEqual(none.mean.shape, (1, 0))


class GroupStatisticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first, self.last, self.t = _cubes(100)
        self.membership = ss.assign_cubes([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)
        rng = np.random.default_rng(2)
        self.values = np.vstack([level + rng.normal(0.0, sigma, 100) for level, sigma in ((1.0, 0.01), (2.0, 0.02), (4.0, 0.04), (9.0, 0.5))])
        self.stats = ss.step_statistics([1, 2, 3, 4], self.values, self.membership)

    def test_group_combines_the_per_roi_step_means(self) -> None:
        g = ss.group_statistics(self.stats, [("A", [1, 2, 3])])[0]
        means = self.stats.mean[:3]
        np.testing.assert_allclose(g.mean, means.mean(axis=0), rtol=1e-12)
        np.testing.assert_allclose(g.std, means.std(axis=0, ddof=1), rtol=1e-12)  # the spread between ROIs, not within one
        np.testing.assert_allclose(g.sem, means.std(axis=0, ddof=1) / np.sqrt(3), rtol=1e-12)
        np.testing.assert_array_equal(g.n_roi, [3, 3])
        np.testing.assert_allclose(g.noise, np.median(self.stats.noise[:3], axis=0), rtol=1e-12)
        self.assertEqual((g.name, g.roi_ids), ("A", (1, 2, 3)))

    def test_group_noise_is_the_typical_roi_not_the_worst(self) -> None:
        g = ss.group_statistics(self.stats, [("all", [1, 2, 3, 4])])[0]  # ROI 4 is ~12x noisier than ROI 3
        np.testing.assert_allclose(g.noise, np.median(self.stats.noise, axis=0), rtol=1e-12)
        self.assertTrue((g.noise < self.stats.noise[3] / 5).all())

    def test_a_roi_without_a_value_in_a_step_is_left_out_of_that_step_only(self) -> None:
        values = self.values.copy()
        values[1, 30:60] = np.nan  # ROI 2 has nothing in step 2
        stats = ss.step_statistics([1, 2, 3, 4], values, self.membership)
        g = ss.group_statistics(stats, [("A", [1, 2, 3])])[0]
        np.testing.assert_array_equal(g.n_roi, [3, 2])
        self.assertAlmostEqual(g.mean[1], np.mean([stats.mean[0, 1], stats.mean[2, 1]]))

    def test_unknown_members_are_ignored_and_empty_groups_dropped(self) -> None:
        groups = ss.group_statistics(self.stats, [("A", [1, 99]), ("none", [98, 99]), ("B", [])])
        self.assertEqual([g.name for g in groups], ["A"])
        self.assertEqual(groups[0].roi_ids, (1,))
        self.assertTrue(np.isnan(groups[0].std).all())  # one ROI: no spread between ROIs
        np.testing.assert_array_equal(groups[0].n_roi, [1, 1])

    def test_a_roi_in_two_groups_counts_in_both(self) -> None:
        a, b = ss.group_statistics(self.stats, [("A", [1, 2]), ("B", [2, 3])])
        self.assertEqual((a.roi_ids, b.roi_ids), ((1, 2), (2, 3)))


class TraceMatrixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first, self.last, self.t = _cubes(100)
        rng = np.random.default_rng(4)
        self.traces = {1: 640.0 + rng.normal(0.0, 0.1, 100), 2: 641.0 + rng.normal(0.0, 0.1, 100)}

    def test_the_values_are_what_the_sensorgram_plots_smoothing_included(self) -> None:
        for settings in (
            StatisticsSettings(),
            StatisticsSettings(smoothing_method="savgol", smoothing_window=9, smoothing_polyorder=2),
            StatisticsSettings(smoothing_method="moving_average", smoothing_window=9),
        ):
            m = ss.trace_matrix([1, 2], self.traces, self.t, settings)
            for row, roi in enumerate((1, 2)):
                np.testing.assert_array_equal(m[row], apply_statistics(self.t, self.traces[roi], settings)[0])

    def test_smoothing_follows_the_selection_and_lowers_std_noise_and_sem(self) -> None:
        membership = ss.assign_cubes([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)
        plain = ss.step_statistics([1], ss.trace_matrix([1], self.traces, self.t, StatisticsSettings()), membership)
        smoothed = ss.step_statistics(
            [1], ss.trace_matrix([1], self.traces, self.t, StatisticsSettings(smoothing_method="savgol", smoothing_window=9, smoothing_polyorder=2)), membership
        )
        self.assertTrue((smoothed.std < plain.std / 1.5).all())
        self.assertTrue((smoothed.noise < plain.noise / 1.5).all())
        self.assertTrue((smoothed.sem < plain.sem / 1.5).all())
        np.testing.assert_allclose(smoothed.mean, plain.mean, atol=0.03)  # a smoother hardly moves the mean of a flat step

    def test_baseline_moves_the_mean_and_leaves_std_and_noise_alone(self) -> None:
        membership = ss.assign_cubes([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)
        plain = ss.step_statistics([1], ss.trace_matrix([1], self.traces, self.t, StatisticsSettings()), membership)
        based = ss.step_statistics(
            [1], ss.trace_matrix([1], self.traces, self.t, StatisticsSettings(baseline_enabled=True, baseline_window_start=0.0, baseline_window_end=100.0)),
            membership,
        )
        offset = self.traces[1][:11].mean()  # x = 0 .. 100 s is cubes 0 .. 10
        np.testing.assert_allclose(based.mean, plain.mean - offset, atol=1e-9)
        np.testing.assert_allclose(based.std, plain.std, atol=1e-9)
        np.testing.assert_allclose(based.noise, plain.noise, atol=1e-9)

    def test_spike_rejection_the_user_chose_is_kept(self) -> None:
        spiky = {1: self.traces[1].copy()}
        spiky[1][50] += 50.0
        raw = ss.trace_matrix([1], spiky, self.t, StatisticsSettings())
        cleaned = ss.trace_matrix([1], spiky, self.t, StatisticsSettings(spike_rejection_enabled=True))
        self.assertGreater(raw[0, 50], 680.0)
        self.assertLess(cleaned[0, 50], 641.0)

    def test_no_rois(self) -> None:
        self.assertEqual(ss.trace_matrix([], self.traces, self.t, StatisticsSettings()).shape, (0, 100))


class ComputeStepStatisticsTest(unittest.TestCase):
    def test_one_call_equals_the_parts(self) -> None:
        first, last, t = _cubes(100)
        rng = np.random.default_rng(9)
        traces = {r: 640.0 + r + rng.normal(0.0, 0.1, 100) for r in (1, 2, 3)}
        steps = [_step(1, 0, 300), _step(2, 300, 600)]
        window = ss.StepWindow(skip_start_s=50)
        whole = ss.compute_step_statistics(steps, [1, 2, 3], traces, t, StatisticsSettings(), first, last, window)
        parts = ss.step_statistics(
            [1, 2, 3], ss.trace_matrix([1, 2, 3], traces, t, StatisticsSettings()), ss.assign_cubes(steps, first, last, window)
        )
        for name in ("n", "mean", "std", "sem", "slope_per_s", "noise"):
            np.testing.assert_array_equal(getattr(whole, name), getattr(parts, name))
        self.assertEqual(whole.roi_ids, (1, 2, 3))
        self.assertEqual([s.step for s in whole.steps], [1, 2])


class SmoothingSegmentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first, self.last, self.t = _cubes(100)

    def test_each_step_is_a_run_and_cubes_outside_the_steps_are_minus_one(self) -> None:
        labels = ss.smoothing_segments([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)
        self.assertEqual(labels.dtype, np.int64)
        np.testing.assert_array_equal(labels[:30], 0)
        np.testing.assert_array_equal(labels[30:60], 1)
        np.testing.assert_array_equal(labels[60:], -1)

    def test_a_cube_across_a_boundary_is_a_run_of_its_own(self) -> None:
        labels = ss.smoothing_segments([_step(1, 0, 305), _step(2, 305, 600)], self.first, self.last)  # cube 30 spans 300-306 s
        np.testing.assert_array_equal(labels[:30], 0)
        np.testing.assert_array_equal(labels[31:60], 1)
        self.assertLess(labels[30], -1)  # not in either step, and not part of the "outside" run
        two = ss.smoothing_segments([_step(1, 0, 305), _step(2, 305, 316)], self.first, self.last)  # cubes 30 and 31 both straddle a boundary
        self.assertNotEqual(two[30], two[31])  # neighbours that straddle are still separate runs

    def test_cubes_without_timing_and_no_steps(self) -> None:
        first, last = self.first.copy(), self.last.copy()
        first[3], last[3] = np.nan, np.nan
        labels = ss.smoothing_segments([_step(1, 0, 300)], first, last)
        self.assertEqual(labels[3], -1)
        self.assertEqual(labels[2], 0)
        np.testing.assert_array_equal(ss.smoothing_segments([], self.first, self.last), np.full(100, -1))

    def test_the_straddling_cubes_are_those_the_statistics_leave_out(self) -> None:
        steps = [_step(1, 0, 305), _step(2, 305, 600)]
        membership = ss.assign_cubes(steps, self.first, self.last)
        labels = ss.smoothing_segments(steps, self.first, self.last)
        np.testing.assert_array_equal(np.flatnonzero(labels < -1), np.flatnonzero(membership.partial.any(axis=0)))
        np.testing.assert_array_equal(membership.partial.sum(axis=1), membership.n_partial)


class ApplyStatisticsSegmentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = np.arange(60) * PERIOD_S
        self.y = np.where(np.arange(60) < 30, 600.0, 610.0)  # two flat levels with a jump between them
        self.labels = np.where(np.arange(60) < 30, 0, 1)
        self.savgol = StatisticsSettings(smoothing_method="savgol", smoothing_window=9, smoothing_polyorder=2)

    def test_whole_trace_smoothing_blurs_the_jump_but_per_segment_smoothing_does_not(self) -> None:
        whole = apply_statistics(self.t, self.y, self.savgol)[0]
        pieces = apply_statistics(self.t, self.y, self.savgol, self.labels)[0]
        self.assertGreater(np.abs(whole - self.y).max(), 1.0)  # the smoother reads across the boundary
        np.testing.assert_allclose(pieces, self.y, atol=1e-9)  # it does not

    def test_each_piece_is_exactly_the_smoothing_of_that_piece_alone(self) -> None:
        rng = np.random.default_rng(0)
        y = self.y + rng.normal(0.0, 0.2, 60)
        for settings, smooth in (
            (self.savgol, lambda part: smooth_savgol(part, 9, 2)),
            (StatisticsSettings(smoothing_method="moving_average", smoothing_window=9), lambda part: smooth_moving_average(part, 9)),
        ):
            expected = np.concatenate([smooth(y[:30]), smooth(y[30:])])
            np.testing.assert_array_equal(apply_statistics(self.t, y, settings, self.labels)[0], expected)

    def test_without_segments_or_without_smoothing_nothing_changes(self) -> None:
        rng = np.random.default_rng(1)
        y = self.y + rng.normal(0.0, 0.2, 60)
        np.testing.assert_array_equal(apply_statistics(self.t, y, self.savgol, None)[0], smooth_savgol(y, 9, 2))
        np.testing.assert_array_equal(apply_statistics(self.t, y, StatisticsSettings(), self.labels)[0], y)  # no smoothing: labels have nothing to do

    def test_a_run_of_one_cube_stays_as_measured_and_a_run_shorter_than_the_window_still_works(self) -> None:
        rng = np.random.default_rng(2)
        y = self.y + rng.normal(0.0, 0.2, 60)
        labels = self.labels.copy()
        labels[29] = -31  # a straddling cube: its own run
        labels[30:32] = 5  # a two-cube run, window 9
        out = apply_statistics(self.t, y, self.savgol, labels)[0]
        self.assertEqual(out[29], y[29])
        self.assertTrue(np.isfinite(out).all())

    def test_gaps_stay_gaps_inside_a_piece(self) -> None:
        y = self.y.copy()
        y[10] = np.nan
        out = apply_statistics(self.t, y, self.savgol, self.labels)[0]
        self.assertTrue(np.isnan(out[10]))
        self.assertEqual(int(np.isnan(out).sum()), 1)

    def test_the_baseline_is_taken_from_the_smoothed_pieces(self) -> None:
        settings = StatisticsSettings(smoothing_method="savgol", smoothing_window=9, smoothing_polyorder=2, baseline_enabled=True,
                                      baseline_window_start=0.0, baseline_window_end=250.0)
        out, baseline = apply_statistics(self.t, self.y, settings, self.labels)
        self.assertAlmostEqual(baseline, 600.0, places=9)
        np.testing.assert_allclose(out, self.y - 600.0, atol=1e-9)

    def test_wrong_length_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            apply_statistics(self.t, self.y, self.savgol, self.labels[:-1])

    def test_the_sensorgram_series_use_the_segments_for_every_display_mode(self) -> None:
        rng = np.random.default_rng(3)
        traces = {r: self.y + r + rng.normal(0.0, 0.2, 60) for r in (1, 2, 3)}
        base = dict(roi_ids=(1, 2, 3), formula_key="absorbance", metric=MetricSettings(), statistics=self.savgol, x=self.t)
        within = SensorgramRequest(**base, segments=self.labels)
        whole = SensorgramRequest(**base)
        for mode in ("individual", "average_all"):
            a = build_series(within, traces, mode, "mean", "sd")
            b = build_series(whole, traces, mode, "mean", "sd")
            self.assertFalse(np.allclose(a[0].y, b[0].y))  # the two curves differ (at the boundary)
        series = build_series(within, traces, "individual", "mean", "sd")
        np.testing.assert_array_equal(series[0].y, apply_statistics(self.t, traces[1], self.savgol, self.labels)[0])


class LineStepValuesTest(unittest.TestCase):
    """What each plotted line carries for the table: `build_series` with a step membership."""

    def setUp(self) -> None:
        self.first, self.last, self.t = _cubes(100)
        rng = np.random.default_rng(8)
        self.traces = {r: 640.0 + 10.0 * r + rng.normal(0.0, 0.1 * r, 100) for r in (1, 2, 3)}
        self.membership = ss.assign_cubes([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)
        self.base = dict(roi_ids=(1, 2, 3), formula_key="absorbance", metric=MetricSettings(), statistics=StatisticsSettings(), x=self.t)

    def _request(self, **extra) -> SensorgramRequest:
        return SensorgramRequest(**self.base, **extra)

    def test_no_membership_no_values(self) -> None:
        self.assertTrue(all(s.step_values is None for s in build_series(self._request(), self.traces, "individual", "mean", "sd")))

    def test_a_roi_line_carries_the_mean_and_sd_of_its_cubes(self) -> None:
        series = build_series(self._request(step_membership=self.membership), self.traces, "individual", "mean", "sd")
        for s, roi in zip(series, (1, 2, 3)):
            values = s.step_values
            self.assertFalse(values.between_rois)
            for step, cubes in enumerate((slice(0, 30), slice(30, 60))):
                self.assertEqual(values.n[step], 30)
                self.assertAlmostEqual(values.mean[step], self.traces[roi][cubes].mean(), places=10)
                self.assertAlmostEqual(values.sd[step], self.traces[roi][cubes].std(ddof=1), places=10)

    def test_an_average_line_carries_the_mean_and_sd_of_the_line_as_drawn(self) -> None:
        series = build_series(self._request(step_membership=self.membership), self.traces, "average_all", "mean", "sd")
        line, values = series[0], series[0].step_values
        self.assertFalse(values.between_rois)
        self.assertEqual(values.n.tolist(), [30, 30])  # cubes of the step, as for a ROI line
        self.assertAlmostEqual(values.mean[0], line.y[:30].mean(), places=10)
        self.assertAlmostEqual(values.sd[0], line.y[:30].std(ddof=1), places=10)
        self.assertLess(values.sd[0], 0.3)  # the noise of the average of three ROIs, not their spread (the ROIs are 10 nm apart)

    def test_the_numbers_of_an_average_line_follow_the_smoothing(self) -> None:
        """The reason the table was changed to the drawn line's own SD: the spread between ROIs cannot react to smoothing, this does."""
        smoothed = StatisticsSettings(smoothing_method="moving_average", smoothing_window=9)
        plain = build_series(self._request(step_membership=self.membership), self.traces, "average_all", "mean", "sd")[0].step_values
        request = SensorgramRequest(**{**self.base, "statistics": smoothed}, step_membership=self.membership)
        after = build_series(request, self.traces, "average_all", "mean", "sd")[0].step_values
        self.assertTrue((after.sd < plain.sd / 1.5).all())

    def test_the_spread_between_rois_is_still_there_when_asked_for(self) -> None:
        processed = np.vstack([self.traces[r] for r in (1, 2, 3)])
        stats = ss.step_statistics([1, 2, 3], processed, self.membership)
        values = ss.line_step_values(stats, (1, 2, 3), between_rois=True)
        roi_means = np.array([self.traces[r][:30].mean() for r in (1, 2, 3)])
        self.assertTrue(values.between_rois)
        self.assertEqual(values.n[0], 3)  # ROIs, not cubes
        self.assertAlmostEqual(values.mean[0], roi_means.mean(), places=10)
        self.assertAlmostEqual(values.sd[0], roi_means.std(ddof=1), places=10)

    def test_the_numbers_come_from_the_curve_that_is_drawn_smoothing_included(self) -> None:
        smoothed = StatisticsSettings(smoothing_method="savgol", smoothing_window=9, smoothing_polyorder=2)
        labels = ss.smoothing_segments([_step(1, 0, 300), _step(2, 300, 600)], self.first, self.last)
        request = SensorgramRequest(**{**self.base, "statistics": smoothed}, segments=labels, step_membership=self.membership)
        line = build_series(request, self.traces, "individual", "mean", "sd")[0]
        self.assertAlmostEqual(line.step_values.sd[0], line.y[:30].std(ddof=1), places=10)  # the SD of the plotted points of the step

    def test_by_group_every_group_line_has_a_mean_and_an_sd_even_a_group_of_one_roi(self) -> None:
        from lspr_imaging_app.analysis.scan_trace import GroupSpec

        request = self._request(step_membership=self.membership, groups=(GroupSpec("A", "#ff0000", (1,)), GroupSpec("B", "#00ff00", (2, 3))))
        a, b, *_ = build_series(request, self.traces, "average_by_group", "mean", "sd")
        for line in (a, b):
            self.assertTrue(np.isfinite(line.step_values.mean).all() and np.isfinite(line.step_values.sd).all())
        np.testing.assert_allclose(a.step_values.mean[0], self.traces[1][:30].mean())  # a group of one ROI is that ROI's own line

    def test_a_line_none_of_whose_rois_are_in_the_numbers_is_empty(self) -> None:
        stats = ss.step_statistics([1], np.zeros((1, 100)), self.membership)
        empty = ss.line_step_values(stats, (99,), between_rois=False)
        self.assertEqual(empty.n.tolist(), [0, 0])
        self.assertTrue(np.isnan(empty.mean).all())
        self.assertTrue(np.isnan(ss.line_step_values(stats, (98, 99), between_rois=True).mean).all())


class SmoothInsideStepsStatisticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first, self.last, self.t = _cubes(100)
        rng = np.random.default_rng(6)
        level = np.where(np.arange(100) < 30, 600.0, np.where(np.arange(100) < 60, 612.0, 590.0))
        self.traces = {r: level + rng.normal(0.0, 0.1, 100) for r in (1, 2, 3)}
        self.steps = [_step(1, 0, 300), _step(2, 300, 600), _step(3, 600, 1000)]

    def _noise(self, settings, steps=None) -> np.ndarray:
        stats = ss.compute_step_statistics(self.steps if steps is None else steps, [1, 2, 3], self.traces, self.t, settings, self.first, self.last)
        return stats.noise

    def test_smoothing_inside_steps_keeps_the_neighbours_levels_out_of_the_noise(self) -> None:
        within = StatisticsSettings(smoothing_method="savgol", smoothing_window=15, smoothing_polyorder=2, smooth_within_steps=True)
        across = StatisticsSettings(smoothing_method="savgol", smoothing_window=15, smoothing_polyorder=2, smooth_within_steps=False)
        plain = self._noise(StatisticsSettings())
        self.assertTrue((self._noise(across)[:, :2] > 3 * plain[:, :2]).all())  # smoothed across the jumps: the edges carry the other level
        self.assertTrue((self._noise(within) < plain).all())  # smoothed inside the steps: smoother than measured, as smoothing should be

    def test_the_switch_does_nothing_without_steps_or_without_smoothing(self) -> None:
        on = StatisticsSettings(smoothing_method="savgol", smooth_within_steps=True)
        none_on = StatisticsSettings(smooth_within_steps=True)
        none_off = StatisticsSettings(smooth_within_steps=False)
        np.testing.assert_array_equal(self._noise(none_on), self._noise(none_off))
        empty = ss.compute_step_statistics([], [1], self.traces, self.t, on, self.first, self.last)  # no steps: smooths the whole trace, no error
        self.assertEqual(empty.mean.shape, (1, 0))


if __name__ == "__main__":
    unittest.main()
