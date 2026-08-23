"""Regression test for LSPRimaging Evaluation's Preferences window
(File > Preferences...): a categorized pop-out QDialog that replaced the old
nested File > Preferences submenu, and that also introduces a "start with
the Workflow log panel open or closed" setting that the old submenu had no
control for (the log console used to always force itself open on every
startup regardless of what the user last left it as).
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.preferences_dialog import PreferencesDialog  # noqa: E402


class _FakeSettings:
    def __init__(self, values: dict) -> None:
        self._values = dict(values)

    def value(self, key, default=None):
        return self._values.get(key, default)

    def setValue(self, key, value) -> None:
        self._values[key] = value


class _FakeWindow:
    def __init__(self, settings: _FakeSettings) -> None:
        self._settings = settings
        self.theme_calls: list[str] = []
        self.startup_restore_timeout_calls: list[int] = []
        self.log_panel_open_calls: list[bool] = []
        self.zarr_adaptive_enabled_calls: list[bool] = []
        self.zarr_adaptive_batch_calls: list[int] = []

    def _set_ui_theme(self, theme_name: str) -> None:
        self.theme_calls.append(theme_name)
        self._settings.setValue("ui/theme", theme_name)

    def _startup_restore_timeout_seconds(self) -> int:
        return max(int(self._settings.value("startup/restore_previous_session_timeout_s", 5)), 0)

    def _set_startup_restore_timeout_seconds(self, seconds: int) -> None:
        self.startup_restore_timeout_calls.append(int(seconds))
        self._settings.setValue("startup/restore_previous_session_timeout_s", int(seconds))

    def _startup_log_panel_open(self) -> bool:
        return bool(self._settings.value("startup/log_panel_open", True))

    def _set_startup_log_panel_open(self, open_on_startup: bool) -> None:
        self.log_panel_open_calls.append(bool(open_on_startup))
        self._settings.setValue("startup/log_panel_open", bool(open_on_startup))

    def _ome_zarr_adaptive_enabled(self) -> bool:
        return str(self._settings.value("export/ome_zarr_adaptive_enabled", "true")).strip().lower() != "false"

    def _set_ome_zarr_adaptive_enabled(self, checked: bool) -> None:
        self.zarr_adaptive_enabled_calls.append(bool(checked))
        self._settings.setValue("export/ome_zarr_adaptive_enabled", "true" if checked else "false")

    def _ome_zarr_adaptive_batch_mb(self) -> int:
        return int(self._settings.value("export/ome_zarr_adaptive_batch_mb", 1024))

    def _set_ome_zarr_adaptive_batch_mb(self, value: int) -> None:
        self.zarr_adaptive_batch_calls.append(int(value))
        self._settings.setValue("export/ome_zarr_adaptive_batch_mb", int(value))


class PreferencesDialogTests(unittest.TestCase):
    def test_loads_current_window_state(self) -> None:
        window = _FakeWindow(
            _FakeSettings(
                {
                    "ui/theme": "dark",
                    "startup/restore_previous_session_timeout_s": 0,
                    "startup/log_panel_open": False,
                    "export/ome_zarr_adaptive_enabled": "false",
                    "export/ome_zarr_adaptive_batch_mb": 2048,
                }
            )
        )
        parent = QtWidgets.QWidget()
        dialog = PreferencesDialog(window, parent=parent)

        self.assertEqual(dialog.theme_combo.currentData(), "dark")
        self.assertEqual(dialog.startup_restore_combo.currentData(), 0)
        self.assertFalse(dialog.log_panel_open_check.isChecked())
        self.assertFalse(dialog.zarr_adaptive_enabled_check.isChecked())
        self.assertEqual(dialog.zarr_adaptive_batch_combo.currentData(), 2048)

    def test_defaults_start_with_log_panel_open(self) -> None:
        window = _FakeWindow(_FakeSettings({}))
        parent = QtWidgets.QWidget()
        dialog = PreferencesDialog(window, parent=parent)
        self.assertTrue(dialog.log_panel_open_check.isChecked())

    def test_apply_changes_propagates_all_fields_to_window(self) -> None:
        window = _FakeWindow(_FakeSettings({}))
        parent = QtWidgets.QWidget()
        dialog = PreferencesDialog(window, parent=parent)

        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
        dialog.startup_restore_combo.setCurrentIndex(dialog.startup_restore_combo.findData(0))
        dialog.log_panel_open_check.setChecked(False)
        dialog.zarr_adaptive_enabled_check.setChecked(False)
        dialog.zarr_adaptive_batch_combo.setCurrentIndex(dialog.zarr_adaptive_batch_combo.findData(256))

        dialog.apply_changes()

        self.assertEqual(window.theme_calls, ["dark"])
        self.assertEqual(window.startup_restore_timeout_calls, [0])
        self.assertEqual(window.log_panel_open_calls, [False])
        self.assertEqual(window.zarr_adaptive_enabled_calls, [False])
        self.assertEqual(window.zarr_adaptive_batch_calls, [256])


if __name__ == "__main__":
    unittest.main()
