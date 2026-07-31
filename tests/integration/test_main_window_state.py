from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtCore import QRect

# Must exist before any lspr_app.gui module is imported below - see
# test_main_window_titlebar.py for why (Qt objects built at import time).
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui import main_window_state
from lspr_app.gui.main_window_state import (
    _restore_window_geometry,
    resolve_initial_source_mode,
    schedule_acquisition_state_persist,
)
from lspr_core import LAUNCH_PROFILE_CONTROL_EDITOR, LAUNCH_PROFILE_FULL, LAUNCH_PROFILE_SIMULATION, launch_profile_spec


class _FakeTimer:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _FakeScreen:
    def __init__(self, geometry: QRect) -> None:
        self._geometry = geometry

    def availableGeometry(self) -> QRect:
        return self._geometry


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

    def test_schedule_acquisition_state_persist_starts_timer_when_enabled(self) -> None:
        # Regression: schedule_acquisition_state_persist used to only record
        # a "requested at" timestamp and never start the debounce timer, so
        # live rate / simulation rate / plot mode changes were only ever
        # persisted via the 100 ms GUI-housekeeping poll (or a clean app
        # close) instead of also having an independent timer-driven save
        # like window geometry (schedule_ui_state_persist_for) already has.
        timer = _FakeTimer()
        window = SimpleNamespace(
            _suspend_acquisition_autosave=False,
            _acquisition_state_autosave_enabled=True,
            _acquisition_state_timer=timer,
        )
        schedule_acquisition_state_persist(window)
        self.assertTrue(timer.started)
        self.assertIsNotNone(window._acquisition_state_requested_at)

    def test_schedule_acquisition_state_persist_stops_timer_when_suspended(self) -> None:
        timer = _FakeTimer()
        window = SimpleNamespace(
            _suspend_acquisition_autosave=True,
            _acquisition_state_autosave_enabled=True,
            _acquisition_state_timer=timer,
        )
        schedule_acquisition_state_persist(window)
        self.assertTrue(timer.stopped)
        self.assertFalse(timer.started)

    def test_schedule_acquisition_state_persist_stops_timer_when_autosave_disabled(self) -> None:
        timer = _FakeTimer()
        window = SimpleNamespace(
            _suspend_acquisition_autosave=False,
            _acquisition_state_autosave_enabled=False,
            _acquisition_state_timer=timer,
        )
        schedule_acquisition_state_persist(window)
        self.assertTrue(timer.stopped)
        self.assertFalse(timer.started)

    def test_restore_window_geometry_clamps_to_screen_under_saved_position(self) -> None:
        # Regression: geometry restore used to always clamp against
        # app.primaryScreen(), so a window last placed on a secondary
        # monitor (common on a multi-monitor lab bench) would be
        # shrunk/repositioned to fit the primary monitor on every restart
        # instead of being clamped against the monitor it actually lived on.
        secondary_geometry = QRect(-1920, 0, 1024, 768)
        primary_geometry = QRect(0, 0, 3840, 2160)
        fake_secondary = _FakeScreen(secondary_geometry)
        fake_primary = _FakeScreen(primary_geometry)

        window = QtWidgets.QMainWindow()
        try:
            ui_state = {"width": 2000, "height": 1500, "x": -1800, "y": 50}
            app_instance = QtWidgets.QApplication.instance()
            with patch.object(main_window_state.QGuiApplication, "screenAt", return_value=fake_secondary), patch.object(
                app_instance, "primaryScreen", return_value=fake_primary
            ):
                _restore_window_geometry(window, ui_state)

            margin = 12
            self.assertEqual(window.width(), secondary_geometry.width() - margin * 2)
            self.assertEqual(window.height(), secondary_geometry.height() - margin * 2)
            self.assertGreaterEqual(window.x(), secondary_geometry.x() + margin)
        finally:
            window.close()
            window.deleteLater()

    def test_restore_window_geometry_falls_back_to_primary_screen(self) -> None:
        primary_geometry = QRect(0, 0, 1280, 800)
        fake_primary = _FakeScreen(primary_geometry)

        window = QtWidgets.QMainWindow()
        try:
            ui_state = {"width": 5000, "height": 4000, "x": 100, "y": 100}
            app_instance = QtWidgets.QApplication.instance()
            with patch.object(main_window_state.QGuiApplication, "screenAt", return_value=None), patch.object(
                app_instance, "primaryScreen", return_value=fake_primary
            ):
                _restore_window_geometry(window, ui_state)

            margin = 12
            self.assertEqual(window.width(), primary_geometry.width() - margin * 2)
            self.assertEqual(window.height(), primary_geometry.height() - margin * 2)
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
