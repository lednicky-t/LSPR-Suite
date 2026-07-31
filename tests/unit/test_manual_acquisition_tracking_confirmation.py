"""Coverage for the "capture new Dark/Reference while tracking is active"
confirmation prompt - request_manual_acquisition (gui/acquisition_controller.py).
Mirrors the existing pause-tracking confirmation
(handle_start_tracking_button_clicked_for, tests/unit/test_start_tracking_button.py):
declining must be a true no-op, and the prompt must not appear at all when
nothing is being tracked.
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
from lspr_app.gui.acquisition_controller import request_manual_acquisition


class _FakeStatusLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


def _make_window(*, tracking_active: bool, busy: bool = True) -> object:
    # busy=True by default routes past the confirmation gate into the
    # shallow "queue while busy" branch, which needs no further window
    # state - the narrowest safe landing spot for testing the gate itself.
    window = type("_FakeWindow", (), {})()
    window._sensorgram_tracking_active = tracking_active
    window._live_active = False
    window._busy = busy
    window._live_worker = None
    window._pending_manual_kind = None
    window.status_label = _FakeStatusLabel()
    window.calls: list[str] = []
    window._log_info = lambda *_args, **_kwargs: window.calls.append("log_info")
    window._log_warning = lambda *_args, **_kwargs: window.calls.append("log_warning")
    return window


class ManualAcquisitionTrackingConfirmationTests(unittest.TestCase):
    @patch("lspr_app.gui.acquisition_controller.QMessageBox.question")
    def test_no_prompt_when_nothing_is_tracked(self, mock_question) -> None:
        window = _make_window(tracking_active=False)

        request_manual_acquisition(window, "dark")

        mock_question.assert_not_called()
        self.assertEqual(window._pending_manual_kind, "dark")

    @patch("lspr_app.gui.acquisition_controller.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
    def test_declining_the_prompt_is_a_true_no_op(self, mock_question) -> None:
        window = _make_window(tracking_active=True)

        request_manual_acquisition(window, "reference")

        mock_question.assert_called_once()
        self.assertIsNone(window._pending_manual_kind)
        self.assertNotIn("log_info", window.calls)

    @patch("lspr_app.gui.acquisition_controller.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes)
    def test_confirming_the_prompt_proceeds(self, mock_question) -> None:
        window = _make_window(tracking_active=True)

        request_manual_acquisition(window, "dark")

        mock_question.assert_called_once()
        self.assertEqual(window._pending_manual_kind, "dark")
        self.assertIn("log_info", window.calls)


if __name__ == "__main__":
    unittest.main()
