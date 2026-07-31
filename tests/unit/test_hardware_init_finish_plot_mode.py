"""Coverage for the plot-mode/discovery-complete verdict handle_hardware_init_finished_for
applies once device discovery is fully finished (main_window_lifecycle.py):

- The spectrum plot stays blank while scanning (see refresh_spectrum_plot_for's
  _device_discovery_complete gate) - this flag flips True here, unconditionally,
  once discovery finishes.
- If a real spectrometer was found during the *initial* startup scan,
  plot_selector is forced to "Raw" (Spectrometer mode itself was already
  switched to earlier, mid-scan, by handle_hardware_init_step_for).
- A later "Reinitialize hardware" rescan (not the initial scan) never forces
  the plot mode, even if it finds a spectrometer.
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

from lspr_app.gui.main_window_lifecycle import handle_hardware_init_finished_for


class _FakePlotSelector:
    def __init__(self, current: str = "Absorbance") -> None:
        self._current = current

    def currentText(self) -> str:
        return self._current

    def setCurrentText(self, text: str) -> None:
        self._current = text


def _make_window(*, initial_scan_pending: bool, hardware_available: bool, plot_selector_text: str = "Absorbance"):
    window = type("_FakeWindow", (), {})()
    window._hardware_init_task = "sentinel"
    window._closing = False
    window._initial_hardware_scan_pending = initial_scan_pending
    window._hardware_available = hardware_available
    window._device_discovery_complete = False
    window.plot_selector = _FakePlotSelector(plot_selector_text)
    window.calls: list[str] = []
    window._refresh_plot = lambda: window.calls.append("refresh_plot")
    window._log_warning = lambda *_args, **_kwargs: window.calls.append("log_warning")
    return window


class HardwareInitFinishPlotModeTests(unittest.TestCase):
    @patch("lspr_app.gui.main_window_lifecycle.finish_hardware_initialization_for")
    def test_real_spectrometer_on_initial_scan_forces_raw(self, _mock_finish) -> None:
        window = _make_window(initial_scan_pending=True, hardware_available=True, plot_selector_text="Absorbance")

        handle_hardware_init_finished_for(window, None)

        self.assertEqual(window.plot_selector.currentText(), "Raw")
        self.assertTrue(window._device_discovery_complete)
        self.assertIn("refresh_plot", window.calls)
        self.assertIsNone(window._hardware_init_task)

    @patch("lspr_app.gui.main_window_lifecycle.finish_hardware_initialization_for")
    def test_no_spectrometer_on_initial_scan_does_not_force_raw(self, _mock_finish) -> None:
        window = _make_window(initial_scan_pending=True, hardware_available=False, plot_selector_text="Absorbance")

        handle_hardware_init_finished_for(window, None)

        self.assertEqual(window.plot_selector.currentText(), "Absorbance")
        self.assertTrue(window._device_discovery_complete)
        self.assertIn("refresh_plot", window.calls)

    @patch("lspr_app.gui.main_window_lifecycle.finish_hardware_initialization_for")
    def test_later_rescan_does_not_force_raw_even_if_spectrometer_found(self, _mock_finish) -> None:
        # e.g. "Reinitialize hardware" run well after startup - the maintainer's
        # policy is this must not yank the user off whatever plot mode they're
        # currently looking at.
        window = _make_window(initial_scan_pending=False, hardware_available=True, plot_selector_text="Dark")

        handle_hardware_init_finished_for(window, None)

        self.assertEqual(window.plot_selector.currentText(), "Dark")
        self.assertTrue(window._device_discovery_complete)

    @patch("lspr_app.gui.main_window_lifecycle.finish_hardware_initialization_for")
    def test_discovery_complete_flips_regardless_of_outcome(self, _mock_finish) -> None:
        window = _make_window(initial_scan_pending=True, hardware_available=False)
        self.assertFalse(window._device_discovery_complete)

        handle_hardware_init_finished_for(window, None)

        self.assertTrue(window._device_discovery_complete)

    @patch("lspr_app.gui.main_window_lifecycle.finish_hardware_initialization_for")
    def test_closing_window_is_a_no_op(self, mock_finish) -> None:
        window = _make_window(initial_scan_pending=True, hardware_available=True)
        window._closing = True

        handle_hardware_init_finished_for(window, None)

        self.assertFalse(window._device_discovery_complete)
        self.assertEqual(window.calls, [])
        mock_finish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
