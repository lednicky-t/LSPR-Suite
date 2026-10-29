"""ROI position tuning, step C3 (`analysis/roi_tuning_module.py`, `roi_tuning_task.py`, `roi_tuning_export.py`): the module that owns the trial, its result and
ranking, on the tiny synthetic dataset with the real scan. Fits run in the task thread here (`helper_process=False`); the helper process has its own tests.

What has to hold: a trial needs a selection and two different plan steps (B1 / S1) and says in plain words why it cannot start; a finished trial installs a result and
a ranking and says how many ROIs would move; switching the objective re-ranks without a new scan; a change that makes the result wrong marks it stale and refuses a
refit; a change of fit settings re-fits in the background; closing the dataset drops everything; Stop returns nothing; a free-form ROI is refused by its id; the
export writes one row per ROI and position. Design: docs/roi_position_tuning_plan_2026-10-28.md (step C3).
"""

from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tests._lspri_scan_data import SyntheticDataset, make_rois
from tests._paths import skip_if_lspri_rewrite_absent

try:
    from PyQt6 import QtWidgets

    from lspr_imaging_app.analysis import roi_tuning_module as tm
    from lspr_imaging_app.analysis.roi_tuning_export import COLUMNS
    from lspr_imaging_app.analysis.settings import AnalysisSettingsChange, MetricSettings
    from lspr_imaging_app.analysis.settings_module import AnalysisSettingsModule
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings, RoiMask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
WAVELENGTHS = (0.0, 500.0, 520.0, 540.0, 560.0, 580.0, 600.0)  # six spectral frames: a metric that is not always the same wavelength
T0 = 1_700_000_000_000
BASELINE = tuple(range(0, 4))
SIGNAL = tuple(range(4, 8))


def _pump(until, seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return bool(until())


def _step(order: int):
    return SimpleNamespace(ref=("a", order), order=order)


class _Plan:
    """The two public queries the module reads from the experimental plan."""

    def __init__(self) -> None:
        self.baseline, self.signal = _step(1), _step(2)
        self.cubes = {1: BASELINE, 2: SIGNAL}

    def baseline_step(self, pair: int = 1):
        return self.baseline

    def signal_step(self, pair: int = 1):
        return self.signal

    def cubes_of(self, step):
        return self.cubes[step.order]


class _Selection:
    def __init__(self, ids) -> None:
        self.ids = set(ids)

    def selected_roi_ids(self):
        return frozenset(self.ids)


class _Dataset:
    def cube_spans_unix_ms(self, cubes):
        first = np.array([T0 + c * 10_000.0 for c in cubes])
        return first, first + 6_000.0


class _Toolbox:
    def detection_settings(self):
        return AreaRoiDetectionSettings()


class ModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = SyntheticDataset(n_cubes=8, wavelengths=WAVELENGTHS)
        cls.free_form = AreaRoi(7, 70.0, 50.0, 8.0, sample_geometry_type="mask", sample_mask=RoiMask(66, 46, np.ones((8, 9), dtype=bool)))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.data.close()

    def setUp(self) -> None:
        self.plan = _Plan()
        self.selection = _Selection([2, 3, 5])
        self.settings = AnalysisSettingsModule()
        self.settings.set_metric_settings(MetricSettings(fit_method="none"))
        rois = make_rois()
        self.module = tm.RoiTuningModule(
            lambda: self.data.source(rois), self.plan, self.selection, self.settings, _Dataset(), _Toolbox(), helper_process=False
        )
        self.module.set_lattice(3, 1.0)
        self.matrix_changes = 0
        self.module.matrix_changed.connect(self._count)

    def tearDown(self) -> None:
        self.module.task.shutdown()

    def _count(self) -> None:
        self.matrix_changes += 1

    def _run_to_ready(self) -> None:
        self.assertTrue(self.module.run(processes=1), self.module.message())
        self.assertTrue(_pump(lambda: self.module.state() != tm.RUNNING), "the trial did not finish")
        self.assertEqual(self.module.state(), tm.READY, self.module.message())

    # -- before a trial -------------------------------------------------------------------------------------------------------

    def test_a_trial_without_a_selection_says_so(self) -> None:
        self.selection.ids.clear()
        self.assertEqual(self.module.preview(), "Select at least one ROI first.")
        self.assertFalse(self.module.can_run())
        self.assertFalse(self.module.run(processes=1))
        self.assertEqual(self.module.message(), "Select at least one ROI first.")
        self.assertEqual(self.module.state(), tm.IDLE)

    def test_a_trial_needs_both_plan_steps(self) -> None:
        self.plan.signal = None
        self.assertIn("baseline step (B1) and a signal step (S1)", self.module.preview())
        self.assertFalse(self.module.can_run())

    def test_the_same_step_for_both_is_refused(self) -> None:
        self.plan.signal = self.plan.baseline
        self.assertIn("same step", self.module.preview())

    def test_a_step_without_whole_cubes_or_a_baseline_too_short_for_a_noise_is_refused(self) -> None:
        self.plan.cubes[2] = ()
        self.assertIn("signal step 2 contains no whole cube", self.module.preview())
        self.plan.cubes[2] = SIGNAL
        self.plan.cubes[1] = (0, 1)
        self.assertIn("needs at least 3", self.module.preview())

    def test_the_preview_says_what_would_be_tried(self) -> None:
        self.assertEqual(self.module.preview(), "9 positions x 3 ROIs x 8 cubes (baseline 0-3, signal 4-7).")
        self.assertTrue(self.module.can_run())

    def test_a_lattice_that_is_not_a_lattice_is_refused_and_the_old_one_stays(self) -> None:
        self.assertFalse(self.module.set_lattice(4, 1.0))
        self.assertIn("odd", self.module.message())
        self.assertFalse(self.module.set_lattice(3, -1.0))
        self.assertEqual((self.module.lattice().size, self.module.lattice().pitch_px), (3, 1.0))

    def test_the_defaults_are_the_maintainers_normal_lattice_and_the_smoothed_snr(self) -> None:
        fresh = tm.RoiTuningModule(lambda: None, self.plan, self.selection, self.settings, _Dataset(), _Toolbox(), helper_process=False)
        self.assertEqual((fresh.lattice().size, fresh.lattice().pitch_px), (5, 1.0))
        self.assertEqual(fresh.objective_key(), "snr_smooth")
        self.assertTrue(fresh.show_crosses())
        self.assertFalse(fresh.show_best())
        fresh.task.shutdown()

    # -- a trial --------------------------------------------------------------------------------------------------------------

    def test_a_finished_trial_installs_a_result_and_a_ranking_and_says_what_would_move(self) -> None:
        states: list[str] = []
        self.module.state_changed.connect(lambda: states.append(self.module.state()))
        self._run_to_ready()
        self.assertIn(tm.RUNNING, states)
        result, ranking = self.module.result(), self.module.ranking()
        self.assertEqual(result.matrix.roi_ids, (2, 3, 5))
        self.assertEqual(result.matrix.mean.shape[:3], (9, 8, 3))
        self.assertEqual(result.traces.shape, (9, 3, 8))
        self.assertEqual(ranking.roi_ids, (2, 3, 5))
        self.assertEqual(self.matrix_changes, 1)
        self.assertRegex(self.module.message(), r"^(\d of 3 ROIs would move|No ROI would move)")

    def test_a_trial_without_usable_numbers_does_not_claim_the_centre_is_best(self) -> None:
        class _Untimed:  # cubes without acquisition times: no cube can be assigned to a step
            def cube_spans_unix_ms(self, cubes):
                return np.full(len(cubes), np.nan), np.full(len(cubes), np.nan)

        rois = make_rois()
        module = tm.RoiTuningModule(lambda: self.data.source(rois), self.plan, self.selection, self.settings, _Untimed(), _Toolbox(), helper_process=False)
        module.set_lattice(3, 1.0)
        try:
            self.assertTrue(module.run(processes=1))
            self.assertTrue(_pump(lambda: module.state() != tm.RUNNING))
            self.assertEqual(module.state(), tm.READY)
            self.assertTrue(module.message().startswith("No ROI could be ranked"), module.message())
            self.assertEqual(module.n_moving(), 0)
            self.assertEqual(module.proposed_centres(), {2: (40.0, 16.0), 3: (64.0, 16.0), 5: (40.0, 38.0)})  # nobody moves
        finally:
            module.task.shutdown()

    def test_the_proposed_centres_are_the_current_ones_plus_the_best_offsets(self) -> None:
        self._run_to_ready()
        matrix, ranking = self.module.result().matrix, self.module.ranking()
        centres = self.module.proposed_centres()
        self.assertEqual(sorted(centres), [2, 3, 5])
        for j, roi_id in enumerate(matrix.roi_ids):
            offset = ranking.offsets[ranking.best[j]] if ranking.moves[j] else np.zeros(2)
            np.testing.assert_allclose(centres[roi_id], matrix.roi_xy[j] + offset)
        self.assertEqual(self.module.n_moving(), int(ranking.moves.sum()))

    def test_switching_the_objective_re_ranks_without_a_new_scan(self) -> None:
        self._run_to_ready()
        first_result, before = self.module.result(), self.matrix_changes
        self.assertTrue(self.module.set_objective("signal"))
        self.assertIs(self.module.result(), first_result)  # the same numbers
        self.assertEqual(self.module.ranking().key, "signal")
        self.assertEqual(self.module.state(), tm.READY)
        self.assertEqual(self.matrix_changes, before + 1)
        self.assertFalse(self.module.set_objective("nonsense"))
        self.assertIn("snr_smooth", self.module.message())  # it names the valid keys
        self.assertEqual(self.module.objective_key(), "signal")

    def test_the_view_switches_announce_themselves_and_touch_nothing_else(self) -> None:
        seen = []
        self.module.view_changed.connect(lambda: seen.append(1))
        self.module.set_show_best(True)
        self.module.set_show_best(True)  # no change: no signal
        self.module.set_show_crosses(False)
        self.assertEqual((len(seen), self.module.show_best(), self.module.show_crosses()), (2, True, False))
        self.assertEqual(self.matrix_changes, 0)

    def test_a_changed_lattice_applies_to_the_next_trial_not_to_the_result_in_hand(self) -> None:
        self._run_to_ready()
        self.module.set_lattice(5, 1.0)
        self.assertEqual(self.module.result().matrix.lattice.size, 3)
        self.assertEqual(self.module.state(), tm.READY)

    def test_a_free_form_roi_is_refused_by_its_id_and_nothing_is_installed(self) -> None:
        rois = make_rois() + [self.free_form]
        module = tm.RoiTuningModule(lambda: self.data.source(rois), self.plan, _Selection([2, 7]), self.settings, _Dataset(), _Toolbox(), helper_process=False)
        module.set_lattice(3, 1.0)
        try:
            self.assertTrue(module.run(processes=1))
            self.assertTrue(_pump(lambda: module.state() != tm.RUNNING))
            self.assertEqual(module.state(), tm.IDLE)
            self.assertIn("ROI 7 is a free-form ROI", module.message())
        finally:
            module.task.shutdown()

    def test_stop_returns_nothing(self) -> None:
        self.assertTrue(self.module.run(processes=1))
        self.module.cancel()
        self.assertTrue(_pump(lambda: self.module.state() != tm.RUNNING))
        self.assertEqual(self.module.state(), tm.IDLE)
        self.assertIsNone(self.module.result())
        self.assertEqual(self.module.message(), "Stopped.")

    def test_a_second_run_while_one_is_going_is_refused(self) -> None:
        self.assertTrue(self.module.run(processes=1))
        self.assertFalse(self.module.run(processes=1))
        self.assertEqual(self.module.message(), "A trial is already running.")
        self.module.cancel()
        _pump(lambda: self.module.state() != tm.RUNNING)

    # -- after a trial --------------------------------------------------------------------------------------------------------

    def test_a_change_that_makes_the_result_wrong_marks_it_stale_and_a_new_trial_clears_that(self) -> None:
        self._run_to_ready()
        before = self.matrix_changes
        self.module.mark_stale()
        self.assertEqual(self.module.state(), tm.STALE)
        self.assertIn("run it again", self.module.message())
        self.assertEqual(self.matrix_changes, before + 1)  # the crosses are redrawn dimmed
        self.assertIsNotNone(self.module.result())  # still there to look at
        self.assertFalse(self.module.refit())  # a stale result is not re-fitted
        self.module.mark_stale()  # twice: nothing more happens
        self.assertEqual(self.matrix_changes, before + 1)
        self._run_to_ready()

    def test_marking_stale_without_a_result_does_nothing(self) -> None:
        self.module.mark_stale()
        self.assertEqual(self.module.state(), tm.IDLE)
        self.assertEqual(self.matrix_changes, 0)

    def test_a_change_of_the_fit_refits_the_same_matrix_in_the_background(self) -> None:
        self._run_to_ready()
        old = self.module.result()
        self.settings.set_metric_settings(MetricSettings(fit_method="none", metric_key="centroid"))
        self.assertTrue(_pump(lambda: self.module.result() is not old), "no refit arrived")
        self.assertTrue(_pump(lambda: self.module.state() == tm.READY))
        self.assertIs(self.module.result().matrix, old.matrix)  # no new scan
        self.assertEqual(self.module.result().request.metric.metric_key, "centroid")
        self.assertFalse(np.array_equal(self.module.result().traces, old.traces, equal_nan=True))

    def test_a_change_of_a_setting_that_does_not_touch_the_fit_does_not_refit(self) -> None:
        self._run_to_ready()
        old = self.module.result()
        self.module._on_settings_changed(AnalysisSettingsChange(field="something_else"))
        _APP.processEvents()
        self.assertIs(self.module.result(), old)
        self.assertEqual(self.module.state(), tm.READY)

    def test_closing_the_dataset_drops_the_result(self) -> None:
        self._run_to_ready()
        self.module.invalidate()
        self.assertEqual(self.module.state(), tm.IDLE)
        self.assertIsNone(self.module.result())
        self.assertIsNone(self.module.ranking())
        self.assertEqual(self.module.proposed_centres(), {})

    def test_clear_drops_the_result_and_tells_the_overlay(self) -> None:
        self._run_to_ready()
        before = self.matrix_changes
        self.module.clear()
        self.assertEqual((self.module.state(), self.matrix_changes), (tm.IDLE, before + 1))

    def test_a_trial_that_finishes_after_the_dataset_changed_is_dropped(self) -> None:
        self.assertTrue(self.module.run(processes=1))
        self.module.invalidate()
        self.assertTrue(_pump(lambda: not self.module.task.is_running()))
        _APP.processEvents()
        self.assertIsNone(self.module.result())
        self.assertEqual(self.module.state(), tm.IDLE)

    # -- export ---------------------------------------------------------------------------------------------------------------

    def test_the_export_has_one_row_per_roi_and_position_with_one_centre_and_at_most_one_best(self) -> None:
        self._run_to_ready()
        done: list[tuple[str, str]] = []
        self.module.export_finished.connect(lambda path, error: done.append((path, error)))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "tuning.csv"
            self.assertTrue(self.module.export_csv(path))
            self.assertTrue(_pump(lambda: bool(done)))
            self.assertEqual(done, [(str(path), "")])
            with open(path, newline="", encoding="utf-8") as handle:
                table = list(csv.DictReader(handle))
        self.assertEqual(tuple(table[0].keys()), COLUMNS)
        self.assertEqual(len(table), 9 * 3)
        for roi_id in ("2", "3", "5"):
            rows = [r for r in table if r["roi_id"] == roi_id]
            self.assertEqual(len(rows), 9)
            self.assertEqual(sum(int(r["is_centre"]) for r in rows), 1)
            self.assertLessEqual(sum(int(r["is_best"]) for r in rows), 1)
        centre = next(r for r in table if r["is_centre"] == "1")
        self.assertEqual((float(centre["dx_px"]), float(centre["dy_px"])), (0.0, 0.0))

    def test_an_unwritable_path_is_reported_not_raised(self) -> None:
        self._run_to_ready()
        done: list[tuple[str, str]] = []
        self.module.export_finished.connect(lambda path, error: done.append((path, error)))
        self.assertTrue(self.module.export_csv(Path(tempfile.gettempdir()) / "no_such_folder_for_tuning" / "x.csv"))
        self.assertTrue(_pump(lambda: bool(done)))
        self.assertNotEqual(done[0][1], "")

    def test_nothing_to_export_without_a_result(self) -> None:
        self.assertFalse(self.module.export_csv(Path("unused.csv")))
        self.assertIn("run a trial first", self.module.message())


if __name__ == "__main__":
    unittest.main()
