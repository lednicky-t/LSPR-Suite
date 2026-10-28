"""Performance mode: worker processes that stay ready between scans (`analysis/scan_pool.py`, `scan.py`, `ScanTask`; 2026-10-27).

What has to hold: a warm pool serves scan after scan on the **same processes**; its numbers equal a scan on private processes and an in-process
scan, also when the settings change between scans (a worker never reuses what it built for another scan); a worker that dies while idle is
replaced; the pool is closed by `shutdown` and its workers end by themselves when the application that started them is killed (Windows does not
stop child processes with their parent); `ScanTask` starts the pool off the GUI thread, defers a mode change made during a scan, and closes the pool
at quit. Real processes, two workers (a fixed count through the perf runtime), a tiny dataset. Design: docs/roi_nudge_partial_scan_proposal_2026-10-26.md
section 11.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import psutil
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core.perf import PerfPreferences, PerfRuntime, set_runtime
    from PyQt6 import QtWidgets

    from lspr_imaging_app.analysis import scan as scan_module
    from lspr_imaging_app.analysis.scan import scan_cubes
    from lspr_imaging_app.analysis.scan_jobs import ScanSource, build_scan_plan, snapshot_source
    from lspr_imaging_app.analysis.scan_pool import MODE_NORMAL, MODE_PERFORMANCE, WarmPool, uses_warm_pool
    from lspr_imaging_app.analysis.scan_task import ScanTask
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

H, W = 64, 96
CUBES = 4
WAVELENGTHS = (0.0, 500.0, 550.0)  # 0.0 is the dark frame
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _rois():
    return [AreaRoi(i, 16.0 + 24.0 * (k % 3), 16.0 + 22.0 * (k // 3), 8.0, reference_inner_diameter_px=12.0, reference_outer_diameter_px=18.0)
            for k, i in enumerate(range(1, 7))]


class _Dataset:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.paths = {}
        rng = np.random.default_rng(5)
        y, x = np.mgrid[0:H, 0:W]
        for cube in range(CUBES):
            for wl in WAVELENGTHS:
                frame = (2000.0 + 7.0 * x + 3.0 * y + 30.0 * cube + wl + rng.normal(0, 20.0, (H, W))).astype(np.float32)
                path = Path(self._tmp.name) / f"c{cube}_w{int(wl)}.tif"
                tifffile.imwrite(path, frame)
                self.paths[(cube, wl)] = str(path)
        self.rois = _rois()
        self.background = BackgroundSettings()
        self.geometry = GeometrySettings()

    def source(self) -> ScanSource:
        return ScanSource(
            cube_indices=lambda: tuple(range(CUBES)), wavelengths_for_cube=lambda c: WAVELENGTHS, is_spectral=lambda c, w: w != 0.0,
            record_path=lambda c, w: self.paths[(c, w)], rois=lambda: self.rois, rois_at=lambda c: self.rois, has_timeline=lambda: False,
            chromatic_affine=lambda c, w: IDENTITY, chromatic_affine_between=lambda a, b: IDENTITY, resolve_mask=lambda c, w: None,
            geometry=lambda: self.geometry, background=lambda: self.background, detection=lambda: AreaRoiDetectionSettings(),
            default_reference_diameters=lambda: (12.0, 18.0),
        )

    def plan(self):
        return build_scan_plan(snapshot_source(self.source()))

    def close(self) -> None:
        self._tmp.cleanup()


def _same(a, b) -> bool:
    return (
        all(np.array_equal(a.values[m], b.values[m], equal_nan=True) for m in a.values)
        and all(np.array_equal(getattr(a, n), getattr(b, n), equal_nan=True) for n in ("std", "n_valid", "n_nominal", "n_masked"))
    )


def _wait_until(condition, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def _gone(pids) -> bool:
    for pid in pids:
        try:
            if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                return False
        except psutil.NoSuchProcess:
            continue  # it ended between the two calls: that is what the test waits for (a flake under load until 2026-10-28)
    return True


class WarmPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Dataset()
        cls.previous_runtime = set_runtime(PerfRuntime(PerfPreferences(mode="manual", manual_workers=2)))

    @classmethod
    def tearDownClass(cls) -> None:
        set_runtime(cls.previous_runtime)
        cls.data.close()

    def setUp(self) -> None:
        self.pool = WarmPool(watch_interval=0.5)
        self.addCleanup(self.pool.shutdown)

    def _scan(self, plan=None, *, warm=None, processes=None):
        plan = plan or self.data.plan()
        results = scan_cubes(plan.jobs, plan.settings, processes=processes, cubes_per_task=1, warm=warm)
        return sorted(results, key=lambda r: r.cube_pos)

    def _reference(self, plan=None):
        plan = plan or self.data.plan()
        return sorted(scan_cubes(plan.jobs, plan.settings, processes=1), key=lambda r: r.cube_pos)

    def test_mode_names_and_what_they_mean(self) -> None:
        self.assertTrue(uses_warm_pool(MODE_PERFORMANCE))
        self.assertFalse(uses_warm_pool(MODE_NORMAL))
        self.assertFalse(uses_warm_pool("anything else"))

    def test_start_makes_all_the_workers_and_shutdown_ends_them(self) -> None:
        pids = self.pool.start(2, True)
        self.assertEqual(len(pids), 2)
        self.assertTrue(self.pool.is_ready())
        self.assertEqual(self.pool.size, 2)
        self.assertTrue(all(psutil.pid_exists(p) for p in pids))
        self.pool.shutdown()
        self.assertTrue(_wait_until(lambda: _gone(pids), 20), "the workers were still running after shutdown")
        self.assertFalse(self.pool.is_ready())
        self.pool.shutdown()  # twice is fine
        with self.assertRaises(RuntimeError):
            self.pool.acquire(2, False)

    def test_scan_after_scan_runs_on_the_same_processes_and_gives_the_same_numbers(self) -> None:
        pids = self.pool.start(2, True)
        reference = self._reference()
        first, second = self._scan(warm=self.pool), self._scan(warm=self.pool)
        for a, b, c in zip(first, second, reference):
            self.assertTrue(_same(a, c))
            self.assertTrue(_same(b, c))
        self.assertEqual(self.pool.worker_pids(), pids)  # nothing was started or replaced
        private = self._scan()  # a pool made for the scan and closed at its end
        for a, c in zip(private, reference):
            self.assertTrue(_same(a, c))

    def test_the_next_scan_has_its_own_settings_not_the_caches_of_the_last(self) -> None:
        self.pool.start(2, True)
        plain = self.data.plan()
        self.data.background = BackgroundSettings(flatten_background_enabled=True, flatten_background_sigma_px=10.0, flatten_background_binning=2)
        try:
            flattened = self.data.plan()
        finally:
            self.data.background = BackgroundSettings()
        self.assertNotEqual(plain.settings, flattened.settings)
        for plan in (plain, flattened, plain):  # back and forth on the same workers
            for a, c in zip(self._scan(plan, warm=self.pool), self._reference(plan)):
                self.assertTrue(_same(a, c))

    def test_trim_drops_the_caches_in_a_worker_and_the_next_scan_still_works(self) -> None:
        plan = self.data.plan()
        scan_module._run_in_process(900, plan.settings, plan.jobs[:1])  # in this process: makes the module's worker
        self.assertEqual(scan_module._WORKER_RUN, 900)
        scan_module._run_in_process(900, plan.settings, plan.jobs[:1])
        same_worker = scan_module._WORKER
        scan_module._run_in_process(901, plan.settings, plan.jobs[:1])  # another run: a fresh worker, the old one is dropped
        self.assertIsNot(scan_module._WORKER, same_worker)
        scan_module._trim_process()
        self.assertIsNone(scan_module._WORKER)
        self.pool.start(2, True)
        self.pool.trim()
        for a, c in zip(self._scan(plan, warm=self.pool), self._reference(plan)):
            self.assertTrue(_same(a, c))

    def test_a_worker_that_died_while_idle_is_replaced_by_the_next_scan(self) -> None:
        pids = sorted(self.pool.start(2, True))
        psutil.Process(pids[0]).kill()
        self.assertTrue(_wait_until(lambda: not self.pool.is_ready(), 30), "the pool did not notice the dead worker")
        for a, c in zip(self._scan(warm=self.pool), self._reference()):
            self.assertTrue(_same(a, c))
        self.assertTrue(self.pool.is_ready())
        self.assertNotIn(pids[0], self.pool.worker_pids())

    def test_a_bigger_request_replaces_the_pool_and_a_smaller_one_is_served_by_it(self) -> None:
        two = self.pool.acquire(2, False)
        self.assertIs(self.pool.acquire(2, False), two)
        self.assertIs(self.pool.acquire(1, False), two)  # two workers serve a scan that needs one
        three = self.pool.acquire(3, False)
        self.assertIsNot(three, two)
        self.assertIsNot(self.pool.acquire(3, True), three)  # another priority (Preferences) is another pool
        self.assertEqual(self.pool.size, 3)

    def test_workers_end_by_themselves_when_the_application_that_started_them_is_killed(self) -> None:
        script = Path(tempfile.mkdtemp()) / "start_pool.py"
        script.write_text(textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(APP_SRC)!r})
            from lspr_imaging_app.analysis.scan_pool import WarmPool

            if __name__ == "__main__":
                pool = WarmPool(watch_interval=0.5)
                pids = pool.start(2, False)
                print("PIDS " + ",".join(str(p) for p in sorted(pids)), flush=True)
                time.sleep(300)
        """), encoding="utf-8")
        child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        workers: list[int] = []
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not workers:
            line = child.stdout.readline()
            if line.startswith("PIDS "):
                workers = [int(p) for p in line.split()[1].split(",")]
            elif not line and child.poll() is not None:
                break
        self.assertEqual(len(workers), 2, "the helper process did not report its workers")
        self.assertTrue(all(psutil.pid_exists(p) for p in workers))
        psutil.Process(child.pid).kill()  # a hard kill: no atexit, no shutdown, no chance to close the pool
        self.assertTrue(_wait_until(lambda: _gone(workers), 30), "the workers of a killed application were still running")


def _pump(until, seconds: float = 60.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.01)
    _APP.processEvents()
    return until()


class PerformanceModeTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _Dataset()
        cls.previous_runtime = set_runtime(PerfRuntime(PerfPreferences(mode="manual", manual_workers=2)))

    @classmethod
    def tearDownClass(cls) -> None:
        set_runtime(cls.previous_runtime)
        cls.data.close()

    def setUp(self) -> None:
        self.task = ScanTask()
        self.states: list[tuple[str, int]] = []
        self.finished: list[tuple] = []
        self.completed: list = []
        self.task.pool_state_changed.connect(lambda state, workers: self.states.append((state, workers)))
        self.task.task_finished.connect(lambda *args: self.finished.append(args))
        self.task.completed.connect(self.completed.append)
        self.addCleanup(self.task.shutdown)

    def _run(self, **kwargs) -> None:
        self.finished.clear()
        self.completed.clear()
        self.task.start(self.data.source(), processes=2, **kwargs)
        self.assertTrue(_pump(lambda: bool(self.finished)))

    def test_performance_mode_starts_the_workers_on_its_own_thread_and_a_scan_uses_them(self) -> None:
        self.assertEqual(self.task.mode(), MODE_NORMAL)
        self.task.set_mode(MODE_PERFORMANCE)
        self.assertTrue(_pump(lambda: ("ready", 2) in self.states), f"states: {self.states}")
        self.assertEqual([s for s, _ in self.states][:2], ["starting", "ready"])
        warm = self.task._warm
        pids = warm.worker_pids()
        self._run()
        self.assertEqual(self.finished[0][1], "completed")
        self.assertTrue(self.completed[0].warm_pool)
        self.assertGreater(self.completed[0].first_cube_seconds, 0.0)
        self.assertIn("performance mode", self.finished[0][2])
        self.assertEqual(warm.worker_pids(), pids)  # the scan ran on the processes that were already there
        self.task.set_mode(MODE_NORMAL)
        self.assertEqual(self.states[-1], ("off", 0))
        self.assertTrue(_wait_until(lambda: _gone(pids), 30))
        self._run()
        self.assertFalse(self.completed[0].warm_pool)

    def test_a_mode_chosen_during_a_scan_is_applied_when_it_has_ended(self) -> None:
        self.task.start(self.data.source(), processes=2)
        self.task.set_mode(MODE_PERFORMANCE, start_pool=False)  # while the scan runs
        self.assertEqual(self.task.mode(), MODE_NORMAL)
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertTrue(_pump(lambda: self.task.mode() == MODE_PERFORMANCE))
        self.assertFalse(self.completed[0].warm_pool)  # that scan started its own workers

    def test_a_remembered_mode_waits_for_start_pool_and_start_pool_is_idempotent(self) -> None:
        self.task.set_mode(MODE_PERFORMANCE, start_pool=False)
        _pump(lambda: bool(self.states), 0.5)
        self.assertEqual(self.states, [])
        self.assertFalse(self.task._warm.is_ready())
        self.task.start_pool()
        self.task.start_pool()  # a second call while the first is going does nothing
        self.assertTrue(_pump(lambda: ("ready", 2) in self.states))
        self.task.start_pool()  # already running: nothing
        _pump(lambda: False, 0.3)
        self.assertEqual([s for s, _ in self.states].count("starting"), 1)

    def test_a_pool_that_cannot_start_is_reported_and_scans_still_run(self) -> None:
        with mock.patch.object(WarmPool, "start", side_effect=RuntimeError("no processes")):
            self.task.set_mode(MODE_PERFORMANCE)
            self.assertTrue(_pump(lambda: any(s == "failed" for s, _ in self.states)))
        self._run()
        self.assertEqual(self.finished[0][1], "completed")

    def test_shutdown_closes_the_pool_at_quit(self) -> None:
        self.task.set_mode(MODE_PERFORMANCE)
        self.assertTrue(_pump(lambda: ("ready", 2) in self.states))
        pids = self.task._warm.worker_pids()
        self.task.shutdown()
        self.assertTrue(_wait_until(lambda: _gone(pids), 30))

    def test_the_numbers_of_a_performance_mode_scan_equal_a_normal_one(self) -> None:
        plan = self.data.plan()
        normal = sorted(scan_cubes(plan.jobs, plan.settings, processes=1), key=lambda r: r.cube_pos)
        self.task.set_mode(MODE_PERFORMANCE)
        self.assertTrue(_pump(lambda: ("ready", 2) in self.states))
        arrays = {}
        task = ScanTask(on_result=lambda p, a, s: arrays.setdefault("a", a))
        self.addCleanup(task.shutdown)
        task._warm, task._mode = self.task._warm, MODE_PERFORMANCE  # the same ready pool
        done = []
        task.task_finished.connect(lambda *args: done.append(args))
        task.start(self.data.source(), processes=2)
        self.assertTrue(_pump(lambda: bool(done)))
        self.assertEqual(done[0][1], "completed")
        for cube_pos, result in enumerate(normal):
            self.assertTrue(np.array_equal(arrays["a"].values["mean"][cube_pos][:, [0, 1], :], result.values["mean"], equal_nan=True))


if __name__ == "__main__":
    unittest.main()
