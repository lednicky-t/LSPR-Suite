"""Export tab chunk tools (2026-10-20; the Workflow Export section until it moved to the Image panel ribbon): chunk W x H, warnings, grid position aligned to the ROIs (pads the edge),
the chunk-style menu, the grid preview and what Export passes to the module.

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
    from lspr_imaging_app.panels.image.export_tab import ExportTab
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

    def test_live_warnings(self) -> None:
        self.set_size(79, 79)
        self.assertIn("not a multiple of 8", self.section._warning_text)
        self.assertIn("Small chunks", self.section._warning_text)
        self.set_size(80, 200)
        self.assertEqual(self.section._warning_text, "")

    def test_without_rois_the_grid_starts_at_the_corner_and_counts_the_whole_image(self) -> None:
        self.toolbox.delete_rois(tuple(roi.area_roi_id for roi in self.toolbox.rois()))
        self.set_size(64, 64)
        self.assertTrue(_pump(lambda: "-> 20 chunks/plane" in self.section._estimate_text, timeout=3.0))  # debounced
        self.assertIn("-> 20 chunks/plane", self.section._estimate_text)  # ceil(300/64) x ceil(200/64)
        self.assertIsNone(self.section._current_frame())

    def test_aligned_mode_puts_each_roi_in_one_chunk(self) -> None:
        self.set_size(40, 40)
        layout, roi_layout = self.section.current_layout()
        self.assertEqual(cp.score_layout(roi_layout.boxes, layout)["single"].chunks, 1.0)
        frame = self.section._current_frame()
        self.assertTrue(frame.lossless)
        self.assertLessEqual(frame.origin_x, 0)
        self.assertEqual(frame.discarded_px, 0)
        self.assertIn("pads", self.section._layout_text)

    def test_preview_follows_the_aligned_grid(self) -> None:
        self.set_size(40, 40)
        self.section._chunk_grid_button.setChecked(True)
        layout, _ = self.section.current_layout()
        preview = self.dataset.chunk_grid_preview()
        self.assertTrue(preview.enabled)
        self.assertEqual((preview.chunk_w, preview.chunk_h, preview.offset_x, preview.offset_y),
                         (40, 40, layout.offset_x, layout.offset_y))

    def test_roi_move_realigns(self) -> None:
        self.set_size(40, 40)
        before, _ = self.section.current_layout()
        for roi in self.toolbox.rois():
            self.toolbox.move_roi(roi.area_roi_id, roi.center_x + 13.0, roi.center_y)
        after, _ = self.section.current_layout()
        self.assertEqual((after.offset_x - before.offset_x) % 40, 13)

    def pick_style(self, key: str) -> None:
        self.section._find_best_button._actions[key].trigger()

    def test_chunk_style_menu_lists_the_three_styles(self) -> None:
        self.assertEqual(list(self.section._find_best_button._actions), ["per_roi", "columns", "rows"])

    def test_chunk_style_columns_applies_at_once(self) -> None:
        self.set_size(64, 64)
        self.pick_style("columns")
        layout, roi_layout = self.section.current_layout()
        self.assertEqual((layout.chunk_w, layout.chunk_h), (40, H))  # one pitch wide, the whole height
        self.assertEqual(cp.score_layout(roi_layout.boxes, layout)["single"].chunks, 1.0)

    def test_chunk_style_rows_and_one_roi(self) -> None:
        self.pick_style("rows")
        self.assertEqual((self.section._chunk_spin.value(), self.section._chunk_h_spin.value()), (W, 40))
        self.pick_style("per_roi")
        self.assertEqual((self.section._chunk_spin.value(), self.section._chunk_h_spin.value()), (40, 40))

    def test_chunk_style_without_rois_reports_and_changes_nothing(self) -> None:
        reports = []
        self.section._status = reports.append
        self.toolbox.delete_rois(tuple(roi.area_roi_id for roi in self.toolbox.rois()))
        self.set_size(64, 64)
        self.pick_style("columns")
        self.assertEqual((self.section._chunk_spin.value(), self.section._chunk_h_spin.value()), (64, 64))
        self.assertTrue(any("place ROIs first" in r for r in reports))

    def test_export_passes_chunk_shape_and_frame(self) -> None:
        self.set_size(40, 48)
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
        self.toolbox.delete_rois(tuple(roi.area_roi_id for roi in self.toolbox.rois()))  # no ROIs: no frame
        calls.clear()
        self.section._set_exporting(False)  # the mocked export never finishes, so re-enable the button
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr", side_effect=lambda d, **kw: calls.append((d, kw))):
            self.section._export_button.click()
        self.assertIsNone(calls[0][1]["frame"])

    # -- the session goes with the export (2026-10-22, storage/session_carry.py) --------------------------------

    class _CarryRecorder:
        def __init__(self, busy: bool = False) -> None:
            self.prepared, self.discarded, self._busy = [], 0, busy

        def prepare(self, transform) -> None:
            if self._busy:
                raise RuntimeError("The previous session carry is still running.")
            self.prepared.append(transform)

        def discard(self) -> None:
            self.discarded += 1

    def _click_export(self, **mock_kwargs) -> list:
        calls = []
        mock_kwargs.setdefault("side_effect", lambda d, **kw: calls.append((d, kw)))
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr", **mock_kwargs):
            self.section._export_button.click()
        return calls

    def test_export_asks_the_session_carry_with_the_exports_own_transform(self) -> None:
        self.geometry.set_rotation(1.4)
        recorder = self.section._session_carry = self._CarryRecorder()
        calls = self._click_export()
        self.assertEqual(len(recorder.prepared), 1)
        transform = recorder.prepared[0]
        self.assertEqual(transform.raw_shape, (H, W))
        self.assertEqual(transform.geometry.rotation_angle_deg, 1.4)
        self.assertEqual(transform.frame, calls[0][1]["frame"])  # the very frame the export is given
        self.assertTrue(self.section._carry_button.isChecked())  # on by default

    def test_session_switched_off_prepares_nothing(self) -> None:
        recorder = self.section._session_carry = self._CarryRecorder()
        self.section._carry_button.setChecked(False)
        calls = self._click_export()
        self.assertEqual((len(calls), recorder.prepared), (1, []))

    def test_a_refused_export_drops_the_prepared_session(self) -> None:
        recorder = self.section._session_carry = self._CarryRecorder()
        self._click_export(side_effect=RuntimeError("An export is already running."))
        self.assertEqual((len(recorder.prepared), recorder.discarded), (1, 1))

    def test_a_carry_still_writing_does_not_stop_the_export(self) -> None:
        recorder = self.section._session_carry = self._CarryRecorder(busy=True)
        reports = []
        self.section._status = reports.append
        calls = self._click_export()
        self.assertEqual(len(calls), 1)
        self.assertEqual(recorder.discarded, 0)
        self.assertTrue(any("Session not carried" in r for r in reports))

    def test_the_session_button_is_off_the_table_without_a_carry_task(self) -> None:
        self.assertFalse(self.section._carry_button.isEnabled())  # this tab was built without one

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

    def test_the_export_reports_what_it_did_before_it_reports_finished(self) -> None:
        events, finished = [], []
        self.dataset.export_stats_ready.connect(lambda stats: events.append(("stats", stats)))
        self.dataset.export_finished.connect(lambda path: events.append(("finished", path)))
        self.dataset.task_finished.connect(lambda *args: finished.append(args))
        self.dataset.export_to_ome_zarr(Path(self._tmp.name) / "stats.zarr", chunk_size_px=64)
        self.assertTrue(_pump(lambda: len(events) == 2 and bool(finished)))
        self.assertEqual([name for name, _ in events], ["stats", "finished"])  # the numbers first
        stats = events[0][1]
        self.assertEqual(stats.planes, 4)  # 2 cubes x 2 wavelengths
        self.assertEqual(stats.stored_plane_bytes, H * W * 2)  # uint16
        self.assertGreater(stats.bytes_written, 0)
        self.assertGreater(stats.seconds, 0.0)
        self.assertGreaterEqual(stats.workers, 1)
        self.assertEqual(stats.uncompressed_bytes, 4 * H * W * 2)
        self.assertIn("Export took", stats.describe())
        self.assertIn("images/s", finished[0][2])  # the status bar's message carries the time and the rate

    def test_a_skipped_plane_is_not_counted_as_written(self) -> None:
        from lspr_imaging_app.domain.exclusions import ImageExclusionRule

        stats, done = [], []
        self.dataset.export_stats_ready.connect(stats.append)
        self.dataset.export_finished.connect(done.append)
        first_cube = self.dataset.spectral_cubes()[0]
        self.dataset.export_to_ome_zarr(
            Path(self._tmp.name) / "skip.zarr", chunk_size_px=64, skip_excluded=True,
            excluded_rules=[ImageExclusionRule(spectral_cube_index=first_cube, wavelength_nm=None)],  # a whole cube: 2 of the 4 planes
        )
        self.assertTrue(_pump(lambda: bool(done)))
        self.assertEqual(stats[0].planes, 2)
        self.assertEqual(stats[0].uncompressed_bytes, 2 * H * W * 2)

    def test_rotate_crop_is_always_applied_while_the_image_tools_are_on(self) -> None:
        self.geometry.set_rotation(10.0)
        self.set_size(40, 40)
        self.section._chunk_grid_button.setChecked(True)
        rotated_shape = tuple(spatial_output_shape((H, W), self.geometry.settings()))
        # The export stores the processed image, and the grid is drawn straight on it (no on/off icon any more).
        self.assertFalse(hasattr(self.section, "_apply_tools_button"))
        self.assertEqual(self.section._export_shape(), rotated_shape)
        self.assertTrue(self.dataset.chunk_grid_preview().processed)
        calls = []
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self._tmp.name), \
                mock.patch.object(self.dataset, "export_to_ome_zarr", side_effect=lambda d, **kw: calls.append((d, kw))):
            self.section._export_button.click()
        self.assertEqual(calls[0][1]["preprocessing"].rotation_angle_deg, 10.0)
        frame = self.section._current_frame()  # the stored window: the rotated image plus the grid padding
        self.assertGreaterEqual(frame.width, rotated_shape[1])
        self.assertIn(f"_{frame.width}x{frame.height}_", calls[0][0].name)
        # Only with the Image tools switched off: raw pixels, the grid in raw pixels.
        self.section._set_exporting(False)
        self.geometry.set_image_tools_enabled(False)
        self.assertEqual(self.section._export_shape(), (H, W))
        self.assertTrue(_pump(lambda: not self.dataset.chunk_grid_preview().processed, timeout=3.0))  # debounced
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
        new = ExportTab(self.dataset, None, ui_state=UiStateStore({"export/chunk_size": 80, "export/chunk_h": 600}))
        self.addCleanup(new.deleteLater)
        self.assertEqual((new._chunk_spin.value(), new._chunk_h_spin.value()), (80, 600))

    def test_layout_maps_through_the_image_tools(self) -> None:
        raw = gather_export_roi_layout(self.toolbox, self.geometry, self.chromatic, (H, W))
        settings = GeometrySettings(image_tools_enabled=True, crop=CropDefinition(10, 5, 250, 180, True))
        cropped = mock.Mock(settings=lambda: settings)
        shifted = gather_export_roi_layout(self.toolbox, cropped, self.chromatic, (H, W))
        # ROIs are in processed (cropped) space: in raw pixels they sit 10 px right and 5 px down.
        np.testing.assert_array_equal(shifted.boxes, np.minimum(raw.boxes + [10, 5, 10, 5], [W, H, W, H]))


if __name__ == "__main__":
    unittest.main()
