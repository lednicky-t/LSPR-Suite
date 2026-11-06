"""The numbers of the ROI / Groups table's step columns (`lspri_imaging_app.analysis.step_table`): they must be the numbers of the Sensorgram's lines, because they go
through the same code. Checked against `build_series` in the Sensorgram's own modes and against plain numpy. No Qt."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np

    from lspr_imaging_app.analysis import step_stats as ss
    from lspr_imaging_app.analysis import step_table as st
    from lspr_imaging_app.analysis.scan_trace import (
        DISPLAY_BY_GROUP,
        DISPLAY_INDIVIDUAL,
        GroupSpec,
        SensorgramRequest,
        build_series,
    )
    from lspr_imaging_app.analysis.settings import MetricSettings, StatisticsSettings
    from lspr_imaging_app.plan.edits import EffectiveStep
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

T0 = 1_700_000_000_000
PERIOD_S, CUBE_S = 10.0, 6.0


def _ms(seconds: float) -> int:
    return T0 + int(round(seconds * 1000))


def _step(number: int, start_s: float, end_s: float) -> EffectiveStep:
    return EffectiveStep(
        step=number, label=f"step {number}", start_unix_ms=_ms(start_s), end_unix_ms=_ms(end_s), color=None,
        measured_label=f"step {number}", measured_start_unix_ms=_ms(start_s), measured_end_unix_ms=_ms(end_s), order=number,
    )


def _setup(n_cubes: int = 90):
    t = np.arange(n_cubes) * PERIOD_S
    first, last = T0 + t * 1000.0, T0 + (t + CUBE_S) * 1000.0
    membership = ss.assign_cubes([_step(1, 0, 300), _step(2, 300, 600), _step(3, 600, 900)], first, last)
    rng = np.random.default_rng(7)
    levels = np.repeat([0.0, 1.5, 0.4], 30)  # nm: a baseline step, a binding step, a rinse
    traces = {r: 600.0 + 0.3 * r + levels + 0.05 * rng.standard_normal(n_cubes) for r in range(1, 7)}
    return t, membership, traces


class StepNumbersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t, self.membership, self.traces = _setup()
        self.stats = StatisticsSettings()

    def _numbers(self, rois, groups, statistics=None):
        return st.compute_step_numbers(rois, groups, self.traces, self.t, statistics or self.stats, None, self.membership)

    def test_a_roi_row_is_the_mean_and_sd_of_its_trace_in_each_step(self) -> None:
        got = self._numbers([2], []).rois[2]
        for step in range(3):
            values = self.traces[2][self.membership.used[step]]
            self.assertEqual(got.n[step], values.size)
            self.assertAlmostEqual(got.mean[step], float(values.mean()), places=9)
            self.assertAlmostEqual(got.sd[step], float(values.std(ddof=1)), places=9)

    def test_a_roi_row_equals_the_sensorgram_individual_line(self) -> None:
        request = SensorgramRequest((1, 2, 3), "", MetricSettings(), self.stats, self.t, (), {}, None, self.membership)
        lines = build_series(request, self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        got = self._numbers([1, 2, 3], []).rois
        for roi, line in zip((1, 2, 3), lines):
            np.testing.assert_array_equal(got[roi].mean, line.step_values.mean)
            np.testing.assert_array_equal(got[roi].sd, line.step_values.sd)

    def test_a_group_row_equals_the_sensorgram_average_by_group_line_for_either_centre(self) -> None:
        for centre in ("mean", "median"):
            stats = replace(self.stats, sensorgram_aggregation=centre)
            request = SensorgramRequest((1, 2, 3), "", MetricSettings(), stats, self.t, (GroupSpec("g", "#fff", (1, 2, 3)),), {}, None, self.membership)
            (line,) = build_series(request, self.traces, DISPLAY_BY_GROUP, centre, "sd")
            got = self._numbers([], [st.GroupMembers("gid", (1, 2, 3))], stats).groups["gid"]
            np.testing.assert_array_equal(got.mean, line.step_values.mean)
            np.testing.assert_array_equal(got.sd, line.step_values.sd)

    def test_a_group_row_is_the_sd_of_the_averaged_line_not_the_spread_between_rois(self) -> None:
        got = self._numbers([], [st.GroupMembers("gid", (1, 6))]).groups["gid"]
        centre = np.mean([self.traces[1], self.traces[6]], axis=0)
        values = centre[self.membership.used[0]]
        self.assertAlmostEqual(got.sd[0], float(values.std(ddof=1)), places=9)
        between = float(np.std([self.traces[1][self.membership.used[0]].mean(), self.traces[6][self.membership.used[0]].mean()], ddof=1))
        self.assertGreater(abs(between - got.sd[0]), 0.1)  # the two are different quantities; this data tells them apart

    def test_the_baseline_switch_changes_the_means_and_leaves_the_sds(self) -> None:
        absolute = self._numbers([3], [])
        # the baseline window is in the axis units (seconds here): the first 100 s are inside step 1
        shifted = self._numbers([3], [], replace(self.stats, baseline_enabled=True, baseline_window_start=0.0, baseline_window_end=100.0))
        self.assertAlmostEqual(shifted.rois[3].mean[0], 0.0, delta=0.05)  # the baseline step reads about zero
        self.assertAlmostEqual(shifted.rois[3].mean[1], 1.5, delta=0.05)  # and the binding step reads as the shift
        np.testing.assert_allclose(shifted.rois[3].sd, absolute.rois[3].sd, atol=1e-9)

    def test_a_group_is_averaged_over_all_its_members_and_a_roi_in_two_groups_counts_in_both(self) -> None:
        both = self._numbers([], [st.GroupMembers("a", (1, 2)), st.GroupMembers("b", (2, 3))]).groups
        alone = self._numbers([], [st.GroupMembers("b", (2, 3))]).groups["b"]
        np.testing.assert_array_equal(both["b"].mean, alone.mean)  # ROI 2 was not given to group a only

    def test_the_ungrouped_row_is_the_average_of_the_rois_in_no_group(self) -> None:
        got = self._numbers([], [st.GroupMembers(st.UNGROUPED, (4, 5))]).groups[st.UNGROUPED]
        request = SensorgramRequest((4, 5), "", MetricSettings(), self.stats, self.t, (), {}, None, self.membership)
        (line,) = build_series(request, self.traces, DISPLAY_BY_GROUP, "mean", "sd")
        np.testing.assert_array_equal(got.mean, line.step_values.mean)

    def test_a_group_without_members_has_no_row_and_nothing_asked_gives_nothing(self) -> None:
        self.assertEqual(self._numbers([], [st.GroupMembers("empty", ())]).groups, {})
        result = self._numbers([], [])
        self.assertEqual((result.rois, result.groups), ({}, {}))

    def test_the_row_knows_its_steps_and_answers_nan_for_a_step_it_was_not_computed_for(self) -> None:
        row = self._numbers([1], []).rois[1]
        refs = tuple(step.ref for step in self.membership.steps)
        self.assertEqual(row.refs, refs)
        n, mean, sd = row.at(refs[1])
        self.assertEqual(n, 30)
        self.assertAlmostEqual(mean, float(self.traces[1][self.membership.used[1]].mean()))
        n, mean, sd = row.at(("a", 99))
        self.assertEqual(n, 0)
        self.assertTrue(np.isnan(mean) and np.isnan(sd))

    def test_a_row_carries_the_noise_of_each_step_the_sd_about_a_straight_line(self) -> None:
        row = self._numbers([1], []).rois[1]
        for step in range(3):
            values = self.traces[1][self.membership.used[step]]
            t = np.arange(values.size, dtype=float) * PERIOD_S
            fit = np.polyval(np.polyfit(t, values, 1), t)
            expected = float(np.sqrt(np.sum((values - fit) ** 2) / (values.size - 2)))
            self.assertAlmostEqual(float(row.noise[step]), expected, places=9)
        group = self._numbers([], [st.GroupMembers("g", (1, 2))]).groups["g"]
        self.assertTrue(np.all(np.isfinite(group.noise)))  # the group's averaged line has a noise too

    def test_the_numbers_do_not_depend_on_the_order_the_rows_are_asked_in(self) -> None:
        forward = self._numbers([1, 2, 3, 4], [st.GroupMembers("g", (1, 2, 3))])
        backward = self._numbers([4, 3, 2, 1], [st.GroupMembers("g", (3, 2, 1))])
        for roi in (1, 2, 3, 4):
            np.testing.assert_array_equal(forward.rois[roi].mean, backward.rois[roi].mean)
        np.testing.assert_allclose(forward.groups["g"].mean, backward.groups["g"].mean, atol=1e-12)


class NumbersKeyTest(unittest.TestCase):
    def test_settings_that_cannot_change_a_number_do_not_change_the_key(self) -> None:
        base = StatisticsSettings()
        self.assertEqual(st.numbers_key(base, group=False), st.numbers_key(replace(base, sensorgram_display_mode="average_by_group", sensorgram_band="sem"), group=False))
        self.assertEqual(st.numbers_key(base, group=True), st.numbers_key(replace(base, sensorgram_display_mode="average_all", sensorgram_band="sem"), group=True))

    def test_the_centre_matters_to_a_group_and_not_to_a_roi(self) -> None:
        base, median = StatisticsSettings(), replace(StatisticsSettings(), sensorgram_aggregation="median")
        self.assertEqual(st.numbers_key(base, group=False), st.numbers_key(median, group=False))
        self.assertNotEqual(st.numbers_key(base, group=True), st.numbers_key(median, group=True))

    def test_smoothing_and_baseline_change_the_key(self) -> None:
        base = StatisticsSettings()
        for changed in (replace(base, smoothing_method="savgol"), replace(base, baseline_enabled=True, baseline_window_start=0.0, baseline_window_end=1.0), replace(base, spike_rejection_enabled=True)):
            self.assertNotEqual(st.numbers_key(base, group=False), st.numbers_key(changed, group=False))


if __name__ == "__main__":
    unittest.main()
