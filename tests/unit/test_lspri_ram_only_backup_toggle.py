"""Regression tests for the Analysis section's [disk]/[RAM] toggle
(_analysis_ram_only_backup, gui/layout_builder.py's _make_ram_only_backup_
toggle) - added after two separate STATUS_HEAP_CORRUPTION crashes traced to
measurement_backup.h5 accumulating write/resize history over a long testing
session (see apps/LSPRi/eva/docs/measurement_backup_performance_and_crash_
recovery.md). The toggle skips the *periodic* mid-run backup flush in
on_sensorgram_partial_result entirely, holding results in RAM until the run
finishes or is stopped - on_sensorgram_ready/on_sensorgram_failed's own
unconditional flush (unchanged, not covered here) is what actually writes
them at that point.

Tests the extracted pure decision function directly (`_measurement_backup_
periodic_flush_due`) rather than the full on_sensorgram_partial_result -
that method touches ~10 unrelated window/QTimer attributes with no bearing
on this specific behavior; the pure split mirrors MainWindow._format_busy_
detail_text's existing testability pattern.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.analysis_worker_mixin import AnalysisWorkerMixin  # noqa: E402

_flush_due = AnalysisWorkerMixin._measurement_backup_periodic_flush_due


class TestMeasurementBackupPeriodicFlushDue(unittest.TestCase):
    def test_disk_mode_flushes_once_batch_size_is_reached(self) -> None:
        self.assertTrue(_flush_due(5, 5, False))
        self.assertTrue(_flush_due(6, 5, False))

    def test_disk_mode_does_not_flush_before_batch_size(self) -> None:
        self.assertFalse(_flush_due(4, 5, False))
        self.assertFalse(_flush_due(0, 5, False))

    def test_ram_only_mode_never_flushes_regardless_of_buffered_count(self) -> None:
        # This is the whole point of the toggle: even a buffered count far
        # past the configured batch size must not trigger a periodic flush
        # while [RAM] mode is on - only the unconditional end-of-run flush
        # (on_sensorgram_ready/on_sensorgram_failed, not covered by this
        # function) is allowed to write anything.
        for buffered_count in (0, 1, 5, 6, 100, 10_000):
            with self.subTest(buffered_count=buffered_count):
                self.assertFalse(_flush_due(buffered_count, 5, True))

    def test_batch_size_is_clamped_to_at_least_one(self) -> None:
        # A misconfigured/zero batch size must not make every single result
        # trigger a flush call for a nonsensical reason - mirrors
        # MainWindow._measurement_backup_batch_size's own `>= 1` clamp, so
        # this stays correct even if that clamp were ever bypassed upstream.
        self.assertTrue(_flush_due(1, 0, False))
        self.assertFalse(_flush_due(1, -3, True))


if __name__ == "__main__":
    unittest.main()
