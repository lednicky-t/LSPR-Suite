"""Image panel, ROIs tab, no tool armed (2026-10-07): left-drag rubber-band select,
right-drag move (live, one undo step) and the right-click ROI menu.

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
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> None:
        self._pos, self._button, self._start, self._finish, self._mods = scene_pos, button, start, finish, modifiers

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

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
        handler = self.panel._on_left_drag_event if button == Qt.MouseButton.LeftButton else self.panel._on_right_drag_event
        claimed = handler(_Drag(self.scene(*points[0]), button, start=True, finish=False, **kw))
        if not claimed:
            return False
        for x, y in points[1:]:
            handler(_Drag(self.scene(x, y), button, start=False, finish=False, **kw))
        handler(_Drag(self.scene(*points[-1]), button, start=False, finish=True, **kw))
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

    # -- right-drag move --------------------------------------------------------------

    def test_right_drag_moves_every_selected_roi_by_the_cursor_delta(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.assertTrue(self.drag(Qt.MouseButton.RightButton, [(40.0, 30.0), (45.0, 33.0), (50.0, 38.0)]))
        self.assertEqual(self.centre(1), (30.0, 28.0))
        self.assertEqual(self.centre(2), (50.0, 38.0))
        self.assertEqual(self.centre(3), (60.0, 45.0), "an unselected ROI stays")

    def test_the_move_is_live_during_the_drag(self) -> None:
        handler = self.panel._on_right_drag_event
        handler(_Drag(self.scene(40.0, 30.0), Qt.MouseButton.RightButton, start=True, finish=False))
        handler(_Drag(self.scene(44.0, 30.0), Qt.MouseButton.RightButton, start=False, finish=False))
        self.assertAlmostEqual(self.centre(2)[0], 44.0)
        handler(_Drag(self.scene(44.0, 30.0), Qt.MouseButton.RightButton, start=False, finish=True))

    def test_the_overlay_follows_each_drag_event_without_waiting_for_the_redraw_timer(self) -> None:
        """The panel's redraw is debounced (a timer every change restarts), so it cannot
        fire during a continuous drag: the circles must be drawn by the drag itself."""
        self.selection.set_roi_selection({2})
        self.panel._draw_roi_overlay()
        curve = self.panel._roi_overlay.selection_curve
        before = float(curve.getData()[0][~np.isnan(curve.getData()[0])].mean())
        self.panel._redraw_timer.stop()
        handler = self.panel._on_right_drag_event
        handler(_Drag(self.scene(40.0, 30.0), Qt.MouseButton.RightButton, start=True, finish=False))
        handler(_Drag(self.scene(46.0, 30.0), Qt.MouseButton.RightButton, start=False, finish=False))
        xs = curve.getData()[0]
        after = float(xs[~np.isnan(xs)].mean())
        handler(_Drag(self.scene(46.0, 30.0), Qt.MouseButton.RightButton, start=False, finish=True))
        self.assertAlmostEqual(after - before, 6.0, places=3)

    def test_the_whole_drag_is_one_undo_step(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.drag(Qt.MouseButton.RightButton, [(40.0, 30.0), (42.0, 31.0), (45.0, 33.0), (50.0, 38.0)])
        undo_manager.undo()
        self.assertEqual(self.centre(1), (20.0, 20.0))
        self.assertEqual(self.centre(2), (40.0, 30.0))
        self.assertFalse(undo_manager.can_undo, "one entry, not one per mouse move")

    def test_dragging_an_unselected_roi_selects_only_it_and_moves_it(self) -> None:
        self.selection.set_roi_selection({1})
        self.drag(Qt.MouseButton.RightButton, [(60.0, 45.0), (62.0, 45.0)])
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({3}))
        self.assertEqual(self.centre(3), (62.0, 45.0))
        self.assertEqual(self.centre(1), (20.0, 20.0))

    def test_right_drag_starting_on_empty_image_is_not_claimed(self) -> None:
        self.selection.set_roi_selection({1})
        self.assertFalse(self.drag(Qt.MouseButton.RightButton, [(5.0, 60.0), (10.0, 60.0)]))
        self.assertEqual(self.centre(1), (20.0, 20.0))

    def test_a_moved_roi_may_leave_the_area_selection(self) -> None:
        self.panel._area_selection.set_rectangle(0, 0, 30, 30)
        self.drag(Qt.MouseButton.RightButton, [(20.0, 20.0), (50.0, 50.0)])
        self.assertEqual(self.centre(1), (50.0, 50.0))

    def test_right_drag_is_not_claimed_off_the_rois_tab(self) -> None:
        self.panel._tool_ribbon.set_category("Mask")
        self.assertFalse(self.drag(Qt.MouseButton.RightButton, [(20.0, 20.0), (30.0, 30.0)]))

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
