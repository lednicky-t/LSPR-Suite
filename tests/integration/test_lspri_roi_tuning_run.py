"""ROI position tuning, step C2 (`analysis/roi_tuning_run.py`): the lattice scan on a tiny synthetic dataset (real TIFF files, the real `scan_cubes`).

What has to hold: the centre position of the matrix is the ordinary scan, number for number; a position is exactly what a scan of a ROI table with those ROIs
moved gives (so a shifted copy is "just a ROI at another place"); ROIs that do not touch each other read the same pixels alone and together; the numbers do not
depend on the worker count; a cancelled run returns nothing; a selection that cannot be tuned is refused with a plain sentence; and the metric traces of the
matrix come out the same through a helper-process runner as in this thread. Design: docs/roi_position_tuning_plan_2026-10-28.md (step C2).
"""


from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np

from tests._lspri_scan_data import SyntheticDataset as _Dataset
from tests._lspri_scan_data import make_rois as _rois
from tests._paths import skip_if_lspri_rewrite_absent

try:
    from lspr_imaging_app.analysis import frame_reduce as fr
    from lspr_imaging_app.analysis import roi_tuning_run as run
    from lspr_imaging_app.analysis.roi_tuning import Lattice
    from lspr_imaging_app.analysis.scan import scan_cubes
    from lspr_imaging_app.analysis.scan_arrays import new_arrays, place
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.trace_fit import traces_from_inputs
    from lspr_imaging_app.progress import Cancelled
    from lspr_imaging_app.roi.model import AreaRoi, RoiMask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

METRIC = MetricSettings(fit_method="none")


def _ordinary_scan(plan):
    """The normal scan of ``plan`` as the arrays a scan file holds, in this process."""
    arrays = new_arrays(plan)
    for result in scan_cubes(plan.jobs, plan.settings, processes=1):
        place(arrays, plan, result)
    return arrays


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and bool(np.array_equal(a, b, equal_nan=a.dtype.kind == "f"))


class LatticeScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Dataset()
        cls.lattice = Lattice(3, 2.0)
        cls.plan = cls.data.plan(_rois(), cubes=(1, 2, 3))  # three cubes: the two steps' cubes, not the whole dataset
        cls.everyone = list(range(6))
        cls.matrix = run.run_lattice_scan(cls.plan, cls.everyone, cls.lattice, processes=1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.data.close()

    # -- what the matrix holds ----------------------------------------------------------------------------------------------------------------

    def test_the_matrix_has_one_slab_per_position_cube_roi_wavelength_and_side(self) -> None:
        m = self.matrix
        self.assertEqual(m.mean.shape, (9, 3, 6, 2, 2))
        for array in (m.std, m.n_valid, m.n_nominal, m.n_masked):
            self.assertEqual(array.shape, m.mean.shape)
        self.assertEqual((m.roi_ids, m.cube_indices, m.wavelength_nm), ((1, 2, 3, 4, 5, 6), (1, 2, 3), (500.0, 550.0)))
        self.assertEqual(m.base_hashes.shape, (3, 6))
        self.assertTrue((m.base_hashes != 0).all())
        np.testing.assert_array_equal(m.roi_xy[:3], [[16.0, 16.0], [40.0, 16.0], [64.0, 16.0]])
        self.assertTrue(np.isfinite(m.mean).all())
        self.assertGreater(m.nbytes, 0)

    def test_the_centre_position_is_the_ordinary_scan_number_for_number(self) -> None:
        ordinary = _ordinary_scan(self.plan)
        k = self.lattice.centre_index
        self.assertTrue(_same(self.matrix.mean[k], ordinary.values["mean"]))
        self.assertTrue(_same(self.matrix.std[k], ordinary.std))
        self.assertTrue(_same(self.matrix.n_valid[k], ordinary.n_valid))
        self.assertTrue(_same(self.matrix.n_nominal[k], ordinary.n_nominal))
        self.assertTrue(_same(self.matrix.n_masked[k], ordinary.n_masked))

    def test_the_stored_base_hashes_are_those_of_the_ordinary_scan(self) -> None:
        from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes

        np.testing.assert_array_equal(self.matrix.base_hashes, cell_hashes(self.plan))

    def test_every_position_is_what_a_scan_of_the_moved_roi_table_gives(self) -> None:
        offsets = self.lattice.offsets()
        for k in (0, 2, 6, 8):  # the four corners: the largest moves
            dx, dy = offsets[k]
            moved = _ordinary_scan(self.data.plan(_rois(dx, dy, moved=tuple(self.everyone)), cubes=(1, 2, 3)))
            with self.subTest(offset=(dx, dy)):
                self.assertTrue(_same(self.matrix.mean[k], moved.values["mean"]))
                self.assertTrue(_same(self.matrix.std[k], moved.std))
                self.assertTrue(_same(self.matrix.n_valid[k], moved.n_valid))

    def test_shifted_positions_really_differ_from_the_centre(self) -> None:
        # the test image rises 7 counts per pixel in x: moving 2 px must change the sample means (otherwise the checks above prove nothing)
        k = self.lattice.centre_index
        right = 5  # offset (+2, 0)
        self.assertTrue(np.all(self.matrix.mean[right][..., 0] > self.matrix.mean[k][..., 0]))

    def test_a_subset_moves_only_its_rois_and_matches_a_scan_with_those_moved(self) -> None:
        subset = [1, 4]
        matrix = run.run_lattice_scan(self.plan, subset, self.lattice, processes=1)
        self.assertEqual(matrix.roi_ids, (2, 5))
        self.assertEqual(matrix.mean.shape, (9, 3, 2, 2, 2))
        offsets = self.lattice.offsets()
        for k in (0, 5, 8):
            dx, dy = offsets[k]
            moved = _ordinary_scan(self.data.plan(_rois(dx, dy, moved=tuple(subset)), cubes=(1, 2, 3)))
            with self.subTest(offset=(dx, dy)):
                self.assertTrue(_same(matrix.mean[k], moved.values["mean"][:, subset]))
                self.assertTrue(_same(matrix.std[k], moved.std[:, subset]))

    def test_a_roi_shifted_alone_reads_what_it_reads_when_all_are_shifted(self) -> None:
        self.assertEqual(self.matrix.interacting_pairs, ())  # the ROIs are far enough apart for this to be a fair statement
        alone = run.run_lattice_scan(self.plan, [3], self.lattice, processes=1)
        self.assertTrue(_same(alone.mean[:, :, 0], self.matrix.mean[:, :, 3]))

    def test_close_rois_are_listed_as_interacting(self) -> None:
        close = [AreaRoi(1, 16.0, 16.0, 8.0, reference_inner_diameter_px=12.0, reference_outer_diameter_px=18.0),
                 AreaRoi(2, 28.0, 16.0, 8.0, reference_inner_diameter_px=12.0, reference_outer_diameter_px=18.0)]  # 12 px apart: rings overlap the other's disc
        matrix = run.run_lattice_scan(self.data.plan(close, cubes=(1, 2)), [0, 1], self.lattice, processes=1)
        self.assertTrue(matrix.interacting_pairs)
        self.assertTrue(all(a != b for a, b in matrix.interacting_pairs))

    # -- how it runs ---------------------------------------------------------------------------------------------------------------------

    def test_the_numbers_do_not_depend_on_the_number_of_worker_processes(self) -> None:
        two = run.run_lattice_scan(self.plan, self.everyone, self.lattice, processes=2)
        for name in ("mean", "std", "n_valid", "n_nominal", "n_masked"):
            with self.subTest(array=name):
                self.assertTrue(_same(getattr(two, name), getattr(self.matrix, name)))

    def test_progress_is_reported_and_ends_at_one(self) -> None:
        seen: list[float] = []
        run.run_lattice_scan(self.plan, [0], Lattice(3, 1.0), processes=1, progress=lambda fraction, message: seen.append(fraction))
        self.assertEqual(len(seen), 9 * 3)  # one report per (position, cube)
        self.assertEqual(seen, sorted(seen))
        self.assertAlmostEqual(seen[-1], 1.0)

    def test_a_cancelled_run_returns_nothing(self) -> None:
        calls = {"n": 0}

        def cancelled() -> bool:
            calls["n"] += 1
            return calls["n"] > 4

        with self.assertRaises(Cancelled):
            run.run_lattice_scan(self.plan, self.everyone, self.lattice, processes=1, cancelled=cancelled)

    def test_the_cost_estimate_counts_frames_and_memory(self) -> None:
        cost = run.estimate_cost(self.plan, 6, self.lattice)
        self.assertEqual((cost.positions, cost.rois, cost.cubes), (9, 6, 3))
        self.assertEqual(cost.frames, 9 * 3 * 2)  # positions x cubes x spectral frames
        self.assertEqual(cost.matrix_bytes, self.matrix.mean.size * run.BYTES_PER_ELEMENT)
        self.assertEqual(cost.matrix_bytes + self.matrix.base_hashes.nbytes, self.matrix.nbytes)

    # -- what cannot be tuned ------------------------------------------------------------------------------------------------------------

    def test_nothing_selected_is_refused_in_plain_words(self) -> None:
        with self.assertRaisesRegex(run.TuningError, "Select at least one ROI"):
            run.run_lattice_scan(self.plan, [], self.lattice, processes=1)

    def test_a_roi_that_is_not_in_the_scan_is_refused(self) -> None:
        with self.assertRaises(run.TuningError):
            run.run_lattice_scan(self.plan, [99], self.lattice, processes=1)
        with self.assertRaises(run.TuningError):
            run.run_lattice_scan(self.plan, [1, 1], self.lattice, processes=1)

    def test_a_free_form_roi_is_refused_by_its_id(self) -> None:
        free = AreaRoi(7, 70.0, 50.0, 8.0, sample_geometry_type="mask", sample_mask=RoiMask(66, 46, np.ones((8, 9), dtype=bool)))
        plan = self.data.plan(_rois() + [free], cubes=(1, 2))
        with self.assertRaisesRegex(run.TuningError, "ROI 7 is a free-form ROI"):
            run.run_lattice_scan(plan, [0, 6], self.lattice, processes=1)
        run.run_lattice_scan(plan, [0], Lattice(1), processes=1)  # the round ROIs of the same plan are fine

    def test_a_geometry_timeline_is_refused(self) -> None:
        other = fr.build_roi_set(_rois(3.0, 0.0, moved=(0,)), 12.0, 18.0)
        jobs = (self.plan.jobs[0], replace(self.plan.jobs[1], rois=other)) + self.plan.jobs[2:]
        with self.assertRaisesRegex(run.TuningError, "timeline"):
            run.run_lattice_scan(replace(self.plan, jobs=jobs), [0], self.lattice, processes=1)


class TraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Dataset()
        cls.plan = cls.data.plan(_rois(), cubes=(0, 1, 2, 3))
        cls.matrix = run.run_lattice_scan(cls.plan, [0, 2, 5], Lattice(3, 1.0), processes=1)
        cls.formula = cls.plan.settings.detection.formula_key

    @classmethod
    def tearDownClass(cls) -> None:
        cls.data.close()

    def test_traces_are_position_roi_cube_and_finite_where_the_scan_had_numbers(self) -> None:
        traces = run.compute_traces(self.matrix, self.formula, METRIC)
        self.assertEqual(traces.shape, (9, 3, 4))
        self.assertTrue(np.isfinite(traces).all())

    def test_a_helper_process_runner_gives_the_same_traces_as_this_thread(self) -> None:
        calls: list[int] = []

        def runner(jobs, formula_key, metric, cancelled, progress):
            calls.append(len(jobs))
            traces = traces_from_inputs([data for _, data in jobs], formula_key, metric)
            return {key: trace for (key, _), trace in zip(jobs, traces, strict=True)}, False

        here = run.compute_traces(self.matrix, self.formula, METRIC)
        there = run.compute_traces(self.matrix, self.formula, METRIC, runner=runner)
        self.assertEqual(calls, [9 * 3])  # one job per (position, ROI)
        np.testing.assert_array_equal(here, there)

    def test_a_cancelling_runner_raises_cancelled(self) -> None:
        with self.assertRaises(Cancelled):
            run.compute_traces(self.matrix, self.formula, METRIC, runner=lambda *a: ({}, True))

    def test_the_trace_of_a_position_is_the_trace_of_its_own_numbers(self) -> None:
        traces = run.compute_traces(self.matrix, self.formula, METRIC)
        jobs = self.matrix.trace_inputs()
        for k, j in ((0, 0), (4, 1), (8, 2)):
            key = k * 3 + j
            self.assertEqual(jobs[key][0], key)
            expected = traces_from_inputs([jobs[key][1]], self.formula, METRIC)[0]
            np.testing.assert_array_equal(traces[k, j], expected)


if __name__ == "__main__":
    unittest.main()
