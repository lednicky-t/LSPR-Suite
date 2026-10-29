"""`ExperimentPlanModule` after 2026-10-29: removing, restoring, unifying and adding steps, the baseline / signal roles, what other panels read (`step_by_ref`, `cubes_of`),
and the new fields in the session file (schema 1.6).

A QApplication is needed only because the module is a QObject; the dataset is a small fake with what the module reads. No dataset files."""

from __future__ import annotations

import json
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
    from PyQt6.QtCore import QObject, pyqtSignal

    from lspr_core import ImagingAcquisitionMetadata, ImagingPlan, ImagingPlanStep
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.plan.module import ExperimentPlanModule
    from lspr_imaging_app.plan.state import EvalPair, added_ref, measured_ref
    from lspr_imaging_app.storage.session import SESSION_SCHEMA_VERSION, SessionState, load_session, save_session, session_path
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

NOTE1, NOTE2 = "H2O_--_ch1F", "10perEG_-_ch1F"
M1, M2 = measured_ref(1, 10_000, NOTE1), measured_ref(2, 30_000, NOTE2)
CUBES = list(range(41))


class _FakeDataset(QObject):
    """What the module reads from DatasetModule: two signals, the metadata, and the cube clock (cube c starts at 2 s * c; its last frame is 1.5 s later)."""

    dataset_loaded = pyqtSignal(object)
    dataset_cleared = pyqtSignal()

    def __init__(self, metadata: ImagingAcquisitionMetadata | None) -> None:
        super().__init__()
        self.metadata = metadata
        self.cubes = tuple(CUBES)

    def acquisition_metadata(self):
        return self.metadata

    def cube_start_unix_ms(self, cube_index: int):
        return 2_000 * int(cube_index)

    def data_end_unix_ms(self):
        return 81_000

    def spectral_cubes(self):
        return self.cubes

    def cube_spans_unix_ms(self, cubes):
        first = np.array([2_000.0 * c for c in cubes])
        return first, first + 1_500.0


def _metadata() -> ImagingAcquisitionMetadata:
    steps = [
        ImagingPlanStep(step=1, label=NOTE1, start_unix_ms=10_000, end_unix_ms=30_000),
        ImagingPlanStep(step=2, label=NOTE2, start_unix_ms=30_000, end_unix_ms=50_000),  # joined to step 1
    ]
    return ImagingAcquisitionMetadata(source_format="test", plan=ImagingPlan(steps=steps))


class StructureTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.dataset = _FakeDataset(_metadata())
        self.module = ExperimentPlanModule(self.dataset)
        self.reasons: list[str] = []
        self.notices: list[str] = []
        self.module.steps_changed.connect(self.reasons.append)
        self.module.notice.connect(self.notices.append)
        self.grid = self.module.snap_grid_unix_ms(CUBES)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_removing_a_step_hides_it_renumbers_the_rest_and_is_one_undo_step(self) -> None:
        self.assertTrue(self.module.remove_steps([0]))
        self.assertEqual(self.reasons, ["structure"])
        self.assertEqual([(s.order, s.ref) for s in self.module.steps()], [(1, M2)])  # step 2 is now number 1; its reference is the same
        self.assertTrue(self.module.has_removed())
        undo_manager.undo()
        self.assertEqual([s.ref for s in self.module.steps()], [M1, M2])
        undo_manager.redo()
        self.assertEqual(len(self.module.steps()), 1)

    def test_restoring_the_removed_steps_is_a_command_of_its_own(self) -> None:
        self.assertFalse(self.module.restore_removed())  # nothing to restore: no undo step
        self.module.remove_steps([0, 1])
        self.assertEqual(self.module.steps(), ())
        self.assertTrue(self.module.restore_removed())
        self.assertEqual([s.ref for s in self.module.steps()], [M1, M2])
        self.assertFalse(self.module.has_removed())

    def test_unify_joins_neighbours_in_one_step_and_refuses_the_rest(self) -> None:
        self.assertFalse(self.module.can_unify([0]))
        self.assertFalse(self.module.unify_steps([0]))
        undo_manager.clear()
        self.assertTrue(self.module.can_unify([1, 0]))
        self.assertTrue(self.module.unify_steps([1, 0]))
        (only,) = self.module.steps()
        self.assertEqual((only.ref, only.start_unix_ms, only.end_unix_ms), (M1, 10_000, 50_000))
        undo_manager.undo()
        self.assertEqual(len(self.module.steps()), 2)

    def test_add_returns_the_new_steps_reference_and_undo_takes_it_away(self) -> None:
        ref = self.module.add_step(0, "after", self.grid)  # joined to step 2: half of step 1 is cut off
        self.assertEqual(ref, added_ref(1))
        steps = self.module.steps()
        self.assertEqual([(s.order, s.is_added) for s in steps], [(1, False), (2, True), (3, False)])
        self.assertEqual((steps[0].end_unix_ms, steps[1].start_unix_ms), (20_000, 20_000))
        self.assertEqual(self.module.step_by_ref(ref).order, 2)
        undo_manager.undo()
        self.assertEqual(([s.ref for s in self.module.steps()], self.module.step_by_ref(ref)), ([M1, M2], None))

    def test_add_with_no_room_says_so_and_changes_nothing(self) -> None:
        self.dataset.metadata = ImagingAcquisitionMetadata(source_format="test", plan=ImagingPlan(steps=[
            ImagingPlanStep(step=1, label="a", start_unix_ms=10_000, end_unix_ms=12_000),
            ImagingPlanStep(step=2, label="b", start_unix_ms=12_000, end_unix_ms=20_000),
        ]))
        undo_manager.clear()
        self.assertIsNone(self.module.add_step(0, "after", self.grid))
        self.assertEqual(len(self.notices), 1)
        self.assertFalse(undo_manager.can_undo)

    def test_the_roles_are_set_read_moved_and_cleared(self) -> None:
        self.assertTrue(self.module.set_role("baseline", 0))
        self.assertTrue(self.module.set_role("signal", 1))
        self.assertEqual(self.reasons, ["roles", "roles"])
        self.assertEqual((self.module.baseline_step().ref, self.module.signal_step().ref), (M1, M2))
        self.assertEqual([s.roles for s in self.module.steps()], [("B1",), ("S1",)])
        self.assertTrue(self.module.set_role("signal", 0))  # the baseline step takes the signal role: the baseline is cleared
        self.assertEqual((self.module.baseline_step(), self.module.signal_step().ref), (None, M1))
        self.assertTrue(self.module.set_role("signal", None))
        self.assertEqual(self.module.state().pairs, ())
        self.assertFalse(self.module.set_role("baseline", 9))  # no such step
        self.assertEqual(self.module.baseline_step(2), None)

    def test_a_second_pair_is_independent(self) -> None:
        self.module.set_role("baseline", 0)
        self.module.set_role("baseline", 1, pair=2)
        self.assertEqual([s.roles for s in self.module.steps()], [("B1",), ("B2",)])
        self.assertEqual((self.module.baseline_step(1).ref, self.module.baseline_step(2).ref), (M1, M2))

    def test_clearing_a_pair_is_one_undo_step(self) -> None:
        self.module.set_role("baseline", 0, pair=2)
        self.module.set_role("signal", 1, pair=2)
        self.module.set_role("baseline", 0)
        undo_manager.clear()
        self.reasons.clear()
        self.assertTrue(self.module.clear_pair(2))
        self.assertEqual(self.reasons, ["roles"])
        self.assertEqual((self.module.baseline_step(2), self.module.signal_step(2)), (None, None))
        self.assertEqual(self.module.baseline_step(1).ref, M1)  # pair 1 is not touched
        self.assertFalse(self.module.clear_pair(2))  # nothing left to clear: no undo step
        undo_manager.undo()  # one undo brings back both roles
        self.assertEqual((self.module.baseline_step(2).ref, self.module.signal_step(2).ref), (M1, M2))
        self.assertFalse(undo_manager.can_undo)

    def test_removing_a_step_clears_its_role_and_the_notice_says_which(self) -> None:
        self.module.set_role("baseline", 0)
        self.module.set_role("signal", 1)
        self.notices.clear()
        self.module.remove_steps([1])
        self.assertIsNone(self.module.signal_step())
        self.assertEqual(self.module.baseline_step().ref, M1)
        self.assertEqual(len(self.notices), 1)
        self.assertIn("signal of pair 1", self.notices[0])
        undo_manager.undo()  # the undo brings the role back with the step
        self.assertEqual(self.module.signal_step().ref, M2)

    def test_a_role_set_on_a_step_follows_it_when_another_is_added_before_it(self) -> None:
        self.module.set_role("signal", 1)
        ref = self.module.add_step(1, "before", self.grid)
        self.assertIsNotNone(ref)
        self.assertEqual(self.module.signal_step().ref, M2)
        self.assertEqual(self.module.signal_step().order, 3)  # its number changed, the role did not move

    def test_the_cubes_of_a_step_are_its_whole_cubes(self) -> None:
        first, second = self.module.steps()
        self.assertEqual(self.module.cubes_of(first), tuple(range(5, 15)))  # 10 s - 30 s: cube 14 (28 - 29.5 s) is the last wholly inside
        self.assertEqual(self.module.cubes_of(second), tuple(range(15, 25)))
        self.module.move_edge(0, "end", 29_000, self.grid)  # shorten step 1 to 28 s: cube 14 (28 s - 29.5 s) now straddles its end
        self.assertEqual(self.module.cubes_of(self.module.steps()[0]), tuple(range(5, 14)))

    def test_no_dataset_cubes_means_no_step_cubes(self) -> None:
        self.dataset.cubes = ()
        self.assertEqual(self.module.cubes_of(self.module.steps()[0]), ())

    def test_reset_all_brings_back_removed_steps_and_drops_added_ones(self) -> None:
        self.module.add_step(0, "after", self.grid)
        self.module.remove_steps([2])
        self.assertTrue(self.module.reset_all())
        self.assertEqual([s.ref for s in self.module.steps()], [M1, M2])
        self.assertEqual(self.module.state().next_uid, 2)

    def test_restoring_a_session_replaces_the_changes_without_an_undo_step(self) -> None:
        other = ExperimentPlanModule(_FakeDataset(_metadata()))
        other.remove_steps([0])
        other.set_role("signal", 0)
        undo_manager.clear()
        self.reasons.clear()
        self.module.restore_state(other.state())
        self.assertEqual((self.reasons, [s.ref for s in self.module.steps()], self.module.signal_step().ref), (["restored"], [M2], M2))
        self.assertFalse(undo_manager.can_undo)


class SessionFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])
        undo_manager.clear()
        module = ExperimentPlanModule(_FakeDataset(_metadata()))
        grid = module.snap_grid_unix_ms(CUBES)
        module.set_label(0, "Water")
        module.add_step(0, "after", grid)
        module.remove_steps([2])  # step 2 hidden
        module.set_role("baseline", 0)
        module.set_role("signal", 1)
        self.state = module.state()
        undo_manager.clear()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_removed_added_and_the_pairs_go_into_the_session_file_and_come_back(self) -> None:
        save_session(self.root, SessionState(plan_edits=self.state), self.naming)
        payload = json.loads(session_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], SESSION_SCHEMA_VERSION)
        self.assertEqual(SESSION_SCHEMA_VERSION, "1.6")
        plan = payload["plan"]
        # one edit: step 1 has the new note and the end it gave up when the new step was cut off it
        self.assertEqual((len(plan["edits"]), len(plan["removed"]), len(plan["added"]), plan["next_uid"], len(plan["pairs"])), (1, 1, 1, 2, 1))
        loaded = load_session(self.root).plan_edits
        self.assertEqual(loaded, self.state)
        self.assertEqual(loaded.pair(1), EvalPair(1, baseline=M1, signal=added_ref(1)))

    def test_the_module_after_a_save_and_load_shows_the_same_steps_and_roles(self) -> None:
        save_session(self.root, SessionState(plan_edits=self.state), self.naming)
        other = ExperimentPlanModule(_FakeDataset(_metadata()))
        other.restore_state(load_session(self.root).plan_edits)
        self.assertEqual([(s.order, s.label, s.roles, s.is_added) for s in other.steps()], [(1, "Water", ("B1",), False), (2, "", ("S1",), True)])
        self.assertEqual(other.add_step(0, "after", other.snap_grid_unix_ms(CUBES)), added_ref(2))  # the spent id is not handed out again

    def test_a_session_with_only_the_old_kinds_of_edit_is_written_as_before(self) -> None:
        module = ExperimentPlanModule(_FakeDataset(_metadata()))
        module.set_label(0, "Water")
        first = save_session(self.root, SessionState(plan_edits=module.state()), self.naming).read_bytes()
        self.assertEqual(set(json.loads(first)["plan"]), {"edits"})

    def test_a_1_5_file_opens_with_no_structure_changes(self) -> None:
        path = save_session(self.root, SessionState(plan_edits=self.state), self.naming)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "1.5"
        for name in ("removed", "added", "next_uid", "pairs"):
            del payload["plan"][name]
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_session(self.root).plan_edits
        self.assertEqual((loaded.removed, loaded.added, loaded.pairs, loaded.next_uid), ((), (), (), 1))
        self.assertEqual(len(loaded.edits), 1)  # the old kind of edit is read as before


if __name__ == "__main__":
    unittest.main()
