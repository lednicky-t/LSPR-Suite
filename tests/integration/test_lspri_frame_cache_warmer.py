"""`FrameCacheWarmer` (2026-11-28): the snapshot's frame tables are read in short steps on the GUI thread, so the out-of-date check does not freeze the window.

Pinned: a cold cache is filled in many steps and the snapshot after it reads nothing from the app; a change meanwhile (the cache is invalidated) makes the
fill start over, and the result is what a snapshot made without the cache gives; a source that cannot be read (no dataset) ends the wait, never hangs it, and
leaves the synchronous snapshot to report the problem; `ScanResultsModule._start_check` waits for the fill and then takes the snapshot (it does not read the
frames itself first). The cache itself: `tests/unit/test_lspri_scan_jobs_frame_cache.py`; its wiring to the app's signals: `test_lspri_frame_cache_wiring.py`.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

try:
    from lspr_imaging_app.analysis import frame_cache_warmer
    from lspr_imaging_app.analysis.frame_cache_warmer import FrameCacheWarmer
    from lspr_imaging_app.analysis.scan_jobs import FrameTableCache, ScanSource, snapshot_source
    from lspr_imaging_app.analysis.scan_results import ScanResultsModule
    from tests.unit.test_lspri_rewrite_scan_jobs import _plain
    from tests.unit.test_lspri_scan_jobs_frame_cache import _counting, _tables
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _pump(until, timeout_s: float = 10.0) -> None:
    end = time.monotonic() + timeout_s
    while not until() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    if not until():
        raise AssertionError("timed out")


class WarmerTests(unittest.TestCase):
    def setUp(self) -> None:
        # a budget of 0 means one cube per step: a small fake needs many steps, like the real dataset needs many 15 ms steps
        patcher = mock.patch.object(frame_cache_warmer, "SLICE_S", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = _plain(cubes=30)
        self.cache = FrameTableCache()

    def _source(self) -> ScanSource:
        return ScanSource(**{**self.app.source().__dict__, "frame_cache": self.cache})

    def test_a_cold_cache_is_filled_in_many_steps_and_the_snapshot_then_reads_nothing(self) -> None:
        warmer = FrameCacheWarmer(self.cache, self._source)
        self.assertFalse(warmer.is_filled_for(self._source()))
        done: list[int] = []
        warmer.warm(lambda: done.append(1))
        _pump(lambda: done)
        self.assertEqual(done, [1])
        self.assertGreater(warmer._steps, 5)
        self.assertTrue(warmer.is_filled_for(self._source()))
        calls: dict[str, int] = {}
        snapshot_source(_counting(self._source(), calls))
        self.assertEqual(calls, {})

    def test_a_change_during_the_fill_starts_it_over_and_nothing_old_is_kept(self) -> None:
        warmer = FrameCacheWarmer(self.cache, self._source)
        done: list[int] = []
        warmer.warm(lambda: done.append(1))
        _pump(lambda: warmer._steps >= 4)
        first_generation = self.cache.generation
        self.app.set_aside.add((2, 510.0))  # an edit that the app's signals would have followed with an invalidation
        self.cache.invalidate()
        self.assertEqual(self.cache.generation, first_generation + 1)
        _pump(lambda: done)
        self.assertEqual(_tables(snapshot_source(self._source()), self.app), _tables(snapshot_source(self.app.source()), self.app))
        self.assertFalse(snapshot_source(self._source()).is_spectral(2, 510.0))

    def test_an_unreadable_source_ends_the_wait_and_leaves_the_error_to_the_snapshot(self) -> None:
        def no_dataset() -> ScanSource:
            raise RuntimeError("No dataset is loaded.")

        warmer = FrameCacheWarmer(self.cache, no_dataset)
        done: list[int] = []
        warmer.warm(lambda: done.append(1))
        _pump(lambda: done)
        self.assertEqual(done, [1])
        self.assertTrue(warmer.is_filled_for(self._source()))  # "go ahead": the check's own snapshot raises and reports it, no endless waiting


class CheckWaitsForTheFillTests(unittest.TestCase):
    def test_the_check_starts_after_the_fill_and_its_snapshot_reads_nothing(self) -> None:
        with mock.patch.object(frame_cache_warmer, "SLICE_S", 0.0):
            app = _plain(cubes=20)
            cache = FrameTableCache()
            calls: dict[str, int] = {}

            def factory() -> ScanSource:
                return _counting(ScanSource(**{**app.source().__dict__, "frame_cache": cache}), calls)

            module = ScanResultsModule(factory, scan_running=lambda: False)
            module._stored = object()
            module._path = Path("scan.h5")
            module.set_frame_warmer(FrameCacheWarmer(cache, factory, parent=module))
            started: list[tuple] = []
            with mock.patch.object(module, "_start_thread", side_effect=lambda fn, name: started.append((name,))):
                module._start_check()
                self.assertFalse(module.is_checking())  # waiting for the fill, nothing snapshotted yet
                self.assertEqual(started, [])
                _pump(lambda: started)
            self.assertTrue(module.is_checking())
            self.assertEqual(started, [("scan-results-check",)])
            # the filling read the frames once each; the snapshot of the check itself read none
            self.assertEqual(calls["is_spectral"], 20 * 4)  # every frame of 20 cubes x (dark + 3 wavelengths), read once by the warm-up


if __name__ == "__main__":
    unittest.main()
