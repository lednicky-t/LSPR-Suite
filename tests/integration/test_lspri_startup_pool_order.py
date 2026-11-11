"""Start-up order (2026-11-11): the analysis scan's Performance-mode workers start only after the ROI / Groups table's start-up calculation has
ended (seven processes starting took the CPU from the fit helper: 11 s instead of ~1.2 s), and `PanelTask.ended` reports the end of every run."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app import app_rewrite  # noqa: E402
from lspr_imaging_app.panels.panel_task import PanelTask  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _Startup(QObject):
    task_finished = pyqtSignal(str, str, str)


class _RoiTable:
    """What `_wire_startup_calculation` uses of the ROI table panel: its task and `calculate_at_startup`."""

    def __init__(self, calculates: bool) -> None:
        self.task = PanelTask("roi_table_steps", "ROI / Groups", show_after_ms=10_000)
        self.calculates = calculates

    def calculate_at_startup(self) -> None:
        if self.calculates:
            self.task.begin("fitting 160 ROIs")


class PanelTaskEndedTests(unittest.TestCase):
    def test_ended_is_sent_for_every_run_also_one_too_short_to_be_shown(self) -> None:
        task = PanelTask("t", "Task", show_after_ms=10_000)
        ended, finished = [], []
        task.ended.connect(ended.append)
        task.task_finished.connect(lambda *args: finished.append(args))
        task.begin("working")
        task.end("completed")
        task.end("completed")  # a second end of the same run: nothing
        self.assertEqual((ended, finished), (["completed"], []))


class StartupPoolOrderTests(unittest.TestCase):
    def _wire(self, calculates: bool):
        startup, table, starts = _Startup(), _RoiTable(calculates), []
        app_rewrite._wire_startup_calculation(startup, table, lambda: starts.append(1))
        return startup, table, starts

    def test_the_pool_starts_only_after_the_startup_calculation_has_ended(self) -> None:
        startup, table, starts = self._wire(calculates=True)
        startup.task_finished.emit("app.startup", "completed", "")
        self.assertTrue(table.task.is_active())
        self.assertEqual(starts, [], "started while the ROI / Groups table was fitting")
        table.task.end("completed")
        self.assertEqual(starts, [1])

    def test_the_pool_starts_at_once_when_there_is_nothing_to_calculate_or_the_startup_failed(self) -> None:
        for calculates, outcome in ((False, "completed"), (True, "failed")):
            startup, _table, starts = self._wire(calculates)
            startup.task_finished.emit("app.startup", outcome, "")
            self.assertEqual(starts, [1], (calculates, outcome))


if __name__ == "__main__":
    unittest.main()
