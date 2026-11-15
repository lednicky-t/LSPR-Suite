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
