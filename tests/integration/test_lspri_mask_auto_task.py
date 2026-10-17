"""`MaskAutoTask`: the scan's particle pointers become masks, as ONE undoable step, in small slices from the event loop (2026-10-17).

A real `QApplication` and the real `MaskModule`; no widgets. What is worth pinning: each frame's mask is the contour grown by N pixels
merged onto what is in effect (a persistent mask, a frame's own individual mask), nothing is written until the end, Cancel and a
failing base change nothing, and the whole batch undoes in one step.
Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md.
"""

from __future__ import annotations

import sys
import time
import unittest

import numpy as np
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask.module import MaskModule
    from lspr_imaging_app.image_tools.mask.transient_detection import KIND_GLOBAL, KIND_PARTICLE, ContourMask, TransientEvent
    from lspr_imaging_app.image_tools.mask.transient_events_state import EventRecord
    from lspr_imaging_app.image_tools.mask_auto_task import TASK_ID, AutoMaskSummary, MaskAutoTask
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

SHAPE = (160, 400)


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
        undo_manager.clear()

    def base(self, frame):
        resolution = self.mask.resolve_mask_source(frame)
        return np.zeros(SHAPE, dtype=bool) if resolution is None else resolution[1]

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
        self.task.start(self.records(), SHAPE, 3, self.base)
        self.task.cancel()
        _pump(lambda: bool(self.finished))
        self.assertEqual([f[0] for f in self.finished], ["cancelled"])
        self.assertEqual(self.cancelled, [True])
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertFalse(undo_manager.can_undo)

    def test_a_failing_base_stops_the_run_with_the_frame_named_and_changes_nothing(self) -> None:
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

    def test_many_frames_are_done_in_slices_with_rising_progress(self) -> None:
        records = [_record(i + 1, (i // 3, 500.0 + 10 * (i % 3)), x0=20 + (i % 30) * 10) for i in range(150)]
        self.task.start(records, (900, 1300), 3, lambda _frame: None)  # real plane size: ~5 ms a frame, so several slices
        _pump(lambda: bool(self.finished or self.failed))
        _APP.processEvents()
        self.assertEqual(self.completed[0].frames_masked, 150)
        self.assertGreater(len(self.progress), 1)
        self.assertEqual(self.progress, sorted(self.progress))
        self.assertAlmostEqual(self.progress[-1], 1.0, delta=1e-9)

    def test_a_second_start_while_running_is_refused(self) -> None:
        self.task.start(self.records(), SHAPE, 3, self.base)
        try:
            with self.assertRaises(RuntimeError):
                self.task.start(self.records(), SHAPE, 3, self.base)
        finally:
            _pump(lambda: bool(self.finished))

    def test_the_task_id_is_the_one_the_status_bar_knows(self) -> None:
        seen: list[str] = []
        self.task.task_progress.connect(lambda task_id, *_rest: seen.append(task_id))
        self.run_task(self.records())
        self.assertEqual(set(seen), {TASK_ID})


if __name__ == "__main__":
    unittest.main()
