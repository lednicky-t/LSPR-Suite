"""The pure model behind the Sensorgram's plan bar (`lspri_imaging_app.plan.bar_model`): the levels of the traces inside a step, colours, tooltips, where the text goes and
how much room the bar needs. No Qt."""

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

    from lspr_core import ImagingPlan, ImagingPlanStep
    from lspr_imaging_app.plan import bar_model as bm
    from lspr_imaging_app.plan.axis_map import AxisMap
    from lspr_imaging_app.plan.edits import apply_edits
    from lspr_imaging_app.plan.label_rule import MODE_SOLUTION, MODE_WHOLE
    from lspr_imaging_app.plan.state import PlanEditsState
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _settings(**changes) -> bm.PlanBarSettings:
    return bm.PlanBarSettings(**changes)


class SettingsTest(unittest.TestCase):
    def test_height_is_a_percentage_up_to_ten_then_full(self) -> None:
        self.assertEqual(_settings(height_stop=1).height_fraction, 0.01)
        self.assertEqual(_settings(height_stop=10).height_fraction, 0.10)
        full = _settings(height_stop=bm.HEIGHT_FULL)
        self.assertTrue(full.is_full)
        self.assertEqual(full.height_fraction, 1.0)
        self.assertFalse(_settings(height_stop=10).is_full)

    def test_the_stop_has_a_name_in_each_mode(self) -> None:
        self.assertEqual(_settings(text_align=bm.ALIGN_BAR, text_stop=5).stop_name(), "Top of the bar")
        self.assertEqual(_settings(text_align=bm.ALIGN_DATA, text_stop=5).stop_name(), "Far above the data")
        self.assertEqual(_settings(text_align=bm.ALIGN_DATA, text_stop=3).stop_name(), "On the data (median)")


class SectionLevelsTest(unittest.TestCase):
    def test_percentiles_of_all_lines_inside_the_half_open_range(self) -> None:
        x = np.arange(10.0)
        a, b = (x, 100.0 + x), (x, 200.0 + x)
        levels = bm.section_levels([a, b], 2.0, 6.0)  # x = 2, 3, 4, 5 of both lines
        self.assertEqual(levels.median, np.median([102, 103, 104, 105, 202, 203, 204, 205]))
        self.assertAlmostEqual(levels.low, np.percentile([102, 103, 104, 105, 202, 203, 204, 205], 2))
        self.assertAlmostEqual(levels.high, np.percentile([102, 103, 104, 105, 202, 203, 204, 205], 98))

    def test_one_outlier_barely_moves_the_levels(self) -> None:
        x = np.arange(200.0)
        y = np.full(200, 600.0)
        y[10] = 900.0  # a stray point
        levels = bm.section_levels([(x, y)], 0.0, 200.0)
        self.assertEqual((levels.low, levels.median, levels.high), (600.0, 600.0, 600.0))

    def test_gaps_and_nan_are_ignored_and_an_empty_step_has_no_levels(self) -> None:
        x = np.arange(5.0)
        y = np.array([1.0, np.nan, 3.0, np.inf, 5.0])
        levels = bm.section_levels([(x, y)], 0.0, 5.0)
        self.assertEqual(levels.median, 3.0)
        self.assertIsNone(bm.section_levels([(x, y)], 10.0, 20.0))
        self.assertIsNone(bm.section_levels([], 0.0, 5.0))


class ColorTest(unittest.TestCase):
    def test_the_same_solution_gets_the_same_colour_whatever_follows_the_separator(self) -> None:
        colors = bm.automatic_colors(["H2O_--_ch1F", "10perEG_-_ch1F", "H2O_--_ch2P", "20perEG_-_ch1F"])
        self.assertEqual(colors[0], colors[2])
        self.assertEqual(len({colors[0], colors[1], colors[3]}), 3)
        self.assertEqual(colors[:2], [bm.STEP_COLORS[0], bm.STEP_COLORS[1]])

    def test_case_is_ignored_and_the_palette_cycles(self) -> None:
        self.assertEqual(len(set(bm.automatic_colors(["Water", "WATER"]))), 1)
        colors = bm.automatic_colors([f"s{i}" for i in range(len(bm.STEP_COLORS) + 1)])
        self.assertEqual(colors[0], colors[-1])

    def test_text_colour_follows_the_blended_fill(self) -> None:
        self.assertEqual(bm.contrast_text_color("#ffffff", 1.0, "#000000"), "#0f1720")  # light fill: dark text
        self.assertEqual(bm.contrast_text_color("#000000", 1.0, "#ffffff"), "#f8fbff")
        self.assertEqual(bm.contrast_text_color("#ffffff", 0.0, "#101010"), "#f8fbff")  # fully transparent: judged on the plot background
        self.assertEqual(bm.contrast_text_color("#ffffff", 0.1, "#101010"), "#f8fbff")  # a faint light fill on a dark plot stays light text


class DevicesAndStepsTest(unittest.TestCase):
    def test_devices_are_described_and_odd_input_is_left_out(self) -> None:
        lines = bm.describe_devices({
            "channels": [{"channel": 1, "flow_ul_min": 40.0, "direction": "CCW"}, {"channel": 2, "flow_ul_min": "x"}, "junk"],
            "valves": {"V1": "Open", "V2": "Close"},
        })
        self.assertEqual(lines, ["Pump: ch1 40 µL/min CCW", "Valves: V1 Open, V2 Close"])
        self.assertEqual(bm.describe_devices(None), [])
        self.assertEqual(bm.describe_devices({"channels": 5, "valves": []}), [])

    def test_steps_are_placed_on_the_axis_with_label_colour_and_tooltip(self) -> None:
        plan = ImagingPlan(steps=[
            ImagingPlanStep(step=1, label="H2O_--_ch1F", start_unix_ms=10_000, end_unix_ms=30_000, devices={"valves": {"V1": "Open"}}),
            ImagingPlanStep(step=2, label="10perEG_-_ch1F", start_unix_ms=30_000, end_unix_ms=50_000, color="#112233"),
        ])
        steps = apply_edits(plan, PlanEditsState()).steps
        axis = AxisMap.build(list(range(0, 60_001, 2_000)), [ms / 1000.0 for ms in range(0, 60_001, 2_000)])  # Time mode, seconds
        x = np.arange(0.0, 61.0, 2.0)
        bars = bm.build_bar_steps(steps, axis, [(x, 600.0 + x)], text_mode=MODE_SOLUTION, time_mode=True)
        self.assertEqual([(b.index, b.x_start, b.x_end, b.text) for b in bars], [(0, 10.0, 30.0, "H2O"), (1, 30.0, 50.0, "10perEG")])
        self.assertEqual((bars[0].color, bars[1].color), (bm.STEP_COLORS[0], "#112233"))  # the plan file's colour wins over the automatic one
        self.assertIn("10.0 s to 30.0 s (20.0 s)", bars[0].tooltip)
        self.assertIn("Valves: V1 Open", bars[0].tooltip)
        self.assertEqual(bars[0].levels.median, np.median(600.0 + np.arange(10.0, 30.0, 2.0)))
        self.assertFalse(bars[0].edited)

    def test_whole_note_and_cube_mode_tooltip(self) -> None:
        plan = ImagingPlan(steps=[ImagingPlanStep(step=1, label="H2O_--_ch1F", start_unix_ms=10_000, end_unix_ms=30_000)])
        steps = apply_edits(plan, PlanEditsState()).steps
        axis = AxisMap.build(list(range(0, 60_001, 2_000)), list(range(31)))  # Cube mode
        bar = bm.build_bar_steps(steps, axis, [], text_mode=MODE_WHOLE, time_mode=False)[0]
        self.assertEqual((bar.text, bar.x_start, bar.x_end, bar.levels), ("H2O_--_ch1F", 5.0, 15.0, None))
        self.assertIn("cube 5.0 to cube 15.0", bar.tooltip)


def _bars(*ranges) -> list[bm.PlanBarStep]:
    return [bm.PlanBarStep(index=i, x_start=a, x_end=b, text="", color="#ffffff", tooltip="", levels=None) for i, (a, b) in enumerate(ranges)]


class GrabEdgeTest(unittest.TestCase):
    """Only selected steps' edges are ever grabbed, so two steps that meet at one place cannot compete."""

    def test_the_nearer_edge_of_a_selected_step_within_the_tolerance_is_found(self) -> None:
        steps = _bars((2.0, 6.0))
        self.assertEqual(bm.grab_edge(steps, {0}, 2.1, 0.3), (0, "start", False))
        self.assertEqual(bm.grab_edge(steps, {0}, 5.8, 0.3), (0, "end", False))
        self.assertEqual(bm.grab_edge(steps, {0}, 2.0, 0.0), (0, "start", False))
        self.assertEqual(bm.grab_edge(steps, {0}, 6.0, 0.0), (0, "end", False))

    def test_nothing_inside_the_step_outside_the_tolerance_or_without_a_selection(self) -> None:
        steps = _bars((2.0, 6.0))
        for x in (4.0, 2.5, 1.6):
            self.assertIsNone(bm.grab_edge(steps, {0}, x, 0.3), x)
        self.assertIsNone(bm.grab_edge(steps, set(), 2.0, 0.3))
        self.assertIsNone(bm.grab_edge(steps, {7}, 2.0, 0.3))

    def test_a_step_narrower_than_the_tolerance_gives_the_nearer_edge(self) -> None:
        steps = _bars((2.0, 2.2))
        self.assertEqual(bm.grab_edge(steps, {0}, 2.04, 0.3), (0, "start", False))
        self.assertEqual(bm.grab_edge(steps, {0}, 2.16, 0.3), (0, "end", False))

    def test_where_two_steps_meet_the_selected_one_owns_the_edge(self) -> None:
        steps = _bars((2.0, 6.0), (6.0, 9.0))
        self.assertEqual(bm.grab_edge(steps, {0}, 6.0, 0.3), (0, "end", False))
        self.assertEqual(bm.grab_edge(steps, {1}, 6.0, 0.3), (1, "start", False))
        self.assertIsNone(bm.grab_edge(steps, {0}, 9.0, 0.3))  # the other step's far edge is not selected

    def test_a_boundary_between_two_selected_steps_moves_both(self) -> None:
        steps = _bars((2.0, 6.0), (6.0, 9.0), (9.0, 12.0))
        for x in (5.9, 6.0, 6.1):
            self.assertEqual(bm.grab_edge(steps, {0, 1}, x, 0.3), (0, "end", True), x)  # reported as the end of the earlier step
        self.assertEqual(bm.grab_edge(steps, {1, 2}, 9.0, 0.3), (1, "end", True))
        self.assertEqual(bm.grab_edge(steps, {0, 1}, 9.0, 0.3), (1, "end", False))  # the far edge of the pair is a single edge: step 3 is not selected
        self.assertEqual(bm.grab_edge(steps, {0, 1}, 2.0, 0.3), (0, "start", False))

    def test_selected_steps_that_only_nearly_touch_stay_separate(self) -> None:
        steps = _bars((2.0, 6.0), (6.2, 9.0))
        self.assertEqual(bm.grab_edge(steps, {0, 1}, 6.18, 0.3), (1, "start", False))
        self.assertEqual(bm.grab_edge(steps, {0, 1}, 6.02, 0.3), (0, "end", False))

    def test_selected_steps_that_are_not_neighbours_move_alone(self) -> None:
        steps = _bars((2.0, 6.0), (6.0, 9.0), (9.0, 12.0))
        self.assertEqual(bm.grab_edge(steps, {0, 2}, 6.0, 0.3), (0, "end", False))


class ClickSelectionTest(unittest.TestCase):
    AVAILABLE = [0, 1, 2, 3, 4]

    def _click(self, current, anchor, clicked, **keys):
        return bm.click_selection(current, anchor, clicked, self.AVAILABLE, **keys)

    def test_a_plain_click_selects_that_step_only(self) -> None:
        self.assertEqual(self._click((), None, 2), ((2,), 2))
        self.assertEqual(self._click((1, 3), 3, 2), ((2,), 2))
        self.assertEqual(self._click((1, 2, 3), 2, 2), ((2,), 2))  # a click on a selected step in a group collapses the group to it

    def test_ctrl_adds_a_step_and_takes_a_selected_one_out(self) -> None:
        self.assertEqual(self._click((1,), 1, 3, ctrl=True), ((1, 3), 3))
        self.assertEqual(self._click((1, 3), 3, 1, ctrl=True), ((3,), 1))
        self.assertEqual(self._click((), None, 2, ctrl=True), ((2,), 2))

    def test_shift_adds_every_step_from_the_anchor_to_the_clicked_one(self) -> None:
        self.assertEqual(self._click((1,), 1, 3, shift=True), ((1, 2, 3), 1))
        self.assertEqual(self._click((3,), 3, 1, shift=True), ((1, 2, 3), 3))  # either direction
        self.assertEqual(self._click((0, 4), 4, 2, shift=True), ((0, 2, 3, 4), 4))  # added to what was selected, the anchor stays
        self.assertEqual(self._click((1, 2, 3), 1, 3, shift=True), ((1, 2, 3), 1))  # nothing new

    def test_ctrl_with_shift_is_a_range_and_shift_without_an_anchor_is_a_plain_click(self) -> None:
        self.assertEqual(self._click((1,), 1, 3, ctrl=True, shift=True), ((1, 2, 3), 1))
        self.assertEqual(self._click((), None, 3, shift=True), ((3,), 3))
        self.assertEqual(self._click((1,), 9, 3, shift=True), ((3,), 3))  # an anchor that is not on the bar is none

    def test_steps_missing_from_the_bar_are_never_selected(self) -> None:
        self.assertEqual(self._click((1, 8), 1, 9), ((1,), 1))
        self.assertEqual(bm.click_selection((0, 1, 2), 0, 2, [0, 2], shift=True), ((0, 2), 0))  # a gap in the numbering is skipped


class ReadoutTest(unittest.TestCase):
    def test_seconds_with_one_decimal_or_the_cube_number(self) -> None:
        self.assertEqual(bm.boundary_readout(42.0, True), "42.0 s")
        self.assertEqual(bm.boundary_readout(12.0, False), "cube 12")
        self.assertEqual(bm.boundary_readout(12.5, False), "cube 12.5")
        self.assertEqual(bm.boundary_readout(3.04, False), "cube 3.04")


class TextPlacementTest(unittest.TestCase):
    AREA = dict(area_top=0.0, area_bottom=400.0)

    def _top(self, settings, bar=(0.0, 40.0), levels=None, th=14.0):
        return bm.text_top_y(settings, text_height=th, bar_top=bar[0], bar_bottom=bar[1], levels_y=levels, **self.AREA)

    def test_bar_mode_stops_run_from_the_top_edge_to_the_bottom_edge(self) -> None:
        tops = [self._top(_settings(text_align=bm.ALIGN_BAR, text_stop=s, height_stop=10)) for s in (5, 4, 3, 2, 1)]
        self.assertEqual(tops[0], 2.0)  # flush with the top edge, minus the padding
        self.assertEqual(tops[4], 40.0 - 2.0 - 14.0)  # flush with the bottom
        self.assertEqual(tops, sorted(tops))  # stop 5 is the highest on the screen
        self.assertAlmostEqual(tops[2] + 7.0, 20.0)  # the middle stop is centred

    def test_bar_mode_text_is_not_drawn_when_the_bar_is_lower_than_the_text(self) -> None:
        self.assertIsNone(self._top(_settings(text_align=bm.ALIGN_BAR, text_stop=3), bar=(0.0, 10.0)))

    def test_data_mode_puts_the_text_above_or_below_the_levels(self) -> None:
        levels = (200.0, 250.0, 300.0)  # device y of (high, median, low): high is higher on the screen
        def at_stop(stop: int) -> float | None:
            return self._top(_settings(text_align=bm.ALIGN_DATA, text_stop=stop, position=bm.POSITION_BOTTOM, height_stop=2), bar=(390.0, 400.0), levels=levels)

        far_above, near_above, on_data, near_below, far_below = (at_stop(k) for k in (5, 4, 3, 2, 1))
        self.assertEqual(near_above, 200.0 - 0.5 * 14.0 - 14.0)  # the text's bottom sits half a text height above the top level
        self.assertEqual(far_above, 200.0 - 3.0 * 14.0 - 14.0)
        self.assertEqual(on_data, 250.0 - 7.0)
        self.assertEqual(near_below, 300.0 + 0.5 * 14.0)
        self.assertEqual(far_below, 300.0 + 3.0 * 14.0)

    def test_data_mode_label_stays_inside_the_plot_and_clear_of_the_bar(self) -> None:
        high_up = (5.0, 20.0, 40.0)  # the data reaches the top edge
        top = self._top(_settings(text_align=bm.ALIGN_DATA, text_stop=5, position=bm.POSITION_TOP, height_stop=10), bar=(0.0, 40.0), levels=high_up)
        self.assertEqual(top, 40.0 + 2.0)  # pushed down to just under the bar
        low_down = (360.0, 380.0, 399.0)
        bottom = self._top(_settings(text_align=bm.ALIGN_DATA, text_stop=1, position=bm.POSITION_BOTTOM, height_stop=10), bar=(360.0, 400.0), levels=low_down)
        self.assertEqual(bottom, 360.0 - 2.0 - 14.0)  # pulled up to just over the bar

    def test_data_mode_in_a_full_bar_may_use_the_whole_plot(self) -> None:
        top = self._top(_settings(text_align=bm.ALIGN_DATA, text_stop=5, height_stop=bm.HEIGHT_FULL), bar=(0.0, 400.0), levels=(5.0, 20.0, 40.0))
        self.assertEqual(top, 2.0)

    def test_data_mode_without_levels_falls_back_to_the_middle_of_the_bar(self) -> None:
        settings = _settings(text_align=bm.ALIGN_DATA, text_stop=5, height_stop=10)
        self.assertEqual(self._top(settings, levels=None), self._top(_settings(text_align=bm.ALIGN_BAR, text_stop=3, height_stop=10)))

    def test_no_room_between_the_bar_and_the_edge_hides_the_label(self) -> None:
        settings = _settings(text_align=bm.ALIGN_DATA, text_stop=3, position=bm.POSITION_TOP, height_stop=10)
        self.assertIsNone(bm.text_top_y(settings, text_height=14.0, area_top=0.0, area_bottom=50.0, bar_top=0.0, bar_bottom=40.0, levels_y=(20.0, 25.0, 30.0)))


class RoomTest(unittest.TestCase):
    def test_the_data_takes_the_middle_and_the_fractions_are_free_at_the_ends(self) -> None:
        low, high = bm.reserved_range(0.0, 8.0, top_fraction=0.2, bottom_fraction=0.0)
        self.assertEqual((low, high), (0.0, 10.0))  # the data are 80 % of the final range
        low, high = bm.reserved_range(0.0, 6.0, top_fraction=0.1, bottom_fraction=0.2)
        self.assertAlmostEqual(high - low, 6.0 / 0.7)
        self.assertAlmostEqual(-low / (high - low), 0.2)
        self.assertAlmostEqual((high - 6.0) / (high - low), 0.1)

    def test_nothing_reserved_and_degenerate_ranges_change_nothing(self) -> None:
        self.assertEqual(bm.reserved_range(1.0, 5.0, 0.0, 0.0), (1.0, 5.0))
        self.assertEqual(bm.reserved_range(3.0, 3.0, 0.2, 0.2), (3.0, 3.0))

    def test_too_much_reserve_is_cut_back_so_some_plot_is_left_for_the_data(self) -> None:
        low, high = bm.reserved_range(0.0, 1.0, 0.9, 0.9)
        self.assertGreaterEqual(1.0 / (high - low), 0.2 - 1e-9)

    def test_label_headroom_follows_the_stop_and_is_zero_in_bar_mode(self) -> None:
        self.assertEqual(bm.label_headroom_px(_settings(text_align=bm.ALIGN_BAR, text_stop=5), 14.0), (0.0, 0.0))
        above, below = bm.label_headroom_px(_settings(text_align=bm.ALIGN_DATA, text_stop=5), 14.0)
        self.assertGreater(above, 14.0 * 3)
        self.assertEqual(below, 0.0)
        above, below = bm.label_headroom_px(_settings(text_align=bm.ALIGN_DATA, text_stop=1), 14.0)
        self.assertEqual(above, 0.0)
        self.assertGreater(below, 14.0 * 3)
        self.assertEqual(bm.label_headroom_px(_settings(text_align=bm.ALIGN_DATA, text_stop=3), 14.0), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
