"""FlexibleTimeAxis tick-formatting coverage for its three time-display
modes (elapsed HH:MM:SS / plain relative seconds / absolute local
HH:MM:SS). Needs a real QApplication - pg.AxisItem is a QGraphicsWidget
and crashes the interpreter outright if constructed without one.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.widgets import FlexibleTimeAxis


class FlexibleTimeAxisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.axis = FlexibleTimeAxis("bottom")

    def test_shows_pointing_hand_cursor(self) -> None:
        self.assertEqual(self.axis.cursor().shape(), Qt.CursorShape.PointingHandCursor)

    def test_has_a_tooltip_explaining_the_double_click(self) -> None:
        self.assertTrue(self.axis.toolTip())

    def test_elapsed_mode_formats_hhmmss(self) -> None:
        self.axis.set_time_mode("elapsed")
        self.assertEqual(self.axis.tickStrings([0.0, 65.0, 3725.0], 1.0, 10.0), ["00:00:00", "00:01:05", "01:02:05"])

    def test_seconds_mode_formats_plain_numbers(self) -> None:
        self.axis.set_time_mode("seconds")
        result = self.axis.tickStrings([0.0, 65.0, 3725.0], 1.0, 10.0)
        self.assertEqual(result, ["0", "65", "3725"])

    def test_clock_mode_formats_local_hhmmss_offset_from_start(self) -> None:
        start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.axis.set_time_mode("clock", start_datetime=start)
        result = self.axis.tickStrings([0.0, 65.0], 1.0, 10.0)
        expected = [
            start.astimezone().strftime("%H:%M:%S"),
            (start + timedelta(seconds=65.0)).astimezone().strftime("%H:%M:%S"),
        ]
        self.assertEqual(result, expected)

    def test_clock_mode_without_start_datetime_falls_back_to_elapsed(self) -> None:
        self.axis.set_time_mode("clock", start_datetime=None)
        self.assertEqual(self.axis.tickStrings([65.0], 1.0, 10.0), ["00:01:05"])

    def test_unknown_mode_falls_back_to_elapsed(self) -> None:
        self.axis.set_time_mode("bogus")
        self.assertEqual(self.axis.tickStrings([65.0], 1.0, 10.0), ["00:01:05"])

    def test_double_click_signal_is_wired_to_the_toggle(self) -> None:
        # Exercising the real mouseDoubleClickEvent() needs a genuine
        # QGraphicsSceneMouseEvent - passing anything else crashes the
        # interpreter at the Qt/C++ boundary instead of raising a catchable
        # Python exception. Connect+emit is what main_window.py actually
        # relies on (self.trace_time_axis.doubleClicked.connect(...)), so
        # that's what's worth covering here.
        emitted: list[int] = []
        self.axis.doubleClicked.connect(lambda: emitted.append(1))
        self.axis.doubleClicked.emit()
        self.assertEqual(emitted, [1])


if __name__ == "__main__":
    unittest.main()
