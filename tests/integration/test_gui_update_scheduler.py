from __future__ import annotations

import unittest

from PyQt6.QtCore import QCoreApplication

from lspr_app.gui.update_scheduler import GuiTaskScheduler


class TestGuiTaskScheduler(unittest.TestCase):
    def test_due_tasks_run_in_priority_order(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        _ = app
        scheduler = GuiTaskScheduler()
        calls: list[str] = []

        scheduler.request("low", 0, lambda: calls.append("low"), priority=10)
        scheduler.request("high", 0, lambda: calls.append("high"), priority=0)

        scheduler._dispatch_due_tasks()  # noqa: SLF001

        self.assertEqual(calls, ["high", "low"])
        self.assertEqual(scheduler._last_dispatch_task_count, 2)
        self.assertIsNotNone(scheduler._last_dispatch_lag_ms)
        self.assertIsNotNone(scheduler._last_dispatch_duration_ms)

    def test_repeated_request_coalesces_to_latest_callback(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        _ = app
        scheduler = GuiTaskScheduler()
        calls: list[str] = []

        scheduler.request("task", 0, lambda: calls.append("first"))
        scheduler.request("task", 0, lambda: calls.append("second"))

        scheduler._dispatch_due_tasks()  # noqa: SLF001

        self.assertEqual(calls, ["second"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
