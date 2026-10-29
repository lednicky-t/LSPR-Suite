"""ROI position tuning, step E (`roi_tuning_accept.py`, `RoiToolbox.move_rois`, `RoiTuningModule.accept`): what Accept does with a finished trial.

The heart of it is the rule "there is no recomputation": the numbers of the best position are copied from the trial's matrix into `scan.h5`, and a cell written that way
must be **the cell a real scan of the moved ROIs writes, to the bit** - values, spread, pixel counts, the hash that says it is current - or nothing may be written. So
the tests scan the moved layout for real and compare the files array for array; then every case in which seeding must refuse (no file, a file that does not fit, a
trial whose inputs changed, ROIs too close to each other) and must leave the file untouched; then the toolbox's bulk move (one undo step, 0.1 px rounding) and the
module's Accept end to end (ROIs move, one Undo puts them back, the numbers arrive, the next Fast scan has only the other cubes to do).
Design: docs/roi_position_tuning_plan_2026-10-28.md (step E).
"""

from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PyQt6 import QtWidgets

from tests._lspri_scan_data import SyntheticDataset, make_rois
from tests._paths import skip_if_lspri_rewrite_absent

try:
    from lspr_imaging_app.analysis import roi_tuning_accept as ta
    from lspr_imaging_app.analysis import roi_tuning_module as tm
    from lspr_imaging_app.analysis.roi_tuning import Lattice, LatticeObjectives, moved_centre
    from lspr_imaging_app.analysis.roi_tuning_objectives import Ranking
    from lspr_imaging_app.analysis.roi_tuning_run import run_lattice_scan
    from lspr_imaging_app.analysis.scan import scan_cubes
    from lspr_imaging_app.analysis.scan_arrays import new_arrays, place
    from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes
    from lspr_imaging_app.analysis.scan_jobs import build_scan_plan, snapshot_source
    from lspr_imaging_app.analysis.scan_store import ScanStore, read_scan, stale_cell_matrix
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.settings_module import AnalysisSettingsModule
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
CUBES = (1, 2, 3)  # the cubes of the trial (the dataset has 0..3: cube 0 is outside the two steps)
LATTICE = Lattice(3, 2.0)  # row 0 (-2,-2), 2 (+2,-2), 4 centre, 5 (+2, 0), 8 (+2,+2)
SELECTED = [0, 1, 3, 4]  # positions in the ROI table = ROI ids 1, 2, 4, 5
BEST = [4, 8, 0, 5]  # lattice row of each: ROI 1 stays, ROI 2 (+2,+2), ROI 4 (-2,-2), ROI 5 (+2, 0)
MOVED_IDS = (2, 4, 5)


def full_scan(data: SyntheticDataset, rois, path: Path):
    """A complete, ordinary scan of ``rois`` into ``path``: the thing a seeded file must equal."""
    plan = build_scan_plan(snapshot_source(data.source(rois)))
    hashes = cell_hashes(plan)
    store = ScanStore(path)
    store.prepare(plan, dataset_label="test")
    arrays = new_arrays(plan)
    for result in scan_cubes(plan.jobs, plan.settings, processes=1):
        place(arrays, plan, result)
    store.write_batch(plan, arrays, [(p, None, hashes[p]) for p in range(len(plan.jobs))])
    return plan, hashes


def moved_rois(rois, best=BEST, selected=SELECTED, lattice=LATTICE):
    out = list(rois)
    for position, row in zip(selected, best, strict=True):
        dx, dy = lattice.offsets()[row]
        x, y = moved_centre(out[position].center_x, out[position].center_y, float(dx), float(dy))
        out[position] = replace(out[position], center_x=x, center_y=y)
    return out


def ranking_for(matrix, best=BEST):
    n, p = len(matrix.roi_ids), len(matrix.offsets)
    return Ranking("signal", "nm", True, matrix.roi_ids, matrix.offsets, matrix.lattice.centre_index, np.zeros((p, n)), np.zeros((p, n)), np.asarray(best, dtype=np.int64))


def planted_objectives(matrix, peaks_px) -> LatticeObjectives:
    """Objectives whose |delta| is a smooth hill with its top at ``peaks_px[roi]`` (dx, dy in px): the answer an interpolation has to find."""
    offsets, n, p = matrix.offsets, len(matrix.roi_ids), len(matrix.offsets)
    delta = np.column_stack([20.0 - 0.5 * ((offsets[:, 0] - px) ** 2 + (offsets[:, 1] - py) ** 2) for px, py in peaks_px])
    zeros, sem, count = np.zeros((p, n)), np.full((p, n), 0.01), np.full((p, n), 30, dtype=np.int64)
    return LatticeObjectives(
        matrix.roi_ids, offsets, zeros, delta, np.full((p, n), 0.05), np.full((p, n), 0.05), zeros + 0.05, zeros + 0.05, sem, sem, count, count
    )


def tuning_plans(data, before, after):
    pb = build_scan_plan(snapshot_source(data.source(before), CUBES), cubes=CUBES, resolve_masks=False)
    pa = build_scan_plan(snapshot_source(data.source(after), CUBES), cubes=CUBES, resolve_masks=False)
    return pb, pa


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SeedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = SyntheticDataset(n_cubes=4)
        cls.rois = make_rois()
        cls.matrix = run_lattice_scan(cls.data.plan(cls.rois, cubes=CUBES), SELECTED, LATTICE, processes=1)
        cls.ranking = ranking_for(cls.matrix)
        cls.after_rois = moved_rois(cls.rois)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.data.close()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.folder = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seeded_file(self) -> Path:
        path = self.folder / "seeded" / "scan.h5"
        full_scan(self.data, self.rois, path)  # the scan the user had before moving anything
        return path

    def seed(self, path: Path, **changes):
        pb, pa = tuning_plans(self.data, changes.pop("before", self.rois), changes.pop("after", self.after_rois))
        return ta.seed_scan_file(path, changes.pop("matrix", self.matrix), changes.pop("ranking", self.ranking), pb, pa, dataset_label="test")

    # -- the numbers --------------------------------------------------------------------------------------------------------------

    def test_a_seeded_cell_is_the_cell_a_real_scan_of_the_moved_rois_writes_to_the_bit(self) -> None:
        seeded_path, real_path = self.seeded_file(), self.folder / "real" / "scan.h5"
        _plan, real_hashes = full_scan(self.data, self.after_rois, real_path)
        before = read_scan(seeded_path)
        report = self.seed(seeded_path)
        self.assertTrue(report.written, report.reason)
        self.assertEqual((report.roi_ids, report.cubes, report.not_saved), (MOVED_IDS, 3, ()))
        seeded, real = read_scan(seeded_path), read_scan(real_path)
        rows = [real.roi_ids.index(i) for i in MOVED_IDS]
        tuned = [real.cube_indices.index(c) for c in CUBES]
        for name, ours, theirs in (
            ("mean", seeded.values["mean"], real.values["mean"]), ("std", seeded.std, real.std), ("n_valid", seeded.n_valid, real.n_valid),
            ("n_nominal", seeded.n_nominal, real.n_nominal), ("n_masked", seeded.n_masked, real.n_masked),
        ):
            for r in tuned:
                with self.subTest(array=name, cube_row=r):
                    self.assertTrue(np.array_equal(ours[r][rows], theirs[r][rows], equal_nan=ours.dtype.kind == "f"))
        for r in tuned:  # and the stamp that says "current": the hash of a real scan, so the next Fast scan skips these cells
            self.assertTrue(np.array_equal(seeded.cell_hash[r][rows], real.cell_hash[r][rows]))
            self.assertTrue(np.array_equal(seeded.recipe_id[r][rows], real.recipe_id[r][rows]))
        self.assertTrue(np.all(seeded.cell_hash[tuned][:, rows] != 0))
        for r in tuned:  # the layout (ids differ between files, the text of the layout must not)
            self.assertEqual(seeded.layouts[int(seeded.layout_id[r])], real.layouts[int(real.layout_id[r])])
        # nothing else in the file was touched: the unmoved ROIs everywhere, and every ROI of the cube outside the trial
        other = [i for i in range(len(real.roi_ids)) if i not in rows]
        for r in range(len(real.cube_indices)):
            self.assertTrue(np.array_equal(seeded.values["mean"][r][other], before.values["mean"][r][other], equal_nan=True), r)
            self.assertTrue(np.array_equal(seeded.cell_hash[r][other], before.cell_hash[r][other]), r)
        outside = [r for r in range(len(real.cube_indices)) if r not in tuned]
        self.assertTrue(np.array_equal(seeded.values["mean"][outside], before.values["mean"][outside], equal_nan=True))
        self.assertTrue(np.array_equal(seeded.cell_hash[outside], before.cell_hash[outside]))
        # the moved ROIs' numbers really changed (otherwise the comparison above would prove nothing)
        self.assertFalse(np.array_equal(seeded.values["mean"][tuned][:, rows], before.values["mean"][tuned][:, rows], equal_nan=True))
        # a Fast scan after this has exactly the cells outside the trial to do: cube 0 of the moved ROIs
        stale = stale_cell_matrix(seeded.cell_hash, real_hashes)
        self.assertEqual({(int(r), int(c)) for r, c in zip(*np.nonzero(stale), strict=True)}, {(0, row) for row in rows})

    def test_the_roi_that_stays_is_not_rewritten(self) -> None:
        path = self.seeded_file()
        before = read_scan(path)
        self.assertTrue(self.seed(path).written)
        after = read_scan(path)
        row = after.roi_ids.index(1)  # ROI 1 had its best position at the centre
        self.assertTrue(np.array_equal(after.values["mean"][:, row], before.values["mean"][:, row], equal_nan=True))
        self.assertTrue(np.array_equal(after.cell_hash[:, row], before.cell_hash[:, row]))

    # -- when nothing may be written ----------------------------------------------------------------------------------------------

    def test_no_scan_file_means_none_is_created(self) -> None:
        path = self.folder / "none" / "scan.h5"
        report = self.seed(path)
        self.assertFalse(report.written)
        self.assertIn("no scan file", report.reason)
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists() and any(path.parent.iterdir()))

    def test_a_file_that_does_not_fit_the_rois_is_left_exactly_as_it_is(self) -> None:
        path = self.folder / "other" / "scan.h5"
        full_scan(self.data, self.rois[:5], path)  # five ROIs: the axes do not fit the six of the trial
        digest = file_digest(path)
        report = self.seed(path)
        self.assertFalse(report.written)
        self.assertEqual(file_digest(path), digest)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["scan.h5"])  # no backup, no new file

    def test_a_trial_whose_inputs_changed_writes_nothing(self) -> None:
        path = self.seeded_file()
        digest = file_digest(path)
        changed = [replace(r, reference_outer_diameter_px=20.0) if i == 1 else r for i, r in enumerate(self.rois)]  # a ring changed after the trial
        report = self.seed(path, before=changed, after=moved_rois(changed))
        self.assertFalse(report.written)
        self.assertIn("Something the trial was made from changed", report.reason)
        self.assertEqual(file_digest(path), digest)

    def test_a_matrix_whose_stored_hashes_do_not_match_writes_nothing(self) -> None:
        path = self.seeded_file()
        digest = file_digest(path)
        report = self.seed(path, matrix=replace(self.matrix, base_hashes=self.matrix.base_hashes + np.uint64(1)))
        self.assertFalse(report.written)
        self.assertEqual(file_digest(path), digest)

    def test_rois_close_to_another_roi_are_moved_but_their_numbers_are_not_copied(self) -> None:
        path = self.seeded_file()
        before = read_scan(path)
        close = replace(self.matrix, interacting_pairs=((2, 4),))  # ROIs 2 and 4 are within reach of each other's ring
        report = self.seed(path, matrix=close)
        self.assertTrue(report.written, report.reason)
        self.assertEqual((report.roi_ids, report.not_saved), ((5,), (2, 4)))
        after = read_scan(path)
        for roi_id in (2, 4):
            row = after.roi_ids.index(roi_id)
            tuned = [after.cube_indices.index(c) for c in CUBES]
            self.assertTrue(np.array_equal(after.values["mean"][tuned][:, row], before.values["mean"][tuned][:, row], equal_nan=True), roi_id)
            self.assertTrue(np.array_equal(after.cell_hash[tuned][:, row], before.cell_hash[tuned][:, row]), roi_id)  # still the old stamp: stale, rescanned
        row5 = after.roi_ids.index(5)
        self.assertFalse(np.array_equal(after.cell_hash[1:4, row5], before.cell_hash[1:4, row5]))

    def test_when_every_moved_roi_is_close_to_another_nothing_is_written(self) -> None:
        path = self.seeded_file()
        digest = file_digest(path)
        report = self.seed(path, matrix=replace(self.matrix, interacting_pairs=((2, 4), (4, 5))))
        self.assertFalse(report.written)
        self.assertEqual(report.not_saved, MOVED_IDS)
        self.assertEqual(file_digest(path), digest)

    def test_nothing_to_write_when_no_roi_moves(self) -> None:
        path = self.seeded_file()
        digest = file_digest(path)
        centre = self.matrix.lattice.centre_index
        report = self.seed(path, ranking=ranking_for(self.matrix, [centre] * 4), after=self.rois)
        self.assertFalse(report.written)
        self.assertEqual(file_digest(path), digest)

    def test_plans_that_are_not_the_trials_are_refused(self) -> None:
        path = self.seeded_file()
        digest = file_digest(path)
        pb, pa = tuning_plans(self.data, self.rois, self.after_rois)
        other_cubes = build_scan_plan(snapshot_source(self.data.source(self.after_rois), (0, 1, 2)), cubes=(0, 1, 2), resolve_masks=False)
        report = ta.seed_scan_file(path, self.matrix, self.ranking, pb, other_cubes)
        self.assertFalse(report.written)
        self.assertEqual(file_digest(path), digest)

    def test_a_roi_at_an_interpolated_centre_is_moved_but_its_numbers_are_not_copied(self) -> None:
        sub = np.zeros((4, 2))
        sub[1] = (0.3, -0.2)  # ROI 2: its best lattice point (+2, +2) plus a step between lattice points
        ranking = replace(self.ranking, subpixel=sub)
        moves = ta.accepted_moves(self.matrix, ranking)
        j = self.matrix.roi_ids.index(2)
        self.assertEqual(moves[2], moved_centre(*self.matrix.roi_xy[j], 2.3, 1.8))
        self.assertEqual(ta.seedable_columns(self.matrix, ranking), [self.matrix.roi_ids.index(4), self.matrix.roi_ids.index(5)])
        after = list(self.after_rois)
        after[1] = replace(after[1], center_x=moves[2][0], center_y=moves[2][1])
        path, real_path = self.seeded_file(), self.folder / "real" / "scan.h5"
        full_scan(self.data, after, real_path)
        before = read_scan(path)
        report = self.seed(path, ranking=ranking, after=after)
        self.assertTrue(report.written, report.reason)
        self.assertEqual((report.roi_ids, report.not_saved), ((4, 5), (2,)))
        seeded, real = read_scan(path), read_scan(real_path)
        tuned = [real.cube_indices.index(c) for c in CUBES]
        row2 = seeded.roi_ids.index(2)
        self.assertTrue(np.array_equal(seeded.values["mean"][tuned][:, row2], before.values["mean"][tuned][:, row2], equal_nan=True))  # left as it was: stale
        self.assertTrue(np.array_equal(seeded.cell_hash[tuned][:, row2], before.cell_hash[tuned][:, row2]))
        for roi_id in (4, 5):  # the others are still exact copies of a real scan of this very layout
            row = real.roi_ids.index(roi_id)
            self.assertTrue(np.array_equal(seeded.values["mean"][tuned][:, row], real.values["mean"][tuned][:, row], equal_nan=True), roi_id)
            self.assertTrue(np.array_equal(seeded.cell_hash[tuned][:, row], real.cell_hash[tuned][:, row]), roi_id)

    def test_a_roi_whose_best_point_is_its_centre_but_whose_interpolated_centre_is_not_is_moved(self) -> None:
        centre = self.matrix.lattice.centre_index
        sub = np.zeros((4, 2))
        sub[0] = (0.4, 0.0)  # ROI 1: best = centre, interpolated 0.4 px to the side
        ranking = replace(ranking_for(self.matrix, [centre, 8, 0, 5]), subpixel=sub)
        moves = ta.accepted_moves(self.matrix, ranking)
        self.assertEqual(sorted(moves), [1, 2, 4, 5])
        self.assertEqual(moves[1], moved_centre(*self.matrix.roi_xy[0], 0.4, 0.0))
        self.assertNotIn(0, ta.seedable_columns(self.matrix, ranking))

    def test_a_ranking_from_another_matrix_is_a_bug_not_a_refusal(self) -> None:
        other = replace(self.matrix, roi_ids=(9, 8, 7, 6))
        with self.assertRaises(ValueError):
            ta.accepted_moves(other, self.ranking)

    # -- the moves ----------------------------------------------------------------------------------------------------------------

    def test_accepted_moves_are_the_centres_the_toolbox_will_store(self) -> None:
        moves = ta.accepted_moves(self.matrix, self.ranking)
        self.assertEqual(sorted(moves), list(MOVED_IDS))  # ROI 1 stays: it is not in the moves
        for roi_id, row in zip((2, 4, 5), (8, 0, 5), strict=True):
            j = self.matrix.roi_ids.index(roi_id)
            dx, dy = LATTICE.offsets()[row]
            self.assertEqual(moves[roi_id], moved_centre(*self.matrix.roi_xy[j], float(dx), float(dy)))

    def test_a_roi_that_could_not_be_ranked_is_not_moved(self) -> None:
        moves = ta.accepted_moves(self.matrix, ranking_for(self.matrix, [-1, 8, -1, 5]))
        self.assertEqual(sorted(moves), [2, 5])


class ToolboxMoveTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        for x, y in ((10.0, 20.0), (30.0, 40.0), (50.0, 60.0)):
            self.toolbox.add_roi(x, y, sample_diameter_px=8.0)
        undo_manager.clear()
        self.signals = []
        self.toolbox.geometry_changed.connect(lambda change: self.signals.append(change))

    def tearDown(self) -> None:
        undo_manager.clear()

    def centres(self):
        return [(r.center_x, r.center_y) for r in self.toolbox.rois()]

    def test_each_roi_goes_to_its_own_position_rounded_to_a_tenth_of_a_pixel(self) -> None:
        self.toolbox.move_rois({1: (12.04, 20.06), 3: (48.0, 61.25)}, label="Accept tuned ROI positions")
        self.assertEqual(self.centres(), [(12.0, 20.1), (30.0, 40.0), (48.0, 61.2)])

    def test_it_is_one_undo_step_and_one_signal(self) -> None:
        self.toolbox.move_rois({1: (12.0, 22.0), 2: (31.0, 41.0), 3: (51.0, 61.0)}, label="Accept tuned ROI positions")
        self.assertEqual(len(self.signals), 1)
        self.assertEqual(undo_manager.undo_label, "Accept tuned ROI positions")
        undo_manager.undo()
        self.assertEqual(self.centres(), [(10.0, 20.0), (30.0, 40.0), (50.0, 60.0)])
        self.assertFalse(undo_manager.can_undo, "one step, not three")

    def test_an_unknown_id_changes_nothing_at_all(self) -> None:
        with self.assertRaises(KeyError):
            self.toolbox.move_rois({1: (12.0, 22.0), 99: (1.0, 1.0)})
        self.assertEqual(self.centres(), [(10.0, 20.0), (30.0, 40.0), (50.0, 60.0)])
        self.assertFalse(undo_manager.can_undo)

    def test_a_position_that_is_not_a_number_changes_nothing_at_all(self) -> None:
        with self.assertRaises(ValueError):
            self.toolbox.move_rois({1: (12.0, 22.0), 2: (float("nan"), 1.0)})
        self.assertEqual(self.centres(), [(10.0, 20.0), (30.0, 40.0), (50.0, 60.0)])

    def test_where_nothing_differs_there_is_no_undo_entry_and_no_signal(self) -> None:
        self.toolbox.move_rois({1: (10.0, 20.0), 2: (30.0, 40.0)})
        self.assertEqual(self.signals, [])
        self.assertFalse(undo_manager.can_undo)


def _pump(until, seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return bool(until())


class _Plan:
    def __init__(self) -> None:
        self.baseline, self.signal = SimpleNamespace(ref=("a", 1), order=1), SimpleNamespace(ref=("a", 2), order=2)

    def baseline_step(self, pair: int = 1):
        return self.baseline

    def signal_step(self, pair: int = 1):
        return self.signal

    def cubes_of(self, step):
        return (0, 1, 2) if step is self.baseline else (3, 4)  # cube 5 is outside both steps


class _Selection:
    def __init__(self, ids) -> None:
        self.ids = set(ids)

    def selected_roi_ids(self):
        return frozenset(self.ids)


class _Dataset:
    def cube_spans_unix_ms(self, cubes):
        first = np.array([1_700_000_000_000 + c * 10_000.0 for c in cubes])
        return first, first + 6_000.0


class ModuleAcceptTest(unittest.TestCase):
    """Accept end to end: a real ROI toolbox, the module, the task, a scan file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = SyntheticDataset(n_cubes=6, wavelengths=(0.0, 500.0, 520.0, 540.0, 560.0, 580.0, 600.0))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.data.close()

    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.scan_path = Path(self._tmp.name) / "analysis" / "scan.h5"
        self.toolbox = RoiToolbox()
        for roi in make_rois():
            self.toolbox.add_roi(roi.center_x, roi.center_y, sample_diameter_px=roi.sample_diameter_px)
        undo_manager.clear()
        settings = AnalysisSettingsModule()
        settings.set_metric_settings(MetricSettings(fit_method="none"))
        self.module = tm.RoiTuningModule(
            lambda: self.data.source(list(self.toolbox.rois())), _Plan(), _Selection([2, 4, 5]), settings, _Dataset(), self.toolbox, helper_process=False
        )
        self.module.set_lattice(3, 2.0)
        self.toolbox.geometry_changed.connect(lambda *_a: self.module.mark_stale())  # as the app wires it
        self.seeded: list = []
        self.module.seeded.connect(self.seeded.append)

    def tearDown(self) -> None:
        self.module.task.shutdown()
        self._tmp.cleanup()
        undo_manager.clear()

    def run_trial(self) -> None:
        self.assertTrue(self.module.run(processes=1), self.module.message())
        self.assertTrue(_pump(lambda: self.module.state() != tm.RUNNING))
        self.assertEqual(self.module.state(), tm.READY, self.module.message())

    def force_best(self, best) -> None:
        """Replace the ranking by one with chosen best positions (the metric of this tiny dataset is arbitrary), as the module would hold it."""
        self.module._ranking = ranking_for(self.module.result().matrix, best)

    def centres(self):
        return {int(r.area_roi_id): (r.center_x, r.center_y) for r in self.toolbox.rois()}

    def test_accept_moves_the_rois_in_one_undo_step_saves_their_numbers_and_spends_the_trial(self) -> None:
        full_scan(self.data, list(self.toolbox.rois()), self.scan_path)
        self.run_trial()
        self.force_best([8, 0, 5])  # ROIs 2, 4, 5
        before = self.centres()
        expected = moved_rois(make_rois(), best=[8, 0, 5], selected=[1, 3, 4], lattice=Lattice(3, 2.0))
        self.assertTrue(self.module.can_accept())
        self.assertTrue(self.module.accept(self.scan_path, "test"), self.module.message())
        after = self.centres()
        for roi_id in (2, 4, 5):
            self.assertEqual(after[roi_id], (expected[roi_id - 1].center_x, expected[roi_id - 1].center_y))
        self.assertEqual({k: v for k, v in after.items() if k not in (2, 4, 5)}, {k: v for k, v in before.items() if k not in (2, 4, 5)})
        self.assertEqual(undo_manager.undo_label, "Accept tuned ROI positions")
        self.assertIsNone(self.module.result())  # the trial is spent
        self.assertIsNone(self.module.view())
        self.assertTrue(_pump(lambda: bool(self.seeded)), "the numbers were not saved")
        report = self.seeded[0]
        self.assertTrue(report.written, report.reason)
        self.assertEqual(report.roi_ids, (2, 4, 5))
        self.assertIn("are in the scan file", self.module.message())
        # the cells of the two steps are current for the moved layout: only the cubes outside them are left for a Fast scan
        stored = read_scan(self.scan_path)
        plan = build_scan_plan(snapshot_source(self.data.source(list(self.toolbox.rois()))), resolve_masks=False)
        stale = stale_cell_matrix(stored.cell_hash, cell_hashes(plan))
        self.assertEqual(sorted({int(stored.cube_indices[r]) for r in np.nonzero(stale.any(axis=1))[0]}), [5])  # the cubes of the two steps are 0-4
        undo_manager.undo()
        self.assertEqual(self.centres(), before)
        self.assertFalse(undo_manager.can_undo, "Accept was one step")

    def plant(self, peaks_px) -> None:
        """Give the result in hand objectives with known hills (the metric of this tiny dataset is arbitrary) and rank by |delta|."""
        result = self.module.result()
        self.module._result = replace(result, objectives=planted_objectives(result.matrix, peaks_px))
        self.module.set_objective("signal")

    def test_interpolation_places_the_best_centre_between_lattice_points_without_a_new_scan(self) -> None:
        self.run_trial()
        result = self.module.result()
        self.plant([(0.8, -0.6), (0.2, 0.5), (-0.7, 0.3)])  # ROIs 2, 4, 5 on the 3 x 3 lattice of pitch 2 px; all tops next to the centre
        self.assertFalse(self.module.interpolate())
        self.assertFalse(self.module.can_accept(), "on the lattice the centre is the best of each: nothing moves")
        self.module.set_interpolate(True)
        self.assertTrue(self.module.interpolate())
        self.assertIs(self.module.result().matrix, result.matrix)  # nothing was scanned again
        self.assertTrue(self.module.can_accept())
        self.assertIn("placed between lattice points", self.module.message())
        xy = {int(i): tuple(c) for i, c in zip(result.matrix.roi_ids, result.matrix.roi_xy, strict=True)}
        expected = {2: moved_centre(*xy[2], 0.8, -0.6), 4: moved_centre(*xy[4], 0.2, 0.5), 5: moved_centre(*xy[5], -0.7, 0.3)}
        self.assertEqual(self.module.proposed_centres()[2], expected[2])
        self.assertEqual(self.module.proposed_centres()[4], expected[4])
        view = self.module.view()
        np.testing.assert_allclose(view.best_offsets, [(0.8, -0.6), (0.2, 0.5), (-0.7, 0.3)], atol=1e-9)
        self.module.set_interpolate(False)
        self.assertFalse(self.module.can_accept())

    def test_accepting_an_interpolated_centre_moves_the_roi_and_saves_nothing_it_never_measured(self) -> None:
        full_scan(self.data, list(self.toolbox.rois()), self.scan_path)
        digest = file_digest(self.scan_path)
        self.run_trial()
        self.plant([(0.8, -0.6), (0.2, 0.5), (-0.7, 0.3)])
        self.module.set_interpolate(True)
        xy = {int(r.area_roi_id): (r.center_x, r.center_y) for r in self.toolbox.rois()}
        before = self.centres()
        self.assertTrue(self.module.accept(self.scan_path, "test"), self.module.message())
        after = self.centres()
        self.assertEqual(after[2], moved_centre(*xy[2], 0.8, -0.6))
        self.assertEqual(after[5], moved_centre(*xy[5], -0.7, 0.3))
        self.assertNotEqual(after[2], before[2])
        self.assertTrue(_pump(lambda: bool(self.seeded)), "the saving task did not report")
        report = self.seeded[0]
        self.assertFalse(report.written)  # every moved ROI is at an interpolated centre: there are no measured numbers to copy
        self.assertEqual(file_digest(self.scan_path), digest)
        self.assertIn("next scan computes", self.module.message())
        undo_manager.undo()
        self.assertEqual(self.centres(), before)

    def test_without_a_scan_file_the_rois_still_move_and_the_message_says_what_is_left(self) -> None:
        self.run_trial()
        self.force_best([8, 4, 4])
        self.assertTrue(self.module.accept(self.scan_path, "test"))
        self.assertFalse(self.scan_path.exists())  # never created by Accept
        self.assertIn("The scan file was not changed", self.module.message())
        self.assertEqual(self.seeded, [])
        self.assertNotEqual(self.centres()[2], (40.0, 16.0))

    def test_accept_with_no_path_moves_the_rois_and_touches_no_file(self) -> None:
        self.run_trial()
        self.force_best([8, 4, 4])
        self.assertTrue(self.module.accept(None))
        self.assertFalse(self.scan_path.parent.exists())

    def test_nothing_to_accept_without_a_trial(self) -> None:
        self.assertFalse(self.module.can_accept())
        self.assertFalse(self.module.accept(self.scan_path))
        self.assertIn("run a trial first", self.module.message())
        self.assertFalse(undo_manager.can_undo)

    def test_nothing_moves_when_every_centre_is_already_the_best(self) -> None:
        self.run_trial()
        self.force_best([4, 4, 4])
        self.assertFalse(self.module.can_accept())
        self.assertFalse(self.module.accept(self.scan_path))
        self.assertIn("already the best", self.module.message())
        self.assertFalse(undo_manager.can_undo)

    def test_an_out_of_date_result_is_not_accepted(self) -> None:
        self.run_trial()
        self.force_best([8, 4, 4])
        self.module.mark_stale()
        self.assertFalse(self.module.can_accept())
        before = self.centres()
        self.assertFalse(self.module.accept(self.scan_path))
        self.assertIn("out of date", self.module.message())
        self.assertEqual(self.centres(), before)

    def test_a_roi_that_was_moved_by_hand_after_the_trial_stops_accept(self) -> None:
        self.run_trial()
        self.force_best([8, 4, 4])
        self.toolbox.move_rois({2: (41.0, 17.0)})  # by hand ...
        self.module._stale = False  # ... and the signal that marks the result stale is supposed to have been missed
        before = self.centres()
        self.assertFalse(self.module.accept(self.scan_path))
        self.assertIn("ROI 2 is not where it was", self.module.message())
        self.assertEqual(self.centres(), before)

    def test_accept_is_refused_while_the_task_runs(self) -> None:
        self.assertTrue(self.module.run(processes=1))
        self.assertFalse(self.module.accept(self.scan_path))
        self.assertIn("A trial is running", self.module.message())
        self.module.cancel()
        _pump(lambda: self.module.state() != tm.RUNNING)


if __name__ == "__main__":
    unittest.main()
