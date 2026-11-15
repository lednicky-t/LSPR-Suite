"""The magic wand in the Image panel, the Mask tab's Selection group, and undoable mask edits (2026-11-14).

A real `ImagePanel` on a one-frame dataset: a noisy background with a bright disk and a dark disk. The wand runs on its
worker thread; the tests pump events until it is done.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, QSize, Qt
from PyQt6.QtGui import QContextMenuEvent, QWheelEvent

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
    from lspr_imaging_app.image_tools.selection_wand import WandMethod
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
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


class _Drag:
    """The slice of pyqtgraph's MouseDragEvent the view box and the wand's right-drag read."""

    def __init__(self, down: QPointF, here: QPointF, *, start: bool, finish: bool,
                 button: Qt.MouseButton = Qt.MouseButton.RightButton) -> None:
        self._down, self._here, self._start, self._finish, self._button = down, here, start, finish, button
        self.accepted = False

    def button(self) -> Qt.MouseButton:
        return self._button

    def isStart(self) -> bool:  # noqa: N802
        return self._start

    def isFinish(self) -> bool:  # noqa: N802
        return self._finish

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._here

    def buttonDownScenePos(self) -> QPointF:  # noqa: N802
        return self._down

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False


class _Click:
    """The slice of pyqtgraph's MouseClickEvent the panel reads."""

    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton, modifiers: Qt.KeyboardModifier) -> None:
        self._pos, self._button, self._modifiers = scene_pos, button, modifiers

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers


def _write_dataset(root: Path) -> ImageDataset:
    image = np.random.default_rng(4).normal(1000.0, 15.0, SHAPE)
    image[_disk(*BRIGHT)] += 600.0
    image[_disk(*DARK)] -= 500.0
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), image.astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class SelectionWandPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.area = AreaSelectionModule()
        self.active_tool = ActiveToolModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.panel.resize(1100, 800)
        self.panel.show()
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.panel._wand._image is not None and self.panel._image_item.levels is not None)
        _pump()
        self.form = self.panel._selection_toolbar.wand_form

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        undo_manager.clear()
        self._tmp.cleanup()

    def _arm_wand(self) -> None:
        self.panel._selection_toolbar.tool_button(AreaSelectionMode.WAND).click()

    def _click(self, row: int, col: int, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
        scene = self.panel._plot.vb.mapViewToScene(QPointF(col + 0.5, row + 0.5))
        self.panel._on_scene_clicked(_Click(scene, Qt.MouseButton.LeftButton, modifiers))
        self.assertTrue(_pump_until(lambda: not self.panel._wand.is_busy()), "the wand finished")
        _pump(0.05)

    def _selected(self) -> np.ndarray:
        mask = self.area.mask(SHAPE)
        self.assertIsNotNone(mask)
        return mask

    # -- the wand --------------------------------------------------------------------------------------------------

    def test_a_click_with_the_wand_selects_the_feature_under_it(self) -> None:
        self._arm_wand()
        self.assertIs(self.active_tool.active(), ImageTool.SELECT_AREA)
        self._click(BRIGHT[0] + 2, BRIGHT[1] - 3)
        selected = self._selected()
        disk = _disk(*BRIGHT)
        # Every pixel of the disk is selected, and nothing far from it (the disk is sharp, so at most a pixel of rim).
        self.assertTrue(np.all(selected[disk]))
        self.assertFalse(np.any(selected & ~_disk(BRIGHT[0], BRIGHT[1], BRIGHT[2] + 2)))
        self.assertIsNotNone(self.area.region())
        self.assertTrue(self.panel._area_tool.is_animating(), "the ants run around a pixel-map selection")

    def test_shift_adds_and_alt_takes_away(self) -> None:
        self._arm_wand()
        self._click(*BRIGHT[:2])
        self._click(*DARK[:2], Qt.KeyboardModifier.ShiftModifier)
        selected = self._selected()
        self.assertTrue(selected[BRIGHT[0], BRIGHT[1]] and selected[DARK[0], DARK[1]])
        self._click(*BRIGHT[:2], Qt.KeyboardModifier.AltModifier)
        selected = self._selected()
        self.assertFalse(selected[BRIGHT[0], BRIGHT[1]])
        self.assertTrue(selected[DARK[0], DARK[1]])

    def test_every_method_selects_the_dark_disk(self) -> None:
        self._arm_wand()
        for index in range(self.form.method.count()):
            self.form.method.setCurrentIndex(index)
            self.area.clear()
            self._click(*DARK[:2])
            self.assertTrue(self.area.has_selection(), (self.form.settings().method, self.messages[-1:]))
            self.assertTrue(self._selected()[DARK[0], DARK[1]])
        self.assertEqual({WandMethod(self.form.method.itemData(i)) for i in range(self.form.method.count())}, set(WandMethod))

    def test_a_click_on_plain_background_selects_nothing_and_says_why(self) -> None:
        self._arm_wand()
        self._click(10, 140)
        self.assertFalse(self.area.has_selection())
        self.assertIn("threshold", self.messages[-1])

    def test_the_settings_form_shows_only_the_picked_methods_threshold(self) -> None:
        for index in range(self.form.method.count()):
            self.form.method.setCurrentIndex(index)
            method = self.form.settings().method
            self.assertEqual(not self.form.contrast_threshold.isHidden(), method is WandMethod.CONTRAST)
            self.assertEqual(not self.form.brightness_tolerance.isHidden(), method is WandMethod.BRIGHTNESS)
            self.assertEqual(not self.form.edge_threshold.isHidden(), method is WandMethod.EDGES)
            self.assertEqual(self.form.background.isEnabled(), method is WandMethod.CONTRAST)

    def test_a_right_click_on_the_wand_button_opens_its_settings_and_a_left_click_does_not(self) -> None:
        toolbar = self.panel._selection_toolbar
        button = toolbar.tool_button(AreaSelectionMode.WAND)
        self.assertFalse(hasattr(toolbar, "settings_button"), "the gear button is gone")
        button.click()  # a left click arms the wand, nothing more
        self.assertFalse(toolbar.wand_menu.isVisible())
        centre = button.rect().center()
        QtWidgets.QApplication.sendEvent(
            button, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, centre, button.mapToGlobal(centre))
        )
        self.assertTrue(toolbar.wand_menu.isVisible())
        self.assertTrue(self.form.isVisible())
        self.assertIs(self.active_tool.active(), ImageTool.SELECT_AREA, "the right-click leaves the armed tool alone")
        toolbar.wand_menu.close()
        # The other two tools have no pop-up.
        other = toolbar.tool_button(AreaSelectionMode.RECTANGLE)
        QtWidgets.QApplication.sendEvent(other, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, centre, other.mapToGlobal(centre)))
        self.assertFalse(toolbar.wand_menu.isVisible())

    def _wheel(self, button: QtWidgets.QWidget, delta_y: int) -> None:
        point = QPointF(5.0, 5.0)
        event = QWheelEvent(point, point, QPoint(0, 0), QPoint(0, delta_y), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(button, event)

    def test_the_wheel_over_the_wand_button_switches_the_method_and_wraps_round(self) -> None:
        button = self.panel._selection_toolbar.tool_button(AreaSelectionMode.WAND)
        order = [WandMethod(self.form.method.itemData(i)) for i in range(self.form.method.count())]
        self.assertEqual(len(order), 3)
        self.form.method.setCurrentIndex(0)
        self._wheel(button, -120)  # towards the user: the next method
        self.assertIs(self.form.settings().method, order[1])
        self._wheel(button, -120)
        self._wheel(button, -120)
        self.assertIs(self.form.settings().method, order[0], "after the last one it wraps round to the first")
        self._wheel(button, 120)  # away from the user: the previous method
        self.assertIs(self.form.settings().method, order[2])
        for _ in range(7):  # a smooth wheel sends small deltas: 8 x 15 make one notch
            self._wheel(button, -15)
        self.assertIs(self.form.settings().method, order[2])
        self._wheel(button, -15)
        self.assertIs(self.form.settings().method, order[0])
        self.assertIn(self.form.method_label(), button.toolTip(), "the tool tip names the method in use")
        self.assertFalse(self.area.has_selection(), "turning the wheel makes no selection")

    def test_the_wand_button_shows_a_different_icon_for_each_method_and_none_is_another_tools(self) -> None:
        toolbar = self.panel._selection_toolbar
        wand = toolbar.tool_button(AreaSelectionMode.WAND)

        def picture(button: QtWidgets.QAbstractButton):
            return button.icon().pixmap(QSize(32, 32)).toImage()

        icons = []
        for index in range(self.form.method.count()):
            self.form.method.setCurrentIndex(index)
            image = picture(wand)
            self.assertFalse(image.isNull(), "every method has a vendored icon")
            icons.append(image)
        others = [picture(toolbar.tool_button(AreaSelectionMode.RECTANGLE)), picture(toolbar.tool_button(AreaSelectionMode.LASSO)),
                  toolbar.deselect_button.icon().pixmap(QSize(32, 32)).toImage()]
        everything = icons + others
        for i, first in enumerate(everything):
            for second in everything[i + 1 :]:
                self.assertNotEqual(first, second)

    def test_the_icon_follows_the_method_when_it_is_switched_from_anywhere(self) -> None:
        wand = self.panel._selection_toolbar.tool_button(AreaSelectionMode.WAND)
        self.form.method.setCurrentIndex(0)
        first = wand.icon().pixmap(QSize(32, 32)).toImage()
        self._wheel(wand, -120)  # the wheel switches the method ...
        second = wand.icon().pixmap(QSize(32, 32)).toImage()
        self.assertNotEqual(first, second)
        self.form.method.setCurrentIndex(0)  # ... and so does the pop-up's drop-down
        self.assertEqual(wand.icon().pixmap(QSize(32, 32)).toImage(), first)

    def _right_drag(self, down: tuple[float, float], here: tuple[float, float], *, start: bool = False, finish: bool = False) -> bool:
        event = _Drag(QPointF(*down), QPointF(*here), start=start, finish=finish)
        self.panel._plot.vb.mouseDragEvent(event)
        return event.accepted

    def test_a_right_drag_with_the_wand_armed_sets_the_threshold_and_grow(self) -> None:
        self._arm_wand()
        form = self.form
        form.method.setCurrentIndex(0)
        self.assertIs(form.settings().method, WandMethod.CONTRAST)
        form.contrast_threshold.setValue(3.0)
        form.grow.setValue(0)
        centre = self.panel._plot.vb.mapViewToScene(QPointF(80.0, 60.0))
        x0, y0 = centre.x(), centre.y()
        self.assertTrue(self._right_drag((x0, y0), (x0 + 5, y0 - 2), start=True))
        self.assertTrue(self.panel._wand_drag.readout.isVisible(), "the numbers are shown beside the cursor while the button is down")
        self.assertTrue(self._right_drag((x0, y0), (x0 + 80, y0 - 30)))
        # Right = a bigger region: for Contrast that is a lower threshold (80 px = one halving); up 30 px = 5 px of Grow.
        self.assertAlmostEqual(form.contrast_threshold.value(), 1.5, places=1)
        self.assertEqual(form.grow.value(), 5)
        self.assertIn("1.5", self.panel._wand_drag.readout.text())
        self.assertTrue(self._right_drag((x0, y0), (x0 + 80, y0 - 30), finish=True))
        self.assertFalse(self.panel._wand_drag.readout.isVisible())
        self.assertFalse(self.area.has_selection(), "adjusting selects nothing")
        # The next drag starts from the new numbers; dragging left and down goes the other way.
        self._right_drag((x0, y0), (x0 - 1, y0 + 1), start=True)
        self._right_drag((x0, y0), (x0 - 80, y0 + 12), finish=True)
        self.assertAlmostEqual(form.contrast_threshold.value(), 3.0, places=1)
        self.assertEqual(form.grow.value(), 3)

    def test_a_right_drag_makes_the_region_bigger_to_the_right_for_every_method(self) -> None:
        self._arm_wand()
        centre = self.panel._plot.vb.mapViewToScene(QPointF(80.0, 60.0))
        x0, y0 = centre.x(), centre.y()
        for index in range(self.form.method.count()):
            self.form.method.setCurrentIndex(index)
            box = self.form.threshold_box()
            box.setValue(4.0)
            self._right_drag((x0, y0), (x0, y0), start=True)
            self._right_drag((x0, y0), (x0 + 80, y0), finish=True)
            expected = 2.0 if self.form.settings().method is WandMethod.CONTRAST else 8.0
            self.assertAlmostEqual(box.value(), expected, places=1, msg=self.form.settings().method)

    def test_a_right_drag_stays_inside_the_ranges_of_the_spin_boxes(self) -> None:
        self._arm_wand()
        self.form.method.setCurrentIndex(1)  # Brightness
        box = self.form.threshold_box()
        self._right_drag((100, 100), (100, 100), start=True)
        self._right_drag((100, 100), (5000, -5000), finish=True)
        self.assertEqual(box.value(), box.maximum())
        self.assertEqual(self.form.grow.value(), self.form.grow.maximum())
        self._right_drag((100, 100), (100, 100), start=True)
        self._right_drag((100, 100), (-5000, 5000), finish=True)
        self.assertEqual(box.value(), box.minimum())
        self.assertEqual(self.form.grow.value(), self.form.grow.minimum())

    def test_a_right_drag_without_the_wand_armed_is_left_alone(self) -> None:
        self.assertIsNone(self.active_tool.active())
        before = self.form.settings()
        self.assertFalse(self._right_drag((100, 100), (180, 70), start=True), "declined: the view box ignores it as before")
        self.assertFalse(self._right_drag((100, 100), (180, 70), finish=True))
        self.assertEqual(self.form.settings(), before)
        self.assertFalse(self.panel._wand_drag.readout.isVisible())

    # -- undo of the selection -------------------------------------------------------------------------------------

    def test_undo_takes_back_each_wand_click_in_turn_and_redo_brings_it_again(self) -> None:
        self._arm_wand()
        self._click(*BRIGHT[:2])
        first = self._selected().copy()
        self._click(*DARK[:2], Qt.KeyboardModifier.ShiftModifier)
        both = self._selected().copy()
        self.assertEqual(undo_manager.undo_label, "Add to selection")
        undo_manager.undo()
        np.testing.assert_array_equal(self._selected(), first)
        undo_manager.undo()
        self.assertFalse(self.area.has_selection())
        undo_manager.redo()
        undo_manager.redo()
        np.testing.assert_array_equal(self._selected(), both)

    def test_a_wand_click_that_ran_into_the_background_is_one_undo_away(self) -> None:
        # The case the removed Max size guard was for: Brightness with a huge tolerance selects (nearly) the whole image.
        self._arm_wand()
        self._click(*BRIGHT[:2])
        small = self._selected().copy()
        self.form.method.setCurrentIndex(1)  # Brightness
        self.form.brightness_tolerance.setValue(100.0)
        self._click(10, 140, Qt.KeyboardModifier.ShiftModifier)
        self.assertGreater(int(self._selected().sum()), 10 * int(small.sum()), "no size limit: the leak is selected")
        undo_manager.undo()
        np.testing.assert_array_equal(self._selected(), small)

    def test_the_selection_and_the_mask_undo_in_the_order_they_were_made(self) -> None:
        actions = self.panel._mask_tab.selection_actions
        self._arm_wand()
        self._click(*BRIGHT[:2])
        actions.add_button.click()
        self.assertIsNotNone(self._mask_now())
        undo_manager.undo()  # the mask edit goes first; the selection stays
        self.assertIsNone(self._mask_now())
        self.assertTrue(self.area.has_selection())
        undo_manager.undo()  # then the selection
        self.assertFalse(self.area.has_selection())

    # -- the Mask tab's Selection group and undo -------------------------------------------------------------------

    def _mask_now(self) -> np.ndarray | None:
        source = self.mask.resolve_mask_source(FRAME)
        return None if source is None else source[1]

    def test_add_selection_puts_it_in_the_mask_and_ctrl_z_takes_it_back(self) -> None:
        actions = self.panel._mask_tab.selection_actions
        self.assertFalse(actions.add_button.isEnabled(), "nothing selected yet")
        self._arm_wand()
        self._click(*BRIGHT[:2])
        self.assertTrue(actions.add_button.isEnabled())
        actions.add_button.click()
        np.testing.assert_array_equal(self._mask_now(), self._selected())  # no rotation: raw == displayed
        self.assertEqual(undo_manager.undo_label, "Add selection to mask")
        undo_manager.undo()
        self.assertIsNone(self._mask_now())
        undo_manager.redo()
        np.testing.assert_array_equal(self._mask_now(), self._selected())

    def test_subtract_selection_takes_it_out_of_the_mask(self) -> None:
        self.mask.set_mask_change(FRAME, "persistent", np.ones(SHAPE, dtype=bool))
        self.area.set_rectangle(10, 20, 30, 40)  # cols 10..29, rows 20..39
        self.panel._mask_tab.selection_actions.subtract_button.click()
        expected = np.ones(SHAPE, dtype=bool)
        expected[20:40, 10:30] = False
        np.testing.assert_array_equal(self._mask_now(), expected)
        undo_manager.undo()
        self.assertTrue(np.all(self._mask_now()))

    def test_an_undo_after_a_non_undoable_change_is_skipped_with_a_message(self) -> None:
        skipped: list[str] = []
        self.mask.undo_skipped.connect(skipped.append)
        self.area.set_rectangle(0, 0, 10, 10)
        self.panel._mask_tab.selection_actions.add_button.click()
        self.mask.set_mask_change(FRAME, "persistent", np.zeros(SHAPE, dtype=bool) | _disk(*DARK))  # e.g. a PNG load
        undo_manager.undo()
        np.testing.assert_array_equal(self._mask_now(), _disk(*DARK))  # the newer mask is kept
        self.assertTrue(skipped and "was not undone" in skipped[-1])


class MaskEditsAreUndoableTest(unittest.TestCase):
    """`MaskModule.apply_candidate` / `apply_morphology` / `paint_brush`: one undo step each (no Qt panel needed)."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.mask = MaskModule()
        self.base = np.zeros((20, 20), dtype=bool)
        self.base[5:10, 5:10] = True
        self.frame = (0, 500.0)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _now(self) -> np.ndarray | None:
        source = self.mask.resolve_mask_source(self.frame)
        return None if source is None else source[1]

    def test_apply_candidate_morphology_and_paint_each_undo_one_step(self) -> None:
        candidate = np.zeros((20, 20), dtype=bool)
        candidate[0, 0] = True
        self.mask.apply_candidate(self.base, candidate, target_frame=self.frame, scope="persistent")
        after_add = self._now().copy()
        self.mask.apply_morphology(after_add, "dilate", 1, target_frame=self.frame, scope="persistent")
        after_dilate = self._now().copy()
        self.mask.paint_brush(after_dilate, (15.0, 15.0), 3.0, target_frame=self.frame, scope="persistent", value=True)
        self.assertGreater(int(self._now().sum()), int(after_dilate.sum()))
        undo_manager.undo()
        np.testing.assert_array_equal(self._now(), after_dilate)
        undo_manager.undo()
        np.testing.assert_array_equal(self._now(), after_add)
        undo_manager.undo()
        self.assertIsNone(self._now())
        self.assertFalse(undo_manager.can_undo)

    def test_an_edit_that_changes_nothing_adds_no_undo_step(self) -> None:
        self.mask.apply_candidate(self.base, self.base, target_frame=self.frame, scope="persistent")
        self.assertTrue(undo_manager.can_undo)
        undo_manager.clear()
        self.mask.apply_candidate(self.base, self.base, target_frame=self.frame, scope="persistent")
        self.assertFalse(undo_manager.can_undo)


if __name__ == "__main__":
    unittest.main()
