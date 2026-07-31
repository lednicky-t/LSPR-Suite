"""Coverage for two bugs found while testing the app: switching source mode
(apply_source_mode_for, gui/main_window_state.py) used to leave source_tabs
showing the previous mode when the switch was triggered programmatically
(e.g. hardware auto-detect), and left the Dark/Reference button icons -
and the Dark/Reference plot views - showing the *other* session's stale
captured state, since window._session swaps to a different
MeasurementSession with its own independent dark/reference/absorbance.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np
from PyQt6.QtWidgets import QApplication

from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.models import Spectrum
from lspr_app.domain.session import MeasurementSession
from lspr_app.gui.icon_helpers import dark_icon, reference_icon
from lspr_app.gui.main_window import MainWindow
from lspr_app.storage import user_profile as up


_APP = QApplication.instance() or QApplication([])


def _make_spectrum(kind: str) -> Spectrum:
    return Spectrum(
        wavelengths_nm=np.asarray([400.0, 500.0, 600.0], dtype=np.float64),
        values=np.asarray([100.0, 200.0, 300.0], dtype=np.float64),
        y_label="Intensity (counts)",
        acquired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata={"kind": kind},
    )


def _make_main_window() -> MainWindow:
    with patch.object(MainWindow, "_start_hardware_initialization", lambda self: None):
        return MainWindow(SimulatedSpectrometer(), MeasurementSession(), None)


def _icon_image(icon):
    return icon.pixmap(24, 24).toImage()


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


class SourceTabsSyncTests(_IsolatedSettingsTestCase):
    def test_programmatic_switch_to_spectrometer_moves_the_tab(self) -> None:
        # Simulates handle_hardware_init_step_for's auto-connect path, which
        # calls apply_source_mode_for directly - not through a tab/link
        # click, which is what used to sync source_tabs itself.
        window = _make_main_window()
        self.assertEqual(window.source_tabs.currentIndex(), 1)  # starts on Simulation

        window._apply_source_mode("spectrometer", False)

        self.assertEqual(window.source_tabs.currentIndex(), 0)

    def test_programmatic_switch_to_simulation_moves_the_tab_back(self) -> None:
        window = _make_main_window()
        window._apply_source_mode("spectrometer", False)
        self.assertEqual(window.source_tabs.currentIndex(), 0)

        window._apply_source_mode("simulation", False)

        self.assertEqual(window.source_tabs.currentIndex(), 1)


class DarkReferenceIconResyncOnModeSwitchTests(_IsolatedSettingsTestCase):
    def test_icons_reflect_the_newly_active_sessions_state_not_the_previous_ones(self) -> None:
        window = _make_main_window()
        window._apply_source_mode("spectrometer", False)

        # Nothing captured on the hardware session yet.
        self.assertEqual(
            _icon_image(window.acquire_dark_button.icon()),
            _icon_image(dark_icon(False)),
        )

        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._update_dark_reference_button_icons()
        self.assertEqual(
            _icon_image(window.acquire_dark_button.icon()),
            _icon_image(dark_icon(True)),
        )
        self.assertEqual(
            _icon_image(window.acquire_reference_button.icon()),
            _icon_image(reference_icon(True)),
        )

        # Switch to Simulation - a different session, nothing captured there.
        window._apply_source_mode("simulation", False)
        self.assertEqual(
            _icon_image(window.acquire_dark_button.icon()),
            _icon_image(dark_icon(False)),
        )
        self.assertEqual(
            _icon_image(window.acquire_reference_button.icon()),
            _icon_image(reference_icon(False)),
        )

        # Switch back to Spectrometer - the earlier capture is still there.
        window._apply_source_mode("spectrometer", False)
        self.assertEqual(
            _icon_image(window.acquire_dark_button.icon()),
            _icon_image(dark_icon(True)),
        )
        self.assertEqual(
            _icon_image(window.acquire_reference_button.icon()),
            _icon_image(reference_icon(True)),
        )

    def test_dark_and_reference_plot_data_follows_the_active_session(self) -> None:
        window = _make_main_window()
        window._apply_source_mode("spectrometer", False)
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))

        self.assertIsNotNone(window._session.get_plot_data("dark"))
        self.assertIsNotNone(window._session.get_plot_data("reference"))

        window._apply_source_mode("simulation", False)
        # The simulation session never had dark/reference captured on it.
        self.assertIsNone(window._session.get_plot_data("dark"))
        self.assertIsNone(window._session.get_plot_data("reference"))

        window._apply_source_mode("spectrometer", False)
        self.assertIsNotNone(window._session.get_plot_data("dark"))
        self.assertIsNotNone(window._session.get_plot_data("reference"))


if __name__ == "__main__":
    unittest.main()
