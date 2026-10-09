"""ROI geometry timeline through the real analysis engine: an edit at a cube recomputes only the cubes it changes.

Same stack as `test_lspri_rewrite_analysis_engine.py` (real TIFFs, a real `data.h5`, the engine
`app_rewrite._build_analysis_engine` wires). Design: apps/LSPRi/eva/docs/roi_timeline_design_2026-10-08.md.
One process per file.
"""

from __future__ import annotations

import sys
import tempfile
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
    from lspr_imaging_app.app_rewrite import _build_analysis_engine
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import SCOPE_INDIVIDUAL
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_analysis_engine import _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_TIMEOUT = 120.0


class RoiTimelineEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self.dataset = DatasetModule()
        self.toolbox = RoiToolbox()
        self.engine = _build_analysis_engine(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.toolbox
        )
        self.dataset.load_dataset(self.dataset_model)
        self.roi_a = self.toolbox.add_roi(40.0, 30.0, sample_diameter_px=6.0)  # on the hot square
        self.roi_b = self.toolbox.add_roi(10.0, 50.0, sample_diameter_px=6.0)  # on plain background
        self.engine.set_storage_root(self.dataset_model.home)
        self.run_all()
        undo_manager.clear()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        undo_manager.clear()

    def run_all(self) -> None:
        done: list[bool] = []
        self.engine.analysis_complete.connect(lambda: done.append(True))
        self.engine.run_analysis(AnalysisScope.ALL_ROIS)
        end = time.monotonic() + _TIMEOUT
        while not done and time.monotonic() < end:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(done, "analysis never completed")

    def planned(self) -> set[tuple[int, int]]:
        return set(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute)

    def sample(self, roi_id: int, cube: int) -> float:
        return float(self.engine.get_spectrum(roi_id, cube).sample_values[0])

    def test_nothing_is_planned_before_any_edit(self) -> None:
        self.assertEqual(self.planned(), set())

    def test_a_persistent_edit_at_the_last_cube_recomputes_only_that_cube(self) -> None:
        before_cube0 = self.sample(self.roi_a, 0)
        self.toolbox.move_roi(self.roi_a, 10.0, 20.0, cube=1)  # off the hot square, from cube 1 on
        # cube 0 still follows the base geometry; at cube 1 the ROI moved (and the rings of its neighbour with it)
        self.assertEqual({cube for _roi, cube in self.planned()}, {1})
        self.assertIn((self.roi_a, 1), self.planned())
        self.run_all()
        self.assertEqual(self.planned(), set())
        self.assertEqual(self.sample(self.roi_a, 0), before_cube0)  # cube 0 untouched
        self.assertLess(self.sample(self.roi_a, 1), 0.5 * self.sample(self.roi_a, 0))  # cube 1 left the hot square

    def test_an_individual_edit_changes_exactly_one_cube(self) -> None:
        self.toolbox.move_roi(self.roi_a, 10.0, 20.0, cube=0, scope=SCOPE_INDIVIDUAL)
        self.assertEqual({cube for _roi, cube in self.planned()}, {0})
        self.run_all()
        self.assertLess(self.sample(self.roi_a, 0), 0.5 * self.sample(self.roi_a, 1))

    def test_an_edit_at_cube_zero_with_persistent_scope_reaches_every_cube(self) -> None:
        self.toolbox.move_roi(self.roi_a, 10.0, 20.0, cube=0)
        self.assertEqual({cube for _roi, cube in self.planned()}, {0, 1})

    def test_apply_to_all_cubes_recomputes_the_cubes_before_the_edit_too(self) -> None:
        self.toolbox.move_roi(self.roi_a, 10.0, 20.0, cube=1)
        self.run_all()
        self.toolbox.apply_to_all_cubes([self.roi_a], 1)
        self.assertIn((self.roi_a, 0), self.planned())  # cube 0 now follows the moved ROI
        self.assertNotIn((self.roi_a, 1), self.planned())  # cube 1 did not change
        self.run_all()
        self.assertEqual(self.planned(), set())

    def test_undo_returns_to_the_stored_results_without_recomputing(self) -> None:
        self.toolbox.move_roi(self.roi_a, 10.0, 20.0, cube=1)
        undo_manager.undo()
        self.assertEqual(self.planned(), set())  # the geometry is the original again: the stored cells fit

    def test_a_restart_keeps_the_timeline_aware_plan(self) -> None:
        self.toolbox.move_roi(self.roi_a, 10.0, 20.0, cube=1)
        self.run_all()
        restarted = _build_analysis_engine(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.toolbox
        )
        restarted.set_storage_root(self.dataset_model.home)
        self.assertEqual(len(restarted.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)


if __name__ == "__main__":
    unittest.main()
