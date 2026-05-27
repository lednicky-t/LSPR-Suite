from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.acquisition_controller import _experiment_runtime_label, _experiment_runtime_state
from lspr_app.gui.main_window_titlebar import device_status_state


class MainWindowTitleBarTests(unittest.TestCase):
    def test_device_status_state_supports_discovered_devices(self) -> None:
        self.assertEqual(device_status_state(True, False), "connected")
        self.assertEqual(device_status_state(False, True), "discovered")
        self.assertEqual(device_status_state(False, False), "disconnected")

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


if __name__ == "__main__":
    unittest.main()
