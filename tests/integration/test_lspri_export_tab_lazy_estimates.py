"""The Export tab works out its chunk estimates only while it is shown (`ExportTab(lazy_estimates=True)`, 2026-11-28).

The estimates cost 0.3-0.8 s of the GUI thread for a big session (`affine_reach` over every fitted chromatic model) and are read only while
the tab is open ("i" icon, field tooltips), yet they were redone on every launch and every ROI edit. These tests pin: hidden = no work,
shown = the numbers appear, the chunk grid on the image keeps working while hidden, and the default (tests, other callers) is unchanged."""

from __future__ import annotations

import sys
import tempfile
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
from lspr_imaging_app.image_tools import ChromaticModule, GeometryModule  # noqa: E402
from lspr_imaging_app.panels.image.export_tab import ExportTab  # noqa: E402
from lspr_imaging_app.roi import RoiToolbox  # noqa: E402

H, W = 200, 300


def _pump(until, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return until()


class LazyEstimatesTests(unittest.TestCase):
    def make_tab(self, *, lazy: bool) -> ExportTab:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        records = []
        for wl in (500.0, 600.0):
            path = folder / f"c0_wl{wl:.0f}.tif"
            tifffile.imwrite(str(path), np.full((H, W), 1500, dtype=np.uint16))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=0), path=path))
        module = DatasetModule()
        toolbox = RoiToolbox()
        for column in range(3):
            toolbox.add_roi(40.0 + column * 40.0, 60.0, sample_diameter_px=20.0)
        tab = ExportTab(module, None, geometry=GeometryModule(), roi_toolbox=toolbox, chromatic=ChromaticModule(), lazy_estimates=lazy)
        self.addCleanup(tab.deleteLater)
        self.frames = 0
        self.layouts = 0
        real_frame, real_layout = tab._current_frame, tab.current_layout

        def counted_frame():
            self.frames += 1
            return real_frame()

        def counted_layout():
            self.layouts += 1
            return real_layout()

        mock.patch.object(tab, "_current_frame", counted_frame).start()
        mock.patch.object(tab, "current_layout", counted_layout).start()
        self.addCleanup(mock.patch.stopall)
        module.load_dataset(ImageDataset(folder=folder, records=records))
        self.assertTrue(_pump(lambda: tab._probe_for is None))
        return tab

    def test_hidden_tab_does_no_estimate_work_even_when_the_rois_change(self) -> None:
        tab = self.make_tab(lazy=True)
        self.assertEqual(self.frames, 0)
        self.assertTrue(tab._estimates_stale)
        tab._on_roi_layout_changed()
        _pump(lambda: False, timeout=0.3)  # the debounce
        self.assertEqual(self.frames, 0)
        self.assertTrue(tab._export_button.isEnabled())  # exporting does not depend on the estimates

    def test_showing_the_tab_fills_the_estimates_in(self) -> None:
        tab = self.make_tab(lazy=True)
        tab.show()
        self.addCleanup(tab.hide)
        self.assertTrue(_pump(lambda: bool(tab._estimate_text)))
        self.assertGreaterEqual(self.frames, 1)
        self.assertFalse(tab._estimates_stale)
        self.assertIn("chunks/plane", tab.info_html())

    def test_the_chunk_grid_on_the_image_keeps_its_numbers_while_hidden(self) -> None:
        tab = self.make_tab(lazy=True)
        before = self.layouts
        tab._chunk_grid_button.setChecked(True)  # the grid on the image needs the aligned layout even with the tab closed
        self.assertGreater(self.layouts, before)
        tab._on_roi_layout_changed()  # and while the grid is on, an ROI edit is followed at once
        self.assertTrue(_pump(lambda: self.frames >= 1))

    def test_without_the_option_nothing_changes(self) -> None:
        tab = self.make_tab(lazy=False)
        self.assertGreaterEqual(self.frames, 1)
        self.assertFalse(tab._estimates_stale)
        self.assertIn("chunks/plane", tab._estimate_text)


if __name__ == "__main__":
    unittest.main()
