"""Coverage for _schedule_plot_mode_revert_to_raw (acquisition_controller.py):
after jumping the spectrum plot to Dark/Reference right after acquiring it,
it should snap back to Raw a moment later - unless the user (or another
capture) has already changed the view by then.
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

from lspr_app.gui.acquisition_controller import _schedule_plot_mode_revert_to_raw


class _FakePlotSelector:
    def __init__(self, text: str) -> None:
        self._text = text

    def currentText(self) -> str:
        return self._text

    def setCurrentText(self, text: str) -> None:
        self._text = text


def _make_window(*, plot_mode: str) -> object:
    window = type("_FakeWindow", (), {})()
    window.plot_selector = _FakePlotSelector(plot_mode)
    return window


class PlotModeRevertTests(unittest.TestCase):
    def _capture_scheduled_callback(self, window, shown_plot: str):
        calls: list[object] = []
        with patch(
            "lspr_app.gui.acquisition_controller.QTimer.singleShot",
            side_effect=lambda _delay_ms, callback: calls.append(callback),
        ):
            _schedule_plot_mode_revert_to_raw(window, shown_plot)
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_reverts_to_raw_when_still_showing_the_captured_view(self) -> None:
        window = _make_window(plot_mode="Dark")
        callback = self._capture_scheduled_callback(window, "Dark")

        callback()

        self.assertEqual(window.plot_selector.currentText(), "Raw")

    def test_does_not_revert_if_the_user_already_switched_away(self) -> None:
        window = _make_window(plot_mode="Absorbance")
        callback = self._capture_scheduled_callback(window, "Dark")

        callback()

        self.assertEqual(window.plot_selector.currentText(), "Absorbance")


if __name__ == "__main__":
    unittest.main()
