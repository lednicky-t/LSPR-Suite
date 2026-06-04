from __future__ import annotations

import unittest
from types import SimpleNamespace
import sys

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.plot_view_cache import (
    PlotViewCache,
    build_active_trace_series_token,
    build_heatmap_arrays,
    build_heatmap_history_token,
    build_metric_series_token,
    extract_series_arrays,
    derive_heatmap_levels_from_matrix,
    expand_heatmap_levels,
    quantize_view_target_points,
    sample_absolute_heatmap_rows_for_view,
    sample_absolute_metric_series_for_view,
    select_heatmap_rows_for_view,
)
from lspr_app.gui.trace_history_buffer import AppendOnlyMetricHistoryBuffer


class PlotViewCacheTests(unittest.TestCase):
    def test_cached_active_trace_series_reuses_previous_result(self) -> None:
        cache = PlotViewCache()
        calls = {"count": 0}

        def builder() -> dict[str, tuple[np.ndarray, np.ndarray]]:
            calls["count"] += 1
            return {"smoothed_max": (np.asarray([0.0, 1.0]), np.asarray([2.0, 3.0]))}

        token = ("absolute", (("smoothed_max", 1, 1, 2),))
        first = cache.cached_active_trace_series(token, builder)
        second = cache.cached_active_trace_series(token, builder)

        self.assertIs(first, second)
        self.assertEqual(calls["count"], 1)

    def test_metric_view_cache_reuses_recent_view(self) -> None:
        cache = PlotViewCache()
        x = np.linspace(0.0, 10.0, num=1000)
        y = np.sin(x)
        token = ("metric", 1, 1000)

        first = cache.metric_view(token, x, y, view_width_px=200.0)
        second = cache.metric_view(token, x, y, view_width_px=202.0)

        self.assertIs(first[0], second[0])
        self.assertIs(first[1], second[1])
        self.assertLessEqual(len(first[0]), len(x))

    def test_heatmap_arrays_and_view_are_cached(self) -> None:
        cache = PlotViewCache()
        history = [
            (0.0, np.asarray([1.0, 2.0, 3.0], dtype=np.float64)),
            (1.0, np.asarray([4.0, 5.0, 6.0], dtype=np.float64)),
            (2.0, np.asarray([7.0, 8.0, 9.0], dtype=np.float64)),
        ]
        token = ("heatmap", 1, 3)
        times_a, matrix_a = cache.heatmap_arrays(token, lambda: build_heatmap_arrays(history))
        times_b, matrix_b = cache.heatmap_arrays(token, lambda: build_heatmap_arrays(history))

        self.assertIs(times_a, times_b)
        self.assertIs(matrix_a, matrix_b)

        view_a = cache.heatmap_view(token, times_a, matrix_a, max_rows=300, view_height_px=100.0)
        view_b = cache.heatmap_view(token, times_a, matrix_a, max_rows=300, view_height_px=102.0)
        self.assertIs(view_a[0], view_b[0])
        self.assertIs(view_a[1], view_b[1])

    def test_heatmap_arrays_from_history_appends_incrementally(self) -> None:
        cache = PlotViewCache()
        history = [
            (0.0, np.asarray([1.0, 2.0], dtype=np.float64)),
            (1.0, np.asarray([3.0, 4.0], dtype=np.float64)),
        ]
        token_a = ("history", 1, 2, ("axis",))
        token_b = ("history", 2, 3, ("axis",))
        times_a, matrix_a = cache.heatmap_arrays_from_history(token_a, history[:2], build_heatmap_arrays)
        history.append((2.0, np.asarray([5.0, 6.0], dtype=np.float64)))
        times_b, matrix_b = cache.heatmap_arrays_from_history(token_b, history, build_heatmap_arrays)

        self.assertEqual(times_b.tolist(), [0.0, 1.0, 2.0])
        self.assertEqual(matrix_b.shape, (3, 2))
        self.assertEqual(times_a.tolist(), [0.0, 1.0])

    def test_quantize_view_target_points_uses_power_of_two_buckets(self) -> None:
        self.assertEqual(quantize_view_target_points(1), 1)
        self.assertEqual(quantize_view_target_points(200), 256)
        self.assertEqual(quantize_view_target_points(257), 512)

    def test_heatmap_levels_expand_stably(self) -> None:
        matrix = np.asarray([[1.0, 2.0, 3.0], [2.5, 3.5, 4.0]], dtype=np.float64)
        levels = derive_heatmap_levels_from_matrix(matrix)
        self.assertLess(levels[0], 1.0)
        self.assertGreater(levels[1], 4.0)
        expanded = expand_heatmap_levels(levels, np.asarray([0.5, 4.5, 3.0], dtype=np.float64))
        self.assertLessEqual(expanded[0], levels[0])
        self.assertGreaterEqual(expanded[1], levels[1])

    def test_absolute_metric_sampling_is_tail_stable(self) -> None:
        x = np.arange(0.0, 20.0, dtype=np.float64)
        y = x * 2.0
        first_x, first_y = sample_absolute_metric_series_for_view(x, y, view_width_px=5.0)
        x2 = np.arange(0.0, 21.0, dtype=np.float64)
        y2 = x2 * 2.0
        second_x, second_y = sample_absolute_metric_series_for_view(x2, y2, view_width_px=5.0)
        self.assertTrue(np.all(first_x == second_x[: len(first_x)]))
        self.assertTrue(np.all(first_y == second_y[: len(first_y)]))

    def test_absolute_metric_sampling_preserves_extrema(self) -> None:
        x = np.arange(0.0, 120.0, dtype=np.float64)
        y = np.zeros_like(x)
        y[17] = 10.0
        y[88] = -5.0
        sampled_x, sampled_y = sample_absolute_metric_series_for_view(
            x,
            y,
            view_width_px=4.0,
            minimum_points=2,
            default_points=8,
        )

        self.assertIn(10.0, sampled_y.tolist())
        self.assertIn(-5.0, sampled_y.tolist())
        self.assertGreaterEqual(len(sampled_x), 4)

    def test_absolute_heatmap_sampling_is_tail_stable(self) -> None:
        times = np.arange(0.0, 30.0, dtype=np.float64)
        matrix = np.stack([np.array([row, row + 1.0], dtype=np.float64) for row in times], axis=0)
        first_times, first_matrix = sample_absolute_heatmap_rows_for_view(times, matrix, max_rows=10)
        times2 = np.arange(0.0, 31.0, dtype=np.float64)
        matrix2 = np.stack([np.array([row, row + 1.0], dtype=np.float64) for row in times2], axis=0)
        second_times, second_matrix = sample_absolute_heatmap_rows_for_view(times2, matrix2, max_rows=10)
        stable_len = max(min(len(first_times), len(second_times)) - 1, 0)
        self.assertTrue(np.all(first_times[:stable_len] == second_times[:stable_len]))
        self.assertTrue(np.all(first_matrix[:stable_len] == second_matrix[:stable_len]))

    def test_absolute_metric_view_cache_extends_tail_without_reflowing_prefix(self) -> None:
        cache = PlotViewCache()
        token = ("series", "absolute", 1, 10)
        x = np.arange(0.0, 3000.0, dtype=np.float64)
        y = x * 10.0
        first_x, first_y = cache.absolute_metric_view(token, x, y, view_width_px=None)
        x2 = np.arange(0.0, 3100.0, dtype=np.float64)
        y2 = x2 * 10.0
        second_x, second_y = cache.absolute_metric_view(token, x2, y2, view_width_px=None)

        stable_len = max(min(len(first_x), len(second_x)) - 1, 0)
        self.assertTrue(np.all(first_x[:stable_len] == second_x[:stable_len]))
        self.assertTrue(np.all(first_y[:stable_len] == second_y[:stable_len]))
        self.assertGreaterEqual(len(second_x), len(first_x))
        self.assertLessEqual(len(cache._absolute_metric_view_cache), 1)

    def test_absolute_metric_view_cache_reuses_same_entry_for_appends(self) -> None:
        cache = PlotViewCache()
        token = ("series", "absolute", 99, 1)
        x = np.arange(0.0, 100.0, dtype=np.float64)
        y = x * 3.0
        cache.absolute_metric_view(token, x, y, view_width_px=100.0)
        x2 = np.arange(0.0, 120.0, dtype=np.float64)
        y2 = x2 * 3.0
        cache.absolute_metric_view(token, x2, y2, view_width_px=100.0)

        self.assertLessEqual(len(cache._absolute_metric_view_cache), 1)

    def test_append_only_metric_history_buffer_reports_incremental_exports(self) -> None:
        buffer = AppendOnlyMetricHistoryBuffer(4)

        buffer.append(1.0, 2.0)
        buffer.append(2.0, 3.0)
        first_x, first_y = buffer.to_arrays()
        first_mode = buffer.last_export_mode

        buffer.append(3.0, 4.0)
        second_x, second_y = buffer.to_arrays()
        second_mode = buffer.last_export_mode

        self.assertEqual(first_mode, "full_rebuild")
        self.assertIn(second_mode, {"hit", "incremental", "full_rebuild"})
        self.assertEqual(first_x.tolist(), [1.0, 2.0])
        self.assertEqual(second_x.tolist(), [1.0, 2.0, 3.0])
        self.assertEqual(first_y.tolist(), [2.0, 3.0])
        self.assertEqual(second_y.tolist(), [2.0, 3.0, 4.0])

    def test_absolute_metric_view_cache_tracks_incremental_mode(self) -> None:
        cache = PlotViewCache()
        token = ("series", "absolute", 123, 1)
        x = np.arange(0.0, 2048.0, dtype=np.float64)
        y = np.sin(x / 15.0)

        first_x, first_y = cache.absolute_metric_view(token, x, y, view_width_px=220.0)
        first_snapshot = cache.metric_cache_debug_snapshot()

        x2 = np.arange(0.0, 2300.0, dtype=np.float64)
        y2 = np.sin(x2 / 15.0)
        second_x, second_y = cache.absolute_metric_view(token, x2, y2, view_width_px=220.0)
        second_snapshot = cache.metric_cache_debug_snapshot()

        self.assertLessEqual(len(cache._absolute_metric_view_cache), 1)
        self.assertGreaterEqual(len(second_x), 1)
        self.assertGreaterEqual(len(second_y), 1)
        self.assertTrue(first_snapshot)
        self.assertTrue(second_snapshot)
        entry = next(iter(second_snapshot.values()))
        self.assertIn(entry["last_mode"], {"full_rebuild", "incremental", "hit"})
        self.assertGreaterEqual(int(entry["incremental"]) + int(entry["rebuilds"]) + int(entry["hits"]), 1)
        self.assertIsNotNone(cache.absolute_metric_display_state(token))

    def test_absolute_heatmap_view_cache_extends_tail_without_reflowing_prefix(self) -> None:
        cache = PlotViewCache()
        token = ("heatmap", "absolute", 1, 10)
        times = np.arange(0.0, 200.0, dtype=np.float64)
        matrix = np.stack([np.array([row, row + 1.0], dtype=np.float64) for row in times], axis=0)
        first_times, first_matrix = cache.absolute_heatmap_view(token, times, matrix, max_rows=64)
        times2 = np.arange(0.0, 210.0, dtype=np.float64)
        matrix2 = np.stack([np.array([row, row + 1.0], dtype=np.float64) for row in times2], axis=0)
        second_times, second_matrix = cache.absolute_heatmap_view(token, times2, matrix2, max_rows=64)

        stable_len = max(min(len(first_times), len(second_times)) - 1, 0)
        self.assertTrue(np.all(first_times[:stable_len] == second_times[:stable_len]))
        self.assertTrue(np.all(first_matrix[:stable_len] == second_matrix[:stable_len]))
        self.assertGreaterEqual(len(second_times), len(first_times))

    def test_token_helpers_track_buffer_revision_and_heatmap_state(self) -> None:
        buffer = AppendOnlyMetricHistoryBuffer(8)
        buffer.append(1.0, 2.0)
        window = SimpleNamespace(
            _selected_trace_metrics=lambda: ["smoothed_max"],
            _normalize_sensorgram_view_mode=lambda value: value,
            _sensorgram_view_mode="absolute",
            _peak_history={"smoothed_max": buffer},
            _peak_history_buffers={},
            _sensorgram_heatmap_history=[(0.0, np.asarray([1.0, 2.0]))],
            _sensorgram_heatmap_history_revision=3,
            _sensorgram_heatmap_wavelengths=np.asarray([500.0, 600.0]),
            _sensorgram_heatmap_axis_key=(2, 500.0, 600.0),
        )

        series_token = build_active_trace_series_token(window)
        heatmap_token = build_heatmap_history_token(window)

        self.assertIn("absolute", series_token)
        self.assertEqual(heatmap_token[1], 3)
        metric_token = build_metric_series_token(window, "smoothed_max")
        self.assertEqual(metric_token[0], "smoothed_max")
        self.assertEqual(extract_series_arrays(buffer)[0].tolist(), [1.0])
        self.assertEqual(len(select_heatmap_rows_for_view(*build_heatmap_arrays(window._sensorgram_heatmap_history))[0]), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
