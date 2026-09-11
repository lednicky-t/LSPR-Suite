"""Regression test for a real data-corruption incident (2026-09-11): three
Stop-then-resume cycles during a bulk "Start analysis" run each left a
permanent, wrong dip in the sensorgram, confirmed by inspecting the user's
own measurement_backup.h5 - the same six (highest) wavelengths were NaN for
every one of the three affected cubes, across every selected ROI.

Root cause, in gui/analysis_tasks.py: _scoped_formula_spectrum_task reads a
cube's wavelengths in parallel (one task per wavelength, via a
ThreadPoolExecutor). If Stop is pressed while some of that SAME cube's
wavelength tasks haven't started yet, those come back as empty/NaN
placeholders while already-in-flight wavelengths finish normally - so the
task can return a PARTIAL, corrupted spectrum for whichever cube was
in-flight at the moment of cancellation, even though the call itself returns
normally (no exception, no cancelled flag on the return value).

_sensorgram_metric_task correctly detects this after the fact (the
cancellation check right after the call discards that cube's own point, so
nothing gets backed up to disk for it THIS run) - but it used to cache the
corrupted spectrum into spectral_cube_result_cache_store *before* that check
ran. A later resumed run (fresh, unset cancel_event) would then get a RAM
cache HIT for that exact cube, treat the corrupted spectrum as a normal
result, fit over the truncated wavelength range, and permanently bake the
wrong metric value into the backup this time.

Fixed by only calling spectral_cube_result_cache_store for a cube whose
compute was NOT interrupted by cancellation.
"""

from __future__ import annotations

import sys
import threading
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.analysis_tasks import _sensorgram_metric_task  # noqa: E402


class _FakeSpectrum:
    def __init__(self, spectral_cube_index: int) -> None:
        self.wavelengths_nm = [500.0, 550.0, 600.0]
        self.formula_values = [0.1, 0.2 + 0.01 * spectral_cube_index, 0.1]


class SensorgramStopMidCubeCachePoisoningTests(unittest.TestCase):
    def test_cube_interrupted_by_cancellation_is_never_cached(self) -> None:
        spectral_cubes = [0, 1, 2]
        cancel_event = threading.Event()
        cache_store_calls: list[int] = []

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, *, cancel_event=None, progress_callback=None, reduction_method=None, trimmed_mean_fraction=None, formula_key=None, compute_all_reduction_methods=None):
            if spectral_cube_index == 1:
                # Simulate Stop landing partway through THIS cube's own
                # internal per-wavelength read pool: some wavelengths
                # already read return real data, the rest come back as NaN
                # placeholders once they notice the now-set cancel_event -
                # but the task itself still returns a (corrupted) spectrum
                # normally, exactly like _scoped_formula_spectrum_task does.
                cancel_event.set()
            return _FakeSpectrum(spectral_cube_index)

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            cancel_event=cancel_event,
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            fit_method_key="none",
            spectral_cube_result_cache_store=lambda idx, spectrum: cache_store_calls.append(idx),
        )

        self.assertTrue(result.cancelled)
        # Cube 0 completed cleanly before cancellation - its point is kept.
        self.assertEqual(result.spectral_cube_indices.tolist(), [0])
        # Cube 1 is the one whose own compute triggered cancellation - it
        # must never reach the RAM cache, or a later resumed run would treat
        # its corrupted spectrum as a legitimate, complete result.
        self.assertEqual(cache_store_calls, [0])

    def test_uncancelled_run_caches_every_cube(self) -> None:
        # Sanity check against over-suppressing: a normal run with no
        # cancellation must still cache every cube it computes.
        spectral_cubes = [0, 1, 2]
        cache_store_calls: list[int] = []

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, *, cancel_event=None, progress_callback=None, reduction_method=None, trimmed_mean_fraction=None, formula_key=None, compute_all_reduction_methods=None):
            return _FakeSpectrum(spectral_cube_index)

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            cancel_event=threading.Event(),
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            fit_method_key="none",
            spectral_cube_result_cache_store=lambda idx, spectrum: cache_store_calls.append(idx),
        )

        self.assertFalse(result.cancelled)
        self.assertEqual(cache_store_calls, [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
