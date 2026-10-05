"""Tests for the app-wide task indicator (`panels/task_indicator.py`).

Pure helpers (duration text, ETA) plus the widget driven through its public
slots with a real in-process `QApplication`; never `.exec()`.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

from tests._qt_fonts import load_system_fonts  # noqa: E402

load_system_fonts()  # offscreen Qt has no fonts of its own; text widths below need real ones

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.panels.task_indicator import TaskIndicator, estimate_total, format_duration  # noqa: E402


class HelperTests(unittest.TestCase):
    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(0), "0:00")
        self.assertEqual(format_duration(-3), "0:00")
        self.assertEqual(format_duration(float("nan")), "0:00")
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3725), "1:02:05")

    def test_estimate_total_withheld_until_enough_signal(self) -> None:
        self.assertIsNone(estimate_total(10.0, 0.01))  # too little progress
        self.assertIsNone(estimate_total(0.5, 0.5))  # too little time
        self.assertAlmostEqual(estimate_total(10.0, 0.25), 40.0)
        self.assertAlmostEqual(estimate_total(10.0, 1.2), 10.0)  # fraction is clipped to 1


class IndicatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = QtWidgets.QWidget()
        self.indicator = TaskIndicator(self.host)
        self.host.show()

    def tearDown(self) -> None:
        self.host.close()

    def test_hidden_until_first_task_then_shows_progress(self) -> None:
        self.assertFalse(self.indicator.isVisible())
        self.indicator.report("t", "Task", 0.4, "step 2")
        self.assertTrue(self.indicator.isVisible())
        self.assertTrue(self.indicator._spinner.is_running())
        self.assertEqual(self.indicator._bar.value(), 40)
        self.assertIn("Task: step 2", self.indicator._text.text())
        self.assertIn("started ", self.indicator._detail.text())
        self.assertIn("elapsed", self.indicator._detail.text())

    def test_result_stays_until_dismissed_or_next_task(self) -> None:
        self.indicator.report("t", "Task", 0.4, "")
        self.indicator.finish("t", "completed", "4 landmarks")
        self.assertTrue(self.indicator.isVisible())
        self.assertFalse(self.indicator._spinner.is_running())
        self.assertFalse(self.indicator._bar.isVisible())
        self.assertIn("done", self.indicator._text.text())
        self.assertIn("4 landmarks", self.indicator._text.text())
        self.assertIn("finished ", self.indicator._detail.text())
        self.indicator._dismiss.click()
        self.assertFalse(self.indicator.isVisible())
        # a new run replaces a remembered result
        self.indicator.report("t", "Task", 0.1, "")
        self.indicator.finish("t", "failed", "boom")
        self.assertIn("FAILED", self.indicator._text.text())
        self.indicator.report("u", "Other", 0.1, "")
        self.assertTrue(self.indicator._bar.isVisible())
        self.assertIn("Other", self.indicator._text.text())

    def test_history_records_every_outcome_and_logs(self) -> None:
        with self.assertLogs("lspr_imaging_app.panels.task_indicator", level="INFO") as logs:
            for task_id, outcome in (("a", "completed"), ("b", "failed"), ("c", "cancelled")):
                self.indicator.report(task_id, task_id.upper(), 0.5, "")
                self.indicator.finish(task_id, outcome, "msg")
        self.assertEqual([r.outcome for r in self.indicator.history()], ["completed", "failed", "cancelled"])
        self.assertEqual([r.levelname for r in logs.records], ["INFO", "WARNING", "INFO"])
        self.indicator.report("d", "D", 0.2, "")
        lines = self.indicator.history_lines()
        self.assertIn("RUNNING", lines[0])  # running first, then newest finished
        self.assertIn("C: cancelled", lines[1])
        self.indicator._show_history()  # popup opens without error
        self.assertTrue(self.indicator._popup.isVisible())
        self.indicator._popup.close()

    def test_row_is_compact_and_parts_do_not_overlap_when_narrow(self) -> None:
        self.indicator.report("t", "Chromatic landmarks", 0.4, "tracking wavelength 12/41")
        self.indicator.resize(900, self.indicator.height())
        _APP.processEvents()
        self.assertLessEqual(self.indicator.height(), 24)
        parts = [getattr(self.indicator, n) for n in ("_text", "_bar", "_detail", "_cancel", "_history_button")]
        for left, right in zip(parts, parts[1:]):
            self.assertLess(left.geometry().right(), right.geometry().left())
        self.assertLessEqual(parts[-1].geometry().right(), 900)

    def test_unknown_fraction_is_a_busy_bar(self) -> None:
        self.indicator.report("t", "Task", -1.0, "")
        self.assertEqual((self.indicator._bar.minimum(), self.indicator._bar.maximum()), (0, 0))
        self.indicator.report("t", "Task", 0.5, "")
        self.assertEqual(self.indicator._bar.maximum(), 100)

    def test_several_tasks_and_cancel_targets_the_shown_one(self) -> None:
        cancelled: list[str] = []
        self.indicator.cancel_requested.connect(cancelled.append)
        self.indicator.report("a", "A", 0.1, "")
        self.indicator.report("b", "B", 0.1, "")
        self.assertEqual(self.indicator._more.text(), "+1 more")
        self.indicator._cancel.click()
        self.assertEqual(cancelled, ["a"])
        self.indicator.finish("a", "cancelled")
        self.assertEqual(self.indicator._more.text(), "")
        self.assertTrue(self.indicator._spinner.is_running())  # b still runs, shown instead of a's result
        self.indicator.finish("b")
        self.assertFalse(self.indicator._spinner.is_running())

    def test_finish_of_unknown_task_is_harmless(self) -> None:
        self.indicator.finish("never-started")
        self.assertEqual(self.indicator.history(), [])
        self.assertFalse(self.indicator.isVisible())


if __name__ == "__main__":
    unittest.main()
