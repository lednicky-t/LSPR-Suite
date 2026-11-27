"""The LbL and S_bulk windows get what the Sensorgram's Plot group has on the plot itself (2026-11-28): a right-click "Add guide here", a right-click menu on a guide, and a "Zero at" row.

What is pinned (for each of the two windows):
- **guides**: the plot's right-click menu starts with "Add guide here", which puts a guide, showing its value and with no label, at the y of the click; a right-click within a few pixels of
  a guide asks for the guide's menu, beside it nothing (the view's own menu opens); a full table refuses and the window's status line says so;
- **zero**: the "Zero at" row is there; a typed zero changes only the numbers printed on the x axis (LbL: its one-tick-per-cycle labels count from the zero; S_bulk: the refractive
  index), never the view or the data; the step list is the window's own steps (cycles; steps with a refractive index).
A real `QApplication` in-process (offscreen); no dataset files.
"""

from __future__ import annotations

import math
import sys
import unittest

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.domain.guide_lines import MAX_GUIDES, Guide
    from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
    from lspr_imaging_app.panels.sensorgram.broken_axis import BrokenAxisItem
    from lspr_imaging_app.panels.sensorgram.guide_controls import FULL_NOTE
    from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

RIS = (1.333, 1.3453, 1.3576, 1.335)


def _numbers() -> tuple[tuple, RowNumbers]:
    refs = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
    return refs, RowNumbers(refs=refs, n=np.full(4, 25), mean=np.array([100.0, 110.0, 125.0, 131.0]), sd=np.full(4, 0.5))


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._windows: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for window in self._windows:
            window.close()
            window.deleteLater()

    def _bulk(self) -> BulkPlotWindow:
        refs, numbers = _numbers()
        window = BulkPlotWindow(plot_axes=None)
        window.set_steps([PlotStep(refs[i], i + 1, f"s{i}", RIS[i], math.nan, i < 3) for i in range(4)], 589.3)
        window.set_lines([BulkLine("A", "#4e79a7", numbers, short_label="A")], y_label="Step mean (nm)")
        return self._shown(window)

    def _lbl(self) -> LblPlotWindow:
        refs, numbers = _numbers()
        window = LblPlotWindow(plot_axes=None)
        window.set_steps([LblStep(refs[i], i + 1, f"s{i}") for i in range(4)])
        window.set_lines([BulkLine("A", "#38bdf8", numbers, short_label="A")], y_label="Shift (nm)")
        return self._shown(window)

    def _shown(self, window):
        self._windows.append(window)
        window.show()
        _APP.processEvents()
        return window

    def _both(self):
        return (("S_bulk", self._bulk()), ("LbL", self._lbl()))

    @staticmethod
    def _scene_point(window, y: float) -> QPointF:
        view_box = window._plot.getPlotItem().getViewBox()
        (x_low, x_high), _ = view_box.viewRange()
        return view_box.mapViewToScene(QPointF((x_low + x_high) / 2.0, y))

    @staticmethod
    def _type(box: QtWidgets.QLineEdit, text: str) -> None:
        box.setText(text)
        box.editingFinished.emit()


class GuideGesturesTest(_Case):
    def test_the_view_menu_starts_with_add_guide_here_which_uses_the_click(self) -> None:
        for name, window in self._both():
            view_box = window._plot.getPlotItem().getViewBox()
            action = view_box.menu.actions()[0]
            self.assertEqual(action.text(), "Add guide here", name)
            view_box.last_menu_scene_pos = None
            action.trigger()
            self.assertEqual(window.guides.guides(), (), name)  # no click remembered: nothing to add
            y = view_box.viewRange()[1][0] + 0.5 * (view_box.viewRange()[1][1] - view_box.viewRange()[1][0])
            view_box.last_menu_scene_pos = self._scene_point(window, y)
            action.trigger()
            guides = window.guides.guides()
            self.assertEqual(len(guides), 1, name)
            self.assertAlmostEqual(guides[0].value, y, delta=0.01 * abs(y) + 0.5, msg=name)
            self.assertEqual((guides[0].label, guides[0].show_value), ("", True), name)

    def test_a_right_click_on_a_guide_asks_for_its_menu_and_only_there(self) -> None:
        for name, window in self._both():
            low, high = window._plot.getPlotItem().getViewBox().viewRange()[1]
            y = round((low + high) / 2.0, 3)  # inside the view of either window
            window.guides.add_guide(Guide(y))
            asked: list[tuple[int, QPoint]] = []
            window.guides.show_menu = lambda index, pos: asked.append((index, pos))  # the real one would open a modal menu
            view_box = window._plot.getPlotItem().getViewBox()
            on = self._scene_point(window, y)
            self.assertTrue(view_box.guide_right_click(on, QPointF(300.0, 200.0)), name)
            self.assertEqual(asked, [(0, QPoint(300, 200))], name)
            self.assertFalse(view_box.guide_right_click(QPointF(on.x(), on.y() + 40.0), QPointF(0.0, 0.0)), name)  # beside the guide: the view's own menu
            self.assertEqual(len(asked), 1, name)

    def test_a_full_table_refuses_and_the_status_line_says_so(self) -> None:
        for name, window in self._both():
            window.guides.add_guides([Guide(100.0 + i) for i in range(MAX_GUIDES)])
            view_box = window._plot.getPlotItem().getViewBox()
            view_box.last_menu_scene_pos = self._scene_point(window, 120.0)
            view_box.menu.actions()[0].trigger()
            self.assertEqual(len(window.guides.guides()), MAX_GUIDES, name)
            self.assertEqual(window.status.text(), FULL_NOTE, name)


class ZeroTest(_Case):
    def test_both_windows_have_the_zero_row(self) -> None:
        for name, window in self._both():
            self.assertFalse(window.axes.zero.isHidden(), name)
            self.assertFalse(window.axes.zero_step.isHidden(), name)
            self.assertIsInstance(window._plot.getPlotItem().getAxis("bottom"), BrokenAxisItem, name)

    def test_the_step_lists_are_the_windows_own_steps(self) -> None:
        self.assertEqual([x for _label, x in self._lbl()._zero_step_choices()], [0.0, 1.0, 2.0, 3.0])
        bulk = self._bulk()
        self.assertEqual([x for _label, x in bulk._zero_step_choices()], list(RIS))
        bulk.set_steps([PlotStep(("m", 1, 0, "s"), 1, "no RI", math.nan, math.nan, False)], 589.3)
        self.assertEqual(bulk._zero_step_choices(), [])  # a step without a refractive index has no place on the axis

    def test_a_typed_zero_changes_only_the_printed_numbers(self) -> None:
        for name, window in self._both():
            axis = window._plot.getPlotItem().getAxis("bottom")
            view_before = window._plot.getPlotItem().getViewBox().viewRange()
            self._type(window.axes.zero, "1.3453" if name == "S_bulk" else "1")
            _APP.processEvents()
            self.assertEqual(axis.zero(), 1.3453 if name == "S_bulk" else 1.0, name)
            self.assertEqual(window._plot.getPlotItem().getViewBox().viewRange(), view_before, name)  # the view and the data stay where they are
            if name == "S_bulk":
                self.assertEqual(axis.tickStrings([1.3453, 1.3576], 1.0, 0.0123), ["0", "0.01"], name)

    def test_lbl_cycle_labels_count_from_the_zero_and_come_back(self) -> None:
        window = self._lbl()
        axis = window._plot.getPlotItem().getAxis("bottom")

        def labels() -> list[str]:
            return [text for _k, text in axis._tickLevels[0]]

        self.assertEqual(labels(), ["0", "1", "2", "3"])
        self._type(window.axes.zero, "2")
        self.assertEqual(labels(), ["-2", "-1", "0", "1"])
        self.assertEqual([k for k, _text in axis._tickLevels[0]], [0, 1, 2, 3])  # the ticks stand on the cycles
        self._type(window.axes.zero, "")
        self.assertEqual(labels(), ["0", "1", "2", "3"])

    def test_a_tick_step_with_a_zero_puts_ticks_on_multiples_from_the_zero(self) -> None:
        window = self._lbl()
        self._type(window.axes.zero, "1")
        self._type(window.axes._sides["x"].major, "2")
        axis = window._plot.getPlotItem().getAxis("bottom")
        ticks = axis.tickValues(0.0, 6.0, 300)
        self.assertEqual([round(v, 6) for v in ticks[0][1]], [1.0, 3.0, 5.0])  # 0 is the printed zero: the ticks are 2 apart from it

    def test_reset_clears_the_zero_of_a_window(self) -> None:
        for name, window in self._both():
            self._type(window.axes.zero, "1")
            window.axes.reset()
            self.assertIsNone(window.axes.axes().x.zero, name)
            self.assertEqual(window._plot.getPlotItem().getAxis("bottom").zero(), 0.0, name)


if __name__ == "__main__":
    unittest.main()
