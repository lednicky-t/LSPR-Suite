"""Regression test for DatasetController.has_restorable_session (LSPRi eva,
gui/dataset_controller.py) - determines whether the startup restore flow
offers to reload a dataset's saved analysis state.

Processing state moved from sitting directly in the dataset folder into an
analysis/ subfolder (see git history: "Declutter dataset folder: analysis/
sidecar layout"). Every other path-resolution helper in main_window.py
(_session_already_exists, _list_available_sessions) was updated to check both
the new analysis/-prefixed location and the old top-level one, but
has_restorable_session was missed - it kept checking only the old location,
so real datasets using the current layout always reported "no previous
session found" at startup even with a fully saved session on disk.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.dataset_controller import DatasetController  # noqa: E402


def _make_controller(active_session_name: str) -> DatasetController:
    window = MagicMock()
    window._load_active_session_name_for_folder.return_value = active_session_name
    return DatasetController(window)


class HasRestorableSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def test_current_layout_default_session_is_detected(self) -> None:
        analysis_dir = self.folder / "analysis"
        analysis_dir.mkdir()
        (analysis_dir / "processing_profile.json").write_text("{}", encoding="utf-8")

        controller = _make_controller("Default")
        self.assertTrue(controller.has_restorable_session(self.folder))

    def test_current_layout_named_session_is_detected(self) -> None:
        session_dir = self.folder / "analysis" / "sessions" / "MySession"
        session_dir.mkdir(parents=True)
        (session_dir / "processing_profile.json").write_text("{}", encoding="utf-8")

        controller = _make_controller("MySession")
        self.assertTrue(controller.has_restorable_session(self.folder))

    def test_legacy_top_level_layout_still_detected(self) -> None:
        (self.folder / "processing_profile.json").write_text("{}", encoding="utf-8")

        controller = _make_controller("Default")
        self.assertTrue(controller.has_restorable_session(self.folder))

    def test_legacy_named_session_layout_still_detected(self) -> None:
        session_dir = self.folder / "sessions" / "MySession"
        session_dir.mkdir(parents=True)
        (session_dir / "processing_profile.json").write_text("{}", encoding="utf-8")

        controller = _make_controller("MySession")
        self.assertTrue(controller.has_restorable_session(self.folder))

    def test_no_saved_state_anywhere_is_not_restorable(self) -> None:
        controller = _make_controller("Default")
        self.assertFalse(controller.has_restorable_session(self.folder))


if __name__ == "__main__":
    unittest.main()
