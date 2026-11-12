"""The S_bulk set of `ExperimentPlanModule` (2026-11-12): which steps the bulk sensitivity fit uses. Adding and removing several steps at once, one undo step each, the
``Sb`` chip, a removed step leaving the set, the session file (schema 1.8, block "plan", key "bulk"), and the table module's S_bulk column. No dataset files."""

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
    from lspr_imaging_app.frame_naming import FrameNamingScheme
    from lspr_imaging_app.plan.edits import BULK_CHIP, describe_role
    from lspr_imaging_app.plan.state import PlanEditsState, measured_ref
    from lspr_imaging_app.selection.experiment_plan_module import ExperimentPlanModule
    from lspr_imaging_app.storage.session import SessionState, load_session, save_session, session_path
    from lspr_imaging_app.undo import undo_manager
    from tests.integration.test_lspri_plan_structure_module import _FakeDataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

NOTES = ("H2O_--_ch1F", "10perEG_-_ch1F", "20perEG_-_ch1F", "H2O_--_ch1F")
REFS = tuple(measured_ref(i + 1, 10_000 + 20_000 * i, note) for i, note in enumerate(NOTES))


def _metadata() -> ImagingAcquisitionMetadata:
    steps = [ImagingPlanStep(step=i + 1, label=note, start_unix_ms=10_000 + 20_000 * i, end_unix_ms=30_000 + 20_000 * i) for i, note in enumerate(NOTES)]
    return ImagingAcquisitionMetadata(source_format="test", plan=ImagingPlan(steps=steps))


class BulkSetTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.module = ExperimentPlanModule(_FakeDataset(_metadata()))
        self.reasons: list[str] = []
        self.module.steps_changed.connect(self.reasons.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_several_steps_go_in_at_once_as_one_undo_step_and_wear_the_chip(self) -> None:
        self.assertTrue(self.module.add_to_bulk([2, 0, 1]))
        self.assertEqual(self.reasons, ["roles"])
        self.assertEqual([s.ref for s in self.module.bulk_steps()], list(REFS[:3]))  # in time order
        self.assertEqual([s.roles for s in self.module.steps()], [(BULK_CHIP,), (BULK_CHIP,), (BULK_CHIP,), ()])
        self.assertIn("S_bulk", describe_role(BULK_CHIP))
        self.assertFalse(self.module.add_to_bulk([0, 1]))  # all in already: no undo step
        undo_manager.undo()
        self.assertEqual(self.module.bulk_steps(), ())
        self.assertFalse(undo_manager.can_undo)

    def test_the_chip_follows_the_pair_chips_and_the_roles_stay_independent(self) -> None:
        self.module.set_role("baseline", 0)
        self.module.add_to_bulk([0, 1])
        self.assertEqual(self.module.steps()[0].roles, ("B1", BULK_CHIP))
        self.module.set_role("baseline", None)
        self.assertEqual(self.module.steps()[0].roles, (BULK_CHIP,))

    def test_remove_and_clear(self) -> None:
        self.module.add_to_bulk([0, 1, 2])
        self.assertTrue(self.module.remove_from_bulk([1]))
        self.assertEqual([s.ref for s in self.module.bulk_steps()], [REFS[0], REFS[2]])
        self.assertFalse(self.module.remove_from_bulk([3]))  # not in the set
        self.assertTrue(self.module.clear_bulk())
        self.assertFalse(self.module.clear_bulk())
        undo_manager.undo()
        self.assertEqual(len(self.module.bulk_steps()), 2)

    def test_a_removed_step_leaves_the_set_and_the_notice_says_so(self) -> None:
        notices: list[str] = []
        self.module.notice.connect(notices.append)
        self.module.add_to_bulk([0, 1])
        self.module.remove_steps([1])
        self.assertEqual([s.ref for s in self.module.bulk_steps()], [REFS[0]])
        self.assertIn("S_bulk step", notices[-1])
        undo_manager.undo()
        self.assertEqual(len(self.module.bulk_steps()), 2)


class BulkSessionFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_set_goes_into_the_session_file_and_comes_back(self) -> None:
        state = PlanEditsState(bulk=(REFS[2], REFS[0]))
        save_session(self.root, SessionState(plan_edits=state), self.naming)
        payload = json.loads(session_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(len(payload["plan"]["bulk"]), 2)
        self.assertEqual(load_session(self.root).plan_edits.bulk, (REFS[2], REFS[0]))

    def test_no_set_writes_no_key_and_a_1_7_file_opens_with_an_empty_set(self) -> None:
        path = save_session(self.root, SessionState(plan_edits=PlanEditsState()), self.naming)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("bulk", payload["plan"])
        payload["schema_version"] = "1.7"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(load_session(self.root).plan_edits.bulk, ())

    def test_an_unreadable_entry_is_skipped_and_the_rest_kept(self) -> None:
        state = PlanEditsState.from_payload({"edits": [], "bulk": [{"step": 1}, {"added": 3}, "junk"]})
        self.assertEqual(state.bulk, (("a", 3),))


class BulkValuesTest(unittest.TestCase):
    """`step_table.bulk_values`: one row's S_bulk from its step numbers."""

    def _numbers(self, means, sds, n=10) -> st.RowNumbers:
        k = len(means)
        return st.RowNumbers(refs=REFS[:k], n=np.full(k, n), mean=np.array(means, float), sd=np.array(sds, float))

    def test_the_slope_and_its_two_uncertainties(self) -> None:
        ri = (1.333, 1.3453, 1.3576)
        numbers = self._numbers([600.0 + 200.0 * (r - 1.333) for r in ri], [0.1, 0.1, 0.1])
        values = st.bulk_values(numbers, [st.BulkPoint(REFS[i], r) for i, r in enumerate(ri)])
        self.assertAlmostEqual(values.slope, 200.0, places=6)
        self.assertEqual(values.n, 3)
        self.assertAlmostEqual(values.sd / values.sem, math.sqrt(10), places=6)  # SEM = SD / sqrt(n): the slope's uncertainty scales the same
        self.assertTrue(values.fit_sem.ok)

    def test_a_step_the_row_has_no_number_for_is_left_out(self) -> None:
        numbers = self._numbers([600.0, 602.46], [0.1, 0.1])
        values = st.bulk_values(numbers, [st.BulkPoint(REFS[0], 1.333), st.BulkPoint(REFS[1], 1.3453), st.BulkPoint(REFS[2], 1.3576)])
        self.assertEqual(values.n, 2)
        self.assertAlmostEqual(values.slope, 2.46 / 0.0123, places=4)

    def test_one_solution_only_gives_no_number(self) -> None:
        numbers = self._numbers([600.0, 600.1], [0.1, 0.1])
        values = st.bulk_values(numbers, [st.BulkPoint(REFS[0], 1.333), st.BulkPoint(REFS[1], 1.333)])
        self.assertTrue(math.isnan(values.slope))
        self.assertIn("two solutions", values.fit_sem.reason)


class BulkControlsTest(unittest.TestCase):
    """The Sensorgram's S_bulk button (`panels/sensorgram/bulk_group.py`): display only, it reports what was clicked."""

    def setUp(self) -> None:
        from lspr_imaging_app.panels.sensorgram.bulk_group import BulkControls

        self.host = QtWidgets.QWidget()
        self.controls = BulkControls(self.host)

    def tearDown(self) -> None:
        self.host.deleteLater()

    def test_the_buttons_follow_the_selection_and_the_set_and_report_clicks(self) -> None:
        c = self.controls
        c.set_available(True)  # (a dataset with steps: the button and so its pop-up are enabled)
        c.set_state((), has_selection=False, selection_in_set=False)
        self.assertEqual((c._add.isEnabled(), c._remove.isEnabled(), c._clear.isEnabled()), (False, False, False))
        self.assertFalse(c._empty.isHidden())
        c.set_state((("1 · H2O", "1.33300"), ("3 · 20perEG", "no RI")), has_selection=True, selection_in_set=True)
        self.assertEqual((c._add.isEnabled(), c._remove.isEnabled(), c._clear.isEnabled()), (True, True, True))
        self.assertEqual([label.text() for label in c._rows], ["1 · H2O", "1.33300", "3 · 20perEG", "no RI"])
        self.assertTrue(c._empty.isHidden())
        got: list[str] = []
        c.add_requested.connect(lambda: got.append("add"))
        c.remove_requested.connect(lambda: got.append("remove"))
        c.clear_requested.connect(lambda: got.append("clear"))
        for button in (c._add, c._remove, c._clear):
            button.click()
        self.assertEqual(got, ["add", "remove", "clear"])

    def test_the_weighting_is_remembered(self) -> None:
        from lspr_imaging_app.panels.ui_state import UiStateStore
        from lspr_imaging_app.panels.sensorgram.bulk_group import BulkControls

        store = UiStateStore({})
        self.controls.restore_ui_state(store)
        self.assertEqual(self.controls.weighting(), "inverse_variance")
        heard: list[str] = []
        self.controls.weighting_changed.connect(heard.append)
        self.controls._weighting.setCurrentIndex(self.controls._weighting.findData("equal"))
        self.assertEqual(heard, ["equal"])
        other_host = QtWidgets.QWidget()
        other = BulkControls(other_host)
        other.restore_ui_state(UiStateStore(dict(store._values)))
        self.assertEqual(other.weighting(), "equal")
        other_host.deleteLater()


if __name__ == "__main__":
    unittest.main()
