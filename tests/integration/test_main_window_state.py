from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window_state import resolve_initial_source_mode
from lspr_core import LAUNCH_PROFILE_CONTROL_EDITOR, LAUNCH_PROFILE_FULL, LAUNCH_PROFILE_SIMULATION, launch_profile_spec


class MainWindowStateTests(unittest.TestCase):
    def test_full_profile_prefers_spectrometer_when_hardware_is_available(self) -> None:
        window = SimpleNamespace(
            _launch_profile_spec=launch_profile_spec(LAUNCH_PROFILE_FULL),
            _hardware_available=True,
        )
        self.assertEqual(resolve_initial_source_mode(window, "simulation"), "spectrometer")

    def test_full_profile_falls_back_to_simulation_when_hardware_is_unavailable(self) -> None:
        window = SimpleNamespace(
            _launch_profile_spec=launch_profile_spec(LAUNCH_PROFILE_FULL),
            _hardware_available=False,
        )
        self.assertEqual(resolve_initial_source_mode(window, "spectrometer"), "simulation")

    def test_simulation_profile_stays_simulation(self) -> None:
        window = SimpleNamespace(
            _launch_profile_spec=launch_profile_spec(LAUNCH_PROFILE_SIMULATION),
            _hardware_available=True,
        )
        self.assertEqual(resolve_initial_source_mode(window, "spectrometer"), "simulation")

    def test_control_editor_preserves_requested_source_when_valid(self) -> None:
        window = SimpleNamespace(
            _launch_profile_spec=launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR),
            _hardware_available=True,
        )
        self.assertEqual(resolve_initial_source_mode(window, "spectrometer"), "spectrometer")
        self.assertEqual(resolve_initial_source_mode(window, "simulation"), "simulation")


if __name__ == "__main__":
    unittest.main()
