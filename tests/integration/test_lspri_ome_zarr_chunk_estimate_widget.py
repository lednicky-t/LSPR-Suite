"""Integration test for the Export section's live chunk-size estimates:
main_window.py's ome_zarr_chunk_estimate_label (per-plane) and
ome_zarr_chunk_total_label (dataset-wide, sitting in its own row directly
under the per-plane one), both next to ome_zarr_chunk_spin - see
apps/LSPRi/eva/docs/bulk_analysis_performance_investigation.md's
"Follow-up #8" for why this exists. Drives the real chunk-size QSpinBox
directly (this repo's own "prefer directly callable widgets" testability
convention) and waits for the real background calibration
(DatasetController._ensure_zarr_read_overhead_calibration, a QThreadPool
FunctionWorker) to complete, the same wait-loop pattern already used by
tests/integration/test_lspri_ome_zarr_export_controller_move.py for the
real threaded export path.

`_current_processed_image` is set directly rather than loading a real
dataset through the full restore flow - the label only ever reads its
shape, and this shortcut matches the existing pattern in
test_lspri_chromatic_seed_preserves_analysis_cache.py /
test_lspri_mask_preview_overlay.py for tests that need *some* displayed
image without exercising the dataset-load pipeline itself. The dataset-wide
total label additionally needs `_state.dataset.records` (for its image
count), so those tests assign a minimal `ImageDataset` directly for the
same reason - no real files, no I/O, since `_sync_ome_zarr_chunk_estimate_
label` only ever reads `len(dataset.records)`.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

import numpy as np
from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402

IMAGE_SIZE = 200


def _fake_dataset(image_count: int) -> ImageDataset:
    records = [ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=i), path=Path("x")) for i in range(image_count)]
    return ImageDataset(folder=Path("unused"), records=records, source_format="ome_zarr")


def _wait_for_calibration(window, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not window._zarr_read_overhead_calibration_attempted and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.05)


class TestOmeZarrChunkEstimateLabel(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MainWindow(REPO_ROOT, fast_startup=True)
        self.window._current_processed_image = np.zeros((IMAGE_SIZE, IMAGE_SIZE), dtype=np.float32)
        # QSettings persists the chunk-size spinner across real app runs on
        # this machine, so it may already equal a value a test below sets it
        # to - setValue() is then a no-op that never emits valueChanged, and
        # the label never gets a chance to sync. Force a known starting value
        # (outside every value used below) so each test's own setValue(...)
        # is guaranteed to be a real change.
        self.window.ome_zarr_chunk_spin.setValue(4)
        _APP.processEvents()

    def tearDown(self) -> None:
        self.window._state.dataset = None
        self.window.close()
        self.window.deleteLater()

    def test_label_is_empty_with_no_image_loaded(self) -> None:
        self.window._current_processed_image = None
        self.window.ome_zarr_chunk_spin.setValue(50)
        _APP.processEvents()
        self.assertEqual(self.window.ome_zarr_chunk_estimate_label.text(), "")
        self.assertEqual(self.window.ome_zarr_chunk_total_label.text(), "")

    def test_total_label_is_empty_with_no_dataset_loaded(self) -> None:
        # An image is displayed (setUp's shortcut) but no dataset is loaded
        # (window._state.dataset stays None) - the per-plane label still
        # populates, but the dataset-wide total has no image count to scale
        # by, so it stays empty rather than showing a total for 0 images.
        self.window.ome_zarr_chunk_spin.setValue(50)
        _APP.processEvents()
        self.assertIn("16 chunks/plane", self.window.ome_zarr_chunk_estimate_label.text())
        self.assertEqual(self.window.ome_zarr_chunk_total_label.text(), "")

    def test_total_label_shows_the_exact_total_scaled_by_image_count(self) -> None:
        self.window._state.dataset = _fake_dataset(5)
        self.window.ome_zarr_chunk_spin.setValue(50)
        _APP.processEvents()
        # 16 chunks/plane (ceil(200/50)^2) x 5 images = 80 chunks total.
        self.assertIn("80 chunks total (5 images)", self.window.ome_zarr_chunk_total_label.text())

        self.window.ome_zarr_chunk_spin.setValue(200)
        _APP.processEvents()
        self.assertIn("5 chunks total (5 images)", self.window.ome_zarr_chunk_total_label.text())

    def test_label_shows_the_exact_chunk_count(self) -> None:
        self.window.ome_zarr_chunk_spin.setValue(50)
        _APP.processEvents()
        # ceil(200/50)^2 = 16 chunks/plane for this synthetic 200x200 image.
        self.assertIn("16 chunks/plane", self.window.ome_zarr_chunk_estimate_label.text())

    def test_label_updates_live_as_the_spinner_changes(self) -> None:
        self.window.ome_zarr_chunk_spin.setValue(50)
        _APP.processEvents()
        self.assertIn("16 chunks/plane", self.window.ome_zarr_chunk_estimate_label.text())

        self.window.ome_zarr_chunk_spin.setValue(200)
        _APP.processEvents()
        self.assertIn("1 chunks/plane", self.window.ome_zarr_chunk_estimate_label.text())

    def test_background_calibration_completes_and_upgrades_the_label_with_a_time_estimate(self) -> None:
        window = self.window
        window._state.dataset = _fake_dataset(5)
        window.ome_zarr_chunk_spin.setValue(50)
        _APP.processEvents()
        # No assertion here about the label lacking a timing estimate yet -
        # calibration runs on a background QThreadPool worker and, once the
        # rest of this test suite has warmed up the process (module imports,
        # OS file cache, thread pool), it can legitimately finish within a
        # couple of processEvents() calls. Nothing in the feature promises
        # calibration takes "long enough" to observe an in-between state, so
        # asserting on that timing was inherently racy (confirmed: this
        # passed in isolation but failed under the full suite, where
        # calibration was fast enough to already be done here).

        _wait_for_calibration(window)
        self.assertTrue(window._zarr_read_overhead_calibration_attempted)
        if window._zarr_read_overhead_calibration is None:
            self.skipTest("calibration failed on this machine (e.g. no zarr support) - nothing to assert")

        # Re-touch the spinner so the now-available calibration gets picked
        # up by the label (matches real usage: the label refreshes on every
        # spinner change).
        window.ome_zarr_chunk_spin.setValue(51)
        window.ome_zarr_chunk_spin.setValue(50)
        _APP.processEvents()
        self.assertIn("ms/plane", window.ome_zarr_chunk_estimate_label.text())
        self.assertIn("s read (estimated)", window.ome_zarr_chunk_total_label.text())


if __name__ == "__main__":
    unittest.main()
