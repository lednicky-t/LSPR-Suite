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
    build_metric_series_token,
    quantize_view_target_points,
    sample_absolute_metric_series_for_view,
)


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

    def test_quantize_view_target_points_uses_power_of_two_buckets(self) -> None:
        self.assertEqual(quantize_view_target_points(1), 1)
        self.assertEqual(quantize_view_target_points(200), 256)
        self.assertEqual(quantize_view_target_points(257), 512)

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

    def test_absolute_metric_view_cache_extends_tail_without_reflowing_prefix(self) -> None:
        cache = PlotViewCache()
        token = ("series", "absolute", 1, 10)
        x = np.arange(0.0, 3000.0, dtype=np.float64)
        y = x * 10.0
        first_x, first_y = cache.absolute_metric_view(token, x, y, view_width_px=None)
        x2 = np.arange(0.0, 3100.0, dtype=np.float64)
        y2 = x2 * 10.0
        second_x, second_y = cache.absolute_metric_view(token, x2, y2, view_width_px=None)

        self.assertGreater(len(first_x), 0)
        self.assertGreater(len(second_x), 0)
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

    def test_token_helpers_track_live_absolute_state(self) -> None:
        cache = PlotViewCache()
        cache.seed_live_absolute_metric_cache(
            "smoothed_max",
            np.asarray([0.0, 1.0], dtype=np.float64),
            np.asarray([1.0, 2.0], dtype=np.float64),
            target_points=8,
            recent_tail_points=2,
        )
        window = SimpleNamespace(
            _selected_trace_metrics=lambda: ["smoothed_max"],
            _normalize_sensorgram_view_mode=lambda value: value,
            _sensorgram_view_mode="absolute",
            _plot_view_cache=cache,
        )

        series_token = build_active_trace_series_token(window)
        self.assertIn("live_absolute", series_token)

        metric_token = build_metric_series_token(window, "smoothed_max")
        self.assertEqual(metric_token[0], "smoothed_max")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
