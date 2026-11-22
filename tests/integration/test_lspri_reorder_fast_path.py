"""Re-ordering without drawing again (2026-11-22): the Sensorgram's `restack` and the LbL / S_bulk windows' re-order path.

A change of the legend's order, the Draw order, the legend's corner or the cursor only moves legend rows and sets a z per line. The plots do that without drawing the lines again
(docs/reorder_redraw_cost_2026-11-22.md). Pinned here:
- the result of the fast path is **the same as a full redraw** (the lines' z, the order of the layered export's parts, the legend rows and their order, the legend's place);
- it really is the fast path (no line gets new data), and it is used only when it is valid: any other option, other lines, or nothing drawn yet draws everything again;
- the legend filled with one layout pass has the same rows and size as filled row by row.
"""

from __future__ import annotations

import math
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtWidgets
from PyQt6.QtCore import QCoreApplication, QEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.analysis.settings import MetricSettings, StatisticsSettings
    from lspr_imaging_app.analysis.step_stats import LineStepValues
    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
    from lspr_imaging_app.panels.sensorgram.draw_order import DRAW_AUTO, DRAW_LEGEND, DRAW_MANUAL
    from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep
    from lspr_imaging_app.panels.sensorgram.legend_corner import BOTTOM_RIGHT, TOP_RIGHT
    from lspr_imaging_app.panels.sensorgram.legend_order import SORT_GROUP, SORT_NAME, SORT_VALUE, VALUE_MEAN, VALUE_STEP
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot, _Legend
    from lspr_imaging_app.panels.sensorgram.view_settings import SensorgramViewSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _own(test: unittest.TestCase, widget):
    test.addCleanup(lambda: (widget.close(), widget.deleteLater(), QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)))
    return widget


# -- the Sensorgram ----------------------------------------------------------------------------------------------------------------------

def _series(count: int) -> list[Series]:
    """Lines whose order differs by name, by mean and by roughness; every other one has a band (more items to stack)."""
    x = np.arange(40.0)
    rng = np.random.default_rng(3)
    lines = []
    for i in range(count):
        y = 600.0 + (i * 7 % 11) * 0.3 + np.cumsum(rng.normal(0.0, 0.02 + 0.01 * (i % 5), x.size))
        values = LineStepValues(np.array([3, 3]), np.array([600.0 + (i * 5 % 7), 610.0 - 2 * (i % 4)]), np.array([0.1, 0.1]), False)
        band = (y - 0.2, y + 0.2) if i % 2 else (None, None)
        lines.append(Series(f"roi:{i}", f"ROI {i}", f"#{(i * 37) % 256:02x}{(i * 91) % 256:02x}{(i * 53) % 256:02x}", x, y, band_low=band[0], band_high=band[1], step_values=values))
    return lines


def _signature(plot: SensorgramPlot) -> dict:
    """Everything a re-order may change, and what must stay as a full redraw leaves it."""
    legend = plot._legend
    return {
        "z": {i: [round(item.zValue(), 9) for item, _base in items] for i, items in plot._line_items.items()},
        "parts": [(part.layer, part.name) for part in plot._svg_parts],
        "rows": [] if legend is None else [label.text for _sample, label in legend.items],
        "rank": list(plot._legend_rank),
        "offset": None if legend is None else tuple(legend.opts["offset"]),
    }


class SensorgramRestackTest(unittest.TestCase):
    def _plot(self) -> SensorgramPlot:
        plot = SensorgramPlot()
        plot.resize(900, 700)
        plot.show()
        _APP.processEvents()
        return _own(self, plot)

    def _check(self, count: int, base: SensorgramViewSettings, new: SensorgramViewSettings, manual: tuple[str, ...] = ()) -> None:
        series = _series(count)
        plot = self._plot()
        plot.set_view(base)
        plot.set_manual_order(manual)
        plot.show_series(series)
        plot.set_view(new)
        self.assertTrue(plot.can_restack(series))
        with mock.patch.object(pg.PlotCurveItem, "setData", autospec=True) as set_data:
            plot.restack(series)
        self.assertEqual(set_data.call_count, 0)  # no line was given data again
        fast = _signature(plot)
        plot.show_series(series)  # the full redraw of the same view
        self.assertEqual(fast, _signature(plot))

    def test_every_kind_of_reorder_gives_what_a_full_redraw_gives(self) -> None:
        base = SensorgramViewSettings()
        cases = {
            "direction": replace(base, legend_descending=True),
            "name": replace(base, legend_sort=SORT_NAME),
            "name descending": replace(base, legend_sort=SORT_NAME, legend_descending=True),
            "mean": replace(base, legend_sort=SORT_VALUE, legend_value=VALUE_MEAN),
            "mean descending": replace(base, legend_sort=SORT_VALUE, legend_value=VALUE_MEAN, legend_descending=True),
            "draw auto": replace(base, draw_mode=DRAW_AUTO),
            "draw manual": replace(base, draw_mode=DRAW_MANUAL),
            "corner": replace(base, legend_corner=BOTTOM_RIGHT),
            "cursor": replace(base, show_cursor=False),
        }
        for count in (12, 30):  # with and without a legend (more than 24 lines are not named)
            for name, new in cases.items():
                with self.subTest(lines=count, change=name):
                    self._check(count, base, new, manual=tuple(f"roi:{i}" for i in (3, 1, 2, 0)) if new.draw_mode == DRAW_MANUAL else ())

    def test_the_stacking_really_changes(self) -> None:
        series, plot = _series(12), self._plot()
        plot.show_series(series)
        before = _signature(plot)
        plot.set_view(SensorgramViewSettings(legend_descending=True))
        plot.restack(series)
        after = _signature(plot)
        self.assertNotEqual(before["z"], after["z"])
        self.assertEqual(before["rows"][::-1], after["rows"])
        self.assertEqual(sorted(before["parts"]), sorted(after["parts"]))  # the same layers, in another order

    def test_it_is_valid_only_for_the_same_lines_and_a_view_that_differs_only_in_the_order(self) -> None:
        series, plot = _series(12), self._plot()
        self.assertFalse(plot.can_restack(series))  # nothing drawn yet
        plot.show_series(series)
        self.assertTrue(plot.can_restack(series))
        self.assertFalse(plot.can_restack(_series(12)))  # other lines (even equal ones)
        for other in (replace(plot.view(), point_size=7), replace(plot.view(), line_width=2.0), replace(plot.view(), legend_text_pt=12), replace(plot.view(), legend_counts=False)):
            plot.set_view(other)
            self.assertFalse(plot.can_restack(series), other)
        plot.set_view(SensorgramViewSettings())
        self.assertTrue(plot.can_restack(series))
        plot.clear()
        self.assertFalse(plot.can_restack(series))

    def test_the_legend_corner_alone_is_applied_by_a_restack(self) -> None:
        series, plot = _series(6), self._plot()
        plot.show_series(series)
        plot.set_view(SensorgramViewSettings(legend_corner=TOP_RIGHT))
        plot.restack(series)
        self.assertLess(plot._legend.opts["offset"][0], 0)  # measured from the right edge


class LegendFillTest(unittest.TestCase):
    def test_one_layout_pass_gives_the_same_rows_and_size_as_row_by_row(self) -> None:
        def build(batched: bool) -> tuple[list[str], tuple[float, float], list[float]]:
            plot = pg.PlotWidget()
            plot.resize(500, 300)
            plot.show()
            legend = _Legend(offset=(8, 8))
            legend.setParentItem(plot.getPlotItem().vb)
            entries = [(pg.PlotCurveItem(pen=pg.mkPen("#ff0000")), f"Group {i} (n={i + 3})") for i in range(9)]
            if batched:
                legend.add_entries(entries)
            else:
                for sample, text in entries:
                    legend.addItem(sample, text)
            _APP.processEvents()
            result = [label.text for _s, label in legend.items], (legend.width(), legend.height()), [label.pos().y() for _s, label in legend.items]
            plot.close()
            return result

        rows_a, size_a, ys_a = build(False)
        rows_b, size_b, ys_b = build(True)
        self.assertEqual(rows_a, rows_b)
        self.assertEqual(size_a, size_b)
        self.assertEqual(ys_a, ys_b)


# -- the Sensorgram panel ----------------------------------------------------------------------------------------------------------------

class PanelReorderTest(unittest.TestCase):
    def setUp(self) -> None:
        m = app_rewrite._build_modules()
        m.session_autosave._suspended = True
        self.m = m
        self.panel = SensorgramPanel(m.scan_results, m.roi_toolbox, m.dataset, m.selection, m.analysis_settings, m.cube_display, trace_in_process=False, mask=m.mask, experiment_plan=m.experiment_plan)
        self.panel.resize(1100, 700)
        self.panel.show()
        self.series = _series(12)
        count = len(self.series)
        request = SimpleNamespace(statistics=StatisticsSettings(), metric=MetricSettings(), roi_ids=list(range(count)), formula_key="x")
        self.job = SimpleNamespace(stored=None, request=request)
        self.panel._mask_toggle.setChecked(False)
        self.panel._stale_toggle.setChecked(False)
        self._draw()

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()

    def _draw(self) -> None:
        self.panel._last = (self.job, self.series)  # (an event the panel does not get in this test would clear it)
        self.panel._draw_last()

    def _rows(self) -> list[str]:
        return [label.text for _sample, label in self.panel._plot._legend.items]

    def test_an_order_change_restacks_and_any_other_change_draws_again(self) -> None:
        look = self.panel._view_controls.look
        with mock.patch.object(self.panel._plot, "show_series", wraps=self.panel._plot.show_series) as show:
            before = self._rows()
            self.panel._last = (self.job, self.series)
            look.sort_descending.setChecked(True)  # the legend direction: only the rows and the stacking move
            self.assertEqual(show.call_count, 0)
            self.assertEqual(self._rows(), before[::-1])
            self.panel._last = (self.job, self.series)
            look.corner_picker.buttons[BOTTOM_RIGHT].click()
            self.assertEqual(show.call_count, 0)
            self.panel._last = (self.job, self.series)
            look.point_size.setValue(look.point_size.value() + 1)  # an item changes: everything is drawn again
            self.assertEqual(show.call_count, 1)

    def test_a_hidden_panel_waits_for_its_catch_up_instead_of_restacking(self) -> None:
        look = self.panel._view_controls.look
        self.panel._gate.set_visible(False)
        self.panel._last = (self.job, self.series)
        with mock.patch.object(self.panel._plot, "restack") as restack, mock.patch.object(self.panel._plot, "show_series") as show:
            look.sort_descending.setChecked(True)
        self.assertEqual((restack.call_count, show.call_count), (0, 0))
        self.assertTrue(self.panel._gate.stale)
        self.panel._gate.set_visible(True)

    def test_a_change_before_anything_is_drawn_is_remembered_not_lost(self) -> None:
        self.panel._last = None
        with mock.patch.object(self.panel._plot, "restack") as restack:
            self.panel._view_controls.look.sort_descending.setChecked(True)
        self.assertEqual(restack.call_count, 0)


# -- the LbL and S_bulk windows ----------------------------------------------------------------------------------------------------------

REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(6))
RI = (1.33300, 1.34531, 1.35762, 1.33500, 1.34000, 1.34600)
NAMES = ("H2O", "10perEG", "20perEG", "Protein", "Buffer", "Rinse")


def _lines() -> list[BulkLine]:
    rng = np.random.default_rng(5)
    out = []
    for g in range(6):
        numbers = RowNumbers(refs=REFS, n=np.full(6, 25), mean=600.0 + np.cumsum(rng.normal(1.0, 1.5, 6)), sd=np.full(6, 0.5))
        out.append(BulkLine(f"Group {g}", f"#{(g * 40) % 256:02x}{(g * 90) % 256:02x}{(g * 60) % 256:02x}", numbers, short_label=f"Group {g}"))
    return out


def _lbl() -> LblPlotWindow:
    window = LblPlotWindow()
    window.set_steps([LblStep(REFS[i], i + 1, NAMES[i]) for i in range(6)])
    window.set_lines(_lines(), y_label="Δλ (nm)")
    window.resize(1000, 700)
    window.show()
    return window


def _bulk() -> BulkPlotWindow:
    window = BulkPlotWindow()
    window.set_steps([PlotStep(REFS[i], i + 1, NAMES[i], RI[i], math.nan, i < 5) for i in range(6)], 589.3)
    window.set_lines(_lines(), y_label="Peak position (nm)")
    window.resize(1000, 700)
    window.show()
    return window


def _window_signature(window) -> dict:
    state = window._state
    return {
        "z": {index: [round(item.zValue(), 9) for item in items] for index, items in sorted(state.groups.items())},
        "rows": [label.text for _sample, label in window._legend.items],
        "table": [(e.index, e.name, e.at_cursor) for e in window._last_entries],
        "offset": tuple(window._legend.opts["offset"]),
        "cursor": window._cursor.x(),
    }


def _same_as_a_full_redraw(test: unittest.TestCase, window) -> None:
    fast = _window_signature(window)
    window.redraw()
    full = _window_signature(window)
    for key in fast:
        test.assertEqual(fast[key], full[key], key)


class WindowReorderTest(unittest.TestCase):
    def _each(self):
        for name, make in (("LbL", _lbl), ("S_bulk", _bulk)):
            yield name, _own(self, make())

    def test_a_reorder_does_not_draw_again_and_gives_what_a_full_redraw_gives(self) -> None:
        for name, window in self._each():
            look = window.look
            changes = {
                "direction": lambda: look.sort_descending.setChecked(True),
                "name": lambda: look._sort_buttons[SORT_NAME].click(),
                "draw auto": lambda: look._draw_buttons[DRAW_AUTO].click(),
                "corner": lambda: look.corner_picker.buttons[BOTTOM_RIGHT].click(),
                "mean": lambda: look._sort_buttons[SORT_VALUE].click(),
                "back to group": lambda: (look._sort_buttons[SORT_GROUP].click(), look._draw_buttons[DRAW_LEGEND].click()),
            }
            for change, act in changes.items():
                with self.subTest(window=name, change=change), mock.patch.object(window, "redraw", wraps=window.redraw) as redraw:
                    act()
                    self.assertEqual(redraw.call_count, 0)
                    _same_as_a_full_redraw(self, window)

    def test_a_moved_cursor_reorders_the_rows_by_the_value_at_its_place(self) -> None:
        for name, window in self._each():
            look = window.look
            look._sort_buttons[SORT_VALUE].click()
            look._value_buttons[VALUE_STEP].click()
            for place in ((0.0, 3.0, 1.0) if name == "LbL" else (RI[0], RI[2], RI[1])):
                with self.subTest(window=name, place=place), mock.patch.object(window, "redraw", wraps=window.redraw) as redraw:
                    window._cursor._dragged(place)
                    self.assertEqual(redraw.call_count, 0)
                    self.assertEqual(window._cursor.x(), place)
                    _same_as_a_full_redraw(self, window)

    def test_any_other_change_new_lines_or_nothing_drawn_draws_everything(self) -> None:
        for name, window in self._each():
            with self.subTest(window=name):
                with mock.patch.object(window, "redraw", wraps=window.redraw) as redraw:
                    window.look.point_size.setValue(window.look.point_size.value() + 2)
                    self.assertEqual(redraw.call_count, 1)
                window.set_lines(_lines(), y_label="Peak position (nm)")  # new lines: drawn (the window is shown), so a record of them exists again
                self.assertIsNotNone(window._state)
                with mock.patch.object(window, "redraw", wraps=window.redraw) as redraw:
                    window._state = None  # nothing known about what is drawn
                    window.look.sort_descending.setChecked(not window.look.sort_descending.isChecked())
                    self.assertEqual(redraw.call_count, 1)  # nothing known about what is drawn: draw it
                self.assertIsNotNone(window._state)

    def test_new_lines_drop_the_record_of_what_was_drawn(self) -> None:
        for name, window in self._each():
            with self.subTest(window=name):
                self.assertIsNotNone(window._state)
                window.hide()
                window.set_lines(_lines(), y_label="x")  # hidden: not drawn, so what was drawn is not what the lines are
                self.assertIsNone(window._state)
                window.show()


if __name__ == "__main__":
    unittest.main()
