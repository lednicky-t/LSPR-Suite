"""Regression test: import/export processing settings must report the
correct status - in particular, a *failed* import must not have its error
message immediately overwritten by a "succeeded" message.

Both SessionStateManager.import_processing_profile() and
export_processing_profile() called window._end_busy() (which both ends the
busy indicator AND sets the status-bar text) more than once per operation:
import_processing_profile had a `finally:` block that unconditionally called
_end_busy("Processing settings imported.") after the except/else branches
had already called it with the real outcome - so on a *failed* import, the
finally block's generic success message silently overwrote
"Import failed: ...". export_processing_profile had a milder version of the
same bug: a successful export's informative _end_busy() message was
immediately overwritten by a generic _set_status_text() call right after.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, PreprocessingSettings  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402
from lspr_imaging_app.storage.workspace import save_processing_profile  # noqa: E402


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


class TestImportExportStatusReporting(unittest.TestCase):
    def test_failed_import_status_is_not_overwritten_by_a_success_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            bad_source = folder / "not_json.json"
            bad_source.write_text("{not valid json", encoding="utf-8")
            with _open_window(folder) as window:
                with mock.patch(
                    "lspr_imaging_app.gui.session_state_manager.QFileDialog.getOpenFileName",
                    return_value=(str(bad_source), ""),
                ), mock.patch("lspr_imaging_app.gui.session_state_manager.QMessageBox.critical"):
                    window._import_processing_profile()

                self.assertIn("Import failed", window.status_label.text())
                self.assertEqual(window._busy_operation_count, 0, "busy count must be balanced after the operation")

    def test_successful_import_reports_the_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            good_source = folder / "processing_profile.json"
            save_processing_profile(
                good_source,
                PreprocessingSettings(),
                AreaRoiDetectionSettings(),
                [],
            )
            with _open_window(folder) as window:
                with mock.patch(
                    "lspr_imaging_app.gui.session_state_manager.QFileDialog.getOpenFileName",
                    return_value=(str(good_source), ""),
                ):
                    window._import_processing_profile()

                self.assertIn(str(good_source), window.status_label.text())
                self.assertNotIn("Import failed", window.status_label.text())
                self.assertEqual(window._busy_operation_count, 0)

    def test_successful_export_status_is_not_overwritten_by_a_generic_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            destination = folder / "exported_profile.json"
            with _open_window(folder) as window:
                with mock.patch(
                    "lspr_imaging_app.gui.session_state_manager.QFileDialog.getSaveFileName",
                    return_value=(str(destination), ""),
                ):
                    window._export_processing_profile()

                self.assertIn(str(destination), window.status_label.text())
                self.assertEqual(window._busy_operation_count, 0)


if __name__ == "__main__":
    unittest.main()
