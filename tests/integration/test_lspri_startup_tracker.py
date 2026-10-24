"""Tests for the status-bar "Starting up" tracker (`startup_tracker.py`).

Driven with fake steps (plain booleans) and a fake clock; `_poll()` is called directly, so no event loop or timing is involved.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.startup_tracker import STARTUP_TASK_ID, StartupStep, StartupTracker  # noqa: E402


class _Recorder:
    def __init__(self, tracker: StartupTracker) -> None:
        self.progress: list[tuple[str, str, float, str]] = []
        self.finished: list[tuple[str, str, str]] = []
        tracker.task_progress.connect(lambda *args: self.progress.append(args))
        tracker.task_finished.connect(lambda *args: self.finished.append(args))


class StartupTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {"dataset": False, "session": False}
        self.now = [0.0]
        self.tracker = StartupTracker(
            [
                StartupStep("dataset", "opening the dataset", lambda: self.state["dataset"]),
                StartupStep("session", "reading the session", lambda: self.state["session"]),
            ],
            settle_polls=2,
            timeout_s=60.0,
            clock=lambda: self.now[0],
        )
        self.rec = _Recorder(self.tracker)

    def tearDown(self) -> None:
        self.tracker.cancel()

    def test_names_the_step_it_waits_for_and_finishes_once_all_are_done(self) -> None:
        self.tracker.start()
        self.assertEqual(self.rec.progress[-1], (STARTUP_TASK_ID, "Starting up", -1.0, "step 1 of 2: opening the dataset"))
        self.state["dataset"] = True
        self.tracker._poll()
        self.assertEqual(self.rec.progress[-1][3], "step 2 of 2: reading the session")
        self.state["session"] = True
        self.tracker._poll()  # all done, but only once: settling
        self.assertEqual(self.rec.finished, [])
        self.tracker._poll()
        self.assertEqual(self.rec.finished, [(STARTUP_TASK_ID, "completed", "ready (dataset, session)")])
        self.assertFalse(self.tracker.is_running())

    def test_a_later_step_is_not_asked_before_the_earlier_ones_are_done(self) -> None:
        asked: list[str] = []
        tracker = StartupTracker([
            StartupStep("a", "a", lambda: False),
            StartupStep("b", "b", lambda: asked.append("b") or True),
        ])
        tracker.start()
        self.assertEqual(asked, [])
        tracker.cancel()

    def test_a_step_that_goes_false_again_restarts_the_settling(self) -> None:
        self.state.update(dataset=True, session=True)
        self.tracker.start()  # first quiet poll
        self.state["session"] = False  # a module started follow-up work in the gap
        self.tracker._poll()
        self.state["session"] = True
        self.tracker._poll()
        self.assertEqual(self.rec.finished, [])  # one quiet poll since the interruption
        self.tracker._poll()
        self.assertEqual([f[1] for f in self.rec.finished], ["completed"])

    def test_a_step_that_never_becomes_true_ends_as_failed_naming_it(self) -> None:
        self.tracker.start()
        self.now[0] = 61.0
        self.tracker._poll()
        self.assertEqual(self.rec.finished[0][1], "failed")
        self.assertIn("dataset", self.rec.finished[0][2])

    def test_a_broken_step_ends_the_run_visibly(self) -> None:
        def broken() -> bool:
            raise ValueError("boom")

        tracker = StartupTracker([StartupStep("x", "x", broken)])
        rec = _Recorder(tracker)
        tracker.start()
        self.assertEqual(rec.finished[0][1], "failed")
        self.assertIn("boom", rec.finished[0][2])

    def test_fail_early_end_and_cancel_report_exactly_once(self) -> None:
        self.tracker.start()
        self.tracker.fail("no dataset found")
        self.tracker.fail("again")
        self.tracker.cancel()
        self.assertEqual(self.rec.finished, [(STARTUP_TASK_ID, "failed", "no dataset found")])

    def test_choice_needed_ends_as_completed(self) -> None:
        self.tracker.start()
        self.tracker.on_choice_needed(object())
        self.assertEqual(self.rec.finished[0][1], "completed")
        self.assertIn("choose", self.rec.finished[0][2])

    def test_progress_is_only_reported_when_the_message_changes(self) -> None:
        self.tracker.start()
        for _ in range(5):
            self.tracker._poll()
        self.assertEqual(len(self.rec.progress), 1)


if __name__ == "__main__":
    unittest.main()
