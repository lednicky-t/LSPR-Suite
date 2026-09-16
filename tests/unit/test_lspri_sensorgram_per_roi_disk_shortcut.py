"""Covers the per-ROI disk-backed shortcut added to gui/analysis_tasks.py's
_sensorgram_metric_task, extending the combined-selection-only shortcut
already covered by test_lspri_sensorgram_disk_metric_shortcut.py.

Motivation (docs/analysis_caching_architecture.md, Stage 3): the combined
shortcut (`metric_value_cache_get`) only ever finds a hit if the EXACT
selected-ROI combination was run together before, under the synthetic
"combined_<ids>" backup key. `_backup_per_roi_sensorgram_points` always
writes each real ROI's OWN row too, regardless of what combination it was
part of - `per_roi_metric_value_cache_get` checks THOSE, so a combination
never run together before (e.g. Stop, reselect a different ROI subset,
Start again) still skips pixel-read+fit entirely as long as every member ROI
individually already has a valid row.
"""

from __future__ import annotations

import math
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


class SensorgramPerRoiDiskShortcutTests(unittest.TestCase):
    def test_per_roi_hit_skips_read_and_fit_and_backs_every_member_roi(self) -> None:
        spectral_cubes = [0, 1]
        # Cube 1: every selected ROI (1, 2) already has its own valid row -
        # a combination that (per per_roi_disk_values below) was NEVER run
        # together as a whole, only individually.
        per_roi_disk_values = {1: {1: 0.5, 2: 0.9}}
        fit_task_calls: list[int] = []
        result_cache_get_calls: list[int] = []

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, *, cancel_event=None, progress_callback=None, reduction_method=None, trimmed_mean_fraction=None, formula_key=None, compute_all_reduction_methods=None):
            fit_task_calls.append(spectral_cube_index)
            return _FakeSpectrum(spectral_cube_index)

        def spectral_cube_result_cache_get(spectral_cube_index: int):
            result_cache_get_calls.append(spectral_cube_index)
            return None

        def per_roi_metric_value_cache_get(spectral_cube_index: int):
            return per_roi_disk_values.get(spectral_cube_index)

        partial_points: list[SensorgramPointResult] = []

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            partial_callback=partial_points.append,
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            spectral_cube_result_cache_get=spectral_cube_result_cache_get,
            per_roi_metric_value_cache_get=per_roi_metric_value_cache_get,
            fit_method_key="none",
        )

        # Cube 1 (the per-ROI hit) never reaches the RAM-cache lookup or fit.
        self.assertNotIn(1, result_cache_get_calls)
        self.assertNotIn(1, fit_task_calls)
        # Cube 0 (no per-ROI hit) still computes normally.
        self.assertIn(0, result_cache_get_calls)
        self.assertIn(0, fit_task_calls)

        # "Combined" preview value is the FIRST selected ROI's own value
        # (insertion order of the dict per_roi_metric_value_cache_get
        # returns) - never a pooled/averaged value across ROIs.
        values_by_cube = dict(zip(result.spectral_cube_indices, result.metric_values))
        self.assertAlmostEqual(values_by_cube[1], 0.5)

        # Both member ROIs' own values reach the partial callback, so
        # _backup_per_roi_sensorgram_points backs up (and RAM-caches) both -
        # not just the one used for the combined preview value.
        # NaN != NaN under `==`, so compare piecewise rather than via
        # assertEqual on the whole dict.
        cube_1_point = next(p for p in partial_points if p.spectral_cube_index == 1)
        self.assertEqual(set(cube_1_point.per_roi_metric_values), {1, 2})
        self.assertAlmostEqual(cube_1_point.per_roi_metric_values[1][0], 0.5)
        self.assertAlmostEqual(cube_1_point.per_roi_metric_values[2][0], 0.9)
        self.assertTrue(math.isnan(cube_1_point.per_roi_metric_values[1][1]))
        self.assertTrue(math.isnan(cube_1_point.per_roi_metric_values[2][1]))
        self.assertFalse(cube_1_point.freshly_computed)

    def test_per_roi_miss_falls_through_to_combined_shortcut(self) -> None:
        # Per-ROI check misses for cube 0 (only checked, not populated for
        # it), but the OLD combined-selection shortcut still has a hit -
        # must still be honored, not treated as an outright miss.
        spectral_cubes = [0]
        fit_task_calls: list[int] = []

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, **_kwargs):
            fit_task_calls.append(spectral_cube_index)
            return _FakeSpectrum(spectral_cube_index)

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            per_roi_metric_value_cache_get=lambda _cube: None,
            metric_value_cache_get=lambda _cube: 0.33,
            fit_method_key="none",
        )

        self.assertNotIn(0, fit_task_calls)
        self.assertAlmostEqual(float(result.metric_values[0]), 0.33)

    def test_per_roi_and_combined_both_miss_computes_normally(self) -> None:
        spectral_cubes = [0]
        fit_task_calls: list[int] = []

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, **_kwargs):
            fit_task_calls.append(spectral_cube_index)
            return _FakeSpectrum(spectral_cube_index)

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            per_roi_metric_value_cache_get=lambda _cube: None,
            metric_value_cache_get=lambda _cube: None,
            fit_method_key="none",
        )

        self.assertIn(0, fit_task_calls)
        self.assertEqual(result.spectral_cube_indices.size, 1)

    def test_omitted_parameter_behaves_exactly_as_before(self) -> None:
        # No per_roi_metric_value_cache_get passed at all - must not raise
        # and must behave identically to the pre-existing code path.
        spectral_cubes = [0]

        def builder(spectral_cube_index: int):
            return (spectral_cube_index,)

        def fake_fit_task(spectral_cube_index, **_kwargs):
            return _FakeSpectrum(spectral_cube_index)

        result = _sensorgram_metric_task(
            spectral_cubes,
            poly_order=1,
            metric_key="centroid",
            spectral_cube_payload_builder=builder,
            task_fn=fake_fit_task,
            fit_method_key="none",
        )
        self.assertEqual(result.spectral_cube_indices.size, 1)


if __name__ == "__main__":
    unittest.main()
