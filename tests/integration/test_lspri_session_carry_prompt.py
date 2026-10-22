"""The question after a carried export (`app_rewrite._wire_session_carry_prompt`, 2026-10-22): what it says (how long the export took
and how fast, what the session brought along) and what its two answers do.

A real in-process `QApplication`, never `.exec()`; the dataset module is a stand-in that records what it is asked to load.
**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

    from lspr_imaging_app.app_rewrite import _wire_session_carry_prompt
    from lspr_imaging_app.dataset.export_stats import ExportStats
    from lspr_imaging_app.storage.session_carry import CarryReport
    from lspr_imaging_app.storage.session_carry_task import CarriedResult, SessionCarry
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])

DESTINATION = Path("D:/exports/run1.ome.zarr")
REPORT = CarryReport(rois=160, masks_carried=411, chromatic_models=22, pointers_carried=250)


class SessionCarryPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.carry = SessionCarry(capture=lambda: None, naming=lambda: None, source=lambda: (None, None))
        self.dataset = mock.Mock()
        _wire_session_carry_prompt(self.window, SimpleNamespace(session_carry=self.carry, dataset=self.dataset))

    def _ask(self, stats: ExportStats | None) -> QMessageBox:
        self.carry.carried.emit(CarriedResult(DESTINATION, "2026-10-22_100000", REPORT, 0.4, stats))
        boxes = self.window.findChildren(QMessageBox)
        self.assertEqual(len(boxes), 1)
        return boxes[0]

    def test_the_box_says_how_long_the_export_took_and_how_fast(self) -> None:
        stats = ExportStats(planes=8478, bytes_written=int(11.2e9), stored_plane_bytes=1297 * 806 * 2, workers=8, seconds=292.0)
        box = self._ask(stats)
        self.assertIn("run1.ome.zarr", box.text())
        text = box.informativeText()
        self.assertIn("Export took 4 min 52 s", text)
        self.assertIn("8,478 images at 29 images/s", text)
        self.assertIn("GB/min of image data", text)
        self.assertIn("Wrote 11.2 GB to disk", text)
        self.assertIn("160 ROIs, 411 masks, 22 chromatic models, 250 mask pointers", text)
        self.assertTrue(text.rstrip().endswith("Open the exported dataset now?"))

    def test_without_numbers_the_box_still_asks(self) -> None:
        box = self._ask(None)
        self.assertNotIn("Export took", box.informativeText())
        self.assertIn("160 ROIs", box.informativeText())

    def test_yes_opens_the_exported_dataset(self) -> None:
        box = self._ask(None)
        box.button(QMessageBox.StandardButton.Yes).click()
        self.dataset.load_dataset_from_folder.assert_called_once_with(DESTINATION)

    def test_no_leaves_everything_as_it_is(self) -> None:
        box = self._ask(None)
        box.button(QMessageBox.StandardButton.No).click()
        self.dataset.load_dataset_from_folder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
