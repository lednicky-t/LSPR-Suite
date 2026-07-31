"""Regression coverage for trace_stats_label's two independent click
behaviors: a single left-click cycles the displayed metric, a double-click
hides/shows the whole stats box (see event_filter_for in
main_window_lifecycle.py).

Qt delivers a double-click as Press, Release, DblClick, Release - so the
first Press of a double-click always arrives on its own first and would fire
the single-click cycle as an unwanted side effect right before the box gets
hidden. The fix delays the single-click action by
QApplication.doubleClickInterval() and bumps a token on every click so a
stale delayed callback can tell it's been superseded - see
_handle_trace_stats_delayed_single_click (main_window.py). These tests drive
that token logic directly instead of waiting out a real timer.
"""
from __future__ import annotations

import sys
import unittest

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from unittest.mock import patch

from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.session import MeasurementSession
from lspr_app.gui.main_window import MainWindow
from lspr_app.gui.main_window_lifecycle import event_filter_for


class _FakeClickEvent:
    def __init__(self, event_type: QEvent.Type) -> None:
        self._type = event_type

    def type(self):
        return self._type

    def button(self):
        return Qt.MouseButton.LeftButton


class TraceStatsDoubleClickDebounceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        with patch.object(MainWindow, "_start_hardware_initialization", lambda self: None):
            self.window = MainWindow(SimulatedSpectrometer(), MeasurementSession(), None)
        self.cycled: list[bool] = []
        self.toggled: list[bool] = []
        self.window._cycle_trace_stats_metric = lambda: self.cycled.append(True)
        self.window._toggle_trace_stats_enabled = lambda: self.toggled.append(True)

    def test_single_press_does_not_cycle_immediately(self) -> None:
        result = event_filter_for(
            self.window, self.window.trace_stats_label, _FakeClickEvent(QEvent.Type.MouseButtonPress)
        )

        self.assertTrue(result)
        self.assertEqual(self.cycled, [])

    def test_a_press_followed_by_a_dblclick_toggles_without_cycling(self) -> None:
        event_filter_for(self.window, self.window.trace_stats_label, _FakeClickEvent(QEvent.Type.MouseButtonPress))
        stale_token = self.window._trace_stats_click_token
        event_filter_for(self.window, self.window.trace_stats_label, _FakeClickEvent(QEvent.Type.MouseButtonDblClick))

        # Simulate the delayed single-click callback finally firing with the
        # token captured before the double-click superseded it.
        self.window._handle_trace_stats_delayed_single_click(stale_token)

        self.assertEqual(self.toggled, [True])
        self.assertEqual(self.cycled, [])

    def test_a_genuine_single_click_still_cycles_once_the_delay_elapses(self) -> None:
        event_filter_for(self.window, self.window.trace_stats_label, _FakeClickEvent(QEvent.Type.MouseButtonPress))
        current_token = self.window._trace_stats_click_token

        self.window._handle_trace_stats_delayed_single_click(current_token)

        self.assertEqual(self.cycled, [True])
        self.assertEqual(self.toggled, [])

    def test_spectrum_stats_dblclick_toggles_directly(self) -> None:
        spectrum_toggled: list[bool] = []
        self.window._toggle_spectrum_stats_enabled = lambda: spectrum_toggled.append(True)

        result = event_filter_for(
            self.window, self.window.spectrum_stats_label, _FakeClickEvent(QEvent.Type.MouseButtonDblClick)
        )

        self.assertTrue(result)
        self.assertEqual(spectrum_toggled, [True])


if __name__ == "__main__":
    unittest.main()
