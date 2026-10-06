"""End-to-end: stored analysis results follow their ROI through a renumber.

Real TIFFs, a real `data.h5`, the real engine, and the exact wiring function
the app uses (`app_rewrite._connect_roi_renumbering`). The pure parts (file
rename, digests, the signal's contract) are in
`tests/unit/test_lspri_rewrite_roi_renumber.py`.

The bug these guard against: results are filed under the ROI's id, so before
this fix, deleting ROI 1 left ROI 1's spectrum filed under id 1 - which now
belongs to the old ROI 2 - and the display showed the wrong ROI's data until
the next run.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.planner import AnalysisScope
    from lspr_imaging_app.app_rewrite import _build_analysis_engine, _connect_roi_renumbering
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import AreaRoi
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_analysis_engine import _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_RUN_TIMEOUT_SECONDS = 120.0
_A = (40.0, 30.0)  # ROI placed first
_B = (47.0, 34.0)  # ROI placed second - a different bright spot, so a different spectrum


class RoiRenumberFollowedByAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)

        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.mask = MaskModule()
        self.chromatic = ChromaticModule()
        self.background = BackgroundModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.engine = self._new_engine()
        _connect_roi_renumbering(self.roi_toolbox, self.selection, self.engine)
        self.dataset.load_dataset(self.dataset_model)
        self.engine.set_storage_root(self.dataset_model.home)

        # Reference-ring exclusion would (correctly) mark the survivors stale
        # after a delete, because a circle that used to be cut out of their
        # ring is gone. Off here so these tests isolate the *numbering*.
        self.engine._reference_exclusion_mode = lambda: "none"

        self.id_a = self.roi_toolbox.add_roi(*_A, sample_diameter_px=6.0)
        self.id_b = self.roi_toolbox.add_roi(*_B, sample_diameter_px=6.0)
        self._run_to_completion(self.engine)
        self.a_values = self.engine.get_spectrum(self.id_a, 0).sample_values
        self.b_values = self.engine.get_spectrum(self.id_b, 0).sample_values
        self.assertNotEqual(self.a_values, self.b_values, "the two ROIs must have distinguishable spectra")

    def tearDown(self) -> None:
        undo_manager.clear()
        self._tmp.cleanup()

    def _new_engine(self):
        return _build_analysis_engine(
            self.dataset, self.geometry, self.mask, self.chromatic, self.background, self.roi_toolbox
        )

    def _run_to_completion(self, engine) -> None:
        done: list[bool] = []
        engine.analysis_complete.connect(lambda: done.append(True))
        engine.run_analysis(AnalysisScope.ALL_ROIS)
        deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS
        while not done and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(done, "analysis never completed")

    def _restarted(self):
        """What reopening the session looks like: a new engine, rehydrated
        from `data.h5`, wired to the same toolbox and exclusion mode."""
        engine = self._new_engine()
        engine._reference_exclusion_mode = lambda: "none"
        engine.set_storage_root(self.dataset_model.home)
        return engine

    def _planned(self, engine) -> int:
        return len(engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute)

    # -- delete ---------------------------------------------------------

    def test_deleting_a_roi_moves_the_survivors_results_with_them(self) -> None:
        metric_before = self.engine.get_metric(self.id_b, 0)  # also warms the metric cache
        self.selection.set_roi_selection({self.id_b})

        self.roi_toolbox.delete_rois((self.id_a,))

        self.assertEqual(self.engine.get_spectrum(1, 0).sample_values, self.b_values)
        self.assertIsNone(self.engine.get_spectrum(2, 0))
        self.assertEqual(self.engine.get_metric(1, 0), metric_before)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1}))
        self.assertEqual(self._planned(self.engine), 0, "a result that followed its ROI is still valid")

    def test_the_renumbered_results_survive_a_restart(self) -> None:
        self.roi_toolbox.delete_rois((self.id_a,))

        restarted = self._restarted()
        self.assertEqual(restarted.get_spectrum(1, 0).sample_values, self.b_values)
        self.assertIsNone(restarted.get_spectrum(2, 0))
        self.assertEqual(self._planned(restarted), 0)

    def test_undoing_the_delete_puts_the_results_back_under_their_old_number(self) -> None:
        self.roi_toolbox.delete_rois((self.id_a,))
        undo_manager.undo()

        self.assertEqual(self.engine.get_spectrum(2, 0).sample_values, self.b_values)
        self.assertIsNone(self.engine.get_spectrum(1, 0), "the deleted ROI's result is not kept")
        # Only the restored ROI 1 is left to analyze: two cubes.
        self.assertEqual(self._planned(self.engine), 2)

    def test_deleting_every_roi_leaves_nothing_for_the_next_roi_to_inherit(self) -> None:
        self.roi_toolbox.delete_rois((self.id_a, self.id_b))
        new_id = self.roi_toolbox.add_roi(10.0, 10.0, sample_diameter_px=6.0)

        self.assertEqual(new_id, 1)
        self.assertIsNone(self.engine.get_spectrum(1, 0))
        self.assertIsNone(self._restarted().get_spectrum(1, 0))

    # -- fresh detection ------------------------------------------------

    def test_a_fresh_detection_does_not_hand_old_results_to_new_rois(self) -> None:
        detected = [AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_diameter_px=6.0)]
        self.roi_toolbox.detect_rois(detected)
        self.assertIsNone(self.engine.get_spectrum(1, 0))

        undo_manager.undo()  # the old ROIs are back, but their results are not
        self.assertIsNone(self.engine.get_spectrum(1, 0))
        self.assertIsNone(self.engine.get_spectrum(2, 0))

    # -- reorder --------------------------------------------------------

    def test_reordering_two_rois_keeps_each_result_with_its_roi(self) -> None:
        """The user swaps rows 1 and 2 (`reorder_rois`). Run in the mode that
        records every ROI's circle in the fingerprint, which is where ids in
        the digest used to mark every cell stale."""
        self.engine._reference_exclusion_mode = lambda: "exclude_all_sample_rois"
        self._run_to_completion(self.engine)
        self.assertEqual(self._planned(self.engine), 0)
        self.selection.set_roi_selection({self.id_a})

        self.roi_toolbox.reorder_rois((self.id_b, self.id_a))

        self.assertEqual(self.roi_toolbox.roi_by_id(1).center_x, _B[0])
        self.assertEqual(self.engine.get_spectrum(1, 0).sample_values, self.b_values)
        self.assertEqual(self.engine.get_spectrum(2, 0).sample_values, self.a_values)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({2}), "the selection follows its ROI")
        self.assertEqual(self._planned(self.engine), 0, "same circles, different numbers: nothing is stale")

        restarted = self._restarted()
        restarted._reference_exclusion_mode = lambda: "exclude_all_sample_rois"
        self.assertEqual(restarted.get_spectrum(1, 0).sample_values, self.b_values)
        self.assertEqual(restarted.get_spectrum(2, 0).sample_values, self.a_values)
        self.assertEqual(self._planned(restarted), 0)

        undo_manager.undo()
        self.assertEqual(self.engine.get_spectrum(1, 0).sample_values, self.a_values)
        self.assertEqual(self.engine.get_spectrum(2, 0).sample_values, self.b_values)
        self.assertEqual(self._planned(self.engine), 0)

    # -- concurrency ----------------------------------------------------

    def test_a_renumber_during_a_run_cancels_the_run_first(self) -> None:
        started = threading.Event()
        worker = self.engine._worker
        worker.submit(lambda: (started.set(), worker.cancel_event.wait(30.0)))
        self.assertTrue(started.wait(5.0))
        self.assertTrue(self.engine.is_running())

        self.engine.remap_roi_ids({1: 1, 2: 2})

        self.assertFalse(self.engine.is_running())

    def test_traces_computed_across_a_renumber_are_discarded(self) -> None:
        """A trace request that was in flight when ids changed holds values for
        the old numbering; emitting it would plot ROI 1's curve as ROI 2's."""
        emitted: list[object] = []
        self.engine.metric_traces_ready.connect(emitted.append)
        original = self.engine.metric_trace

        def trace_then_renumber(roi_id: int, cube_indices=None):
            trace = original(roi_id, cube_indices)
            self.engine.remap_roi_ids({1: 2, 2: 1})  # the renumber lands mid-request
            return trace

        self.engine.metric_trace = trace_then_renumber
        self.engine.request_metric_traces((self.id_a,))
        deadline = time.monotonic() + 30.0
        while self.engine._derived_worker.is_running() and time.monotonic() < deadline:
            time.sleep(0.01)
        _APP.processEvents()

        self.assertFalse(self.engine._derived_worker.is_running())
        self.assertEqual(emitted, [])


if __name__ == "__main__":
    unittest.main()
