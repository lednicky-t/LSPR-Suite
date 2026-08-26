"""Covers the disk-backed "skip fitting entirely" cache-hit path added to
gui/analysis_tasks.py's _sensorgram_metric_task (analysis_pipeline_redesign.md
§4c item 3). Distinct from the existing spectral_cube_result_cache_get path,
which only ever skips the pixel-read/spectrum-build step and still runs the
fit: metric_value_cache_get supplies the already-reduced final scalar for one
exact (preprocessing/ROI/fit) signature, so a hit must skip BOTH the pixel
read and the fit for that spectral cube.
"""

from __future__ import annotations

import sys
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
        self.wavelengths_nm = [500.0, 550.0, 600.0]
        self.formula_values = [0.1, 0.2 + 0.01 * spectral_cube_index, 0.1]


class SensorgramDiskMetricShortcutTests(unittest.TestCase):
    def test_disk_hit_skips_read_and_fit_for_that_cube_only(self) -> None:
        spectral_cubes = [0, 1, 2, 3]
        # Cubes 1 and 3 already have a matching, up-to-date disk row.
        disk_values = {1: 0.42, 3: 0.77}
        fit_task_calls: list[int] = []
        result_cache_get_calls: list[int] = []

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, *, cancel_event=None, progress_callback=None, reduction_method=None, trimmed_mean_fraction=None, formula_key=None):
            fit_task_calls.append(spectral_cube_index)
            return _FakeSpectrum(spectral_cube_index)

        def spectral_cube_result_cache_get(spectral_cube_index: int):
            result_cache_get_calls.append(spectral_cube_index)
            return None

        def metric_value_cache_get(spectral_cube_index: int):
            return disk_values.get(spectral_cube_index)

        partial_points: list[SensorgramPointResult] = []

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            partial_callback=partial_points.append,
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            spectral_cube_result_cache_get=spectral_cube_result_cache_get,
            metric_value_cache_get=metric_value_cache_get,
            fit_method_key="none",
        )

        # Disk-hit cubes (1, 3) must never reach the RAM-cache lookup or the
        # fit task - that's the entire point of the shortcut.
        self.assertNotIn(1, result_cache_get_calls)
        self.assertNotIn(3, result_cache_get_calls)
        self.assertNotIn(1, fit_task_calls)
        self.assertNotIn(3, fit_task_calls)
        # Cubes without a disk hit (0, 2) must still be computed normally.
        self.assertIn(0, result_cache_get_calls)
        self.assertIn(2, result_cache_get_calls)
        self.assertIn(0, fit_task_calls)
        self.assertIn(2, fit_task_calls)

        self.assertEqual(list(result.spectral_cube_indices), [0, 1, 2, 3])
        values_by_cube = dict(zip(result.spectral_cube_indices, result.metric_values))
        self.assertAlmostEqual(values_by_cube[1], 0.42)
        self.assertAlmostEqual(values_by_cube[3], 0.77)

        # A disk-hit point still reaches the partial callback (so it still
        # drives the live plot and the per-point backup), with metric_signal
        # unavailable (not persisted on disk) rather than a stale/wrong value.
        disk_hit_points = {point.spectral_cube_index: point for point in partial_points if point.spectral_cube_index in (1, 3)}
        self.assertEqual(set(disk_hit_points), {1, 3})
        self.assertAlmostEqual(disk_hit_points[1].metric_value, 0.42)
        self.assertIsNone(disk_hit_points[1].metric_signal)

    def test_no_metric_value_cache_get_behaves_exactly_as_before(self) -> None:
        spectral_cubes = [0, 1]

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, *, cancel_event=None, progress_callback=None, reduction_method=None, trimmed_mean_fraction=None, formula_key=None):
            return _FakeSpectrum(spectral_cube_index)

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            fit_method_key="none",
        )

        self.assertEqual(result.spectral_cube_indices.size, len(spectral_cubes))
        self.assertFalse(result.cancelled)


if __name__ == "__main__":
    unittest.main()
