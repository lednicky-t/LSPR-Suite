"""Export tab chunk tools (2026-10-20; the Workflow Export section until it moved to the Image panel ribbon): chunk W x H, warnings, grid position aligned to the ROIs (lossless pad /
lossy trim), the Find best popup, the grid preview and what Export passes to the module.

Real `DatasetModule` (a tiny TIFF dataset), `RoiToolbox`, `GeometryModule`, `ChromaticModule`; a real in-process
`QApplication`, never `.exec()`. The file dialog and the export itself are replaced (no files written by the export).
Also the raw <-> processed map the export layout relies on (`raw_to_processed_affine_xy`).

Rewrite-only; skipped when the `apps/LSPRi/eva` submodule is not on its `rewrite` branch.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtWidgets import QApplication, QFileDialog

    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset import chunk_plan as cp
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord  # the rewrite's own model
    from lspr_imaging_app.image_tools import ChromaticModule, GeometryModule
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import raw_to_processed_affine_xy, spatial_output_shape
    from lspr_imaging_app.panels.image.export_tab import GRID_CORNER, GRID_LOSSLESS, GRID_LOSSY, ExportTab
    from lspr_imaging_app.panels.image.export_roi_layout import gather_export_roi_layout
    from lspr_imaging_app.roi import RoiToolbox
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])
H, W = 200, 300
PITCH = 40.0


def _pump(until=lambda: False, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


class RawProcessedMapTests(unittest.TestCase):
    def test_identity_when_image_tools_are_off_and_crop_shift_when_on(self):
        settings = GeometrySettings(image_tools_enabled=False, crop=CropDefinition(10, 5, 100, 80, True))
        np.testing.assert_allclose(raw_to_processed_affine_xy((H, W), settings), np.eye(2, 3))
        settings = GeometrySettings(image_tools_enabled=True, crop=CropDefinition(10, 5, 100, 80, True))
        np.testing.assert_allclose(raw_to_processed_affine_xy((H, W), settings) @ [15.0, 7.0, 1.0], [5.0, 2.0], atol=1e-9)


class ExportChunkToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        folder = Path(self._tmp.name)
        records = []
        rng = np.random.default_rng(0)
        for cube in range(2):
            for wl in (500.0, 600.0):
                path = folder / f"c{cube}_wl{wl:.0f}.tif"
                tifffile.imwrite(str(path), (1500 + rng.normal(0, 25, (H, W))).astype(np.uint16))
                records.append(ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=cube), path=path))
        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.chromatic = ChromaticModule()
        self.toolbox = RoiToolbox()
        for r in range(3):  # a 3 x 5 lattice, pitch 40 px, starting at (37, 33)
            for c in range(5):
                self.toolbox.add_roi(37.0 + c * PITCH, 33.0 + r * PITCH, sample_diameter_px=20.0)
        self.section = ExportTab(self.dataset, None, geometry=self.geometry, roi_toolbox=self.toolbox,
                                            chromatic=self.chromatic)
        self.addCleanup(self.section.deleteLater)
        self.dataset.load_dataset(ImageDataset(folder=folder, records=records))
        _APP.processEvents()

    def set_size(self, w: int, h: int) -> None:
        self.section._chunk_spin.setValue(w)
        self.section._chunk_h_spin.setValue(h)

    def set_mode(self, mode: str) -> None:
        self.section._grid_combo.setCurrentIndex(self.section._grid_combo.findData(mode))

    def test_live_warnings(self) -> None:
        self.set_size(79, 79)
        self.assertIn("not a multiple of 8", self.section._warning_text)
        self.assertIn("Small chunks", self.section._warning_text)
        self.set_size(80, 200)
        self.assertEqual(self.section._warning_text, "")

    def test_corner_mode_counts_whole_image(self) -> None:
        self.set_size(64, 64)
        self.set_mode(GRID_CORNER)
        self.assertIn("-> 20 chunks/plane", self.section._estimate_text)  # ceil(300/64) x ceil(200/64)
        self.assertIsNone(self.section._current_frame())

    def test_aligned_modes_put_each_roi_in_one_chunk(self) -> None:
        self.set_size(40, 40)
        self.set_mode(GRID_LOSSLESS)
        layout, roi_layout = self.section.current_layout()
        self.assertEqual(cp.score_layout(roi_layout.boxes, layout)["single"].chunks, 1.0)
        frame = self.section._current_frame()
        self.assertTrue(frame.lossless)
        self.assertLessEqual(frame.origin_x, 0)
        self.assertEqual(frame.discarded_px, 0)
        self.assertIn("pads", self.section._layout_text)
        self.set_mode(GRID_LOSSY)
        frame = self.section._current_frame()
        self.assertFalse(frame.lossless)
        self.assertGreater(frame.discarded_px, 0)
        self.assertIn("not exported", self.section._layout_text)

    def test_preview_follows_the_aligned_grid(self) -> None:
        self.set_size(40, 40)
        self.set_mode(GRID_LOSSLESS)
        self.section._chunk_grid_button.setChecked(True)
        layout, _ = self.section.current_layout()
        preview = self.dataset.chunk_grid_preview()
        self.assertTrue(preview.enabled)
        self.assertEqual((preview.chunk_w, preview.chunk_h, preview.offset_x, preview.offset_y),
                         (40, 40, layout.offset_x, layout.offset_y))

    def test_roi_move_realigns(self) -> None:
        self.set_size(40, 40)
        self.set_mode(GRID_LOSSLESS)
        before, _ = self.section.current_layout()
        for roi in self.toolbox.rois():
            self.toolbox.move_roi(roi.area_roi_id, roi.center_x + 13.0, roi.center_y)
        after, _ = self.section.current_layout()
        self.assertEqual((after.offset_x - before.offset_x) % 40, 13)

    def test_find_best_applies_a_suggestion(self) -> None:
        self.set_size(64, 64)
        self.section._find_best_button.click()
        dialog = self.section._dialog
        self.assertIsNotNone(dialog)
        self.assertTrue(_pump(lambda: dialog._table.rowCount() > 0), dialog._status.text())
        self.assertEqual(dialog._table.item(0, 0).text(), "★")  # recommended first
        first = dialog._rows[0][0]
        self.assertEqual(first.key, "columns")
        self.assertRegex(dialog._table.item(0, 6).text(), r"^\d+(\.\d)? (kB|MB|GB)$")  # lossless size
        self.assertIn("% px)", dialog._table.item(0, 7).text())  # lossy size and how much it drops
        dialog._lossy_radio.setChecked(True)
        dialog._apply_button.click()
        self.assertEqual((self.section._chunk_spin.value(), self.section._chunk_h_spin.value()),
                         (first.layout.chunk_w, first.layout.chunk_h))
        self.assertEqual(self.section._grid_combo.currentData(), GRID_LOSSY)
        self.assertEqual(self.section.current_layout()[0], first.layout)

    def test_export_passes_chunk_shape_and_frame(self) -> None:
        self.set_size(40, 48)
        self.set_mode(GRID_LOSSLESS)
        calls = []
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr", side_effect=lambda d, **kw: calls.append((d, kw))):
            self.section._export_button.click()
        self.assertEqual(len(calls), 1)
        destination, kwargs = calls[0]
        frame = self.section._current_frame()
        self.assertEqual(kwargs["chunk_shape"], (48, 40))
        self.assertEqual(kwargs["frame"], (frame.origin_x, frame.origin_y, frame.width, frame.height))
        self.assertIn("_c40x48", destination.name)
        self.set_mode(GRID_CORNER)
        calls.clear()
        self.section._set_exporting(False)  # the mocked export never finishes, so re-enable the button
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr", side_effect=lambda d, **kw: calls.append((d, kw))):
            self.section._export_button.click()
        self.assertIsNone(calls[0][1]["frame"])

    def test_export_button_turns_into_cancel_while_running(self) -> None:
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr"):
            self.section._export_button.click()
        self.assertEqual(self.section._export_caption.text(), "Cancel")
        with mock.patch.object(self.dataset, "cancel_export") as cancel:
            self.section._export_button.click()
        cancel.assert_called_once()
        self.dataset.export_failed.emit("OME-Zarr export cancelled.")
        self.assertEqual(self.section._export_caption.text(), "Export")

    def test_warning_and_counts_reach_the_fields_tooltip(self) -> None:
        self.set_size(79, 79)
        self.assertIn("not a multiple of 8", self.section._chunk_spin.toolTip())
        self.assertIn("color:", self.section._chunk_spin.styleSheet())
        self.set_size(80, 200)
        self.assertNotIn("color:", self.section._chunk_spin.styleSheet())
        self.assertIn("chunks/plane", self.section.info_html())

    def test_real_export_reports_to_the_task_indicator_signals(self) -> None:
        progress, finished = [], []
        self.dataset.task_progress.connect(lambda *args: progress.append(args))
        self.dataset.task_finished.connect(lambda *args: finished.append(args))
        self.dataset.export_to_ome_zarr(Path(self._tmp.name) / "out.zarr", chunk_size_px=64)
        self.assertTrue(_pump(lambda: bool(finished)))
        self.assertTrue(progress)
        self.assertTrue(all(0.0 <= fraction <= 1.0 for _id, _label, fraction, _msg in progress))
        self.assertEqual(finished[0][:2], ("zarr_export", "completed"), finished[0])

    def test_rotate_crop_toggle_switches_the_export_space(self) -> None:
        self.geometry.set_rotation(10.0)
        self.set_size(40, 40)
        self.section._chunk_grid_button.setChecked(True)
        rotated_shape = tuple(spatial_output_shape((H, W), self.geometry.settings()))
        # On (the default): the export stores the processed image, and the grid is drawn straight on it.
        self.assertTrue(self.section._apply_tools_button.isChecked())
        self.assertEqual(self.section._export_shape(), rotated_shape)
        self.assertTrue(self.dataset.chunk_grid_preview().processed)
        calls = []
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr", side_effect=lambda d, **kw: calls.append((d, kw))):
            self.section._export_button.click()
        self.assertEqual(calls[0][1]["preprocessing"].rotation_angle_deg, 10.0)
        self.assertIn(f"_{rotated_shape[1]}x{rotated_shape[0]}_", calls[0][0].name)
        # Off: raw pixels, the grid in raw pixels.
        self.section._set_exporting(False)
        self.section._apply_tools_button.click()
        self.assertEqual(self.section._export_shape(), (H, W))
        self.assertFalse(self.dataset.chunk_grid_preview().processed)
        calls.clear()
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr", side_effect=lambda d, **kw: calls.append((d, kw))):
            self.section._export_button.click()
        self.assertIsNone(calls[0][1]["preprocessing"])

    def test_stored_dtype_comes_from_the_files_not_the_float32_display_plane(self) -> None:
        self.assertEqual(self.section._image_dtype, np.uint16)
        self.assertIn("_u16", self.section._build_export_folder_name(Path(self._tmp.name)))

    def test_saved_square_size_restores_as_width_and_height(self) -> None:
        from lspr_imaging_app.panels.ui_state import UiStateStore

        old = ExportTab(self.dataset, None, ui_state=UiStateStore({"export/chunk_size": 128}))
        self.addCleanup(old.deleteLater)
        self.assertEqual((old._chunk_spin.value(), old._chunk_h_spin.value()), (128, 128))
        new = ExportTab(self.dataset, None, ui_state=UiStateStore({"export/chunk_size": 80, "export/chunk_h": 600,
                                                                              "export/grid_position": GRID_LOSSY}))
        self.addCleanup(new.deleteLater)
        self.assertEqual((new._chunk_spin.value(), new._chunk_h_spin.value()), (80, 600))
        self.assertEqual(new._grid_combo.currentData(), GRID_LOSSY)

    def test_layout_maps_through_the_image_tools(self) -> None:
        raw = gather_export_roi_layout(self.toolbox, self.geometry, self.chromatic, (H, W))
        settings = GeometrySettings(image_tools_enabled=True, crop=CropDefinition(10, 5, 250, 180, True))
        cropped = mock.Mock(settings=lambda: settings)
        shifted = gather_export_roi_layout(self.toolbox, cropped, self.chromatic, (H, W))
        # ROIs are in processed (cropped) space: in raw pixels they sit 10 px right and 5 px down.
        np.testing.assert_array_equal(shifted.boxes, np.minimum(raw.boxes + [10, 5, 10, 5], [W, H, W, H]))


if __name__ == "__main__":
    unittest.main()
