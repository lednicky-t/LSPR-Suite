"""The selection lock and the mode button in the Image panel (2026-11-15).

A real `ImagePanel` on a one-frame dataset (noisy background, a bright disk and a dark disk), like `test_lspri_selection_wand_panel.py`.
Pins what the maintainer decided: Lock turns the selection into the fence and clears it; a lock alone selects nothing, so Add selection
to the mask adds nothing; everything is dimmed outside the lock; every tool is clipped to it; one level only; Unlock keeps the
selection; the mode button (Replace / Add / Subtract / Intersect, wheel, Shift / Alt overrides) is remembered.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QIcon, QWheelEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
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
    from lspr_imaging_app.panels.image.selection_toolbar import LOCK_ACTION, UNLOCK_ACTION
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
    from lspr_imaging_app.selection.area_selection_module import ADD, COMBINE_MODES, INTERSECT, REPLACE, SUBTRACT
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

FRAME = (0, 500.0)
SHAPE = (120, 160)
BRIGHT = (40, 50, 8.0)  # row, col, radius
DARK = (80, 115, 10.0)


def _disk(row: int, col: int, radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    return np.hypot(yy - row, xx - col) <= radius


def _box(top: int, bottom: int, left: int, right: int) -> np.ndarray:
    pixels = np.zeros(SHAPE, dtype=bool)
    pixels[top:bottom, left:right] = True
    return pixels


def _pump_until(condition, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


class _Click:
    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton, modifiers: Qt.KeyboardModifier) -> None:
        self._pos, self._button, self._modifiers = scene_pos, button, modifiers

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers


class _Drag:
    """The slice of pyqtgraph's MouseDragEvent the canvas reads for a left drag."""

    def __init__(self, here: QPointF, *, start: bool, finish: bool, modifiers: Qt.KeyboardModifier) -> None:
        self._here, self._start, self._finish, self._modifiers = here, start, finish, modifiers
        self.accepted = False

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def isStart(self) -> bool:  # noqa: N802
        return self._start

    def isFinish(self) -> bool:  # noqa: N802
        return self._finish

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._here

    def buttonDownScenePos(self) -> QPointF:  # noqa: N802
        return self._here

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False


def _write_dataset(root: Path) -> ImageDataset:
    image = np.random.default_rng(4).normal(1000.0, 15.0, SHAPE)
    image[_disk(*BRIGHT)] += 600.0
    image[_disk(*DARK)] -= 500.0
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), image.astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class SelectionLockPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.area = AreaSelectionModule()
        self.active_tool = ActiveToolModule()
        self.roi_toolbox = RoiToolbox()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), self.roi_toolbox,
            SelectionModule(), self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.panel.resize(1100, 800)
        self.panel.show()
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.panel._selection_tools.wand._image is not None and self.panel._image_item.levels is not None)
        _pump()
        self.toolbar = self.panel._selection_tools.toolbar

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        undo_manager.clear()
        self._tmp.cleanup()

    # -- helpers ----------------------------------------------------------------------------------------------------

    def _scene(self, row: float, col: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(col, row))

    def _drag_rectangle(self, top: int, left: int, bottom: int, right: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        """Drag the armed Rectangle tool from (top, left) to (bottom, right), pixel-edge coordinates."""
        interaction = self.panel._interaction
        interaction.on_left_drag(_Drag(self._scene(top, left), start=True, finish=False, modifiers=modifiers))
        interaction.on_left_drag(_Drag(self._scene(bottom, right), start=False, finish=False, modifiers=modifiers))
        interaction.on_left_drag(_Drag(self._scene(bottom, right), start=False, finish=True, modifiers=modifiers))

    def _wand_click(self, row: int, col: int) -> None:
        scene = self._scene(row + 0.5, col + 0.5)
        self.panel._on_scene_clicked(_Click(scene, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        _pump_until(lambda: not self.panel._selection_tools.wand.is_busy())
        _pump(0.05)

    def _arm(self, mode: AreaSelectionMode) -> None:
        self.toolbar.tool_button(mode).click()

    def _lock_box(self, top: int = 20, bottom: int = 70, left: int = 20, right: int = 90) -> np.ndarray:
        box = _box(top, bottom, left, right)
        self.area.combine_region(box, REPLACE)
        self.toolbar.lock_button.click()
        return box

    def _wheel(self, button: QtWidgets.QWidget, delta_y: int) -> None:
        point = QPointF(5.0, 5.0)
        event = QWheelEvent(point, point, QPoint(0, 0), QPoint(0, delta_y), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(button, event)

    def _mask_now(self) -> np.ndarray | None:
        source = self.mask.resolve_mask_source(FRAME)
        return None if source is None else source[1]

    # -- the buttons -----------------------------------------------------------------------------------------------------

    def test_one_button_locks_needing_a_selection_and_then_unlocks_needing_only_the_lock(self) -> None:
        """2026-11-16 (maintainer): Lock and Unlock are one button; while a lock is on it is Unlock."""
        button = self.toolbar.lock_button
        self.assertFalse(hasattr(self.toolbar, "unlock_button"))
        self.assertEqual(self.toolbar.lock_action(), LOCK_ACTION)
        self.assertFalse(button.isEnabled(), "nothing to lock yet")
        self.area.combine_region(_box(20, 70, 20, 90), REPLACE)
        self.assertTrue(button.isEnabled())
        self.assertEqual(self.toolbar.lock_action(), LOCK_ACTION)
        lock_face = button.icon().cacheKey()
        button.click()
        self.assertTrue(self.area.has_lock())
        self.assertEqual(self.toolbar.lock_action(), UNLOCK_ACTION, "the selection became the fence: the button is Unlock now")
        self.assertTrue(button.isEnabled(), "an Unlock needs no selection")
        self.assertNotEqual(button.icon().cacheKey(), lock_face, "the open padlock")
        self.assertIn("Unlock", button.toolTip())
        button.click()
        self.assertFalse(self.area.has_lock())
        self.assertEqual(self.toolbar.lock_action(), LOCK_ACTION)
        self.assertFalse(button.isEnabled(), "unlocked with nothing selected: nothing to lock")

    def test_the_wheel_flips_a_locked_button_to_lock_inside_the_lock_and_a_click_narrows_it(self) -> None:
        self._lock_box(20, 70, 20, 90)
        button = self.toolbar.lock_button
        self._wheel(button, -120)
        self.assertEqual(self.toolbar.lock_action(), UNLOCK_ACTION, "no selection: there is nothing to lock inside the lock")
        inner = _box(30, 50, 30, 60)
        self.area.combine_region(inner, REPLACE)
        unlock_face = button.icon().cacheKey()
        self._wheel(button, -120)
        self.assertEqual(self.toolbar.lock_action(), LOCK_ACTION)
        self.assertNotEqual(button.icon().cacheKey(), unlock_face)
        self._wheel(button, 120)
        self.assertEqual(self.toolbar.lock_action(), UNLOCK_ACTION, "the wheel flips it back")
        self._wheel(button, -120)
        button.click()
        np.testing.assert_array_equal(self.area.lock_region(), inner)
        self.assertFalse(self.area.has_selection(), "the selection became the new fence")
        self.assertEqual(self.toolbar.lock_action(), UNLOCK_ACTION, "after the click the button rests on Unlock again")
        button.click()
        self.assertFalse(self.area.has_lock())

    def test_the_lock_face_is_dropped_when_the_selection_goes_and_the_wheel_is_left_to_the_column_without_a_lock(self) -> None:
        button = self.toolbar.lock_button
        self.area.combine_region(_box(20, 70, 20, 90), REPLACE)
        event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        button.wheelEvent(event)
        self.assertFalse(event.isAccepted(), "no lock: the wheel is not the button's, so the column scrolls over it")
        button.click()  # locked
        self.area.combine_region(_box(30, 50, 30, 60), REPLACE)
        self._wheel(button, -120)
        self.assertEqual(self.toolbar.lock_action(), LOCK_ACTION)
        self.area.clear()
        self.assertEqual(self.toolbar.lock_action(), UNLOCK_ACTION, "Lock inside the lock needs a selection")
        self.assertTrue(button.isEnabled())

    def test_lock_makes_the_selection_the_fence_says_so_and_ctrl_z_undoes_it(self) -> None:
        box = self._lock_box()
        self.assertTrue(self.area.has_lock())
        self.assertFalse(self.area.has_selection())
        np.testing.assert_array_equal(self.area.lock_region(), box)
        self.assertTrue(self.messages[-1].startswith("Locked to"), self.messages[-1:])
        undo_manager.undo()
        self.assertFalse(self.area.has_lock())
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), box)

    def test_unlock_keeps_the_selection_and_says_so(self) -> None:
        self._lock_box()
        inner = _box(30, 40, 30, 40)
        self.area.combine_region(inner, REPLACE)
        self.toolbar.lock_button.click()  # shows Unlock while a lock is on
        self.assertFalse(self.area.has_lock())
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), inner)
        self.assertTrue(self.messages[-1].startswith("Unlocked"))

    # -- what is on the image ---------------------------------------------------------------------------------------------

    def test_everything_outside_the_lock_is_dimmed_and_the_border_is_drawn(self) -> None:
        layer = self.panel._lock_layer
        self.assertFalse(layer.item.isVisible())
        box = self._lock_box()
        self.assertTrue(layer.item.isVisible())
        self.assertTrue(layer.border_item.isVisible())
        alpha = layer.item.image[..., 3]
        self.assertTrue(np.all(alpha[~box] > 0), "dimmed outside")
        self.assertTrue(np.all(alpha[box] == 0), "untouched inside")
        self.toolbar.lock_button.click()
        self.assertFalse(layer.item.isVisible())
        self.assertFalse(layer.border_item.isVisible())

    def test_the_lock_is_hidden_over_the_uncropped_canvas_of_rotate_and_crop(self) -> None:
        self._lock_box()
        self.active_tool.set_active(ImageTool.CROP, True)
        self.assertFalse(self.panel._lock_layer.item.isVisible())
        self.active_tool.set_active(ImageTool.CROP, False)
        self.assertTrue(self.panel._lock_layer.item.isVisible())

    def test_the_ants_outline_the_selection_not_the_lock(self) -> None:
        self._lock_box()
        self.assertFalse(self.panel._area_tool.is_animating(), "a lock alone selects nothing, so there are no ants")
        self.area.combine_region(_box(30, 40, 30, 40), REPLACE)
        self.assertTrue(self.panel._area_tool.is_animating())

    # -- tools stay inside the lock -----------------------------------------------------------------------------------------

    def test_a_rectangle_dragged_over_the_edge_of_the_lock_is_clipped(self) -> None:
        box = self._lock_box(20, 70, 20, 90)
        self._arm(AreaSelectionMode.RECTANGLE)
        self._drag_rectangle(10, 10, 100, 60)
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), box & _box(10, 100, 10, 60))

    def test_a_rectangle_wholly_outside_the_lock_selects_nothing_and_says_why(self) -> None:
        self._lock_box(20, 70, 20, 90)
        self._arm(AreaSelectionMode.RECTANGLE)
        self._drag_rectangle(90, 100, 115, 150)
        self.assertFalse(self.area.has_selection())
        self.assertIn("outside the lock", self.messages[-1])

    def test_the_wand_inside_the_lock_equals_the_unlocked_result_cut_to_the_lock(self) -> None:
        self._arm(AreaSelectionMode.WAND)
        self._wand_click(BRIGHT[0], BRIGHT[1])
        free = self.area.selected_mask(SHAPE).copy()
        self.area.clear()
        box = _box(35, 100, 45, 100)  # cuts the bright disk (rows 32..48, cols 42..58) on two sides
        self.area.combine_region(box, REPLACE)
        self.toolbar.lock_button.click()  # the wand stays armed (a second click on its button would leave the tool)
        self._wand_click(BRIGHT[0], BRIGHT[1])
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), free & box)

    def test_a_wand_click_outside_the_lock_does_nothing_and_says_so(self) -> None:
        self._lock_box(20, 70, 20, 90)  # the bright disk is inside, the dark disk (row 80) is outside
        self._arm(AreaSelectionMode.WAND)
        self._wand_click(DARK[0], DARK[1])
        self.assertFalse(self.area.has_selection())
        self.assertIn("outside the lock", self.messages[-1])

    # -- Add selection to the mask ------------------------------------------------------------------------------------------

    def test_add_selection_with_only_a_lock_adds_nothing_and_says_why(self) -> None:
        self._lock_box()
        editor = self.panel._mask_tab._selection_editor
        self.assertEqual(editor.apply(subtract=False), 0)
        self.assertIsNone(self._mask_now())
        self.assertIn("a lock alone selects nothing", self.messages[-1])

    def test_add_selection_inside_a_lock_adds_exactly_the_selection(self) -> None:
        self._lock_box()
        inner = _box(30, 40, 30, 40)
        self.area.combine_region(inner, REPLACE)
        self.panel._mask_tab.selection_actions.add_button.click()
        np.testing.assert_array_equal(self._mask_now(), inner)

    # -- the mode picker: one button with the Venn diagram of the picked mode, a menu of the four, the wheel ------------------

    def test_the_mode_menu_lists_the_four_modes_and_picking_one_sets_it(self) -> None:
        actions = self.toolbar.mode_menu.actions()
        self.assertEqual(len(actions), 4)
        self.assertEqual([a.isChecked() for a in actions], [True, False, False, False], "Replace is the starting mode")
        actions[3].trigger()
        self.assertEqual(self.area.combine_mode(), INTERSECT)
        self.assertEqual([a.isChecked() for a in actions], [False, False, False, True])
        self.assertFalse(self.area.has_selection(), "picking a mode selects nothing")
        self.assertFalse(undo_manager.can_undo, "a mode is a tool setting, not an undo step")

    def test_a_click_on_the_mode_button_opens_the_menu(self) -> None:
        self.assertFalse(self.toolbar.mode_menu.isVisible())
        self.toolbar.mode_button.click()
        self.assertTrue(self.toolbar.mode_menu.isVisible())
        self.toolbar.mode_menu.close()

    def test_the_wheel_over_the_mode_button_steps_through_the_modes_and_wraps(self) -> None:
        button = self.toolbar.mode_button
        self._wheel(button, -120)
        self.assertEqual(self.area.combine_mode(), ADD)
        self._wheel(button, -120)
        self.assertEqual(self.area.combine_mode(), SUBTRACT)
        self._wheel(button, -120)
        self._wheel(button, -120)
        self.assertEqual(self.area.combine_mode(), REPLACE, "wraps round after the last")
        self._wheel(button, 120)
        self.assertEqual(self.area.combine_mode(), INTERSECT)
        self.assertFalse(self.area.has_selection(), "turning the wheel selects nothing")

    def test_a_mode_set_from_elsewhere_changes_the_button_and_checks_its_menu_entry(self) -> None:
        self.area.set_combine_mode(SUBTRACT)  # as the restore of the saved mode does
        self.assertTrue(self.toolbar.mode_menu.actions()[2].isChecked())
        self.assertTrue(self.toolbar.mode_button.toolTip().startswith("Selection mode: Subtract"), "the short names (2026-11-16)")

    def test_the_button_shows_the_venn_diagram_of_the_picked_mode_and_the_menu_all_four(self) -> None:
        pictures = []
        for how, action in zip(COMBINE_MODES, self.toolbar.mode_menu.actions()):
            self.area.set_combine_mode(how)
            button_image = self.toolbar.mode_button.icon().pixmap(48, 48).toImage()
            self.assertFalse(button_image.isNull())
            self.assertEqual(button_image, action.icon().pixmap(48, 48).toImage(), "the button wears the picture its menu entry has")
            pictures.append(button_image)
        for i in range(len(pictures)):
            for j in range(i + 1, len(pictures)):
                self.assertNotEqual(pictures[i], pictures[j])

    def test_the_venn_icons_colour_what_the_mode_does(self) -> None:
        """Grey where the result is selected (the fill was blue until 2026-11-16: too close to the pink and green circle lines), nothing elsewhere,
        what is taken away included (it was orange; the table `MODE_AREAS` still tells it from "not selected", pinned against the real combine rules in the unit test). Sampled at
        the middle of each of the three areas of the 24-unit design, on the 48 px picture (so every sample is clear of the outlines)."""
        from PyQt6.QtGui import QColor

        from lspr_imaging_app.panels.image.combine_mode_icons import MODE_AREAS, SELECTED
        from lspr_imaging_app.panels.image.selection_palette import ICON_SELECTED_FILL

        colour = {SELECTED: QColor(ICON_SELECTED_FILL)}
        points = ((11, 24), (24, 24), (37, 24))  # A only, both, B only
        for how, action in zip(COMBINE_MODES, self.toolbar.mode_menu.actions()):
            image = action.icon().pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage()
            for point, state in zip(points, MODE_AREAS[how]):
                got = image.pixelColor(*point)
                if state != SELECTED:  # not selected, or taken away (the orange fill is gone, 2026-11-16): the plain background
                    self.assertEqual(got.alpha(), 0, (how, point, "nothing is drawn where nothing ends up selected: the circle's own colour shows it"))
                else:
                    self.assertEqual((got.red(), got.green(), got.blue(), got.alpha()), (colour[state].red(), colour[state].green(), colour[state].blue(), 255), (how, point))

    def test_the_sticky_mode_decides_a_drag_and_the_keys_override_it_for_one_drag(self) -> None:
        self._arm(AreaSelectionMode.RECTANGLE)
        self._drag_rectangle(10, 10, 40, 40)
        self.area.set_combine_mode(ADD)
        self._drag_rectangle(60, 60, 90, 90)  # Add (sticky)
        both = _box(10, 40, 10, 40) | _box(60, 90, 60, 90)
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), both)
        self._drag_rectangle(10, 10, 40, 40, Qt.KeyboardModifier.AltModifier)  # Alt overrides: take away
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), _box(60, 90, 60, 90))
        self._drag_rectangle(70, 70, 120, 120, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)  # intersect
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), _box(70, 90, 70, 90))
        self.assertEqual(self.area.combine_mode(), ADD, "a key changes one gesture, never the sticky mode")

    def test_threshold_style_use_inside_a_lock_with_intersect_narrows_the_selection(self) -> None:
        self._lock_box(20, 100, 20, 140)
        self.area.set_combine_mode(INTERSECT)
        self._arm(AreaSelectionMode.RECTANGLE)
        self._drag_rectangle(30, 30, 60, 60)  # nothing selected yet: the shape alone
        self._drag_rectangle(40, 40, 80, 80)  # intersect with the first
        np.testing.assert_array_equal(self.area.selected_mask(SHAPE), _box(40, 60, 40, 60))

    def test_the_mode_is_remembered_across_restarts(self) -> None:
        store = UiStateStore({"selection/combine_mode": "subtract"})
        self.panel.restore_ui_state(store)
        self.assertEqual(self.area.combine_mode(), SUBTRACT)
        self.area.set_combine_mode(INTERSECT)
        self.assertEqual(store.get("selection/combine_mode"), INTERSECT)

    def test_a_saved_mode_that_is_not_one_of_the_four_is_ignored(self) -> None:
        self.panel.restore_ui_state(UiStateStore({"selection/combine_mode": "xor"}))
        self.assertEqual(self.area.combine_mode(), REPLACE)


if __name__ == "__main__":
    unittest.main()
