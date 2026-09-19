"""Regression test for a bug found while consolidating two duplicate
"start sensorgram" implementations in gui/analysis_controller.py.

Before the fix, the method actually wired to the "Start analysis" button
(`_calculate_sensorgram_for_range`) checked the sensorgram cache for a hit
*before* checking whether a different run was already in flight
(`window._sensorgram_running`). That meant: if run B was in progress and the
user's current selection happened to match an already-cached signature A,
clicking "Start analysis" would immediately overwrite the displayed
sensorgram with A's cached result via a hand-rolled apply block - bypassing
the pending-queue mechanism entirely - while `_sensorgram_running`/
`_sensorgram_running_signature` kept describing run B. Run B's own
completion (`on_sensorgram_ready`) would then silently overwrite the display
again once it finished, and reset those flags for a run the user never
explicitly saw applied.

The fix reorders the check: the "is something else already running" check
now runs first, so a cache hit for a different signature gets queued via
`_pending_sensorgram_payload` (the same mechanism already used when a
setting changes mid-run) instead of being applied immediately. These tests
assert on that ordering directly, via `_apply_cached_sensorgram_result` /
`_start_sensorgram_worker` call spies, rather than reproducing the full
worker/plotting machinery.

Updated 2026-09-19: the availability check itself
(`_sensorgram_selection_fully_available`) now runs off the GUI thread (see
`_dispatch_sensorgram_availability_check`'s docstring - a large selection on
a cold cache used to freeze the UI for minutes with zero feedback). The
"is something else already running" guard tested below is unchanged and
still runs synchronously inside `_calculate_sensorgram_for_range`, so those
tests are untouched. The apply-vs-start *decision* that used to happen
inline in `_calculate_sensorgram_for_range` now happens in the result
callback, `_on_sensorgram_availability_checked` - the "cache hit/miss"
tests below now call that callback directly (with a matching request_id,
simulating the background check having already reported its answer)
instead of `_calculate_sensorgram_for_range`, since that's now the actual
unit that makes the decision.
"""

from __future__ import annotations

import sys
import threading
import unittest
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


class _FakeWindow:
    """Duck-typed stand-in exposing only what
    `AnalysisController._calculate_sensorgram_for_range` actually reads, so
    the guard-ordering logic can be tested without constructing a real
    MainWindow (Qt widgets, live datasets, etc.)."""

    def __init__(self) -> None:
        self._state = SimpleNamespace(dataset=SimpleNamespace(folder="dataset_folder"))
        self._chromatic_setup_active = False
        self._sensorgram_running = False
        self._sensorgram_running_signature: tuple | None = None
        self._sensorgram_request_id = 0
        self._pending_sensorgram_payload = None
        self._analysis_cache_lock = threading.Lock()
        self._workflow_log: list[str] = []
        self._summary_text: str | None = None
        self._status_text: str | None = None
        self._control_state_refresh_count = 0
        self._background_errors: list[tuple[str, str]] = []

    def _selected_spectrum_roi_ids(self) -> tuple[int, ...]:
        return (1,)

    def _selected_source_rois_snapshot(self):
        return ["roiA"]

    def _available_analysis_spectral_cubes(self):
        return [0, 1, 2]

    def _sensorgram_signature_for_selection(self, spectral_cubes, selected_roi_ids, selected_source_rois):
        return ("current-selection-signature",)

    def _analysis_metric_label(self) -> str:
        return "Peak position"

    def _append_workflow_log(self, message: str, *, level: str = "info") -> None:
        self._workflow_log.append(message)

    def _set_sensorgram_summary_text(self, text: str) -> None:
        self._summary_text = text

    def _set_status_text(self, text: str) -> None:
        self._status_text = text

    def _update_analysis_control_state(self) -> None:
        self._control_state_refresh_count += 1

    def _background_error(self, context: str, message: str) -> None:
        self._background_errors.append((context, message))


class SensorgramStartReentrancyTests(unittest.TestCase):
    def test_cache_hit_for_a_different_running_signature_is_queued_not_applied(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        # The current selection's signature is the one _calculate_sensorgram_
        # for_range would compute (fixed by _FakeWindow's own stub)...
        cached_signature = ("current-selection-signature",)
        # ...but a DIFFERENT run is in flight right now.
        window._sensorgram_running = True
        window._sensorgram_running_signature = ("some-other-signature",)

        with mock.patch.object(controller, "_apply_already_available_sensorgram_selection") as apply_mock, \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._calculate_sensorgram_for_range()

        apply_mock.assert_not_called()
        start_mock.assert_not_called()
        self.assertIsNotNone(window._pending_sensorgram_payload)
        self.assertEqual(window._pending_sensorgram_payload[0], cached_signature)
        # The flags describing the in-flight run must be left alone - this
        # is what proves the display wasn't silently switched to the cached
        # result out from under the still-running worker.
        self.assertTrue(window._sensorgram_running)
        self.assertEqual(window._sensorgram_running_signature, ("some-other-signature",))

    def test_cache_hit_for_the_currently_running_signature_is_a_no_op(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        signature = ("current-selection-signature",)
        window._sensorgram_running = True
        window._sensorgram_running_signature = signature

        with mock.patch.object(controller, "_apply_cached_sensorgram_result") as apply_mock, \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._calculate_sensorgram_for_range()

        apply_mock.assert_not_called()
        start_mock.assert_not_called()
        self.assertIsNone(window._pending_sensorgram_payload)

    def test_nothing_running_dispatches_the_availability_check(self) -> None:
        """`_calculate_sensorgram_for_range` itself no longer decides apply-
        vs-start (see _on_sensorgram_availability_checked below) - it just
        hands off to `_dispatch_sensorgram_availability_check`, synchronously,
        before anything expensive runs. That method is what marks the run
        in-flight and shows busy state immediately (covered by its own
        behavior below via the callback tests, which simulate having gone
        through it) instead of the UI going silent for however long the now-
        backgrounded cache scan takes - see `_dispatch_sensorgram_
        availability_check`'s own docstring for the 2026-09-19 "feels
        frozen, nothing shows it's happening" report this fixes."""
        window = _FakeWindow()
        controller = AnalysisController(window)

        with mock.patch.object(controller, "_dispatch_sensorgram_availability_check") as dispatch_mock:
            controller._calculate_sensorgram_for_range()

        dispatch_mock.assert_called_once_with(("current-selection-signature",), [0, 1, 2], (1,), ["roiA"])

    def test_dispatch_marks_running_and_starts_a_worker_before_anything_expensive(self) -> None:
        """`_dispatch_sensorgram_availability_check` is what actually shows
        busy state immediately - marking `_sensorgram_running` True and
        bumping the request id happen synchronously, before the (real)
        background thread that runs the expensive check is even started.
        `FunctionWorker` itself is mocked here so this stays a fast, real-
        thread-free unit test - the worker's own dispatch mechanics
        (`.start()` spawning a plain `threading.Thread`, never QThreadPool)
        are FunctionWorker's own contract, not this method's."""
        window = _FakeWindow()
        controller = AnalysisController(window)

        with mock.patch("lspr_imaging_app.gui.worker.FunctionWorker") as worker_cls:
            worker_instance = worker_cls.return_value
            controller._dispatch_sensorgram_availability_check(
                ("current-selection-signature",), [0, 1, 2], (1,), ["roiA"]
            )

        self.assertTrue(window._sensorgram_running)
        self.assertEqual(window._sensorgram_running_signature, ("current-selection-signature",))
        self.assertEqual(window._sensorgram_request_id, 1)
        worker_instance.start.assert_called_once()

    def test_availability_check_result_hit_is_applied_and_clears_running(self) -> None:
        # "Cache hit" is "every selected ROI already has every requested
        # cube's value" (_sensorgram_selection_fully_available), checked via
        # the atomic per-(ROI, cube) cache rather than a single combined-
        # selection entry - see docs/analysis_caching_architecture.md. That
        # check now runs off the GUI thread (_dispatch_sensorgram_
        # availability_check); this test exercises its result callback
        # directly - the actual unit that now makes the apply-vs-start
        # decision - as if the background check had already reported "hit".
        # The question's own real logic is covered separately
        # (test_lspri_sensorgram_metric_cache.py,
        # test_lspri_sensorgram_missing_data_message.py).
        window = _FakeWindow()
        window._sensorgram_request_id = 1
        window._sensorgram_running = True
        controller = AnalysisController(window)

        with mock.patch.object(controller, "_apply_already_available_sensorgram_selection") as apply_mock, \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._on_sensorgram_availability_checked(
                1, True, ("current-selection-signature",), [0, 1, 2], (1,), ["roiA"]
            )

        apply_mock.assert_called_once_with((1,), [0, 1, 2])
        start_mock.assert_not_called()
        self.assertFalse(window._sensorgram_running)

    def test_availability_check_result_miss_starts_the_worker(self) -> None:
        """Regression coverage carried over from before the async rewrite:
        pressing Stop must not make a later, identical "Start analysis"
        click silently redisplay the stopped run's incomplete result
        instead of resuming it - the button would look unresponsive because
        nothing visibly changed and the run never continued. There's no
        distinct `cancelled` flag to check (see docs/analysis_caching_
        architecture.md); the guarantee holds by construction: a Stopped run
        only ever leaves the cubes it actually finished in the atomic cache,
        so `_sensorgram_selection_fully_available` correctly reports "not
        fully available" for the remaining ones and a real worker start
        follows, exactly like any other incomplete selection (or a plain
        cache miss, the other case this covers)."""
        window = _FakeWindow()
        window._sensorgram_request_id = 1
        window._sensorgram_running = True
        controller = AnalysisController(window)

        with mock.patch.object(controller, "_apply_already_available_sensorgram_selection") as apply_mock, \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._on_sensorgram_availability_checked(
                1, False, ("current-selection-signature",), [0, 1, 2], (1,), ["roiA"]
            )

        apply_mock.assert_not_called()
        start_mock.assert_called_once_with(("current-selection-signature",), [0, 1, 2], (1,), ["roiA"])

    def test_availability_check_result_ignored_if_superseded(self) -> None:
        """If the selection changed (or a newer check/run started) while the
        background check was still in flight, `_sensorgram_request_id` will
        have moved on by the time the stale result arrives - same guard
        on_sensorgram_ready/on_sensorgram_failed already use for the real
        worker's own results. Neither apply nor start must fire for a
        request_id that no longer matches."""
        window = _FakeWindow()
        window._sensorgram_request_id = 2  # a newer request is now current
        controller = AnalysisController(window)

        with mock.patch.object(controller, "_apply_already_available_sensorgram_selection") as apply_mock, \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._on_sensorgram_availability_checked(
                1, True, ("current-selection-signature",), [0, 1, 2], (1,), ["roiA"]
            )

        apply_mock.assert_not_called()
        start_mock.assert_not_called()

    def test_public_alias_delegates_to_the_same_implementation(self) -> None:
        """calculate_sensorgram_for_range (called by the live-preview prompt)
        must go through the exact same guarded path as the button-wired
        _calculate_sensorgram_for_range - there is only one implementation
        now, not two diverging ones."""
        window = _FakeWindow()
        controller = AnalysisController(window)

        with mock.patch.object(controller, "_calculate_sensorgram_for_range") as impl_mock:
            controller.calculate_sensorgram_for_range()

        impl_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
