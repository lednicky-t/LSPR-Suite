"""Image panel > Analysis ribbon tab (Scan / Fast scan / Scan file): the real buttons, the real `ScanTask` and a small real dataset
(2026-10-23; moved from the Workflow panel 2026-10-24).

Driven through `QToolButton.click()`, never by screen coordinate. The window is not shown: the modules of the app are built
(`app_rewrite._build_modules`), an `AnalysisTab` is made and `_wire_analysis_scan` connects it, exactly as `build_main_window` does. Design: apps/LSPRi/eva/docs/analysis_redesign_plan_2026-10-19.md, section 18.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis import scan_store
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 64, 80


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(3)
    base = rng.uniform(900.0, 1100.0, size=(H, W)).astype(np.float32)
    for cube in (0, 1, 2):
        for wavelength in (500.0, 550.0, 600.0):
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), base + cube * 40.0 + (wavelength - 500.0) * 0.5)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _pump(until, seconds: float = 90.0) -> None:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


class ScanRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.row = AnalysisTab()
        self.ui_state = UiStateStore({})
        app_rewrite._wire_analysis_scan(self.m, self.row, self.ui_state)
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))

    def tearDown(self) -> None:
        self.m.analysis_scan.shutdown()
        self.row.deleteLater()
        self._tmp.cleanup()

    def _open_dataset(self, with_rois: bool = True) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        if with_rois:
            self.m.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=6.0)
            self.m.roi_toolbox.add_roi(55.0, 34.0, sample_diameter_px=6.0)

    def _press_run(self) -> None:
        self.finished.clear()
        self.row.run_button.click()

    def _wait_finished(self) -> None:
        _pump(lambda: bool(self.finished))
        _APP.processEvents()

    def _scan_file(self) -> Path:
        session_id = self.m.session_coordinator.active_session_id()
        return self.root / "sessions" / session_id / "analysis" / scan_store.FILE_NAME

    def test_run_without_a_dataset_says_so_and_starts_nothing(self) -> None:
        self._press_run()
        self.assertIn("Open a dataset", self.row.status_text())
        self.assertFalse(self.m.analysis_scan.is_running())

    def test_run_scans_saves_and_shows_the_result(self) -> None:
        self._open_dataset()
        self.assertEqual(self.row.status_text(), "No scan yet.")
        self._press_run()
        self.assertTrue(self.row.is_running())  # the Scan button is a Stop button now
        self.assertEqual(self.row._run_caption.text(), "Stop")
        self._wait_finished()
        self.assertEqual(self.finished[0][1], "completed")
        self.assertFalse(self.row.is_running())
        self.assertEqual(self.row._run_caption.text(), "Scan")
        self.assertIn("saved to scan.h5", self.row.status_text())
        stored = scan_store.read_scan(self._scan_file())
        self.assertEqual((stored.roi_ids, stored.wavelength_nm), ((1, 2), (500.0, 550.0, 600.0)))
        self.assertTrue(stored.computed().all())
        self.assertTrue(np.isfinite(stored.values["mean"]).all())

    def test_a_second_run_is_up_to_date_and_switching_fast_scan_off_forces_a_rescan(self) -> None:
        self._open_dataset()
        self._press_run()
        self._wait_finished()
        self._press_run()
        self._wait_finished()
        self.assertIn("up to date", self.row.status_text())
        self.assertTrue(self.row.fast_scan())  # on by default
        self.row.fast_scan_button.click()  # off: every cube again
        self.assertFalse(self.row.fast_scan())
        self._press_run()
        self._wait_finished()
        self.assertIn("scanned in", self.row.status_text())
        self.assertNotIn("already current", self.row.status_text())

    def test_moving_a_roi_makes_the_next_run_scan_again(self) -> None:
        self._open_dataset()
        self._press_run()
        self._wait_finished()
        self.m.roi_toolbox.move_roi(1, 41.0, 30.0)
        self._press_run()
        self._wait_finished()
        self.assertIn("scanned in", self.row.status_text())

    def test_stop_cancels_through_the_row(self) -> None:
        self._open_dataset()
        self._press_run()
        self.row.run_button.click()  # while it runs, the same button stops it
        self._wait_finished()
        self.assertEqual(self.finished[0][1], "cancelled")
        self.assertIn("saved", self.row.status_text())

    def test_no_rois_is_explained_not_crashed(self) -> None:
        self._open_dataset(with_rois=False)
        self._press_run()
        self._wait_finished()
        self.assertEqual(self.finished[0][1], "failed")
        self.assertIn("no ROIs", self.row.status_text())

    def test_fast_scan_is_remembered(self) -> None:
        self.row.fast_scan_button.click()
        self.assertEqual(self.ui_state.get("analysis/fast_scan"), False)

    def test_the_scan_file_button_shows_only_while_there_is_a_file_and_opens_its_folder(self) -> None:
        opened: list[str] = []
        real = app_rewrite.QDesktopServices.openUrl
        app_rewrite.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toLocalFile()) or True)
        self.addCleanup(lambda: setattr(app_rewrite.QDesktopServices, "openUrl", real))
        self.assertFalse(self.row.scan_file_button_visible())
        self._open_dataset()
        self.assertFalse(self.row.scan_file_button_visible())  # a session, but no scan yet
        self._press_run()
        self._wait_finished()
        _pump(lambda: self.row.scan_file_button_visible(), 5.0)
        self.assertTrue(self.row.scan_file_button_visible())
        self.row.folder_button.click()
        self.assertEqual([Path(p) for p in opened], [self._scan_file().parent])
        self.m.dataset.clear_dataset()
        _APP.processEvents()
        self.assertFalse(self.row.scan_file_button_visible())

    def test_a_refusal_goes_to_the_status_bar_signal_but_the_quiet_text_does_not(self) -> None:
        shown: list[str] = []
        self.row.status_message.connect(shown.append)
        self._press_run()  # no dataset
        self.assertEqual(len(shown), 1)
        self.assertIn("Open a dataset", shown[0])
        self._open_dataset()
        self.assertEqual(len(shown), 1)  # "No scan yet." is quiet

    def test_an_existing_scan_file_is_described_when_a_session_opens(self) -> None:
        self._open_dataset()
        self._press_run()
        self._wait_finished()
        line = scan_store.describe_file(self._scan_file())
        self.assertIn("3 of 3 cubes saved", line)
        self.assertIn("2 ROIs", line)
        self.assertIsNone(scan_store.describe_file(self.root / "nothing.h5"))
        (self.root / "bad.h5").write_bytes(b"x")
        self.assertIn("cannot be read", scan_store.describe_file(self.root / "bad.h5"))


if __name__ == "__main__":
    unittest.main()
