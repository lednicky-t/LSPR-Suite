"""Regression tests for request_deferred_ui_refresh's live-visual fast path
(gui/main_window_runtime.py).

Bug: any call to _request_deferred_ui_refresh(telemetry=True/live_estimate=True/
trace_plot=True) while live used to unconditionally request an immediate
(0ms) "live_visual_refresh" scheduler tick. Because that task uses
coalesce="earliest" (GuiTaskScheduler.request - see gui/update_scheduler.py),
an immediate request always pulls an *already scheduled* tick's due time
earlier, never later. handle_plot_processing_result_for (the Absorbance live
path, main_window_plotting.py) calls this from a QThreadPool completion
signal well after the tick that scheduled the next ~200ms-out poll has
finished - so every live Absorbance frame collapsed the next poll to "now",
defeating the display-rate throttle and producing "recent avg" footer
readings faster than the configured refresh rate (reported by the
maintainer). See main_window_runtime.py's live_visual_dirty branch for the
fix: only fire immediately if nothing is already scheduled/running.
"""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QCoreApplication

from lspr_app.gui.main_window_runtime import request_deferred_ui_refresh
from lspr_app.gui.main_window_runtime_state import UiRefreshState
from lspr_app.gui.update_scheduler import GuiTaskScheduler


def _make_window(**kwargs) -> SimpleNamespace:
    window = SimpleNamespace(
        _ui_refresh_state=UiRefreshState(pending_metric_label="Metric position (nm)"),
        _ui_task_scheduler=GuiTaskScheduler(),
        _session_stats_refresh_requested_at=None,
        _live_active=True,
        _live_visual_refresh_in_progress=False,
        _live_ui_refresh_delay_ms=200.0,
        _stats_refresh_delay_ms=50.0,
        _display_refresh_requested_at=None,
        _metric_refresh_requested_at=None,
        _flush_deferred_display_refreshes=lambda: None,
        _flush_deferred_metric_refreshes=lambda: None,
        _flush_deferred_stats_refreshes=lambda: None,
    )
    for key, value in kwargs.items():
        setattr(window, key, value)
    return window


class LiveVisualDirtyFastPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_first_dirty_call_schedules_immediately_when_nothing_pending(self) -> None:
        window = _make_window()

        request_deferred_ui_refresh(window, telemetry=True)

        self.assertTrue(window._ui_task_scheduler.is_pending("live_visual_refresh"))

    def test_async_completion_does_not_pull_an_already_scheduled_tick_earlier(self) -> None:
        """The core regression: a correctly-paced ~200ms-out re-arm (as done
        by flush_live_processed_results/flush_live_acquisition_results at the
        end of every tick) must survive a later, asynchronously-arriving
        dirty notification - not get collapsed to "now"."""
        window = _make_window()
        calls: list[str] = []
        window._ui_task_scheduler.request(
            "live_visual_refresh", 200.0, lambda: calls.append("tick"), priority=-20, coalesce="earliest",
        )
        task = window._ui_task_scheduler._tasks["live_visual_refresh"]  # noqa: SLF001
        original_due_at = task.due_at

        # Simulate handle_plot_processing_result_for's trailing call, arriving
        # asynchronously (not mid-tick: _live_visual_refresh_in_progress is False).
        request_deferred_ui_refresh(window, trace_plot=True, live_estimate=True, telemetry=True)

        task_after = window._ui_task_scheduler._tasks["live_visual_refresh"]  # noqa: SLF001
        self.assertEqual(task_after.due_at, original_due_at)
        self.assertEqual(calls, [])  # not dispatched early

    def test_mid_tick_call_also_does_not_reschedule(self) -> None:
        """The old code's only guard (_live_visual_refresh_in_progress) should
        still work for the synchronous, mid-tick case."""
        window = _make_window(_live_visual_refresh_in_progress=True)

        request_deferred_ui_refresh(window, telemetry=True)

        self.assertFalse(window._ui_task_scheduler.is_pending("live_visual_refresh"))

    def test_not_live_active_does_not_take_the_live_visual_fast_path(self) -> None:
        window = _make_window(_live_active=False)

        request_deferred_ui_refresh(window, telemetry=True)

        self.assertFalse(window._ui_task_scheduler.is_pending("live_visual_refresh"))
        self.assertTrue(window._ui_task_scheduler.is_pending("deferred_display_flush"))


if __name__ == "__main__":
    unittest.main()
