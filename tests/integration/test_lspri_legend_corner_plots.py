"""The legend's corner in the three plots (2026-11-22): Sensorgram, LbL series and S_bulk.

Pinned, driven through the cells' `.click()` and methods (never a screen coordinate):
- the Legend menu of every plot has the same Corner row of four cells, the values are separate per plot, remembered, damaged values fall back, and the Legend reset puts the corner back;
- the LbL and S_bulk legends sit in the picked corner of the plot area, entries added after the corner was set included;
- a legend the user dragged stays where it was dropped through a redraw (the corner is applied only when it changes);
- the Sensorgram: with the step table off the legend is in the picked corner; with the table on it stays at the table's edge and the menu says why; with the table off again the corner is back.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

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
    from lspr_imaging_app.panels.sensorgram.legend_corner import (
        BOTTOM_LEFT,
        BOTTOM_RIGHT,
        CORNER_STEP_TABLE_NOTE,
        CORNERS,
        TOP_LEFT,
        TOP_RIGHT,
    )
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.sensorgram.view_settings import SensorgramViewSettings
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.bar_model import PlanBarStep
    from lspr_imaging_app.plan.stats_table import StatsLine, StatsTableSettings, build_stats_table
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import math

REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
RI = (1.33300, 1.34531, 1.35762, 1.33500)
NAMES = ("H2O", "10perEG", "20perEG", "Protein")
TOLERANCE_PX = 1.5  # the legend is anchored to the pixel; allow for the rounding of a fractional size


def _lines() -> list[BulkLine]:
    numbers = RowNumbers(refs=REFS, n=np.full(4, 25), mean=np.array([100.0, 110.0, 125.0, 131.0]), sd=np.full(4, 0.5))
    return [BulkLine(f"Group {name}", color, numbers, short_label=f"Group {name}") for name, color in (("A", "#38bdf8"), ("B", "#f59e0b"))]


def _lbl() -> LblPlotWindow:
    window = LblPlotWindow()
    window.set_steps([LblStep(REFS[i], i + 1, NAMES[i]) for i in range(4)])
    window.set_lines(_lines(), y_label="Δλ (nm)")
    window.resize(1000, 700)
    window.show()
    return window


def _bulk() -> BulkPlotWindow:
    window = BulkPlotWindow()
    window.set_steps([PlotStep(REFS[i], i + 1, NAMES[i], RI[i], math.nan, i < 3) for i in range(4)], 589.3)
    window.set_lines(_lines(), y_label="Peak position (nm)")
    window.resize(1000, 700)  # the S_bulk legend also names the fit lines: in a small window it is wider than the plot area
    window.show()
    return window


def _gaps(legend, view_box) -> tuple[float, float, float, float]:
    """(left, right, top, bottom): the distance in pixels from each edge of the plot area to the legend."""
    box, area = legend.mapRectToItem(view_box, legend.boundingRect()), view_box.boundingRect()
    return box.left() - area.left(), area.right() - box.right(), box.top() - area.top(), area.bottom() - box.bottom()


def _in_corner(test: unittest.TestCase, legend, view_box, corner: str) -> None:
    left, right, top, bottom = _gaps(legend, view_box)
    near_left, near_top = corner in (TOP_LEFT, BOTTOM_LEFT), corner in (TOP_LEFT, TOP_RIGHT)
    test.assertLess(left if near_left else right, 20.0, (corner, left, right, top, bottom))
    test.assertGreater(right if near_left else left, 100.0, (corner, left, right, top, bottom))
    test.assertLess(top if near_top else bottom, 20.0, (corner, left, right, top, bottom))
    test.assertGreater(bottom if near_top else top, 50.0, (corner, left, right, top, bottom))


class _Case(unittest.TestCase):
    def _own(self, widget):
        self.addCleanup(lambda: (widget.close(), widget.deleteLater(), QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)))
        return widget


class MenuTest(_Case):
    def test_every_plot_has_the_corner_row_with_four_cells_and_top_left_is_picked(self) -> None:
        for window in (self._own(_lbl()), self._own(_bulk())):
            look = window.look
            self.assertIn("Corner", [label.text() for label in look.legend_menu._captions.values()])
            self.assertEqual(tuple(look.corner_picker.buttons), CORNERS)
            self.assertEqual(look.settings().legend_corner, TOP_LEFT)
            self.assertTrue(look.corner_picker.buttons[TOP_LEFT].isChecked())

    def test_a_click_picks_exactly_one_corner_and_the_values_are_separate_per_plot(self) -> None:
        lbl, bulk = self._own(_lbl()), self._own(_bulk())
        lbl.look.corner_picker.buttons[BOTTOM_RIGHT].click()
        self.assertEqual(lbl.look.settings().legend_corner, BOTTOM_RIGHT)
        self.assertEqual([c for c, b in lbl.look.corner_picker.buttons.items() if b.isChecked()], [BOTTOM_RIGHT])
        self.assertEqual(bulk.look.settings().legend_corner, TOP_LEFT)  # never heard of it

    def test_a_pick_is_reported_once_and_the_legend_reset_puts_the_corner_back(self) -> None:
        window = self._own(_lbl())
        reports: list[int] = []
        window.look.settings_changed.connect(lambda: reports.append(1))
        window.look.corner_picker.buttons[TOP_RIGHT].click()
        self.assertEqual(len(reports), 1)
        window.look.reset_legend()
        self.assertEqual(window.look.settings().legend_corner, TOP_LEFT)

    def test_the_corner_is_remembered_and_a_damaged_value_falls_back(self) -> None:
        store = UiStateStore({})
        window = self._own(LblPlotWindow())
        window.restore_ui_state(store)
        window.look.corner_picker.buttons[BOTTOM_LEFT].click()
        again = self._own(LblPlotWindow())
        again.restore_ui_state(UiStateStore(dict(store._values)))
        self.assertEqual(again.look.settings().legend_corner, BOTTOM_LEFT)
        damaged = self._own(LblPlotWindow())
        damaged.restore_ui_state(UiStateStore({"sensorgram/lbl_plot_look_legend_corner": "middle"}))
        self.assertEqual(damaged.look.settings().legend_corner, TOP_LEFT)


class WindowPlacementTest(_Case):
    def _check(self, window) -> None:
        view_box = window._plot.getPlotItem().vb
        for corner in (BOTTOM_RIGHT, TOP_RIGHT, BOTTOM_LEFT, TOP_LEFT):
            window.look.corner_picker.buttons[corner].click()
            _APP.processEvents()
            _in_corner(self, window._legend, view_box, corner)

    def test_the_lbl_legend_follows_the_picked_corner(self) -> None:
        self._check(self._own(_lbl()))

    def test_the_bulk_legend_follows_the_picked_corner(self) -> None:
        self._check(self._own(_bulk()))

    def test_a_legend_dragged_by_hand_stays_through_a_redraw_until_another_corner_is_picked(self) -> None:
        window = self._own(_lbl())
        view_box = window._plot.getPlotItem().vb
        window._legend.setOffset((60.0, 70.0))  # what a drag does: another place, the corner untouched
        window.redraw()
        _APP.processEvents()
        left, _right, top, _bottom = _gaps(window._legend, view_box)
        self.assertAlmostEqual(left, 60.0, delta=TOLERANCE_PX)
        self.assertAlmostEqual(top, 70.0, delta=TOLERANCE_PX)
        window.look.corner_picker.buttons[BOTTOM_RIGHT].click()
        _APP.processEvents()
        _in_corner(self, window._legend, view_box, BOTTOM_RIGHT)


def _series(count: int = 3) -> list[Series]:
    x = np.arange(6.0)
    lines = []
    for i in range(count):
        values = LineStepValues(np.array([3, 3]), np.array([600.0 + i, 610.0 - 2 * i]), np.array([0.1, 0.1]), False)
        lines.append(Series(f"roi:{i}", f"ROI {i}", f"#0000{i}f", x, 600.0 + i + x, step_values=values))
    return lines


class SensorgramTest(_Case):
    def _plot(self) -> SensorgramPlot:
        plot = SensorgramPlot()
        plot.resize(900, 700)
        plot.show()
        _APP.processEvents()
        return self._own(plot)

    def _show(self, plot: SensorgramPlot, corner: str) -> None:
        plot.set_view(replace(SensorgramViewSettings(), legend_corner=corner))
        plot.show_series(_series())
        _APP.processEvents()

    def _table(self, plot: SensorgramPlot) -> None:
        bars = [PlanBarStep(index=i, x_start=3.0 * i, x_end=3.0 * i + 3.0, text=f"step {i}", color="#ff0000", tooltip="", levels=None) for i in range(2)]
        rows = [StatsLine(f"ROI {i}", "#ffffff", (600.0 + i, 610.0 - 2 * i), (0.1, 0.1), (3, 3), False) for i in range(3)]
        plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings())
        _APP.processEvents()

    def test_with_the_step_table_off_the_legend_is_in_the_picked_corner(self) -> None:
        plot = self._plot()
        view_box = plot._item.getViewBox()
        for corner in (BOTTOM_RIGHT, TOP_RIGHT, BOTTOM_LEFT, TOP_LEFT):
            self._show(plot, corner)
            _in_corner(self, plot._legend, view_box, corner)

    def test_the_step_table_keeps_the_legend_at_its_edge_says_so_and_gives_it_back(self) -> None:
        plot = self._plot()
        view_box = plot._item.getViewBox()
        notes: list[str] = []
        plot.legend_corner_note_changed.connect(notes.append)
        self._show(plot, BOTTOM_RIGHT)
        _in_corner(self, plot._legend, view_box, BOTTOM_RIGHT)
        self.assertEqual(plot.legend_corner_note(), "")
        self._table(plot)
        _in_corner(self, plot._legend, view_box, TOP_LEFT)  # the table's edge (top, left), whatever the corner says
        self.assertEqual(notes, [CORNER_STEP_TABLE_NOTE])
        self.assertEqual(plot.legend_corner_note(), CORNER_STEP_TABLE_NOTE)
        plot.set_stats_table(None, StatsTableSettings())
        _APP.processEvents()
        self.assertEqual(notes, [CORNER_STEP_TABLE_NOTE, ""])
        _in_corner(self, plot._legend, view_box, BOTTOM_RIGHT)  # the picked corner is back

    def test_the_menu_shows_the_note_only_while_the_table_is_on(self) -> None:
        window = self._own(_lbl())
        look = window.look
        self.assertTrue(look.legend_menu._captions[look.corner_note].isHidden())
        look.set_corner_note(CORNER_STEP_TABLE_NOTE)
        self.assertFalse(look.legend_menu._captions[look.corner_note].isHidden())
        self.assertEqual(look.corner_note.text(), CORNER_STEP_TABLE_NOTE)
        look.set_corner_note("")
        self.assertTrue(look.legend_menu._captions[look.corner_note].isHidden())


if __name__ == "__main__":
    unittest.main()
