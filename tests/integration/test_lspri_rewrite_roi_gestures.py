"""Image panel, ROIs tab, no tool armed (2026-10-07): left-drag rubber-band select,
move (from inside a selected ROI) and resize (from its border), each live and one undo
step, the hover cursors, and the right-click ROI menu.

Events are faked the way `test_lspri_rewrite_crop_tool.py` does it (a real panel,
a real view range, hand-built event objects, no mouse); the menu's choice is
injected by patching `show_roi_context_menu`. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PyQt6.QtCore import QPointF, Qt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        ImageTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.roi_context_menu import (
        ADD_ROI,
        ADD_TO_GROUP,
        DELETE,
        DESELECT,
        GROUP,
        UNGROUP,
        RoiMenuChoice,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_MENU = "lspr_imaging_app.panels.image.interaction.show_roi_context_menu"
_NAME_DIALOG = "lspr_imaging_app.panels.image.interaction.QInputDialog.getText"


class _Drag:
    def __init__(
        self, scene_pos: QPointF, button: Qt.MouseButton, *, start: bool, finish: bool,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier, down: QPointF | None = None,
    ) -> None:
        self._pos, self._button, self._start, self._finish, self._mods = scene_pos, button, start, finish, modifiers
        self._down = scene_pos if down is None else down

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def buttonDownScenePos(self) -> QPointF:  # noqa: N802
        return self._down

    def button(self) -> Qt.MouseButton:
        return self._button

    def isStart(self) -> bool:  # noqa: N802
        return self._start

    def isFinish(self) -> bool:  # noqa: N802
        return self._finish

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._mods


class _Click:
    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton) -> None:
        self._pos, self._button = scene_pos, button

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


class RoiGesturesTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))
        self.dataset = DatasetModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.active_tool = ActiveToolModule()
        self.busy = False
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.roi_toolbox,
            self.selection, self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), analysis_running=lambda: self.busy,
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        self.panel._tool_ribbon.set_category("ROIs")
        _pump(0.1)
        for x, y in ((20.0, 20.0), (40.0, 30.0), (60.0, 45.0)):
            self.roi_toolbox.add_roi(x, y, sample_diameter_px=10.0)
        _pump(0.2)
        undo_manager.clear()

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def scene(self, x: float, y: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(x, y))

    def drag(self, button: Qt.MouseButton, points: list[tuple[float, float]], **kw) -> bool:
        """Start at the first point, move through the rest, finish at the last; returns whether the start was claimed."""
        handler = self.panel._on_left_drag_event
        down = self.scene(*points[0])
        claimed = handler(_Drag(down, button, start=True, finish=False, down=down, **kw))
        if not claimed:
            return False
        for x, y in points[1:]:
            handler(_Drag(self.scene(x, y), button, start=False, finish=False, down=down, **kw))
        handler(_Drag(self.scene(*points[-1]), button, start=False, finish=True, down=down, **kw))
        return True

    def centre(self, roi_id: int) -> tuple[float, ...]:
        roi = self.roi_toolbox.roi_by_id(roi_id)
        return round(roi.center_x, 6), round(roi.center_y, 6)  # absorb float noise from the incremental drag

    def right_click(self, x: float, y: float, choice: RoiMenuChoice | None):
        with mock.patch(_MENU, return_value=choice) as menu:
            self.panel._on_scene_clicked(_Click(self.scene(x, y), Qt.MouseButton.RightButton))
        return menu

    # -- rubber band ------------------------------------------------------------------

    def test_left_drag_selects_the_rois_whose_centre_is_inside(self) -> None:
        self.assertTrue(self.drag(Qt.MouseButton.LeftButton, [(10.0, 10.0), (45.0, 35.0)]))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 2}))

    def test_a_rectangle_over_nothing_clears_the_selection(self) -> None:
        self.selection.set_roi_selection({3})
        self.drag(Qt.MouseButton.LeftButton, [(70.0, 5.0), (78.0, 12.0)])
        self.assertEqual(self.selection.selected_roi_ids(), frozenset())

    def test_ctrl_drag_adds_to_the_selection(self) -> None:
        self.selection.set_roi_selection({3})
        self.drag(Qt.MouseButton.LeftButton, [(10.0, 10.0), (30.0, 30.0)], modifiers=Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 3}))

    def test_the_rubber_band_is_hidden_after_the_drag(self) -> None:
        self.drag(Qt.MouseButton.LeftButton, [(10.0, 10.0), (30.0, 30.0)])
        self.assertFalse(self.panel._interaction._roi_gestures._band.isVisible())

    def test_left_drag_is_not_claimed_off_the_rois_tab_or_with_a_tool_armed(self) -> None:
        self.panel._tool_ribbon.set_category("Mask")
        self.assertFalse(self.drag(Qt.MouseButton.LeftButton, [(10.0, 10.0), (30.0, 30.0)]))
        self.panel._tool_ribbon.set_category("ROIs")
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self.assertFalse(self.drag(Qt.MouseButton.LeftButton, [(10.0, 10.0), (30.0, 30.0)]))

    # -- left-drag move (from inside a selected ROI) ------------------------------------

    def test_dragging_a_selected_roi_moves_every_selected_roi_by_the_cursor_delta(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.assertTrue(self.drag(Qt.MouseButton.LeftButton, [(40.0, 30.0), (45.0, 33.0), (50.0, 38.0)]))
        self.assertEqual(self.centre(1), (30.0, 28.0))
        self.assertEqual(self.centre(2), (50.0, 38.0))
        self.assertEqual(self.centre(3), (60.0, 45.0), "an unselected ROI stays")
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 2}), "moving does not change the selection")

    def test_the_move_is_live_during_the_drag(self) -> None:
        self.selection.set_roi_selection({2})
        handler = self.panel._on_left_drag_event
        down = self.scene(40.0, 30.0)
        handler(_Drag(down, Qt.MouseButton.LeftButton, start=True, finish=False, down=down))
        handler(_Drag(self.scene(44.0, 30.0), Qt.MouseButton.LeftButton, start=False, finish=False, down=down))
        self.assertAlmostEqual(self.centre(2)[0], 44.0)
        handler(_Drag(self.scene(44.0, 30.0), Qt.MouseButton.LeftButton, start=False, finish=True, down=down))

    def test_the_movement_before_pyqtgraph_reports_the_drag_is_not_lost(self) -> None:
        """pyqtgraph reports a drag only after a few pixels: the start event's position is
        already past the press. The ROI must follow the press point, not jump."""
        self.selection.set_roi_selection({2})
        down = self.scene(40.0, 30.0)
        handler = self.panel._on_left_drag_event
        handler(_Drag(self.scene(42.0, 30.0), Qt.MouseButton.LeftButton, start=True, finish=False, down=down))
        self.assertAlmostEqual(self.centre(2)[0], 42.0)
        handler(_Drag(self.scene(42.0, 30.0), Qt.MouseButton.LeftButton, start=False, finish=True, down=down))

    def test_the_overlay_follows_each_drag_event_without_waiting_for_the_redraw_timer(self) -> None:
        """The panel's redraw is debounced (a timer every change restarts), so it cannot
        fire during a continuous drag: the circles must be drawn by the drag itself."""
        self.selection.set_roi_selection({2})
        self.panel._draw_roi_overlay()
        curve = self.panel._roi_overlay.selection_curve
        before = float(curve.getData()[0][~np.isnan(curve.getData()[0])].mean())
        self.panel._redraw_timer.stop()
        handler = self.panel._on_left_drag_event
        down = self.scene(40.0, 30.0)
        handler(_Drag(down, Qt.MouseButton.LeftButton, start=True, finish=False, down=down))
        handler(_Drag(self.scene(46.0, 30.0), Qt.MouseButton.LeftButton, start=False, finish=False, down=down))
        xs = curve.getData()[0]
        after = float(xs[~np.isnan(xs)].mean())
        handler(_Drag(self.scene(46.0, 30.0), Qt.MouseButton.LeftButton, start=False, finish=True, down=down))
        self.assertAlmostEqual(after - before, 6.0, places=3)

    def test_the_whole_move_is_one_undo_step(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.drag(Qt.MouseButton.LeftButton, [(40.0, 30.0), (42.0, 31.0), (45.0, 33.0), (50.0, 38.0)])
        undo_manager.undo()
        self.assertEqual(self.centre(1), (20.0, 20.0))
        self.assertEqual(self.centre(2), (40.0, 30.0))
        self.assertFalse(undo_manager.can_undo, "one entry, not one per mouse move")

    def test_dragging_an_unselected_roi_draws_a_rectangle_instead_of_moving_it(self) -> None:
        self.selection.set_roi_selection({1})
        self.drag(Qt.MouseButton.LeftButton, [(60.0, 45.0), (62.0, 47.0)])
        self.assertEqual(self.centre(3), (60.0, 45.0))
        self.assertEqual(self.centre(1), (20.0, 20.0))

    def test_ctrl_drag_from_a_selected_roi_is_a_rectangle_not_a_move(self) -> None:
        self.selection.set_roi_selection({2})
        self.drag(Qt.MouseButton.LeftButton, [(40.0, 30.0), (62.0, 47.0)], modifiers=Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.centre(2), (40.0, 30.0))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({2, 3}))

    def test_a_moved_roi_may_leave_the_area_selection(self) -> None:
        self.panel._area_selection.set_rectangle(0, 0, 30, 30)
        self.selection.set_roi_selection({1})
        self.drag(Qt.MouseButton.LeftButton, [(20.0, 20.0), (50.0, 50.0)])
        self.assertEqual(self.centre(1), (50.0, 50.0))

    def test_right_drag_does_nothing_any_more(self) -> None:
        self.selection.set_roi_selection({1})
        event = _Drag(self.scene(20.0, 20.0), Qt.MouseButton.RightButton, start=True, finish=False)
        event.ignore = mock.Mock()
        self.panel._plot.vb.mouseDragEvent(event)
        event.ignore.assert_called_once()
        self.assertEqual(self.centre(1), (20.0, 20.0))

    # -- left-drag resize (from a selected ROI's border) ---------------------------------

    def test_dragging_the_border_sets_the_diameter_from_the_cursor_distance(self) -> None:
        self.selection.set_roi_selection({2})
        self.assertTrue(self.drag(Qt.MouseButton.LeftButton, [(45.0, 30.0), (47.0, 30.0), (48.0, 30.0)]))
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(2).sample_diameter_px, 16.0)
        self.assertEqual(self.centre(2), (40.0, 30.0), "resizing does not move the ROI")

    def test_every_selected_roi_gets_the_dragged_diameter(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.drag(Qt.MouseButton.LeftButton, [(45.0, 30.0), (46.5, 30.0)])
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(1).sample_diameter_px, 13.0)
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(2).sample_diameter_px, 13.0)
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(3).sample_diameter_px, 10.0, msg="unselected stays")

    def test_the_diameter_never_drops_below_the_minimum(self) -> None:
        from lspr_imaging_app.roi.toolbox import MIN_SAMPLE_DIAMETER_PX

        self.selection.set_roi_selection({2})
        self.drag(Qt.MouseButton.LeftButton, [(45.0, 30.0), (40.2, 30.0)])
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(2).sample_diameter_px, MIN_SAMPLE_DIAMETER_PX)

    def test_the_whole_resize_is_one_undo_step(self) -> None:
        self.selection.set_roi_selection({2})
        self.drag(Qt.MouseButton.LeftButton, [(45.0, 30.0), (46.0, 30.0), (48.0, 30.0)])
        undo_manager.undo()
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(2).sample_diameter_px, 10.0)
        self.assertFalse(undo_manager.can_undo)

    def test_the_border_of_an_unselected_roi_is_not_a_handle(self) -> None:
        self.selection.set_roi_selection({1})
        self.drag(Qt.MouseButton.LeftButton, [(45.0, 30.0), (48.0, 30.0)])
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(2).sample_diameter_px, 10.0)

    def test_hit_test_pulls_the_cursor_back_through_the_affine(self) -> None:
        """Display is 2x the stored size: a stored diameter of 10 is drawn with radius 10."""
        from lspr_imaging_app.panels.image.roi_gestures import SelectedApertures, hit_test

        selected = SelectedApertures(
            ids=np.array([7]), centers=np.array([[50.0, 50.0]]), diameters=np.array([10.0]), resizable=np.array([True])
        )
        linear = np.diag([2.0, 2.0])
        self.assertEqual(hit_test(60.0, 50.0, selected, linear, 1.0).zone, "edge")
        self.assertEqual(hit_test(55.0, 50.0, selected, linear, 1.0).zone, "body")
        self.assertIsNone(hit_test(65.0, 50.0, selected, linear, 1.0))

    def test_a_small_roi_keeps_a_grabbable_middle_and_a_mask_has_no_border(self) -> None:
        from lspr_imaging_app.panels.image.roi_gestures import SelectedApertures, hit_test

        small = SelectedApertures(
            ids=np.array([1]), centers=np.array([[0.0, 0.0]]), diameters=np.array([4.0]), resizable=np.array([True])
        )
        self.assertEqual(hit_test(0.0, 0.0, small, np.eye(2), 10.0).zone, "body")  # huge tolerance, centre still moves
        mask = SelectedApertures(
            ids=np.array([1]), centers=np.array([[0.0, 0.0]]), diameters=np.array([10.0]), resizable=np.array([False])
        )
        self.assertEqual(hit_test(5.0, 0.0, mask, np.eye(2), 1.0).zone, "body")

    # -- hover cursor -------------------------------------------------------------------

    def hover_cursor(self, x: float, y: float) -> Qt.CursorShape | None:
        self.panel._on_scene_moved(self.scene(x, y))
        cursor = self.panel._view.viewport().cursor().shape()
        return None if cursor == Qt.CursorShape.ArrowCursor else cursor

    def test_hovering_a_selected_rois_border_shows_a_resize_arrow_along_the_radius(self) -> None:
        self.selection.set_roi_selection({2})
        self.assertEqual(self.hover_cursor(45.0, 30.0), Qt.CursorShape.SizeHorCursor)
        self.assertEqual(self.hover_cursor(40.0, 35.0), Qt.CursorShape.SizeVerCursor)

    def test_hovering_inside_a_selected_roi_shows_the_move_cursor(self) -> None:
        self.selection.set_roi_selection({2})
        self.assertEqual(self.hover_cursor(40.0, 30.0), Qt.CursorShape.SizeAllCursor)

    def test_no_special_cursor_over_unselected_rois_or_empty_image(self) -> None:
        self.selection.set_roi_selection({2})
        self.assertIsNone(self.hover_cursor(60.0, 45.0))
        self.assertIsNone(self.hover_cursor(5.0, 60.0))

    def test_the_cursor_is_dropped_when_the_roi_tab_is_left(self) -> None:
        self.selection.set_roi_selection({2})
        self.hover_cursor(45.0, 30.0)
        self.panel._tool_ribbon.set_category("Mask")
        self.assertIsNone(self.hover_cursor(45.0, 30.0))

    def test_left_drag_is_not_claimed_over_a_selected_roi_off_the_rois_tab(self) -> None:
        self.selection.set_roi_selection({2})
        self.panel._tool_ribbon.set_category("Mask")
        self.assertFalse(self.drag(Qt.MouseButton.LeftButton, [(40.0, 30.0), (45.0, 30.0)]))

    # -- right-click menu -------------------------------------------------------------

    def test_right_click_on_an_unselected_roi_selects_it_before_the_menu(self) -> None:
        self.selection.set_roi_selection({1})
        menu = self.right_click(60.0, 45.0, None)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({3}))
        self.assertEqual(menu.call_args.kwargs["selected_count"], 1)

    def test_right_click_on_a_selected_roi_keeps_the_whole_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        menu = self.right_click(40.0, 30.0, None)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 2}))
        self.assertEqual(menu.call_args.kwargs["selected_count"], 2)

    def test_menu_group_makes_a_new_group_of_the_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        with mock.patch(_NAME_DIALOG, return_value=("Row", True)):
            self.right_click(40.0, 30.0, RoiMenuChoice(GROUP))
        group = self.roi_toolbox.group_for_roi(1)
        self.assertEqual(group.name, "Row")
        self.assertIs(self.roi_toolbox.group_for_roi(2), group)

    def test_a_cancelled_group_name_changes_nothing(self) -> None:
        self.selection.set_roi_selection({1})
        with mock.patch(_NAME_DIALOG, return_value=("", False)):
            self.right_click(20.0, 20.0, RoiMenuChoice(GROUP))
        self.assertIsNone(self.roi_toolbox.group_for_roi(1))

    def test_menu_add_to_group_and_ungroup(self) -> None:
        group_id = self.roi_toolbox.group_rois((1,), "A")
        self.selection.set_roi_selection({2})
        self.right_click(40.0, 30.0, RoiMenuChoice(ADD_TO_GROUP, group_id))
        self.assertEqual(self.roi_toolbox.group_for_roi(2).group_id, group_id)
        self.right_click(40.0, 30.0, RoiMenuChoice(UNGROUP))
        self.assertIsNone(self.roi_toolbox.group_for_roi(2))

    def test_the_menu_lists_existing_groups_and_whether_the_selection_is_grouped(self) -> None:
        self.roi_toolbox.group_rois((1,), "A")
        self.selection.set_roi_selection({1})
        menu = self.right_click(20.0, 20.0, None)
        self.assertEqual([name for _id, name in menu.call_args.kwargs["groups"]], ["A"])
        self.assertTrue(menu.call_args.kwargs["any_selected_grouped"])

    def test_menu_delete_removes_the_selected_rois(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.right_click(40.0, 30.0, RoiMenuChoice(DELETE))
        self.assertEqual(len(self.roi_toolbox.rois()), 1)

    def test_menu_delete_is_refused_while_an_analysis_runs(self) -> None:
        self.busy = True
        self.selection.set_roi_selection({1})
        statuses: list[str] = []
        self.panel.tool_status_changed.connect(statuses.append)
        menu = self.right_click(20.0, 20.0, RoiMenuChoice(DELETE))
        self.assertFalse(menu.call_args.kwargs["can_delete"])
        self.assertEqual(len(self.roi_toolbox.rois()), 3)
        self.assertTrue(any("analysis" in text for text in statuses))

    def test_menu_add_roi_places_one_at_the_click_and_selects_it(self) -> None:
        self.right_click(10.0, 55.0, RoiMenuChoice(ADD_ROI))
        self.assertEqual(len(self.roi_toolbox.rois()), 4)
        self.assertEqual(self.centre(4), (10.0, 55.0))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({4}))

    def test_menu_deselect_clears_the_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.right_click(40.0, 30.0, RoiMenuChoice(DESELECT))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset())

    def test_right_click_off_the_rois_tab_opens_no_roi_menu(self) -> None:
        self.panel._tool_ribbon.set_category("Mask")
        menu = self.right_click(40.0, 30.0, None)
        menu.assert_not_called()

    def test_the_canvas_tool_icons_are_gone_from_the_rois_tab(self) -> None:
        self.assertFalse(hasattr(self.panel, "_canvas_tools"))


if __name__ == "__main__":
    unittest.main()
