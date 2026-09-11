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
        # each other. ETA is items-based (avg 4.00s/cube * 90 remaining =
        # 360s), deliberately NOT the percent-based 40*(50/50)=40s a naive
        # extrapolation from the (here mismatched-on-purpose) 50% would give
        # - see _format_busy_detail_text's docstring for why items win.
        text = MainWindow._format_busy_detail_text(
            40.0, 50, 100, items_completed=10, last_item_seconds=2.5
        )
        self.assertEqual(text, "0:40 | ETA 6:00 | 50% | Curr/Avg 2.50/4.00 s/cube")

    def test_curr_falls_back_to_avg_when_not_given(self) -> None:
        # Only ever happens for the very first completed item (no prior
        # completion to diff against) - Curr should read the same as Avg
        # rather than showing nothing. ETA is items-based (avg 2.00s/cube *
        # 99 remaining = 198s), not the percent-based 2*(90/10)=18s.
        text = MainWindow._format_busy_detail_text(
            2.0, 10, 100, items_completed=1, last_item_seconds=None
        )
        self.assertEqual(text, "0:02 | ETA 3:18 | 10% | Curr/Avg 2.00/2.00 s/cube")

    def test_curr_can_differ_sharply_from_avg(self) -> None:
        # A single slow cube should be fully visible in Curr, not smoothed
        # away by Avg - this is the whole point of showing both.
        text = MainWindow._format_busy_detail_text(
            100.0, 50, 100, items_completed=50, last_item_seconds=9.0
        )
        self.assertEqual(text, "1:40 | ETA 1:40 | 50% | Curr/Avg 9.00/2.00 s/cube")

    def test_eta_uses_item_rate_not_percent_when_they_diverge(self) -> None:
        # Reproduces the reported "35% at 1 minute in, but ETA reads far too
        # low" symptom: the 20%-prep/80%-compute split in analysis_tasks.py's
        # spectral_cube_progress_callback can put current_percent well ahead
        # of real per-cube progress right after prep finishes. Naive percent
        # extrapolation (60*(65/35)=~111s) badly underestimates the true
        # remaining time; the items-based rate (59 s/cube average here) gives
        # a much larger, correct-shaped answer instead.
        text = MainWindow._format_busy_detail_text(
            60.0, 35, 314, items_completed=59, last_item_seconds=1.0
        )
        avg = 60.0 / 59
        remaining = 314 - 59
        expected_eta_seconds = avg * remaining
        self.assertGreater(expected_eta_seconds, 111.4)
        self.assertIn("ETA 4:", text)

    def test_curr_zero_for_an_instant_cache_hit_cube(self) -> None:
        # A cache-hit cube can legitimately finish in ~0s - that's real
        # information (not a "meaningless" value to hide/fall back from).
        # This exercises the legacy (fresh_items_completed=None) path,
        # where the caller doesn't distinguish cache hits from real
        # computations at all - see the fresh-tracking tests below for the
        # scenario this whole distinction actually exists for.
        text = MainWindow._format_busy_detail_text(
            10.0, 50, 100, items_completed=10, last_item_seconds=0.0
        )
        self.assertIn("Curr/Avg 0.00/1.00 s/cube", text)

    def test_resumed_run_catch_up_phase_hides_speed_instead_of_faking_it(self) -> None:
        # Regression test for a real bug: resuming "Start analysis" after
        # Stop makes the worker race through every cube a previous, since-
        # stopped run already finished as a near-instant RAM/disk cache
        # hit, before reaching genuinely new cubes. Before fresh-tracking
        # existed, items_completed counted those free hits the same as a
        # real computation, so elapsed/items_completed read as a falsely
        # fast average for the rest of the run. Here: 200 items "completed"
        # (all cache hits) in 0.3s elapsed, but nothing has been genuinely
        # computed yet (fresh_items_completed=0) - the old formula would
        # have reported ~0.0015 s/cube; the fix must show no speed reading
        # at all rather than that fabricated number.
        text = MainWindow._format_busy_detail_text(
            0.3, 40, 500, items_completed=200, last_item_seconds=0.0015,
            fresh_items_completed=0, fresh_elapsed_seconds=0.0,
        )
        self.assertNotIn("s/cube", text)

    def test_resumed_run_avg_reflects_only_genuinely_computed_cubes(self) -> None:
        # Continuing the scenario above: the run has since moved past the
        # cache-hit catch-up phase and genuinely computed 5 new cubes,
        # taking 10s of real compute time total, most recently 2.5s. Total
        # elapsed is 10.3s (0.3s catch-up + 10s real compute) over 205
        # items_completed (200 free + 5 real) - the old whole-run average
        # (10.3/205 ~= 0.05 s/cube) would still be badly diluted by the
        # free prefix. Avg/Curr must reflect only the 5 real completions
        # (10/5 = 2.00 s/cube), and the ETA must extrapolate from that real
        # rate over the remaining item count, not the diluted one.
        text = MainWindow._format_busy_detail_text(
            10.3, 41, 500, items_completed=205, last_item_seconds=2.5,
            fresh_items_completed=5, fresh_elapsed_seconds=10.0,
        )
        self.assertIn("Curr/Avg 2.50/2.00 s/cube", text)
        remaining = 500 - 205
        expected_eta_seconds = 2.00 * remaining
        self.assertNotAlmostEqual(expected_eta_seconds, 10.3 / 205 * remaining, delta=1.0)
        self.assertIn("ETA 9:50", text)


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
        window._busy_fresh_items_completed = 0
        window._busy_fresh_elapsed_seconds = 0.0
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
        # Default freshly_computed=True - a genuine completion also counts
        # toward the fresh-only tracking used for the Avg/Curr readout.
        self.assertEqual(window._busy_fresh_items_completed, 1)
        self.assertAlmostEqual(window._busy_fresh_elapsed_seconds, 3.0, places=6)

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

    def test_cache_hit_advances_items_completed_but_not_the_fresh_counters(self) -> None:
        # Regression test: a RAM/disk cache hit (freshly_computed=False -
        # e.g. a cube a since-stopped earlier run already finished, picked
        # up near-instantly on a resumed "Start analysis" run) must still
        # advance items_completed/the elapsed clock (so the NEXT item's own
        # duration and the ETA's remaining-item count stay correct), but
        # must not be counted as a sample of real per-cube compute time.
        window = self._make_window(100)
        import time as time_module

        real_perf_counter = time_module.perf_counter
        time_module.perf_counter = lambda: 0.01
        try:
            window._busy_started_at = 0.0
            window._note_busy_item_completed(freshly_computed=False)
        finally:
            time_module.perf_counter = real_perf_counter
        self.assertEqual(window._busy_items_completed, 1)
        self.assertAlmostEqual(window._busy_last_item_elapsed, 0.01, places=6)
        self.assertEqual(window._busy_fresh_items_completed, 0)
        self.assertAlmostEqual(window._busy_fresh_elapsed_seconds, 0.0, places=6)
        # Curr must still be None (not the cache hit's near-zero duration) -
        # nothing genuine has completed yet to report a rate for.
        self.assertIsNone(window._busy_last_item_seconds)

    def test_fresh_completion_after_cache_hits_measures_only_its_own_duration(self) -> None:
        # Continuing the scenario above: several cache hits fly by, then a
        # genuine computation finishes. Its own duration must be measured
        # from when IT started (i.e. from the last item's elapsed marker,
        # which the cache hits above still advanced), not inflated by the
        # cache-hit time that preceded it.
        window = self._make_window(100)
        window._busy_last_item_elapsed = 0.02  # left behind by prior cache hits
        window._busy_items_completed = 5
        import time as time_module

        real_perf_counter = time_module.perf_counter
        time_module.perf_counter = lambda: 2.02  # this cube took 2.0s
        try:
            window._busy_started_at = 0.0
            window._note_busy_item_completed(freshly_computed=True)
        finally:
            time_module.perf_counter = real_perf_counter
        self.assertEqual(window._busy_items_completed, 6)
        self.assertEqual(window._busy_fresh_items_completed, 1)
        self.assertAlmostEqual(window._busy_last_item_seconds, 2.0, places=6)
        self.assertAlmostEqual(window._busy_fresh_elapsed_seconds, 2.0, places=6)
        self.assertAlmostEqual(window._busy_last_item_elapsed, 2.02, places=6)


if __name__ == "__main__":
    unittest.main()
