"""The Sensorgram's View tab (2026-11-13): the band and anchor toggles, the points & lines menu, the legend menu, and the groups that moved onto the tab.

Three layers, driven through methods and signals, never by screen coordinate:
- the settings and the legend text, on their own (`view_settings.py`, and `Series.short_label` from `build_series`);
- `SensorgramPlot` with hand-made series (fast): what each option changes in what is drawn;
- `SensorgramPanel` with the real modules on the small two-step dataset of `test_lspri_plan_bar`: the tab, the controls, what they hand the plot, the remembered state.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QCoreApplication, QEvent, QRectF
from PyQt6.QtGui import QColor, QFontMetricsF, QImage
from PyQt6.QtWidgets import QToolButton

from lspr_ui import get_active_theme

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.baseline_drift import METHOD_ANCHORS, METHOD_WINDOW
    from lspr_imaging_app.analysis.scan_trace import (
        DISPLAY_AVERAGE_ALL,
        DISPLAY_BY_GROUP,
        DISPLAY_INDIVIDUAL,
        GroupSpec,
        Series,
        SensorgramRequest,
        build_series,
    )
    from lspr_imaging_app.analysis.settings import MetricSettings, StatisticsSettings
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.sensorgram import plot as plot_module
    from lspr_imaging_app.panels.sensorgram.curve_smoothing import SUBDIVISIONS, smooth_line, smooth_run
    from lspr_imaging_app.panels.sensorgram.group_menu import PICTURE_CHOICE_PX, icon_choice_buttons
    from lspr_imaging_app.panels.sensorgram.line_icons import joint_picture, lines_toggle_icon, points_lines_icon, points_toggle_icon
    from lspr_imaging_app.panels.sensorgram.view_controls import SensorgramViewControls
    from lspr_imaging_app.panels.sensorgram.text_link import TextSizeLink, make_link_toggle
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.plan_bar import DrawnStep, PlanBarItem
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.sensorgram.view_settings import (
        LEGEND_SPACING_RANGE,
        LEGEND_TEXT_RANGE,
        LINE_WIDTH_RANGE,
        POINT_SIZE_RANGE,
        SensorgramViewSettings,
        legend_text,
    )
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan import role_colors
    from lspr_imaging_app.plan.bar_model import HEIGHT_FULL, POSITION_BOTTOM, POSITION_TOP, PlanBarSettings, PlanBarStep, SectionLevels
    from lspr_imaging_app.plan.stats_table import StatsLine, StatsTableSettings, build_stats_table
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_plan_bar import CENTERS, _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()


def _lines(n: int) -> list[Series]:
    x = np.arange(10.0)
    return [Series(f"roi:{i}", f"ROI {i}", "#ff8800", x, 600.0 + i + 0.1 * x) for i in range(n)]


def _averages(n: int) -> list[Series]:
    """`n` averaged lines, each with a band, named "G<i> (n=4)" with the short name "G<i>"."""
    x = np.arange(10.0)
    out = []
    for i in range(n):
        y = 600.0 + i + 0.1 * x
        out.append(Series(f"group:{i}", f"G{i} (n=4)", "#0088ff", x, y, y - 0.3, y + 0.3, n_members=4, short_label=f"G{i}"))
    return out


def _parts(plot: SensorgramPlot, layer: str) -> list:
    return [p.item for p in plot._svg_parts if p.layer == layer]


# -- the settings and the legend text -------------------------------------------------------------------------------------------------


class ViewSettingsTest(unittest.TestCase):
    def test_the_defaults_are_what_the_plot_always_did(self) -> None:
        d = SensorgramViewSettings()
        self.assertEqual(
            (d.show_band, d.show_anchors, d.show_points, d.show_lines, d.point_size, d.line_width, d.line_style, d.legend_mode, d.legend_text_pt, d.legend_spacing_px, d.legend_counts),
            (True, True, True, True, 4, 1.0, "straight", "auto", 9, 0, True),
        )
        self.assertEqual(d.sanitized(), d)
        self.assertEqual(d.line_width, plot_module.LINE_WIDTH)  # 1 px: Qt's fast path
        self.assertEqual(d.legend_text_pt, 9)  # pyqtgraph's own legend size

    def test_a_value_outside_its_range_is_pulled_in_and_an_unknown_mode_is_auto(self) -> None:
        wild = SensorgramViewSettings(point_size=99, line_width=9.0, legend_mode="nonsense", legend_text_pt=1, legend_spacing_px=-100)
        tame = wild.sanitized()
        self.assertEqual((tame.point_size, tame.line_width, tame.legend_mode, tame.legend_text_pt, tame.legend_spacing_px), (POINT_SIZE_RANGE[1], LINE_WIDTH_RANGE[1], "auto", LEGEND_TEXT_RANGE[0], LEGEND_SPACING_RANGE[0]))
        low = SensorgramViewSettings(point_size=-3, line_width=0.0, legend_text_pt=99, legend_spacing_px=99).sanitized()
        self.assertEqual((low.point_size, low.line_width, low.legend_text_pt, low.legend_spacing_px), (POINT_SIZE_RANGE[0], LINE_WIDTH_RANGE[0], LEGEND_TEXT_RANGE[1], LEGEND_SPACING_RANGE[1]))

    def test_an_unknown_line_style_is_straight_and_no_dots_is_the_toggles_job_not_a_size(self) -> None:
        self.assertEqual(SensorgramViewSettings(line_style="curvy").sanitized().line_style, "straight")
        self.assertEqual(SensorgramViewSettings(line_style="spline").sanitized().line_style, "spline")
        self.assertEqual(POINT_SIZE_RANGE[0], 1)  # a size of 0 used to mean "none" (the first build of 2026-11-13); the Points toggle does that now

    def test_the_line_width_is_a_multiple_of_half_a_pixel(self) -> None:
        self.assertEqual(SensorgramViewSettings(line_width=1.3).sanitized().line_width, 1.5)
        self.assertEqual(SensorgramViewSettings(line_width=1.2).sanitized().line_width, 1.0)

    def test_the_legend_leaves_the_count_out_only_where_there_is_one(self) -> None:
        self.assertEqual(legend_text("Group A (n=12)", "Group A", True), "Group A (n=12)")
        self.assertEqual(legend_text("Group A (n=12)", "Group A", False), "Group A")
        self.assertEqual(legend_text("ROI 5", "", False), "ROI 5")  # a line of one ROI has no count to leave out

    def test_a_group_and_an_average_offer_their_name_without_the_count_and_keep_it_in_the_label(self) -> None:
        t = np.arange(20.0)
        traces = {r: 600.0 + r + 0.1 * t for r in (1, 2, 3)}

        def lines(mode: str, groups=()):
            request = SensorgramRequest((1, 2, 3), "", MetricSettings(), StatisticsSettings(), t, tuple(groups), {})
            return build_series(request, traces, mode, "mean", "sd")

        by_group = lines(DISPLAY_BY_GROUP, (GroupSpec("A", "#ffffff", (1, 2)),))
        self.assertEqual([(s.label, s.short_label) for s in by_group], [("A (n=2)", "A"), ("Ungrouped (n=1)", "Ungrouped")])
        (average,) = lines(DISPLAY_AVERAGE_ALL)
        self.assertEqual((average.label, average.short_label), ("Average of 3 ROIs", "Average"))
        for line in lines(DISPLAY_INDIVIDUAL):
            self.assertEqual((line.label.startswith("ROI "), line.short_label), (True, ""))


# -- the curve of the Spline style ---------------------------------------------------------------------------------------------------


class CurveSmoothingTest(unittest.TestCase):
    def test_the_curve_passes_through_every_measured_point_and_has_a_fixed_number_of_points_per_interval(self) -> None:
        x = np.array([0.0, 1.0, 3.0, 4.0, 7.0])
        y = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
        xs, ys = smooth_run(x, y)
        self.assertEqual(xs.size, (x.size - 1) * SUBDIVISIONS + 1)
        for xi, yi in zip(x, y):
            at = int(np.flatnonzero(xs == xi)[0])  # every measured x is one of the drawn x, exactly
            self.assertAlmostEqual(float(ys[at]), float(yi), places=12)

    def test_it_never_leaves_the_range_of_the_two_points_around_it_even_on_noisy_data(self) -> None:
        """What a natural cubic spline does not guarantee (on 400 points of 0.3 nm noise it left that range in 18 % of the samples): a peak that is not in the data."""
        rng = np.random.default_rng(3)
        x = np.arange(200.0)
        y = rng.normal(600.0, 0.3, x.size)
        _xs, ys = smooth_run(x, y)
        low = np.minimum(y[:-1], y[1:]).repeat(SUBDIVISIONS)
        high = np.maximum(y[:-1], y[1:]).repeat(SUBDIVISIONS)
        self.assertTrue(np.all(ys[:-1] <= high + 1e-9) and np.all(ys[:-1] >= low - 1e-9))

    def test_a_straight_run_stays_straight(self) -> None:
        x = np.arange(6.0)
        xs, ys = smooth_run(x, 2.0 * x + 1.0)
        np.testing.assert_allclose(ys, 2.0 * xs + 1.0, atol=1e-12)

    def test_a_gap_stays_a_gap_and_a_line_without_one_has_no_nan(self) -> None:
        x = np.arange(10.0)
        y = 2.0 * x
        whole_x, whole_y = smooth_line(x, y)
        self.assertFalse(np.isnan(whole_x).any() or np.isnan(whole_y).any())
        y[4] = np.nan  # two stretches: 0-3 (4 points) and 5-9 (5 points)
        xs, ys = smooth_line(x, y)
        self.assertEqual(xs.size, (3 * SUBDIVISIONS + 1) + 1 + (4 * SUBDIVISIONS + 1))
        self.assertEqual(int(np.isnan(ys).sum()), 1)
        self.assertEqual(int(np.flatnonzero(np.isnan(xs))[0]), int(np.flatnonzero(np.isnan(ys))[0]))  # the break is in both arrays, at the same place
        self.assertFalse(np.isnan(xs[: 3 * SUBDIVISIONS + 1]).any())

    def test_what_cannot_be_smoothed_comes_back_as_it_was(self) -> None:
        for x, y in (([0.0, 1.0, 1.0, 2.0], [1.0, 2.0, 3.0, 4.0]), ([5.0], [1.0]), ([], [])):  # x not increasing, one point, none
            xs, ys = smooth_run(np.array(x), np.array(y))
            np.testing.assert_array_equal(xs, x)
            np.testing.assert_array_equal(ys, y)
        np.testing.assert_array_equal(smooth_run(np.arange(3.0), np.arange(3.0), per=1)[0], np.arange(3.0))  # one point per interval = the points
        nothing_x, nothing_y = smooth_line(np.full(4, np.nan), np.full(4, np.nan))
        self.assertEqual((nothing_x.size, nothing_y.size), (0, 0))


# -- what each option changes in what is drawn ----------------------------------------------------------------------------------------


class ViewPlotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = SensorgramPlot()
        self.plot.resize(900, 500)
        self.plot.show()

    def tearDown(self) -> None:
        self.plot.close()
        self.plot.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # deleted with Qt now, not when the collector gets to it

    def _draw(self, series, **changes) -> None:
        self.plot.set_view(replace(SensorgramViewSettings(), **changes))
        self.plot.show_series(series)
        _APP.processEvents()

    def _pitch(self) -> float:
        return float(np.mean(np.diff(self.plot._legend_rows().centers_scene_y)))

    def test_the_dots_and_the_lines_follow_the_size_and_the_width(self) -> None:
        self._draw(_lines(2))
        self.assertEqual(float(_parts(self.plot, "Points")[0].opts["size"]), 4.0)  # as it always was
        self.assertAlmostEqual(_parts(self.plot, "Lines")[0].opts["pen"].widthF(), 1.0)
        self._draw(_lines(2), point_size=9, line_width=2.5)
        self.assertEqual(float(_parts(self.plot, "Points")[0].opts["size"]), 9.0)
        self.assertAlmostEqual(_parts(self.plot, "Lines")[0].opts["pen"].widthF(), 2.5)

    def test_the_dashed_baseline_curve_has_the_width_of_the_lines(self) -> None:
        series = _lines(1)
        series[0].baseline_curve = series[0].y - 0.5
        self.plot.set_view(replace(SensorgramViewSettings(), line_width=2.0))
        self.plot.show_series(series, baseline_curves=True)
        self.assertAlmostEqual(_parts(self.plot, "Baseline curves")[0].opts["pen"].widthF(), 2.0)

    def test_hiding_the_points_draws_no_dots_and_more_than_ten_lines_never_get_any(self) -> None:
        self._draw(_lines(2), show_points=False)
        self.assertEqual(_parts(self.plot, "Points"), [])
        self.assertEqual(len(_parts(self.plot, "Lines")), 2)  # the lines stay
        self._draw(_lines(plot_module.DOTS_MAX_LINES + 1), point_size=8)  # the guard against 64,000 dots a redraw is not the View tab's to lift
        self.assertEqual(_parts(self.plot, "Points"), [])

    def test_the_spline_joins_the_points_with_a_denser_curve_and_leaves_the_dots_on_the_points(self) -> None:
        series = _lines(2)
        self._draw(series)
        self.assertEqual(len(_parts(self.plot, "Lines")[0].xData), 10)  # straight: the points themselves
        self._draw(series, line_style="spline")
        line = _parts(self.plot, "Lines")[0]
        self.assertEqual(len(line.xData), 9 * SUBDIVISIONS + 1)
        np.testing.assert_allclose(line.yData[::SUBDIVISIONS], series[0].y, atol=1e-9)  # through every measured point
        np.testing.assert_allclose(line.xData[::SUBDIVISIONS], series[0].x)
        self.assertEqual(len(_parts(self.plot, "Points")[0].data["x"]), 10)  # the dots stay on the measured points, not on the curve between them
        self.assertEqual(len(_parts(self.plot, "Lines")), 2)

    def test_a_gap_in_the_data_is_still_a_gap_with_the_spline(self) -> None:
        series = _lines(1)
        series[0].y = series[0].y.copy()
        series[0].y[4] = np.nan
        self._draw(series, line_style="spline")
        line = _parts(self.plot, "Lines")[0]
        self.assertEqual(int(np.isnan(line.yData).sum()), 1)  # one break, drawn with connect="finite"

    def test_the_band_follows_a_spline_line_on_the_same_denser_x(self) -> None:
        self._draw(_averages(1), line_style="spline")
        edges = [p.item for p in self.plot._svg_parts if p.layer == "Bands" and p.name.endswith("(edges)")]
        self.assertEqual(len(edges), 2)
        for edge in edges:
            self.assertEqual(len(edge.xData), 9 * SUBDIVISIONS + 1)
        np.testing.assert_allclose(edges[0].xData, edges[1].xData)

    def test_hidden_lines_keep_the_view_and_the_legends_colour_marks(self) -> None:
        self._draw(_lines(2), show_lines=False)
        self.assertEqual(_parts(self.plot, "Lines"), [])  # nothing of them in the export either
        self.assertEqual(len(_parts(self.plot, "Points")), 2)  # the dots remain
        self.assertEqual(self.plot.legend_entry_count(), 2)
        marker_pen = self.plot._legend.items[0][0].item.opts["pen"]
        self.assertGreater(marker_pen.color().alpha(), 0)  # the legend's marker has the line's colour, it is not invisible too
        low, high = self.plot._item.getViewBox().viewRange()[1]
        self.assertLess(low, 600.0)  # the invisible lines still give the view its extent: the dots are drawn out of the auto-range
        self.assertGreater(high, 601.9)

    def test_with_the_dots_and_the_lines_both_hidden_only_the_legend_is_left(self) -> None:
        self._draw(_lines(2), show_points=False, show_lines=False)
        self.assertEqual((_parts(self.plot, "Lines"), _parts(self.plot, "Points")), ([], []))
        self.assertEqual(self.plot.legend_entry_count(), 2)

    def test_the_band_can_be_hidden_and_the_line_inside_it_stays(self) -> None:
        self._draw(_averages(2))
        self.assertGreater(len(_parts(self.plot, "Bands")), 0)
        self._draw(_averages(2), show_band=False)
        self.assertEqual(_parts(self.plot, "Bands"), [])
        self.assertEqual(len(_parts(self.plot, "Lines")), 2)

    def test_the_legend_modes(self) -> None:
        many = _lines(plot_module.MAX_LEGEND_ENTRIES + 6)
        self._draw(many)
        self.assertEqual(self.plot.legend_entry_count(), 0)  # auto: a legend of this many lines is a wall of text
        self._draw(many, legend_mode="show")
        self.assertEqual(self.plot.legend_entry_count(), len(many))  # show: all of them, however many
        self.assertTrue(self.plot._legend.isVisible())
        self._draw(_lines(4), legend_mode="auto")
        self.assertEqual(self.plot.legend_entry_count(), 4)
        self.assertTrue(self.plot._legend.isVisible())
        self._draw(_lines(4), legend_mode="hide")
        self.assertFalse(self.plot._legend.isVisible())  # hide: not drawn ...
        self.assertEqual(self.plot.legend_entry_count(), 4)  # ... but its entries stay: the step table lines up with their rows
        self._draw(_lines(4), legend_mode="auto")
        self.assertTrue(self.plot._legend.isVisible())  # and it comes back

    def test_show_keeps_a_legend_that_auto_would_clear_for_taking_too_much_of_the_plot(self) -> None:
        series = _lines(plot_module.MAX_LEGEND_ENTRIES)  # named, but taller than half of a short plot
        self.plot.resize(900, 220)
        _APP.processEvents()
        self._draw(series)
        self.assertEqual(self.plot.legend_entry_count(), 0)
        self._draw(series, legend_mode="show")
        self.assertEqual(self.plot.legend_entry_count(), len(series))

    def test_the_legend_text_size_and_the_row_gap_move_the_rows(self) -> None:
        series = _lines(4)
        self._draw(series)
        base = self._pitch()
        self._draw(series, legend_spacing_px=6)
        self.assertAlmostEqual(self._pitch() - base, 6.0, delta=0.5)
        self._draw(series, legend_spacing_px=-3)
        self.assertAlmostEqual(self._pitch() - base, -3.0, delta=0.5)  # closer than the plot's look so far
        self._draw(series, legend_text_pt=16)
        self.assertEqual(self.plot._legend.opts["labelTextSize"], "16pt")
        self.assertGreater(self._pitch(), base)  # a bigger text needs taller rows

    def test_the_legend_can_leave_the_number_of_rois_out(self) -> None:
        def names() -> list[str]:
            return [label.text for _sample, label in self.plot._legend.items]

        self._draw(_averages(2))
        self.assertEqual(names(), ["G0 (n=4)", "G1 (n=4)"])
        self._draw(_averages(2), legend_counts=False)
        self.assertEqual(names(), ["G0", "G1"])
        self._draw(_lines(2), legend_counts=False)
        self.assertEqual(names(), ["ROI 0", "ROI 1"])  # one ROI: nothing to leave out
        self._draw(_averages(2), legend_counts=False)
        self.assertEqual([p.name for p in self.plot._svg_parts if p.layer == "Lines"], ["G0 (n=4)", "G1 (n=4)"])  # the exports keep the full label

    def test_a_hidden_legend_takes_no_room_on_the_left_and_the_table_keeps_its_rows(self) -> None:
        series = _lines(3)
        bars = [PlanBarStep(index=i, x_start=5.0 * i, x_end=5.0 * (i + 1), text=f"step {i}", color="#ff0000", tooltip="", levels=None) for i in range(2)]
        rows = [StatsLine(f"ROI {i}", "#ff8800", (600.0 + i, 601.0 + i), (0.1, 0.1), (5, 5), False) for i in range(3)]
        self._draw(series)
        self.plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings(position="top", text_pt=9))
        _APP.processEvents()
        top, _bottom, left = self.plot._room()
        shown = self.plot._legend_rows()
        self.assertGreater(left, 0.0)  # the first step's numbers start right of the legend
        self.assertIsNotNone(shown.right_scene_x)

        self._draw(series, legend_mode="hide")
        top_hidden, _bottom_hidden, left_hidden = self.plot._room()
        hidden = self.plot._legend_rows()
        self.assertEqual(left_hidden, 0.0)  # nothing to keep clear of
        self.assertIsNone(hidden.right_scene_x)  # the cells may use the whole width
        self.assertAlmostEqual(top_hidden, top, places=6)  # the rows are still there, so is their room above
        np.testing.assert_allclose(hidden.centers_scene_y, shown.centers_scene_y, atol=0.5)  # on the very same rows

    def _table_and_bar(self, bar_position: str = POSITION_TOP, **bar_settings) -> None:
        """A step table of 3 lines and a plan bar of two steps along `bar_position`, on the plot (the legend rows and the room need both)."""
        bars = [PlanBarStep(index=i, x_start=4.0 * i, x_end=4.0 * (i + 1), text=f"step {i}", color="#4e79a7", tooltip="", levels=None) for i in range(2)]
        rows = [StatsLine(f"ROI {i}", "#ff8800", (600.0 + i, 601.0 + i), (0.1, 0.1), (5, 5), False) for i in range(3)]
        self.plot.set_plan(bars, PlanBarSettings(**{"position": bar_position, "height_stop": 5, **bar_settings}))
        self.plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings(position=POSITION_TOP, text_pt=9))
        _APP.processEvents()

    def _legend_top_offset(self) -> float:
        return float(self.plot._legend.opts["offset"][1])

    def test_the_legend_can_ignore_the_bar_and_go_to_the_edge_of_the_plot(self) -> None:
        """Maintainer, 2026-11-13: with the stats table on, the legend sat just inside (under) the plan bar; now it can ignore the bar and sit at the edge."""
        series = _lines(3)
        self._draw(series)
        self._table_and_bar()
        bar_height = self.plot._bar_height_px(POSITION_TOP)
        under = self._legend_top_offset()
        self.assertGreater(bar_height, 10.0)
        self._draw(series, legend_over_bar=True)
        at_edge = self._legend_top_offset()
        self.assertAlmostEqual(under - at_edge, bar_height, delta=0.5)  # it moved up by exactly the bar's height
        self.assertAlmostEqual(at_edge, plot_module._LEGEND_EDGE_PX)  # to the margin the plot keeps from its edge
        self._draw(series)  # and back
        self.assertAlmostEqual(self._legend_top_offset(), under)

    def test_the_stats_rows_go_to_the_edge_with_the_legend_they_line_up_with(self) -> None:
        series = _lines(3)
        self._draw(series)
        self._table_and_bar()
        before = self.plot._legend_rows().centers_scene_y
        self._draw(series, legend_over_bar=True)
        after = self.plot._legend_rows().centers_scene_y
        moved = [b - a for a, b in zip(after, before)]
        self.assertTrue(all(abs(m - self.plot._bar_height_px(POSITION_TOP)) < 1.0 for m in moved))  # every row moved up by the bar's height

    def test_at_the_edge_the_room_for_the_bar_and_the_rows_is_the_larger_of_the_two_not_their_sum(self) -> None:
        series = _lines(3)
        self._draw(series)
        self._table_and_bar()
        top_sum = self.plot._room()[0]
        bar = self.plot._plan_bar.settings().height_fraction
        self._draw(series, legend_over_bar=True)
        top_over = self.plot._room()[0]
        rows = top_sum - bar  # the room the rows need on their own (the default adds it to the bar's)
        self.assertAlmostEqual(top_over, max(bar, rows), places=9)
        self.assertLess(top_over, top_sum)  # the traces get the room back that the overlap saves

    def test_the_option_changes_nothing_when_the_bar_is_on_the_other_edge_or_full_or_the_table_is_off(self) -> None:
        series = _lines(3)
        self._draw(series)
        self._table_and_bar(bar_position=POSITION_BOTTOM)  # the table at the top, the bar at the bottom: they do not share an edge
        before = (self._legend_top_offset(), self.plot._room())
        self._draw(series, legend_over_bar=True)
        self.assertEqual((self._legend_top_offset(), self.plot._room()), before)
        self._draw(series)
        self._table_and_bar(height_stop=HEIGHT_FULL)  # a full bar fills the plot behind the traces: it takes no edge
        before = (self._legend_top_offset(), self.plot._room())
        self._draw(series, legend_over_bar=True)
        self.assertEqual((self._legend_top_offset(), self.plot._room()), before)
        self.plot.set_stats_table(None, StatsTableSettings(visible=False))  # no table: the legend is where it always was
        self._draw(series, legend_over_bar=True)
        self.assertEqual(self._legend_top_offset(), float(plot_module._LEGEND_OFFSET_PX[1]))

    def test_the_stats_legend_room_scales_the_room_made_before_the_first_step_and_off_makes_none(self) -> None:
        """Maintainer, 2026-11-13: the x axis is widened before its start for the legend the stats rows line up with; that room is now a percentage, with off."""
        series = _lines(3)
        bars = [PlanBarStep(index=i, x_start=3.0 * i, x_end=3.0 * (i + 1), text=f"step {i}", color="#ff0000", tooltip="", levels=None) for i in range(3)]
        rows = [StatsLine(f"ROI {i}", "#ff8800", (600.0 + i, 601.0 + i, 602.0 + i), (0.1, 0.1, 0.1), (5, 5, 5), False) for i in range(3)]
        table = build_stats_table(bars, rows)
        self._draw(series)

        def put(pct: int) -> tuple[float, float]:
            self.plot.set_stats_table(table, StatsTableSettings(position="top", text_pt=9, legend_room_pct=pct))
            _APP.processEvents()
            return self.plot._room()[2], self.plot._item.getViewBox().viewRange()[0][0]

        full, low_full = put(100)
        self.assertGreater(full, 0.0)
        half, _ = put(50)
        double, _ = put(200)
        none, low_none = put(0)
        self.assertAlmostEqual(half, 0.5 * full, delta=1e-6 * full)  # a percentage of what holds the legend (same legend, so exactly proportional)
        self.assertAlmostEqual(double, 2.0 * full, delta=1e-6 * full)
        self.assertEqual(none, 0.0)  # off: no room is made ...
        self.assertGreater(low_none, low_full)  # ... so the axis starts later than with the room (nearer the data)
        self.assertEqual(StatsTableSettings().legend_room_pct, 100)  # the default is what the plot always did

    def test_a_negative_room_is_never_made(self) -> None:
        series = _lines(3)
        bars = [PlanBarStep(index=0, x_start=0.0, x_end=9.0, text="s", color="#ff0000", tooltip="", levels=None)]
        rows = [StatsLine(f"ROI {i}", "#ff8800", (600.0 + i,), (0.1,), (5,), False) for i in range(3)]
        self._draw(series)
        self.plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings(position="top", legend_room_pct=-50))
        _APP.processEvents()
        self.assertEqual(self.plot._room()[2], 0.0)

    def test_the_step_table_rows_follow_the_legend_row_gap(self) -> None:
        series = _lines(3)
        bars = [PlanBarStep(index=0, x_start=0.0, x_end=9.0, text="step", color="#ff0000", tooltip="", levels=None)]
        rows = [StatsLine(f"ROI {i}", "#ff8800", (600.0 + i,), (0.1,), (5,), False) for i in range(3)]
        self._draw(series)
        self.plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings(position="top", text_pt=9))
        _APP.processEvents()

        item = self.plot.stats_item()

        def cell_pitch() -> float:
            cells = item.layout(item._device_transform(), item.view_rect())
            return float(np.mean(np.diff(sorted(c.rect.center().y() for c in cells))))

        before = cell_pitch()
        self._draw(series, legend_spacing_px=8)
        self.assertAlmostEqual(cell_pitch() - before, 8.0, delta=1.0)  # the maintainer: "vertical spacing also influencing the stats spacing"


# -- the pictures of the Points & lines menu, and the text-size link (2026-11-13) -------------------------------------------------------


def _picture_array(pixmap) -> np.ndarray:
    """A picture as rows x columns x (B, G, R, A), not premultiplied."""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    return np.frombuffer(image.constBits().asstring(image.sizeInBytes()), dtype=np.uint8).reshape(image.height(), image.width(), 4).astype(int)


class LineIconsTest(unittest.TestCase):
    """The Straight / Spline pictures and the menu's face (`line_icons.py`): three dots in the text colour and the line through them in the gold accent, like the baseline shape pictures.
    Maintainer, 2026-11-13: the first ones were "very similar", the second ones "terrible"."""

    def test_the_pictures_are_20_px_squares_like_the_baseline_shape_pictures(self) -> None:
        for kind in ("straight", "spline"):
            size = joint_picture(kind)("#ffffff").deviceIndependentSize()
            self.assertEqual((size.width(), size.height()), (20.0, 20.0), kind)

    def test_the_dots_and_the_line_are_two_colours_the_dots_take_the_given_one_and_the_line_stays_gold(self) -> None:
        gold = QColor(get_active_theme().accent_gold)

        def counts(ink: str, kind: str) -> tuple[int, int]:
            image = _picture_array(joint_picture(kind)(ink))
            solid = image[image[..., 3] > 220]
            blue, green, red = solid[:, 0], solid[:, 1], solid[:, 2]
            goldish = int(((abs(red - gold.red()) < 30) & (abs(green - gold.green()) < 30) & (abs(blue - gold.blue()) < 30)).sum())
            target = QColor(ink)
            inked = int(((abs(red - target.red()) < 12) & (abs(green - target.green()) < 12) & (abs(blue - target.blue()) < 12)).sum())
            return inked, goldish

        for kind in ("straight", "spline"):
            for ink in ("#ffffff", "#202020"):  # a light and a dark theme's text colour
                inked, goldish = counts(ink, kind)
                self.assertGreater(inked, 20, f"{kind} in {ink}: the dots")
                self.assertGreater(goldish, 20, f"{kind} in {ink}: the line is gold whatever the text colour")

    def test_the_two_joints_are_clearly_different_at_the_size_they_are_shown(self) -> None:
        """The first pictures (four dots, one colour) shared only 68 % of their ink with each other's, i.e. 32 % differed; these (three dots, two colours) 63 % differ. Kept above half."""
        straight, spline = _picture_array(joint_picture("straight")("#ffffff"))[..., 3], _picture_array(joint_picture("spline")("#ffffff"))[..., 3]
        ink = int(((straight > 40) | (spline > 40)).sum())
        differing = int((np.abs(straight - spline) > 40).sum())
        self.assertGreater(differing / ink, 0.5, f"only {100 * differing / ink:.0f} % of the ink differs")

    def test_the_curve_in_the_spline_picture_is_the_one_the_plot_draws_and_never_leaves_its_dots_range(self) -> None:
        from lspr_imaging_app.panels.sensorgram import line_icons

        dots = np.array(line_icons._DOTS)
        self.assertEqual(len(dots), 3)  # three dots, as the baseline shape pictures
        xs, ys = smooth_run(dots[:, 0], dots[:, 1], line_icons._CURVE_PER_INTERVAL)
        np.testing.assert_allclose(ys[:: line_icons._CURVE_PER_INTERVAL], dots[:, 1], atol=1e-12)  # through every dot
        self.assertLessEqual(float(ys.max()), float(dots[:, 1].max()) + 1e-12)  # turns round at the dots, no overshoot (PCHIP)
        self.assertGreaterEqual(float(ys.min()), float(dots[:, 1].min()) - 1e-12)

    def test_the_menus_face_is_the_dots_joined_by_a_line_in_the_colour_it_is_given(self) -> None:
        light, dark = points_lines_icon("#ffffff"), points_lines_icon("#202020")
        self.assertFalse(light.isNull() or dark.isNull())
        self.assertNotEqual(light.pixmap(36, 36).toImage(), dark.pixmap(36, 36).toImage())

    def test_the_points_and_lines_toggles_are_the_picture_split_in_two_in_the_same_colours_and_crossed_out_when_off(self) -> None:
        """Maintainer, 2026-11-13: the Points and Lines show / hide toggles get the new style too (they were Spectra's pictures)."""
        theme = get_active_theme()
        gold, text, dim = QColor(theme.accent_gold), QColor(theme.text_primary), QColor("#888888")

        def pixels(icon, on: bool) -> tuple[np.ndarray, dict[str, int]]:
            image = _picture_array(icon(on, dim.name(), "#38bdf8").pixmap(40, 40))
            solid = image[image[..., 3] > 200]

            def near(color: QColor) -> int:
                return int(((abs(solid[:, 2] - color.red()) < 25) & (abs(solid[:, 1] - color.green()) < 25) & (abs(solid[:, 0] - color.blue()) < 25)).sum())

            return image, {"text": near(text), "gold": near(gold), "dim": near(dim)}

        for name, icon, mine, other in (("points", points_toggle_icon, "text", "gold"), ("lines", lines_toggle_icon, "gold", "text")):
            on_image, on_counts = pixels(icon, True)
            off_image, off_counts = pixels(icon, False)
            self.assertGreater(on_counts[mine], 15, f"{name} on: its own colour")
            self.assertEqual(on_counts[other], 0, f"{name} on: nothing of the other half's colour")  # the dots alone, or the line alone
            self.assertGreater(off_counts["dim"], 15, f"{name} off: dim")
            self.assertEqual((off_counts["text"], off_counts["gold"]), (0, 0), f"{name} off: no colour left")
            height, width = on_image.shape[:2]
            centre = (height // 2, width // 2)
            self.assertEqual(int(on_image[centre][3]), 0, f"{name} on: nothing across the middle")
            self.assertGreater(int(off_image[centre][3]), 150, f"{name} off: the crossing-out stroke runs through the middle")

    def test_the_points_toggle_has_no_line_and_the_lines_toggle_has_no_dots(self) -> None:
        """Each toggle is one half of the picture: the dots alone (their pixels are round, the line's are a thin stroke), so the two toggles read as two different things."""
        dots_only = _picture_array(points_toggle_icon(True, "#888888", "#38bdf8").pixmap(40, 40))[..., 3]
        line_only = _picture_array(lines_toggle_icon(True, "#888888", "#38bdf8").pixmap(40, 40))[..., 3]
        self.assertNotEqual(dots_only.tolist(), line_only.tolist())
        height, width = dots_only.shape
        # the middle of the gap between the first two dots (a fifth of the way across, at the height of the line there) is on the line but not on any dot
        x, y = int(0.325 * width), int(0.5 * height)  # (6.5, 10) in the 20 x 20 picture: on the rising segment from (3, 15) to (10, 5)
        self.assertGreater(int(line_only[y, x]), 100)
        self.assertEqual(int(dots_only[y, x]), 0)

    def test_the_toggles_in_the_menu_use_them_and_change_their_picture_when_pressed(self) -> None:
        parent = QtWidgets.QWidget()
        controls = SensorgramViewControls(parent)
        for toggle in (controls.show_points, controls.show_lines):
            self.assertFalse(toggle.icon().isNull())
            before = toggle.icon().pixmap(36, 36).toImage()
            toggle.click()  # on -> off
            self.assertNotEqual(toggle.icon().pixmap(36, 36).toImage(), before)
            toggle.click()
            self.assertEqual(toggle.icon().pixmap(36, 36).toImage(), before)
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_the_two_joint_buttons_are_picture_buttons_with_a_frame_when_pressed_and_keep_their_size(self) -> None:
        parent = QtWidgets.QWidget()
        pictures = {"straight": joint_picture("straight"), "spline": joint_picture("spline")}
        _holder, _group, buttons = icon_choice_buttons(parent, ("straight", "spline"), pictures, {"straight": "Straight.", "spline": "Spline."}, "joint")
        for round_ in range(2):  # the second time after a theme change
            for kind, button in buttons.items():
                self.assertEqual(button.text(), "", kind)  # a picture, no word
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.isCheckable())
                self.assertEqual((button.width(), button.height()), (PICTURE_CHOICE_PX, PICTURE_CHOICE_PX))
                self.assertIn(":checked", button.styleSheet())  # the pressed one has the same frame as a pressed text segment
            for button in buttons.values():
                button.refresh_theme()
        self.assertEqual([kind for kind, button in buttons.items() if button.isChecked()], ["straight"])  # the first starts pressed
        buttons["spline"].click()
        self.assertEqual([kind for kind, button in buttons.items() if button.isChecked()], ["spline"])  # one at a time
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class TextSizeLinkTest(unittest.TestCase):
    """`TextSizeLink` on bare boxes and buttons: the rule, without a panel."""

    def setUp(self) -> None:
        self.parent = QtWidgets.QWidget()
        self.legend = QtWidgets.QSpinBox(self.parent)
        self.legend.setRange(6, 24)
        self.legend.setValue(9)
        self.stats = QtWidgets.QSpinBox(self.parent)
        self.stats.setRange(6, 24)
        self.stats.setValue(14)
        self.legend_toggle, self.stats_toggle = make_link_toggle(self.parent), make_link_toggle(self.parent)
        self.link = TextSizeLink(self.legend, self.legend_toggle, self.stats, self.stats_toggle, self.parent)

    def tearDown(self) -> None:
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_unlinked_the_two_sizes_are_independent_as_they_always_were(self) -> None:
        self.assertFalse(self.link.is_linked())
        self.legend.setValue(20)
        self.assertEqual((self.legend.value(), self.stats.value()), (20, 14))
        self.stats.setValue(7)
        self.assertEqual((self.legend.value(), self.stats.value()), (20, 7))

    def test_turning_it_on_gives_both_the_size_of_the_menu_it_was_pressed_in(self) -> None:
        self.legend_toggle.click()
        self.assertEqual((self.legend.value(), self.stats.value()), (9, 9))  # pressed in the Legend menu: the legend's size
        self.legend_toggle.click()
        self.stats.setValue(16)
        self.stats_toggle.click()
        self.assertEqual((self.legend.value(), self.stats.value()), (16, 16))  # pressed in the Stats menu: the stats' size

    def test_linked_changing_either_changes_both_and_unlinking_frees_them(self) -> None:
        self.legend_toggle.click()
        self.legend.setValue(12)
        self.assertEqual(self.stats.value(), 12)
        self.stats.setValue(18)
        self.assertEqual(self.legend.value(), 18)
        self.legend_toggle.click()  # off
        self.legend.setValue(8)
        self.assertEqual((self.legend.value(), self.stats.value()), (8, 18))

    def test_the_two_buttons_show_one_state(self) -> None:
        self.stats_toggle.click()
        self.assertTrue(self.legend_toggle.isChecked() and self.stats_toggle.isChecked() and self.link.is_linked())
        self.legend_toggle.click()
        self.assertFalse(self.legend_toggle.isChecked() or self.stats_toggle.isChecked() or self.link.is_linked())

    def test_linked_both_boxes_take_the_narrower_range_so_they_can_always_be_equal(self) -> None:
        legend, stats = QtWidgets.QSpinBox(self.parent), QtWidgets.QSpinBox(self.parent)
        legend.setRange(6, 24)
        stats.setRange(8, 20)  # a stats box narrower than the legend's (today both are 6 - 24)
        legend_toggle, stats_toggle = make_link_toggle(self.parent), make_link_toggle(self.parent)
        TextSizeLink(legend, legend_toggle, stats, stats_toggle, self.parent)
        legend_toggle.click()
        self.assertEqual((legend.minimum(), legend.maximum(), stats.minimum(), stats.maximum()), (8, 20, 8, 20))
        legend.setValue(24)  # past the common range: clamped in the box itself, so the pair stays equal
        self.assertEqual((legend.value(), stats.value()), (20, 20))
        legend_toggle.click()
        self.assertEqual((legend.minimum(), legend.maximum(), stats.minimum(), stats.maximum()), (6, 24, 8, 20))  # each its own range again

    def test_sync_after_a_restore_makes_a_linked_pair_equal_and_leaves_an_unlinked_one_alone(self) -> None:
        self.link.sync()
        self.assertEqual((self.legend.value(), self.stats.value()), (9, 14))
        # a restore: the link comes back first (it is the legend menu's remembered button), the sizes one after the other, and they disagree
        self.link.begin_restore()
        self.legend_toggle.setChecked(True)
        self.assertEqual((self.legend.value(), self.stats.value()), (9, 14))  # turning the link on while restoring copies nothing
        self.legend.setValue(10)
        self.stats.setValue(7)  # the second size restored must not overwrite the first
        self.assertEqual((self.legend.value(), self.stats.value()), (10, 7))
        self.link.end_restore()
        self.assertEqual((self.legend.value(), self.stats.value()), (10, 10))  # the Legend menu's wins, whatever the order
        self.stats.setValue(12)  # and it is a working link afterwards
        self.assertEqual(self.legend.value(), 12)


# -- the evaluation chips on the plan bar (2026-11-13) -------------------------------------------------------------------------------


AREA = QRectF(0.0, 0.0, 800.0, 400.0)  # the plot area in device pixels


def _chip_step(index: int, roles: tuple[str, ...], median: float | None = None) -> PlanBarStep:
    levels = None if median is None else SectionLevels(median - 1.0, median, median + 1.0)
    return PlanBarStep(index=index, x_start=0.0, x_end=1.0, text="", color="#4e79a7", tooltip="", levels=levels, roles=roles)


def _drawn(step: PlanBarStep, left: float = 100.0, right: float = 300.0, top: float = 0.0, bottom: float = 24.0) -> DrawnStep:
    return DrawnStep(step=step, rect=QRectF(left, top, right - left, bottom - top), left_edge=left, right_edge=right, text="", text_rect=None, text_color="#ffffff")


class EvaluationChipsTest(unittest.TestCase):
    """Where the B1 / S1 / Sb chips go (`PlanBarItem.role_chips`: the layout alone, nothing is painted) and what colour they are."""

    def _bar(self, steps: list[PlanBarStep], **settings) -> PlanBarItem:
        bar = PlanBarItem()
        bar.set_plan(steps, PlanBarSettings(**settings))
        return bar

    def _metrics(self, bar: PlanBarItem) -> tuple[QFontMetricsF, float]:
        metrics = QFontMetricsF(bar._chip_font())
        return metrics, metrics.height() + 2.0

    def test_the_default_is_where_the_chips_always_were(self) -> None:
        """Beside the bar at its left end: below a bar at the top, above a bar at the bottom, inside a full bar at its top; two pixels from the step's end, 3 px between chips."""
        step = _chip_step(0, ("B1", "S1"))
        bar = self._bar([step])
        metrics, height = self._metrics(bar)
        first, second = bar.role_chips([_drawn(step)], AREA)
        self.assertEqual((first.role, second.role), ("B1", "S1"))
        self.assertAlmostEqual(first.rect.left(), 102.0)
        self.assertAlmostEqual(first.rect.top(), 26.0)  # the bar's bottom (24) + 2
        self.assertAlmostEqual(first.rect.width(), metrics.horizontalAdvance("B1") + 8.0)
        self.assertAlmostEqual(first.rect.height(), height)
        self.assertAlmostEqual(second.rect.left(), first.rect.right() + 3.0)
        bottom = self._bar([step], position=POSITION_BOTTOM)
        chip = bottom.role_chips([_drawn(step, top=376.0, bottom=400.0)], AREA)[0]
        self.assertAlmostEqual(chip.rect.top(), 376.0 - 2.0 - height)  # above a bar at the bottom
        full = self._bar([step], height_stop=HEIGHT_FULL)
        chip = full.role_chips([_drawn(step, top=0.0, bottom=400.0)], AREA)[0]
        self.assertAlmostEqual(chip.rect.top(), 2.0)  # inside a full bar, at its top

    def test_every_edge_and_side_puts_the_chips_in_that_corner_of_the_step(self) -> None:
        step = _chip_step(0, ("B1", "S1", "Sb"))
        probe = self._bar([step])
        _metrics, height = self._metrics(probe)
        # (bar position, chips edge) -> top of the chips
        expected_top = {
            (POSITION_TOP, "auto"): 26.0, (POSITION_TOP, POSITION_TOP): 26.0, (POSITION_TOP, POSITION_BOTTOM): 400.0 - 2.0 - height,
            (POSITION_BOTTOM, "auto"): 376.0 - 2.0 - height, (POSITION_BOTTOM, POSITION_BOTTOM): 376.0 - 2.0 - height, (POSITION_BOTTOM, POSITION_TOP): 2.0,
        }
        for (position, edge), top in expected_top.items():
            bar_top, bar_bottom = (0.0, 24.0) if position == POSITION_TOP else (376.0, 400.0)
            for side in ("left", "right"):
                with self.subTest(bar=position, edge=edge, side=side):
                    bar = self._bar([step], position=position, roles_edge=edge, roles_side=side)
                    chips = bar.role_chips([_drawn(step, top=bar_top, bottom=bar_bottom)], AREA)
                    self.assertEqual([c.role for c in chips], ["B1", "S1", "Sb"])  # the same order read left to right on either side
                    self.assertTrue(all(abs(c.rect.top() - top) < 1e-6 for c in chips))
                    if side == "left":
                        self.assertAlmostEqual(chips[0].rect.left(), 102.0)
                    else:
                        self.assertAlmostEqual(chips[-1].rect.right(), 298.0)  # two pixels in from the step's right end

    def test_a_full_bar_can_have_its_chips_at_the_bottom_too(self) -> None:
        step = _chip_step(0, ("S1",))
        _m, height = self._metrics(self._bar([step]))
        full = dict(height_stop=HEIGHT_FULL)
        drawn = [_drawn(step, top=0.0, bottom=400.0)]
        self.assertAlmostEqual(self._bar([step], roles_edge=POSITION_BOTTOM, **full).role_chips(drawn, AREA)[0].rect.top(), 400.0 - 2.0 - height)
        self.assertAlmostEqual(self._bar([step], roles_edge=POSITION_TOP, **full).role_chips(drawn, AREA)[0].rect.top(), 2.0)
        self.assertAlmostEqual(self._bar([step], position=POSITION_BOTTOM, **full).role_chips(drawn, AREA)[0].rect.top(), 2.0)  # auto: at the top, as always

    def test_the_markers_can_be_hidden_and_a_step_without_roles_has_no_chip(self) -> None:
        with_roles, without = _chip_step(0, ("B1",)), _chip_step(1, ())
        self.assertEqual(self._bar([with_roles], show_roles=False).role_chips([_drawn(with_roles)], AREA), [])
        chips = self._bar([with_roles, without]).role_chips([_drawn(with_roles), _drawn(without, left=300.0, right=500.0)], AREA)
        self.assertEqual([c.step_index for c in chips], [0])

    def test_a_pair_is_one_colour_with_the_baseline_darker_and_pairs_differ(self) -> None:
        steps = [_chip_step(0, ("B1",)), _chip_step(1, ("S1",)), _chip_step(2, ("B2",)), _chip_step(3, ("S2",))]
        chips = {c.role: c for c in self._bar(steps).role_chips([_drawn(s, left=100.0 * (i + 1), right=100.0 * (i + 1) + 90.0) for i, s in enumerate(steps)], AREA)}
        self.assertEqual(chips["S1"].fill, role_colors.pair_color(1))
        self.assertEqual(chips["S2"].fill, role_colors.pair_color(2))
        self.assertEqual(chips["B1"].fill, role_colors.darken(role_colors.pair_color(1)))
        self.assertNotEqual(chips["S1"].fill, chips["S2"].fill)
        self.assertLess(chips["B1"].alpha, chips["S1"].alpha)  # dimmer as well as darker
        self.assertFalse(any(c.outlined for c in chips.values()))

    def test_the_bulk_chips_are_greys_from_dark_to_white_by_the_level_of_their_step(self) -> None:
        steps = [_chip_step(0, ("B1",), 600.0), _chip_step(1, ("Sb",), 601.0), _chip_step(2, ("S1", "Sb"), 603.0), _chip_step(3, ("Sb",), 600.5), _chip_step(4, ("Sb",), None)]
        bar = self._bar(steps)
        drawn = [_drawn(s, left=100.0 * (i + 1), right=100.0 * (i + 1) + 90.0) for i, s in enumerate(steps)]
        bulk = {c.step_index: c for c in bar.role_chips(drawn, AREA) if c.role == "Sb"}
        level = {i: int(c.fill[1:3], 16) for i, c in bulk.items()}
        self.assertEqual(bulk[2].fill, "#ffffff")  # the highest level of the S_bulk steps (603): white
        self.assertEqual(level[3], role_colors.GRAY_DARK)  # the lowest (600.5; step 0 is not an S_bulk step, its 600 does not count): dark
        self.assertAlmostEqual(level[1], role_colors.GRAY_DARK + 0.2 * (role_colors.GRAY_WHITE - role_colors.GRAY_DARK), delta=1.0)  # 601 is a fifth of the way from 600.5 to 603
        self.assertTrue(level[3] < level[1] < level[2])  # 600.5 < 601 < 603: the order of the shift is the order of the greys
        self.assertEqual(bulk[4].fill, "#808080")  # no data in the step: the middle grey
        self.assertTrue(all(c.outlined for c in bulk.values()))  # a white or a black chip needs an edge on a plot of either colour
        self.assertTrue(all(c.alpha == 1.0 for c in bulk.values()))

    def test_the_greys_do_not_change_when_only_some_steps_are_in_view(self) -> None:
        """Panning or zooming hands `role_chips` fewer steps; the greys are judged over the whole plan (`set_plan`), so a chip never changes colour while the view moves."""
        steps = [_chip_step(0, ("Sb",), 600.0), _chip_step(1, ("Sb",), 602.0), _chip_step(2, ("Sb",), 604.0)]
        bar = self._bar(steps)
        everything = {c.step_index: c.fill for c in bar.role_chips([_drawn(s, left=100.0 * (i + 1), right=100.0 * (i + 1) + 90.0) for i, s in enumerate(steps)], AREA)}
        only_middle = {c.step_index: c.fill for c in bar.role_chips([_drawn(steps[1])], AREA)}
        self.assertEqual(only_middle[1], everything[1])
        self.assertNotIn(everything[1], ("#ffffff", "#808080"))  # in between: neither end of the scale

    def test_an_unknown_chip_is_neutral(self) -> None:
        step = _chip_step(0, ("X9",))
        (chip,) = self._bar([step]).role_chips([_drawn(step)], AREA)
        self.assertEqual((chip.fill, chip.outlined), ("#e6edf3", False))  # the text colour at low opacity, as the baseline chip was before the pairs had colours
        self.assertAlmostEqual(chip.alpha, 0.43)


# -- the tab in the panel -------------------------------------------------------------------------------------------------------------


class ViewTabPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.m.session_autosave._suspended = True
        self.workflow = AnalysisTab()
        app_rewrite._wire_analysis_scan(self.m, self.workflow, UiStateStore({}))
        self.m.scan_results._timer.setInterval(30)
        self.m.cube_display.set_mode(MODE_CUBE)
        self.panel = self._new_panel()
        self.panel.resize(1300, 480)
        self.panel.show()
        finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: finished.append(args))
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.m.selection.set_roi_selection({1, 2})
        self._settle()
        undo_manager.clear()
        self.view = self.panel._view_controls
        self._others: list[SensorgramPanel] = []

    def tearDown(self) -> None:
        for other in self._others:
            other.shutdown()
            other.deleteLater()
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _new_panel(self) -> SensorgramPanel:
        return SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask, experiment_plan=self.m.experiment_plan,
        )

    def _settle(self) -> None:
        _APP.processEvents()
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None and not self.panel._redraw_timer.isActive()), "the panel never drew")
        _APP.processEvents()

    def _set(self, **changes) -> None:
        self.m.analysis_settings.set_statistics_settings(replace(self.m.analysis_settings.statistics_settings(), **changes))
        self._settle()

    def test_the_view_tab_sits_between_plot_and_plan_and_holds_every_view_group(self) -> None:
        tabs = self.panel._ribbon_tabs
        self.assertEqual(tabs._category_names, ["Plot", "View", "Experimental plan"])
        view, plot_row, plan = self.panel._view_ribbon, self.panel._ribbon, self.panel._plan_ribbon
        for widget in (
            self.view.band, self.view.anchors, self.panel._mask_toggle, self.panel._stale_toggle, self.view.lines_menu, self.view.legend_menu,
            *self.panel._steps.plan_controls.widgets(), *self.panel._steps.stats_controls.widgets(),
        ):
            self.assertTrue(view.isAncestorOf(widget), widget)
            self.assertFalse(plot_row.isAncestorOf(widget) or plan.isAncestorOf(widget), widget)  # moved, not copied
        tabs.set_category("View")
        self.assertEqual(self.panel.current_ribbon_tab(), "View")
        self.assertFalse(self.panel._steps.actions.structure_tools_active())  # the plan's structure tools stay for the Experimental plan tab

    def test_the_options_reach_the_plot_and_the_resets_put_the_defaults_back_in_one_change(self) -> None:
        self.assertEqual(self.panel._plot.view(), SensorgramViewSettings())  # untouched: the plot looks as it always did
        self.view.point_size.setValue(8)
        self.view.line_width.setValue(2.5)
        self.view.show_points.setChecked(False)
        self.view.show_lines.setChecked(False)
        self.view._style_buttons["spline"].click()
        self.view._mode_buttons["show"].click()
        self.view.legend_text.setValue(12)
        self.view.legend_spacing.setValue(5)
        self.view.legend_counts.setChecked(False)
        self.view.band.setChecked(False)
        self.view.anchors.setChecked(False)
        self.assertEqual(
            self.panel._plot.view(),
            SensorgramViewSettings(
                show_band=False, show_anchors=False, show_points=False, show_lines=False, point_size=8, line_width=2.5, line_style="spline",
                legend_mode="show", legend_text_pt=12, legend_spacing_px=5, legend_counts=False,
            ),
        )
        changes: list[int] = []
        self.view.settings_changed.connect(lambda: changes.append(1))
        self.view.lines_menu.findChild(QToolButton, "view_lines_reset").click()
        view = self.panel._plot.view()
        self.assertEqual((view.show_points, view.show_lines, view.point_size, view.line_width, view.line_style), (True, True, 4, 1.0, "straight"))
        self.assertEqual(len(changes), 1)  # five controls, one change
        self.view.legend_menu.findChild(QToolButton, "view_legend_reset").click()
        self.assertEqual(
            (self.view.legend_mode(), self.panel._plot.view().legend_text_pt, self.panel._plot.view().legend_spacing_px, self.panel._plot.view().legend_counts),
            ("auto", 9, 0, True),
        )
        self.assertEqual(len(changes), 2)
        self.assertEqual((self.panel._plot.view().show_band, self.panel._plot.view().show_anchors), (False, False))  # a reset touches only its own menu

    def test_the_band_toggle_is_only_for_an_average_and_hides_its_band(self) -> None:
        self.assertFalse(self.view.band.isEnabled())  # one line per ROI: no band to show
        self.assertIn("one line per ROI", self.view.band.toolTip())
        self._set(sensorgram_display_mode=DISPLAY_AVERAGE_ALL)
        self.assertTrue(self.view.band.isEnabled())
        self.assertGreater(len(_parts(self.panel._plot, "Bands")), 0)
        self.view.band.click()
        self.assertEqual(_parts(self.panel._plot, "Bands"), [])
        self.assertEqual(len(_parts(self.panel._plot, "Lines")), 1)  # the average itself stays
        self.view.band.click()
        self.assertGreater(len(_parts(self.panel._plot, "Bands")), 0)

    def test_the_anchors_toggle_hides_the_squares_and_the_dashed_baseline_curve(self) -> None:
        """Maintainer, 2026-11-13: the toggle of the anchors also owns the baseline made from them."""
        self.assertFalse(self.view.anchors.isEnabled())  # the Range method with nothing marked: no anchors, no curve
        self._set(baseline_method=METHOD_ANCHORS, baseline_shape="constant", baseline_point_halfwidth=0)
        self.assertTrue(self.view.anchors.isEnabled())
        self.panel._drift.add_point_at(float(self.panel._x_axis()[1][1]))
        self._settle()
        self.assertEqual(len(_parts(self.panel._plot, "Baseline anchors")), 2)  # a square item per line
        self.assertEqual(len(_parts(self.panel._plot, "Baseline curves")), 2)  # and a dashed curve per line (Subtract is off)
        self.view.anchors.click()
        self.assertEqual(_parts(self.panel._plot, "Baseline anchors"), [])
        self.assertEqual(_parts(self.panel._plot, "Baseline curves"), [])
        self.assertEqual(len(self.m.analysis_settings.statistics_settings().baseline_anchor_points), 1)  # hiding them removes no point ...
        self.assertTrue(all(line.baseline_curve is not None for line in self.panel._last[1]))  # ... and the curve is still made, only not drawn
        self.view.anchors.click()
        self.assertEqual((len(_parts(self.panel._plot, "Baseline anchors")), len(_parts(self.panel._plot, "Baseline curves"))), (2, 2))

    def test_the_anchors_toggle_also_owns_the_range_methods_curve_and_is_off_without_marked_steps(self) -> None:
        steps = self.m.experiment_plan.steps()
        self._set(baseline_method=METHOD_WINDOW)
        self.assertFalse(self.view.anchors.isEnabled())
        self.assertIn("neither Anchor points nor Range", self.view.anchors.toolTip())
        self._set(baseline_method=METHOD_WINDOW, baseline_anchor_steps=(tuple(steps[0].ref),), baseline_shape="constant", baseline_step_trim=0)
        self.assertTrue(self.view.anchors.isEnabled())  # a marked step is an anchor with a curve of its own
        self.assertEqual(len(_parts(self.panel._plot, "Baseline curves")), 2)
        self.assertEqual(_parts(self.panel._plot, "Baseline anchors"), [])  # the Range method draws no squares
        self.view.anchors.click()
        self.assertEqual(_parts(self.panel._plot, "Baseline curves"), [])
        self.view.anchors.click()
        self.assertEqual(len(_parts(self.panel._plot, "Baseline curves")), 2)

    def test_a_subtracted_baseline_draws_no_curve_with_or_without_the_toggle(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS, baseline_shape="constant", baseline_point_halfwidth=0, baseline_enabled=True)
        self.panel._drift.add_point_at(float(self.panel._x_axis()[1][1]))
        self._settle()
        self.assertEqual(_parts(self.panel._plot, "Baseline curves"), [])  # what Subtract took off is not drawn: the traces are already corrected
        self.assertEqual(len(_parts(self.panel._plot, "Baseline anchors")), 2)  # the squares are (near zero)
        self.view.anchors.click()
        self.assertEqual(_parts(self.panel._plot, "Baseline anchors"), [])

    def test_every_option_is_remembered_and_comes_back_in_another_panel(self) -> None:
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        self.view.point_size.setValue(7)
        self.view.line_width.setValue(1.5)
        self.view.show_points.setChecked(False)
        self.view.show_lines.setChecked(False)
        self.view._style_buttons["spline"].click()
        self.view._mode_buttons["hide"].click()
        self.view.legend_text.setValue(11)
        self.view.legend_spacing.setValue(-2)
        self.view.legend_counts.setChecked(False)
        self.view.band.setChecked(False)
        self.view.anchors.setChecked(False)
        self.panel._ribbon_tabs.set_category("View")
        saved = dict(store._values)
        self.assertEqual(saved["sensorgram/legend_mode"], "hide")
        self.assertEqual(saved["sensorgram/line_style"], "spline")
        self.assertEqual(saved["sensorgram/ribbon_tab"], "View")
        other = self._new_panel()
        self._others.append(other)
        other.restore_ui_state(UiStateStore(saved))
        self.assertEqual(other._plot.view(), self.panel._plot.view())  # the restored options are the ones the plot draws with
        self.assertEqual(
            other._plot.view(),
            SensorgramViewSettings(
                show_band=False, show_anchors=False, show_points=False, show_lines=False, point_size=7, line_width=1.5, line_style="spline",
                legend_mode="hide", legend_text_pt=11, legend_spacing_px=-2, legend_counts=False,
            ),
        )
        self.assertEqual(other.current_ribbon_tab(), "View")

    def test_a_reset_is_remembered_too(self) -> None:
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        self.view._mode_buttons["show"].click()
        self.view._style_buttons["spline"].click()
        self.assertEqual((store._values["sensorgram/legend_mode"], store._values["sensorgram/line_style"]), ("show", "spline"))
        self.view.legend_menu.findChild(QToolButton, "view_legend_reset").click()  # presses Auto in code: no click reaches the button group
        self.view.lines_menu.findChild(QToolButton, "view_lines_reset").click()  # and Straight
        self.assertEqual((store._values["sensorgram/legend_mode"], store._values["sensorgram/line_style"]), ("auto", "straight"))

    def test_the_chain_in_both_menus_links_the_legend_and_stats_text_sizes(self) -> None:
        """Maintainer, 2026-11-13: link the legend's text size and the stats' text size together."""
        stats = self.panel._steps.stats_controls
        self.assertIn(self.view.text_link, self.view.legend_menu.findChildren(QtWidgets.QWidget))  # a chain beside each size box
        self.assertIn(stats.text_link, stats.menu.findChildren(QtWidgets.QWidget))
        self.assertFalse(self.view.text_link.isChecked() or stats.text_link.isChecked())  # off at the start: nothing changes until it is used
        self.view.legend_text.setValue(12)
        stats.size_box.setValue(9)
        self.assertEqual((self.view.legend_text.value(), stats.size_box.value()), (12, 9))  # independent as ever
        stats.text_link.click()  # pressed in the Stats menu: the stats' size is the one both take
        self.assertEqual((self.view.legend_text.value(), stats.size_box.value()), (9, 9))
        self.assertTrue(self.view.text_link.isChecked())  # one state in both menus
        self.view.legend_text.setValue(15)
        self._settle()
        self.assertEqual(stats.settings().text_pt, 15)  # the stats numbers follow ...
        self.assertEqual(self.panel._plot.stats_item().settings().text_pt, 15)  # ... on the plot ...
        self.assertEqual(self.panel._plot.view().legend_text_pt, 15)  # ... and the legend is the size it was set to
        stats.size_box.setValue(11)
        self._settle()
        self.assertEqual((self.view.legend_text.value(), self.panel._plot.view().legend_text_pt), (11, 11))  # and the other way round

    def test_the_legend_can_ignore_the_bar_from_its_menu_and_the_reset_puts_that_and_the_link_back(self) -> None:
        self.assertIn("Position", [label.text() for label in self.view.legend_menu.findChildren(QtWidgets.QLabel)])
        self.assertFalse(self.panel._plot.view().legend_over_bar)  # as the legend always was
        self.view.legend_over_bar.setChecked(True)
        self.assertTrue(self.panel._plot.view().legend_over_bar)
        stats = self.panel._steps.stats_controls
        self.view.legend_text.setValue(15)
        self.view.text_link.click()  # linked: the stats size is 15 too
        self.assertTrue(stats.text_link.isChecked())
        self.assertEqual(stats.size_box.value(), 15)
        self.view.legend_menu.findChild(QToolButton, "view_legend_reset").click()
        self.assertFalse(self.panel._plot.view().legend_over_bar)
        self.assertFalse(self.view.text_link.isChecked() or stats.text_link.isChecked())  # unlinked, in both menus
        self.assertEqual(self.view.legend_text.value(), 9)  # the legend's size is back at its default ...
        self.assertEqual(stats.size_box.value(), 15)  # ... and the stats size is not part of the legend's reset: it keeps what it had (unlinked first, so it was not dragged along)

    def test_the_link_and_the_legend_position_are_remembered_and_a_restored_linked_pair_is_equal(self) -> None:
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        self.view.legend_over_bar.setChecked(True)
        self.view.legend_text.setValue(13)
        self.view.text_link.click()
        self.assertEqual((store._values["sensorgram/text_size_linked"], store._values["sensorgram/view_legend_over_bar"]), (True, True))
        saved = dict(store._values)
        saved["sensorgram/stats_text_pt"] = 7  # a stored pair that disagrees: the link makes it equal again (the Legend menu's size wins)
        other = self._new_panel()
        self._others.append(other)
        other.restore_ui_state(UiStateStore(saved))
        self.assertTrue(other._view_controls.text_link.isChecked() and other._steps.stats_controls.text_link.isChecked())
        self.assertTrue(other._plot.view().legend_over_bar)
        self.assertEqual((other._view_controls.legend_text.value(), other._steps.stats_controls.size_box.value()), (13, 13))

    def test_the_stats_menu_has_a_legend_room_in_steps_of_25_percent_with_off_and_remembers_it(self) -> None:
        stats = self.panel._steps.stats_controls
        self.assertEqual((stats._room.value(), stats._room.singleStep(), stats._room.minimum()), (100, 25, 0))
        self.assertIn("Legend room", [label.text() for label in stats.menu.findChildren(QtWidgets.QLabel)])
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        stats._room.setValue(50)
        self._settle()
        self.assertEqual(self.panel._plot.stats_item().settings().legend_room_pct, 50)  # it reaches the plot's table
        stats._room.setValue(0)
        self.assertEqual(stats._room.text(), "off")  # not "0 %"
        self.assertEqual(stats.settings().legend_room_pct, 0)
        self.assertEqual(store._values["sensorgram/stats_legend_room"], 0)
        other = self._new_panel()
        self._others.append(other)
        other.restore_ui_state(UiStateStore(dict(store._values)))
        self.assertEqual(other._steps.stats_controls.settings().legend_room_pct, 0)

    def test_the_evaluation_menu_shows_hides_and_places_the_markers_and_remembers_it(self) -> None:
        """Maintainer, 2026-11-13: a new Evaluation section for the B1 / S1 / Sb markers: shown or hidden, and where on their step (the dice picker: corners and the middle = auto)."""
        controls = self.panel._steps.plan_controls
        self.assertTrue(self.panel._view_ribbon.isAncestorOf(controls.evaluation_menu))
        self.assertFalse(self.panel._plan_ribbon.isAncestorOf(controls.evaluation_menu))
        self.assertEqual(controls.evaluation_widgets(), (controls.evaluation_menu,))
        self.assertFalse(controls.evaluation_menu.icon().isNull())
        inside = controls.evaluation_menu.findChildren(QtWidgets.QWidget)
        for widget in (controls._show_roles, controls._roles_place, *controls._roles_place.buttons.values()):
            self.assertIn(widget, inside)
        captions = [label.text() for label in controls.evaluation_menu.findChildren(QtWidgets.QLabel) if label.text()]
        self.assertEqual(captions[:2], ["Markers", "Place"])  # two rows now: the Edge and Side rows became the one picture
        settings = controls.settings()
        self.assertEqual((settings.show_roles, settings.roles_edge, settings.roles_side), (True, "auto", "left"))  # untouched: as the chips always were

        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        controls._show_roles.setChecked(False)
        controls._roles_place.buttons["bottom-right"].click()
        self._settle()
        bar = self.panel._plot.plan_bar().settings()
        self.assertEqual((bar.show_roles, bar.roles_edge, bar.roles_side), (False, "bottom", "right"))  # the plan bar draws with them
        saved = dict(store._values)
        self.assertEqual((saved["sensorgram/plan_roles_visible"], saved["sensorgram/plan_roles_place"]), (False, "bottom-right"))  # one remembered place, not an edge and a side
        other = self._new_panel()
        self._others.append(other)
        other.restore_ui_state(UiStateStore(saved))
        restored = other._steps.plan_controls.settings()
        self.assertEqual((restored.show_roles, restored.roles_edge, restored.roles_side), (False, "bottom", "right"))
        self.assertEqual(other._steps.plan_controls.roles_place(), "bottom-right")

    def test_the_dice_has_five_pips_like_a_5_and_exactly_one_is_pressed(self) -> None:
        picker = self.panel._steps.plan_controls._roles_place
        self.assertEqual(set(picker.buttons), {"top-left", "top-right", "auto", "bottom-left", "bottom-right"})
        grid = picker.layout()
        cells = {place: (grid.getItemPosition(grid.indexOf(button))[0], grid.getItemPosition(grid.indexOf(button))[1]) for place, button in picker.buttons.items()}
        self.assertEqual(cells, {"top-left": (0, 0), "top-right": (0, 2), "auto": (1, 1), "bottom-left": (2, 0), "bottom-right": (2, 2)})  # the corners and the middle of a 3 x 3 grid
        self.assertEqual(picker.place(), "auto")  # the middle at the start: as the chips always were
        self.assertEqual([place for place, button in picker.buttons.items() if button.isChecked()], ["auto"])
        picker.buttons["top-right"].click()
        self.assertEqual([place for place, button in picker.buttons.items() if button.isChecked()], ["top-right"])  # one at a time
        picker.buttons["top-right"].click()
        self.assertEqual(picker.place(), "top-right")  # pressing the pressed pip again keeps it
        for button in picker.buttons.values():
            self.assertTrue(button.toolTip())
            self.assertFalse(button.icon().isNull())
        self.assertNotEqual(picker.buttons["top-right"].icon().pixmap(32, 32).toImage(), picker.buttons["top-left"].icon().pixmap(32, 32).toImage())  # solid against a ring

    def test_the_dice_is_compact_and_stays_compact_after_a_theme_change(self) -> None:
        """Maintainer, 2026-11-13: "it takes too much space, the points are far apart": 16 px cells with no gap (48 px), not ribbon-sized buttons with gaps (76 px)."""
        picker = self.panel._steps.plan_controls._roles_place
        self.panel._steps.plan_controls.evaluation_menu.show()
        for _round in range(2):  # the second time after `refresh_theme`, which gives an `IconToggle` back the ribbon's 24 px unless the pip puts its size back
            for place, button in picker.buttons.items():
                self.assertEqual((button.width(), button.height()), (16, 16), place)
                self.assertLessEqual(button.iconSize().width(), 14)
            hint = picker.sizeHint()
            self.assertLessEqual((hint.width(), hint.height()), (48, 48))  # three cells of 16 px, nothing between them
            picker.refresh_theme()
        picker.setGeometry(0, 0, picker.sizeHint().width(), picker.sizeHint().height())  # the menu was never opened, so its layout has not run: lay it out
        picker.layout().activate()
        centres = {place: button.geometry().center() for place, button in picker.buttons.items()}
        self.assertEqual(centres["top-right"].x() - centres["top-left"].x(), 32)  # corners two cells apart: the pips sit 16 px from each other
        self.assertEqual(centres["bottom-left"].y() - centres["top-left"].y(), 32)
        self.assertEqual((centres["auto"].x() - centres["top-left"].x(), centres["auto"].y() - centres["top-left"].y()), (16, 16))  # the middle one cell in

    def test_every_pip_gives_its_edge_and_side_to_the_plan_bar_and_a_changed_signal_goes_out_once(self) -> None:
        controls = self.panel._steps.plan_controls
        changes: list[int] = []
        controls.settings_changed.connect(lambda: changes.append(1))
        expected = {"top-left": ("top", "left"), "top-right": ("top", "right"), "auto": ("auto", "left"), "bottom-left": ("bottom", "left"), "bottom-right": ("bottom", "right")}
        for place, pair in expected.items():
            before = len(changes)
            controls._roles_place.buttons[place].click()
            settings = controls.settings()
            self.assertEqual((settings.roles_edge, settings.roles_side), pair, place)
            self.assertEqual(len(changes) - before, 1, place)  # one pip pressed, one change

    def test_an_unknown_remembered_place_leaves_the_middle(self) -> None:
        store = UiStateStore({"sensorgram/plan_roles_place": "diagonal"})
        self.panel.restore_ui_state(store)
        self.assertEqual(self.panel._steps.plan_controls.roles_place(), "auto")

    def test_the_evaluation_pop_ups_role_labels_have_the_colours_of_their_chips(self) -> None:
        """The pop-up on the Experimental plan tab, where the pairs are set, names its rows B1 / S1 ... in the pair's colour, so a row and its chips on the bar are seen to belong together."""
        labels = self.panel._steps.evaluation._labels
        self.assertEqual([label.text() for label in labels[:2]], ["B1", "S1"])
        self.assertIn(role_colors.pair_color(1), labels[1].styleSheet())  # the signal: the pair's colour itself
        self.assertIn(role_colors.darken(role_colors.pair_color(1), 0.75), labels[0].styleSheet())  # the baseline: the same hue, darker
        self.assertNotEqual(labels[0].styleSheet(), labels[1].styleSheet())

    def test_the_evaluation_menu_is_greyed_out_without_steps_like_the_other_plan_menus(self) -> None:
        controls = self.panel._steps.plan_controls
        controls.set_available(False)
        self.assertFalse(controls.evaluation_menu.isEnabled())
        self.assertIn("no experiment steps", controls.evaluation_menu.toolTip())
        controls.set_available(True)
        self.assertTrue(controls.evaluation_menu.isEnabled())
        self.assertNotIn("no experiment steps", controls.evaluation_menu.toolTip())

    def test_the_steps_and_stats_menus_are_the_same_kind_of_menu_as_the_others(self) -> None:
        """The Steps and Stats groups are menus like the Legend menu: one icon each, every control inside, greyed out together without steps."""
        steps, stats = self.panel._steps.plan_controls, self.panel._steps.stats_controls
        for controls in (steps, stats):
            self.assertEqual(controls.widgets(), (controls.menu,))
            self.assertIsInstance(controls.menu, type(self.view.legend_menu))  # the one shared GroupMenuButton
            self.assertFalse(controls.menu.icon().isNull())
        self.assertIs(type(steps.menu), type(self.view.lines_menu))
        self.assertTrue(steps.menu.isEnabled() and stats.menu.isEnabled())  # this dataset has steps
        steps.set_available(False)
        stats.set_available(False)
        self.assertFalse(steps.menu.isEnabled() or stats.menu.isEnabled())
        self.assertIn("no experiment steps", steps.menu.toolTip())
        self.assertIn("no experiment steps", stats.menu.toolTip())
        steps.set_available(True)
        stats.set_available(True)
        self.assertTrue(steps.menu.isEnabled() and stats.menu.isEnabled())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
