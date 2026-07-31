"""Coverage for the spectrometer auto-connect policy and the tab link-icon
refresh, both in handle_hardware_init_step_for (main_window_lifecycle.py).

Two related bugs, reported together by the maintainer:

1. The Spectrometer/Simulation tab header link icon (see main_window_headers.py's
   source_link_button_icon: gray = not available, red = available but not the
   active feed, green = active) only repainted incidentally (e.g. locking the
   UI for a measurement) - never when _hardware_available itself changed as a
   result of the background startup hardware scan. So a real spectrometer
   detected at launch left the icon stuck on stale gray until something else
   happened to trigger a repaint.

2. Nothing ever automatically switched the active source back to "spectrometer"
   when one was found during startup - the app always starts on the simulated
   placeholder (see main_window.py's construction-time fallback) and stayed
   there even once a real device was detected. The maintainer's explicit
   policy: no spectrometer at launch -> stay on simulation; spectrometer found
   during that *same* initial scan -> auto-connect it; spectrometer connected
   *later* (e.g. via "Reinitialize hardware" after already running a while) ->
   do not auto-switch, since simulation may be in use deliberately by then.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[2] / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.device_lifecycle import DeviceLifecycleEvent, STAGE_READY, STAGE_MISSING
from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.gui.main_window_lifecycle import handle_hardware_init_step_for


class _FakeRealSpectrometer:
    def capabilities(self):
        return object()

    def device_name(self) -> str:
        return "FakeOcean"


def _make_window(*, source_mode: str, initial_scan_pending: bool, hardware_available: bool = False):
    window = type("_FakeWindow", (), {})()
    window.status_label = type("_L", (), {"setText": lambda self, text: None})()
    window._device_activity_text = {}
    window._spectrometer = SimulatedSpectrometer()
    window._hardware_available = hardware_available
    window._source_mode = source_mode
    window._initial_hardware_scan_pending = initial_scan_pending
    window._measurement_active = False
    window.calls: list[str] = []
    return window


class SpectrometerAutoConnectTests(unittest.TestCase):
    @patch("lspr_app.gui.main_window_lifecycle.refresh_hw_device_status_strip", lambda window: None)
    @patch("lspr_app.gui.main_window_lifecycle.apply_source_mode_for")
    @patch("lspr_app.gui.main_window_lifecycle.update_source_link_buttons")
    def test_real_spectrometer_found_during_initial_scan_auto_connects(self, mock_update_icons, mock_apply_mode) -> None:
        window = _make_window(source_mode="simulation", initial_scan_pending=True)
        real_spectrometer = _FakeRealSpectrometer()
        event = DeviceLifecycleEvent(device_key="spectrometer", stage=STAGE_READY, message="ready", probe=real_spectrometer)

        handle_hardware_init_step_for(window, event)

        self.assertTrue(window._hardware_available)
        self.assertIs(window._spectrometer, real_spectrometer)
        mock_apply_mode.assert_called_once_with(window, "spectrometer", restart_live=True)

    @patch("lspr_app.gui.main_window_lifecycle.refresh_hw_device_status_strip", lambda window: None)
    @patch("lspr_app.gui.main_window_lifecycle.apply_source_mode_for")
    @patch("lspr_app.gui.main_window_lifecycle.update_source_link_buttons")
    def test_icon_refresh_happens_even_when_nothing_is_found(self, mock_update_icons, mock_apply_mode) -> None:
        window = _make_window(source_mode="simulation", initial_scan_pending=True)
        event = DeviceLifecycleEvent(device_key="spectrometer", stage=STAGE_MISSING, message="not found", probe=None)

        handle_hardware_init_step_for(window, event)

        self.assertFalse(window._hardware_available)
        mock_update_icons.assert_called_once_with(window)
        mock_apply_mode.assert_not_called()

    @patch("lspr_app.gui.main_window_lifecycle.refresh_hw_device_status_strip", lambda window: None)
    @patch("lspr_app.gui.main_window_lifecycle.apply_source_mode_for")
    @patch("lspr_app.gui.main_window_lifecycle.update_source_link_buttons")
    def test_spectrometer_found_after_initial_scan_does_not_auto_connect(self, mock_update_icons, mock_apply_mode) -> None:
        # e.g. "Reinitialize hardware" run later, after the app has already
        # been running on simulation for a while - must not yank the user
        # off simulation without being asked.
        window = _make_window(source_mode="simulation", initial_scan_pending=False)
        real_spectrometer = _FakeRealSpectrometer()
        event = DeviceLifecycleEvent(device_key="spectrometer", stage=STAGE_READY, message="ready", probe=real_spectrometer)

        handle_hardware_init_step_for(window, event)

        self.assertTrue(window._hardware_available)
        self.assertIs(window._spectrometer, real_spectrometer)
        mock_apply_mode.assert_not_called()
        mock_update_icons.assert_called_once_with(window)

    @patch("lspr_app.gui.main_window_lifecycle.refresh_hw_device_status_strip", lambda window: None)
    @patch("lspr_app.gui.main_window_lifecycle.apply_source_mode_for")
    @patch("lspr_app.gui.main_window_lifecycle.update_source_link_buttons")
    def test_already_on_spectrometer_mode_does_not_redundantly_switch(self, mock_update_icons, mock_apply_mode) -> None:
        window = _make_window(source_mode="spectrometer", initial_scan_pending=True)
        real_spectrometer = _FakeRealSpectrometer()
        event = DeviceLifecycleEvent(device_key="spectrometer", stage=STAGE_READY, message="ready", probe=real_spectrometer)

        handle_hardware_init_step_for(window, event)

        mock_apply_mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
