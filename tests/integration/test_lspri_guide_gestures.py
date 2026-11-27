"""Guides made and changed on the plot (2026-11-27): right-click "Add guide here", "Add guide from step", drag, the guide's own menu, the table.

What is pinned:
- **the rules**: "show value" is saved only when set, a dropped guide is rounded to a thousandth of the view, at most `MAX_GUIDES` (8);
- **the lines**: which guide is under a pointer (a few pixels, the nearest), the label with the value that follows the line, the open / closed hand, one report after a drag, none for a
  plain click;
- **the plot**: a right-click on a guide asks for its menu (not in an edit mode, not beside a guide), the view's menu starts with "Add guide here" which puts the guide at the click's y,
  a step's guide is the mean of the plotted lines' means, labelled with the step;
- **the table / controls**: add (with a note when the rows are full), move, remove, rename, colour, show value, all through the session value, one menu per guide;
- **the owners**: the Sensorgram panel and the two windows connect the gestures to the table.
A real `QApplication` in-process (offscreen); no dataset files.
"""

from __future__ import annotations

import math
import sys
import unittest

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.domain.guide_lines import MAX_GUIDES, Guide, rounded_for_span
    from lspr_imaging_app.domain.plot_axes import AxisOptions, PlotAxes
    from lspr_imaging_app.panels.sensorgram.guide_controls import FULL_NOTE, GuideControls
    from lspr_imaging_app.panels.sensorgram.guide_items import GuideLine, label_format
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.plan.stats_table import StatsColumn, StatsLine, StatsTable, StatsTableSettings
    from lspr_imaging_app.selection.plot_axes_module import PlotAxesModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

X = np.arange(100.0)
Y_LABEL = "Peak position (nm)"


def _series() -> Series:
    return Series("roi:1", "ROI 1", "#ffffff", X.copy(), 600.0 + 0.1 * X)


class RulesTest(unittest.TestCase):
    def test_show_value_is_saved_only_when_set_and_read_back(self) -> None:
        self.assertNotIn("show_value", Guide(1.0).to_payload())
        guide = Guide(650.0, "Base", "#d62728", "solid", True)
        self.assertEqual(Guide.from_payload(guide.to_payload()), guide)
        self.assertFalse(Guide.from_payload({"value": 1, "show_value": "yes"}).show_value)  # only an explicit true counts
        self.assertEqual(MAX_GUIDES, 8)

    def test_a_dropped_guide_is_rounded_to_a_thousandth_of_the_view(self) -> None:
        self.assertEqual(rounded_for_span(650.12345678, 10.0), 650.12)
        self.assertEqual(rounded_for_span(650.12345678, 0.5), 650.1235)
        self.assertEqual(rounded_for_span(6501.2345, 5000.0), 6501.0)
        self.assertEqual(rounded_for_span(650.12345678, 0.0), 650.12345678)  # no usable span: as it is
        self.assertTrue(math.isnan(rounded_for_span(float("nan"), 10.0)))

    def test_the_label_is_the_text_the_value_or_both_with_braces_kept(self) -> None:
        self.assertIsNone(label_format(Guide(1.0)))
        self.assertEqual(label_format(Guide(1.0, "Base")), "Base")
        self.assertEqual(label_format(Guide(1.0, "", None, "dash", True)), "{value:.6g}")
        self.assertEqual(label_format(Guide(1.0, "Base", None, "dash", True)), "Base {value:.6g}")
        self.assertEqual(label_format(Guide(1.0, "{x}")), "{{x}}")


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._widgets: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for widget in self._widgets:
            widget.close()
            widget.deleteLater()
        _APP.processEvents()

    def _own(self, widget):
        self._widgets.append(widget)
        return widget

    def _plot(self, guides=()) -> SensorgramPlot:
        plot = self._own(SensorgramPlot())
        plot.resize(900, 600)
        plot.show()
        _APP.processEvents()
        plot.set_axes(PlotAxes(y=AxisOptions(guides=tuple(guides))))
        plot.show_series([_series()])
        _APP.processEvents()
        return plot

    @staticmethod
    def _scene_point(plot: SensorgramPlot, y: float, x: float = 50.0) -> QPointF:
        return plot.plot_item().getViewBox().mapViewToScene(QPointF(x, y))


class LinesTest(_Case):
    def test_which_guide_is_under_the_pointer(self) -> None:
        plot = self._plot([Guide(603.0), Guide(607.0)])
        lines = plot._guides
        at = self._scene_point(plot, 603.0)
        self.assertEqual(lines.index_at(at), 0)
        self.assertEqual(lines.index_at(QPointF(at.x(), at.y() + 4.0)), 0)  # a few pixels away is still on it
        self.assertIsNone(lines.index_at(QPointF(at.x(), at.y() + 12.0)))
        self.assertEqual(lines.index_at(self._scene_point(plot, 607.0)), 1)
        self.assertIsNone(lines.index_at(QPointF(5.0, at.y())))  # left of the plot area (over the axis): not on a guide

    def test_the_cursor_is_an_open_hand_and_a_closed_one_while_it_is_held(self) -> None:
        plot = self._plot([Guide(605.0)])
        line = plot._guides.lines()[0]
        self.assertIsInstance(line, GuideLine)
        self.assertTrue(line.movable)
        self.assertEqual(line.cursor().shape(), Qt.CursorShape.OpenHandCursor)

        class _Event:
            def __init__(self, start: bool, finish: bool) -> None:
                self._start, self._finish = start, finish

            def button(self):
                return Qt.MouseButton.LeftButton

            def isStart(self):
                return self._start

            def isFinish(self):
                return self._finish

            def accept(self):
                pass

            def buttonDownPos(self):
                return QPointF(0.0, 0.0)

            def pos(self):
                return QPointF(0.0, 0.0)

        line.mouseDragEvent(_Event(True, False))
        self.assertEqual(line.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        line.mouseDragEvent(_Event(False, True))
        self.assertEqual(line.cursor().shape(), Qt.CursorShape.OpenHandCursor)

    def test_a_drag_is_reported_once_after_it_ends_rounded_to_the_view(self) -> None:
        plot = self._plot([Guide(605.0, "", None, "dash", True)])
        told: list[tuple[int, float]] = []
        plot.guide_moved.connect(lambda i, v: told.append((i, v)))
        line = plot._guides.lines()[0]
        line.setValue(606.123456789)
        self.assertEqual(told, [])  # nothing while it is held
        line.sigPositionChangeFinished.emit(line)
        _APP.processEvents()
        self.assertEqual(len(told), 1)
        self.assertEqual(told[0][0], 0)
        self.assertAlmostEqual(told[0][1], round(606.123456789, 3 - math.floor(math.log10(plot._guides.view_span()))), places=6)

    def test_the_label_shows_the_value_and_follows_the_line(self) -> None:
        plot = self._plot([Guide(605.0, "Base", None, "dash", True)])
        line = plot._guides.lines()[0]
        self.assertEqual(line.label.textItem.toPlainText(), "Base 605")
        line.setValue(606.5)
        self.assertEqual(line.label.textItem.toPlainText(), "Base 606.5")


class PlotGesturesTest(_Case):
    def test_a_right_click_on_a_guide_asks_for_its_menu_and_only_there(self) -> None:
        plot = self._plot([Guide(603.0), Guide(607.0)])
        told: list[tuple[int, QPoint]] = []
        plot.guide_menu_requested.connect(lambda i, p: told.append((i, p)))
        on = self._scene_point(plot, 607.0)
        self.assertTrue(plot._on_guide_right_click(on, QPointF(300.0, 200.0)))
        self.assertEqual(told, [(1, QPoint(300, 200))])
        self.assertFalse(plot._on_guide_right_click(QPointF(on.x(), on.y() + 30.0), QPointF(0.0, 0.0)))  # beside a guide: the view's own menu
        self.assertEqual(len(told), 1)
        plot.set_anchor_mode(True)
        self.assertFalse(plot._on_guide_right_click(on, QPointF(0.0, 0.0)))  # the anchor edit owns the right button
        plot.set_anchor_mode(False)

    def test_the_view_menu_starts_with_add_guide_here_which_uses_the_click(self) -> None:
        plot = self._plot()
        view_box = plot.plot_item().getViewBox()
        self.assertEqual(view_box.menu.actions()[0].text(), "Add guide here")
        told: list[float] = []
        plot.guide_add_requested.connect(told.append)
        view_box.last_menu_scene_pos = self._scene_point(plot, 606.0)
        view_box.menu.actions()[0].trigger()
        self.assertEqual(len(told), 1)
        self.assertAlmostEqual(told[0], 606.0, delta=0.2)
        view_box.last_menu_scene_pos = None
        view_box.menu.actions()[0].trigger()
        self.assertEqual(len(told), 1)  # no click remembered: nothing to add

    def test_a_steps_guide_is_the_mean_of_the_lines_means_labelled_with_the_step(self) -> None:
        plot = self._plot()
        self.assertEqual(plot.step_guides([0]), [])  # no table yet
        lines = (
            StatsLine("A", "#fff", (600.0, 610.0, float("nan")), (0.1, 0.1, 0.1), (5, 5, 0), False),
            StatsLine("B", "#fff", (602.0, 614.0, float("nan")), (0.1, 0.1, 0.1), (5, 5, 0), False),
        )
        table = StatsTable(columns=(StatsColumn(0, 0.0, 30.0, "Buffer"), StatsColumn(1, 30.0, 60.0, "Step 2"), StatsColumn(2, 60.0, 90.0, "Empty")), lines=lines, decimals=1, unit="nm")
        plot.set_stats_table(table, StatsTableSettings())
        guides = plot.step_guides([0, 1, 2, 7])
        self.assertEqual([(g.label, g.show_value) for g in guides], [("Buffer", True), ("Step 2", True)])  # a step with no number, and one that is not there, give none
        self.assertAlmostEqual(guides[0].value, 601.0, delta=0.01)
        self.assertAlmostEqual(guides[1].value, 612.0, delta=0.01)
        told: list = []
        plot.guides_add_requested.connect(told.append)
        plot.request_guides_from_steps([1])
        self.assertEqual([g.label for g in told[0]], ["Step 2"])
        plot.request_guides_from_steps([2])
        self.assertEqual(len(told), 1)  # nothing to add: nothing asked


class ControlsTest(_Case):
    def _controls(self, module=None) -> GuideControls:
        parent = self._own(QtWidgets.QWidget())
        return GuideControls(parent, module or PlotAxesModule(), "sensorgram", lambda: ("Cube (#)", Y_LABEL), object_prefix="g")

    def test_add_move_remove_and_change_go_through_the_session(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        self.assertTrue(controls.add_guide(Guide(650.0, "", None, "dash", True)))
        self.assertTrue(controls.add_guide(Guide(660.0, "Top")))
        self.assertEqual([g.value for g in controls.guides()], [650.0, 660.0])
        controls.move_guide(0, 655.5)
        self.assertEqual(controls.guides()[0], Guide(655.5, "", None, "dash", True))
        controls.change_guide(1, label="Ceiling", color="#d62728", show_value=True)
        self.assertEqual(controls.guides()[1], Guide(660.0, "Ceiling", "#d62728", "dash", True))
        self.assertEqual(controls._slots[1].label.text(), "Ceiling")  # the table shows it
        self.assertTrue(controls._slots[1].show_value.isChecked())
        controls.remove_guide(0)
        self.assertEqual([g.value for g in controls.guides()], [660.0])
        self.assertEqual(module.state()["sensorgram"]["axes"][0]["guides"][0]["label"], "Ceiling")
        controls.move_guide(5, 1.0)  # no such guide: nothing happens
        controls.remove_guide(5)
        self.assertEqual(len(controls.guides()), 1)

    def test_a_full_table_refuses_a_new_guide_and_says_so_and_takes_what_fits(self) -> None:
        controls = self._controls()
        controls.add_guides([Guide(float(i)) for i in range(MAX_GUIDES - 1)])
        self.assertEqual(controls.note.text(), "")
        self.assertEqual(controls.add_guides([Guide(100.0), Guide(101.0)]), 1)  # one row was free
        self.assertEqual(controls.note.text(), FULL_NOTE)
        self.assertEqual(len(controls.guides()), MAX_GUIDES)
        self.assertFalse(controls.add_guide(Guide(5.5)))

    def test_the_guides_own_menu_has_rename_colour_show_value_and_remove(self) -> None:
        controls = self._controls()
        controls.add_guide(Guide(650.0, "Base"))
        menu = controls.build_menu(0)
        texts = [a.text() for a in menu.actions() if a.text()]
        self.assertEqual(texts, ["Guide 650: Base", "Rename...", "Colour...", "Show y value", "Remove guide"])  # "Default colour" only while it has a colour of its own
        self.assertTrue(all(not a.icon().isNull() for a in menu.actions()[2:] if a.text()))  # every entry but the header has an icon (menu_icons.ICONS)
        show = next(a for a in menu.actions() if a.text() == "Show y value")
        self.assertFalse(show.isChecked())
        show.trigger()
        self.assertTrue(controls.guides()[0].show_value)
        controls.change_guide(0, color="#2ca02c")
        self.assertIn("Default colour", [a.text() for a in controls.build_menu(0).actions()])
        next(a for a in controls.build_menu(0).actions() if a.text() == "Default colour").trigger()
        self.assertIsNone(controls.guides()[0].color)
        next(a for a in controls.build_menu(0).actions() if a.text() == "Remove guide").trigger()
        self.assertEqual(controls.guides(), ())
        self.assertIsNone(controls.build_menu(0))

    def test_the_table_has_a_column_for_showing_the_value(self) -> None:
        controls = self._controls()
        slot = controls._slots[0]
        slot.value.setText("650")
        slot.value.editingFinished.emit()
        slot.show_value.setChecked(True)
        self.assertTrue(controls.guides()[0].show_value)
        slot.show_value.setChecked(False)
        self.assertFalse(controls.guides()[0].show_value)


class OwnersTest(_Case):
    def test_the_windows_keep_a_dragged_guide_in_their_table(self) -> None:
        from lspr_imaging_app.analysis.step_table import RowNumbers
        from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
        from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep

        refs = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
        numbers = RowNumbers(refs=refs, n=np.full(4, 25), mean=np.array([100.0, 110.0, 125.0, 131.0]), sd=np.full(4, 0.5))
        bulk = self._own(BulkPlotWindow(plot_axes=None))
        bulk.set_steps([PlotStep(refs[i], i + 1, f"s{i}", (1.333, 1.3453, 1.3576, 1.335)[i], math.nan, i < 3) for i in range(4)], 589.3)
        bulk.set_lines([BulkLine("A", "#4e79a7", numbers, short_label="A")], y_label=Y_LABEL)
        lbl = self._own(LblPlotWindow(plot_axes=None))
        lbl.set_steps([LblStep(refs[i], i + 1, f"s{i}") for i in range(4)])
        lbl.set_lines([BulkLine("A", "#38bdf8", numbers, short_label="A")], y_label="Shift (nm)")
        for window in (bulk, lbl):
            window.show()
            _APP.processEvents()
            box = window.guides._slots[0].value
            box.setText("120")
            box.editingFinished.emit()
            line = window._guide_lines.lines()[0]
            line.setValue(123.0)
            line.sigPositionChangeFinished.emit(line)
            _APP.processEvents()
            self.assertAlmostEqual(window.guides.guides()[0].value, 123.0, delta=0.5, msg=type(window).__name__)
            self.assertEqual([round(line.value()) for line in window._guide_lines.lines()], [round(window.guides.guides()[0].value)], type(window).__name__)


if __name__ == "__main__":
    unittest.main()
