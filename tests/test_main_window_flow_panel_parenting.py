from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window import MainWindow
from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.session import MeasurementSession


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

    def __init__(
        self,
        _ui_state: dict[str, object],
        *,
        known_probe=None,
        theme_mode: str | None = None,
        initial_mswitch_devices=None,
        auto_connect_devices: bool = False,
        parent=None,
    ) -> None:
        self.__class__.created_parent = parent
        self.availability_changed = _DummySignal()
        self.valve_availability_changed = _DummySignal()
        self.mswitch_availability_changed = _DummySignal()
        self.recording_control_requested = _DummySignal()
        self.experimental_control_state_recorded = _DummySignal()
        self.theme_changed = _DummySignal()

    def _set_record_with_flow_recording_active(self, _active: bool) -> None:
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
        window._top_content_stack = _DummyStack()
        window._experiment_control_panel_placeholder = SimpleNamespace()
        window._flow_panel_placeholder = window._experiment_control_panel_placeholder
        window.set_theme = lambda _theme: None  # type: ignore[method-assign]

        with (
            patch.object(MainWindow, "_log_info", lambda *args, **kwargs: None),
            patch("lspr_app.gui.experiment_control_window.ExperimentControlWindow", _FakeExperimentControlWindow),
        ):
            MainWindow._ensure_flow_panel(window)

        self.assertIs(_FakeExperimentControlWindow.created_parent, window._top_content_stack)
        self.assertIsNotNone(window._experiment_control_window)
        self.assertIn(window._experiment_control_window, window._top_content_stack.widgets)


if __name__ == "__main__":
    unittest.main()
