"""Regression tests for MainWindow._format_busy_detail_text, the pure
text-formatting half of _update_busy_progress (elapsed/ETA/percent, plus the
"s/cube" speed readout added for analysis runs - see gui/analysis_worker_
mixin.py's _start_sensorgram_worker, which is the one caller that passes
total_items to _begin_busy). Split into a staticmethod specifically so this
math is testable without constructing a real Qt MainWindow.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.main_window import MainWindow


class TestFormatBusyDetailText(unittest.TestCase):
    def test_no_total_items_omits_speed(self) -> None:
        text = MainWindow._format_busy_detail_text(30.0, 50, None)
        self.assertEqual(text, "0:30 | ETA 0:30 | 50%")

    def test_zero_percent_shows_placeholder_eta_and_no_speed(self) -> None:
        text = MainWindow._format_busy_detail_text(5.0, 0, 100)
        self.assertEqual(text, "0:05 | ETA --:-- | 0%")

    def test_speed_reflects_items_done_from_percent(self) -> None:
        # 50% of 100 cubes = 50 done in 100s -> 2.00 s/cube.
        text = MainWindow._format_busy_detail_text(100.0, 50, 100)
        self.assertEqual(text, "1:40 | ETA 1:40 | 50%" " | 2.00 s/cube")

    def test_speed_rounds_items_done_to_at_least_one(self) -> None:
        # 1% of 100 cubes rounds to 1 item, not 0 - avoids a division by zero
        # and a nonsensical "0 cubes done in 3s" reading.
        text = MainWindow._format_busy_detail_text(3.0, 1, 100)
        self.assertTrue(text.endswith("3.00 s/cube"), msg=text)

    def test_full_run_speed_is_total_elapsed_over_total_items(self) -> None:
        text = MainWindow._format_busy_detail_text(50.0, 100, 100)
        self.assertEqual(text, "0:50 | ETA 0:00 | 100% | 0.50 s/cube")

    def test_falsy_total_items_omits_speed(self) -> None:
        # total_items=0 is meaningless (nothing to divide by) - must behave
        # exactly like total_items=None, not raise or show "0.00 s/cube".
        text = MainWindow._format_busy_detail_text(10.0, 50, 0)
        self.assertNotIn("s/cube", text)


if __name__ == "__main__":
    unittest.main()
