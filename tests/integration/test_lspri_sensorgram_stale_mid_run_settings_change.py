"""Regression test for a bug found during a whole-app audit of LSPRi
Evaluation (see apps/LSPRi/eva/docs/whole_app_bug_audit_2026-09.md, finding
2.1).

Before the fix, `AnalysisWorkerMixin.mark_stale` unconditionally no-opped
whenever a "Start analysis" sensorgram run was already in flight
(`window._sensorgram_running`). Every settings-changed handler that isn't the
"Start analysis" button itself (Fit method, Metric, Reduction, Formula,
spectral-cube range, wavelength range) routes through `mark_stale`, not
through `_calculate_sensorgram_for_range`'s own pending-payload queuing
mechanism. So changing a setting while a long run was in progress was
silently dropped: `on_sensorgram_ready` would apply the just-finished result
and print a normal "done" status, indistinguishable from a fully current one,
even though it was computed under the settings that were active when the run
*started*, not the ones on screen when it *finished*.

The fix: `mark_stale` now remembers (`window._sensorgram_settings_changed_
during_run`) that a setting changed after the run started, and
`on_sensorgram_ready` checks that flag before applying/backing up its result -
if set (and nothing already queued a fresh recompute via
`_pending_sensorgram_payload`), it discards the stale result and shows the
normal "out of date, press Start analysis" state instead, exactly like an
idle settings change would.
"""

from __future__ import annotations

import sys
import threading
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.gui.main_window import MainWindow


class _FakeWindow:
    """Duck-typed stand-in exposing only what mark_stale/on_sensorgram_ready
    actually read, so this can be tested without constructing a real
    MainWindow (Qt widgets, live datasets, etc.) - same approach as
    test_lspri_sensorgram_start_reentrancy.py."""

    def __init__(self) -> None:
        self._sensorgram_running = False
        self._sensorgram_running_signature: tuple | None = None
        self._sensorgram_cancel_event = None
        self._sensorgram_started_at = None
        self._sensorgram_request_id = 1
        self._sensorgram_settings_changed_during_run = False
        self._pending_sensorgram_payload = None
        self._analysis_cache_lock = threading.Lock()
        self._sensorgram_cache: OrderedDict = OrderedDict()
        self._formula_spectrum_dirty = False
        self._workflow_log: list[str] = []
        self._status_text: str | None = None
        self._wavelength_values = [500.0, 550.0, 600.0]
        self._sensorgram_running_roi_ids: tuple[int, ...] = (1, 2, 3)

    def _append_workflow_log(self, message: str, *, level: str = "info") -> None:
        self._workflow_log.append(message)

    def _set_status_text(self, text: str) -> None:
        self._status_text = text

    def _begin_busy(self, text: str, *, determinate: bool = False, show_wait_cursor: bool = True, total_items=None) -> None:
        pass

    def _end_busy(self, *, show_wait_cursor: bool = True) -> None:
        pass

    def _sync_busy_cursor_state(self) -> None:
        pass

    def _analysis_metric_label(self) -> str:
        return "Peak position"

    def _current_analysis_spectral_cube_range(self):
        return None

    def _format_elapsed_seconds(self, seconds: float) -> str:
        return f"{seconds:.2f}s"

    def _compact_timing_text(self, *pairs) -> str:
        return ", ".join(f"{label} {self._format_elapsed_seconds(value)}" for label, value in pairs)

    # Reuses the real (pure, static) formatter rather than a hand-rolled
    # duplicate, so this fixture can't silently drift from the real format.
    _format_sensorgram_completion_summary = staticmethod(MainWindow._format_sensorgram_completion_summary)


def _make_result(*, cancelled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        cancelled=cancelled,
        completed_count=3,
        total_count=3,
        prep_seconds=0.1,
        fit_seconds=0.2,
        spectral_cube_indices=[0, 1, 2],
        metric_values=[1.0, 2.0, 3.0],
        metric_signal=[1.0, 2.0, 3.0],
    )


class MarkStaleWhileRunningTests(unittest.TestCase):
    def test_mark_stale_while_running_sets_flag_without_touching_the_plot(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        window._sensorgram_running = True

        with mock.patch.object(controller, "clear_sensorgram") as clear_mock:
            controller.mark_stale("Fit method changed")

        clear_mock.assert_not_called()
        self.assertTrue(window._sensorgram_settings_changed_during_run)
        # The in-flight run's own bookkeeping must be untouched.
        self.assertTrue(window._sensorgram_running)

    def test_mark_stale_while_idle_behaves_as_before(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)

        with mock.patch.object(controller, "clear_sensorgram") as clear_mock:
            controller.mark_stale("Fit method changed")

        clear_mock.assert_called_once_with("Fit method changed")
        self.assertFalse(window._sensorgram_settings_changed_during_run)


class SensorgramReadyDiscardsStaleResultTests(unittest.TestCase):
    def _run_on_sensorgram_ready(self, controller, result):
        with mock.patch.object(controller, "_flush_measurement_backup_buffers"), \
                mock.patch.object(controller, "_refresh_formula_spectrum"), \
                mock.patch.object(controller, "_apply_cached_sensorgram_result") as apply_mock, \
                mock.patch.object(controller, "schedule_cube_slider_cache_refresh"), \
                mock.patch.object(controller, "clear_sensorgram") as clear_mock, \
                mock.patch.object(controller, "start_pending_sensorgram_refresh") as pending_mock:
            controller.on_sensorgram_ready(controller.window._sensorgram_request_id, result)
        return apply_mock, clear_mock, pending_mock

    def test_result_discarded_when_settings_changed_mid_run_and_nothing_queued(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        window._sensorgram_running = True
        window._sensorgram_settings_changed_during_run = True

        apply_mock, clear_mock, pending_mock = self._run_on_sensorgram_ready(controller, _make_result())

        apply_mock.assert_not_called()
        clear_mock.assert_called_once()
        pending_mock.assert_not_called()
        # The flag must be consumed, not left set for the next run.
        self.assertFalse(window._sensorgram_settings_changed_during_run)
        self.assertFalse(window._sensorgram_running)

    def test_result_applied_normally_when_settings_did_not_change(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        window._sensorgram_running = True
        window._sensorgram_settings_changed_during_run = False

        apply_mock, clear_mock, pending_mock = self._run_on_sensorgram_ready(controller, _make_result())

        apply_mock.assert_called_once()
        clear_mock.assert_not_called()
        pending_mock.assert_not_called()

    def test_pending_payload_takes_priority_over_the_stale_flag(self) -> None:
        # If a fresh recompute is already queued (e.g. the user re-clicked
        # Start analysis for a different ROI selection while this run was
        # going), that existing mechanism already guarantees a current
        # result will follow shortly - the stale-discard path only exists
        # for when nothing else is going to fix the display.
        window = _FakeWindow()
        controller = AnalysisController(window)
        window._sensorgram_running = True
        window._sensorgram_settings_changed_during_run = True
        window._pending_sensorgram_payload = (("sig",), [0], (1,), ["roiA"])

        apply_mock, clear_mock, pending_mock = self._run_on_sensorgram_ready(controller, _make_result())

        pending_mock.assert_called_once()
        clear_mock.assert_not_called()
        self.assertFalse(window._sensorgram_settings_changed_during_run)


if __name__ == "__main__":
    unittest.main()
