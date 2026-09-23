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
    from lspr_imaging_app.analysis import AnalysisSettingsModule
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
    from lspr_imaging_app.image_tools.preprocess import apply_preprocessing
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.rasterize import rasterize_sample
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


class RewriteBackgroundExclusionTest(unittest.TestCase):
    """The background-exclusion port gap, closed 2026-09-23.

    `compute_cell` called `apply_preprocessing` with neither `rois` nor
    `mask_settings`, which left both of `BackgroundSettings`' exclusion
    toggles inert on the analysis path - including
    `flatten_background_exclude_area_rois`, which defaults **on**. Nothing
    raised and nothing looked wrong; the background estimate simply had
    every ROI's bright spot pulling the local average up under it.

    Both tests here fail if the fix is reverted, and they fail for
    different reasons on purpose: the first pins the computed *value*
    against an independent reference, the second pins the *invalidation*
    that value now depends on. Getting only one of them right is the
    dangerous half-fix - excluding ROIs from the background without
    fingerprinting them means a cell silently keeps a value computed
    against a background that no longer exists.
    """

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
        self.engine = _build_analysis_engine(
            self.dataset, self.geometry, self.mask, self.chromatic, self.background, self.roi_toolbox
        )
        self.dataset.load_dataset(self.dataset_model)
        self.engine.set_storage_root(self.dataset_model.home)
        self._enable_flattening(exclude_area_rois=True)

    def tearDown(self) -> None:
        from lspr_imaging_app.undo import undo_manager

        undo_manager.clear()
        self._tmp.cleanup()

    def _enable_flattening(self, *, exclude_area_rois: bool) -> None:
        # binning=1 so the reference computation below compares against the
        # exact same code path, with no binned/upsampled approximation in
        # between to explain a difference away.
        self.background.set_flatten_background_settings(
            enabled=True, sigma_px=12.0, binning=1,
            exclude_area_rois=exclude_area_rois, exclude_mask=False,
            exclusion_dilation_px=0, local_reference_normalization_enabled=False,
        )

    def _place_two_rois(self) -> tuple[int, int]:
        return (
            self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=3.0),
            self.roi_toolbox.add_roi(47.0, 34.0, sample_radius_px=3.0),
        )

    def _run_to_completion(self) -> None:
        done: list[bool] = []
        self.engine.analysis_complete.connect(lambda: done.append(True))
        self.engine.run_analysis(AnalysisScope.ALL_ROIS)
        deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS
        while not done and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(done, "analysis never completed")

    def _reference_sample_mean(self, roi_id: int, *, with_rois: bool) -> float:
        """The sample mean for cube 0 / 500 nm computed straight from the
        pure functions, bypassing the engine entirely - so this asserts
        against the pipeline's own arithmetic rather than against a number
        copied out of a previous run."""
        roi = self.roi_toolbox.roi_by_id(roi_id)
        raw = self.dataset.load_plane(0, 500.0)
        processed = apply_preprocessing(
            raw, self.geometry.settings(), self.background.settings(),
            rois=list(self.roi_toolbox.rois()) if with_rois else None,
            mask_settings=self.roi_toolbox.detection_settings() if with_rois else None,
        )
        affine = self.chromatic.affine_for((0, 500.0))
        mask = rasterize_sample(roi, processed.shape[:2], affine)
        return float(np.mean(processed[mask]))

    def test_rois_actually_reach_the_background_estimate(self) -> None:
        roi_a, _roi_b = self._place_two_rois()
        self._run_to_completion()

        stored = self.engine.get_spectrum(roi_a, 0)
        self.assertIsNotNone(stored)
        at_500nm = stored.sample_values[stored.wavelengths_nm.index(500.0)]

        excluded = self._reference_sample_mean(roi_a, with_rois=True)
        not_excluded = self._reference_sample_mean(roi_a, with_rois=False)

        # The two references have to differ, or this test proves nothing -
        # it would pass just as happily against the unfixed code.
        self.assertGreater(
            abs(excluded - not_excluded), 1.0,
            "the ROI exclusion changes nothing here, so this test cannot detect the bug",
        )
        self.assertAlmostEqual(at_500nm, excluded, delta=1e-3)

    def test_moving_one_roi_invalidates_another_rois_stored_cells(self) -> None:
        """With ROI exclusion on, ROI A's spot is cut out of the background
        estimate under ROI B - so moving A genuinely changes B's value and
        B's stored cells must be planned for recompute.

        Reference-ring exclusion is turned off for this test *only*: it
        already invalidates every cell on any ROI move (by design, see
        `sample_exclusion_digest`), which would mask whether the background
        half works at all."""
        self.engine._reference_exclusion_mode = lambda: "none"
        _roi_a, roi_b = self._place_two_rois()
        self._run_to_completion()
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)

        self.roi_toolbox.move_roi(_roi_a, 41.5, 31.0)
        planned = self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute
        self.assertIn((roi_b, 0), planned)
        self.assertIn((roi_b, 1), planned)

    def test_an_untouched_roi_is_left_alone_when_the_background_ignores_rois(self) -> None:
        """The other side of the same coin: with the exclusion off, other
        ROIs are genuinely not an input, so moving one must **not** drag
        every other cell into a recompute."""
        self.engine._reference_exclusion_mode = lambda: "none"
        self._enable_flattening(exclude_area_rois=False)
        roi_a, roi_b = self._place_two_rois()
        self._run_to_completion()
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)

        self.roi_toolbox.move_roi(roi_a, 41.5, 31.0)
        planned = self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute
        self.assertIn((roi_a, 0), planned)
        self.assertNotIn((roi_b, 0), planned)


class RewriteAnalysisFailureReportingTest(unittest.TestCase):
    """A task exception used to vanish: `threading.Thread(target=task)`
    hands it to `threading.excepthook`, i.e. to a stderr a packaged build
    has nowhere to show, and `analysis_complete` was never emitted - so the
    UI would wait for a run that had already died."""

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
        self.engine = _build_analysis_engine(
            self.dataset, self.geometry, self.mask, self.chromatic, self.background, self.roi_toolbox
        )
        self.dataset.load_dataset(self.dataset_model)
        self.engine.set_storage_root(self.dataset_model.home)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_failing_cell_still_completes_the_run_and_records_the_error(self) -> None:
        def _explode(*_args: object) -> object:
            raise RuntimeError("pixel loading blew up")

        self.engine._load_plane = _explode
        self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=3.0)

        done: list[bool] = []
        self.engine.analysis_complete.connect(lambda: done.append(True))
        with self.assertLogs("lspr_imaging_app.analysis.worker", level="ERROR"):
            self.engine.run_analysis(AnalysisScope.ALL_ROIS)
            deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS
            while not done and time.monotonic() < deadline:
                _APP.processEvents()
                time.sleep(0.01)

        self.assertTrue(done, "analysis_complete was never emitted after a failure")
        self.assertIsInstance(self.engine._worker.last_error, RuntimeError)


class RewriteQueryLayerTest(unittest.TestCase):
    """Pillar I's layers 2-3 over a real store (built 2026-09-23).

    The claim this exists to pin is the one the whole design rests on:
    `compute_cell` stores raw reduced (sample, reference) pairs and nothing
    else, so changing the formula, the fit or the metric must re-derive from
    memory and recompute **nothing**. If that ever stops being true, the
    symptom is not a wrong number - it is a full dataset recompute every
    time someone switches a combo box, which on a real dataset is hours."""

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
        self.analysis_settings = AnalysisSettingsModule()
        self.engine = _build_analysis_engine(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.analysis_settings,
        )
        self.dataset.load_dataset(self.dataset_model)
        self.engine.set_storage_root(self.dataset_model.home)
        self.roi = self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=3.0)
        self._run_to_completion()

    def tearDown(self) -> None:
        from lspr_imaging_app.undo import undo_manager

        undo_manager.clear()
        self._tmp.cleanup()

    def _run_to_completion(self) -> None:
        done: list[bool] = []
        self.engine.analysis_complete.connect(lambda: done.append(True))
        self.engine.run_analysis(AnalysisScope.ALL_ROIS)
        deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS
        while not done and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(done, "analysis never completed")

    # -- layer 2 --------------------------------------------------------

    def test_a_formula_spectrum_is_derived_from_the_stored_pairs(self) -> None:
        stored = self.engine.get_spectrum(self.roi, 0)
        spectrum = self.engine.formula_spectrum(self.roi, 0)
        self.assertIsNotNone(spectrum)
        self.assertEqual(spectrum.wavelengths_nm.tolist(), list(stored.wavelengths_nm))
        expected = np.log10(
            np.asarray(stored.reference_values) / np.asarray(stored.sample_values)
        )
        np.testing.assert_allclose(spectrum.values, expected, rtol=1e-9)

    def test_an_unanalyzed_cell_is_none_and_never_computes_on_read(self) -> None:
        """Sketch §7: a panel shows "needs analysis"; nothing computes
        behind the user's back."""
        unknown_roi = self.roi_toolbox.add_roi(20.0, 20.0, sample_radius_px=3.0)
        self.assertIsNone(self.engine.formula_spectrum(unknown_roi, 0))
        self.assertIsNone(self.engine.get_metric(unknown_roi, 0))

    # -- the central claim ----------------------------------------------

    def test_changing_the_formula_recomputes_nothing_but_changes_the_values(self) -> None:
        before = self.engine.formula_spectrum(self.roi, 0).values.copy()
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)

        self.roi_toolbox.set_detection_settings(
            replace(self.roi_toolbox.detection_settings(), formula_key="ratio")
        )

        after = self.engine.formula_spectrum(self.roi, 0).values
        self.assertEqual(
            len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0,
            "a formula change must never invalidate a stored cell",
        )
        self.assertFalse(np.allclose(before, after), "the formula change had no effect on the values")

    def test_changing_the_metric_settings_recomputes_nothing(self) -> None:
        self.engine.get_metric(self.roi, 0)
        self.analysis_settings.set_metric_settings(
            replace(self.analysis_settings.metric_settings(), metric_key="centroid")
        )
        self.assertEqual(len(self.engine.preview_recompute(AnalysisScope.ALL_ROIS).to_recompute), 0)

    def test_the_cache_follows_the_settings_rather_than_a_subscription(self) -> None:
        """No signal wiring keeps the cache honest - the fingerprint does.
        A changed setting simply misses, which is why nothing has to
        remember to invalidate."""
        first = self.engine.get_metric(self.roi, 0)

        # Narrowing the fit window, rather than switching fit method: this
        # dataset's spectrum rises monotonically to 600 nm, so every fit
        # method agrees on the peak and a method change could not tell a
        # live cache from a stale one. A window change moves the answer
        # whatever the fit does.
        self.analysis_settings.set_metric_settings(
            replace(self.analysis_settings.metric_settings(), fit_wl_max=550.0)
        )
        second = self.engine.get_metric(self.roi, 0)

        self.assertGreater(first, 550.0)
        self.assertLessEqual(second, 550.0)

    def test_clearing_the_dataset_drops_derived_values_too(self) -> None:
        """A (roi_id, cube_index) key means something different under a
        different dataset."""
        self.engine.get_metric(self.roi, 0)
        self.assertTrue(self.engine._metric_cache)
        self.engine.set_storage_root(None)
        self.assertFalse(self.engine._metric_cache)

    # -- layer 3 traces --------------------------------------------------

    def test_a_trace_keeps_its_length_when_a_cube_is_missing(self) -> None:
        """NaN, not a shorter array: every ROI's trace has to stay aligned
        to the same x axis for the group aggregation to stack them, and
        dropping a point would shift every later point left."""
        lonely = self.roi_toolbox.add_roi(20.0, 20.0, sample_radius_px=3.0)
        trace = self.engine.metric_trace(lonely)
        self.assertEqual(trace.size, len(self.dataset.spectral_cubes()))
        self.assertTrue(np.all(np.isnan(trace)))

    def test_traces_are_computed_off_the_gui_thread(self) -> None:
        received: list[dict] = []
        self.engine.metric_traces_ready.connect(received.append)
        self.engine.request_metric_traces((self.roi,))

        deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS
        while not received and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)

        self.assertTrue(received, "metric_traces_ready was never emitted")
        self.assertIn(self.roi, received[0])
        self.assertEqual(received[0][self.roi].size, len(self.dataset.spectral_cubes()))


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
