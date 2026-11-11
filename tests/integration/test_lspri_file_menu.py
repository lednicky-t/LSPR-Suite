"""File menu and File -> Dataset Manager (2026-11-11; they replace the retired Workflow panel).

The real modules (`app_rewrite._build_modules`), a plain `QMainWindow` and the File menu wired exactly as
`build_main_window` does (`_build_menu_bar` + `_wire_file_menu`). Driven through `QAction.trigger()` and widget calls,
never by screen coordinate; file dialogs and message boxes are patched.
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
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels import file_menu as file_menu_module
    from lspr_imaging_app.panels.dataset_manager import DatasetManagerDialog
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.storage.ui_state_keys import DATASET_MANAGER_PAGE
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 32, 40


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    for cube in (0, 1):
        for wavelength in (500.0, 550.0, 600.0):
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), np.full((H, W), 1000.0 + cube, dtype=np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _pump(seconds: float = 0.3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


class FileMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.window = QtWidgets.QMainWindow()
        self.ui_state = UiStateStore({})
        self.menu, _edit, _view = app_rewrite._build_menu_bar(self.window)
        self.actions = app_rewrite._wire_file_menu(self.menu, self.window, self.m, self.ui_state)
        self.manager = self.window.findChild(DatasetManagerDialog)

    def tearDown(self) -> None:
        self.m.session_autosave.flush()
        self.manager.hide()
        self.window.deleteLater()
        _pump(0.1)
        self._tmp.cleanup()

    def _open_dataset(self) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        _pump()

    def _entries(self, menu: QtWidgets.QMenu) -> list[str]:
        return [action.text() for action in menu.actions() if not action.isSeparator()]

    # -- the menu ------------------------------------------------------------------------------------

    def test_the_entries_in_order(self) -> None:
        self.assertEqual(
            self._entries(self.menu),
            ["Open Dataset...", "Show Dataset Folder", "Session", "Import", "Export", "Dataset Manager...", "E&xit"],
        )
        self.assertEqual(self.actions.open_action.shortcut().toString(), "Ctrl+O")
        self.assertEqual(self.actions.manager_action.shortcut().toString(), "Ctrl+I")

    def test_dataset_actions_wait_for_a_dataset(self) -> None:
        dataset_actions = (self.actions.show_folder_action, self.actions.import_plan_action, self.actions.export_metadata_action)
        self.assertFalse(any(action.isEnabled() for action in dataset_actions))
        self.assertFalse(self.actions.new_session_action.isEnabled())
        self._open_dataset()
        self.assertTrue(all(action.isEnabled() for action in dataset_actions))
        self.assertTrue(self.actions.new_session_action.isEnabled())

    def test_open_dataset_loads_the_chosen_folder(self) -> None:
        with mock.patch.object(file_menu_module.QFileDialog, "getExistingDirectory", return_value=str(self.root)), \
                mock.patch.object(self.m.dataset, "load_dataset_from_folder") as load:
            self.actions.open_action.trigger()
        load.assert_called_once_with(self.root)

    def test_cancelling_the_folder_dialog_loads_nothing(self) -> None:
        with mock.patch.object(file_menu_module.QFileDialog, "getExistingDirectory", return_value=""), \
                mock.patch.object(self.m.dataset, "load_dataset_from_folder") as load:
            self.actions.open_action.trigger()
        load.assert_not_called()

    def test_a_failed_load_is_shown_not_only_logged(self) -> None:
        with mock.patch.object(file_menu_module.QMessageBox, "critical") as critical:
            self.m.dataset.dataset_load_failed.emit("no images in this folder")
        critical.assert_called_once()
        self.assertIn("no images in this folder", critical.call_args.args[2])

    # -- sessions ------------------------------------------------------------------------------------

    def _session_entries(self) -> list:
        return [action for action in self.actions.session_menu.actions() if action.isCheckable()]

    def test_the_session_menu_lists_the_sessions_with_the_active_one_checked(self) -> None:
        self._open_dataset()
        entries = self._session_entries()
        self.assertEqual(len(entries), 1)  # a first, blank session is made when a dataset opens
        self.assertTrue(entries[0].isChecked())
        first_id = self.m.session_coordinator.active_session_id()

        self.actions.new_session_action.trigger()
        _pump()
        entries = self._session_entries()
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(self.m.session_coordinator.active_session_id(), first_id)
        self.assertEqual([entry.isChecked() for entry in entries], [False, True])

        entries[0].trigger()
        _pump()
        self.assertEqual(self.m.session_coordinator.active_session_id(), first_id)
        self.assertTrue(self._session_entries()[0].isChecked())

    # -- the Dataset Manager ---------------------------------------------------------------------

    def test_the_manager_is_one_non_modal_window_with_three_pages(self) -> None:
        self.actions.manager_action.trigger()
        self.assertTrue(self.manager.isVisible())
        self.assertFalse(self.manager.isModal())
        self.actions.manager_action.trigger()
        self.assertEqual(len(self.window.findChildren(DatasetManagerDialog)), 1)
        tabs = self.manager.tabs
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ["Overview", "Experiment plan", "Sessions"])

    def test_the_overview_follows_the_dataset(self) -> None:
        self.assertFalse(self.manager.summary._empty_label.isHidden())
        self._open_dataset()
        self.assertTrue(self.manager.summary._empty_label.isHidden())
        self.assertEqual(self.manager.summary._folder_label.text(), str(self.root))
        self.assertEqual(self.manager.summary._images_label.text(), "6")
        self.assertEqual(self.manager.summary._cubes_label.text(), "2")
        self.assertEqual(self.manager.summary._wavelengths_label.text(), "3")
        self.m.dataset.clear_dataset()
        self.assertFalse(self.manager.summary._empty_label.isHidden())

    def test_the_sessions_page_opens_and_creates_sessions(self) -> None:
        self._open_dataset()
        page = self.manager.sessions
        first_id = self.m.session_coordinator.active_session_id()
        self.assertEqual(page.list.count(), 1)
        self.assertFalse(page.open_button.isEnabled())  # the selected one is already active
        page.new_button.click()
        _pump()
        self.assertEqual(page.list.count(), 2)
        page.list.setCurrentRow(0)
        self.assertTrue(page.open_button.isEnabled())
        page.open_button.click()
        _pump()
        self.assertEqual(self.m.session_coordinator.active_session_id(), first_id)

    def test_the_page_shown_is_remembered(self) -> None:
        self.manager.present("sessions")
        self.assertEqual(self.ui_state.get(DATASET_MANAGER_PAGE), "sessions")
        again = DatasetManagerDialog(self.m.dataset, self.m.selection, self.m.session_coordinator, ui_state=self.ui_state)
        self.addCleanup(again.deleteLater)
        self.assertEqual(again.current_page(), "sessions")

    def test_import_shows_the_files_on_the_plan_page(self) -> None:
        self._open_dataset()
        picked = [self.root / "plan.csv"]
        with mock.patch.object(file_menu_module, "import_metadata_dialog", return_value=picked):
            self.actions.import_plan_action.trigger()
        self.assertEqual(self.manager.plan._path_edit.text(), "plan.csv")


if __name__ == "__main__":
    unittest.main()
