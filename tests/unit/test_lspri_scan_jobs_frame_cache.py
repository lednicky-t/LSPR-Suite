"""`FrameTableCache` (2026-11-28): `snapshot_source` keeps the per-frame tables between snapshots (`analysis/scan_jobs.py`).

Pinned: a snapshot with the cache holds exactly the tables a snapshot without it holds; a second snapshot reads no frame from the app again; after
`invalidate()` an edit (affine, mask, frame role, path) is seen; a subset of cubes then the whole set reads each cube's tables once; the arrays that are
handed out are read-only. The wiring of the invalidation to the app's signals is pinned in `tests/integration/test_lspri_frame_cache_wiring.py`.
Pure logic, no Qt, no files.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan_jobs import FrameTableCache, MaskRef, ScanSource, snapshot_source
    from tests.unit.test_lspri_rewrite_scan_jobs import _Fake, _plain
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

SHIFTED = np.array([[1.002, 0.0, 0.5], [0.0, 0.998, -0.5]])


def _counting(source: ScanSource, counter: dict[str, int]) -> ScanSource:
    """The same source, counting every per-frame read."""

    def wrap(name: str, fn):
        def counted(*args):
            counter[name] = counter.get(name, 0) + 1
            return fn(*args)

        return counted

    return ScanSource(**{
        **source.__dict__,
        **{name: wrap(name, getattr(source, name)) for name in (
            "wavelengths_for_cube", "is_spectral", "record_path", "chromatic_affine", "chromatic_affine_between", "resolve_mask",
        )},
    })


def _tables(snap: ScanSource, fake: _Fake) -> dict:
    """Everything a snapshot answers for every frame of the fake, as plain comparable values."""
    out: dict = {}
    for cube in snap.all_cube_indices():
        out[("wavelengths", cube)] = tuple(snap.wavelengths_for_cube(cube))
        for wl in snap.wavelengths_for_cube(cube):
            out[("spectral", cube, wl)] = snap.is_spectral(cube, wl)
            if cube in tuple(snap.cube_indices()) and snap.is_spectral(cube, wl):
                out[("path", cube, wl)] = snap.record_path(cube, wl)
                out[("affine", cube, wl)] = snap.chromatic_affine(cube, wl).tolist()
                ref = snap.resolve_mask(cube, wl)
                out[("mask", cube, wl)] = None if ref is None else (ref.authored_frame, ref.scope)
                if ref is not None and ref.authored_frame != (cube, wl):
                    out[("warp", cube, wl)] = snap.chromatic_affine_between(ref.authored_frame, (cube, wl)).tolist()
    return out


def _app() -> _Fake:
    app = _plain(cubes=4)
    app.set_aside.add((2, 510.0))
    app.affines[(1, 500.0)] = SHIFTED
    authored = np.ones((40, 60), dtype=bool)
    app.masks[(0, 500.0)] = ((0, 500.0), authored, "persistent")
    for cube in (1, 2, 3):
        app.masks[(cube, 500.0)] = ((0, 500.0), authored, "persistent")
    return app


class FrameTableCacheTests(unittest.TestCase):
    def test_a_cached_snapshot_holds_the_same_tables_as_one_without_the_cache(self) -> None:
        app = _app()
        plain = snapshot_source(app.source())
        cache = FrameTableCache()
        with_cache = snapshot_source(ScanSource(**{**app.source().__dict__, "frame_cache": cache}))
        again = snapshot_source(ScanSource(**{**app.source().__dict__, "frame_cache": cache}))  # served from the cache
        self.assertEqual(_tables(plain, app), _tables(with_cache, app))
        self.assertEqual(_tables(plain, app), _tables(again, app))

    def test_a_second_snapshot_reads_no_frame_from_the_app(self) -> None:
        app = _app()
        cache = FrameTableCache()
        calls: dict[str, int] = {}
        source = _counting(ScanSource(**{**app.source().__dict__, "frame_cache": cache}), calls)
        snapshot_source(source)
        first = dict(calls)
        self.assertGreater(first["is_spectral"], 0)
        self.assertGreater(first["chromatic_affine"], 0)
        calls.clear()
        snapshot_source(source)
        self.assertEqual(calls, {})  # nothing per frame: wavelengths, spectral flags, paths, affines, masks and warps all came from the cache

    def test_after_invalidate_an_edit_is_seen(self) -> None:
        app = _app()
        cache = FrameTableCache()

        def snap() -> ScanSource:
            return snapshot_source(ScanSource(**{**app.source().__dict__, "frame_cache": cache}))

        before = snap()
        self.assertTrue(np.array_equal(before.chromatic_affine(1, 500.0), SHIFTED))
        app.affines[(1, 500.0)] = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])  # a chromatic edit
        app.set_aside.add((3, 520.0))  # a frame role edit
        del app.masks[(3, 500.0)]  # a mask edit
        stale = snap()  # no invalidate: the cache still answers as before (that is what the app's signals are for)
        self.assertTrue(np.array_equal(stale.chromatic_affine(1, 500.0), SHIFTED))
        cache.invalidate()
        fresh = snap()
        self.assertTrue(np.array_equal(fresh.chromatic_affine(1, 500.0), np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])))
        self.assertFalse(fresh.is_spectral(3, 520.0))
        self.assertIsNone(fresh.resolve_mask(3, 500.0))
        self.assertEqual(_tables(fresh, app), _tables(snapshot_source(app.source()), app))  # and equals a snapshot made without the cache

    def test_a_subset_then_the_whole_set_reads_each_cube_once(self) -> None:
        app = _app()
        cache = FrameTableCache()
        calls: dict[str, int] = {}
        source = _counting(ScanSource(**{**app.source().__dict__, "frame_cache": cache}), calls)
        snapshot_source(source, cubes=[1, 2])
        spectral_after_subset = calls["is_spectral"]
        affines_after_subset = calls["chromatic_affine"]
        snapshot_source(source)  # every cube: the detail of cubes 0 and 3 is read now, the rest comes from the cache
        self.assertEqual(calls["is_spectral"], spectral_after_subset)  # spectral flags were read for every cube the first time
        frames_of_a_cube = 3  # 500, 510, 520 (the dark frame is not spectral); cube 2 has one set aside
        self.assertEqual(calls["chromatic_affine"], affines_after_subset + 2 * frames_of_a_cube)
        calls.clear()
        snapshot_source(source)
        self.assertEqual(calls, {})

    def test_the_arrays_handed_out_are_read_only(self) -> None:
        app = _app()
        snap = snapshot_source(ScanSource(**{**app.source().__dict__, "frame_cache": FrameTableCache()}))
        with self.assertRaises(ValueError):
            snap.chromatic_affine(1, 500.0)[0, 0] = 5.0
        ref = snap.resolve_mask(1, 500.0)
        self.assertIsInstance(ref, MaskRef)
        with self.assertRaises(ValueError):
            snap.chromatic_affine_between(ref.authored_frame, (1, 500.0))[0, 0] = 5.0

    def test_invalidate_slot_ignores_signal_arguments_and_counts_restart(self) -> None:
        cache = FrameTableCache()
        snapshot_source(ScanSource(**{**_app().source().__dict__, "frame_cache": cache}))
        self.assertGreater(len(cache), 0)
        self.assertGreater(cache.misses, 0)
        cache.invalidate("a signal argument", 3)
        self.assertEqual((len(cache), cache.hits, cache.misses), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
