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

    def test_recent_rate_overrides_whole_run_average(self) -> None:
        # Whole-run average would say 2.00 s/cube (100s/50 cubes); a supplied
        # recent-window rate should win instead - this is the whole point of
        # BUSY_PROGRESS_SPEED_WINDOW_SECONDS (see its docstring): a run that
        # started with a burst of near-instant cache-hit cubes shouldn't keep
        # reporting that fast average once it's grinding through slow,
        # freshly-computed ones.
        text = MainWindow._format_busy_detail_text(100.0, 50, 100, recent_seconds_per_item=5.5)
        self.assertEqual(text, "1:40 | ETA 1:40 | 50% | 5.50 s/cube")

    def test_recent_rate_none_falls_back_to_whole_run_average(self) -> None:
        text = MainWindow._format_busy_detail_text(100.0, 50, 100, recent_seconds_per_item=None)
        self.assertEqual(text, "1:40 | ETA 1:40 | 50% | 2.00 s/cube")

    def test_recent_rate_zero_or_negative_falls_back_to_whole_run_average(self) -> None:
        # A zero/negative rate is meaningless (not enough of a time span yet -
        # see _recent_busy_progress_rate) - must not divide-by-zero or show
        # a nonsensical negative speed.
        for bogus_rate in (0.0, -1.0):
            with self.subTest(bogus_rate=bogus_rate):
                text = MainWindow._format_busy_detail_text(100.0, 50, 100, recent_seconds_per_item=bogus_rate)
                self.assertEqual(text, "1:40 | ETA 1:40 | 50% | 2.00 s/cube")


class TestRecentBusyProgressRate(unittest.TestCase):
    """_recent_busy_progress_rate doesn't touch any Qt widget - safe to call
    on an otherwise-unconstructed MainWindow.__new__() instance with just the
    handful of attributes it actually reads/writes."""

    def _make_window(self, total_items: int | None) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window._busy_total_items = total_items
        window._busy_progress_window = __import__("collections").deque()
        return window

    def test_no_total_items_returns_none(self) -> None:
        window = self._make_window(None)
        self.assertIsNone(window._recent_busy_progress_rate(5.0, 50))

    def test_single_sample_returns_none(self) -> None:
        # Not enough history yet to compute a window-spanning rate.
        window = self._make_window(100)
        self.assertIsNone(window._recent_busy_progress_rate(2.0, 20))

    def test_short_time_span_returns_none(self) -> None:
        # Two samples less than 1 second apart aren't a stable enough basis
        # for a rate - falls back to the whole-run average instead.
        window = self._make_window(100)
        window._recent_busy_progress_rate(2.0, 20)
        self.assertIsNone(window._recent_busy_progress_rate(2.4, 22))

    def test_computes_rate_from_window_span(self) -> None:
        # 10 items done over 5 seconds (elapsed 2.0 -> 7.0, percent 20 -> 30
        # of 100 total) -> 0.5 s/item.
        window = self._make_window(100)
        window._recent_busy_progress_rate(2.0, 20)
        rate = window._recent_busy_progress_rate(7.0, 30)
        self.assertAlmostEqual(rate, 0.5, places=6)

    def test_old_samples_age_out_of_the_window(self) -> None:
        # A fast burst (cache hits) followed by a slow stretch (fresh
        # compute) should reflect the slow stretch once the fast samples
        # fall outside BUSY_PROGRESS_SPEED_WINDOW_SECONDS (10s) - not an
        # average blending both regimes together.
        window = self._make_window(100)
        window._recent_busy_progress_rate(0.0, 0)
        window._recent_busy_progress_rate(0.1, 50)  # 50 "cubes" in 0.1s - cache hits
        # Now a slow stretch: 5 more items over the next 12 seconds.
        window._recent_busy_progress_rate(6.0, 52)
        rate = window._recent_busy_progress_rate(12.1, 55)
        # The t=0.0 and t=0.1 samples are now more than 10s behind t=12.1,
        # so they should have aged out - only the slow-stretch samples
        # (t=6.0 -> t=12.1, 3 items over 6.1s) should remain.
        self.assertAlmostEqual(rate, 6.1 / 3.0, places=3)


if __name__ == "__main__":
    unittest.main()
