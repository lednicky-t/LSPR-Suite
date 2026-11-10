"""The Sensorgram's drift baseline (`lspr_imaging_app.analysis.baseline_drift`, 2026-11-09): anchor points clicked on the plot, the shapes through
them, and the same numbers in the Sensorgram's lines and the ROI table's step columns. No Qt."""

from __future__ import annotations

import sys
import unittest
from dataclasses import asdict, replace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np

    from lspr_imaging_app.analysis import baseline_drift as bd
    from lspr_imaging_app.analysis import step_stats as ss
    from lspr_imaging_app.analysis import step_table as st
    from lspr_imaging_app.analysis.scan_trace import DISPLAY_BY_GROUP, DISPLAY_INDIVIDUAL, GroupSpec, SensorgramRequest, build_series
    from lspr_imaging_app.analysis.settings import MetricSettings, StatisticsSettings
    from lspr_imaging_app.analysis.statistics import apply_statistics, apply_statistics_with_fit, normalize_to_baseline_window
    from lspr_imaging_app.plan.edits import EffectiveStep
    from lspr_imaging_app.storage.session import _decode_settings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

T0 = 1_700_000_000_000
PERIOD_S, CUBE_S = 10.0, 6.0
LABELS = ("H2O", "sample", "H2O", "sample", "H2O")  # buffer, binding, buffer, binding, buffer: 20 cubes each
DRIFT_NM_PER_S = 0.0004
LEVELS = (0.0, 1.5, 0.0, 0.8, 0.0)
CUBES = np.arange(100, 200)  # cube numbers of the file positions (not starting at 0 on purpose)
BUFFER_POINTS = ((0, 115), (0, 155), (0, 195))  # one shared point late in each buffer step (positions 15, 55, 95)


def _ms(seconds: float) -> int:
    return T0 + int(round(seconds * 1000))


def _step(number: int, label: str, start_s: float, end_s: float) -> EffectiveStep:
    return EffectiveStep(
        step=number, label=label, start_unix_ms=_ms(start_s), end_unix_ms=_ms(end_s), color=None,
        measured_label=label, measured_start_unix_ms=_ms(start_s), measured_end_unix_ms=_ms(end_s), order=number,
    )


def _setup(noise: float = 0.0):
    n = 20 * len(LABELS)
    t = np.arange(n) * PERIOD_S
    first, last = T0 + t * 1000.0, T0 + (t + CUBE_S) * 1000.0
    steps = [_step(i + 1, label, 200.0 * i, 200.0 * (i + 1)) for i, label in enumerate(LABELS)]
    membership = ss.assign_cubes(steps, first, last)  # only to check the step means; the anchors are points
    levels = np.repeat(LEVELS, 20)
    rng = np.random.default_rng(3)
    traces = {r: 600.0 + 0.3 * r + levels + DRIFT_NM_PER_S * t * r + noise * rng.standard_normal(n) for r in (1, 2, 3)}  # each ROI its own drift
    return t, membership, traces


def _anchor_settings(**changes) -> StatisticsSettings:
    return replace(StatisticsSettings(), **{
        "baseline_enabled": True, "baseline_method": bd.METHOD_ANCHORS, "baseline_anchor_points": BUFFER_POINTS, "baseline_point_halfwidth": 3, **changes,
    })


class FitDriftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.x = np.arange(100, dtype=float)
        self.regions = [np.isin(np.arange(100), np.arange(a, a + 10)) for a in (0, 45, 90)]

    def test_linear_drift_is_removed_exactly(self) -> None:
        y = 3.0 + 0.02 * self.x
        fit = bd.fit_drift(self.x, y, self.regions, "linear")
        self.assertTrue(fit.ok)
        np.testing.assert_allclose(y - fit.curve, 0.0, atol=1e-12)
        self.assertAlmostEqual(fit.anchor_rms, 0.0, places=12)

    def test_a_quadratic_through_three_anchors_is_removed_exactly(self) -> None:
        y = 1.0 + 0.01 * self.x - 1e-4 * self.x**2
        fit = bd.fit_drift(self.x, y, self.regions, "quadratic")
        # one anchor = the mean of its region, so a curved region's anchor sits slightly off the curve; tolerance from the curvature over 10 points
        np.testing.assert_allclose(y - fit.curve, 0.0, atol=1e-3)

    def test_constant_is_the_mean_of_the_anchor_levels(self) -> None:
        y = np.zeros(100)
        y[0:10], y[45:55], y[90:100] = 1.0, 2.0, 6.0
        fit = bd.fit_drift(self.x, y, self.regions, "constant")
        np.testing.assert_allclose(fit.curve, 3.0)
        self.assertAlmostEqual(fit.level, 3.0)

    def test_too_few_anchors_leave_no_curve_and_say_why(self) -> None:
        fit = bd.fit_drift(self.x, np.ones(100), self.regions[:2], "cubic")
        self.assertFalse(fit.ok)
        self.assertIn("cubic needs 4", fit.message)
        self.assertTrue(np.isnan(fit.curve).all())
        none = bd.fit_drift(self.x, np.ones(100), [], "constant")
        self.assertFalse(none.ok)
        self.assertIn("no anchors", none.message)

    def test_an_anchor_without_values_does_not_count(self) -> None:
        y = np.ones(100)
        y[45:55] = np.nan
        fit = bd.fit_drift(self.x, y, self.regions, "quadratic")
        self.assertIn("found 2", fit.message)

    def test_pchip_passes_through_every_anchor_and_is_flat_beyond_the_ends(self) -> None:
        regions = [np.isin(np.arange(100), np.arange(a, a + 9)) for a in (0, 45, 90)]  # 9 points each: the anchors sit on x = 4, 49, 94
        y = np.zeros(100)
        y[0:9], y[45:54], y[90:99] = 1.0, 3.0, 2.0
        fit = bd.fit_drift(self.x, y, regions, "pchip")
        np.testing.assert_allclose(fit.curve[[4, 49, 94]], fit.anchors_y, atol=1e-12)
        self.assertAlmostEqual(fit.curve[0], 1.0)  # left of the first anchor
        self.assertAlmostEqual(fit.curve[-1], 2.0)
        self.assertLessEqual(fit.curve.max(), 3.0 + 1e-12)  # no overshoot

    def test_a_cube_without_x_gets_no_baseline(self) -> None:
        x = self.x.copy()
        x[50] = np.nan
        fit = bd.fit_drift(x, 0.01 * self.x, self.regions, "linear")
        self.assertTrue(np.isnan(fit.curve[50]))
        self.assertTrue(np.isfinite(np.delete(fit.curve, 50)).all())

    def test_an_unknown_shape_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            bd.fit_drift(self.x, self.x, self.regions, "spline")


class AnchorPointsTest(unittest.TestCase):
    def test_a_point_averages_the_cubes_around_it_in_file_order(self) -> None:
        (region,) = bd.point_regions(CUBES, [150], 2)
        np.testing.assert_array_equal(np.flatnonzero(region), [48, 49, 50, 51, 52])
        (edge,) = bd.point_regions(CUBES, [100], 3)  # cut at the start of the file
        np.testing.assert_array_equal(np.flatnonzero(edge), [0, 1, 2, 3])
        self.assertEqual(bd.point_regions(CUBES, [999], 2), ())  # not in the file: no anchor

    def test_a_roi_has_the_shared_points_and_its_own(self) -> None:
        points = ((0, 120), (2, 180), (3, 150), (0, 180))
        self.assertEqual(bd.points_for(2, points), (120, 180))
        self.assertEqual(bd.points_for(1, points), (120, 180))
        self.assertEqual(bd.points_for(3, points), (120, 150, 180))
        regions = bd.regions_by_roi((1, 3), CUBES, points, 1)
        self.assertEqual((len(regions[1]), len(regions[3])), (2, 3))


class ApplyStatisticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t, self.membership, self.traces = _setup()
        self.regions = bd.point_regions(CUBES, [c for _r, c in BUFFER_POINTS], 3)

    def test_the_anchors_method_removes_the_drift_and_keeps_the_responses(self) -> None:
        trace, level = apply_statistics(self.t, self.traces[2], _anchor_settings(baseline_shape="linear"), None, self.regions)
        for step, response in enumerate(LEVELS):
            self.assertAlmostEqual(float(np.mean(trace[self.membership.used[step]])), response, places=9)
        self.assertTrue(np.isfinite(level))

    def test_the_window_method_is_unchanged(self) -> None:
        stats = replace(StatisticsSettings(), baseline_enabled=True, baseline_window_start=0.0, baseline_window_end=100.0)
        got, value = apply_statistics(self.t, self.traces[1], stats, None, None)  # no anchor steps: the window's mean, bit for bit
        want, want_value = normalize_to_baseline_window(self.t, self.traces[1], 0.0, 100.0)
        np.testing.assert_array_equal(got, want)
        self.assertEqual(value, want_value)

    def test_switched_off_the_trace_is_absolute_but_the_curve_is_made(self) -> None:
        stats = replace(_anchor_settings(), baseline_enabled=False)
        trace, value, fit = apply_statistics_with_fit(self.t, self.traces[1], stats, None, self.regions)
        np.testing.assert_array_equal(trace, self.traces[1])
        self.assertTrue(np.isnan(value))
        self.assertTrue(fit is not None and fit.ok)

    def test_too_few_anchors_leave_the_trace_uncorrected(self) -> None:
        trace, value = apply_statistics(self.t, self.traces[1], _anchor_settings(baseline_shape="cubic"), None, self.regions)
        np.testing.assert_array_equal(trace, self.traces[1])
        self.assertTrue(np.isnan(value))


class SeriesAndTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t, self.membership, self.traces = _setup(noise=0.02)

    def _request(self, stats, rois=(1, 2, 3), groups=(), cube_numbers=CUBES):
        return SensorgramRequest(tuple(rois), "", MetricSettings(), stats, self.t, tuple(groups), {}, None, self.membership, cube_numbers=cube_numbers)

    def test_points_correct_the_drift_of_each_roi(self) -> None:
        lines = build_series(self._request(_anchor_settings(baseline_shape="linear")), self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        for line in lines:
            self.assertEqual(line.baseline_note, "")
            for step in (0, 2, 4):  # the buffer steps read about zero again: each ROI's own drift is gone
                self.assertLess(abs(float(np.mean(line.y[self.membership.used[step]]))), 0.03)

    def test_a_roi_with_its_own_point_uses_it(self) -> None:
        stats = _anchor_settings(baseline_shape="constant", baseline_anchor_points=((0, 115), (2, 135)))  # ROI 2 also anchored in a binding step
        lines = build_series(self._request(stats), self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        by_roi = {line.member_ids[0]: line for line in lines}
        self.assertEqual((by_roi[1].anchor_x.size, by_roi[2].anchor_x.size), (1, 2))
        self.assertNotAlmostEqual(by_roi[1].baseline - 0.3, by_roi[2].baseline - 0.6, places=2)

    def test_an_average_combines_the_members_curves_as_it_combines_their_traces(self) -> None:
        stats = _anchor_settings(baseline_enabled=False)
        lines = build_series(self._request(stats, groups=(GroupSpec("g", "#fff", (1, 2, 3)),)), self.traces, DISPLAY_BY_GROUP, "mean", "sd")
        alone = build_series(self._request(stats), self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        np.testing.assert_allclose(lines[0].baseline_curve, np.mean([s.baseline_curve for s in alone], axis=0))
        self.assertEqual(lines[0].anchor_x.size, 3)  # every member has the same shared points: the squares are drawn

    def test_too_few_points_are_still_on_the_line_to_be_drawn(self) -> None:
        stats = _anchor_settings(baseline_shape="linear", baseline_anchor_points=((0, 115),))
        lines = build_series(self._request(stats, groups=(GroupSpec("g", "#fff", (1, 2)),)), self.traces, DISPLAY_BY_GROUP, "mean", "sd")
        self.assertIn("linear needs 2", lines[0].baseline_note)
        self.assertEqual(lines[0].anchor_x.size, 1)
        self.assertIsNone(lines[0].anchor_base)  # no fit: the trace is uncorrected and the square sits on it
        self.assertIsNone(lines[0].baseline_curve)

    def test_no_points_give_a_note_and_an_uncorrected_line(self) -> None:
        line = build_series(self._request(_anchor_settings(baseline_anchor_points=()), rois=(1,)), self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")[0]
        np.testing.assert_array_equal(line.y, self.traces[1])
        self.assertIn("no anchors", line.baseline_note)

    def test_the_step_columns_equal_the_sensorgram_lines(self) -> None:
        stats = _anchor_settings(baseline_shape="linear", baseline_anchor_points=(*BUFFER_POINTS, (2, 135)))
        lines = build_series(self._request(stats), self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        numbers = st.compute_step_numbers((1, 2, 3), (), self.traces, self.t, stats, None, self.membership, CUBES)
        for line in lines:
            got = numbers.rois[line.member_ids[0]]
            np.testing.assert_allclose(got.mean, line.step_values.mean, rtol=0, atol=1e-12)
            np.testing.assert_allclose(got.sd, line.step_values.sd, rtol=0, atol=1e-12)


class RangeAnchorStepsTest(unittest.TestCase):
    """The Range ("window") method with plan steps marked as baseline anchors (2026-11-09)."""

    def setUp(self) -> None:
        self.t, self.membership, self.traces = _setup(noise=0.02)
        self.water = tuple(tuple(self.membership.steps[i].ref) for i in (0, 2, 4))

    def _stats(self, **changes) -> StatisticsSettings:
        return replace(StatisticsSettings(), **{
            "baseline_enabled": True, "baseline_method": bd.METHOD_WINDOW, "baseline_anchor_steps": self.water, "baseline_shape": "linear", **changes,
        })

    def test_marked_steps_minus_the_border_offset_are_the_regions(self) -> None:
        regions = bd.step_regions(self.membership, self.water, 3)
        self.assertEqual(len(regions), 3)
        cubes = np.flatnonzero(self.membership.used[2])
        np.testing.assert_array_equal(np.flatnonzero(regions[1]), cubes[3:-3])  # 3 cubes off each border
        self.assertEqual(bd.step_regions(self.membership, self.water[:1], 10), ())  # 20 cubes - 2 x 10: nothing left
        self.assertEqual(bd.step_regions(self.membership, (), 0), ())

    def test_no_marked_step_keeps_the_window_mean(self) -> None:
        stats = self._stats(baseline_anchor_steps=())
        self.assertFalse(bd.uses_anchor_steps(stats))
        self.assertIsNone(bd.window_step_regions(self.membership, stats))

    def test_marked_steps_remove_each_rois_drift(self) -> None:
        stats = self._stats()
        request = SensorgramRequest((1, 2, 3), "", MetricSettings(), stats, self.t, (), {}, None, self.membership,
                                    baseline_step_regions=bd.window_step_regions(self.membership, stats))
        for line in build_series(request, self.traces, DISPLAY_INDIVIDUAL, "mean", "sd"):
            self.assertEqual(line.baseline_note, "")
            self.assertEqual(line.anchor_x.size, 3)
            for step in (0, 2, 4):
                self.assertLess(abs(float(np.mean(line.y[self.membership.used[step]]))), 0.03)

    def test_marked_but_nothing_left_says_so_and_does_not_fall_back_to_the_window(self) -> None:
        stats = self._stats(baseline_step_trim=10)
        request = SensorgramRequest((1,), "", MetricSettings(), stats, self.t, (), {}, None, self.membership,
                                    baseline_step_regions=bd.window_step_regions(self.membership, stats))
        line = build_series(request, self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")[0]
        self.assertIn("no anchors", line.baseline_note)
        np.testing.assert_array_equal(line.y, self.traces[1])

    def test_the_step_columns_use_the_same_anchor_steps(self) -> None:
        stats = self._stats(baseline_step_trim=4)
        request = SensorgramRequest((1, 2, 3), "", MetricSettings(), stats, self.t, (), {}, None, self.membership,
                                    baseline_step_regions=bd.window_step_regions(self.membership, stats))
        lines = build_series(request, self.traces, DISPLAY_INDIVIDUAL, "mean", "sd")
        numbers = st.compute_step_numbers((1, 2, 3), (), self.traces, self.t, stats, None, self.membership)
        for line in lines:
            np.testing.assert_allclose(numbers.rois[line.member_ids[0]].mean, line.step_values.mean, rtol=0, atol=1e-12)

    def test_the_marked_steps_survive_a_session_round_trip(self) -> None:
        import json

        stats = self._stats(baseline_step_trim=5)
        raw = json.loads(json.dumps(asdict(stats)))  # the refs become lists in the file
        back = _decode_settings(raw, StatisticsSettings())
        self.assertEqual(back, stats)
        self.assertEqual(set(back.baseline_anchor_steps), set(self.water))


class AnchorCubesTest(unittest.TestCase):
    """The anchor regions as cube numbers (2026-11-10): how a job that scans only some of the cubes (ROI position tuning) fits the same curve."""

    def setUp(self) -> None:
        self.t, self.membership, self.traces = _setup(noise=0.02)
        self.water = tuple(tuple(self.membership.steps[i].ref) for i in (0, 2, 4))
        self.steps = replace(StatisticsSettings(), baseline_enabled=True, baseline_method=bd.METHOD_WINDOW, baseline_anchor_steps=self.water)

    def test_a_curve_is_subtracted_only_with_subtract_on_and_anchors_in_use(self) -> None:
        self.assertTrue(bd.curve_subtracted(_anchor_settings()))
        self.assertTrue(bd.curve_subtracted(self.steps))
        self.assertFalse(bd.curve_subtracted(replace(self.steps, baseline_enabled=False)))
        self.assertFalse(bd.curve_subtracted(replace(self.steps, baseline_anchor_steps=())))  # the range's mean: one number, no curve
        self.assertFalse(bd.curve_subtracted(_anchor_settings(baseline_enabled=False)))

    def test_no_curve_gives_no_anchor_cubes(self) -> None:
        self.assertIsNone(bd.anchor_cubes_by_roi((1, 2), CUBES, replace(self.steps, baseline_anchor_steps=()), self.membership))
        self.assertEqual(bd.cubes_needed(None), frozenset())

    def test_anchor_points_become_the_cubes_they_average_per_roi(self) -> None:
        stats = _anchor_settings(baseline_anchor_points=((0, 115), (2, 150)), baseline_point_halfwidth=1)
        cubes = bd.anchor_cubes_by_roi((1, 2), CUBES, stats, None)
        self.assertEqual(cubes[1], ((114, 115, 116),))  # the shared point only
        self.assertEqual(cubes[2], ((114, 115, 116), (149, 150, 151)))  # and ROI 2's own
        self.assertEqual(bd.cubes_needed(cubes), frozenset({114, 115, 116, 149, 150, 151}))

    def test_marked_steps_become_their_cubes_minus_the_border_offset(self) -> None:
        cubes = bd.anchor_cubes_by_roi((1, 3), CUBES, replace(self.steps, baseline_step_trim=3), self.membership)
        self.assertEqual(cubes[1], cubes[3])  # steps are the same for every ROI
        self.assertEqual([region[0] for region in cubes[1]], [103, 143, 183])  # steps of 20 cubes from cube 100: 3 cubes off the border
        self.assertEqual([len(region) for region in cubes[1]], [14, 14, 14])

    def test_a_trace_cut_to_some_cubes_gets_the_same_correction_as_the_whole_trace(self) -> None:
        """The property the ROI position tuning relies on: an anchor is the mean of its region, so the curve at a cube depends only on the anchor
        cubes. Fitted on the whole trace or on the anchor cubes plus any others, a cube's corrected value is the same."""
        for stats in (replace(self.steps, baseline_shape="linear"), replace(self.steps, baseline_shape="pchip"), _anchor_settings(baseline_shape="quadratic")):
            anchor_cubes = bd.anchor_cubes_by_roi((1, 2, 3), CUBES, stats, self.membership)
            scanned = np.array(sorted(bd.cubes_needed(anchor_cubes) | set(range(120, 140))))  # the anchors and one binding step
            positions = scanned - CUBES[0]
            cut = bd.regions_from_cubes(scanned, anchor_cubes)
            whole = bd.regions_from_cubes(CUBES, anchor_cubes)
            for roi in (1, 2, 3):
                full, _ = apply_statistics(self.t, self.traces[roi], stats, None, whole[roi])
                part, _ = apply_statistics(self.t[positions], self.traces[roi][positions], stats, None, cut[roi])
                self.assertTrue(np.isfinite(part).all())
                np.testing.assert_allclose(part, full[positions], rtol=0, atol=1e-9)

    def test_an_anchor_cube_that_was_not_scanned_is_simply_not_in_the_region(self) -> None:
        cubes = bd.anchor_cubes_by_roi((1,), CUBES, _anchor_settings(baseline_anchor_points=((0, 115),), baseline_point_halfwidth=1), None)
        (region,) = bd.regions_from_cubes(np.array([110, 115, 116, 190]), cubes)[1]
        np.testing.assert_array_equal(region, [False, True, True, False])


class SettingsTest(unittest.TestCase):
    def test_the_settings_survive_a_session_round_trip(self) -> None:
        stats = _anchor_settings(baseline_shape="pchip", baseline_anchor_points=((0, 12), (3, 40), (0, 12)), baseline_point_halfwidth=4)
        import json

        raw = json.loads(json.dumps(asdict(stats)))  # as the session file holds it: the pairs become lists
        self.assertEqual(_decode_settings(raw, StatisticsSettings()), stats)
        self.assertEqual(stats.baseline_anchor_points, ((0, 12), (3, 40)))  # each once

    def test_an_old_session_reads_as_the_window_method(self) -> None:
        old = {"baseline_enabled": True, "baseline_window_start": 1.0, "baseline_window_end": 5.0}
        self.assertEqual(_decode_settings(old, StatisticsSettings()).baseline_method, bd.METHOD_WINDOW)

    def test_a_session_saved_with_the_step_anchors_reads_as_points(self) -> None:
        """A session saved during the step-anchor days (2026-11-09) loads: the method is renamed, the step keys are dropped."""
        old = {"baseline_method": "steps", "baseline_anchor_label": "H2O", "baseline_settle_percent": 40, "baseline_anchor_points": [[0, 7]]}
        stats = _decode_settings(old, StatisticsSettings())
        self.assertEqual((stats.baseline_method, stats.baseline_anchor_points), (bd.METHOD_ANCHORS, ((0, 7),)))


if __name__ == "__main__":
    unittest.main()
