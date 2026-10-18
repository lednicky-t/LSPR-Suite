"""`TransientScanTask`: the scan on a worker thread, with progress, Cancel and its result in `TransientEventsModule` (2026-10-17).

A real `QApplication` (the result comes back from the thread through a queued signal) but no widgets and no real dataset: the frames
are synthetic, a dark blob crossing the field in one cube. Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask.transient_detection import DetectionParams
    from lspr_imaging_app.image_tools.mask.transient_events import TransientEventsModule
    from lspr_imaging_app.image_tools.mask.transient_events_state import TransientEventsState
    from lspr_imaging_app.image_tools.mask.transient_scan import ScanParams, scan_transients
    from lspr_imaging_app.image_tools.mask_scan_task import TASK_ID, ScanSummary, TransientScanTask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

HEIGHT, WIDTH = 160, 400
CUBES = 6
WAVELENGTHS = [0.0] + [470.0 + 10 * i for i in range(7)]
BLOB_X = {2: 40.0, 3: 120.0, 4: 200.0, 5: 280.0}  # wavelength index -> x, in cube 3


def _load(cube: int, wavelength: float) -> np.ndarray:
    index = int(round((wavelength - 470.0) / 10)) + 1 if wavelength > 0 else 0
    rng = np.random.default_rng(cube * 100 + index)
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    frame = np.full((HEIGHT, WIDTH), 30000.0, dtype=np.float32) * (1.0 + 0.1 * index) * (1.0 + 0.002 * cube)
    frame[((x % 40) - 20) ** 2 + ((y % 40) - 20) ** 2 < 64] *= 0.5
    frame *= 1.0 + 0.004 * rng.standard_normal(frame.shape).astype(np.float32)
    if cube == 3 and index in BLOB_X:
        frame[(x - BLOB_X[index]) ** 2 + (y - 80.0) ** 2 <= 36] *= 0.2
    return frame


def _pump(until, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


class TransientScanTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events = TransientEventsModule()
        self.task = TransientScanTask(self.events)
        self.index = {c: list(WAVELENGTHS) for c in range(CUBES)}
        self.params = ScanParams(detection=DetectionParams(bin_factor=2, max_particle_area_px=2000), stride=1, workers=2)
        self.progress: list[tuple[str, str, float, str]] = []
        self.finished: list[tuple[str, str, str]] = []
        self.completed: list[ScanSummary] = []
        self.failed: list[str] = []
        self.cancelled: list[bool] = []
        self.running: list[bool] = []
        self.task.task_progress.connect(lambda *args: self.progress.append(args))
        self.task.task_finished.connect(lambda *args: self.finished.append(args))
        self.task.completed.connect(self.completed.append)
        self.task.failed.connect(self.failed.append)
        self.task.cancelled.connect(lambda: self.cancelled.append(True))
        self.task.running_changed.connect(self.running.append)

    def tearDown(self) -> None:
        self.task.shutdown()

    def _run(self, load=_load) -> None:
        self.task.start(self.index, load, self.params)
        _pump(lambda: bool(self.finished or self.failed))
        _APP.processEvents()

    def test_a_scan_runs_off_the_gui_thread_and_installs_its_events(self) -> None:
        threads: set[int] = set()

        def load(cube: int, wavelength: float) -> np.ndarray:
            threads.add(threading.get_ident())
            return _load(cube, wavelength)

        self._run(load)
        self.assertNotIn(threading.get_ident(), threads)
        self.assertEqual(self.failed, [])
        self.assertEqual([f[:2] for f in self.finished], [(TASK_ID, "completed")])
        self.assertEqual({r.event.frame[1] for r in self.events.records()}, {480.0, 490.0, 500.0, 510.0})
        summary = self.completed[0]
        self.assertEqual((summary.particles, summary.passages, summary.unreadable_frames), (4, 1, 0))
        self.assertIn("4 particles in 1 passages", self.finished[0][2])
        self.assertEqual(self.running, [True, False])
        self.assertFalse(self.task.is_running())

    def test_progress_is_reported_from_zero_to_one_with_the_task_id(self) -> None:
        self._run()
        self.assertTrue(self.progress)
        self.assertTrue(all(p[0] == TASK_ID for p in self.progress))
        fractions = [p[2] for p in self.progress]
        self.assertEqual(fractions, sorted(fractions))
        self.assertAlmostEqual(fractions[-1], 1.0, delta=1e-6)

    def test_cancel_stops_the_scan_and_leaves_the_list_alone(self) -> None:
        started = threading.Event()

        def slow(cube: int, wavelength: float) -> np.ndarray:
            started.set()
            time.sleep(0.02)
            return _load(cube, wavelength)

        self.task.start(self.index, slow, self.params)
        _pump(started.is_set, 5.0)
        self.task.cancel()
        _pump(lambda: bool(self.finished))
        _APP.processEvents()
        self.assertEqual([f[1] for f in self.finished], ["cancelled"])
        self.assertEqual(self.cancelled, [True])
        self.assertFalse(self.events.has_events())

    def test_an_unreadable_frame_is_counted_in_the_result(self) -> None:
        def flaky(cube: int, wavelength: float) -> np.ndarray:
            if (cube, wavelength) == (3, 500.0):
                raise OSError("no")
            return _load(cube, wavelength)

        self._run(flaky)
        self.assertEqual(self.completed[0].unreadable_frames, 1)
        self.assertIn("1 frames could not be read", self.finished[0][2])

    def test_a_crash_is_reported_not_swallowed(self) -> None:
        with mock.patch("lspr_imaging_app.image_tools.mask_scan_task.scan_transients", side_effect=RuntimeError("boom")):
            with self.assertLogs("lspr_imaging_app.image_tools.mask_scan_task", level="ERROR"):
                self._run()
        self.assertEqual([f[1] for f in self.finished], ["failed"])
        self.assertEqual(self.failed, ["Unexpected error: boom"])
        self.assertFalse(self.task.is_running())
        self.assertFalse(self.events.has_events())

    def test_an_empty_dataset_fails_with_a_sentence(self) -> None:
        self.task.start({}, _load, self.params)
        _APP.processEvents()
        self.assertEqual(self.failed, ["There is no dataset to scan."])
        self.assertFalse(self.task.is_running())

    def test_a_second_start_while_running_is_refused(self) -> None:
        gate = threading.Event()

        def blocked(cube: int, wavelength: float) -> np.ndarray:
            gate.wait(5.0)
            return _load(cube, wavelength)

        self.task.start(self.index, blocked, self.params)
        try:
            with self.assertRaises(RuntimeError):
                self.task.start(self.index, _load, self.params)
        finally:
            gate.set()
            self.task.cancel()
            _pump(lambda: bool(self.finished))

    # -- the audit fixes (2026-10-17): apps/LSPRi/eva/docs/transient_scan_automask_slider_audit_2026-10-17.md ------------------

    def _slow_loader(self):
        started, gate = threading.Event(), threading.Event()

        def load(cube: int, wavelength: float) -> np.ndarray:
            started.set()
            gate.wait(10.0)
            return _load(cube, wavelength)

        return load, started, gate

    def test_a_dataset_change_mid_scan_stops_it_and_its_result_is_not_installed(self) -> None:
        """A1: the worker reads through the dataset module as it is at each read, so a result after a dataset change is a mixture."""
        load, started, gate = self._slow_loader()
        self.task.start(self.index, load, self.params)
        _pump(started.is_set, 5.0)
        self.task.invalidate()  # what the Image panel does when the dataset is loaded or cleared
        gate.set()
        _pump(lambda: bool(self.finished))
        _APP.processEvents()
        self.assertEqual([f[1] for f in self.finished], ["cancelled"])
        self.assertIn("dataset or the session changed", self.finished[0][2])
        self.assertEqual(self.cancelled, [True])
        self.assertFalse(self.events.has_events())
        self.assertFalse(self.task.is_running())

    def test_a_result_that_arrives_after_the_change_is_dropped_even_when_the_scan_ran_to_the_end(self) -> None:
        """The scan may finish before it notices it was stopped; the generation number, not the cancel flag, decides."""
        complete = scan_transients(self.index, _load, self.params)
        gate, started = threading.Event(), threading.Event()

        def slow_but_unstoppable(*_args, **_kwargs):
            started.set()
            gate.wait(10.0)
            return complete

        with mock.patch("lspr_imaging_app.image_tools.mask_scan_task.scan_transients", side_effect=slow_but_unstoppable):
            self.task.start(self.index, _load, self.params)
            _pump(started.is_set, 5.0)
            self.task.invalidate()
            gate.set()
            _pump(lambda: bool(self.finished))
            _APP.processEvents()
        self.assertEqual([f[1] for f in self.finished], ["cancelled"])
        self.assertFalse(self.events.has_events())
        self.assertEqual(self.completed, [])

    def test_restoring_a_session_stops_a_running_scan(self) -> None:
        load, started, gate = self._slow_loader()
        self.task.start(self.index, load, self.params)
        _pump(started.is_set, 5.0)
        self.events.restore_state(TransientEventsState())  # a session switch / dataset load restores every module
        gate.set()
        _pump(lambda: bool(self.finished))
        _APP.processEvents()
        self.assertEqual([f[1] for f in self.finished], ["cancelled"])
        self.assertFalse(self.events.has_events())

    def test_a_scan_started_after_a_change_works_normally(self) -> None:
        self.task.invalidate()
        self.events.restore_state(TransientEventsState())
        self._run()
        self.assertEqual([f[1] for f in self.finished], ["completed"])
        self.assertTrue(self.events.has_events())

    def test_one_cube_cannot_be_scanned_and_the_sentence_says_why(self) -> None:
        """A3: with nothing to compare with the scan would end in a clean-looking '0 particles'."""
        self.task.start({3: list(WAVELENGTHS)}, _load, self.params)
        _APP.processEvents()
        self.assertEqual(len(self.failed), 1)
        self.assertIn("at least two cubes", self.failed[0])
        self.assertIn("the dataset has 1", self.failed[0])
        self.assertFalse(self.task.is_running())
        self.assertEqual(self.running, [])

    def test_a_range_of_one_cube_is_refused_too(self) -> None:
        params = ScanParams(detection=self.params.detection, stride=1, workers=2, cube_range=(2, 2))
        self.task.start(self.index, _load, params)
        _APP.processEvents()
        self.assertIn("the chosen range holds 1", self.failed[0])

    def test_a_dark_frame_only_cube_does_not_count_as_a_cube(self) -> None:
        self.task.start({0: list(WAVELENGTHS), 1: [0.0]}, _load, self.params)
        _APP.processEvents()
        self.assertIn("at least two cubes", self.failed[0])

    def test_the_done_message_says_how_many_frames_were_really_compared(self) -> None:
        self._run()
        summary = self.completed[0]
        self.assertEqual((summary.frames_screened, summary.frames_in_range), (CUBES * 7, CUBES * 7))  # stride 1: every frame
        self.assertIn(f"{CUBES * 7} of {CUBES * 7} frames compared", self.finished[0][2])

    def test_both_thread_pools_get_the_worker_init(self) -> None:
        """B2: the reading pool and the screening pool both start their threads through `worker_init` (priority, decoder)."""
        import lspr_imaging_app.image_tools.mask.transient_scan as scan_module

        real, initializers = scan_module.ThreadPoolExecutor, []

        def spy(*args, **kwargs):
            initializers.append(kwargs.get("initializer"))
            return real(*args, **kwargs)

        def init() -> None:
            pass

        with mock.patch.object(scan_module, "ThreadPoolExecutor", side_effect=spy):
            self.task.start(self.index, _load, self.params, worker_init=init)
            _pump(lambda: bool(self.finished or self.failed))
            _APP.processEvents()
        self.assertEqual(initializers, [init, init])


if __name__ == "__main__":
    unittest.main()
