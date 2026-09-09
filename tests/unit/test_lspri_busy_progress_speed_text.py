"""Regression tests for MainWindow._format_busy_detail_text/_note_busy_item_
completed, the "Curr/Avg s/cube" speed readout for analysis runs (see
gui/analysis_worker_mixin.py's _start_sensorgram_worker, which is the one
caller that passes total_items to _begin_busy, and on_sensorgram_partial_
result, which calls _note_busy_item_completed once per real spectral-cube
completion). _format_busy_detail_text is a staticmethod specifically so this
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
        text = MainWindow._format_busy_detail_text(5.0, 0, 100, items_completed=0)
        self.assertEqual(text, "0:05 | ETA --:-- | 0%")

    def test_no_items_completed_yet_omits_speed(self) -> None:
        # total_items known, but nothing has actually finished yet - nothing
        # real to report a rate for, so no speed text (not a fabricated
        # "0 items in Ns" reading).
        text = MainWindow._format_busy_detail_text(3.0, 5, 100, items_completed=0)
        self.assertNotIn("s/cube", text)

    def test_falsy_total_items_omits_speed_even_with_items_completed(self) -> None:
        # total_items=0/None is meaningless (nothing to divide by) - must
        # behave the same regardless of items_completed.
        text = MainWindow._format_busy_detail_text(10.0, 50, 0, items_completed=5)
        self.assertNotIn("s/cube", text)

    def test_curr_and_avg_both_shown(self) -> None:
        # 10 cubes done in 40s total (avg 4.00 s/cube), most recent one took
        # 2.5s (curr) - the two numbers are independent, not derived from
        # each other.
        text = MainWindow._format_busy_detail_text(
            40.0, 50, 100, items_completed=10, last_item_seconds=2.5
        )
        self.assertEqual(text, "0:40 | ETA 0:40 | 50% | Curr/Avg 2.50/4.00 s/cube")

    def test_curr_falls_back_to_avg_when_not_given(self) -> None:
        # Only ever happens for the very first completed item (no prior
        # completion to diff against) - Curr should read the same as Avg
        # rather than showing nothing.
        text = MainWindow._format_busy_detail_text(
            2.0, 10, 100, items_completed=1, last_item_seconds=None
        )
        self.assertEqual(text, "0:02 | ETA 0:18 | 10% | Curr/Avg 2.00/2.00 s/cube")

    def test_curr_can_differ_sharply_from_avg(self) -> None:
        # A single slow cube should be fully visible in Curr, not smoothed
        # away by Avg - this is the whole point of showing both.
        text = MainWindow._format_busy_detail_text(
            100.0, 50, 100, items_completed=50, last_item_seconds=9.0
        )
        self.assertEqual(text, "1:40 | ETA 1:40 | 50% | Curr/Avg 9.00/2.00 s/cube")

    def test_curr_zero_for_an_instant_cache_hit_cube(self) -> None:
        # A cache-hit cube can legitimately finish in ~0s - that's real
        # information (not a "meaningless" value to hide/fall back from).
        text = MainWindow._format_busy_detail_text(
            10.0, 50, 100, items_completed=10, last_item_seconds=0.0
        )
        self.assertIn("Curr/Avg 0.00/1.00 s/cube", text)


class TestNoteBusyItemCompleted(unittest.TestCase):
    """_note_busy_item_completed doesn't touch any Qt widget - safe to call
    on an otherwise-unconstructed MainWindow.__new__() instance with just the
    handful of attributes it actually reads/writes."""

    def _make_window(self, total_items: int | None, started_at: float | None = 0.0) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window._busy_total_items = total_items
        window._busy_started_at = started_at
        window._busy_items_completed = 0
        window._busy_last_item_elapsed = 0.0
        window._busy_last_item_seconds = None
        return window

    def test_no_total_items_is_a_no_op(self) -> None:
        window = self._make_window(None)
        window._note_busy_item_completed()
        self.assertEqual(window._busy_items_completed, 0)
        self.assertIsNone(window._busy_last_item_seconds)

    def test_no_started_at_is_a_no_op(self) -> None:
        window = self._make_window(100, started_at=None)
        window._note_busy_item_completed()
        self.assertEqual(window._busy_items_completed, 0)

    def test_first_completion_counts_from_operation_start(self) -> None:
        window = self._make_window(100)
        import time as time_module

        real_perf_counter = time_module.perf_counter
        time_module.perf_counter = lambda: 3.0
        try:
            window._busy_started_at = 0.0
            window._note_busy_item_completed()
        finally:
            time_module.perf_counter = real_perf_counter
        self.assertEqual(window._busy_items_completed, 1)
        self.assertAlmostEqual(window._busy_last_item_seconds, 3.0, places=6)

    def test_each_completion_increments_and_diffs_against_the_previous(self) -> None:
        window = self._make_window(100)
        window._busy_last_item_elapsed = 2.0
        window._busy_items_completed = 3

        class _FakeClock:
            value = 5.5

        # Patch time.perf_counter for a deterministic elapsed reading.
        import time as time_module

        real_perf_counter = time_module.perf_counter
        time_module.perf_counter = lambda: _FakeClock.value
        try:
            window._busy_started_at = 0.0
            window._note_busy_item_completed()
        finally:
            time_module.perf_counter = real_perf_counter
        self.assertEqual(window._busy_items_completed, 4)
        self.assertAlmostEqual(window._busy_last_item_seconds, 3.5, places=6)
        self.assertAlmostEqual(window._busy_last_item_elapsed, 5.5, places=6)


if __name__ == "__main__":
    unittest.main()
