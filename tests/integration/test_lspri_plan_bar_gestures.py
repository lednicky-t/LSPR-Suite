"""The plan bar's pointer gestures (2026-10-26): select a step, drag one of its edges, double-click to retype its note, right-click for colour and reset.

Driven without a pointer: the item is handed small stand-ins for pyqtgraph's mouse events (`_Event`), the editor and the menu are used through their own methods, and the colour dialog is
replaced. Four layers: `PlanBarItem` (what it accepts and emits), `StepTextEditor`, `PlanBarActions` (gestures -> `ExperimentPlanModule` commands) and the real panel with a real scan.

The rule under test (maintainer, 2026-10-26: dragging two edges that meet was annoying): a click selects a step; only the selected step's edges can be grabbed; only that step changes,
and it stops at its neighbour's edge; Shift drags a shared boundary together with the neighbour.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_core import ImagingAcquisitionMetadata, ImagingPlan, ImagingPlanStep
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.plan_actions import PlanAxisContext, PlanBarActions
    from lspr_imaging_app.panels.sensorgram.plan_text_editor import StepTextEditor
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan import bar_model as bm
    from lspr_imaging_app.plan.axis_map import AxisMap
    from lspr_imaging_app.plan.module import ExperimentPlanModule
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_plan_bar import CENTERS, CUBE_MS, T0, _pump, _series, _step, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()

NO_SHIFT = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier


class _Event:
    """The parts of pyqtgraph's mouse events the item uses."""

    def __init__(self, pos, *, button=Qt.MouseButton.LeftButton, down=None, start=False, finish=False, double=False, shift=False, ctrl=False, screen=(40.0, 50.0), scene=None) -> None:
        self._pos, self._button, self._down = QPointF(*pos), button, QPointF(*(down if down is not None else pos))
        self._start, self._finish, self._double, self._screen = start, finish, double, QPointF(*screen)
        self._modifiers = (SHIFT if shift else NO_SHIFT) | (CTRL if ctrl else NO_SHIFT)
        self._scene = scene
        self.accepted: bool | None = None

    def button(self):
        return self._button

    def pos(self):
        return self._pos

    def buttonDownPos(self):
        return self._down

    def isStart(self):
        return self._start

    def isFinish(self):
        return self._finish

    def double(self):
        return self._double

    def modifiers(self):
        return self._modifiers

    def screenPos(self):
        return self._screen

    def scenePos(self):
        return self._scene

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False

    def isAccepted(self):
        return bool(self.accepted)


# -- the item ---------------------------------------------------------------------------------------------------------------------


class PlanBarGestureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = SensorgramPlot()
        self.plot.resize(640, 320)
        self.plot.show()
        _APP.processEvents()
        self.plot.show_series([_series()])
        self.plot.set_plan([_step(0, 2, 6, text="H2O"), _step(1, 6, 9, text="EG")], bm.PlanBarSettings(position=bm.POSITION_TOP, height_stop=10))
        _APP.processEvents()
        self.bar = self.plot.plan_bar()
        view = self.bar.view_rect()
        self.strip_y = view.bottom() - 0.05 * view.height()  # inside the bar
        self.low_y = view.top() + 0.5 * view.height()  # in the plot, away from the bar
        self.log: list[tuple] = []
        self.bar.edge_drag_started.connect(lambda i, e: self.log.append(("start", i, e)))
        self.bar.edge_dragged.connect(lambda i, e, x, together: self.log.append(("drag", i, e, round(x, 3), together)))
        self.bar.edge_drag_finished.connect(lambda: self.log.append(("finish",)))
        self.bar.text_edit_requested.connect(lambda i: self.log.append(("edit", i)))
        self.bar.menu_requested.connect(lambda i, pos: self.log.append(("menu", i, pos.x(), pos.y())))
        self.bar.selection_changed.connect(lambda i: self.log.append(("selected", i)))

    def tearDown(self) -> None:
        self.plot.close()
        self.plot.deleteLater()

    # -- selecting ------------------------------------------------------------------------------------------------------------

    def test_a_click_selects_the_step_and_a_click_on_another_moves_the_selection(self) -> None:
        self.assertEqual(self.bar.selected_indices(), ())
        click = _Event((4.0, self.strip_y))
        self.bar.mouseClickEvent(click)
        self.assertTrue(click.accepted)  # taken: the cube slider does not move
        self.assertEqual(self.bar.selected_indices(), (0,))
        self.bar.mouseClickEvent(_Event((7.0, self.strip_y)))
        self.assertEqual(self.bar.selected_indices(), (1,))
        self.assertEqual(self.log, [("selected", (0,)), ("selected", (1,))])

    def test_a_right_click_and_a_double_click_select_too(self) -> None:
        self.bar.mouseClickEvent(_Event((4.0, self.strip_y), button=Qt.MouseButton.RightButton))
        self.assertEqual(self.bar.selected_indices(), (0,))
        self.bar.mouseClickEvent(_Event((7.0, self.strip_y), double=True))
        self.assertEqual(self.bar.selected_indices(), (1,))

    def test_a_click_on_the_plot_clears_the_selection_and_still_moves_the_cube(self) -> None:
        self.bar.select(0)
        clicked: list[float] = []
        self.plot.x_clicked.connect(clicked.append)
        scene_pos = self.bar.mapToScene(QPointF(4.0, self.low_y))
        self.plot._on_scene_clicked(_Event((0.0, 0.0), scene=scene_pos))
        self.assertEqual(self.bar.selected_indices(), ())
        self.assertEqual(len(clicked), 1)

    def test_a_click_the_bar_took_does_not_clear_the_selection_or_move_the_cube(self) -> None:
        clicked: list[float] = []
        self.plot.x_clicked.connect(clicked.append)
        click = _Event((4.0, self.strip_y), scene=self.bar.mapToScene(QPointF(4.0, self.strip_y)))
        self.bar.mouseClickEvent(click)  # accepted by the bar; pyqtgraph then reports the same event to the plot
        self.plot._on_scene_clicked(click)
        self.assertEqual(self.bar.selected_indices(), (0,))
        self.assertEqual(clicked, [])

    def test_the_selection_goes_when_its_step_goes(self) -> None:
        self.bar.select(1)
        self.plot.set_plan([_step(0, 2, 6)], bm.PlanBarSettings())
        self.assertEqual(self.bar.selected_indices(), ())
        self.bar.select(5)  # no such step
        self.assertEqual(self.bar.selected_indices(), ())

    def test_the_selected_step_is_marked_with_handles_at_its_edges(self) -> None:
        self.plot.set_plan([_step(0, 2, 6, color="#ff0000"), _step(1, 6, 9, color="#ff0000")], bm.PlanBarSettings(alpha=1.0, height_stop=10))
        self.bar.set_theme("#ffffff", "#000000", "#00ff00")
        self.bar.select(0)
        _APP.processEvents()
        drawn = self.bar.layout(self.bar.deviceTransform(self.plot._widget.viewportTransform()), self.bar.view_rect())
        image = self.plot.grab().toImage()
        y = int(drawn[0].rect.top()) + 3  # above the text, which is in the middle of the bar
        at_selected_edge = image.pixelColor(int(drawn[0].left_edge) + 1, y)
        at_other_step = image.pixelColor(int(drawn[1].rect.center().x()), y)
        self.assertGreater(at_selected_edge.green(), 200)  # the accent handle
        self.assertLess(at_selected_edge.red(), 60)
        self.assertGreater(at_other_step.red(), 200)  # the unselected step keeps its own colour
        self.assertLess(at_other_step.green(), 60)

    # -- selecting several steps ----------------------------------------------------------------------------------------------

    def _four_steps(self) -> None:
        self.plot.set_plan(
            [_step(0, 1, 3, text="A"), _step(1, 3, 5, text="B"), _step(2, 5, 7, text="C"), _step(3, 7, 9, text="D")],
            bm.PlanBarSettings(position=bm.POSITION_TOP, height_stop=10),
        )
        _APP.processEvents()
        self.log.clear()

    def _click(self, x, **keys) -> None:
        self.bar.mouseClickEvent(_Event((x, self.strip_y), **keys))

    def test_ctrl_click_adds_a_step_and_takes_a_selected_one_out(self) -> None:
        self._four_steps()
        self._click(2.0)
        self._click(6.0, ctrl=True)
        self.assertEqual(self.bar.selected_indices(), (0, 2))
        self._click(4.0, ctrl=True)
        self.assertEqual(self.bar.selected_indices(), (0, 1, 2))
        self._click(2.0, ctrl=True)
        self.assertEqual(self.bar.selected_indices(), (1, 2))
        self.assertEqual([entry for entry in self.log if entry[0] == "selected"][-1], ("selected", (1, 2)))

    def test_shift_click_adds_the_range_from_the_last_click(self) -> None:
        self._four_steps()
        self._click(2.0)
        self._click(6.0, shift=True)
        self.assertEqual(self.bar.selected_indices(), (0, 1, 2))
        self._click(8.0, ctrl=True)  # a new anchor: step 4
        self._click(4.0, shift=True)  # ... so the range is steps 2 - 4, added to the rest
        self.assertEqual(self.bar.selected_indices(), (0, 1, 2, 3))

    def test_a_plain_click_on_one_of_several_selected_steps_selects_only_it(self) -> None:
        self._four_steps()
        self.bar.select_many((0, 1, 2), anchor=0)
        self._click(4.0)
        self.assertEqual(self.bar.selected_indices(), (1,))

    def test_a_right_click_keeps_the_selection_when_it_is_on_a_selected_step_and_replaces_it_otherwise(self) -> None:
        self._four_steps()
        self.bar.select_many((0, 1), anchor=0)
        self.bar.mouseClickEvent(_Event((4.0, self.strip_y), button=Qt.MouseButton.RightButton))
        self.assertEqual(self.bar.selected_indices(), (0, 1))
        self.bar.mouseClickEvent(_Event((8.0, self.strip_y), button=Qt.MouseButton.RightButton))
        self.assertEqual(self.bar.selected_indices(), (3,))

    def test_a_double_click_on_one_of_several_selected_steps_keeps_the_selection(self) -> None:
        self._four_steps()
        self.bar.select_many((0, 1), anchor=0)
        self.bar.mouseClickEvent(_Event((4.0, self.strip_y), double=True))
        self.assertEqual(self.bar.selected_indices(), (0, 1))
        self.assertIn(("edit", 1), self.log)

    def test_every_selected_step_is_marked_on_both_edges(self) -> None:
        self.plot.set_plan(
            [_step(0, 2, 4, color="#ff0000"), _step(1, 4, 6, color="#ff0000"), _step(2, 6, 9, color="#ff0000")], bm.PlanBarSettings(alpha=1.0, height_stop=10)
        )
        self.bar.set_theme("#ffffff", "#000000", "#00ff00")
        self.bar.select_many((0, 2), anchor=0)
        _APP.processEvents()
        drawn = self.bar.layout(self.bar.deviceTransform(self.plot._widget.viewportTransform()), self.bar.view_rect())
        image = self.plot.grab().toImage()
        y = int(drawn[0].rect.top()) + 3
        marked = [image.pixelColor(int(x), y) for x in (drawn[0].left_edge, drawn[0].right_edge, drawn[2].left_edge, drawn[2].right_edge)]  # the bar is 2.4 px wide, centred on the edge: that pixel is wholly inside it
        middle = image.pixelColor(int(drawn[1].rect.center().x()), y)
        self.assertTrue(all(c.green() > 200 and c.red() < 60 for c in marked), [c.name() for c in marked])
        self.assertGreater(middle.red(), 200)  # the step between them is not marked
        self.assertLess(middle.green(), 60)

    def test_a_boundary_between_two_selected_steps_is_dragged_with_both(self) -> None:
        self._four_steps()
        self.bar.select_many((1, 2), anchor=1)
        down = (5.0, self.strip_y)  # steps 2 and 3 meet here
        self.bar.mouseDragEvent(_Event((5.1, self.strip_y), down=down, start=True))
        self.bar.mouseDragEvent(_Event((5.6, self.strip_y), down=down))
        self.bar.mouseDragEvent(_Event((5.6, self.strip_y), down=down, finish=True))
        self.assertEqual(
            [entry for entry in self.log if entry[0] != "selected"],
            [("start", 1, "end"), ("drag", 1, "end", 5.1, True), ("drag", 1, "end", 5.6, True), ("drag", 1, "end", 5.6, True), ("finish",)],
        )

    def test_the_outer_edges_of_a_group_and_edges_of_steps_that_are_not_neighbours_move_alone(self) -> None:
        self._four_steps()
        self.bar.select_many((0, 2), anchor=0)
        for x in (3.0, 5.0):  # steps 1 and 3 are selected: each shared boundary has an unselected step on its other side
            self.log.clear()
            self.bar.mouseDragEvent(_Event((x + 0.05, self.strip_y), down=(x, self.strip_y), start=True))
            self.bar.mouseDragEvent(_Event((x + 0.2, self.strip_y), down=(x, self.strip_y), finish=True))
            drags = [entry for entry in self.log if entry[0] == "drag"]
            self.assertTrue(all(entry[-1] is False for entry in drags), (x, drags))

    def test_the_tooltip_tells_how_to_select_more(self) -> None:
        self._four_steps()
        self.bar.hover_at(QPointF(2.0, self.strip_y))
        self.assertIn("Ctrl+click", self.bar.toolTip())
        self._click(2.0)
        self.bar.hover_at(QPointF(2.0, self.strip_y))
        self.assertIn("Ctrl+click", self.bar.toolTip())
        self.assertIn("Shift+click", self.bar.toolTip())

    # -- dragging an edge -----------------------------------------------------------------------------------------------------

    def test_a_drag_needs_a_selected_step(self) -> None:
        ev = _Event((6.0, self.strip_y), down=(6.0, self.strip_y), start=True)
        self.bar.mouseDragEvent(ev)
        self.assertFalse(ev.accepted)
        self.assertEqual(self.log, [])

    def test_where_two_steps_meet_the_selected_one_decides_whose_edge_is_taken(self) -> None:
        down = (6.0, self.strip_y)  # the boundary the two steps share
        self.bar.select(0)
        self.bar.mouseDragEvent(_Event(down, down=down, start=True))
        self.bar.mouseDragEvent(_Event(down, down=down, finish=True))
        self.bar.select(1)
        self.bar.mouseDragEvent(_Event(down, down=down, start=True))
        self.bar.mouseDragEvent(_Event(down, down=down, finish=True))
        started = [entry for entry in self.log if entry[0] == "start"]
        self.assertEqual(started, [("start", 0, "end"), ("start", 1, "start")])

    def test_a_drag_is_followed_to_the_end_and_reports_shift(self) -> None:
        self.bar.select(0)
        down = (6.0, self.strip_y)
        first = _Event((6.1, self.strip_y), down=down, start=True)
        self.bar.mouseDragEvent(first)
        self.assertTrue(first.accepted)
        self.assertTrue(self.bar.is_dragging())
        self.bar.mouseDragEvent(_Event((7.0, self.strip_y), down=down, shift=True))
        last = _Event((7.5, self.low_y), down=down, finish=True)  # the pointer may leave the strip: the edge is still held
        self.bar.mouseDragEvent(last)
        self.assertTrue(last.accepted)
        self.assertFalse(self.bar.is_dragging())
        drags = [entry for entry in self.log if entry[0] != "selected"]
        self.assertEqual(
            drags,
            [("start", 0, "end"), ("drag", 0, "end", 6.1, False), ("drag", 0, "end", 7.0, True), ("drag", 0, "end", 7.5, False), ("finish",)],
        )

    def test_the_outer_edges_of_the_selected_step_can_be_dragged_too(self) -> None:
        self.bar.select(0)
        self.bar.mouseDragEvent(_Event((2.05, self.strip_y), down=(2.0, self.strip_y), start=True))
        self.assertIn(("start", 0, "start"), self.log)

    def test_drags_that_do_not_start_on_an_edge_of_the_selected_step_are_declined(self) -> None:
        self.bar.select(0)
        for down, button in (
            ((4.0, self.strip_y), Qt.MouseButton.LeftButton),  # inside the step
            ((9.0, self.strip_y), Qt.MouseButton.LeftButton),  # the far edge of the other step
            ((6.0, self.low_y), Qt.MouseButton.LeftButton),  # on the edge's x but below the bar
            ((6.0, self.strip_y), Qt.MouseButton.MiddleButton),  # the middle button pans
            ((6.0, self.strip_y), Qt.MouseButton.RightButton),
        ):
            ev = _Event(down, button=button, down=down, start=True)
            self.bar.mouseDragEvent(ev)
            self.assertFalse(ev.accepted, (down, button))
        self.assertFalse(self.bar.is_dragging())
        self.assertEqual([entry for entry in self.log if entry[0] != "selected"], [])

    def test_the_bar_does_nothing_while_hidden(self) -> None:
        self.bar.select(0)
        self.plot.set_plan([_step(0, 2, 6)], bm.PlanBarSettings(visible=False))
        ev = _Event((2.0, self.strip_y), down=(2.0, self.strip_y), start=True)
        self.bar.mouseDragEvent(ev)
        self.assertFalse(ev.accepted)

    def test_the_boundary_between_two_steps_is_part_of_the_hit_area(self) -> None:
        """The two steps' strips overlap at their shared boundary; with the default fill rule that overlap was a hole in the shape."""
        for x in (5.99, 6.0, 6.01):
            self.assertTrue(self.bar.shape().contains(QPointF(x, self.strip_y)), x)

    # -- the other gestures ---------------------------------------------------------------------------------------------------

    def test_a_double_click_asks_for_the_editor(self) -> None:
        double = _Event((7.0, self.strip_y), double=True)
        self.bar.mouseClickEvent(double)
        self.assertTrue(double.accepted)
        self.assertEqual([entry for entry in self.log if entry[0] == "edit"], [("edit", 1)])

    def test_a_right_click_on_a_step_asks_for_the_menu_at_the_screen_position(self) -> None:
        ev = _Event((4.0, self.strip_y), button=Qt.MouseButton.RightButton, screen=(120.0, 80.0))
        self.bar.mouseClickEvent(ev)
        self.assertTrue(ev.accepted)
        self.assertIn(("menu", 0, 120, 80), self.log)

    def test_clicks_elsewhere_are_not_taken(self) -> None:
        for pos, button in (
            ((4.0, self.low_y), Qt.MouseButton.LeftButton), ((1.0, self.strip_y), Qt.MouseButton.LeftButton),
            ((4.0, self.strip_y), Qt.MouseButton.MiddleButton), ((4.0, self.low_y), Qt.MouseButton.RightButton),
        ):
            ev = _Event(pos, button=button, double=True)
            self.bar.mouseClickEvent(ev)
            self.assertFalse(ev.accepted, (pos, button))
        self.assertEqual(self.log, [])
        self.assertEqual(self.bar.selected_indices(), ())

    def test_hovering_says_what_to_do_and_only_the_selected_steps_edges_get_the_resize_cursor(self) -> None:
        self.bar.hover_at(QPointF(4.0, self.strip_y))
        self.assertTrue(self.bar.toolTip().startswith("tip 0"))
        self.assertIn("Click to select", self.bar.toolTip())
        self.bar.hover_at(QPointF(6.02, self.strip_y))
        self.assertNotEqual(self.bar.cursor().shape(), Qt.CursorShape.SizeHorCursor)  # nothing selected: no edge to take
        self.bar.select(0)
        self.bar.hover_at(QPointF(4.0, self.strip_y))
        self.assertIn("Drag its edges", self.bar.toolTip())
        self.assertIn("Shift", self.bar.toolTip())
        self.bar.hover_at(QPointF(6.02, self.strip_y))
        self.assertEqual(self.bar.cursor().shape(), Qt.CursorShape.SizeHorCursor)
        self.bar.hover_at(QPointF(9.0, self.strip_y))  # the other step's far edge
        self.assertNotEqual(self.bar.cursor().shape(), Qt.CursorShape.SizeHorCursor)
        self.bar.hover_at(QPointF(4.0, self.low_y))
        self.assertEqual(self.bar.toolTip(), "")

    def test_the_view_box_leaves_a_right_click_on_a_step_to_the_bar(self) -> None:
        view_box = self.plot._item.getViewBox()
        on_bar = self.bar.mapToScene(QPointF(4.0, self.strip_y))
        off_bar = self.bar.mapToScene(QPointF(4.0, self.low_y))
        self.assertTrue(self.bar.claims_scene_pos(on_bar))
        self.assertFalse(self.bar.claims_scene_pos(off_bar))
        ev = _Event((0.0, 0.0), button=Qt.MouseButton.RightButton, scene=on_bar)
        view_box.mouseClickEvent(ev)  # pyqtgraph's own handler would accept it and raise its menu
        self.assertFalse(ev.accepted)

    def test_pyqtgraphs_scene_finds_the_bar_under_the_pointer_and_only_there(self) -> None:
        """The scene decides who gets a click or a drag by asking each item whether its shape holds the pointer: ask the real scene."""
        scene = self.plot._item.scene()
        on = scene.itemsNearEvent(_Event((0.0, 0.0), scene=self.bar.mapToScene(QPointF(4.0, self.strip_y))))
        near_edge = scene.itemsNearEvent(_Event((0.0, 0.0), scene=self.bar.mapToScene(QPointF(6.03, self.strip_y))))
        off = scene.itemsNearEvent(_Event((0.0, 0.0), scene=self.bar.mapToScene(QPointF(4.0, self.low_y))))
        self.assertIn(self.bar, list(on))
        self.assertIn(self.bar, list(near_edge))
        self.assertNotIn(self.bar, list(off))

    def test_labels_next_to_the_data_answer_the_pointer_too(self) -> None:
        levels = bm.SectionLevels(low=601.0, median=605.0, high=610.0)
        self.plot.set_plan([_step(0, 2, 8, text="H2O", levels=levels)], bm.PlanBarSettings(text_align=bm.ALIGN_DATA, text_stop=4, height_stop=5))
        _APP.processEvents()
        to_device = self.bar.deviceTransform(self.plot._widget.viewportTransform())
        drawn = self.bar.layout(to_device, self.bar.view_rect())[0]
        self.assertIsNotNone(drawn.text_rect)
        inverse, _ok = to_device.inverted()
        centre = inverse.map(drawn.text_rect.center())
        self.assertEqual(self.bar.step_at_local(centre).index, 0)
        self.assertTrue(self.bar.shape().contains(centre))
        self.bar.select(0)
        self.assertIsNone(self.bar.edge_at_local(centre))  # a label has no edge to drag


# -- the in-place editor ------------------------------------------------------------------------------------------------------------


class StepTextEditorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.host = QtWidgets.QWidget()
        self.host.resize(600, 300)
        self.editor = StepTextEditor(self.host)
        self.committed: list[tuple[int, str]] = []
        self.editor.committed.connect(lambda i, t: self.committed.append((i, t)))
        self.bounds = QRectF(0, 0, 600, 300)

    def tearDown(self) -> None:
        self.host.deleteLater()

    def _open(self, text="H2O_--_ch1F", rect=QRectF(10, 10, 100, 20)) -> None:
        self.editor.open_over(2, text, rect, QFont(), self.bounds)

    def test_it_opens_over_the_bar_and_stays_inside_the_plot(self) -> None:
        self._open(rect=QRectF(550, 10, 40, 10))  # a narrow bar at the right edge
        geometry = self.editor.geometry()
        self.assertTrue(self.editor.is_open())
        self.assertGreaterEqual(geometry.width(), 180)
        self.assertLessEqual(geometry.right(), 600)
        self.assertGreaterEqual(geometry.left(), 0)

    def test_enter_or_leaving_the_field_commits_a_changed_text(self) -> None:
        self._open()
        self.editor.setText("Water")
        self.editor.editingFinished.emit()
        self.assertEqual(self.committed, [(2, "Water")])
        self.assertFalse(self.editor.is_open())

    def test_an_unchanged_text_commits_nothing_and_an_empty_one_does(self) -> None:
        self._open()
        self.editor.editingFinished.emit()
        self.assertEqual(self.committed, [])
        self._open()
        self.editor.setText("")
        self.editor.editingFinished.emit()
        self.assertEqual(self.committed, [(2, "")])  # "back to the measured note"

    def test_escape_cancels(self) -> None:
        self._open()
        self.editor.setText("Water")
        self.editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(self.editor.is_open())
        self.editor.editingFinished.emit()  # hiding the field fires this too
        self.assertEqual(self.committed, [])

    def test_a_second_finish_does_not_commit_twice(self) -> None:
        self._open()
        self.editor.setText("Water")
        self.editor.editingFinished.emit()
        self.editor.editingFinished.emit()
        self.assertEqual(len(self.committed), 1)

    def test_opening_another_step_commits_the_first(self) -> None:
        self._open()
        self.editor.setText("Water")
        self.editor.open_over(5, "x", None, QFont(), self.bounds)
        self.assertEqual(self.committed, [(2, "Water")])
        self.assertTrue(self.editor.is_open())


# -- the actions on the module ----------------------------------------------------------------------------------------------------


class _FakeDataset(QObject):
    dataset_loaded = pyqtSignal(object)
    dataset_cleared = pyqtSignal()

    def __init__(self, metadata) -> None:
        super().__init__()
        self.metadata = metadata

    def acquisition_metadata(self):
        return self.metadata

    def cube_start_unix_ms(self, cube_index):
        return 2_000 * int(cube_index)  # a cube every 2 s from t = 0

    def data_end_unix_ms(self):
        return 81_000


NOTE_1, NOTE_2 = "H2O_--_ch1F", "10perEG_-_ch1F"


class PlanBarActionsTest(unittest.TestCase):
    """The real module and plot with a fake dataset (steps 10 - 30 s and 30 - 50 s, cubes every 2 s); the axis is time in seconds."""

    def setUp(self) -> None:
        undo_manager.clear()
        plan = ImagingPlan(steps=[
            ImagingPlanStep(step=1, label=NOTE_1, start_unix_ms=10_000, end_unix_ms=30_000),
            ImagingPlanStep(step=2, label=NOTE_2, start_unix_ms=30_000, end_unix_ms=50_000),
        ])
        self.module = ExperimentPlanModule(_FakeDataset(ImagingAcquisitionMetadata(source_format="test", plan=plan)))
        self.plot = SensorgramPlot()
        self.plot.resize(640, 320)
        self.plot.show()
        cubes = list(range(41))
        self.context = PlanAxisContext(
            axis=AxisMap.build([2_000 * c for c in cubes], [2.0 * c for c in cubes]), grid_unix_ms=self.module.snap_grid_unix_ms(cubes), time_mode=True
        )
        self.actions = PlanBarActions(self.module, self.plot, lambda: self.context)
        self.plot.set_plan([_step(0, 10.0, 30.0, text=NOTE_1), _step(1, 30.0, 50.0, text=NOTE_2)], bm.PlanBarSettings())
        _APP.processEvents()

    def tearDown(self) -> None:
        undo_manager.clear()
        self.plot.close()
        self.plot.deleteLater()

    def _bounds(self):
        return [(s.start_unix_ms, s.end_unix_ms) for s in self.module.steps()]

    def _drag(self, index, edge, xs, together=False) -> None:
        self.plot.plan_edge_drag_started.emit(index, edge)
        for x in xs:
            self.plot.plan_edge_dragged.emit(index, edge, x, together)
        self.plot.plan_edge_drag_finished.emit()

    def test_a_drag_moves_only_the_dragged_step_snapped_to_a_cube_and_is_one_undo_step(self) -> None:
        self.plot.plan_edge_drag_started.emit(1, "start")
        self.assertFalse(undo_manager.can_undo)  # a gesture is open
        for x in (31.0, 35.0, 37.5):  # seconds; the cube starts are every 2 s
            self.plot.plan_edge_dragged.emit(1, "start", x, False)
        self.plot.plan_edge_drag_finished.emit()
        self.assertEqual(self._bounds(), [(10_000, 30_000), (38_000, 50_000)])  # step 1 is untouched; a gap opened
        self.assertTrue(undo_manager.can_undo)
        undo_manager.undo()
        self.assertEqual(self._bounds(), [(10_000, 30_000), (30_000, 50_000)])
        self.assertFalse(undo_manager.can_undo)  # that was the only entry

    def test_a_step_cannot_be_dragged_into_its_neighbour(self) -> None:
        self._drag(0, "end", (31.0, 41.5))
        self.assertEqual(self._bounds(), [(10_000, 30_000), (30_000, 50_000)])
        self.assertFalse(undo_manager.can_undo)

    def test_with_shift_a_shared_boundary_moves_both_steps(self) -> None:
        self._drag(0, "end", (31.0, 35.0, 41.5), together=True)
        self.assertEqual(self._bounds(), [(10_000, 42_000), (42_000, 50_000)])
        undo_manager.undo()
        self.assertEqual(self._bounds(), [(10_000, 30_000), (30_000, 50_000)])
        self.assertFalse(undo_manager.can_undo)

    def test_a_drag_with_no_axis_changes_nothing(self) -> None:
        self.context = None
        self._drag(0, "end", (41.5,), together=True)
        self.assertEqual(self._bounds(), [(10_000, 30_000), (30_000, 50_000)])
        self.assertFalse(undo_manager.can_undo)

    def test_a_drag_that_never_moves_leaves_no_undo_step(self) -> None:
        self._drag(0, "end", (30.2,), together=True)  # snaps to where it is
        self.assertFalse(undo_manager.can_undo)

    def test_the_editor_opens_with_the_whole_note_and_a_changed_text_is_a_label_edit(self) -> None:
        self.plot.plan_text_edit_requested.emit(1)
        editor = self.plot._text_editor
        self.assertTrue(editor.is_open())
        self.assertEqual(editor.text(), NOTE_2)
        editor.setText("Ten percent EG")
        editor.editingFinished.emit()
        self.assertEqual(self.module.steps()[1].label, "Ten percent EG")
        undo_manager.undo()
        self.assertEqual(self.module.steps()[1].label, NOTE_2)

    def test_an_empty_text_goes_back_to_the_measured_note(self) -> None:
        self.module.set_label(0, "Water")
        self.plot.plan_text_edit_requested.emit(0)
        self.plot._text_editor.setText("")
        self.plot._text_editor.editingFinished.emit()
        self.assertEqual(self.module.steps()[0].label, NOTE_1)
        self.assertEqual(self.module.state().edits, ())

    def _actions_of(self, index):
        menu = self.actions.build_menu(index)
        return menu, {action.text(): action for action in menu.actions() if action.text()}

    def test_the_menu_offers_what_applies_to_the_step(self) -> None:
        menu, actions = self._actions_of(0)
        self.assertEqual(list(actions), [f"Step 1: {NOTE_1}", "Edit text...", "Colour...", "Default colour", "Reset step to measured", "Reset all steps"])
        self.assertFalse(actions[f"Step 1: {NOTE_1}"].isEnabled())
        self.assertTrue(actions["Colour..."].isEnabled())
        for greyed in ("Default colour", "Reset step to measured", "Reset all steps"):
            self.assertFalse(actions[greyed].isEnabled(), greyed)  # nothing edited yet
        menu.deleteLater()
        self.assertIsNone(self.actions.build_menu(7))

    def test_colour_default_colour_and_reset_work_from_the_menu(self) -> None:
        with mock.patch("lspr_imaging_app.panels.sensorgram.plan_actions.QColorDialog.getColor", return_value=QColor("#336699")) as dialog:
            _menu, actions = self._actions_of(1)
            actions["Colour..."].trigger()
        self.assertTrue(dialog.called)
        self.assertEqual(self.module.steps()[1].color, "#336699")
        _menu, actions = self._actions_of(1)
        self.assertTrue(actions["Default colour"].isEnabled())
        self.assertTrue(actions["Reset step to measured"].isEnabled())
        actions["Default colour"].trigger()
        self.assertIsNone(self.module.steps()[1].color)
        self.module.set_label(0, "Water")
        self.module.set_color(1, "#112233")
        _menu, actions = self._actions_of(0)
        actions["Reset step to measured"].trigger()
        self.assertEqual(self.module.steps()[0].label, NOTE_1)
        self.assertEqual(self.module.steps()[1].color, "#112233")  # the other step keeps its edit
        _menu, actions = self._actions_of(0)
        actions["Reset all steps"].trigger()
        self.assertEqual(self.module.state().edits, ())

    def test_the_menu_acts_on_the_whole_selection_when_the_clicked_step_is_in_it(self) -> None:
        self.plot.plan_bar().select_many((0, 1), anchor=0)
        menu, actions = self._actions_of(0)
        self.assertEqual(list(actions)[0], "2 steps selected")
        self.assertIn("Reset steps to measured", actions)
        with mock.patch("lspr_imaging_app.panels.sensorgram.plan_actions.QColorDialog.getColor", return_value=QColor("#336699")):
            actions["Colour..."].trigger()
        self.assertEqual([s.color for s in self.module.steps()], ["#336699", "#336699"])
        undo_manager.undo()
        self.assertEqual(self.module.state().edits, ())  # one undo step for both
        self.assertFalse(undo_manager.can_undo)

    def test_default_colour_and_reset_work_on_the_selection_and_reset_all_stays_global(self) -> None:
        self.module.set_colors([0, 1], "#112233")
        self.module.set_label(0, "Water")
        self.plot.plan_bar().select_many((0, 1), anchor=0)
        _menu, actions = self._actions_of(1)
        self.assertTrue(actions["Default colour"].isEnabled())
        actions["Default colour"].trigger()
        self.assertEqual([s.color for s in self.module.steps()], [None, None])
        _menu, actions = self._actions_of(1)
        actions["Reset steps to measured"].trigger()
        self.assertEqual(self.module.state().edits, ())

    def test_the_menu_of_a_step_outside_the_selection_acts_on_that_step_alone(self) -> None:
        self.plot.plan_bar().select_many((0,), anchor=0)
        _menu, actions = self._actions_of(1)
        self.assertEqual(list(actions)[0], f"Step 2: {NOTE_2}")
        with mock.patch("lspr_imaging_app.panels.sensorgram.plan_actions.QColorDialog.getColor", return_value=QColor("#336699")):
            actions["Colour..."].trigger()
        self.assertEqual([s.color for s in self.module.steps()], [None, "#336699"])

    def test_edit_text_is_for_the_clicked_step_only(self) -> None:
        self.plot.plan_bar().select_many((0, 1), anchor=0)
        _menu, actions = self._actions_of(1)
        actions["Edit text..."].trigger()
        self.assertEqual(self.plot._text_editor.text(), NOTE_2)
        self.plot.close_plan_text_editor()

    def test_a_cancelled_colour_dialog_changes_nothing(self) -> None:
        with mock.patch("lspr_imaging_app.panels.sensorgram.plan_actions.QColorDialog.getColor", return_value=QColor()):
            _menu, actions = self._actions_of(0)
            actions["Colour..."].trigger()
        self.assertEqual(self.module.state().edits, ())

    def test_edit_text_from_the_menu_opens_the_editor(self) -> None:
        _menu, actions = self._actions_of(0)
        actions["Edit text..."].trigger()
        self.assertTrue(self.plot._text_editor.is_open())
        self.plot.close_plan_text_editor()
        self.assertFalse(self.plot._text_editor.is_open())


# -- the real panel ---------------------------------------------------------------------------------------------------------------


class PlanBarPanelGestureTest(unittest.TestCase):
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
        self.panel.resize(900, 420)
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
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None), "the panel never drew")
        self.bar = self.panel._plot.plan_bar()
        view = self.bar.view_rect()
        self.strip_y = view.bottom() - 0.01 * view.height()  # the bar is the top 5 %
        undo_manager.clear()  # adding the ROIs above pushed undo steps; the tests count only their own

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _drag_edge(self, down_x, to_x, *, shift=False) -> None:
        down = (down_x, self.strip_y)
        self.bar.mouseDragEvent(_Event((down_x + 0.1, self.strip_y), down=down, start=True, shift=shift))
        self.bar.mouseDragEvent(_Event((to_x, self.strip_y), down=down, shift=shift))
        self.bar.mouseDragEvent(_Event((to_x, self.strip_y), down=down, finish=True, shift=shift))

    def test_select_a_step_then_drag_its_edge_and_only_that_step_changes(self) -> None:
        self.bar.mouseClickEvent(_Event((1.0, self.strip_y)))  # select step 1: cubes 0 - 2, its end meets step 2 at cube 3
        self.assertEqual(self.bar.selected_indices(), (0,))
        self._drag_edge(3.0, 1.4)  # towards cube 1
        steps = self.bar.steps()
        self.assertEqual((round(steps[0].x_end, 6), round(steps[1].x_start, 6)), (1.0, 3.0))  # step 1 now ends at cube 1, step 2 is where it was
        undo_manager.undo()
        self.assertEqual(round(self.bar.steps()[0].x_end, 6), 3.0)
        self.assertEqual(self.bar.selected_indices(), (0,))  # selecting is not part of the undo

    def test_without_a_selection_the_same_drag_does_nothing(self) -> None:
        self._drag_edge(3.0, 1.4)
        self.assertEqual(round(self.bar.steps()[0].x_end, 6), 3.0)
        self.assertFalse(undo_manager.can_undo)

    def test_shift_drags_the_shared_boundary_with_the_neighbour(self) -> None:
        self.bar.mouseClickEvent(_Event((1.0, self.strip_y)))
        self._drag_edge(3.0, 4.4, shift=True)
        steps = self.bar.steps()
        self.assertEqual((round(steps[0].x_end, 6), round(steps[1].x_start, 6)), (4.0, 4.0))  # cube 4 is the nearest cube start
        effective = self.m.experiment_plan.steps()
        self.assertEqual((effective[0].end_unix_ms, effective[1].start_unix_ms), (T0 + 4 * CUBE_MS, T0 + 4 * CUBE_MS))
        undo_manager.undo()
        self.assertEqual(round(self.bar.steps()[0].x_end, 6), 3.0)

    def test_the_selection_goes_with_the_dataset(self) -> None:
        self.bar.select(1)
        self.m.experiment_plan.steps_changed.emit("dataset")
        self.assertEqual(self.bar.selected_indices(), ())

    def test_double_clicking_a_step_edits_its_note_on_the_bar(self) -> None:
        self.bar.mouseClickEvent(_Event((1.0, self.strip_y), double=True))
        editor = self.panel._plot._text_editor
        self.assertTrue(editor.is_open())
        self.assertEqual(editor.text(), "H2O_--_ch1F")
        editor.setText("Water")
        editor.editingFinished.emit()
        self.assertEqual(self.bar.steps()[0].text, "Water")
        self.assertTrue(self.bar.steps()[0].edited)
        self.assertEqual(self.bar.selected_indices(), (0,))  # the edit redraws the bar; the selection stays

    def test_the_menu_of_the_panel_acts_on_the_modules_steps(self) -> None:
        menu = self.panel._steps.actions.build_menu(1)
        actions = {a.text(): a for a in menu.actions() if a.text()}
        with mock.patch("lspr_imaging_app.panels.sensorgram.plan_actions.QColorDialog.getColor", return_value=QColor("#abcdef")):
            actions["Colour..."].trigger()
        self.assertEqual(self.bar.steps()[1].color, "#abcdef")


if __name__ == "__main__":
    unittest.main()
