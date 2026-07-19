from __future__ import annotations

import unittest

from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window_preferences import PreferencesDialog


class _FakeWindow:
    def __init__(self) -> None:
        self._ui_state_autosave_enabled = True
        self._acquisition_state_autosave_enabled = True
        self._log_buffering_enabled = True
        self._gui_housekeeping_enabled = True
        self._metric_plot_enabled = True
        self.called_set_ui_state_autosave_enabled: bool | None = None
        self.called_set_acquisition_state_autosave_enabled: bool | None = None
        self.called_set_log_buffering_enabled: bool | None = None
        self.called_set_gui_housekeeping_enabled: bool | None = None
        self.called_set_metric_plot_enabled: bool | None = None

    def _set_ui_state_autosave_enabled(self, enabled: bool) -> None:
        self.called_set_ui_state_autosave_enabled = bool(enabled)

    def _set_acquisition_state_autosave_enabled(self, enabled: bool) -> None:
        self.called_set_acquisition_state_autosave_enabled = bool(enabled)

    def _set_log_buffering_enabled(self, enabled: bool) -> None:
        self.called_set_log_buffering_enabled = bool(enabled)

    def _set_gui_housekeeping_enabled(self, enabled: bool) -> None:
        self.called_set_gui_housekeeping_enabled = bool(enabled)

    def _set_metric_plot_enabled(self, enabled: bool) -> None:
        self.called_set_metric_plot_enabled = bool(enabled)


class MainWindowPreferencesTests(unittest.TestCase):
    def test_dialog_loads_performance_switches_from_window_state(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.assertIsNotNone(app)
        window = _FakeWindow()
        window._log_buffering_enabled = False

        parent = QtWidgets.QWidget()
        dialog = PreferencesDialog(window, parent=parent)

        self.assertTrue(dialog.ui_state_autosave_check.isChecked())
        self.assertTrue(dialog.acquisition_state_autosave_check.isChecked())
        self.assertFalse(dialog.log_buffering_check.isChecked())
        self.assertTrue(dialog.gui_housekeeping_check.isChecked())
        self.assertTrue(dialog.metric_plot_check.isChecked())

    def test_apply_changes_propagates_performance_switches_to_window(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.assertIsNotNone(app)
        window = _FakeWindow()
        parent = QtWidgets.QWidget()
        dialog = PreferencesDialog(window, parent=parent)

        dialog.ui_state_autosave_check.setChecked(False)
        dialog.acquisition_state_autosave_check.setChecked(False)
        dialog.log_buffering_check.setChecked(False)
        dialog.gui_housekeeping_check.setChecked(False)
        dialog.metric_plot_check.setChecked(False)

        dialog.apply_changes()

        self.assertFalse(window.called_set_ui_state_autosave_enabled)
        self.assertFalse(window.called_set_acquisition_state_autosave_enabled)
        self.assertFalse(window.called_set_log_buffering_enabled)
        self.assertFalse(window.called_set_gui_housekeeping_enabled)
        self.assertFalse(window.called_set_metric_plot_enabled)


if __name__ == "__main__":
    unittest.main()
