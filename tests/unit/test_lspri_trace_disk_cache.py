"""Pure-logic tests for the disk copy of the fitted Sensorgram traces (`analysis/trace_disk_cache.py`, 2026-11-28). No Qt; files only in a temp folder.

The rule they pin: a stored trace is used only when the setup and the ROI's own input arrays are byte-identical; everything else is fitted again, and
the numbers that come back are the same as without the cache. A scan is identified by `scan_trace.scan_fingerprint` (one hash of every array a trace is read from)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import trace_disk_cache as cache
    from lspr_imaging_app.analysis.scan_store import StoredScan
    from lspr_imaging_app.analysis.scan_trace import scan_fingerprint, trace_input
    from lspr_imaging_app.analysis.settings import LEGACY_METRIC_DEFAULTS
    from lspr_imaging_app.analysis.trace_fit import TraceInput, traces_from_inputs
    from lspr_imaging_app.analysis.trace_store import TraceStore
    from PyQt6.QtCore import QObject, pyqtSignal
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)
METRIC = LEGACY_METRIC_DEFAULTS
FORMULA = "absorbance"


SCAN = "scan-A"


def make_input(seed: int, n_cube: int = 12, scan: str | None = SCAN) -> TraceInput:
    rng = np.random.default_rng(seed)
    centre = 560.0 + 3.0 * np.arange(n_cube)
    band = 0.25 * np.exp(-0.5 * ((WL[None, :] - centre[:, None]) / 35.0) ** 2)
    reference = 1000.0 + rng.normal(0.0, 1.0, (n_cube, WL.size))
    sample = reference * 10.0 ** (-band) + rng.normal(0.0, 0.5, (n_cube, WL.size))
    return TraceInput(WL.copy(), sample, reference, np.ones(sample.shape, dtype=bool), np.ones(n_cube, dtype=bool), scan_digest=scan)


class FakePool:
    """Stands in for `TracePool.run`: the real fit (`traces_from_inputs`), and a record of which ROIs it was asked for."""

    def __init__(self) -> None:
        self.asked: list[list[int]] = []

    def run(self, jobs, formula_key, metric, cancelled=None, progress=None):
        self.asked.append([r for r, _ in jobs])
        if cancelled is not None and cancelled():
            return {}, True
        traces = traces_from_inputs([d for _, d in jobs], formula_key, metric)
        if progress is not None:
            progress(len(jobs), len(jobs))
        return {r: t for (r, _), t in zip(jobs, traces)}, False


def make_scan(seed: int = 0, n_cube: int = 6, n_roi: int = 3, n_wl: int = 5) -> StoredScan:
    rng = np.random.default_rng(seed)
    shape = (n_cube, n_roi, n_wl, 2)
    return StoredScan(
        path=Path("scan.h5"), attrs={}, cube_indices=tuple(range(n_cube)), roi_ids=tuple(range(10, 10 + n_roi)),
        wavelength_nm=tuple(float(w) for w in np.linspace(500.0, 600.0, n_wl)), acquired_at_unix_ms=np.zeros(n_cube, dtype=np.int64),
        values={"mean": rng.normal(1000.0, 5.0, shape)}, std=np.zeros(shape), n_valid=np.full(shape, 9, dtype=np.int32),
        n_nominal=np.full(shape, 9, dtype=np.int32), n_masked=np.zeros(shape, dtype=np.int32), cell_hash=np.ones((n_cube, n_roi), dtype=np.uint64),
        recipe_id=np.zeros((n_cube, n_roi), dtype=np.int32), layout_id=np.zeros((n_cube, n_roi), dtype=np.int32),
    )


class ScanFingerprintTests(unittest.TestCase):
    def test_equal_scans_have_equal_fingerprints_and_it_is_kept_on_the_object(self) -> None:
        a, b = make_scan(1), make_scan(1)
        self.assertEqual(scan_fingerprint(a), scan_fingerprint(b))
        self.assertIs(scan_fingerprint(a), scan_fingerprint(a))  # the same string object: not hashed again
        self.assertNotEqual(scan_fingerprint(a), scan_fingerprint(make_scan(2)))

    def test_every_array_a_trace_is_read_from_changes_it(self) -> None:
        base = scan_fingerprint(make_scan(3))
        for change in ("mean", "n_valid", "cell_hash", "roi_ids", "cube_indices"):
            scan = make_scan(3)
            if change == "mean":
                scan.values["mean"][2, 1, 3, 0] += 1e-9  # the smallest edit still counts
            elif change == "n_valid":
                scan.n_valid[0, 0, 0, 0] -= 1
            elif change == "cell_hash":
                scan.cell_hash[1, 1] = 0  # a cube never scanned
            elif change == "roi_ids":
                scan.roi_ids = (10, 11, 99)
            else:
                scan.cube_indices = (0, 1, 2, 3, 4, 7)
            with self.subTest(change):
                self.assertNotEqual(base, scan_fingerprint(scan))

    def test_a_spectral_correction_changes_it(self) -> None:
        class Corrected(StoredScan):  # the shifted wavelength axis of a spectral correction
            def abscissa_nm(self):
                return super().abscissa_nm() + 0.5

        plain, shifted = make_scan(4), make_scan(4)
        shifted = Corrected(**{f: getattr(shifted, f) for f in shifted.__dataclass_fields__})
        self.assertNotEqual(scan_fingerprint(plain), scan_fingerprint(shifted))

    def test_trace_input_carries_it_only_when_asked(self) -> None:
        scan = make_scan(5)
        self.assertIsNone(trace_input(scan, 11).scan_digest)
        self.assertEqual(trace_input(scan, 11, fingerprint=True).scan_digest, scan_fingerprint(scan))


class SetupKeyTests(unittest.TestCase):
    def test_setup_key_follows_the_fit_settings_only(self) -> None:
        base = cache.setup_key(FORMULA, METRIC)
        self.assertEqual(base, cache.setup_key(FORMULA, replace(METRIC)))
        self.assertNotEqual(base, cache.setup_key("transmittance", METRIC))
        self.assertNotEqual(base, cache.setup_key(FORMULA, replace(METRIC, poly_order=METRIC.poly_order + 1)))
        self.assertNotEqual(base, cache.setup_key(FORMULA, replace(METRIC, fit_method="gaussian")))
        self.assertNotEqual(base, cache.setup_key(FORMULA, replace(METRIC, metric_key="maximum" if METRIC.metric_key != "maximum" else "centroid")))
        self.assertNotEqual(base, cache.setup_key(FORMULA, replace(METRIC, fit_wl_min=500.0)))
        self.assertEqual(base, cache.setup_key(FORMULA, replace(METRIC, response_base_last=99)))  # the response shape does not go through this cache

    def test_fit_modules_exist(self) -> None:
        self.assertEqual(len(cache.fit_code_fingerprint()), 2 * 16)


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / cache.FILE_NAME
        self.pool = FakePool()
        self.disk = cache.TraceDiskCache()
        self.disk.set_path(self.path)
        self.jobs = [(10 + i, make_input(i)) for i in range(4)]

    def run_cache(self, jobs=None, metric=METRIC, **kw):
        out = self.disk.run(self.pool.run, self.jobs if jobs is None else jobs, FORMULA, metric, **kw)
        self.disk.flush()
        return out

    def test_second_run_asks_the_pool_for_nothing_and_gives_the_same_numbers(self) -> None:
        first, cancelled = self.run_cache()
        self.assertFalse(cancelled)
        self.assertTrue(self.path.is_file())
        second, _ = self.run_cache()
        self.assertEqual(self.pool.asked, [[10, 11, 12, 13]])  # the second run never reached the pool
        self.assertEqual(set(first), set(second))
        for roi in first:
            np.testing.assert_array_equal(first[roi], second[roi])  # bit for bit: the file holds float64

    def test_another_scan_is_fitted_again_and_replaces_the_file(self) -> None:
        self.run_cache()
        rescanned = [(r, make_input(i + 50, scan="scan-B")) for i, (r, _) in enumerate(self.jobs)]
        out, _ = self.run_cache(rescanned)
        self.assertEqual(self.pool.asked[-1], [10, 11, 12, 13])
        expected = traces_from_inputs([make_input(52)], FORMULA, METRIC)[0]
        np.testing.assert_array_equal(out[12], expected)  # the numbers of the new scan, not the old file's
        again, _ = self.run_cache(rescanned)
        self.assertEqual(len(self.pool.asked), 2)  # the file now holds scan B
        np.testing.assert_array_equal(again[12], expected)
        self.run_cache()  # scan A again: the file holds one scan only
        self.assertEqual(len(self.pool.asked), 3)

    def test_another_setup_is_fitted_again_and_replaces_the_file(self) -> None:
        self.run_cache()
        other = replace(METRIC, poly_order=METRIC.poly_order + 1)
        self.run_cache(metric=other)
        self.assertEqual(self.pool.asked[-1], [10, 11, 12, 13])
        self.run_cache(metric=other)
        self.assertEqual(len(self.pool.asked), 2)  # the new setup is in the file now
        self.run_cache()  # back to the first: the file holds one setup only
        self.assertEqual(len(self.pool.asked), 3)

    def test_other_rois_of_the_same_setup_stay_in_the_file(self) -> None:
        self.run_cache()
        self.run_cache(self.jobs[:2])  # a smaller selection: all hits, nothing written
        self.run_cache([(50, make_input(50))])  # a new ROI is added to the file
        self.run_cache()  # the first four are still there
        self.assertEqual(self.pool.asked, [[10, 11, 12, 13], [50]])
        stored = cache.read_entries(self.path, cache.setup_key(FORMULA, METRIC), SCAN)
        self.assertEqual(sorted(stored), [10, 11, 12, 13, 50])
        self.assertEqual(cache.read_entries(self.path, cache.setup_key(FORMULA, METRIC), "scan-B"), {})

    def test_a_different_cube_count_is_not_used(self) -> None:
        self.run_cache()
        longer = [(r, make_input(i, n_cube=15)) for i, (r, _) in enumerate(self.jobs)]
        out, _ = self.run_cache(longer)
        self.assertEqual(self.pool.asked[-1], [10, 11, 12, 13])
        self.assertEqual(len(out[10]), 15)

    def test_a_damaged_file_is_ignored_and_replaced(self) -> None:
        self.path.write_bytes(b"this is not an npz file")
        with self.assertLogs("lspr_imaging_app.analysis.trace_disk_cache", level="WARNING"):
            out, _ = self.run_cache()
        self.assertEqual(len(out), 4)
        self.assertEqual(self.pool.asked, [[10, 11, 12, 13]])
        self.run_cache()
        self.assertEqual(len(self.pool.asked), 1)  # the replaced file works

    def test_no_path_means_no_cache(self) -> None:
        self.disk.set_path(None)
        self.run_cache()
        self.run_cache()
        self.assertEqual(len(self.pool.asked), 2)
        self.assertFalse(self.path.exists())

    def test_progress_counts_cached_rois_too(self) -> None:
        self.run_cache()
        seen: list[tuple[int, int]] = []
        self.run_cache([*self.jobs, (77, make_input(77))], progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen[0], (4, 5))  # four from the file at once
        self.assertEqual(seen[-1], (5, 5))

    def test_jobs_without_a_scan_fingerprint_are_never_cached(self) -> None:
        loose = [(r, make_input(i, scan=None)) for i, (r, _) in enumerate(self.jobs)]
        self.run_cache(loose)
        self.run_cache(loose)
        self.assertEqual(len(self.pool.asked), 2)
        self.assertFalse(self.path.exists())

    def test_a_cancelled_run_writes_nothing_and_says_so(self) -> None:
        out, cancelled = self.disk.run(self.pool.run, self.jobs, FORMULA, METRIC, cancelled=lambda: True)
        self.disk.flush()
        self.assertTrue(cancelled)
        self.assertEqual(out, {})
        self.assertFalse(self.path.exists())


class _FakeScan(QObject):
    results_changed = pyqtSignal()

    def __init__(self, path) -> None:
        super().__init__()
        self._path = path

    def path(self):
        return self._path


class TraceStoreWiringTests(unittest.TestCase):
    """The store points the disk copy at the session's scan file and hands the Sensorgram a runner that goes through it."""

    def test_the_file_is_put_next_to_the_scan_and_follows_a_new_scan(self) -> None:
        scan = _FakeScan(Path("C:/session/scan.h5"))
        store = TraceStore(scan)  # the helper process is made lazily: nothing starts here
        self.addCleanup(store.shutdown)
        self.assertEqual(store.disk_cache().path(), Path("C:/session/") / cache.FILE_NAME)
        scan._path = Path("C:/other/scan.h5")
        scan.results_changed.emit()
        self.assertEqual(store.disk_cache().path(), Path("C:/other/") / cache.FILE_NAME)
        scan._path = None
        scan.results_changed.emit()
        self.assertIsNone(store.disk_cache().path())

    def test_the_runner_goes_through_the_disk_copy(self) -> None:
        store = TraceStore(_FakeScan(Path("C:/session/scan.h5")))
        self.addCleanup(store.shutdown)
        runner = store.runner()
        self.assertIs(runner.func.__self__, store.disk_cache())
        self.assertEqual(runner.args, (store.pool().run,))

    def test_fits_in_the_calling_thread_have_no_disk_copy(self) -> None:
        store = TraceStore(_FakeScan(Path("C:/session/scan.h5")), in_process=False)
        self.assertIsNone(store.disk_cache())
        self.assertIsNone(store.runner())


if __name__ == "__main__":
    unittest.main()
