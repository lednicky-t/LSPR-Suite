"""The Image panel's layers (2026-11-16): Image / ROI / Ignore mask, the Layers button, and the wand on the active layer.

A real `ImagePanel` on a one-frame dataset (a bright disk on a noisy background), one ROI and a mask of two blobs.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

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
    from lspr_imaging_app.image_tools.mask.raster_tools import connected_grain
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.layer_wand import mask_blob
    from lspr_imaging_app.panels.image.layers import IMAGE, LAYERS, MASK, ROI
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.rasterize import effective_reference_diameters
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
    from lspr_imaging_app.storage.ui_state_keys import IMAGE_ACTIVE_LAYER, IMAGE_LAYER_IMAGE_VISIBLE
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

FRAME = (0, 500.0)
SHAPE = (120, 160)
BRIGHT = (40, 50, 8.0)  # row, col, radius
ROI_CENTRE = (90.0, 40.0)  # x, y of the ROI (display space = processed space here: no rotation, no chromatic model)
BLOB_A = (slice(10, 20), slice(120, 135))
BLOB_B = (slice(60, 70), slice(120, 135))


def _disk(row: float, col: float, radius: float) -> np.ndarray:
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
    def __init__(self, scene_pos: QPointF) -> None:
        self._pos = scene_pos

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


class _Store:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


def _write_dataset(root: Path) -> ImageDataset:
    image = np.random.default_rng(4).normal(1000.0, 15.0, SHAPE)
    image[_disk(*BRIGHT)] += 600.0
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), image.astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class MaskBlobTest(unittest.TestCase):
    def test_the_flood_fill_matches_connected_grain(self) -> None:
        rng = np.random.default_rng(7)
        mask = rng.random((80, 90)) > 0.55
        for row, col in ((0, 0), (40, 45), (79, 89), (10, 60), (55, 3)):
            expected = connected_grain(mask, row, col)
            got = mask_blob(mask, row, col)
            if expected is None:
                self.assertIsNone(got)
            else:
                self.assertTrue(np.array_equal(got, expected), (row, col))
        self.assertIsNone(mask_blob(mask, -1, 5))


class ImageLayersPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.area = AreaSelectionModule()
        self.active_tool = ActiveToolModule()
        self.toolbox = RoiToolbox()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), self.toolbox,
            SelectionModule(), self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.panel.resize(1100, 800)
        self.panel.show()
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.panel._selection_tools.wand._image is not None and self.panel._image_item.levels is not None)
        self.roi_id = self.toolbox.add_roi(*ROI_CENTRE, sample_diameter_px=16.0)
        blobs = np.zeros(SHAPE, dtype=bool)
        blobs[BLOB_A] = True
        blobs[BLOB_B] = True
        self.blobs = blobs
        self.mask.set_mask_change(FRAME, "persistent", blobs)
        _pump_until(lambda: self.panel._tints.displayed_mask() is not None)
        _pump()
        self.layers = self.panel.layers
        self.toolbar = self.panel._selection_tools.toolbar
        self.button = self.panel._selection_tools.layers_button

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        undo_manager.clear()
        self._tmp.cleanup()

    def _wand_click(self, row: float, col: float) -> None:
        if self.active_tool.active() is not ImageTool.SELECT_AREA:
            self.toolbar.tool_button(AreaSelectionMode.WAND).click()
        scene = self.panel._plot.vb.mapViewToScene(QPointF(col + 0.5, row + 0.5))
        self.panel._on_scene_clicked(_Click(scene))
        self.assertTrue(_pump_until(lambda: not self.panel._selection_tools.wand.is_busy()))
        _pump(0.05)

    def _wheel(self, delta_y: int) -> None:
        point = QPointF(5.0, 5.0)
        event = QWheelEvent(point, point, QPoint(0, 0), QPoint(0, delta_y), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(self.button, event)

    # -- layers ------------------------------------------------------------------------------------------------------

    def test_three_layers_image_active_and_shown_by_default(self) -> None:
        self.assertEqual(LAYERS, (MASK, ROI, IMAGE))
        self.assertEqual(self.layers.active(), IMAGE)
        self.assertTrue(all(self.layers.is_visible(layer) for layer in LAYERS))

    def test_the_mask_tint_is_drawn_above_the_roi_circles(self) -> None:
        roi_layer = self.panel._roi_overlay.layers.data
        self.assertGreater(self.panel._mask_tint.item.zValue(), roi_layer.zValue())
        self.assertGreater(self.panel._lock_layer.item.zValue(), self.panel._mask_tint.item.zValue())

    def test_hiding_the_image_layer_hides_the_frame_and_shows_the_cover(self) -> None:
        self.button.form.eye_buttons[IMAGE].click()
        self.assertFalse(self.layers.is_visible(IMAGE))
        self.assertEqual(self.panel._image_item.opacity(), 0.0)
        self.assertTrue(self.panel._image_cover.item.isVisible())
        rect = self.panel._image_cover.item.mapRectToParent(self.panel._image_cover.item.boundingRect())
        self.assertAlmostEqual(rect.width(), SHAPE[1])
        self.assertAlmostEqual(rect.height(), SHAPE[0])
        self.button.form.eye_buttons[IMAGE].click()
        self.assertEqual(self.panel._image_item.opacity(), 1.0)
        self.assertFalse(self.panel._image_cover.item.isVisible())

    def test_mask_and_roi_eyes_are_the_existing_toggles(self) -> None:
        self.button.form.eye_buttons[MASK].click()
        self.assertFalse(self.panel._mask_tint.visible)
        self.assertFalse(self.panel._mask_tab.overlay_controls._toggle_button.isChecked())
        self.panel._mask_tab.overlay_controls.set_visible(True)  # switched on the Mask tab: the pop-up follows
        self.assertTrue(self.button.form.eye_buttons[MASK].isChecked())
        self.button.form.eye_buttons[ROI].click()
        self.assertFalse(self.panel._roi_display.sample.visible)
        self.assertFalse(self.panel._roi_display.reference.visible)
        self.assertFalse(self.layers.is_visible(ROI))

    def test_wheel_and_names_change_the_active_layer(self) -> None:
        self._wheel(-120)  # one notch down: the next layer in the list wraps from Image to Ignore mask
        self.assertEqual(self.layers.active(), MASK)
        self._wheel(120)
        self.assertEqual(self.layers.active(), IMAGE)
        self.button.form.name_buttons[ROI].click()
        self.assertEqual(self.layers.active(), ROI)
        self.assertTrue(self.button.form.name_buttons[ROI].isChecked())
        self.assertFalse(self.button.form.name_buttons[IMAGE].isChecked())

    def test_hiding_a_layer_never_changes_the_active_one(self) -> None:
        self.layers.set_active(MASK)
        self.button.form.eye_buttons[MASK].click()
        self.assertEqual(self.layers.active(), MASK)

    # -- the wand per layer ---------------------------------------------------------------------------------------------

    def test_the_wand_on_the_mask_layer_picks_the_blob_under_the_click(self) -> None:
        self.layers.set_active(MASK)
        self._wand_click(15, 125)
        selected = self.area.mask(SHAPE)
        expected = np.zeros(SHAPE, dtype=bool)
        expected[BLOB_A] = True
        self.assertTrue(np.array_equal(selected, expected))
        self.assertIn("mask blob", self.messages[-1])

    def test_the_wand_on_the_mask_layer_off_the_mask_selects_nothing(self) -> None:
        self.layers.set_active(MASK)
        self._wand_click(*BRIGHT[:2])
        self.assertFalse(self.area.has_selection())
        self.assertIn("no mask pixel", self.messages[-1])

    def test_the_wand_on_the_roi_layer_picks_the_disk_or_the_ring(self) -> None:
        self.layers.set_active(ROI)
        x, y = ROI_CENTRE
        self._wand_click(y, x)
        disk = self.area.mask(SHAPE).copy()
        self.assertTrue(disk[int(y), int(x)])
        self.assertIn("disk", self.messages[-1])
        self.assertLess(int(disk.sum()), int(np.pi * 9.0**2) + 1)
        roi = next(r for r in self.toolbox.rois() if r.area_roi_id == self.roi_id)
        detection = self.toolbox.detection_settings()
        inner, outer = effective_reference_diameters(roi, detection.default_border())
        self.assertGreater(outer, inner, "the ROI has a reference ring")
        middle = (inner + outer) / 4.0  # halfway across the ring, as a radius
        self.area.clear()
        self._wand_click(y, x + middle)
        ring = self.area.mask(SHAPE)
        self.assertFalse(ring[int(y), int(x)], "the ring does not cover the disk")
        self.assertTrue(ring[int(y), int(x + middle)])
        self.assertIn("rim", self.messages[-1])
        # With the rings hidden, the same click finds nothing.
        self.panel._roi_tab.reference_controls.set_visible(False)
        self.area.clear()
        self._wand_click(y, x + middle)
        self.assertFalse(self.area.has_selection())

    def test_a_hidden_active_layer_is_never_read(self) -> None:
        self.layers.set_active(MASK)
        self.layers.set_visible(MASK, False)
        self._wand_click(15, 125)
        self.assertFalse(self.area.has_selection())
        self.assertIn("hidden", self.messages[-1])

    def test_the_image_layer_wand_still_selects_the_feature(self) -> None:
        self._wand_click(*BRIGHT[:2])
        self.assertTrue(self.area.mask(SHAPE)[BRIGHT[0], BRIGHT[1]])

    # -- select by value ------------------------------------------------------------------------------------------------

    def test_value_tools_work_only_with_the_image_layer_active(self) -> None:
        threshold = self.toolbar.tool_button(AreaSelectionMode.THRESHOLD)
        threshold.click()
        self.assertIs(self.active_tool.active(), ImageTool.SELECT_AREA)
        self.layers.set_active(ROI)
        self.assertFalse(threshold.isEnabled())
        self.assertIsNone(self.active_tool.active(), "the armed value tool was put down")
        self.assertIn("Image layer only", self.messages[-1])
        self.assertTrue(self.toolbar.tool_button(AreaSelectionMode.RECTANGLE).isEnabled())
        self.layers.set_active(IMAGE)
        self.assertTrue(threshold.isEnabled())

    # -- remembered -----------------------------------------------------------------------------------------------------

    def test_the_image_eye_and_the_active_layer_are_remembered(self) -> None:
        store = _Store({IMAGE_ACTIVE_LAYER.key: ROI, IMAGE_LAYER_IMAGE_VISIBLE.key: False})
        self.button.bind_ui_state(store)
        self.assertEqual(self.layers.active(), ROI)
        self.assertFalse(self.layers.is_visible(IMAGE))
        self.assertEqual(self.panel._image_item.opacity(), 0.0)
        self.layers.set_active(MASK)
        self.layers.set_visible(IMAGE, True)
        self.assertEqual(store.values[IMAGE_ACTIVE_LAYER.key], MASK)
        self.assertIs(store.values[IMAGE_LAYER_IMAGE_VISIBLE.key], True)


if __name__ == "__main__":
    unittest.main()
