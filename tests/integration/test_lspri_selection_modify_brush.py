"""Modify selection, the selection brush, and the Mask tab's Replace / Selection from mask (2026-11-15).

A real `ImagePanel` on a one-frame dataset. Pins: each Modify operation gives exactly what the old mask tool gave on the same pixels
(`raster_tools.apply_morphology_to_mask`) and never leaves the lock; the brush paints the exact stroke (`stroke_region`), Alt erases,
one stroke is one undo step, the lock clips it; Replace sets the mask to the selection (nothing selected: nothing happens);
Selection from mask followed by Grow and Replace equals dilating the mask.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt

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
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.image_tools.brush_stroke import stroke_region
    from lspr_imaging_app.image_tools.mask import raster_tools
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
    from lspr_imaging_app.selection.area_selection_module import ADD, INTERSECT, REPLACE, SUBTRACT
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

FRAME = (0, 500.0)
SHAPE = (120, 160)


def _box(top: int, bottom: int, left: int, right: int) -> np.ndarray:
    pixels = np.zeros(SHAPE, dtype=bool)
    pixels[top:bottom, left:right] = True
    return pixels


def _disk(row: int, col: int, radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    return np.hypot(yy - row, xx - col) <= radius


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
    def __init__(self, down: QPointF, here: QPointF, *, start: bool, finish: bool, modifiers: Qt.KeyboardModifier) -> None:
        self._down, self._here, self._start, self._finish, self._modifiers = down, here, start, finish, modifiers
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
        return self._down

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False


class _RightDrag:
    """The slice of pyqtgraph's MouseDragEvent the right-drag handlers read."""

    def __init__(self, down: QPointF, here: QPointF, *, start: bool, finish: bool) -> None:
        self._down, self._here, self._start, self._finish = down, here, start, finish

    def isStart(self) -> bool:  # noqa: N802
        return self._start

    def isFinish(self) -> bool:  # noqa: N802
        return self._finish

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._here

    def buttonDownScenePos(self) -> QPointF:  # noqa: N802
        return self._down


def _write_dataset(root: Path) -> ImageDataset:
    image = np.random.default_rng(4).normal(1000.0, 15.0, SHAPE)
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), image.astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class ModifyBrushTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.area = AreaSelectionModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.panel.resize(1100, 800)
        self.panel.show()
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.panel.selection_regions.displayed_shape() is not None and self.panel._image_item.levels is not None)
        _pump()
        self.toolbar = self.panel._selection_tools.toolbar
        self.menu = self.panel._selection_tools.modify_menu
        self.form = self.panel._selection_tools.brush_form

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        undo_manager.clear()
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------------------------------------------------

    def _scene(self, x: float, y: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(x, y))

    def _selected(self) -> np.ndarray:
        selected = self.area.selected_mask(SHAPE)
        self.assertIsNotNone(selected, self.messages[-1:])
        return selected

    def _lock_box(self, top: int = 20, bottom: int = 90, left: int = 20, right: int = 120) -> np.ndarray:
        box = _box(top, bottom, left, right)
        self.area.combine_region(box, REPLACE)
        self.toolbar.lock_button.click()
        return box

    def _arm_brush(self) -> None:
        self.toolbar.tool_button(AreaSelectionMode.BRUSH).click()

    def _drag(self, points: list[tuple[float, float]], modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        """Drag the armed brush through view points (x, y): the press at the first, the drag reported from the second (as pyqtgraph does)."""
        interaction = self.panel._interaction
        down = self._scene(*points[0])
        interaction.on_left_drag(_Drag(down, self._scene(*points[1]), start=True, finish=False, modifiers=modifiers))
        for point in points[2:]:
            interaction.on_left_drag(_Drag(down, self._scene(*point), start=False, finish=False, modifiers=modifiers))
        interaction.on_left_drag(_Drag(down, self._scene(*points[-1]), start=False, finish=True, modifiers=modifiers))

    def _mask_now(self) -> np.ndarray | None:
        source = self.mask.resolve_mask_source(FRAME)
        return None if source is None else source[1]

    # -- Modify selection ----------------------------------------------------------------------------------------------

    def test_the_modify_button_needs_a_selection(self) -> None:
        self.assertFalse(self.toolbar.modify_button.isEnabled())
        self.area.combine_region(_disk(60, 80, 10), REPLACE)
        self.assertTrue(self.toolbar.modify_button.isEnabled())
        self.assertTrue(all(a.isEnabled() for a in self.menu.actions_by_operation.values()))
        self.area.clear()
        self.assertFalse(any(a.isEnabled() for a in self.menu.actions_by_operation.values()))
        self.assertFalse(self.menu.apply("dilate"))
        self.assertIn("nothing is selected", self.messages[-1])

    def test_every_operation_gives_what_the_old_mask_tool_gave_on_the_same_pixels(self) -> None:
        shape = _disk(60, 80, 12) | _disk(60, 104, 5)
        for radius in (1, 3, 7):
            self.menu.radius_spin.setValue(radius)
            for operation in ("dilate", "erode", "open", "close"):
                self.area.combine_region(shape, REPLACE)
                expected = raster_tools.apply_morphology_to_mask(shape, operation, radius)
                self.assertEqual(self.menu.apply(operation), bool((expected != shape).any()), (operation, radius))
                np.testing.assert_array_equal(self._selected(), expected, (operation, radius))

    def test_fill_holes_turns_a_ring_into_a_disk(self) -> None:
        ring = _disk(60, 80, 14) & ~_disk(60, 80, 7)
        self.area.combine_region(ring, REPLACE)
        self.assertTrue(self.menu.apply("fill_holes"))
        np.testing.assert_array_equal(self._selected(), _disk(60, 80, 14))
        self.assertEqual(undo_manager.undo_label, "Fill holes selection")
        undo_manager.undo()
        np.testing.assert_array_equal(self._selected(), ring)

    def test_a_grow_never_leaves_the_lock(self) -> None:
        box = self._lock_box(20, 90, 20, 120)
        near_edge = _box(30, 60, 100, 119)
        self.area.combine_region(near_edge, REPLACE)
        self.menu.radius_spin.setValue(5)
        self.menu.apply("dilate")
        grown = self._selected()
        np.testing.assert_array_equal(grown, raster_tools.apply_morphology_to_mask(near_edge, "dilate", 5) & box)
        self.assertFalse((grown & ~box).any())

    def test_an_operation_is_one_undo_step_named_with_its_radius(self) -> None:
        self.area.combine_region(_disk(60, 80, 12), REPLACE)
        self.menu.radius_spin.setValue(4)
        self.menu.apply("dilate")
        self.assertEqual(undo_manager.undo_label, "Grow selection by 4 px")
        undo_manager.undo()
        np.testing.assert_array_equal(self._selected(), _disk(60, 80, 12))

    def test_shrinking_to_nothing_leaves_the_selection_alone_and_says_so(self) -> None:
        small = _disk(60, 80, 2)
        self.area.combine_region(small, REPLACE)
        self.menu.radius_spin.setValue(10)
        self.assertFalse(self.menu.apply("erode"))
        np.testing.assert_array_equal(self._selected(), small)
        self.assertIn("nothing would be left", self.messages[-1])

    def test_the_radius_is_the_mask_tools_setting_both_ways(self) -> None:
        self.menu.radius_spin.setValue(9)
        self.assertEqual(self.mask.settings().morphology_radius_px, 9)
        from lspr_imaging_app.panels.mask_tool_settings import push_tool_settings

        push_tool_settings(self.mask, morphology_radius_px=13)
        self.assertEqual(self.menu.radius_spin.value(), 13)

    def test_the_menu_actions_do_the_work_and_invert_is_in_the_menu(self) -> None:
        self.area.combine_region(_disk(60, 80, 12), REPLACE)
        self.menu.radius_spin.setValue(2)
        self.menu.actions_by_operation["dilate"].trigger()
        np.testing.assert_array_equal(self._selected(), raster_tools.apply_morphology_to_mask(_disk(60, 80, 12), "dilate", 2))
        self.menu.invert_action.trigger()
        self.assertTrue(self.area.is_inverted())

    def test_the_modify_button_opens_its_menu(self) -> None:
        self.area.combine_region(_disk(60, 80, 12), REPLACE)
        self.toolbar.modify_button.click()
        self.assertTrue(self.menu.isVisible())
        self.menu.close()

    # -- the brush -----------------------------------------------------------------------------------------------------

    def test_a_drag_paints_exactly_the_stroke_and_is_one_undo_step(self) -> None:
        self.form.size_spin.setValue(7)
        self._arm_brush()
        self._drag([(30.5, 40.5), (35.5, 40.5), (50.5, 40.5), (70.5, 40.5)])
        np.testing.assert_array_equal(self._selected(), stroke_region([(30.5, 40.5), (70.5, 40.5)], 7.0, SHAPE))
        self.assertEqual(undo_manager.undo_label, "Brush stroke")
        undo_manager.undo()
        self.assertFalse(self.area.has_selection(), "one drag, one step")

    def test_a_second_stroke_adds_to_the_first_in_the_add_mode(self) -> None:
        """In the Add mode (picked in the mode picker); with Replace picked a stroke replaces the selection, like every tool (2026-11-16)."""
        self.form.size_spin.setValue(5)
        self.area.set_combine_mode(ADD)
        self._arm_brush()
        self._drag([(20.5, 20.5), (25.5, 20.5), (40.5, 20.5)])
        self._drag([(20.5, 60.5), (25.5, 60.5), (40.5, 60.5)])
        expected = stroke_region([(20.5, 20.5), (40.5, 20.5)], 5.0, SHAPE) | stroke_region([(20.5, 60.5), (40.5, 60.5)], 5.0, SHAPE)
        np.testing.assert_array_equal(self._selected(), expected)

    def test_alt_erases_from_the_selection(self) -> None:
        box = _box(30, 50, 30, 80)
        self.area.combine_region(box, REPLACE)
        self.form.size_spin.setValue(5)
        self._arm_brush()
        self._drag([(40.5, 40.5), (45.5, 40.5), (60.5, 40.5)], Qt.KeyboardModifier.AltModifier)
        np.testing.assert_array_equal(self._selected(), box & ~stroke_region([(40.5, 40.5), (60.5, 40.5)], 5.0, SHAPE))
        self.assertEqual(undo_manager.undo_label, "Brush erase")

    def test_a_click_is_one_stamp_and_the_subtract_mode_erases(self) -> None:
        self.form.size_spin.setValue(9)
        self._arm_brush()
        self.panel._on_scene_clicked(_Click(self._scene(50.5, 50.5), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        stamp = stroke_region([(50.5, 50.5)], 9.0, SHAPE)
        np.testing.assert_array_equal(self._selected(), stamp)
        self.area.set_combine_mode(SUBTRACT)
        self.panel._on_scene_clicked(_Click(self._scene(50.5, 50.5), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(self.area.has_selection(), "the Subtract mode erases the stamp it painted")

    def test_replace_makes_each_stroke_the_new_selection_and_add_builds_one_up_from_several(self) -> None:
        """Maintainer, 2026-11-16: "I select replace, and when I start drawing it switches to add mode". The brush does what the picked mode
        says, like every selection tool: with Replace the second stroke replaces the first; with Add (or Shift) the strokes accumulate."""
        self.form.size_spin.setValue(5)
        self._arm_brush()
        first = stroke_region([(30.5, 30.5), (50.5, 30.5)], 5.0, SHAPE)
        second = stroke_region([(30.5, 60.5), (50.5, 60.5)], 5.0, SHAPE)
        self.area.clear()
        self.area.set_combine_mode(REPLACE)
        self._drag([(30.5, 30.5), (35.5, 30.5), (50.5, 30.5)])
        np.testing.assert_array_equal(self._selected(), first)
        self._drag([(30.5, 60.5), (35.5, 60.5), (50.5, 60.5)])
        np.testing.assert_array_equal(self._selected(), second, "Replace: the second stroke is the whole selection now")
        self.assertEqual(self.area.combine_mode(), REPLACE, "and the picked mode is still Replace")
        self.area.clear()
        self.area.set_combine_mode(ADD)
        self._drag([(30.5, 30.5), (35.5, 30.5), (50.5, 30.5)])
        self._drag([(30.5, 60.5), (35.5, 60.5), (50.5, 60.5)])
        np.testing.assert_array_equal(self._selected(), first | second, "Add: the strokes accumulate")
        self.area.clear()
        self.area.set_combine_mode(REPLACE)
        self._drag([(30.5, 30.5), (35.5, 30.5), (50.5, 30.5)])
        self._drag([(30.5, 60.5), (35.5, 60.5), (50.5, 60.5)], Qt.KeyboardModifier.ShiftModifier)
        np.testing.assert_array_equal(self._selected(), first | second, "Shift adds to a Replace selection")

    def test_the_intersect_mode_keeps_only_what_the_stroke_covers_it_does_not_jump_to_add(self) -> None:
        """Maintainer, 2026-11-16: "when I select intersection of the selection, it will always jump to adding mode" (Draw). The picked mode
        stays Intersect, a stroke intersects, and the toolbar's mode button never shows Add."""
        self.form.size_spin.setValue(5)
        box = np.zeros(SHAPE, dtype=bool)
        box[20:70, 20:70] = True
        self.area.combine_region(box, REPLACE)
        self.area.set_combine_mode(INTERSECT)
        self._arm_brush()
        self._drag([(30.5, 40.5), (35.5, 40.5), (60.5, 40.5)])
        stroke = stroke_region([(30.5, 40.5), (60.5, 40.5)], 5.0, SHAPE)
        np.testing.assert_array_equal(self._selected(), box & stroke)
        self.assertEqual(undo_manager.undo_label, "Brush intersect")
        self.assertEqual(self.area.combine_mode(), INTERSECT, "the picked mode is untouched")
        self.assertIsNone(self.panel._selection_tools.combine_badge.held_mode(), "no key is down, so the button shows the picked mode")

    def test_shift_alt_intersects_for_the_brush_too_and_a_click_is_one_intersecting_stamp(self) -> None:
        self.form.size_spin.setValue(9)
        box = np.zeros(SHAPE, dtype=bool)
        box[20:70, 20:70] = True
        self.area.combine_region(box, REPLACE)
        self._arm_brush()
        self._drag([(30.5, 40.5), (35.5, 40.5), (60.5, 40.5)], Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)
        stroke = stroke_region([(30.5, 40.5), (60.5, 40.5)], 9.0, SHAPE)
        np.testing.assert_array_equal(self._selected(), box & stroke)
        self.area.combine_region(box, REPLACE)
        self.area.set_combine_mode(INTERSECT)
        self.panel._on_scene_clicked(_Click(self._scene(50.5, 50.5), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        np.testing.assert_array_equal(self._selected(), box & stroke_region([(50.5, 50.5)], 9.0, SHAPE))

    def test_the_lock_clips_a_stroke_and_refuses_one_wholly_outside(self) -> None:
        box = self._lock_box(20, 90, 20, 120)
        self.form.size_spin.setValue(9)
        self._arm_brush()
        self._drag([(100.5, 50.5), (105.5, 50.5), (140.5, 50.5)])
        np.testing.assert_array_equal(self._selected(), box & stroke_region([(100.5, 50.5), (140.5, 50.5)], 9.0, SHAPE))
        self.area.clear()
        self._drag([(130.5, 100.5), (135.5, 100.5), (150.5, 100.5)])
        self.assertFalse(self.area.has_selection())
        self.assertIn("outside the lock", self.messages[-1])

    def test_the_brush_size_is_the_mask_tools_setting_and_the_ring_follows_the_mouse(self) -> None:
        self.form.size_spin.setValue(21)
        self.assertEqual(self.mask.settings().brush_size_px, 21)
        self._arm_brush()
        self.panel._interaction.on_scene_moved(self._scene(60.0, 60.0))
        ring = self.panel._area_tool._cursor_items[-1]
        self.assertTrue(ring.isVisible())
        self.assertAlmostEqual(ring.rect().width(), 21.0)
        self.assertAlmostEqual(ring.rect().center().x(), 60.0)
        self._arm_brush()  # leaves the tool
        self.panel._interaction.on_scene_moved(self._scene(61.0, 61.0))
        self.assertFalse(ring.isVisible())

    def test_right_drag_with_the_brush_armed_sets_the_size_sideways_and_the_ring_follows(self) -> None:
        """2026-11-15 (maintainer: tool gestures that change a parameter with the right button held and moved)."""
        self.form.size_spin.setValue(10)
        self._arm_brush()
        interaction = self.panel._interaction
        down = self._scene(60.0, 60.0)

        def drag(dx: float, dy_up: float, *, start=False, finish=False):
            here = QPointF(down.x() + dx, down.y() - dy_up)
            interaction.on_right_drag(_RightDrag(down, here, start=start, finish=finish))

        drag(4.0, 0.0, start=True)
        drag(80.0, 0.0)  # one doubling to the right
        self.assertEqual(self.form.size_spin.value(), 20)
        self.assertEqual(self.mask.settings().brush_size_px, 20, "the size is the mask tools' setting")
        ring = self.panel._area_tool._cursor_items[-1]
        self.assertTrue(ring.isVisible())
        self.assertAlmostEqual(ring.rect().width(), 20.0)
        self.assertAlmostEqual(ring.rect().center().x(), 60.0, places=1)
        readout = self.panel._selection_tools.value_drag[AreaSelectionMode.BRUSH].readout
        self.assertIn("Brush size 20 px", readout.text())
        drag(80.0, 200.0)  # up does nothing for the brush: only the size, sideways
        self.assertEqual(self.form.size_spin.value(), 20)
        drag(-80.0, 0.0, finish=True)  # back to the left: half
        self.assertEqual(self.form.size_spin.value(), 5)
        self.assertFalse(readout.isVisible())
        drag(-4000.0, 0.0, start=True)
        self.assertEqual(self.form.size_spin.value(), self.form.size_spin.minimum(), "kept inside the box's range")

    def test_right_drag_without_the_brush_armed_leaves_the_size_alone(self) -> None:
        self.form.size_spin.setValue(10)
        down = self._scene(60.0, 60.0)
        self.assertFalse(self.panel._interaction.on_right_drag(_RightDrag(down, QPointF(down.x() + 80.0, down.y()), start=True, finish=False)))
        self.assertEqual(self.form.size_spin.value(), 10)

    def test_a_right_click_on_the_brush_button_opens_its_size(self) -> None:
        self.toolbar.show_brush_settings()
        self.assertTrue(self.toolbar.brush_menu.isVisible())
        self.assertTrue(self.form.isVisible())
        self.toolbar.brush_menu.close()

    # -- the Mask tab: Replace and Selection from mask ------------------------------------------------------------

    def test_replace_makes_the_mask_exactly_the_selection(self) -> None:
        self.mask.set_mask_change(FRAME, "persistent", _box(10, 30, 10, 30))
        selection = _disk(60, 80, 10)
        self.area.combine_region(selection, REPLACE)
        self.panel._mask_tab.selection_actions.replace_button.click()
        np.testing.assert_array_equal(self._mask_now(), selection)
        self.assertEqual(undo_manager.undo_label, "Replace mask with selection")
        undo_manager.undo()
        np.testing.assert_array_equal(self._mask_now(), _box(10, 30, 10, 30))

    def test_replace_with_nothing_selected_leaves_the_mask_alone(self) -> None:
        old = _box(10, 30, 10, 30)
        self.mask.set_mask_change(FRAME, "persistent", old)
        self.assertFalse(self.panel._mask_tab.selection_actions.replace_button.isEnabled())
        self.assertEqual(self.panel._mask_tab._selection_editor.replace(), 0)
        np.testing.assert_array_equal(self._mask_now(), old)
        self.assertIn("nothing is selected", self.messages[-1])

    def test_replace_under_a_lock_changes_only_the_mask_inside_the_lock(self) -> None:
        outside, inside = _box(100, 115, 100, 150), _box(40, 60, 40, 60)  # one block in the lock's area, one far from it
        self.mask.set_mask_change(FRAME, "persistent", outside | inside)
        self._lock_box(30, 90, 30, 90)
        selection = _box(45, 55, 45, 55)  # smaller than the block inside the lock
        self.area.combine_region(selection, REPLACE)
        self.panel._mask_tab.selection_actions.replace_button.click()
        np.testing.assert_array_equal(self._mask_now(), outside | selection)
        self.assertIn("inside the lock", self.messages[-1])

    def test_a_lock_alone_does_not_make_replace_wipe_the_mask(self) -> None:
        old = _box(10, 30, 10, 30)
        self.mask.set_mask_change(FRAME, "persistent", old)
        self._lock_box()
        self.assertEqual(self.panel._mask_tab._selection_editor.replace(), 0)
        np.testing.assert_array_equal(self._mask_now(), old)

    def test_selection_from_mask_makes_the_mask_the_selection(self) -> None:
        box = _box(10, 30, 10, 30)
        self.mask.set_mask_change(FRAME, "persistent", box)
        self.panel._mask_tab.selection_actions.from_mask_button.click()
        np.testing.assert_array_equal(self._selected(), box)
        self.assertEqual(undo_manager.undo_label, "Selection from mask")

    def test_selection_from_mask_follows_the_mode_and_the_lock(self) -> None:
        box = _box(10, 60, 10, 60)
        self.mask.set_mask_change(FRAME, "persistent", box)
        editor = self.panel._mask_tab._selection_editor
        self.area.combine_region(_box(30, 100, 30, 100), REPLACE)
        editor.from_mask(INTERSECT)
        np.testing.assert_array_equal(self._selected(), _box(30, 60, 30, 60))
        self.area.clear()
        self._lock_box(70, 100, 70, 100)  # no mask pixel inside this lock
        self.assertEqual(editor.from_mask(REPLACE), 0)
        self.assertIn("inside the lock", self.messages[-1])

    def test_selection_from_mask_with_no_mask_says_so(self) -> None:
        self.assertEqual(self.panel._mask_tab._selection_editor.from_mask(), 0)
        self.assertIn("no mask at this frame", self.messages[-1])

    def test_a_mask_can_be_grown_through_the_selection_tools(self) -> None:
        """Selection from mask -> Grow -> Replace equals dilating the mask (no rotation: displayed == raw, so exactly)."""
        box = _box(40, 70, 50, 90)
        self.mask.set_mask_change(FRAME, "persistent", box)
        actions = self.panel._mask_tab.selection_actions
        actions.from_mask_button.click()
        self.menu.radius_spin.setValue(4)
        self.menu.apply("dilate")
        actions.replace_button.click()
        np.testing.assert_array_equal(self._mask_now(), raster_tools.apply_morphology_to_mask(box, "dilate", 4))


if __name__ == "__main__":
    unittest.main()
