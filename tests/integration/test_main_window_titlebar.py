from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtGui import QUndoStack

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.acquisition_controller import _experiment_runtime_label, _experiment_runtime_state
from lspr_app.gui.acquisition_controller import update_window_mode_label
from lspr_app.gui.chrome import build_menu_bar
from lspr_app.gui.main_window_titlebar import device_status_state, device_status_tooltip, refresh_hw_device_status_strip
from lspr_core import LAUNCH_PROFILE_CONTROL_EDITOR, LAUNCH_PROFILE_FULL, launch_profile_spec


class MainWindowTitleBarTests(unittest.TestCase):
    class _FakeLabel:
        def __init__(self) -> None:
            self._text = ""
            self.tooltip = ""
            self.stylesheet = ""
            self.visible = True
            self.pixmap = None
            self.cleared = False

        def setText(self, value: str) -> None:
            self._text = str(value)

        def text(self) -> str:
            return self._text

        def setToolTip(self, value: str) -> None:
            self.tooltip = str(value)

        def setStyleSheet(self, value: str) -> None:
            self.stylesheet = str(value)

        def clear(self) -> None:
            self.cleared = True
            self.pixmap = None

        def setVisible(self, visible: bool) -> None:
            self.visible = bool(visible)

        def setPixmap(self, pixmap) -> None:
            self.pixmap = pixmap

    class _FakeCluster:
        def __init__(self) -> None:
            self.visible = True

        def setVisible(self, visible: bool) -> None:
            self.visible = bool(visible)

    class _FakeAction:
        def __init__(self) -> None:
            self.enabled = None

        def setEnabled(self, value: bool) -> None:
            self.enabled = bool(value)

    class _FakeWindow:
        def __init__(self) -> None:
            self.undo_stack = QUndoStack()
            self.called_disconnect_all_devices = False
            self.called_show_connected_devices = False
            self.called_set_acquisition_state_autosave_enabled = None
            self.called_set_ui_state_autosave_enabled = None
            self.called_set_log_buffering_enabled = None
            self.called_set_gui_housekeeping_enabled = None

        def _save_processing_settings_dialog(self) -> None:
            pass

        def _load_processing_settings_dialog(self) -> None:
            pass

        def _export_current_plot(self) -> None:
            pass

        def close(self) -> None:
            pass

        def _activate_spectra_view(self) -> None:
            pass

        def _activate_experimental_control_view(self) -> None:
            pass

        def _toggle_left_controls(self, *_args) -> None:
            pass

        def _toggle_sensorgram(self, *_args) -> None:
            pass

        def _refresh_plot(self) -> None:
            pass

        def _show_connected_devices_dialog(self) -> None:
            self.called_show_connected_devices = True

        def _disconnect_all_devices(self) -> None:
            self.called_disconnect_all_devices = True

        def _start_hardware_initialization(self) -> None:
            pass

        def _show_quick_help_dialog(self) -> None:
            pass

        def _show_shortcuts_dialog(self) -> None:
            pass

        def _show_diagnostics_legend_dialog(self) -> None:
            pass

        def _set_processing_debug_mode_enabled(self, *_args) -> None:
            pass

        def _set_acquisition_state_autosave_enabled(self, enabled: bool) -> None:
            self.called_set_acquisition_state_autosave_enabled = bool(enabled)

        def _set_ui_state_autosave_enabled(self, enabled: bool) -> None:
            self.called_set_ui_state_autosave_enabled = bool(enabled)

        def _set_log_buffering_enabled(self, enabled: bool) -> None:
            self.called_set_log_buffering_enabled = bool(enabled)

        def _set_gui_housekeeping_enabled(self, enabled: bool) -> None:
            self.called_set_gui_housekeeping_enabled = bool(enabled)

        def _set_sensorgram_heatmap_enabled(self, enabled: bool) -> None:
            self.called_set_sensorgram_heatmap_enabled = bool(enabled)

        def _set_metric_plot_enabled(self, enabled: bool) -> None:
            self.called_set_metric_plot_enabled = bool(enabled)

        def _show_about_dialog(self) -> None:
            pass

        def _show_preferences_dialog(self) -> None:
            pass

        def _show_import_from_measurement_dialog(self) -> None:
            pass

        def _open_recording_project_destination_in_explorer(self) -> None:
            pass

        def _apply_layout_preset(self, *_args, **_kwargs) -> None:
            pass

        def _save_current_layout_to_preset(self, *_args) -> None:
            pass

        def _reset_layout_presets_to_defaults(self, *_args) -> None:
            pass

        def _toggle_diagnostics_panel(self, *_args) -> None:
            pass

        def _set_always_on_top(self, *_args) -> None:
            pass

        def _show_device_manager_dialog(self) -> None:
            pass

        def _open_app_config_folder(self) -> None:
            pass

    def test_device_status_state_supports_discovered_devices(self) -> None:
        self.assertEqual(device_status_state(True, False), "connected")
        self.assertEqual(device_status_state(False, True), "discovered")
        self.assertEqual(device_status_state(False, False), "disconnected")

    def test_device_status_tooltip_includes_state_and_port(self) -> None:
        self.assertEqual(
            device_status_tooltip("Pump", "connected", port_name="COM8"),
            "Pump: connected on COM8.",
        )
        self.assertEqual(
            device_status_tooltip("Valve", "discovered", port_name="COM9", detail="manual assignment"),
            "Valve: discovered on COM9. (manual assignment)",
        )
        self.assertEqual(device_status_tooltip("Switch Rotary Valve", "disconnected"), "Switch Rotary Valve: disconnected.")

    def test_experiment_runtime_state_prefers_experiment_control_states(self) -> None:
        window = SimpleNamespace(
            _source_mode="spectrometer",
            _measurement_active=False,
            _live_active=False,
            _experiment_control_window=SimpleNamespace(
                _plan_running=False,
                _plan_holding=False,
                _plan_paused=False,
            ),
        )

        self.assertEqual(_experiment_runtime_state(window), "stopped")
        window._experiment_control_window._plan_running = True
        self.assertEqual(_experiment_runtime_state(window), "running")
        window._experiment_control_window._plan_running = False
        window._experiment_control_window._plan_holding = True
        self.assertEqual(_experiment_runtime_state(window), "hold")
        window._experiment_control_window._plan_holding = False
        window._experiment_control_window._plan_paused = True
        self.assertEqual(_experiment_runtime_state(window), "paused")
        window._experiment_control_window._plan_paused = False
        window._measurement_active = True
        self.assertEqual(_experiment_runtime_state(window), "stopped")
        window._measurement_active = False
        window._live_active = True
        self.assertEqual(_experiment_runtime_state(window), "stopped")

    def test_experiment_runtime_label_shows_recording_suffix(self) -> None:
        window = SimpleNamespace(
            _source_mode="spectrometer",
            _measurement_active=True,
            _experiment_control_window=SimpleNamespace(
                _plan_running=False,
                _plan_holding=False,
                _plan_paused=False,
            ),
        )
        self.assertEqual(_experiment_runtime_label(window), "Experiment: stopped & recording")
        window._experiment_control_window._plan_running = True
        self.assertEqual(_experiment_runtime_label(window), "Experiment: running & recording")
        window._experiment_control_window._plan_running = False
        window._experiment_control_window._plan_holding = True
        self.assertEqual(_experiment_runtime_label(window), "Experiment: hold & recording")
        window._experiment_control_window._plan_holding = False
        window._experiment_control_window._plan_paused = True
        self.assertEqual(_experiment_runtime_label(window), "Experiment: paused & recording")

    def test_control_editor_profile_uses_mode_label_and_hides_source_icon(self) -> None:
        window = SimpleNamespace(
            _launch_profile_spec=launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR),
            _source_mode="spectrometer",
            _measurement_active=False,
            _experiment_control_window=SimpleNamespace(
                _plan_running=False,
                _plan_holding=False,
                _plan_paused=False,
            ),
            _window_mode_label=self._FakeLabel(),
            _window_mode_icon_label=self._FakeLabel(),
        )
        update_window_mode_label(window)
        self.assertEqual(window._window_mode_label.text(), "Control Plan Editor Mode")
        self.assertIn("Control Plan Editor mode", window._window_mode_label.tooltip)
        self.assertFalse(window._window_mode_icon_label.visible)
        self.assertEqual(window._window_mode_label.stylesheet, "color: #9FD7A6; font-weight: 700;")

    def test_device_status_strip_can_be_hidden_for_non_full_profiles(self) -> None:
        window = SimpleNamespace(
            _launch_profile_spec=launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR),
            _hw_status_items=[("spectrometer", self._FakeLabel(), self._FakeLabel())],
            _titlebar_status_cluster=self._FakeCluster(),
            _experiment_control_window=None,
            _hardware_init_task=None,
            _hardware_init_ready_emitted=True,
        )
        refresh_hw_device_status_strip(window)
        self.assertFalse(window._titlebar_status_cluster.visible)

    def test_full_profile_keeps_status_strip_visible(self) -> None:
        window = SimpleNamespace(
            _launch_profile_spec=launch_profile_spec(LAUNCH_PROFILE_FULL),
            _hw_status_items=[("spectrometer", self._FakeLabel(), self._FakeLabel())],
            _titlebar_status_cluster=self._FakeCluster(),
            _experiment_control_window=None,
            _hardware_init_task=None,
            _hardware_init_ready_emitted=True,
            _hardware_available=True,
        )
        with patch("lspr_app.gui.main_window_titlebar.device_status_icon", lambda _state: SimpleNamespace(pixmap=lambda *_args, **_kwargs: None)):
            refresh_hw_device_status_strip(window)
        self.assertTrue(window._titlebar_status_cluster.visible)

    def test_hw_menu_includes_disconnect_all_devices_action(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.assertIsNotNone(app)
        window = self._FakeWindow()
        menu_bar = build_menu_bar(window)
        hw_menu_action = next(action for action in menu_bar.actions() if action.text() == "Hardware")
        hw_menu = hw_menu_action.menu()
        self.assertIsNotNone(hw_menu)
        disconnect_action = next(action for action in hw_menu.actions() if action.text() == "Disconnect all")
        disconnect_action.trigger()
        self.assertTrue(window.called_disconnect_all_devices)

    def test_disconnect_all_devices_action_is_disabled_while_hardware_init_runs(self) -> None:
        window = SimpleNamespace(
            _hw_disconnect_all_action=self._FakeAction(),
            _hardware_init_task=object(),
        )
        from lspr_app.gui.main_window import MainWindow

        MainWindow._sync_hardware_menu_actions(window)
        self.assertFalse(window._hw_disconnect_all_action.enabled)

        window._hardware_init_task = None
        MainWindow._sync_hardware_menu_actions(window)
        self.assertTrue(window._hw_disconnect_all_action.enabled)

    def test_experiment_control_startup_ports_sync_triggers_after_late_panel_creation(self) -> None:
        calls: list[str] = []
        window = SimpleNamespace(
            _hardware_init_ready_emitted=True,
            _launch_profile_settings=lambda: launch_profile_spec(LAUNCH_PROFILE_FULL),
            _experiment_control_window=SimpleNamespace(
                sync_from_lifecycle_controller=lambda: calls.append("sync"),
            ),
            _log_info=lambda message: calls.append(f"info:{message}"),
            _log_warning=lambda message: calls.append(f"warn:{message}"),
        )

        from lspr_app.gui.main_window import MainWindow

        MainWindow._sync_experiment_control_startup_ports(window)

        self.assertEqual(calls, ["sync"])


if __name__ == "__main__":
    unittest.main()
