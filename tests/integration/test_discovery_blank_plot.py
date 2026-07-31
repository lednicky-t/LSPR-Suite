"""Coverage for the spectrum plot staying blank (with a "Scanning for
devices..." placeholder) until device discovery finishes - see
refresh_spectrum_plot_for's _device_discovery_complete gate
(main_window_plotting.py) and handle_hardware_init_finished_for
(main_window_lifecycle.py), which flips the flag.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtWidgets import QApplication

from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.session import MeasurementSession
from lspr_app.gui.main_window import MainWindow
from lspr_app.gui.main_window_plotting import refresh_spectrum_plot_for
from lspr_app.storage import user_profile as up


_APP = QApplication.instance() or QApplication([])


def _make_main_window() -> MainWindow:
    with patch.object(MainWindow, "_start_hardware_initialization", lambda self: None):
        return MainWindow(SimulatedSpectrometer(), MeasurementSession(), None)


class _IsolatedSettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        patches = [
            patch.object(up, "_SHARED_CONFIG_DIR", base),
            patch.object(up, "_REGISTRY_PATH", base / "lspr_users.json"),
            patch.object(up, "GLOBAL_CONFIG_PATH", base / "lspr_settings.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class DiscoveryBlankPlotTests(_IsolatedSettingsTestCase):
    def test_plot_stays_blank_and_shows_placeholder_before_discovery_completes(self) -> None:
        window = _make_main_window()
        self.assertFalse(window._device_discovery_complete)

        refresh_spectrum_plot_for(window, window._last_processed_plot, window._last_fit_plot)

        x_data, _y_data = window.spectrum_curve.getData()
        self.assertTrue(x_data is None or len(x_data) == 0)
        self.assertTrue(window._discovery_placeholder_text.isVisible())
        self.assertEqual(window._discovery_placeholder_text.toPlainText(), "Scanning for devices...")

    def test_placeholder_hides_once_discovery_completes(self) -> None:
        window = _make_main_window()
        refresh_spectrum_plot_for(window, None, None)
        self.assertTrue(window._discovery_placeholder_text.isVisible())

        window._device_discovery_complete = True
        refresh_spectrum_plot_for(window, None, None)

        self.assertFalse(window._discovery_placeholder_text.isVisible())


if __name__ == "__main__":
    unittest.main()
