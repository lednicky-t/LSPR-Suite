"""`ExperimentPlanModule`: the measured steps with the user's edits on top, its undo steps, and the edits in the session file (schema 1.5).

A QApplication is needed only because the module is a QObject; the dataset is a small fake with the four things the module reads. No dataset files."""

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
    from lspr_imaging_app.plan.state import PlanEdit, PlanEditsState
    from lspr_imaging_app.storage.session import SESSION_SCHEMA_VERSION, SessionState, load_session, save_session, session_path
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

NOTE = "H2O_--_ch1F"


def _metadata(*steps: ImagingPlanStep) -> ImagingAcquisitionMetadata:
    return ImagingAcquisitionMetadata(source_format="test", plan=ImagingPlan(steps=list(steps)))


class _FakeDataset(QObject):
    """The four things the module reads from DatasetModule: two signals, the metadata and the cube clock (a cube every 2 s from t = 0)."""

    dataset_loaded = pyqtSignal(object)
    dataset_cleared = pyqtSignal()

    def __init__(self, metadata: ImagingAcquisitionMetadata | None) -> None:
        super().__init__()
        self.metadata = metadata

    def acquisition_metadata(self):
        return self.metadata

    def cube_start_unix_ms(self, cube_index: int):
        return 2_000 * int(cube_index)

    def data_end_unix_ms(self):
        return 81_000


def _two_steps() -> ImagingAcquisitionMetadata:
    return _metadata(
        ImagingPlanStep(step=1, label=NOTE, start_unix_ms=10_000, end_unix_ms=30_000),
        ImagingPlanStep(step=2, label="10perEG_-_ch1F", start_unix_ms=30_000, end_unix_ms=50_000),
    )


class ModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.dataset = _FakeDataset(_two_steps())
        self.module = ExperimentPlanModule(self.dataset)
        self.reasons: list[str] = []
        self.module.steps_changed.connect(self.reasons.append)
        self.cubes = list(range(41))
        self.grid = self.module.snap_grid_unix_ms(self.cubes)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_the_steps_are_the_measured_ones_until_something_is_edited(self) -> None:
        self.assertEqual([(s.label, s.start_unix_ms, s.end_unix_ms) for s in self.module.steps()], [(NOTE, 10_000, 30_000), ("10perEG_-_ch1F", 30_000, 50_000)])
        self.assertTrue(self.module.has_steps())
        self.assertEqual(self.module.state(), PlanEditsState())

    def test_a_dataset_without_steps_has_none(self) -> None:
        self.dataset.metadata = ImagingAcquisitionMetadata(source_format="test")
        self.assertFalse(self.module.has_steps())
        self.dataset.metadata = None
        self.assertEqual(self.module.steps(), ())

    def test_the_snap_grid_is_the_plotted_cube_starts_and_the_end_of_the_data(self) -> None:
        grid = self.module.snap_grid_unix_ms([0, 1, 2])
        np.testing.assert_array_equal(grid, [0.0, 2_000.0, 4_000.0, 81_000.0])

    def test_the_axis_map_is_built_from_the_plotted_cubes(self) -> None:
        axis = self.module.axis_map([0, 1, 2, 3], [0.0, 2.0, 4.0, 6.0])  # Time mode: seconds from t = 0
        self.assertAlmostEqual(axis.x_at(5_000), 5.0, places=9)
        self.assertIsNone(self.module.axis_map([0], [0.0]))

    def test_a_label_edit_is_applied_announced_and_undoable(self) -> None:
        self.assertTrue(self.module.set_label(0, "Water"))
        self.assertEqual(self.module.steps()[0].label, "Water")
        self.assertEqual(self.reasons, ["label"])
        undo_manager.undo()
        self.assertEqual(self.module.steps()[0].label, NOTE)
        undo_manager.redo()
        self.assertEqual(self.module.steps()[0].label, "Water")

    def test_the_measured_plan_is_never_changed(self) -> None:
        self.module.set_label(0, "Water")
        self.module.set_color(1, "#336699")
        self.module.move_edge(0, "end", 42_000, self.grid, together=True)
        measured = self.module.measured_plan().steps
        self.assertEqual([(s.label, s.start_unix_ms, s.end_unix_ms, s.color) for s in measured],
                         [(NOTE, 10_000, 30_000, None), ("10perEG_-_ch1F", 30_000, 50_000, None)])

    def test_nothing_is_announced_or_pushed_when_nothing_changes(self) -> None:
        self.assertFalse(self.module.set_label(0, NOTE))
        self.assertFalse(self.module.set_color(0, None))
        self.assertFalse(self.module.move_edge(0, "end", 30_100, self.grid))  # snaps to where it already is
        self.assertFalse(self.module.set_label(7, "x"))  # no such step
        self.assertEqual(self.reasons, [])
        self.assertFalse(undo_manager.can_undo)

    def test_a_drag_is_one_undo_step_and_moves_the_joined_boundary(self) -> None:
        undo_manager.begin_batch("Move step boundary")
        for target in (32_000, 36_000, 41_500):  # what the mouse passes on the way
            self.module.move_edge(0, "end", target, self.grid, together=True)
        undo_manager.end_batch()
        steps = self.module.steps()
        self.assertEqual((steps[0].end_unix_ms, steps[1].start_unix_ms), (42_000, 42_000))
        undo_manager.undo()
        steps = self.module.steps()
        self.assertEqual((steps[0].end_unix_ms, steps[1].start_unix_ms), (30_000, 30_000))
        self.assertEqual(self.module.state(), PlanEditsState())
        self.assertFalse(undo_manager.can_undo)  # one entry only

    def test_by_default_only_the_dragged_step_changes(self) -> None:
        self.assertTrue(self.module.move_edge(0, "end", 20_000, self.grid))
        steps = self.module.steps()
        self.assertEqual([(s.start_unix_ms, s.end_unix_ms) for s in steps], [(10_000, 20_000), (30_000, 50_000)])  # a gap; the neighbour stays
        self.assertTrue(self.module.move_edge(1, "start", 24_000, self.grid))  # the time that was freed is open to the neighbour
        self.assertEqual([(s.start_unix_ms, s.end_unix_ms) for s in self.module.steps()], [(10_000, 20_000), (24_000, 50_000)])
        undo_manager.undo()
        undo_manager.undo()
        self.assertEqual(self.module.state(), PlanEditsState())

    def test_several_steps_get_one_colour_or_are_reset_in_one_undo_step(self) -> None:
        self.assertTrue(self.module.set_colors([0, 1], "#336699"))
        self.assertEqual([s.color for s in self.module.steps()], ["#336699", "#336699"])
        undo_manager.undo()
        self.assertEqual(self.module.state(), PlanEditsState())
        self.assertFalse(undo_manager.can_undo)  # both steps were one entry
        self.module.set_label(0, "A")
        self.module.set_label(1, "B")
        self.assertTrue(self.module.reset_steps([0, 1]))
        self.assertEqual(self.module.state(), PlanEditsState())
        undo_manager.undo()
        self.assertEqual([s.label for s in self.module.steps()], ["A", "B"])  # one entry brings both back
        self.assertTrue(self.module.set_colors([0, 1], None) is False)  # nothing to clear

    def test_several_steps_ignore_indices_that_do_not_exist(self) -> None:
        self.assertTrue(self.module.set_colors([1, 7, -1], "#336699"))
        self.assertEqual([s.color for s in self.module.steps()], [None, "#336699"])
        self.assertFalse(self.module.reset_steps([9]))

    def test_reset_step_and_reset_all(self) -> None:
        self.module.set_label(0, "A")
        self.module.set_color(1, "#112233")
        self.assertTrue(self.module.reset_step(0))
        self.assertEqual([e.step for e in self.module.state().edits], [2])
        self.assertTrue(self.module.reset_all())
        self.assertEqual(self.module.state(), PlanEditsState())
        self.assertFalse(self.module.reset_all())

    def test_restoring_a_state_replaces_the_edits_without_an_undo_step(self) -> None:
        edit = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=NOTE, label="Water")
        self.module.restore_state(PlanEditsState(edits=(edit,)))
        self.assertEqual(self.module.steps()[0].label, "Water")
        self.assertEqual(self.reasons, ["restored"])
        self.assertFalse(undo_manager.can_undo)

    def test_an_edit_that_does_not_match_this_dataset_is_reported_and_not_applied(self) -> None:
        stale = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label="another note", label="Water")
        with self.assertLogs("lspr_imaging_app.plan.module", level="WARNING"):
            self.module.restore_state(PlanEditsState(edits=(stale,)))
        self.assertEqual(self.module.steps()[0].label, NOTE)
        self.assertEqual(len(self.module.warnings()), 1)

    def test_replacing_the_dataset_metadata_is_picked_up_and_old_edits_stop_matching(self) -> None:
        self.module.set_label(0, "Water")
        self.dataset.metadata = _metadata(ImagingPlanStep(step=1, label="Other", start_unix_ms=5_000, end_unix_ms=9_000))
        self.dataset.dataset_loaded.emit(object())
        self.assertEqual([s.label for s in self.module.steps()], ["Other"])
        self.assertEqual(len(self.module.warnings()), 1)
        self.assertEqual(self.reasons[-1], "dataset")

    def test_clearing_the_dataset_forgets_the_edits(self) -> None:
        self.module.set_label(0, "Water")
        self.dataset.dataset_cleared.emit()
        self.assertEqual(self.module.state(), PlanEditsState())
        self.assertEqual(self.reasons[-1], "dataset")


class SessionFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])
        self.state = PlanEditsState(edits=(
            PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=NOTE, end_unix_ms=42_000, label="Water"),
            PlanEdit(step=2, measured_start_unix_ms=30_000, measured_label="10perEG_-_ch1F", start_unix_ms=42_000, color="#336699"),
        ))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_edits_go_into_the_session_file_and_come_back(self) -> None:
        save_session(self.root, SessionState(plan_edits=self.state), self.naming)
        payload = json.loads(session_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], SESSION_SCHEMA_VERSION)
        self.assertEqual(SESSION_SCHEMA_VERSION, "1.5")
        self.assertEqual(len(payload["plan"]["edits"]), 2)
        self.assertEqual(load_session(self.root).plan_edits, self.state)

    def test_a_session_without_edits_writes_an_empty_block_and_saves_identical_bytes(self) -> None:
        first = save_session(self.root, SessionState(), self.naming).read_bytes()
        self.assertEqual(json.loads(first)["plan"], {"edits": []})
        self.assertEqual(save_session(self.root, SessionState(), self.naming).read_bytes(), first)

    def test_an_older_file_without_the_block_opens_with_no_edits(self) -> None:
        path = save_session(self.root, SessionState(plan_edits=self.state), self.naming)
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["plan"]
        payload["schema_version"] = "1.4"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(load_session(self.root).plan_edits, PlanEditsState())

    def test_a_damaged_block_does_not_stop_the_session_opening(self) -> None:
        path = save_session(self.root, SessionState(plan_edits=self.state), self.naming)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["plan"] = {"edits": ["junk", payload["plan"]["edits"][0]]}
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(load_session(self.root).plan_edits.edits, self.state.edits[:1])

    def test_the_module_state_survives_a_save_and_load(self) -> None:
        undo_manager.clear()
        module = ExperimentPlanModule(_FakeDataset(_two_steps()))
        module.set_label(0, "Water")
        module.move_edge(0, "end", 42_000, module.snap_grid_unix_ms(list(range(41))), together=True)
        save_session(self.root, SessionState(plan_edits=module.state()), self.naming)
        other = ExperimentPlanModule(_FakeDataset(_two_steps()))
        other.restore_state(load_session(self.root).plan_edits)
        self.assertEqual([(s.label, s.start_unix_ms, s.end_unix_ms) for s in other.steps()], [("Water", 10_000, 42_000), ("10perEG_-_ch1F", 42_000, 50_000)])
        undo_manager.clear()


if __name__ == "__main__":
    unittest.main()
