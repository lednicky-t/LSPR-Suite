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


class _FakeWindow:
    """Duck-typed stand-in exposing only what
    `AnalysisController._calculate_sensorgram_for_range` actually reads, so
    the guard-ordering logic can be tested without constructing a real
    MainWindow (Qt widgets, live datasets, etc.)."""

    def __init__(self) -> None:
        self._analysis_enabled = True
        self._state = SimpleNamespace(dataset=SimpleNamespace(folder="dataset_folder"))
        self._chromatic_setup_active = False
        self._sensorgram_running = False
        self._sensorgram_running_signature: tuple | None = None
        self._pending_sensorgram_payload = None
        self._analysis_cache_lock = threading.Lock()
        self._sensorgram_cache: OrderedDict = OrderedDict()
        self._workflow_log: list[str] = []
        self._summary_text: str | None = None

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


class SensorgramStartReentrancyTests(unittest.TestCase):
    def test_cache_hit_for_a_different_running_signature_is_queued_not_applied(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        # The current selection's signature already has a cached result...
        cached_signature = ("current-selection-signature",)
        window._sensorgram_cache[cached_signature] = object()
        # ...but a DIFFERENT run is in flight right now.
        window._sensorgram_running = True
        window._sensorgram_running_signature = ("some-other-signature",)

        with mock.patch.object(controller, "_apply_cached_sensorgram_result") as apply_mock, \
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

    def test_cache_hit_with_nothing_running_is_applied_immediately(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        cached_signature = ("current-selection-signature",)
        cached_result = object()
        window._sensorgram_cache[cached_signature] = cached_result

        with mock.patch.object(controller, "_apply_cached_sensorgram_result") as apply_mock, \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._calculate_sensorgram_for_range()

        apply_mock.assert_called_once_with(cached_signature, cached_result, preview=True)
        start_mock.assert_not_called()

    def test_cache_miss_with_nothing_running_starts_the_worker(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)

        with mock.patch.object(controller, "_apply_cached_sensorgram_result") as apply_mock, \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._calculate_sensorgram_for_range()

        apply_mock.assert_not_called()
        start_mock.assert_called_once()

    def test_public_alias_delegates_to_the_same_implementation(self) -> None:
        """calculate_sensorgram_for_range (called by _finish_group_calculation
        and the live-preview prompt) must go through the exact same guarded
        path as the button-wired _calculate_sensorgram_for_range - there is
        only one implementation now, not two diverging ones."""
        window = _FakeWindow()
        controller = AnalysisController(window)

        with mock.patch.object(controller, "_calculate_sensorgram_for_range") as impl_mock:
            controller.calculate_sensorgram_for_range()

        impl_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
