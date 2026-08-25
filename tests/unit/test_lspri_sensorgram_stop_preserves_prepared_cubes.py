"""Regression test for a bug reported against the LSPRi eva "Export Results"
feature: stopping a sensorgram calculation while it was still in its initial
parallel image-loading ("prep") phase discarded every cube that had already
finished loading, instead of fitting them - the sensorgram plot went blank
and the analyzed data never reached the per-point backup callback that HDF5
export reads from (see storage/measurement_export.py,
gui/analysis_controller.py's _backup_sensorgram_point), so exporting right
after a Stop produced an essentially empty file even though real work had
been done. Fixed in gui/analysis_tasks.py's _sensorgram_metric_task: a
cancel seen during prep now finishes fitting whatever was already loaded
instead of returning empty arrays.
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
from lspr_imaging_app.gui.worker import SensorgramPointResult  # noqa: E402


class _FakeSpectrum:
    def __init__(self, spectral_cube_index: int) -> None:
        # Distinct per cube so a real fit is happening, not a fixed stub.
        self.wavelengths_nm = [500.0, 550.0, 600.0]
        self.formula_values = [0.1, 0.2 + 0.01 * spectral_cube_index, 0.1]


class SensorgramStopDuringPrepTests(unittest.TestCase):
    def test_cancelling_during_prep_keeps_and_backs_up_already_loaded_cubes(self) -> None:
        spectral_cubes = list(range(12))
        cancel_event = threading.Event()
        lock = threading.Lock()
        loaded_count = 0

        def builder(spectral_cube_index: int):
            nonlocal loaded_count
            with lock:
                loaded_count += 1
                # Simulate the user hitting Stop partway through loading -
                # only some cubes have finished loading by this point.
                if loaded_count >= 3:
                    cancel_event.set()
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, *, cancel_event=None, progress_callback=None, reduction_method=None, trimmed_mean_fraction=None, formula_key=None):
            # A prep-cancelled batch must be fit with cancel_event=None, or
            # this would itself bail out to an empty/NaN result - that's
            # exactly the deeper bug this test guards against.
            self.assertIsNone(cancel_event)
            return _FakeSpectrum(spectral_cube_index)

        partial_points: list[SensorgramPointResult] = []

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            cancel_event=cancel_event,
            partial_callback=partial_points.append,
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            fit_method_key="none",
        )

        self.assertTrue(result.cancelled)
        self.assertGreater(result.spectral_cube_indices.size, 0, "already-loaded cubes must not be discarded")
        self.assertLess(result.spectral_cube_indices.size, len(spectral_cubes), "loading must actually have stopped early")
        self.assertEqual(result.metric_values.size, result.spectral_cube_indices.size)
        # Every surviving cube must have reached the per-point callback that
        # drives both the live plot and the HDF5 export backup.
        self.assertEqual(len(partial_points), result.spectral_cube_indices.size)
        self.assertTrue(all(point.metric_value is not None for point in partial_points))

    def test_uncancelled_run_is_unaffected(self) -> None:
        spectral_cubes = list(range(5))

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, *, cancel_event=None, progress_callback=None, reduction_method=None, trimmed_mean_fraction=None, formula_key=None):
            return _FakeSpectrum(spectral_cube_index)

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            cancel_event=threading.Event(),
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            fit_method_key="none",
        )

        self.assertFalse(result.cancelled)
        self.assertEqual(result.spectral_cube_indices.size, len(spectral_cubes))


if __name__ == "__main__":
    unittest.main()
