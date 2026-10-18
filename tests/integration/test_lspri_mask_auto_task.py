"""`MaskAutoTask`: the scan's particle pointers become masks, as ONE undoable step, made on a worker thread (2026-10-17).

A real `QApplication` and the real `MaskModule`; no widgets. What is worth pinning: each frame's mask is the contour grown by N pixels
merged onto what is in effect (a persistent mask, a frame's own individual mask, a mask warped by a chromatic matrix), nothing is
written until the end, Cancel, a failing lookup, a failing worker, a mask changed meanwhile and a changed dataset all change nothing,
the pixel work is off the GUI thread, and the whole batch undoes in one step.
Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md; audit (A1, A2, B1):
apps/LSPRi/eva/docs/transient_scan_automask_slider_audit_2026-10-17.md.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools import ChromaticModule
    from lspr_imaging_app.image_tools import mask_auto_task as task_module
    from lspr_imaging_app.image_tools.chromatic.warp import warp_boolean_mask_affine
    from lspr_imaging_app.image_tools.mask.module import MaskModule
    from lspr_imaging_app.image_tools.mask.transient_detection import KIND_GLOBAL, KIND_PARTICLE, ContourMask, TransientEvent
    from lspr_imaging_app.image_tools.mask.transient_events_state import EventRecord
    from lspr_imaging_app.image_tools.mask.transient_masks import BaseMask
    from lspr_imaging_app.image_tools.mask_auto_task import TASK_ID, AutoMaskSummary, MaskAutoTask
    from lspr_imaging_app.panels.mask_edit_common import plan_mask_edit_base
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

SHAPE = (160, 400)
_REAL_FRAME_MASK = task_module.frame_mask


def _disk(radius: int = 5) -> np.ndarray:
    y, x = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    return (x**2 + y**2) <= radius**2


def _record(event_id: int, frame, x0: int = 60, y0: int = 70, kind: str = KIND_PARTICLE, dismissed: bool = False) -> EventRecord:
    mask = _disk()
    event = TransientEvent(
        frame=frame, kind=kind, area_px=int(mask.sum()), centroid_xy=(x0 + 5.0, y0 + 5.0), bbox_xyxy=(x0, y0, x0 + 11, y0 + 11),
        depth=0.8, z=100.0, contour=ContourMask(x0=x0, y0=y0, mask=mask) if kind == KIND_PARTICLE else None,
    )
    return EventRecord(id=event_id, event=event, dismissed=dismissed)


def _pump(until, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)


class _Held:
    """A `frame_mask` that waits at its first call until the test lets it go, so the test can act while the worker is mid-run."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.reached = threading.Event()
        self.threads: set[int] = set()

    def __call__(self, *args, **kwargs):
        self.threads.add(threading.get_ident())
        self.reached.set()
        self.gate.wait(10.0)
        return _REAL_FRAME_MASK(*args, **kwargs)


class MaskAutoTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.mask = MaskModule()
        self.task = MaskAutoTask(self.mask)
        self.progress: list[float] = []
        self.finished: list[tuple[str, str]] = []
        self.completed: list[AutoMaskSummary] = []
        self.failed: list[str] = []
        self.cancelled: list[bool] = []
        self.running: list[bool] = []
        self.task.task_progress.connect(lambda _id, _label, fraction, _msg: self.progress.append(fraction))
        self.task.task_finished.connect(lambda _id, outcome, text: self.finished.append((outcome, text)))
        self.task.completed.connect(self.completed.append)
        self.task.failed.connect(self.failed.append)
        self.task.cancelled.connect(lambda: self.cancelled.append(True))
        self.task.running_changed.connect(self.running.append)
        self.persistent = np.zeros(SHAPE, dtype=bool)
        self.persistent[100:120, 300:340] = True
        self.mask.set_mask_change((0, 500.0), "persistent", self.persistent)
        undo_manager.clear()

    def tearDown(self) -> None:
        self.task.shutdown()
        undo_manager.clear()

    def base(self, frame):
        """What the panel hands the task: the mask in effect at `frame`, still compressed (no chromatic model: identity)."""
        return plan_mask_edit_base(self.mask, ChromaticModule(), frame)

    def run_task(self, records, dilate_px=3, resolve=None) -> None:
        self.task.start(records, SHAPE, dilate_px, resolve or self.base)
        _pump(lambda: bool(self.finished or self.failed))
        _APP.processEvents()

    def records(self):
        return [_record(i + 1, (3, 500.0 + 10 * i), x0=60 + 100 * i) for i in range(3)]

    def test_every_frame_gets_its_contour_grown_and_merged_onto_the_persistent_mask(self) -> None:
        self.run_task(self.records())
        self.assertEqual(self.failed, [])
        self.assertEqual(self.finished[0][0], "completed")
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0, 510.0, 520.0)})
        for i, frame in enumerate([(3, 500.0), (3, 510.0), (3, 520.0)]):
            authored, plane, scope = self.mask.resolve_mask_source(frame)
            self.assertEqual((authored, scope), (frame, "individual"))
            self.assertTrue(np.all(plane[self.persistent]), "the persistent mask is merged in")
            x0 = 60 + 100 * i
            self.assertTrue(plane[75, x0 + 5], "the particle is masked")
            self.assertTrue(plane[75, x0 - 2], "and a margin of 3 px around it")
            self.assertFalse(plane[75, x0 - 6])
            expected = int(self.persistent.sum()) + int(ContourMask(x0=x0, y0=70, mask=_disk()).to_array(SHAPE, 3).sum())
            self.assertEqual(int(plane.sum()), expected)
        np.testing.assert_array_equal(self.mask.resolve_mask_source((0, 500.0))[1], self.persistent)  # the persistent mask is untouched
        summary = self.completed[0]
        self.assertEqual((summary.frames_masked, summary.frames_unchanged, summary.events_used, summary.dilate_px), (3, 0, 3, 3))
        self.assertEqual(self.running, [True, False])
        self.assertFalse(self.task.is_running())

    def test_the_whole_batch_is_one_undo_step_and_one_redo(self) -> None:
        self.run_task(self.records())
        self.assertIn("Auto mask (3 frames)", undo_manager.undo_label)
        undo_manager.undo()
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertFalse(undo_manager.can_undo)
        undo_manager.redo()
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0, 510.0, 520.0)})

    def test_a_frames_own_individual_mask_is_merged_into_not_replaced(self) -> None:
        mine = np.zeros(SHAPE, dtype=bool)
        mine[10:20, 10:20] = True
        self.mask.set_mask_change((3, 500.0), "individual", mine)
        self.run_task(self.records())
        plane = self.mask.resolve_mask_source((3, 500.0))[1]
        self.assertTrue(np.all(plane[mine]))
        self.assertTrue(plane[75, 65])
        self.assertFalse(plane[110, 310])  # the persistent mask is not in an individual one that did not have it: it replaces it, as before

    def test_running_twice_changes_nothing_the_second_time(self) -> None:
        self.run_task(self.records())
        self.finished.clear()
        undo_manager.clear()
        self.run_task(self.records())
        self.assertEqual(self.completed[-1].frames_masked, 0)
        self.assertEqual(self.completed[-1].frames_unchanged, 3)
        self.assertFalse(undo_manager.can_undo)

    def test_only_standing_particles_are_masked(self) -> None:
        records = [
            _record(1, (3, 500.0)),
            _record(2, (3, 510.0), dismissed=True),
            _record(3, (3, 520.0), kind=KIND_GLOBAL),
        ]
        self.run_task(records)
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0,)})
        self.assertEqual(self.completed[0].events_used, 1)

    def test_nothing_to_mask_is_said_in_a_sentence(self) -> None:
        self.task.start([_record(1, (3, 500.0), dismissed=True)], SHAPE, 3, self.base)
        _APP.processEvents()
        self.assertIn("no particle pointers to mask", self.failed[0])
        self.assertEqual(self.finished, [])
        self.assertFalse(self.task.is_running())

    def test_cancel_changes_nothing(self) -> None:
        held = _Held()
        with mock.patch.object(task_module, "frame_mask", side_effect=held):
            self.task.start(self.records(), SHAPE, 3, self.base)
            _pump(held.reached.is_set, 5.0)
            self.task.cancel()
            held.gate.set()
            _pump(lambda: bool(self.finished))
            _APP.processEvents()
        self.assertEqual([f[0] for f in self.finished], ["cancelled"])
        self.assertEqual(self.cancelled, [True])
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertFalse(undo_manager.can_undo)

    def test_a_failing_lookup_stops_the_run_with_the_frame_named_and_changes_nothing(self) -> None:
        def broken(frame):
            if frame == (3, 510.0):
                raise ValueError("no chromatic model")
            return self.base(frame)

        with self.assertLogs("lspr_imaging_app.image_tools.mask_auto_task", level="ERROR"):
            self.run_task(self.records(), resolve=broken)
        self.assertEqual(self.finished[0][0], "failed")
        self.assertIn("cube 3, 510 nm", self.failed[0])
        self.assertIn("no chromatic model", self.failed[0])
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertFalse(self.task.is_running())
        self.assertEqual(self.running, [True, False])

    def test_a_failing_worker_stops_the_run_with_the_frame_named_and_changes_nothing(self) -> None:
        with mock.patch.object(task_module, "materialize_base", side_effect=ValueError("bad warp")):
            with self.assertLogs("lspr_imaging_app.image_tools.mask_auto_task", level="ERROR"):
                self.run_task(self.records())
        self.assertEqual(self.finished[0][0], "failed")
        self.assertIn("cube 3, 500 nm", self.failed[0])
        self.assertIn("bad warp", self.failed[0])
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertFalse(undo_manager.can_undo)

    def test_the_pixel_work_runs_off_the_gui_thread_with_rising_progress(self) -> None:
        threads: set[int] = set()

        def spying(*args, **kwargs):
            threads.add(threading.get_ident())
            return _REAL_FRAME_MASK(*args, **kwargs)

        records = [_record(i + 1, (i // 3, 500.0 + 10 * (i % 3)), x0=20 + (i % 30) * 10) for i in range(150)]
        with mock.patch.object(task_module, "frame_mask", side_effect=spying):
            self.task.start(records, (900, 1300), 3, lambda _frame: None)  # real plane size
            _pump(lambda: bool(self.finished or self.failed))
            _APP.processEvents()
        self.assertEqual(self.completed[0].frames_masked, 150)
        self.assertTrue(threads)
        self.assertNotIn(threading.get_ident(), threads)
        self.assertGreater(len(self.progress), 1)
        self.assertEqual(self.progress, sorted(self.progress))
        self.assertAlmostEqual(self.progress[-1], 1.0, delta=1e-9)

    def test_a_mask_warped_by_a_chromatic_matrix_is_what_the_contour_is_merged_onto(self) -> None:
        change = self.mask.resolve_mask_change((0, 500.0))
        matrix = np.array([[1.0, 0.0, 12.0], [0.0, 1.0, -7.0]])  # the mask moves 12 px right and 7 px up in the target frame
        self.run_task(self.records()[:1], resolve=lambda frame: BaseMask(change, matrix))
        plane = self.mask.resolve_mask_source((3, 500.0))[1]
        warped = warp_boolean_mask_affine(self.persistent, matrix)
        contour = ContourMask(x0=60, y0=70, mask=_disk()).to_array(SHAPE, 3)
        self.assertTrue(warped.any() and not np.array_equal(warped, self.persistent))
        np.testing.assert_array_equal(plane, warped | contour)

    def test_a_mask_changed_while_it_runs_is_not_overwritten(self) -> None:
        """Audit A2: a stroke painted during the run must not be replaced by a mask made from the old state."""
        held = _Held()
        mine = np.zeros(SHAPE, dtype=bool)
        mine[10:20, 10:20] = True
        with mock.patch.object(task_module, "frame_mask", side_effect=held):
            self.task.start(self.records(), SHAPE, 3, self.base)
            _pump(held.reached.is_set, 5.0)
            self.mask.set_mask_change((3, 500.0), "individual", mine)  # the user paints on a frame that is being auto-masked
            held.gate.set()
            _pump(lambda: bool(self.finished or self.failed))
            _APP.processEvents()
        self.assertEqual([f[0] for f in self.finished], ["failed"])
        self.assertIn("mask was changed while it ran", self.failed[0])
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0,)})  # only the user's own
        np.testing.assert_array_equal(self.mask.resolve_mask_source((3, 500.0))[1], mine)

    def test_a_dataset_change_drops_the_result(self) -> None:
        """Audit A1: the frames the run was making masks for may not exist any more."""
        held = _Held()
        with mock.patch.object(task_module, "frame_mask", side_effect=held):
            self.task.start(self.records(), SHAPE, 3, self.base)
            _pump(held.reached.is_set, 5.0)
            self.task.invalidate()
            held.gate.set()
            _pump(lambda: bool(self.finished))
            _APP.processEvents()
        self.assertEqual([f[0] for f in self.finished], ["cancelled"])
        self.assertEqual(self.cancelled, [True])
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertFalse(undo_manager.can_undo)
        self.assertFalse(self.task.is_running())

    def test_a_run_after_a_stopped_one_works_again(self) -> None:
        self.task.invalidate()  # nothing is running: harmless, and does not poison the next run
        self.run_task(self.records())
        self.assertEqual(self.finished[0][0], "completed")
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0, 510.0, 520.0)})

    def test_the_commit_of_one_run_does_not_make_the_next_one_refuse(self) -> None:
        self.run_task(self.records())
        self.finished.clear()
        self.failed.clear()
        self.run_task(self.records())  # the first run's own commit emitted mask_changed; that must not count as "changed meanwhile"
        self.assertEqual(self.failed, [])
        self.assertEqual(self.finished[0][0], "completed")

    def test_a_second_start_while_running_is_refused(self) -> None:
        held = _Held()
        with mock.patch.object(task_module, "frame_mask", side_effect=held):
            self.task.start(self.records(), SHAPE, 3, self.base)
            try:
                with self.assertRaises(RuntimeError):
                    self.task.start(self.records(), SHAPE, 3, self.base)
            finally:
                held.gate.set()
                _pump(lambda: bool(self.finished))

    def test_the_task_id_is_the_one_the_status_bar_knows(self) -> None:
        seen: list[str] = []
        self.task.task_progress.connect(lambda task_id, *_rest: seen.append(task_id))
        self.run_task(self.records())
        self.assertEqual(set(seen), {TASK_ID})


if __name__ == "__main__":
    unittest.main()
