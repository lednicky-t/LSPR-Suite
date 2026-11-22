"""The order of the legend rows in the three plots (2026-11-22): Sensorgram, LbL series and S_bulk.

Pinned, driven through methods and signals (never a screen coordinate):
- the Legend menu has the same Order rows in every plot, the "Value of" row only while Value is picked, its values are separate per plot, remembered, and reset by the Legend reset;
- the LbL and S_bulk plots order by Name, by the mean, and by the value at the cursor's step; the cursor jumps from step to step, is drawn only while that order is on and the Cursor toggle
  is on, and a move of it re-orders the rows;
- the Sensorgram orders by the value at the plan bar's one selected step (no step: the default order and a note), and **the step table's rows stay level with the legend row of their own
  line** when the rows are in another order;
- the lines themselves (colours, numbers) are not touched: only the rows move.
"""

from __future__ import annotations

import math
import sys
import unittest

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QCoreApplication, QEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.analysis.step_stats import LineStepValues
    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
    from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep
    from lspr_imaging_app.panels.sensorgram.legend_order import SORT_GROUP, SORT_NAME, SORT_VALUE, VALUE_MEAN, VALUE_STEP
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.sensorgram.view_settings import SensorgramViewSettings
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.bar_model import PlanBarStep
    from lspr_imaging_app.plan.stats_table import StatsLine, StatsTableSettings, build_stats_table
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
RI = (1.33300, 1.34531, 1.35762, 1.33500)
NAMES = ("H2O", "10perEG", "20perEG", "Protein")
# Two lines whose order changes with the step: accumulated shift from step 0 is A: 0, 10, 25, 31 and B: 0, 40, 25, 20.
MEAN_A = [100.0, 110.0, 125.0, 131.0]
MEAN_B = [100.0, 140.0, 125.0, 120.0]


def _numbers(mean: list[float]) -> RowNumbers:
    return RowNumbers(refs=REFS, n=np.full(4, 25), mean=np.array(mean), sd=np.full(4, 0.5))


def _texts(legend) -> list[str]:
    return [label.text for _sample, label in legend.items]


def _lbl(lines: list[BulkLine]) -> LblPlotWindow:
    window = LblPlotWindow()
    window.set_steps([LblStep(REFS[i], i + 1, NAMES[i]) for i in range(4)])
    window.set_lines(lines, y_label="Δλ (nm)")
    window.show()
    return window


def _bulk(lines: list[BulkLine]) -> BulkPlotWindow:
    window = BulkPlotWindow()
    window.set_steps([PlotStep(REFS[i], i + 1, NAMES[i], RI[i], math.nan, i < 3) for i in range(4)], 589.3)
    window.set_lines(lines, y_label="Peak position (nm)")
    window.show()
    return window


def _two_lines(first: str = "A", second: str = "B") -> list[BulkLine]:
    means = {"A": MEAN_A, "B": MEAN_B}
    return [BulkLine(f"Group {name}", "#38bdf8" if name == "A" else "#f59e0b", _numbers(means[name]), short_label=f"Group {name}") for name in (first, second)]


def _pick(look, sort: str, value: str | None = None, descending: bool = False) -> None:
    look._sort_buttons[sort].click()
    if value is not None:
        look._value_buttons[value].click()
    look.sort_descending.setChecked(descending)


class _WindowCase(unittest.TestCase):
    def _own(self, window):
        self.addCleanup(lambda: (window.close(), window.deleteLater(), QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)))
        return window


class LblOrderTest(_WindowCase):
    def test_group_order_is_the_default_and_the_other_orders_move_only_the_rows(self) -> None:
        window = self._own(_lbl(_two_lines("B", "A")))
        self.assertEqual(_texts(window._legend), ["Group B", "Group A"])  # the lines' own order
        _pick(window.look, SORT_NAME)
        self.assertEqual(_texts(window._legend), ["Group A", "Group B"])
        _pick(window.look, SORT_NAME, descending=True)
        self.assertEqual(_texts(window._legend), ["Group B", "Group A"])
        _pick(window.look, SORT_GROUP, descending=True)
        self.assertEqual(_texts(window._legend), ["Group A", "Group B"])  # the reverse of the lines' order
        self.assertEqual([d[0].label for d in window._drawn], ["Group B", "Group A"])  # the lines are as they were

    def test_value_mean_orders_by_the_mean_of_the_line_as_drawn(self) -> None:
        window = self._own(_lbl(_two_lines("B", "A")))
        _pick(window.look, SORT_VALUE, VALUE_MEAN)  # accumulated means: A 16.5, B 21.25
        self.assertEqual(_texts(window._legend), ["Group A", "Group B"])
        _pick(window.look, SORT_VALUE, VALUE_MEAN, descending=True)
        self.assertEqual(_texts(window._legend), ["Group B", "Group A"])

    def test_value_at_step_follows_the_cursor_which_jumps_from_cycle_to_cycle(self) -> None:
        window = self._own(_lbl(_two_lines("A", "B")))
        _pick(window.look, SORT_VALUE, VALUE_STEP)
        self.assertEqual(window._cursor.x(), 3.0)  # the last cycle to begin with: A 31, B 20
        self.assertEqual(_texts(window._legend), ["Group B", "Group A"])
        window._cursor._dragged(1.2)  # between cycles: it takes the nearest, cycle 1: A 10, B 40
        self.assertEqual(window._cursor.x(), 1.0)
        self.assertEqual(_texts(window._legend), ["Group A", "Group B"])
        window.look.sort_descending.setChecked(True)
        self.assertEqual(_texts(window._legend), ["Group B", "Group A"])

    def test_the_cursor_is_drawn_only_while_the_value_at_step_order_and_the_toggle_are_on(self) -> None:
        window = self._own(_lbl(_two_lines()))
        shown = lambda: window._cursor._cursor.is_shown()  # noqa: E731
        self.assertFalse(shown())  # Group order: nothing reads a step
        _pick(window.look, SORT_VALUE, VALUE_MEAN)
        self.assertFalse(shown())
        _pick(window.look, SORT_VALUE, VALUE_STEP)
        self.assertTrue(shown())
        window.look.show_cursor.setChecked(False)
        self.assertFalse(shown())
        self.assertEqual(window._cursor.x(), 3.0)  # hidden, not forgotten: the order still reads that step
        self.assertEqual(_texts(window._legend), ["Group B", "Group A"])
        window.look.show_cursor.setChecked(True)
        self.assertTrue(shown())

    def test_the_menu_says_which_cycle_the_order_reads(self) -> None:
        window = self._own(_lbl(_two_lines()))
        self.assertEqual(window.look.order_note.text(), "")
        _pick(window.look, SORT_VALUE, VALUE_STEP)
        self.assertIn("cycle 3", window.look.order_note.text())
        window._cursor._dragged(0.0)
        self.assertIn("cycle 0", window.look.order_note.text())

    def test_the_cursor_survives_a_redraw_and_is_remembered(self) -> None:
        store = UiStateStore({})
        window = self._own(_lbl(_two_lines()))
        window.restore_ui_state(store)
        _pick(window.look, SORT_VALUE, VALUE_STEP)
        window._cursor._dragged(2.0)
        window.redraw()  # `PlotItem.clear()` takes the cursor's line out; it must be put back
        self.assertIn(window._cursor._cursor.line, window._plot.getPlotItem().items)
        again = self._own(LblPlotWindow())
        again.set_steps([LblStep(REFS[i], i + 1, NAMES[i]) for i in range(4)])
        again.restore_ui_state(UiStateStore(dict(store._values)))
        again.set_lines(_two_lines(), y_label="Δλ (nm)")
        again.show()
        self.assertEqual(again._cursor.x(), 2.0)
        self.assertEqual(again.look.settings().legend_sort, SORT_VALUE)
        self.assertEqual(again.look.settings().legend_value, VALUE_STEP)


class BulkOrderTest(_WindowCase):
    def test_value_at_step_reads_the_step_the_cursor_stands_on(self) -> None:
        window = self._own(_bulk(_two_lines("A", "B")))
        _pick(window.look, SORT_VALUE, VALUE_STEP)
        self.assertEqual(window._cursor.x(), max(RI))  # the highest RI (step 3: A 125, B 125) to begin with
        window._cursor._dragged(RI[0])  # H2O: A 100, B 100
        self.assertEqual(window._cursor.x(), RI[0])
        self.assertEqual(len(_texts(window._legend)), 2)
        self.assertIn("RI 1.33300", window.look.order_note.text())

    def test_steps_of_one_solution_share_one_place_and_are_averaged(self) -> None:
        window = self._own(_bulk(_two_lines("A", "B")))
        _pick(window.look, SORT_VALUE, VALUE_STEP)
        window._cursor.set_steps([RI[0], RI[3]])
        self.assertEqual(window._cursor.x(), RI[3])  # it was on 1.35762, which is gone: the nearest of what is left
        self.assertEqual(window._mean_of(window._lines[0], RI[3]), MEAN_A[3])
        self.assertTrue(math.isnan(window._mean_of(window._lines[0], 9.0)))  # no step there: no number

    def test_mean_and_name_orders(self) -> None:
        window = self._own(_bulk(_two_lines("B", "A")))
        self.assertEqual(len(_texts(window._legend)), 2)
        _pick(window.look, SORT_NAME)
        first, second = _texts(window._legend)
        self.assertTrue(first.startswith("Group A") and second.startswith("Group B"))
        _pick(window.look, SORT_NAME, descending=True)
        first, _second = _texts(window._legend)
        self.assertTrue(first.startswith("Group B"))


class MenuTest(_WindowCase):
    def test_every_plot_has_the_same_order_rows_with_values_of_their_own(self) -> None:
        lbl, bulk = self._own(_lbl(_two_lines())), self._own(_bulk(_two_lines()))
        for window in (lbl, bulk):
            captions = [label.text() for label in window.look.legend_menu._captions.values()]
            self.assertTrue({"Order", "Value of", "Cursor"} <= set(captions))
        _pick(lbl.look, SORT_NAME, descending=True)
        self.assertEqual(bulk.look.settings().legend_sort, SORT_GROUP)  # never heard of it
        self.assertEqual(bulk.look.settings().legend_descending, False)

    def test_value_of_is_shown_only_while_value_is_picked_and_reset_puts_everything_back(self) -> None:
        window = self._own(_lbl(_two_lines()))
        menu = window.look.legend_menu
        row_visible = lambda: not menu._captions[window.look._value_holder].isHidden()  # noqa: E731
        self.assertFalse(row_visible())
        _pick(window.look, SORT_VALUE, VALUE_STEP, descending=True)
        self.assertTrue(row_visible())
        window.look.reset_legend()
        settings = window.look.settings()
        self.assertEqual((settings.legend_sort, settings.legend_descending, settings.legend_value, settings.show_cursor), (SORT_GROUP, False, VALUE_MEAN, True))
        self.assertFalse(row_visible())

    def test_a_damaged_remembered_value_falls_back_to_the_default(self) -> None:
        window = self._own(LblPlotWindow())
        window.restore_ui_state(UiStateStore({"sensorgram/lbl_plot_look_legend_sort": "sideways", "sensorgram/lbl_plot_look_legend_value": 7}))
        settings = window.look.settings()
        self.assertEqual((settings.legend_sort, settings.legend_value), (SORT_GROUP, VALUE_MEAN))


# -- the Sensorgram -------------------------------------------------------------------------------------------------------------------


def _series(count: int = 3) -> list[Series]:
    """ROI 0..2: their means over the whole plot rise with the number, their mean in step 1 falls (so the two orders differ)."""
    x = np.arange(6.0)
    lines = []
    for i in range(count):
        values = LineStepValues(np.array([3, 3]), np.array([600.0 + i, 610.0 - 2 * i]), np.array([0.1, 0.1]), False)
        lines.append(Series(f"roi:{i}", f"ROI {i}", f"#0000{i}f", x, 600.0 + i + x, step_values=values))
    return lines


class SensorgramOrderTest(unittest.TestCase):
    def _plot(self) -> SensorgramPlot:
        plot = SensorgramPlot()
        plot.resize(900, 700)
        plot.show()
        _APP.processEvents()
        self.addCleanup(lambda: (plot.close(), plot.deleteLater(), QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)))
        return plot

    def test_group_is_the_default_and_name_and_value_reorder_the_rows(self) -> None:
        plot = self._plot()
        plot.show_series(_series())
        self.assertEqual(_texts(plot._legend), ["ROI 0", "ROI 1", "ROI 2"])
        plot.set_view(SensorgramViewSettings(legend_sort=SORT_GROUP, legend_descending=True))
        plot.show_series(_series())
        self.assertEqual(_texts(plot._legend), ["ROI 2", "ROI 1", "ROI 0"])
        plot.set_view(SensorgramViewSettings(legend_sort=SORT_VALUE, legend_value=VALUE_MEAN, legend_descending=True))
        plot.show_series(_series())
        self.assertEqual(_texts(plot._legend), ["ROI 2", "ROI 1", "ROI 0"])  # the mean rises with the number

    def test_value_at_step_reads_the_selected_step_and_without_one_it_is_the_default_order_with_a_note(self) -> None:
        plot = self._plot()
        plot.set_view(SensorgramViewSettings(legend_sort=SORT_VALUE, legend_value=VALUE_STEP))
        plot.show_series(_series())
        self.assertEqual(_texts(plot._legend), ["ROI 0", "ROI 1", "ROI 2"])
        self.assertIn("Select exactly one step", plot.legend_order_note())
        plot.set_order_step(1)  # step 1: ROI 0 610, ROI 1 608, ROI 2 606 (ascending: ROI 2 first); no redraw needed
        self.assertEqual(_texts(plot._legend), ["ROI 2", "ROI 1", "ROI 0"])
        self.assertIn("step 2", plot.legend_order_note())
        plot.set_order_step(None)
        self.assertEqual(_texts(plot._legend), ["ROI 0", "ROI 1", "ROI 2"])
        plot.set_view(SensorgramViewSettings(legend_sort=SORT_NAME))
        self.assertEqual(plot.legend_order_note(), "")

    def test_the_table_rows_stay_level_with_the_legend_row_of_their_own_line(self) -> None:
        plot = self._plot()
        plot.set_view(SensorgramViewSettings(legend_sort=SORT_GROUP, legend_descending=True))
        plot.show_series(_series())
        bars = [PlanBarStep(index=i, x_start=3.0 * i, x_end=3.0 * i + 3.0, text=f"step {i}", color="#ff0000", tooltip="", levels=None) for i in range(2)]
        rows = [StatsLine(f"ROI {i}", "#ffffff", (600.0 + i, 610.0 - 2 * i), (0.1, 0.1), (3, 3), False) for i in range(3)]
        plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings())
        _APP.processEvents()
        item = plot.stats_item()
        to_device = item._device_transform()
        centers = {label.text: to_device.map(item.mapFromScene(label.sceneBoundingRect().center())).y() for _sample, label in plot._legend.items}
        cells = item.layout(to_device, item.view_rect())
        self.assertEqual({c.line for c in cells}, {0, 1, 2})
        for cell in cells:
            self.assertAlmostEqual(cell.rect.center().y(), centers[f"ROI {cell.line}"], delta=1.0)  # line i's numbers are on line i's row, whatever the row's place

    def test_the_cursor_toggle_hides_the_cube_cursor_and_keeps_its_place(self) -> None:
        plot = self._plot()
        plot.show_series(_series())
        plot.set_current_x(2.0)
        self.assertTrue(plot._axis_cursor.is_shown())
        plot.set_view(SensorgramViewSettings(show_cursor=False))
        self.assertFalse(plot._axis_cursor.is_shown())
        self.assertEqual(plot._axis_cursor.x(), 2.0)
        plot.set_view(SensorgramViewSettings(show_cursor=True))
        self.assertTrue(plot._axis_cursor.is_shown())


if __name__ == "__main__":
    unittest.main()
