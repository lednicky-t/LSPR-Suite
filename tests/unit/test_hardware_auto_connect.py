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
   there even once a real device was detected. The maintainer's policy: no
   spectrometer at launch -> stay on simulation; spectrometer found during
   that *same* initial scan -> auto-connect it; spectrometer connected
   *later* (e.g. via "Reinitialize hardware" after already running a while)
   -> only auto-switch if the tool panel (source tabs) is currently visible,
   since that's a reasonable signal the user is actively looking at source
   selection rather than deliberately running simulation with the panel
   tucked away.
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


class _FakeScrollArea:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    def isVisible(self) -> bool:
        return self._visible


def _make_window(*, source_mode: str, initial_scan_pending: bool, hardware_available: bool = False, tool_panel_visible: bool = False, live_active: bool = False):
    window = type("_FakeWindow", (), {})()
    window.status_label = type("_L", (), {"setText": lambda self, text: None})()
    window._device_activity_text = {}
    window._spectrometer = SimulatedSpectrometer()
    window._hardware_available = hardware_available
    window._source_mode = source_mode
    window._initial_hardware_scan_pending = initial_scan_pending
    window._left_controls_scroll = _FakeScrollArea(tool_panel_visible)
    window._measurement_active = False
    window._live_active = live_active
    window.calls: list[str] = []
    window._stop_live_acquisition = lambda message="": window.calls.append(f"stop_live_acquisition:{message}")
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
    def test_spectrometer_found_after_initial_scan_does_not_auto_connect_with_panel_hidden(self, mock_update_icons, mock_apply_mode) -> None:
        # e.g. "Reinitialize hardware" run later, after the app has already
        # been running on simulation for a while, tool panel tucked away -
        # must not yank the user off simulation without being asked.
        window = _make_window(source_mode="simulation", initial_scan_pending=False, tool_panel_visible=False)
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
    def test_spectrometer_found_after_initial_scan_does_auto_connect_with_panel_visible(self, mock_update_icons, mock_apply_mode) -> None:
        # Same later-scan scenario, but the tool panel (source tabs) is
        # currently visible - the maintainer's requested signal that the
        # user is actively looking at source selection right now, so this
        # case should auto-switch same as the initial-scan case.
        window = _make_window(source_mode="simulation", initial_scan_pending=False, tool_panel_visible=True)
        real_spectrometer = _FakeRealSpectrometer()
        event = DeviceLifecycleEvent(device_key="spectrometer", stage=STAGE_READY, message="ready", probe=real_spectrometer)

        handle_hardware_init_step_for(window, event)

        self.assertTrue(window._hardware_available)
        mock_apply_mode.assert_called_once_with(window, "spectrometer", restart_live=True)

    @patch("lspr_app.gui.main_window_lifecycle.refresh_hw_device_status_strip", lambda window: None)
    @patch("lspr_app.gui.main_window_lifecycle.apply_source_mode_for")
    @patch("lspr_app.gui.main_window_lifecycle.update_source_link_buttons")
    def test_auto_connect_stops_a_still_running_simulation_worker_first(self, mock_update_icons, mock_apply_mode) -> None:
        # Regression test: simulation auto-starts its live worker on launch/
        # mode-switch, so it is almost always still running when a
        # spectrometer is discovered later (e.g. "Reinitialize hardware").
        # start_live_acquisition() no-ops while _live_active is already True,
        # so apply_source_mode_for(..., restart_live=True) alone silently
        # failed to actually start a spectrometer-mode worker - the GUI
        # flipped to "spectrometer" while the stale simulation worker kept
        # running underneath, showing no raw spectra until the user manually
        # toggled source tabs (which stops-then-restarts correctly). Must
        # stop the live worker before switching source, same as
        # request_source_mode_switch (main_window_headers.py) already does.
        window = _make_window(source_mode="simulation", initial_scan_pending=False, tool_panel_visible=True, live_active=True)
        real_spectrometer = _FakeRealSpectrometer()
        event = DeviceLifecycleEvent(device_key="spectrometer", stage=STAGE_READY, message="ready", probe=real_spectrometer)

        handle_hardware_init_step_for(window, event)

        self.assertEqual(window.calls, ["stop_live_acquisition:Switching source..."])
        mock_apply_mode.assert_called_once_with(window, "spectrometer", restart_live=True)

    @patch("lspr_app.gui.main_window_lifecycle.refresh_hw_device_status_strip", lambda window: None)
    @patch("lspr_app.gui.main_window_lifecycle.apply_source_mode_for")
    @patch("lspr_app.gui.main_window_lifecycle.update_source_link_buttons")
    def test_auto_connect_does_not_stop_live_when_nothing_is_running(self, mock_update_icons, mock_apply_mode) -> None:
        window = _make_window(source_mode="simulation", initial_scan_pending=True, live_active=False)
        real_spectrometer = _FakeRealSpectrometer()
        event = DeviceLifecycleEvent(device_key="spectrometer", stage=STAGE_READY, message="ready", probe=real_spectrometer)

        handle_hardware_init_step_for(window, event)

        self.assertEqual(window.calls, [])
        mock_apply_mode.assert_called_once_with(window, "spectrometer", restart_live=True)

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
