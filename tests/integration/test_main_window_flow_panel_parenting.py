from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window import MainWindow
from lspr_app.gui.hardware_initializer import HardwareInitResult
from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.session import MeasurementSession
from lspr_core import LAUNCH_PROFILE_CONTROL_EDITOR, launch_profile_spec


class _DummySignal:
    def connect(self, _slot) -> None:
        return None


class _DummyStack:
    def __init__(self) -> None:
        self.widgets: list[object] = []
        self.removed: list[object] = []

    def indexOf(self, widget: object) -> int:
        try:
            return self.widgets.index(widget)
        except ValueError:
            return -1

    def removeWidget(self, widget: object) -> None:
        self.removed.append(widget)
        if widget in self.widgets:
            self.widgets.remove(widget)

    def addWidget(self, widget: object) -> None:
        self.widgets.append(widget)


class _FakeExperimentControlWindow:
    created_parent: object | None = None
    created_kwargs: dict[str, object] | None = None

    def __init__(
        self,
        _ui_state: dict[str, object],
        *,
        known_probe=None,
        theme_mode: str | None = None,
        initial_mswitch_devices=None,
        auto_connect_devices: bool = False,
        show_runtime_controls: bool = True,
        parent=None,
    ) -> None:
        self.__class__.created_parent = parent
        self.__class__.created_kwargs = {
            "auto_connect_devices": bool(auto_connect_devices),
            "show_runtime_controls": bool(show_runtime_controls),
        }
        self.availability_changed = _DummySignal()
        self.valve_availability_changed = _DummySignal()
        self.mswitch_availability_changed = _DummySignal()
        self.recording_control_requested = _DummySignal()
        self.experimental_control_state_recorded = _DummySignal()
        self.theme_changed = _DummySignal()
        self._ui_startup_ready = False

    def _set_record_with_flow_recording_active(self, _active: bool) -> None:
        return None

    def setVisible(self, _visible: bool) -> None:
        return None

    def set_theme(self, _mode: str) -> None:
        return None


class MainWindowFlowPanelParentingTest(unittest.TestCase):
    def test_sensorgram_downsampling_button_is_not_shown_while_unparented(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.assertIsNotNone(app)

        seen_unparented_show = []
        original_show_event = QtWidgets.QWidget.showEvent

        def _show_event(widget, event) -> None:
            if type(widget).__name__ == "QToolButton" and widget.objectName() == "sensorgramDownsamplingButton":
                seen_unparented_show.append(widget.parent() is None)
            return original_show_event(widget, event)

        with (
            patch.object(MainWindow, "_start_hardware_initialization", lambda self: None),
            patch.object(QtWidgets.QWidget, "showEvent", _show_event),
        ):
            window = MainWindow(SimulatedSpectrometer(), MeasurementSession(), None)
            window.close()

        self.assertFalse(any(seen_unparented_show))

    def test_experiment_control_window_is_parented_when_created(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._experiment_control_window = None
        window._experiment_control_window_ui_state = {}
        window._discovered_pump_probe = None
        window._theme_mode = "dark"
        window._initial_mswitch_devices = []
        window._measurement_active = False
        window._ui_startup_ready = False
        window._top_content_stack = _DummyStack()
        window._top_view_mode = "spectra"
        window._pending_layout_preset_selected = ""
        window._experiment_control_panel_placeholder = SimpleNamespace()
        window._flow_panel_placeholder = window._experiment_control_panel_placeholder
        window.set_theme = lambda _theme: None  # type: ignore[method-assign]

        with (
            patch.object(MainWindow, "_log_info", lambda *args, **kwargs: None),
            patch("lspr_app.gui.experiment_control_window.ExperimentControlWindow", _FakeExperimentControlWindow),
            patch("lspr_app.gui.main_window_lifecycle.ensure_visible_top_content_splitter", lambda *_args, **_kwargs: None),
        ):
            MainWindow._ensure_flow_panel(window)

        self.assertIs(_FakeExperimentControlWindow.created_parent, window._top_content_stack)
        self.assertIsNotNone(window._experiment_control_window)
        self.assertIn(window._experiment_control_window, window._top_content_stack.widgets)
        self.assertFalse(window._experiment_control_window._ui_startup_ready)

    def test_experiment_control_window_uses_editor_profile_flags(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._experiment_control_window = None
        window._experiment_control_window_ui_state = {}
        window._discovered_pump_probe = None
        window._theme_mode = "dark"
        window._initial_mswitch_devices = []
        window._measurement_active = False
        window._ui_startup_ready = True
        window._top_content_stack = _DummyStack()
        window._top_view_mode = "spectra"
        window._pending_layout_preset_selected = ""
        window._experiment_control_panel_placeholder = SimpleNamespace()
        window._flow_panel_placeholder = window._experiment_control_panel_placeholder
        window._launch_profile_spec = launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR)
        window.set_theme = lambda _theme: None  # type: ignore[method-assign]

        with (
            patch.object(MainWindow, "_log_info", lambda *args, **kwargs: None),
            patch("lspr_app.gui.experiment_control_window.ExperimentControlWindow", _FakeExperimentControlWindow),
            patch("lspr_app.gui.main_window_lifecycle.ensure_visible_top_content_splitter", lambda *_args, **_kwargs: None),
        ):
            MainWindow._ensure_flow_panel(window)

        self.assertIs(_FakeExperimentControlWindow.created_parent, window._top_content_stack)
        self.assertEqual(_FakeExperimentControlWindow.created_kwargs, {"auto_connect_devices": False, "show_runtime_controls": False})
        self.assertTrue(window._experiment_control_window._ui_startup_ready)

    def test_hardware_init_finished_syncs_startup_after_ready_flag(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window._hardware_init_task = None
        window._closing = False
        window._hardware_init_ready_emitted = False
        window._experiment_control_window = None
        window._spectrometer = SimulatedSpectrometer()
        window._discovered_pump_probe = None
        window._hw_status_items = {}
        window._initial_mswitch_devices = []
        window._mswitch_probe = None
        calls: list[object] = []

        window._update_pump_status = lambda *_args, **_kwargs: calls.append("pump_status")  # type: ignore[method-assign]
        window._log_info = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        window._log_warning = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        window._log_success = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        window._emit_hardware_init_progress = lambda *_args, **_kwargs: calls.append("progress")  # type: ignore[method-assign]

        def _fake_finish(win, _text: str = "") -> None:
            calls.append(("finish", bool(win._hardware_init_ready_emitted)))
            win._hardware_init_ready_emitted = True

        def _fake_sync(win) -> None:
            calls.append(("sync", bool(win._hardware_init_ready_emitted)))

        result = HardwareInitResult(
            steps=[],
            pump_probe=None,
            pump_error=None,
            selector_devices=[],
            selector_error=None,
            valve_probe=None,
            valve_error=None,
            spectrometer_name="Spectrometer backend active: USB2000PLUS.",
        )

        with (
            patch("lspr_app.gui.main_window_lifecycle.finish_hardware_initialization_for", _fake_finish),
            patch("lspr_app.gui.main_window_lifecycle.sync_experiment_control_startup_ports_for", _fake_sync),
        ):
            MainWindow._handle_hardware_init_finished(window, result)

        self.assertIn(("finish", False), calls)
        self.assertIn(("sync", True), calls)
        self.assertLess(calls.index(("finish", False)), calls.index(("sync", True)))


if __name__ == "__main__":
    unittest.main()
