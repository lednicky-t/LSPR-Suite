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

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask.transient_detection import DetectionParams
    from lspr_imaging_app.image_tools.mask.transient_events import TransientEventsModule
    from lspr_imaging_app.image_tools.mask.transient_scan import ScanParams
    from lspr_imaging_app.image_tools.mask_scan_task import TASK_ID, ScanSummary, TransientScanTask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

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


if __name__ == "__main__":
    unittest.main()
