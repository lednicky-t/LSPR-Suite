"""Tests for `RoiGeometrySync` (Geometry -> ROI remap coordinator) and
`RoiToolbox.remap_all`'s end-to-end wiring, plus `DatasetModule.
raw_plane_shape()`, the small query it relies on.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.

The remap *math* is pinned in
`tests/unit/test_lspri_rewrite_roi_geometry_remap.py` (no Qt). What matters
here is the *wiring*: which `GeometryComputationalChange.reason`s trigger a
remap and which don't (especially `"session_restored"`, which must not
double-apply a transform to ROIs that were saved consistent with it
already), that a rotation and the ROI shift it causes land as one undo
step, and that the status message reaches a real listener.
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

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

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
    )
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi_geometry_sync import RoiGeometrySync
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402


def _pump(seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path, *, shape: tuple[int, int] = (64, 80)) -> ImageDataset:
    rng = np.random.default_rng(3)
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=shape).astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class RawPlaneShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name), shape=(64, 80))
        self.dataset = DatasetModule()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_none_before_load_and_after_clear_real_shape_after_load(self) -> None:
        self.assertIsNone(self.dataset.raw_plane_shape())
        self.dataset.load_dataset(self.dataset_model)
        self.assertEqual(self.dataset.raw_plane_shape(), (64, 80))
        self.dataset.clear_dataset()
        self.assertIsNone(self.dataset.raw_plane_shape())


class RoiGeometrySyncTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name), shape=(64, 80))
        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.roi_toolbox = RoiToolbox()
        self.sync = RoiGeometrySync(self.geometry, self.roi_toolbox, self.dataset)
        self.statuses: list[str] = []
        self.sync.status_changed.connect(self.statuses.append)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        undo_manager.clear()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)

    def test_no_dataset_loaded_logs_and_does_not_crash(self) -> None:
        # No dataset ever loaded - raw_shape is None throughout.
        roi_id = self.roi_toolbox.add_roi(10.0, 10.0, sample_radius_px=5.0)
        self.geometry.set_rotation(5.0)
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertEqual((roi.center_x, roi.center_y), (10.0, 10.0))  # unchanged
        self.assertEqual(self.statuses, [])

    def test_rotation_remaps_existing_rois(self) -> None:
        self._load()
        roi_id = self.roi_toolbox.add_roi(10.0, 20.0, sample_radius_px=5.0)
        self.geometry.set_rotation(12.0)
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertNotEqual((roi.center_x, roi.center_y), (10.0, 20.0))
        self.assertEqual(len(self.statuses), 1)
        self.assertIn("1 ROI", self.statuses[0])
        self.assertIn("rotation", self.statuses[0])

    def test_flip_and_crop_also_remap(self) -> None:
        self._load()
        roi_id = self.roi_toolbox.add_roi(10.0, 20.0, sample_radius_px=5.0)
        self.geometry.set_flip(True, False)
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertNotEqual((roi.center_x, roi.center_y), (10.0, 20.0))

        pos_after_flip = (roi.center_x, roi.center_y)
        self.geometry.set_crop(2, 3, 40, 30)
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertNotEqual((roi.center_x, roi.center_y), pos_after_flip)
        self.assertEqual(len(self.statuses), 2)

    def test_image_tools_enabled_and_rotation_fill_do_not_remap(self) -> None:
        self._load()
        roi_id = self.roi_toolbox.add_roi(10.0, 20.0, sample_radius_px=5.0)
        self.geometry.set_image_tools_enabled(False)
        self.geometry.set_rotation_fill_dark(True)
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertEqual((roi.center_x, roi.center_y), (10.0, 20.0))
        self.assertEqual(self.statuses, [])

    def test_session_restore_does_not_remap_but_updates_the_baseline(self) -> None:
        self._load()
        roi_id = self.roi_toolbox.add_roi(10.0, 20.0, sample_radius_px=5.0)

        restored = GeometrySettings(rotation_angle_deg=30.0)
        self.geometry.restore_settings(restored)
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertEqual((roi.center_x, roi.center_y), (10.0, 20.0))  # untouched by the restore itself
        self.assertEqual(self.statuses, [])  # no status noise on a session load

        # A real edit right after the restore must diff against the
        # *restored* 30deg baseline, not whatever came before it.
        self.geometry.set_rotation(33.0)
        from lspr_imaging_app.image_tools.geometry.transform import remap_point_for_geometry_change

        expected = remap_point_for_geometry_change(
            (10.0, 20.0), (64, 80), GeometrySettings(rotation_angle_deg=30.0), GeometrySettings(rotation_angle_deg=33.0)
        )
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertAlmostEqual(roi.center_x, expected[0], places=6)
        self.assertAlmostEqual(roi.center_y, expected[1], places=6)

    def test_loading_a_new_dataset_rebaselines_instead_of_diffing_against_the_old_one(self) -> None:
        self._load()
        self.geometry.set_rotation(10.0)  # this dataset's geometry now sits at 10deg

        second_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            second_dataset = _write_dataset(Path(second_tmp.name), shape=(50, 60))
            self.dataset.load_dataset(second_dataset)  # a fresh GeometryModule state is the app's job, not this test's
            roi_id = self.roi_toolbox.add_roi(5.0, 5.0, sample_radius_px=3.0)
            self.geometry.set_rotation(self.geometry.settings().rotation_angle_deg + 2.0)
            # No crash despite the raw shape having changed underneath - the
            # real assertion is just that this ran at all and remapped
            # against the *current* raw shape (50, 60), not the stale (64, 80).
            roi = self.roi_toolbox.roi_by_id(roi_id)
            self.assertNotEqual((roi.center_x, roi.center_y), (5.0, 5.0))
        finally:
            second_tmp.cleanup()

    def test_mask_shape_lost_is_mentioned_in_the_status_text(self) -> None:
        """Exercises the reporting path via a monkeypatched `RoiToolbox.
        remap_all` result (see the unit test file for why a real geometry
        edit essentially cannot produce a lost mask shape)."""
        from lspr_imaging_app.roi.toolbox import RoiRemapReport

        self._load()
        self.roi_toolbox.add_roi(10.0, 20.0, sample_radius_px=5.0)
        original = self.roi_toolbox.remap_all
        self.roi_toolbox.remap_all = lambda *a, **k: RoiRemapReport((1,), 0, (1,), ())
        try:
            self.geometry.set_rotation(4.0)
        finally:
            self.roi_toolbox.remap_all = original
        self.assertEqual(len(self.statuses), 1)
        self.assertIn("could not be repositioned", self.statuses[0])


class RoiGeometrySyncUndoBatchTest(unittest.TestCase):
    """The real gesture path: `RotateLineTool` -> `GeometryModule.set_rotation`
    -> (synchronously) `RoiGeometrySync` -> `RoiToolbox.remap_all` - one
    undo entry for both."""

    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name), shape=(64, 80))

        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.active_tool = ActiveToolModule()
        self.roi_toolbox = RoiToolbox()
        self.sync = RoiGeometrySync(self.geometry, self.roi_toolbox, self.dataset)
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, MaskModule(), ChromaticModule(), BackgroundModule(),
            self.roi_toolbox, self.selection, self.active_tool,
        )
        self.tool = self.panel._rotate_tool
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        _pump(0.1)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def _click(self, x: float, y: float, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        vb = self.panel._plot.vb
        scene_pos = vb.mapViewToScene(QPointF(x, y))

        class _Click:
            def scenePos(self) -> QPointF:  # noqa: N802
                return scene_pos

            def button(self) -> Qt.MouseButton:
                return button

            def modifiers(self) -> Qt.KeyboardModifier:
                return Qt.KeyboardModifier.NoModifier

        self.panel._on_scene_clicked(_Click())

    def test_rotating_with_an_existing_roi_undoes_both_in_one_step(self) -> None:
        roi_id = self.roi_toolbox.add_roi(20.0, 30.0, sample_radius_px=5.0)
        undo_depth_before = len(undo_manager._undo_stack)

        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)

        self.assertEqual(len(undo_manager._undo_stack), undo_depth_before + 1)  # ONE entry, not two
        rotated_angle = self.geometry.settings().rotation_angle_deg
        self.assertNotEqual(rotated_angle, 0.0)
        roi_after_rotate = self.roi_toolbox.roi_by_id(roi_id)
        self.assertNotEqual((roi_after_rotate.center_x, roi_after_rotate.center_y), (20.0, 30.0))

        undo_manager.undo()
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        roi_after_undo = self.roi_toolbox.roi_by_id(roi_id)
        self.assertEqual((roi_after_undo.center_x, roi_after_undo.center_y), (20.0, 30.0))

        undo_manager.redo()
        self.assertEqual(self.geometry.settings().rotation_angle_deg, rotated_angle)
        roi_after_redo = self.roi_toolbox.roi_by_id(roi_id)
        self.assertEqual(
            (roi_after_redo.center_x, roi_after_redo.center_y),
            (roi_after_rotate.center_x, roi_after_rotate.center_y),
        )

    def test_arrow_key_step_also_batches_the_roi_remap(self) -> None:
        roi_id = self.roi_toolbox.add_roi(20.0, 30.0, sample_radius_px=5.0)
        undo_depth_before = len(undo_manager._undo_stack)

        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.tool.handle_key(Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)

        self.assertEqual(len(undo_manager._undo_stack), undo_depth_before + 1)
        undo_manager.undo()
        roi = self.roi_toolbox.roi_by_id(roi_id)
        self.assertEqual((roi.center_x, roi.center_y), (20.0, 30.0))
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)


if __name__ == "__main__":
    unittest.main()
