"""The Sensorgram's step statistics table (2026-10-26): mean +/- SD of every line in every experiment step, written on the plot.

Two layers, driven through methods and signals, never by screen coordinate:
- `StepStatsItem.layout`: what is written where, on its own (a given data -> device transform, no plot);
- `SensorgramPanel` with the real modules and a real scan on the small two-step dataset of `test_lspri_plan_bar` (6 cubes, steps = cubes 0-2 and 3-5): the group in the ribbon,
  the table, its rows level with the legend, the room made for it, top and bottom, switching it, a moved boundary, too many lines, the remembered state.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QFontMetricsF, QTransform

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.analysis.step_stats import LineStepValues
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.sensorgram.stat_icons import step_stats_icon
    from lspr_imaging_app.panels.sensorgram.stats_table import StepStatsItem
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.bar_model import PlanBarStep
    from lspr_imaging_app.plan.stats_table import PLUS_MINUS, StatsLine, StatsTableSettings, build_stats_table
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_plan_bar import CENTERS, CUBE_MS, T0, _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()

NAN = float("nan")
_SIDE_MARGIN_SLACK = 3.5  # the cell keeps a 3 px margin from the legend's edge, which moves the middle of a step that starts at it by up to that


def _bar(index: int, x0: float, x1: float) -> PlanBarStep:
    return PlanBarStep(index=index, x_start=x0, x_end=x1, text=f"step {index}", color="#ff0000", tooltip="", levels=None)


# -- the item on its own ----------------------------------------------------------------------------------------------------------


class StepStatsItemLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        bars = [_bar(0, 0.0, 10.0), _bar(1, 10.0, 20.0), _bar(2, 20.0, 30.0)]
        rows = [
            StatsLine("ROI 1", "#ff8800", (591.41, 593.52, NAN), (0.14, 0.09, NAN), (32, 27, 0), False),
            StatsLine("ROI 2", "#0088ff", (580.12, 582.31, 584.55), (0.2, 0.11, 0.1), (32, 27, 21), False),
        ]
        self.table = build_stats_table(bars, rows)
        self.item = StepStatsItem()
        self.view = QRectF(0.0, 0.0, 30.0, 20.0)

    def _cells(self, scale: float, position: str = "top"):
        self.item.set_table(self.table, StatsTableSettings(position=position, text_pt=9))
        return self.item.layout(QTransform().scale(scale, scale), self.view)

    def test_a_cell_per_number_in_the_middle_of_its_step_and_one_row_per_line(self) -> None:
        cells = self._cells(10.0)  # 100 px per step
        self.assertEqual(len(cells), 5)  # ROI 1 has no number in step 3
        for cell in cells:
            self.assertAlmostEqual(cell.rect.center().x(), 100.0 * cell.column.index + 50.0, delta=0.6)
            self.assertIn(PLUS_MINUS, cell.text)  # a hundred pixels are plenty
        by_line = {line: [c for c in cells if c.line == line] for line in (0, 1)}
        self.assertTrue(all(a.rect.center().y() < b.rect.center().y() for a, b in zip(by_line[0], by_line[1])))  # the first line above the second
        self.assertEqual({c.color for c in by_line[0]}, {"#ff8800"})
        self.assertEqual({c.color for c in by_line[1]}, {"#0088ff"})

    def test_narrow_steps_get_shorter_text_and_keep_the_chosen_size(self) -> None:
        self.assertEqual({c.pt for c in self._cells(10.0)}, {9.0})  # room enough: the chosen size, with the SD
        metrics = QFontMetricsF(self.item.font(9.0))
        mean_only = max(metrics.horizontalAdvance(t) for t in ("591.4", "593.5", "580.1", "582.3", "584.6"))
        short_with_sd = min(metrics.horizontalAdvance(t) for t in ("591.4 ± 0.1", "593.5 ± 0.1", "580.1 ± 0.2", "582.3 ± 0.1", "584.6 ± 0.1"))
        cells = self._cells((mean_only * 1.15 + 7.0) / 10.0)  # holds the mean at 9 pt, but not the mean with its SD
        self.assertLess(mean_only * 1.15, short_with_sd)
        self.assertEqual(len(cells), 5)
        self.assertTrue(all(c.text and PLUS_MINUS not in c.text for c in cells))  # the SD is what goes, not the font size
        self.assertEqual({c.pt for c in cells}, {9.0})

    def test_a_step_too_narrow_for_anything_keeps_its_cell_and_the_chosen_size(self) -> None:
        narrow = self._cells(2.5)  # 25 px: not even the mean fits
        self.assertEqual(len(narrow), 5)
        self.assertTrue(all(c.text == "" for c in narrow))  # the cells stay, for the tooltip
        self.assertEqual({c.pt for c in narrow}, {9.0})  # nothing to shrink the text for

    def test_the_size_is_the_one_in_the_size_box_whatever_the_numbers_and_the_steps(self) -> None:
        """The same box value must draw at the same size for every selection (it used to shrink to the narrowest step, so a ROI whose numbers were a sign or a digit longer
        was drawn at 6 pt while another was at 10 pt)."""
        short = StatsLine("ROI 1", "#ff8800", (591.41, 593.52, 584.5), (0.14, 0.09, 0.1), (32, 27, 21), False)
        long = StatsLine("ROI 1", "#ff8800", (-591.4123, -593.5234, -584.5345), (0.1412, 0.0923, 0.1034), (32, 27, 21), False)  # signs and more digits
        bars = [_bar(0, 0.0, 10.0), _bar(1, 10.0, 20.0), _bar(2, 20.0, 20.3)]  # the last step is 0.3 of a unit wide
        for pt in (6, 9, 12, 24):
            for scale in (3.0, 6.0, 10.0, 30.0):  # steps from 30 to 300 px
                drawn = set()
                for line in (short, long):
                    self.item.set_table(build_stats_table(bars, [line]), StatsTableSettings(text_pt=pt))
                    drawn |= {c.pt for c in self.item.layout(QTransform().scale(scale, 10.0), self.view)}
                self.assertEqual(drawn, {float(pt)}, (pt, scale))

    def test_the_size_chosen_in_the_size_box_is_drawn_even_where_it_does_not_fit(self) -> None:
        self.item.set_table(self.table, StatsTableSettings(text_pt=24))
        cells = self.item.layout(QTransform().scale(10.0, 10.0), self.view)  # 100 px steps: mean ± SD at 24 pt needs far more
        self.assertEqual({c.pt for c in cells}, {24.0})
        self.assertTrue(all(c.text for c in cells))  # something is written in every cell, shortened if need be

    def test_without_a_legend_the_rows_stack_from_the_edge(self) -> None:
        top = self._cells(10.0, "top")
        bottom = self._cells(10.0, "bottom")  # the view is 200 px high
        self.assertLess(max(c.rect.bottom() for c in top), 60.0)
        self.assertGreater(min(c.rect.top() for c in bottom), 140.0)

    def test_a_table_that_is_off_or_empty_draws_nothing(self) -> None:
        self.item.set_table(self.table, StatsTableSettings(visible=False))
        self.assertEqual(self.item.layout(QTransform().scale(10.0, 10.0), self.view), [])
        self.item.set_table(None, StatsTableSettings())
        self.assertEqual(self.item.layout(QTransform().scale(10.0, 10.0), self.view), [])
        self.assertFalse(self.item.is_shown())


# -- the legend, and the table with more lines than the old limit of six ------------------------------------------------------------


def _lines(count: int) -> list[Series]:
    x = np.arange(6.0)
    return [Series(f"roi:{i}", f"ROI {i}", "#ffffff", x, 600.0 + i + x) for i in range(count)]


class LegendCapacityTest(unittest.TestCase):
    """More groups (or ROIs) than six used to take the legend, and with it the table, away. Now the legend stays while it takes at most half of the plot height."""

    def _plot(self, height: int) -> SensorgramPlot:
        plot = SensorgramPlot()
        plot.resize(900, height)
        plot.show()
        _APP.processEvents()
        self.addCleanup(plot.close)
        return plot

    def test_up_to_six_lines_always_have_a_legend(self) -> None:
        plot = self._plot(160)  # a small plot
        plot.show_series(_lines(6))
        self.assertEqual(plot.legend_entry_count(), 6)

    def test_more_lines_keep_the_legend_in_a_plot_tall_enough_for_it(self) -> None:
        plot = self._plot(700)
        plot.show_series(_lines(10))
        self.assertEqual(plot.legend_entry_count(), 10)

    def test_a_legend_that_would_fill_the_plot_is_dropped(self) -> None:
        plot = self._plot(300)
        plot.show_series(_lines(10))
        self.assertEqual(plot.legend_entry_count(), 0)
        plot.show_series(_lines(3))  # and comes back when the lines fit again
        self.assertEqual(plot.legend_entry_count(), 3)

    def test_a_great_many_lines_are_never_named(self) -> None:
        plot = self._plot(1500)  # tall, but thirty entries are still a wall of text
        plot.show_series(_lines(30))
        self.assertEqual(plot.legend_entry_count(), 0)
        self.assertTrue(all(item.name() is None for item in plot._items if hasattr(item, "name")))

    def test_the_table_has_a_row_for_every_line_the_legend_names(self) -> None:
        plot = self._plot(700)
        plot.show_series(_lines(9))
        bars = [_bar(0, 0.0, 3.0), _bar(1, 3.0, 6.0)]
        rows = [StatsLine(f"ROI {i}", "#ffffff", (600.0 + i, 603.0 + i), (0.1, 0.1), (3, 3), False) for i in range(9)]
        plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings())
        _APP.processEvents()
        item = plot.stats_item()
        to_device = item._device_transform()
        centers = [to_device.map(item.mapFromScene(label.sceneBoundingRect().center())).y() for _sample, label in plot._legend.items]
        cells = item.layout(to_device, item.view_rect())
        self.assertEqual({c.line for c in cells}, set(range(9)))  # nine rows, not six
        self.assertTrue(all(abs(c.rect.center().y() - centers[c.line]) < 1.0 for c in cells))

    def test_a_legend_the_user_dragged_stays_where_it_was_while_the_table_is_off(self) -> None:
        plot = self._plot(500)
        plot.show_series(_lines(3))
        plot._legend.setOffset((120, 60))  # what a drag of the legend does
        plot.show_series(_lines(3))  # a redraw
        plot._place_legend()  # and a resize
        self.assertEqual(tuple(plot._legend.opts["offset"]), (120, 60))


# -- the panel ---------------------------------------------------------------------------------------------------------------------


class StepStatsPanelTest(unittest.TestCase):
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
        self.panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask, experiment_plan=self.m.experiment_plan,
        )
        self.panel.resize(1300, 480)
        self.panel.show()
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.m.selection.set_roi_selection({1, 2})
        self._settle()
        undo_manager.clear()
        self.plot = self.panel._plot
        self.item = self.plot.stats_item()
        self.controls = self.panel._steps.stats_controls

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _settle(self) -> None:
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None and not self.panel._redraw_timer.isActive()), "the panel never drew")
        _APP.processEvents()

    def _set_position(self, key: str) -> None:
        self.controls._position.setCurrentIndex(self.controls._position.findData(key))
        self._settle()

    def _geometry(self):
        """(to_device, view, cells, legend row centres in device y, legend right edge in device x)."""
        to_device = self.item._device_transform()
        view = self.item.view_rect()
        legend = self.plot._legend
        centers = [to_device.map(self.item.mapFromScene(label.sceneBoundingRect().center())).y() for _sample, label in legend.items]
        right = to_device.map(self.item.mapFromScene(QPointF(legend.sceneBoundingRect().right(), 0.0))).x()
        return to_device, view, self.item.layout(to_device, view), centers, right

    # -- the group ------------------------------------------------------------------------------------------------------------------

    def test_the_group_is_in_the_ribbon_on_by_default_and_has_a_pictogram(self) -> None:
        self.assertIn("Step stats", [label.text() for label in self.panel._plan_ribbon._captions])  # on the Experimental plan tab since 2026-10-29
        self.assertTrue(self.controls.is_available())
        settings = self.controls.settings()
        self.assertEqual((settings.visible, settings.position), (True, "top"))
        on, off = step_stats_icon(True, "#888888", "#3399ff"), step_stats_icon(False, "#888888", "#3399ff")
        self.assertFalse(on.isNull() or off.isNull())
        self.assertNotEqual(on.pixmap(32, 32).toImage(), off.pixmap(32, 32).toImage())

    # -- what is written ----------------------------------------------------------------------------------------------------------

    def test_a_row_per_line_and_a_column_per_step(self) -> None:
        table = self.item.table()
        self.assertEqual([line.label for line in table.lines], ["ROI 1", "ROI 2"])
        self.assertEqual(len(table.columns), 2)
        first = self.panel._last[1][0]
        self.assertEqual(table.lines[0].color, first.color)
        self.assertEqual(table.lines[0].mean, tuple(first.step_values.mean))
        self.assertEqual(table.lines[0].n, (3, 3))  # cubes 0-2 and 3-5

    def test_cells_are_in_the_middle_of_their_steps_and_level_with_the_legend_rows(self) -> None:
        to_device, view, cells, legend_centers, legend_right = self._geometry()
        self.assertEqual(len(cells), 4)
        area = to_device.mapRect(view)
        for cell in cells:
            self.assertAlmostEqual(cell.rect.center().y(), legend_centers[cell.line], delta=1.0)  # level with the marker and name of its line
            self.assertGreaterEqual(cell.rect.left(), legend_right)  # never under the legend
            self.assertLess(cell.rect.center().y(), area.center().y())  # along the top
            x0, x1 = (to_device.map(QPointF(x, 0.0)).x() for x in (cell.column.x_start, cell.column.x_end))
            visible = (max(x0, area.left(), legend_right), min(x1, area.right()))  # a step that runs past the last cube is centred on the part that is on screen
            self.assertAlmostEqual(cell.rect.center().x(), (visible[0] + visible[1]) / 2.0, delta=_SIDE_MARGIN_SLACK)  # in the middle of the step
            self.assertIn(PLUS_MINUS, cell.text)  # 1300 px: room for mean ± SD

    def test_room_is_made_on_the_left_so_the_first_step_starts_after_the_legend(self) -> None:
        to_device, _view, _cells, _centers, legend_right = self._geometry()
        first_step_left = to_device.map(QPointF(self.item.table().columns[0].x_start, 0.0)).x()
        self.assertGreaterEqual(first_step_left, legend_right)
        self.assertLess(self.plot._item.getViewBox().viewRange()[0][0], self.item.table().columns[0].x_start)

    def test_the_legend_sits_inside_the_plan_bar_not_over_it(self) -> None:
        bar = self.plot.plan_bar()
        to_device, view, _cells, _centers, _right = self._geometry()
        legend_top = to_device.map(self.item.mapFromScene(self.plot._legend.sceneBoundingRect().topLeft())).y()
        bar_bottom = to_device.mapRect(view).top() + bar.settings().height_fraction * to_device.mapRect(view).height()
        self.assertGreaterEqual(legend_top, bar_bottom - 0.5)

    # -- top and bottom -----------------------------------------------------------------------------------------------------------

    def test_at_the_bottom_the_legend_and_the_rows_move_to_the_bottom(self) -> None:
        self._set_position("bottom")
        to_device, view, cells, legend_centers, _right = self._geometry()
        area = to_device.mapRect(view)
        self.assertLess(self.plot._legend.opts["offset"][1], 0)  # anchored to the bottom edge
        self.assertEqual(len(cells), 4)
        for cell in cells:
            self.assertGreater(cell.rect.center().y(), area.center().y())
            self.assertAlmostEqual(cell.rect.center().y(), legend_centers[cell.line], delta=1.0)

    def test_the_traces_keep_clear_of_the_rows(self) -> None:
        _to_device, view, cells, _centers, _right = self._geometry()
        to_device = self.item._device_transform()
        data_top = max(float(np.nanmax(s.y)) for s in self.panel._last[1])
        y_of_data_top = to_device.map(QPointF(0.0, data_top)).y()
        self.assertGreater(y_of_data_top, max(c.rect.bottom() for c in cells))  # the highest trace point is below the lowest row (the device y grows downwards)

    # -- switching it ---------------------------------------------------------------------------------------------------------------

    def test_switching_it_off_gives_back_the_room_and_the_legend(self) -> None:
        shown_low = self.plot._item.getViewBox().viewRange()[0][0]
        self.controls._show.setChecked(False)
        self._settle()
        self.assertFalse(self.item.is_shown())
        self.assertEqual(tuple(self.plot._legend.opts["offset"]), (8, 8))
        self.assertGreater(self.plot._item.getViewBox().viewRange()[0][0], shown_low)  # no room kept on the left

    def test_switching_it_on_asks_for_the_numbers(self) -> None:
        self.controls._show.setChecked(False)
        self.panel._schedule_redraw()  # switching it off recomputes nothing; a later redraw is made without the numbers
        self._settle()
        self.assertTrue(all(s.step_values is None for s in self.panel._last[1]))  # nothing was asked for
        self.controls._show.setChecked(True)
        self._settle()
        self.assertTrue(_pump(lambda: self.item.table() is not None))
        self.assertEqual(len(self.item.table().lines), 2)

    # -- the numbers follow the steps ---------------------------------------------------------------------------------------------

    def test_a_dragged_boundary_changes_the_cubes_and_so_the_numbers(self) -> None:
        plan = self.m.experiment_plan
        plan.move_edge(0, "end", T0 + 4 * CUBE_MS, plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5]), together=True)  # step 1 = cubes 0-3, step 2 = cubes 4-5
        self.assertTrue(_pump(lambda: self.item.table() is not None and self.item.table().lines[0].n == (4, 2)), "the numbers did not follow the boundary")

    def test_a_note_edit_does_not_recompute(self) -> None:
        drawn = self.panel._last
        self.m.experiment_plan.set_label(0, "Water")
        self.assertFalse(self.panel._redraw_timer.isActive())
        self.assertIs(self.panel._last, drawn)

    # -- group lines, tooltips -----------------------------------------------------------------------------------------------------

    def test_an_average_line_is_described_by_its_own_values_like_any_other(self) -> None:
        self.panel._display.setCurrentIndex(self.panel._display.findData("average_all"))
        self._settle()
        table = self.item.table()
        self.assertEqual(len(table.lines), 1)
        self.assertFalse(table.lines[0].between_rois)
        self.assertEqual(table.lines[0].n, (3, 3))  # cubes of the step
        drawn = self.panel._last[1][0]
        self.assertAlmostEqual(table.lines[0].mean[0], float(np.mean(drawn.y[:3])), places=9)  # the mean of the line as drawn
        cell, rect = self.item._cells_data()[0]
        self.item.hover_at(rect.center())
        self.assertIn("SD of the 3 cubes in the step", self.item.toolTip())

    def test_the_numbers_follow_the_smoothing_in_every_display_mode(self) -> None:
        """The report that started this change: turning smoothing on or off, or changing its window, must change the numbers (the SD; the mean hardly moves)."""
        for mode in ("individual", "average_all"):
            self.panel._display.setCurrentIndex(self.panel._display.findData(mode))
            self.panel._smoothing.setCurrentIndex(self.panel._smoothing.findData("none"))
            self._settle()
            plain = self.item.table().lines[0].sd
            self.panel._smoothing.setCurrentIndex(self.panel._smoothing.findData("moving_average"))
            self.panel._window.setValue(3)
            self._settle()
            smoothed = self.item.table().lines[0].sd
            self.assertTrue(all(s < p for s, p in zip(smoothed, plain)), (mode, plain, smoothed))  # smoother: smaller SD
            self.panel._within_steps.click()  # across the steps instead of inside them: other values at the step edges
            self._settle()
            self.assertNotEqual(self.item.table().lines[0].sd, smoothed, mode)
            self.panel._within_steps.click()
            self.panel._smoothing.setCurrentIndex(self.panel._smoothing.findData("none"))
            self._settle()
            self.assertEqual(self.item.table().lines[0].sd, plain, mode)  # back to where it was

    def test_hovering_a_number_gives_its_full_values(self) -> None:
        cell, rect = self.item._cells_data()[0]
        self.item.hover_at(rect.center())
        text = self.item.toolTip()
        self.assertIn("ROI 1", text)
        self.assertIn("SD of the 3 cubes in the step", text)
        self.item.hover_at(QPointF(rect.center().x(), rect.center().y() + 1e6))  # away from every cell
        self.assertEqual(self.item.toolTip(), "")

    # -- when there is nothing to write ----------------------------------------------------------------------------------------------

    def test_too_many_lines_give_no_table_and_say_why(self) -> None:
        values = LineStepValues(np.array([3, 3]), np.array([591.0, 593.0]), np.array([0.1, 0.1]), False)
        series = [Series(f"roi:{i}", f"ROI {i}", "#ffffff", np.arange(6.0), np.arange(6.0), step_values=values) for i in range(7)]
        self.panel._steps.show_stats_table(self.plot.plan_bar().steps(), series, True)
        self.assertIsNone(self.item.table())
        self.assertIn("does not hold 7 lines", self.controls._show.toolTip())  # the plot's legend names the 2 lines it was given

    def test_without_steps_the_group_is_greyed_out_and_nothing_is_drawn(self) -> None:
        dataset = self.m.dataset._dataset
        dataset.acquisition_metadata = dataset.acquisition_metadata.model_copy(update={"plan": None})
        self.m.experiment_plan.steps_changed.emit("dataset")
        self.assertFalse(self.controls.is_available())
        self.assertIsNone(self.item.table())

    # -- remembered ---------------------------------------------------------------------------------------------------------------------

    def test_the_choices_are_remembered(self) -> None:
        store = UiStateStore({"sensorgram/stats_position": "bottom", "sensorgram/stats_visible": False, "sensorgram/stats_text_pt": 11})
        self.controls.restore_ui_state(store)
        settings = self.controls.settings()
        self.assertEqual((settings.position, settings.visible, settings.text_pt), ("bottom", False, 11))
        self.controls._position.setCurrentIndex(self.controls._position.findData("top"))
        self.assertEqual(store.get("sensorgram/stats_position"), "top")


if __name__ == "__main__":
    unittest.main()
