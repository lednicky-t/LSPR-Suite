"""Coverage for start_tracking_button's ready-to-track indicator and enabled
state. Only the Absorbance spectrum is ever trackable (see
spectral_processing_pipeline_architecture.md):

- The icon fills solid green and alpha-pulses (see gui/icon_helpers.py::
  tracking_ready_icon and MainWindow._refresh_tracking_ready_indicator) once
  Absorbance is available (dark+reference+sample in Spectrometer mode, or
  simulation output wired directly in Simulation mode) and tracking hasn't
  started yet.
- The button itself is only *enabled* while plot_selector is showing
  Absorbance and Absorbance is available (MainWindow.
  _refresh_start_tracking_button_enabled) - never for Raw/Dark/Reference,
  even if Absorbance happens to be available too.

MainWindow is constructed with a SimulatedSpectrometer, so it starts in
Simulation mode (with Absorbance already wired from the construction-time
seed) - tests that exercise Spectrometer-mode dark/reference/sample
plumbing explicitly switch source mode first.
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
from lspr_app.gui.icon_helpers import transport_icon
from lspr_app.gui.main_window import MainWindow
from lspr_app.gui.main_window_plotting import set_sensorgram_tracking_active_for
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


def _make_spectrometer_mode_window() -> MainWindow:
    """A window switched to Spectrometer mode, with a clean (un-primed)
    hardware session - dark/reference/Raw are visible/selectable and
    Absorbance isn't available until captured, same as real hardware.
    """
    window = _make_main_window()
    window._apply_source_mode("spectrometer", False)
    return window


def _icon_image(icon):
    return icon.pixmap(24, 24).toImage()


class _IsolatedSettingsTestCase(unittest.TestCase):
    """Same isolation as test_main_window_settings_undo.py's base class -
    MainWindow reads/writes the machine-wide lspr_settings.json otherwise.
    """

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


class SpectrometerModeTrackingReadyTests(_IsolatedSettingsTestCase):
    def test_defaults_to_plain_play_icon_with_nothing_captured(self) -> None:
        window = _make_spectrometer_mode_window()
        self.assertFalse(window._tracking_ready_blink_timer.isActive())
        self.assertEqual(
            _icon_image(window.start_tracking_button.icon()),
            _icon_image(transport_icon(window._theme_mode, "play")),
        )

    def test_dark_and_reference_alone_does_not_start_the_blink(self) -> None:
        # Absorbance needs dark+reference+sample - not just dark+reference.
        window = _make_spectrometer_mode_window()
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._update_dark_reference_button_icons()
        self.assertFalse(window._tracking_ready_blink_timer.isActive())

    def test_dark_reference_and_sample_starts_the_blink_and_fills_the_icon(self) -> None:
        window = _make_spectrometer_mode_window()
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._session.set_sample(_make_spectrum("sample"))
        window._update_dark_reference_button_icons()

        self.assertTrue(window._tracking_ready_blink_timer.isActive())
        self.assertNotEqual(
            _icon_image(window.start_tracking_button.icon()),
            _icon_image(transport_icon(window._theme_mode, "play")),
        )

    def test_blink_alternates_the_icon_alpha(self) -> None:
        window = _make_spectrometer_mode_window()
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._session.set_sample(_make_spectrum("sample"))
        window._update_dark_reference_button_icons()

        first = _icon_image(window.start_tracking_button.icon())
        window._advance_tracking_ready_blink_indicator()
        second = _icon_image(window.start_tracking_button.icon())
        window._advance_tracking_ready_blink_indicator()
        third = _icon_image(window.start_tracking_button.icon())

        self.assertNotEqual(first, second)
        self.assertEqual(first, third)

    def test_starting_tracking_stops_the_blink_and_shows_pause(self) -> None:
        window = _make_spectrometer_mode_window()
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._session.set_sample(_make_spectrum("sample"))
        window._update_dark_reference_button_icons()

        set_sensorgram_tracking_active_for(window, True)

        self.assertFalse(window._tracking_ready_blink_timer.isActive())
        self.assertEqual(
            _icon_image(window.start_tracking_button.icon()),
            _icon_image(transport_icon(window._theme_mode, "pause")),
        )

    def test_pausing_tracking_resumes_the_blink_if_still_ready(self) -> None:
        window = _make_spectrometer_mode_window()
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._session.set_sample(_make_spectrum("sample"))
        window._update_dark_reference_button_icons()

        set_sensorgram_tracking_active_for(window, True)
        set_sensorgram_tracking_active_for(window, False)

        self.assertTrue(window._tracking_ready_blink_timer.isActive())
        self.assertNotEqual(
            _icon_image(window.start_tracking_button.icon()),
            _icon_image(transport_icon(window._theme_mode, "play")),
        )

    def test_button_disabled_until_absorbance_is_available(self) -> None:
        window = _make_spectrometer_mode_window()
        self.assertFalse(window.start_tracking_button.isEnabled())
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._session.set_sample(_make_spectrum("sample"))
        window._update_dark_reference_button_icons()
        self.assertTrue(window.start_tracking_button.isEnabled())

    def test_button_disabled_while_viewing_raw_even_with_absorbance_available(self) -> None:
        window = _make_spectrometer_mode_window()
        window._session.set_dark(_make_spectrum("dark"))
        window._session.set_reference(_make_spectrum("reference"))
        window._session.set_sample(_make_spectrum("sample"))
        window._update_dark_reference_button_icons()
        self.assertTrue(window.start_tracking_button.isEnabled())

        window.plot_selector.setCurrentText("Raw")

        self.assertFalse(window.start_tracking_button.isEnabled())

        window.plot_selector.setCurrentText("Absorbance")

        self.assertTrue(window.start_tracking_button.isEnabled())


class SimulationModeTrackingReadyTests(_IsolatedSettingsTestCase):
    def test_simulation_mode_starts_tracking_automatically(self) -> None:
        # Construction already switches to Simulation mode (SimulatedSpectrometer
        # arg) and wires the seeded output directly to Absorbance - no
        # set_dark/set_reference/set_sample involved at all. Simulation has no
        # dark/reference ceremony and no real prep-procedure "trash" to gate
        # out, so apply_source_mode_for starts tracking immediately instead
        # of waiting for a manual click (see main_window_state.py).
        window = _make_main_window()

        self.assertEqual(window._source_mode, "simulation")
        self.assertIsNotNone(window._session.state.absorbance)
        self.assertEqual(window.PLOT_MODES.get(window.plot_selector.currentText()), "absorbance")
        self.assertTrue(window._sensorgram_tracking_active)
        # Nothing left to prompt the user about - tracking already started,
        # so the ready-to-track blink has nothing to indicate.
        self.assertFalse(window._tracking_ready_blink_timer.isActive())
        self.assertEqual(
            _icon_image(window.start_tracking_button.icon()),
            _icon_image(transport_icon(window._theme_mode, "pause")),
        )

    def test_dark_reference_raw_hidden_in_simulation_mode(self) -> None:
        # isHidden() (the widget's own explicit-hide flag), not isVisible()
        # (composited with ancestor/top-level shown state) - the test window
        # is never actually shown, so isVisible() would be False regardless
        # of what setVisible() was called with.
        window = _make_main_window()
        self.assertTrue(window.acquire_dark_button.isHidden())
        self.assertTrue(window.acquire_reference_button.isHidden())
        # start_tracking_button has nothing to do in simulation either -
        # tracking is already running automatically (see the test above).
        self.assertTrue(window.start_tracking_button.isHidden())
        for name in ("Raw", "Reference", "Dark"):
            self.assertFalse(window.plot_selector._actions[name].isVisible())

    def test_switching_back_to_spectrometer_restores_dark_reference_raw(self) -> None:
        window = _make_main_window()
        window._apply_source_mode("spectrometer", False)
        self.assertFalse(window.acquire_dark_button.isHidden())
        self.assertFalse(window.acquire_reference_button.isHidden())
        self.assertFalse(window.start_tracking_button.isHidden())
        for name in ("Raw", "Reference", "Dark"):
            self.assertTrue(window.plot_selector._actions[name].isVisible())
        # Leaving simulation restores the manual-gated off-by-default
        # behaviour, so real acquisition still keeps prep-time data out of
        # the sensorgram by default.
        self.assertFalse(window._sensorgram_tracking_active)


if __name__ == "__main__":
    unittest.main()
