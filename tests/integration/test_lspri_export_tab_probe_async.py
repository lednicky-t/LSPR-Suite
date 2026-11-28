"""The Export tab reads the dataset's image size on a thread (`ExportTab._on_dataset_loaded`, 2026-11-28).

The read of one real plane took 1.4 s on the GUI thread at every launch (`gui.stall`). These tests pin: the load returns before the read
is done, the Export button waits for it, the size and stored type arrive when it is in, and a result for a dataset that was cleared or
replaced meanwhile is dropped. The read is held back with a `threading.Event` (no sleeping, no timing)."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.dataset.module import DatasetModule  # noqa: E402
from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.panels.image import export_tab as export_tab_file  # noqa: E402
from lspr_imaging_app.panels.image.export_tab import ExportTab  # noqa: E402

H, W = 40, 60


def _pump(until, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return until()


class ExportProbeAsyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        folder = Path(self._tmp.name)
        path = folder / "c0_wl500.tif"
        tifffile.imwrite(str(path), np.full((H, W), 1500, dtype=np.uint16))
        self.dataset_obj = ImageDataset(
            folder=folder, records=[ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)]
        )
        self.module = DatasetModule()
        self.tab = ExportTab(self.module, None)
        self.addCleanup(self.tab.deleteLater)
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        real = export_tab_file.probe_ome_zarr_export_shape

        def held(dataset, preprocessing):
            self.release.wait(10.0)
            return real(dataset, preprocessing)

        patcher = mock.patch.object(export_tab_file, "probe_ome_zarr_export_shape", held)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_loading_does_not_wait_for_the_read_and_the_button_waits_for_it(self) -> None:
        self.module.load_dataset(self.dataset_obj)
        self.assertIsNone(self.tab._image_dtype)  # the load returned with the read still held
        self.assertFalse(self.tab._export_button.isEnabled())
        self.assertIn("reading", self.tab._export_button.toolTip())
        self.release.set()
        self.assertTrue(_pump(lambda: self.tab._image_dtype is not None))
        self.assertEqual((self.tab._image_height, self.tab._image_width, self.tab._image_dtype), (H, W, np.uint16))
        self.assertTrue(self.tab._export_button.isEnabled())

    def test_a_result_for_a_cleared_dataset_is_dropped(self) -> None:
        self.module.load_dataset(self.dataset_obj)
        self.module.clear_dataset()
        self.release.set()
        _pump(lambda: False, timeout=0.3)
        self.assertIsNone(self.tab._image_dtype)
        self.assertFalse(self.tab._export_button.isEnabled())

    def test_a_failing_read_leaves_the_button_usable_with_no_size(self) -> None:
        with mock.patch.object(export_tab_file, "probe_ome_zarr_export_shape", side_effect=OSError("gone")):
            self.module.load_dataset(self.dataset_obj)
            self.assertTrue(_pump(lambda: self.tab._probe_for is None))
        self.assertIsNone(self.tab._image_dtype)
        self.assertTrue(self.tab._export_button.isEnabled())  # as before: the export reads the plane itself


if __name__ == "__main__":
    unittest.main()
