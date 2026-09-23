"""End-to-end tests for the LSPRimaging Evaluation rewrite's analysis engine.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why the
whole file skips otherwise.

Real TIFF files written to a temp directory, a real `data.h5`, and the real
module graph `app_rewrite._build_analysis_engine` wires up - not fakes. The
point of these is the properties that only appear once the whole stack runs
together: per-cube wavelengths surviving into a computed cell, and
provenance dedup actually deduplicating.

The dedup tests deliberately set an ignore mask. That is not incidental:
until 2026-09-23 the planner substituted a placeholder mask version while
`compute_cell` recorded the real one, so *with a mask present* every cell
looked permanently stale and the whole provenance design was inert. Without
a mask the bug is invisible, which is exactly how it survived its own
verification pass.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from dataclasses import replace
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
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
    )
    from lspr_imaging_app.roi import RoiToolbox
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_RUN_TIMEOUT_SECONDS = 120.0


def _write_dataset(root: Path) -> ImageDataset:
    """Two cubes x three wavelengths, except cube 1 never recorded 550 nm."""
    records = []
    rng = np.random.default_rng(7)
    base = rng.uniform(900.0, 1100.0, size=(64, 80)).astype(np.float32)
    for cube in (0, 1):
        for wavelength in (500.0, 550.0, 600.0):
            if cube == 1 and wavelength == 550.0:
                continue
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            frame = base + cube * 40.0 + (wavelength - 500.0) * 0.5
            frame[28:33, 38:43] += 5000.0
            frame[32:37, 45:50] += 5000.0
            tifffile.imwrite(str(path), frame)
            records.append(
                ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path)
            )
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class RewriteAnalysisEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)

        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.mask = MaskModule()
        self.chromatic = ChromaticModule()
        self.background = BackgroundModule()
        self.roi_toolbox = RoiToolbox()
        self.engine = self._new_engine()
        self.dataset.load_dataset(self.dataset_model)

        # A mask in a corner, away from both ROIs - present so the dedup
        # tests exercise the mask-version path, not so it changes any value.
        painted = np.zeros((64, 80), dtype=bool)
        painted[0:6, 0:6] = True
        self.mask.set_mask_change((0, 500.0), "persistent", painted)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _new_engine(self):
        """A second engine over the same modules and folder - what an app
        restart looks like from the store's point of view."""
        return _build_analysis_engine(
            self.dataset, self.geometry, self.mask, self.chromatic, self.background, self.roi_toolbox
        )

    def _place_two_rois(self) -> tuple[int, int]:
        return (
            self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=3.0),
            self.roi_toolbox.add_roi(47.0, 34.0, sample_radius_px=3.0),
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

    # -- storage root ---------------------------------------------------

    def test_run_refuses_without_a_storage_root(self) -> None:
        """Otherwise data.h5 and every snapshot land in whatever the
        process's working directory happens to be - results that compute
        fine and are then never found again."""
        self._place_two_rois()
        with self.assertRaises(RuntimeError):
            self.engine.run_analysis(AnalysisScope.ALL_ROIS)

    def test_storage_root_places_the_store_beside_the_dataset(self) -> None:
        self.engine.set_storage_root(self.dataset_model.home)
        self.assertEqual(self.engine.storage_root(), self.root)
        self.assertEqual(self.engine._data_h5_path, self.root / "analysis" / "data.h5")

    def test_clearing_the_storage_root_drops_results(self) -> None:
        roi_a, _roi_b = self._place_two_rois()
        self.engine.set_storage_root(self.dataset_model.home)
        self._run_to_completion(self.engine)
        self.assertIsNotNone(self.engine.get_spectrum(roi_a, 0))
        self.engine.set_storage_root(None)
        self.assertIsNone(self.engine.get_spectrum(roi_a, 0))

    # -- computing ------------------------------------------------------

    def test_per_cube_wavelengths_survive_into_the_stored_cell(self) -> None:
        """Cube 1 is short 550 nm, so its cells must hold two wavelengths
        while cube 0's hold three - driving the loop off the dataset-global
        wavelength list would instead raise on a plane that isn't there."""
        roi_a, roi_b = self._place_two_rois()
        self.engine.set_storage_root(self.dataset_model.home)
        self._run_to_completion(self.engine)

        cube0 = self.engine.get_spectrum(roi_a, 0)
        cube1 = self.engine.get_spectrum(roi_b, 1)
        self.assertIsNotNone(cube0)
        self.assertIsNotNone(cube1)
        self.assertEqual(cube0.wavelengths_nm, (500.0, 550.0, 600.0))
        self.assertEqual(cube1.wavelengths_nm, (500.0, 600.0))
        self.assertTrue(all(np.isfinite(v) for v in cube0.sample_values))
        self.assertTrue((self.root / "analysis" / "data.h5").exists())

    # -- dedup ----------------------------------------------------------

    def test_nothing_changed_means_nothing_recomputes(self) -> None:
        """The regression guard for the placeholder-mask-version bug: with
        a mask set, this used to plan a full recompute forever."""
        self._place_two_rois()
        self.engine.set_storage_root(self.dataset_model.home)
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 4)
        self._run_to_completion(self.engine)
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)

    def test_a_restart_rehydrates_and_still_plans_nothing(self) -> None:
        roi_a, _roi_b = self._place_two_rois()
        self.engine.set_storage_root(self.dataset_model.home)
        self._run_to_completion(self.engine)
        original = self.engine.get_spectrum(roi_a, 0)

        restarted = self._new_engine()
        restarted.set_storage_root(self.dataset_model.home)
        rehydrated = restarted.get_spectrum(roi_a, 0)
        self.assertIsNotNone(rehydrated)
        self.assertEqual(rehydrated.sample_values, original.sample_values)
        self.assertEqual(len(restarted.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)

    def test_changing_the_reduction_method_invalidates_every_cell(self) -> None:
        self._place_two_rois()
        self.engine.set_storage_root(self.dataset_model.home)
        self._run_to_completion(self.engine)
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)

        self.roi_toolbox.set_detection_settings(
            replace(self.roi_toolbox.detection_settings(), reduction_method="median")
        )
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 4)


class RewriteDetectionSettingsOwnershipTest(unittest.TestCase):
    """`RoiToolbox` became the owner of `AreaRoiDetectionSettings` on
    2026-09-23 - nothing owned it before, every module took it as a
    parameter, so the engine had nowhere to read the reference radii and
    reduction method from."""

    def setUp(self) -> None:
        self.roi_toolbox = RoiToolbox()

    def test_query_returns_a_defensive_copy(self) -> None:
        settings = self.roi_toolbox.detection_settings()
        settings.reduction_method = "median"
        self.assertEqual(self.roi_toolbox.detection_settings().reduction_method, "mean")

    def test_set_and_undo(self) -> None:
        from lspr_imaging_app.undo import undo_manager

        self.roi_toolbox.set_detection_settings(
            replace(self.roi_toolbox.detection_settings(), reduction_method="median")
        )
        self.assertEqual(self.roi_toolbox.detection_settings().reduction_method, "median")
        undo_manager.undo()
        self.assertEqual(self.roi_toolbox.detection_settings().reduction_method, "mean")

    def test_setting_the_same_value_is_a_no_op(self) -> None:
        changes: list[str] = []
        self.roi_toolbox.geometry_changed.connect(lambda change: changes.append(change.reason))
        self.roi_toolbox.set_detection_settings(self.roi_toolbox.detection_settings())
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
