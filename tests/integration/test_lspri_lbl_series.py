"""The LbL (layer-by-layer) series (2026-11-22): the start step and steps per cycle of `ExperimentPlanModule`, the numbered chips (0, 1, 2 ...), the session file (schema 1.15,
block "plan", key "lbl"), the arithmetic of `analysis/lbl_series.py`, the Sensorgram's LbL button and the floating plot. No dataset files."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np

    from lspr_core import ImagingAcquisitionMetadata, ImagingPlan, ImagingPlanStep
    from lspr_imaging_app.analysis import step_table as st
    from lspr_imaging_app.analysis.lbl_series import lbl_points
    from lspr_imaging_app.frame_naming import FrameNamingScheme
    from lspr_imaging_app.plan.edits import describe_role
    from lspr_imaging_app.plan.lbl import lbl_indices
    from lspr_imaging_app.plan.state import LblSeries, PlanEditsState, measured_ref
    from lspr_imaging_app.selection.experiment_plan_module import ExperimentPlanModule
    from lspr_imaging_app.storage.session import SESSION_SCHEMA_VERSION, SessionState, load_session, save_session, session_path
    from lspr_imaging_app.undo import undo_manager
    from tests.integration.test_lspri_plan_structure_module import _FakeDataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

# water, PAH, water, PSS, water, water(rinse) | water, PAH, water, ... : 7 steps with a cycle of 3 steps (H2O, PAH, H2O) is enough for the tests
NOTES = ("H2O_--", "PAH_--", "H2O_--", "PSS_--", "H2O_--", "PAH_--", "H2O_--")
REFS = tuple(measured_ref(i + 1, 10_000 + 20_000 * i, note) for i, note in enumerate(NOTES))


def _metadata() -> ImagingAcquisitionMetadata:
    steps = [ImagingPlanStep(step=i + 1, label=note, start_unix_ms=10_000 + 20_000 * i, end_unix_ms=30_000 + 20_000 * i) for i, note in enumerate(NOTES)]
    return ImagingAcquisitionMetadata(source_format="test", plan=ImagingPlan(steps=steps))


class LblIndicesTest(unittest.TestCase):
    def test_the_start_and_every_period_th_step_after_it(self) -> None:
        self.assertEqual(lbl_indices(REFS, LblSeries(REFS[0], 6)), (0, 6))  # the maintainer's example: start 1, 6 per cycle -> steps 1, 7
        self.assertEqual(lbl_indices(REFS, LblSeries(REFS[1], 2)), (1, 3, 5))
        self.assertEqual(lbl_indices(REFS, LblSeries(REFS[0], 1)), tuple(range(7)))

    def test_no_series_or_a_start_that_is_not_there_gives_nothing(self) -> None:
        self.assertEqual(lbl_indices(REFS, None), ())
        self.assertEqual(lbl_indices(REFS[1:], LblSeries(REFS[0], 3)), ())


class LblModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.module = ExperimentPlanModule(_FakeDataset(_metadata()))
        self.reasons: list[str] = []
        self.module.steps_changed.connect(self.reasons.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_the_chips_are_the_cycle_numbers_and_the_start_is_zero(self) -> None:
        self.assertTrue(self.module.set_lbl(0, 3))
        self.assertEqual(self.reasons, ["roles"])
        self.assertEqual([s.roles for s in self.module.steps()], [("0",), (), (), ("1",), (), (), ("2",)])
        self.assertEqual([s.ref for s in self.module.lbl_steps()], [REFS[0], REFS[3], REFS[6]])
        self.assertIn("start", describe_role("0"))
        self.assertIn("Cycle 2", describe_role("2"))
        self.assertFalse(self.module.set_lbl(0, 3))  # nothing changed: no undo step
        undo_manager.undo()
        self.assertIsNone(self.module.lbl_series())
        self.assertEqual(self.module.lbl_steps(), ())

    def test_a_chip_sits_with_the_pair_chips(self) -> None:
        self.module.set_role("baseline", 0)
        self.module.add_to_bulk([0])
        self.module.set_lbl(0, 3)
        self.assertEqual(self.module.steps()[0].roles, ("B1", "Sb", "0"))

    def test_the_period_changes_the_series_and_needs_a_series(self) -> None:
        self.assertFalse(self.module.set_lbl_period(2))
        self.module.set_lbl(0, 3)
        self.assertTrue(self.module.set_lbl_period(2))
        self.assertEqual([s.order for s in self.module.lbl_steps()], [1, 3, 5, 7])
        self.assertTrue(self.module.clear_lbl())
        self.assertFalse(self.module.clear_lbl())

    def test_removing_the_start_step_clears_the_series_and_says_so(self) -> None:
        notices: list[str] = []
        self.module.notice.connect(notices.append)
        self.module.set_lbl(0, 3)
        self.module.remove_steps([0])
        self.assertIsNone(self.module.lbl_series())
        self.assertIn("LbL series", notices[-1])
        undo_manager.undo()
        self.assertEqual(len(self.module.lbl_steps()), 3)

    def test_removing_another_step_shifts_the_count_and_keeps_the_start(self) -> None:
        self.module.set_lbl(0, 3)
        self.module.remove_steps([1])  # one step fewer inside the first cycle: the period counts the steps that are there now
        self.assertEqual([s.ref for s in self.module.lbl_steps()], [REFS[0], REFS[4]])


class LblSessionFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_series_goes_into_the_session_file_and_comes_back(self) -> None:
        self.assertEqual(SESSION_SCHEMA_VERSION, "1.15")
        save_session(self.root, SessionState(plan_edits=PlanEditsState(lbl=LblSeries(REFS[0], 6))), self.naming)
        payload = json.loads(session_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(payload["plan"]["lbl"]["period"], 6)
        self.assertEqual(load_session(self.root).plan_edits.lbl, LblSeries(REFS[0], 6))

    def test_no_series_writes_no_key_and_a_1_14_file_opens_without_one(self) -> None:
        path = save_session(self.root, SessionState(plan_edits=PlanEditsState()), self.naming)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("lbl", payload["plan"])
        payload["schema_version"] = "1.14"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(load_session(self.root).plan_edits.lbl)

    def test_an_unreadable_series_is_skipped(self) -> None:
        self.assertIsNone(PlanEditsState.from_payload({"edits": [], "lbl": {"start": {"added": 1}, "period": 0}}).lbl)
        self.assertIsNone(PlanEditsState.from_payload({"edits": [], "lbl": "junk"}).lbl)
        self.assertEqual(PlanEditsState.from_payload({"edits": [], "lbl": {"start": {"added": 1}, "period": 4}}).lbl, LblSeries(("a", 1), 4))


class LblPointsTest(unittest.TestCase):
    def _numbers(self, means, sds, n=25) -> st.RowNumbers:
        k = len(means)
        return st.RowNumbers(refs=REFS[:k], n=np.full(k, n), mean=np.array(means, float), sd=np.array(sds, float))

    def test_accumulated_is_from_the_start_and_per_cycle_from_the_previous_one(self) -> None:
        numbers = self._numbers([100.0, 110.0, 125.0, 135.0], [0.5, 0.5, 0.5, 0.5])
        points = lbl_points(numbers, REFS[:4])
        self.assertEqual([p.cycle for p in points], [0, 1, 2, 3])
        np.testing.assert_allclose([p.accumulated for p in points], [0.0, 10.0, 25.0, 35.0])
        self.assertTrue(math.isnan(points[0].cycle_shift))
        np.testing.assert_allclose([p.cycle_shift for p in points[1:]], [10.0, 15.0, 10.0])

    def test_uncertainty_adds_the_two_sems_in_quadrature(self) -> None:
        sem = 0.5 / math.sqrt(25)
        points = lbl_points(self._numbers([100.0, 110.0, 125.0], [0.5, 0.5, 0.5]), REFS[:3])
        self.assertEqual(points[0].accumulated_u, 0.0)
        self.assertAlmostEqual(points[2].accumulated_u, math.hypot(sem, sem), places=12)
        self.assertAlmostEqual(points[2].cycle_shift_u, math.hypot(sem, sem), places=12)

    def test_a_missing_number_gives_nan_never_a_guess(self) -> None:
        numbers = self._numbers([100.0, math.nan, 125.0], [0.5, 0.5, 0.5])
        points = lbl_points(numbers, REFS[:3])
        self.assertTrue(math.isnan(points[1].accumulated) and math.isnan(points[1].cycle_shift))
        self.assertTrue(math.isnan(points[2].cycle_shift))  # its previous cycle has no mean
        self.assertAlmostEqual(points[2].accumulated, 25.0)
        points = lbl_points(self._numbers([100.0, 110.0], [0.5, 0.5], n=1), REFS[:2])  # one cube: no SEM, so the shift has no uncertainty
        self.assertAlmostEqual(points[1].accumulated, 10.0)
        self.assertTrue(math.isnan(points[1].accumulated_u))

    def test_a_step_the_line_was_not_computed_with_gives_nan(self) -> None:
        points = lbl_points(self._numbers([100.0, 110.0], [0.5, 0.5]), REFS[:3])
        self.assertTrue(math.isnan(points[2].mean))


class LblControlsTest(unittest.TestCase):
    def setUp(self) -> None:
        from lspr_imaging_app.panels.sensorgram.lbl_group import LblControls

        self.host = QtWidgets.QWidget()
        self.controls = LblControls(self.host)

    def tearDown(self) -> None:
        self.host.deleteLater()

    def test_the_use_button_follows_the_selection_and_reports_the_period(self) -> None:
        c = self.controls
        c.set_available(True)
        c.set_state(None, "", has_single_selection=False)
        self.assertEqual((c._use.isEnabled(), c._clear.isEnabled()), (False, False))
        c.set_state(None, "", has_single_selection=True)
        got: list[int] = []
        c.start_requested.connect(got.append)
        c._period.setValue(4)
        c._use.click()
        self.assertEqual(got, [4])
        c.set_state("1 · H2O", "Uses steps 1, 7 (cycles 0 to 1).", has_single_selection=True, period=6)
        self.assertEqual((c._start.text(), c._clear.isEnabled(), c.period()), ("1 · H2O", True, 6))

    def test_the_period_signals_only_for_the_user_and_is_remembered(self) -> None:
        from lspr_imaging_app.panels.sensorgram.lbl_group import LblControls
        from lspr_imaging_app.panels.ui_state import UiStateStore

        store = UiStateStore({})
        heard: list[int] = []
        self.controls.period_changed.connect(heard.append)
        self.controls.restore_ui_state(store)
        self.controls.set_state("1 · H2O", "", has_single_selection=False, period=5)  # from the session: no signal
        self.assertEqual(heard, [])
        self.controls._period.setValue(7)  # the user
        self.assertEqual(heard, [7])
        other_host = QtWidgets.QWidget()
        other = LblControls(other_host)
        other.restore_ui_state(UiStateStore(dict(store._values)))
        self.assertEqual(other.period(), 7)
        other_host.deleteLater()


class LblPlotTest(unittest.TestCase):
    def test_both_shifts_are_drawn_and_exported(self) -> None:
        from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine
        from lspr_imaging_app.panels.sensorgram.lbl_plot import SHIFT_CYCLE, LblPlotWindow, LblStep

        numbers = st.RowNumbers(refs=REFS[:3], n=np.full(3, 25), mean=np.array([100.0, 110.0, 125.0]), sd=np.full(3, 0.5))
        plot = LblPlotWindow()
        plot.set_steps([LblStep(REFS[0], 1, "H2O"), LblStep(REFS[1], 2, "PAH"), LblStep(REFS[2], 3, "H2O")])
        plot.set_lines([BulkLine("ROI 1", "#38bdf8", numbers)], y_label="Δλ (nm)")
        plot.redraw()
        self.assertEqual(plot.status.text(), "")
        data = plot.export_data()
        table = data.tables["lbl_points"]
        self.assertEqual(len(table.rows), 3)
        self.assertAlmostEqual(table.rows[2][7], 25.0)  # accumulated shift of cycle 2
        plot.shift_pick.setCurrentIndex(plot.shift_pick.findData(SHIFT_CYCLE))
        plot.redraw()
        self.assertEqual(data.info["shift_shown"], "accumulated")
        self.assertEqual(plot.export_data().info["shift_shown"], "cycle")
        plot.deleteLater()

    def test_fewer_than_two_cycles_says_what_to_do(self) -> None:
        from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow

        plot = LblPlotWindow()
        plot.redraw()
        self.assertIn("LbL pop-up", plot.status.text())
        plot.deleteLater()


if __name__ == "__main__":
    unittest.main()
