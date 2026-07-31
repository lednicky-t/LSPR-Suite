"""Coverage for the "Start Tracking" button's confirm-before-pause behavior
and its disablement during an active measurement recording.

See main_window_plotting.py's handle_start_tracking_button_clicked_for and
acquisition_controller.py's set_measurement_ui_locked.
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

from PyQt6.QtWidgets import QMessageBox
from lspr_app.gui.acquisition_controller import set_measurement_ui_locked
from lspr_app.gui.main_window_plotting import handle_start_tracking_button_clicked_for


class _FakeButton:
    def __init__(self, checked: bool = False) -> None:
        self._checked = checked
        self._signals_blocked = False
        self.enabled = True

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        self._checked = bool(value)

    def blockSignals(self, value: bool) -> None:
        self._signals_blocked = bool(value)

    def setEnabled(self, value: bool) -> None:
        self.enabled = bool(value)

    def setToolTip(self, text: str) -> None:
        pass


class _FakeStatusLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


def _make_window(*, tracking_active: bool, button_checked: bool):
    window = type("_FakeWindow", (), {})()
    window._sensorgram_tracking_active = tracking_active
    window.start_tracking_button = _FakeButton(checked=button_checked)
    window.status_label = _FakeStatusLabel()
    window._schedule_acquisition_state_persist = lambda: None
    return window


class StartTrackingConfirmationTests(unittest.TestCase):
    @patch("lspr_app.gui.main_window_plotting.QMessageBox.question")
    def test_starting_tracking_never_prompts(self, mock_question) -> None:
        window = _make_window(tracking_active=False, button_checked=True)

        handle_start_tracking_button_clicked_for(window, True)

        mock_question.assert_not_called()
        self.assertTrue(window._sensorgram_tracking_active)
        self.assertTrue(window.start_tracking_button.isChecked())

    @patch("lspr_app.gui.main_window_plotting.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes)
    def test_pausing_active_tracking_prompts_and_confirms(self, mock_question) -> None:
        window = _make_window(tracking_active=True, button_checked=False)

        handle_start_tracking_button_clicked_for(window, False)

        mock_question.assert_called_once()
        self.assertFalse(window._sensorgram_tracking_active)
        self.assertFalse(window.start_tracking_button.isChecked())

    @patch("lspr_app.gui.main_window_plotting.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
    def test_declining_the_prompt_keeps_tracking_active(self, mock_question) -> None:
        window = _make_window(tracking_active=True, button_checked=False)

        handle_start_tracking_button_clicked_for(window, False)

        mock_question.assert_called_once()
        # Tracking state itself was never touched - still True.
        self.assertTrue(window._sensorgram_tracking_active)
        # Button visually reverted back to checked (still tracking).
        self.assertTrue(window.start_tracking_button.isChecked())

    @patch("lspr_app.gui.main_window_plotting.QMessageBox.question")
    def test_click_while_not_actually_active_does_not_prompt(self, mock_question) -> None:
        # Defensive case: checked=False arriving while tracking was already
        # inactive (shouldn't normally happen from a real click, but the
        # guard should not spuriously prompt).
        window = _make_window(tracking_active=False, button_checked=False)

        handle_start_tracking_button_clicked_for(window, False)

        mock_question.assert_not_called()
        self.assertFalse(window._sensorgram_tracking_active)


class MeasurementLockDisablesTrackingButtonTests(unittest.TestCase):
    def _make_measurement_window(self, *, source_mode: str = "simulation"):
        window = type("_FakeWindow", (), {})()
        window.sim_resolution_spin = _FakeButton()
        window.sim_output_rate_spin = _FakeButton()
        window.source_mode = source_mode
        window._source_mode = source_mode
        window.source_tabs = type("_T", (), {"setToolTip": lambda self, text: None})()
        window.start_tracking_button = _FakeButton()
        return window

    @patch("lspr_app.gui.acquisition_controller.update_source_link_buttons", lambda window: None)
    def test_locking_for_measurement_disables_the_button(self) -> None:
        window = self._make_measurement_window()

        set_measurement_ui_locked(window, True)

        self.assertFalse(window.start_tracking_button.enabled)

    @patch("lspr_app.gui.acquisition_controller.update_source_link_buttons", lambda window: None)
    def test_unlocking_after_measurement_reenables_the_button(self) -> None:
        window = self._make_measurement_window()

        set_measurement_ui_locked(window, True)
        set_measurement_ui_locked(window, False)

        self.assertTrue(window.start_tracking_button.enabled)


if __name__ == "__main__":
    unittest.main()
