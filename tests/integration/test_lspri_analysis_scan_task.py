"""`ScanTask` (analysis step 1 on a task thread): progress, Cancel, refusal, the stale-run rule, logging, and that an edit made after
`start()` cannot change the result (2026-10-23).

A real `QApplication` (the result comes back through a queued signal) but no widgets and no real dataset: three small TIFF cubes of
synthetic pixels and a `ScanSource` made of plain callables. The arithmetic itself is pinned by `test_lspri_rewrite_scan.py`; here the
question is the task around it. Design: apps/LSPRi/eva/docs/analysis_redesign_plan_2026-10-19.md, sections 15-16.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import scan_jobs, scan_store
    from lspr_imaging_app.analysis.scan import scan_cubes
    from lspr_imaging_app.analysis.scan_arrays import new_arrays, place
    from lspr_imaging_app.analysis.scan_jobs import MaskRef, ScanSource, build_scan_plan, snapshot_source
    from lspr_imaging_app.analysis.scan_task import TASK_ID, ScanSummary, ScanTask
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 80, 110
WAVELENGTHS = (0.0, 500.0, 510.0)  # 0.0 is the dark frame
CUBES = 3
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
TASK_LOGGER = "lspr_imaging_app.analysis.scan_task"


def _rois(ids=tuple(range(1, 9)), shift=0.0):
    return [AreaRoi(i, 18.0 + 25.0 * (k % 4) + shift, 18.0 + 24.0 * (k // 4), 10.0, reference_inner_diameter_px=14.0,
                    reference_outer_diameter_px=20.0) for k, i in enumerate(ids)]


def _pump(until, seconds: float = 60.0) -> None:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


class ScanTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        rng = np.random.default_rng(7)
        y, x = np.mgrid[0:H, 0:W]
        cls.paths = {}
        for cube in range(CUBES):
            for wl in WAVELENGTHS:
                frame = (3000.0 + 9.0 * x + 5.0 * y + 40.0 * cube + wl + rng.normal(0, 25.0, (H, W))).astype(np.float32)
                path = Path(cls.tmp.name) / f"c{cube}_w{int(wl)}.tif"
                tifffile.imwrite(path, frame)
                cls.paths[(cube, wl)] = str(path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.rois = _rois()
        self.rois_by_cube: dict[int, list] | None = None
        self.affine_override: dict = {}
        self.results: list[tuple] = []
        self.task = ScanTask(on_result=lambda plan, arrays, summary: self.results.append((plan, arrays, summary)))
        self.progress: list[tuple] = []
        self.finished: list[tuple] = []
        self.failed: list[str] = []
        self.completed: list[ScanSummary] = []
        self.task.task_progress.connect(lambda *args: self.progress.append(args))
        self.task.task_finished.connect(lambda *args: self.finished.append(args))
        self.task.failed.connect(self.failed.append)
        self.task.completed.connect(self.completed.append)

    def tearDown(self) -> None:
        self.task.shutdown()

    def _source(self) -> ScanSource:
        return ScanSource(
            cube_indices=lambda: tuple(range(CUBES)),
            wavelengths_for_cube=lambda c: WAVELENGTHS,
            is_spectral=lambda c, w: w != 0.0,
            record_path=lambda c, w: self.paths[(c, w)],
            rois=lambda: self.rois,
            rois_at=lambda c: self.rois_by_cube[c] if self.rois_by_cube is not None else self.rois,
            has_timeline=lambda: self.rois_by_cube is not None,
            chromatic_affine=lambda c, w: self.affine_override.get((c, w), IDENTITY),
            chromatic_affine_between=lambda a, b: IDENTITY,
            resolve_mask=lambda c, w: MaskRef((c, w), "individual", lambda: _mask()) if (c, w) == (1, 500.0) else None,
            geometry=lambda: GeometrySettings(rotation_angle_deg=1.0),
            background=lambda: BackgroundSettings(flatten_background_enabled=True, flatten_background_sigma_px=10.0,
                                                  flatten_background_binning=2, flatten_background_exclude_mask=True),
            detection=lambda: AreaRoiDetectionSettings(ignore_marked_pixels=True),
            default_reference_diameters=lambda: (14.0, 20.0),
        )

    def _run(self, **kwargs) -> None:
        self.task.start(self._source(), **kwargs)
        _pump(lambda: bool(self.finished or self.failed))
        _APP.processEvents()

    def _expected(self):
        plan = build_scan_plan(snapshot_source(self._source()))
        arrays = new_arrays(plan)
        for result in scan_cubes(plan.jobs, plan.settings, processes=1):
            place(arrays, plan, result)
        return arrays

    # -- a normal run --------------------------------------------------------------------------------------------------------

    def test_a_scan_completes_reports_progress_and_hands_over_the_arrays(self) -> None:
        with self.assertLogs(TASK_LOGGER, level="INFO") as logs:
            self.task.start(self._source(), processes=1)
            self.assertTrue(self.task.is_running())  # start() returned at once; the work is on the task's thread
            _pump(lambda: bool(self.finished))
            _APP.processEvents()
        self.assertEqual([f[:2] for f in self.finished], [(TASK_ID, "completed")])
        self.assertEqual(self.failed, [])
        fractions = [p[2] for p in self.progress]
        self.assertTrue(fractions and all(0.0 <= f <= 1.0 for f in fractions))
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(fractions[-1], 1.0)
        self.assertTrue(all(p[0] == TASK_ID for p in self.progress))

        plan, arrays, summary = self.results[0]
        self.assertEqual((arrays.n_done, summary.cubes, summary.frames, summary.rois, summary.wavelengths), (3, 3, 6, 8, 2))
        expected = self._expected()
        self.assertTrue(np.array_equal(arrays.values["mean"], expected.values["mean"], equal_nan=True))
        self.assertTrue(np.array_equal(arrays.std, expected.std, equal_nan=True))
        self.assertTrue(np.array_equal(arrays.n_valid, expected.n_valid))
        self.assertTrue(np.isfinite(arrays.values["mean"]).any())
        self.assertIn("8 ROIs x 3 cubes x 2 wavelengths scanned", self.finished[0][2])
        self.assertEqual(self.completed[0].mode, "explicit")

        text = "\n".join(logs.output)
        for event in ("analysis.scan_prepared", "analysis.scan_start", "analysis.scan_done"):
            self.assertIn(event, text)
        done = [line for line in logs.output if "analysis.scan_done" in line][0]
        for field in ("frames_per_s=", "busy_read_s=", "busy_prepare_s=", "busy_reduce_s="):
            self.assertIn(field, done)

    def test_per_cube_timings_are_logged_at_debug_level(self) -> None:
        with self.assertLogs(TASK_LOGGER, level="DEBUG") as logs:
            self._run(processes=1)
        self.assertEqual(sum("analysis.scan_cube" in line for line in logs.output), CUBES)

    def test_worker_processes_give_the_same_arrays_as_one_process(self) -> None:
        self._run(processes=2)
        self.assertEqual(self.finished[0][1], "completed")
        self.assertEqual(self.completed[0].workers, 2)
        expected = self._expected()
        self.assertTrue(np.array_equal(self.results[0][1].values["mean"], expected.values["mean"], equal_nan=True))
        self.assertTrue(np.array_equal(self.results[0][1].n_nominal, expected.n_nominal))

    def test_the_automatic_worker_count_is_reported_with_its_mode(self) -> None:
        with self.assertLogs(TASK_LOGGER, level="INFO") as logs:
            self._run()
        self.assertEqual(self.finished[0][1], "completed")
        self.assertIn(self.completed[0].mode, {"standard", "saved", "auto", "manual", "fixed"})
        self.assertGreaterEqual(self.completed[0].workers, 1)
        done = [line for line in logs.output if "analysis.scan_done" in line][0]
        self.assertIn("perf_workers=", done)
        self.assertIn("perf_mode=", done)

    def test_a_cube_subset_scans_only_those_cubes(self) -> None:
        self._run(cubes=[2, 0], processes=1)
        plan, arrays, summary = self.results[0]
        self.assertEqual(plan.cube_indices, (2, 0))
        self.assertEqual(summary.cubes, 2)

    # -- saving to scan.h5 ---------------------------------------------------------------------------------------------------

    def _store_path(self) -> Path:
        return Path(self.tmp.name) / f"session{len(list(Path(self.tmp.name).glob('session*')))}" / "analysis" / "scan.h5"

    def _rerun(self, path: Path, **kwargs) -> None:
        self.finished.clear()
        self.results.clear()
        self.completed.clear()
        self._run(store_path=path, processes=1, **kwargs)

    def test_the_scan_is_saved_and_reads_back_equal(self) -> None:
        path = self._store_path()
        with self.assertLogs(TASK_LOGGER, level="INFO") as logs:
            self._run(store_path=path, processes=1, dataset_label="unit")
        self.assertEqual(self.finished[0][1], "completed")
        self.assertIn("saved to scan.h5", self.finished[0][2])
        stored = scan_store.read_scan(path)
        arrays = self.results[0][1]
        self.assertTrue(np.array_equal(stored.values["mean"], arrays.values["mean"], equal_nan=True))
        self.assertTrue(np.array_equal(stored.n_nominal, arrays.n_nominal))
        self.assertTrue(stored.computed().all())
        self.assertEqual(stored.attrs["dataset"], "unit")
        self.assertEqual(stored.wavelength_nm, (500.0, 510.0))
        done = [line for line in logs.output if "analysis.scan_done" in line][0]
        self.assertIn("write_s=", done)
        self.assertGreater(self.completed[0].file_mb, 0.0)

    def test_a_second_run_on_an_unchanged_session_does_nothing(self) -> None:
        path = self._store_path()
        self._run(store_path=path, processes=1)
        before = path.stat().st_mtime_ns
        with self.assertLogs(TASK_LOGGER, level="INFO") as logs:
            self._rerun(path)
        self.assertEqual(self.finished[0][1], "completed")
        self.assertIn("up to date", self.finished[0][2])
        self.assertEqual(self.completed[0].cubes, 0)
        self.assertEqual(self.completed[0].cubes_current, CUBES)
        self.assertEqual(path.stat().st_mtime_ns, before)  # the file was not touched
        self.assertTrue(any("analysis.scan_current" in line for line in logs.output))

    def test_after_an_edit_only_the_cubes_with_changed_inputs_are_scanned_again(self) -> None:
        path = self._store_path()
        self._run(store_path=path, processes=1)
        first = scan_store.read_scan(path)
        self.affine_override = {(2, 510.0): np.array([[1.002, 0.0, 0.4], [0.0, 1.0, 0.0]])}  # one frame's chromatic correction changes
        self._rerun(path)
        self.assertEqual((self.completed[0].cubes, self.completed[0].cubes_current), (1, CUBES - 1))
        second = scan_store.read_scan(path)
        self.assertTrue(np.array_equal(second.values["mean"][:2], first.values["mean"][:2], equal_nan=True))  # the other cubes were not rewritten
        self.assertFalse(np.array_equal(second.cell_hash[2], first.cell_hash[2]))
        self.assertTrue(second.computed().all())
        self.assertEqual(sorted(second.recipes), [1])  # same recipe

    def test_masks_are_built_only_for_the_cubes_that_are_scanned_again(self) -> None:
        path = self._store_path()
        self._run(store_path=path, processes=1)
        real = scan_jobs.resolve_external_mask
        built: list[int] = []

        def counting(*args, **kwargs):
            built.append(1)
            return real(*args, **kwargs)

        with mock.patch.object(scan_jobs, "resolve_external_mask", counting):
            self.affine_override = {(2, 510.0): np.array([[1.002, 0.0, 0.4], [0.0, 1.0, 0.0]])}  # cube 2 is stale; the masked cube 1 is current
            self._rerun(path)
            self.assertEqual((self.completed[0].cubes, built), (1, []))
            self.affine_override = {}
            built.clear()
            self._rerun(path)  # cube 2 changes back, so it is stale again; cube 1 is still current
            self.assertEqual(built, [])
            self._rerun(path, only_stale=False)  # everything is scanned: the masked cube 1 needs its mask now
            self.assertEqual((self.completed[0].cubes, len(built)), (CUBES, 1))

    def test_run_all_ignores_what_is_current(self) -> None:
        path = self._store_path()
        self._run(store_path=path, processes=1)
        self._rerun(path, only_stale=False)
        self.assertEqual((self.completed[0].cubes, self.completed[0].cubes_current), (CUBES, 0))

    def test_a_cancelled_run_keeps_the_finished_cubes_and_the_next_run_finishes_the_rest(self) -> None:
        path = self._store_path()
        real = scan_store.ScanStore.write_cube
        written = []

        def write_then_cancel(store, plan, arrays, cube_pos, row):
            real(store, plan, arrays, cube_pos, row)
            written.append(cube_pos)
            self.task.cancel()  # after the first cube is safely on disk

        scan_store.ScanStore.write_cube = write_then_cancel
        try:
            self._run(store_path=path, processes=1)
        finally:
            scan_store.ScanStore.write_cube = real
        self.assertEqual(self.finished[0][1], "cancelled")
        self.assertEqual(len(written), 1)
        self.assertEqual(int(scan_store.read_scan(path).computed().any(axis=1).sum()), 1)
        self._rerun(path)  # resume
        self.assertEqual((self.completed[0].cubes, self.completed[0].cubes_current), (CUBES - 1, 1))
        self.assertTrue(scan_store.read_scan(path).computed().all())

    def test_a_file_that_cannot_be_written_ends_the_run_with_a_readable_message(self) -> None:
        blocker = Path(self.tmp.name) / "a_file_not_a_folder"
        blocker.write_text("x")
        self._run(store_path=blocker / "analysis" / "scan.h5", processes=1)
        self.assertEqual(self.finished[0][1], "failed")
        self.assertIn("Could not save the scan file", self.finished[0][2])

    def test_a_partial_run_fits_the_same_file_as_a_full_run(self) -> None:
        path = self._store_path()
        self._run(store_path=path, cubes=[1], processes=1)
        self.assertEqual(self.completed[0].cubes, 1)
        self.assertEqual(list(scan_store.read_scan(path).computed().any(axis=1)), [False, True, False])
        self._rerun(path)
        self.assertEqual((self.completed[0].cubes, self.completed[0].cubes_current), (CUBES - 1, 1))
        self.assertFalse(path.with_name("scan.previous.h5").exists())  # same axes: no replacement

    # -- inputs are captured when start() is called --------------------------------------------------------------------------

    def test_an_edit_after_start_cannot_change_the_result(self) -> None:
        expected = self._expected()
        self.task.start(self._source(), processes=1)
        self.rois[0].center_x = 90.0  # the user drags a ROI while the scan is running
        self.rois.pop()
        _pump(lambda: bool(self.finished))
        _APP.processEvents()
        self.assertEqual(self.finished[0][1], "completed")
        self.assertTrue(np.array_equal(self.results[0][1].values["mean"], expected.values["mean"], equal_nan=True))

    # -- Cancel, refusal, stale runs -----------------------------------------------------------------------------------------

    def test_cancel_ends_the_run_as_cancelled_and_installs_nothing(self) -> None:
        with self.assertLogs(TASK_LOGGER, level="INFO") as logs:
            self.task.start(self._source(), processes=1)
            self.task.cancel()
            _pump(lambda: bool(self.finished))
            _APP.processEvents()
        self.assertEqual([f[:2] for f in self.finished], [(TASK_ID, "cancelled")])
        self.assertEqual(self.results, [])
        self.assertFalse(self.task.is_running())
        self.assertTrue(any("analysis.scan_cancelled" in line for line in logs.output))

    def test_a_dataset_change_during_a_run_drops_the_result(self) -> None:
        self.task.start(self._source(), processes=1)
        self.task.invalidate()
        _pump(lambda: bool(self.finished))
        _APP.processEvents()
        self.assertEqual(self.finished[0][1], "cancelled")
        self.assertIn("dataset", self.finished[0][2])
        self.assertEqual(self.results, [])

    def test_roi_ids_that_differ_between_cubes_are_refused_with_a_message(self) -> None:
        self.rois_by_cube = {0: _rois(), 1: _rois(ids=tuple(range(1, 8)) + (99,)), 2: _rois()}
        self._run(processes=1)
        self.assertEqual([f[:2] for f in self.finished], [(TASK_ID, "failed")])
        self.assertIn("ROI", self.finished[0][2])
        self.assertEqual(self.results, [])

    def test_no_rois_is_refused(self) -> None:
        self.rois = []
        self._run(processes=1)
        self.assertEqual(self.finished[0][1], "failed")
        self.assertIn("no ROIs", self.finished[0][2])

    def test_unknown_cubes_fail_at_start_without_a_run(self) -> None:
        self.task.start(self._source(), cubes=[42], processes=1)
        self.assertEqual(len(self.failed), 1)
        self.assertFalse(self.task.is_running())
        self.assertEqual(self.finished, [])

    def test_a_second_start_while_running_is_refused(self) -> None:
        self.task.start(self._source(), processes=1)
        with self.assertRaises(RuntimeError):
            self.task.start(self._source(), processes=1)
        _pump(lambda: bool(self.finished))


def _mask() -> np.ndarray:
    mask = np.zeros((H, W), dtype=bool)
    mask[20:30, 30:45] = True
    return mask


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()
