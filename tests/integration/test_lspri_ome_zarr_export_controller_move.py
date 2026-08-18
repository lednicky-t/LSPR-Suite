"""Regression test for moving the OME-Zarr export subsystem (worker/thread
launch, progress/completion handling, ~350 lines) out of the MainWindowIcons
mixin (main_window_icons.py, which should hold icon factories only) into
DatasetController, where it naturally continues the export flow
DatasetController already owns (destination picking, collision resolution).

The move required rewriting every `self.<attr>` reference in that code to
either stay `self.<attr>` (calls between the moved methods, now siblings on
the same controller) or become `self.window.<attr>` (real MainWindow state/
widgets/other methods) - ~30 methods, ~80 references. This test exercises
the real, full, threaded export path end-to-end (not just a direct call into
io.dataset.export_ome_zarr_dataset, which the adaptive-export tests already
cover) specifically to catch a botched self/self.window substitution
anywhere in that rewrite, including in the queued-signal completion
handlers that only run once the background thread finishes.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import tifffile
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
from lspr_imaging_app.io.dataset import load_ome_zarr_dataset  # noqa: E402

IMAGE_SIZE = 16
WAVELENGTHS_NM = [450.0, 500.0]


def _make_dataset(folder: Path) -> tuple[ImageDataset, dict]:
    records: list[ImageRecord] = []
    known: dict[tuple[int, float], np.ndarray] = {}
    for wavelength in WAVELENGTHS_NM:
        rng = np.random.default_rng(int(wavelength))
        image = rng.integers(0, 4096, size=(IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint16)
        path = folder / f"cube0_wl{wavelength:.0f}.tif"
        tifffile.imwrite(str(path), image)
        records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=0), path=path))
        known[(0, wavelength)] = image
    return ImageDataset(folder=folder, records=records), known


@contextmanager
def _open_window(folder: Path):
    """Matches test_lspri_metadata_gui.py's MainWindow construction/teardown
    convention: dataset is cleared before close() so the closeEvent's
    processing-state save is a no-op instead of racing the temp dir cleanup."""
    window = MainWindow(folder, fast_startup=True)
    try:
        yield window
    finally:
        window._state.dataset = None
        window.close()
        window.deleteLater()


class TestOmeZarrExportViaDatasetController(unittest.TestCase):
    def test_export_through_the_moved_controller_completes_and_produces_a_loadable_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            source_dir.mkdir()
            dataset, known = _make_dataset(source_dir)

            with _open_window(root) as window:
                window._state.dataset = dataset
                destination = root / "export.ome.zarr"

                # Exercises the real path: DatasetController._start_ome_zarr_export
                # builds a FunctionWorker, launches it on a background daemon
                # thread, and wires its progress/result/error signals to
                # DatasetController's own completion handlers.
                window._dataset_controller._start_ome_zarr_export(
                    destination,
                    IMAGE_SIZE,
                    compression_enabled=False,
                    shard_mode="per_image",
                    skip_excluded_images=False,
                )
                self.assertTrue(window._ome_zarr_export_running)

                deadline = time.monotonic() + 30.0
                while window._ome_zarr_export_running and time.monotonic() < deadline:
                    _APP.processEvents()
                    time.sleep(0.05)

                self.assertFalse(window._ome_zarr_export_running, "export did not finish within the timeout")
                self.assertEqual(window._busy_operation_count, 0, "busy indicator must be balanced after export")

            reloaded = load_ome_zarr_dataset(destination)
            self.assertEqual(len(reloaded.records), len(known))
            for record in reloaded.records:
                from lspr_imaging_app.io.dataset import load_image_array

                expected = known[(int(record.key.spectral_cube_index), float(record.key.wavelength_nm))]
                read_back = load_image_array(str(record.path))
                np.testing.assert_array_equal(read_back.astype(np.uint16), expected)


if __name__ == "__main__":
    unittest.main()
