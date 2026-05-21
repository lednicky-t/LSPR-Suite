from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import Spectrum
from lspr_app.gui.acquisition_controller import append_processed_trace_history
from lspr_app.gui.main_window_plotting import clear_trace_history_for
from lspr_app.gui.plot_controller import (
    clip_series_to_window,
    autoscale_trace_plot,
    downsample_sensorgram_history_for_view,
    downsample_spectrum_series_for_view,
    downsample_trace_series_for_view,
    render_sensorgram_heatmap,
    render_trace_series,
)


class _FakeSpin:
    def __init__(self, value: float) -> None:
        self._value = value

    def value(self) -> float:
        return self._value


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append_metrics(self, rows: list[dict[str, object]]) -> None:
        self.rows.extend(rows)


class _FakeCurve:
    def __init__(self) -> None:
        self.data: tuple[np.ndarray, np.ndarray] | None = None
        self.visible: bool | None = None

    def setData(self, x, y) -> None:  # noqa: N802 - Qt-style API
        self.data = (np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)


class _FakeTracePlot:
    def __init__(self) -> None:
        self.ranges: list[tuple[float, float]] = []
        self.y_ranges: list[tuple[float, float]] = []

    def enableAutoRange(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        return None

    def autoRange(self) -> None:  # noqa: N802 - Qt-style API
        return None

    def setXRange(self, x_min, x_max, *, padding=0.0) -> None:  # noqa: N802 - Qt-style API
        self.ranges.append((float(x_min), float(x_max)))

    def setYRange(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        if len(args) >= 2:
            self.y_ranges.append((float(args[0]), float(args[1])))
        return None

    def getPlotItem(self):  # noqa: N802 - Qt-style API
        return SimpleNamespace(vb=self)

    def viewRange(self):  # noqa: N802 - Qt-style API
        return [[0.0, 20.0], [0.0, 1.0]]

    def sceneBoundingRect(self):  # noqa: N802 - Qt-style API
        return SimpleNamespace(width=lambda: 320.0, height=lambda: 240.0)


class _FakeTraceAxis:
    def __init__(self, mode: str) -> None:
        self._mode = mode


class _FakeImageItem:
    def __init__(self) -> None:
        self.visible = None
        self.image = None
        self.rect = None

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)

    def setImage(self, image, autoLevels=True) -> None:  # noqa: N802 - Qt-style API
        self.image = np.asarray(image, dtype=np.float64)

    def setRect(self, rect) -> None:  # noqa: N802 - Qt-style API
        self.rect = rect

    def setLookupTable(self, table) -> None:  # noqa: N802 - Qt-style API
        self.lookup_table = np.asarray(table)


class _FakeLegend:
    def __init__(self) -> None:
        self.visible = None

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)


class SensorgramTimeTests(unittest.TestCase):
    def _make_window(self, *, measurement_active: bool, measurement_started_at: datetime | None) -> SimpleNamespace:
        return SimpleNamespace(
            _live_active=True,
            _measurement_active=measurement_active,
            _measurement_started_at=measurement_started_at,
            _measurement_writer=_FakeWriter() if measurement_active else None,
            _peak_history={},
            _trace_display_window_s=60.0,
            _trace_display_cursor_s=0.0,
            live_rate_spin=_FakeSpin(2.0),
            TRACE_METRIC_LABELS={"smoothed_max": "Smoothed"},
            _selected_trace_metrics=lambda: ["smoothed_max"],
            _get_analysis_metrics=lambda processed, fit: {"smoothed_max": 42.0},
            _request_deferred_ui_refresh=lambda **kwargs: None,
            _request_trace_autoscale=lambda: None,
            _log_throttled=lambda *args, **kwargs: None,
        )

    def test_recording_trace_starts_at_zero(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        processed = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0], dtype=np.float64),
            values=np.asarray([1.0, 2.0], dtype=np.float64),
            y_label="sample",
            acquired_at=started_at + timedelta(seconds=5),
        )
        window = self._make_window(measurement_active=True, measurement_started_at=started_at)

        append_processed_trace_history(window, processed, None)

        self.assertIn("smoothed_max", window._peak_history)
        self.assertAlmostEqual(window._peak_history["smoothed_max"][0][0], 5.0, places=6)
        self.assertEqual(window._measurement_writer.rows[0]["t_ms"], 5000)

    def test_live_trace_uses_local_timestamp(self) -> None:
        acquired_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        processed = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0], dtype=np.float64),
            values=np.asarray([1.0, 2.0], dtype=np.float64),
            y_label="sample",
            acquired_at=acquired_at,
        )
        window = self._make_window(measurement_active=False, measurement_started_at=None)

        append_processed_trace_history(window, processed, None)

        self.assertIn("smoothed_max", window._peak_history)
        self.assertAlmostEqual(window._peak_history["smoothed_max"][0][0], acquired_at.timestamp(), places=6)

    def test_trace_renderer_accepts_float_timestamps(self) -> None:
        window = SimpleNamespace(
            trace_curves={"smoothed_max": _FakeCurve()},
            _selected_trace_metrics=lambda: ["smoothed_max"],
            _primary_trace_metric=lambda: "smoothed_max",
            _visible_trace_x=None,
            _visible_trace_y=None,
        )
        history = {
            "smoothed_max": [
                (10.0, 1.5),
                (12.5, 1.75),
            ]
        }

        render_trace_series(window, history, clock_mode=True)

        self.assertIsNotNone(window.trace_curves["smoothed_max"].data)
        x_values, y_values = window.trace_curves["smoothed_max"].data
        self.assertListEqual(x_values.tolist(), [10.0, 12.5])
        self.assertListEqual(y_values.tolist(), [1.5, 1.75])

    def test_recording_history_is_not_trimmed_for_absolute_view(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        window = self._make_window(measurement_active=True, measurement_started_at=started_at)
        window._trace_display_window_s = 1.0
        first = Spectrum(
            wavelengths_nm=np.asarray([610.0], dtype=np.float64),
            values=np.asarray([1.0], dtype=np.float64),
            y_label="sample",
            acquired_at=started_at,
        )
        second = Spectrum(
            wavelengths_nm=np.asarray([610.0], dtype=np.float64),
            values=np.asarray([2.0], dtype=np.float64),
            y_label="sample",
            acquired_at=started_at + timedelta(seconds=5),
        )

        append_processed_trace_history(window, first, None)
        append_processed_trace_history(window, second, None)

        self.assertEqual(len(window._peak_history["smoothed_max"]), 2)

    def test_trace_history_is_bounded_to_recent_points(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        window = self._make_window(measurement_active=False, measurement_started_at=None)
        window._trace_history_max_points = 2

        for offset_s, value in enumerate([1.0, 2.0, 3.0]):
            processed = Spectrum(
                wavelengths_nm=np.asarray([610.0], dtype=np.float64),
                values=np.asarray([float(value)], dtype=np.float64),
                y_label="sample",
                acquired_at=started_at + timedelta(seconds=float(offset_s)),
            )
            append_processed_trace_history(window, processed, None)

        history = window._peak_history["smoothed_max"]
        self.assertEqual(len(history), 2)
        self.assertAlmostEqual(history[0][0], (started_at + timedelta(seconds=1)).timestamp(), places=6)
        self.assertAlmostEqual(history[1][0], (started_at + timedelta(seconds=2)).timestamp(), places=6)
        self.assertListEqual([item[1] for item in history], [42.0, 42.0])

    def test_autoscale_trace_plot_respects_absolute_and_rolling_modes(self) -> None:
        series = {
            "smoothed_max": (
                np.asarray([0.0, 10.0, 20.0], dtype=np.float64),
                np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
            )
        }
        window = SimpleNamespace(
            _active_trace_series=lambda: series,
            _sensorgram_view_mode="absolute",
            _trace_display_window_s=5.0,
            trace_plot=_FakeTracePlot(),
            trace_time_axis=_FakeTraceAxis("elapsed"),
        )

        autoscale_trace_plot(window)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][0], 0.0, places=6)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 20.6, places=6)

        window._sensorgram_view_mode = "rolling"
        autoscale_trace_plot(window)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][0], 15.0, places=6)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 20.15, places=6)

    def test_heatmap_renderer_populates_image_matrix(self) -> None:
        wavelengths = np.asarray([610.0, 620.0], dtype=np.float64)
        history = [
            (0.0, np.asarray([0.1, 0.2], dtype=np.float64)),
            (5.0, np.asarray([0.3, 0.4], dtype=np.float64)),
        ]
        window = SimpleNamespace(
            trace_heatmap_image=_FakeImageItem(),
            trace_plot=_FakeTracePlot(),
            trace_curves={"smoothed_max": _FakeCurve()},
            trace_legend=_FakeLegend(),
            _sensorgram_heatmap_wavelengths=wavelengths,
            _visible_trace_x=None,
            _visible_trace_y=None,
            _visible_trace_mode=None,
        )

        render_sensorgram_heatmap(window, history, clock_mode=False)

        self.assertTrue(window.trace_heatmap_image.visible)
        self.assertIsNotNone(window.trace_heatmap_image.image)
        self.assertEqual(window.trace_heatmap_image.image.shape, (2, 2))
        self.assertFalse(window.trace_curves["smoothed_max"].visible)
        self.assertFalse(window.trace_legend.visible)

    def test_heatmap_renderer_respects_rolling_view_mode(self) -> None:
        wavelengths = np.asarray([610.0, 620.0], dtype=np.float64)
        history = [
            (0.0, np.asarray([0.1, 0.2], dtype=np.float64)),
            (10.0, np.asarray([0.3, 0.4], dtype=np.float64)),
            (20.0, np.asarray([0.5, 0.6], dtype=np.float64)),
        ]
        window = SimpleNamespace(
            trace_heatmap_image=_FakeImageItem(),
            trace_plot=_FakeTracePlot(),
            trace_curves={"smoothed_max": _FakeCurve()},
            trace_legend=_FakeLegend(),
            _sensorgram_heatmap_wavelengths=wavelengths,
            _visible_trace_x=None,
            _visible_trace_y=None,
            _visible_trace_mode=None,
            _sensorgram_view_mode="rolling",
            _trace_view_locked=False,
            _trace_display_window_s=5.0,
        )

        render_sensorgram_heatmap(window, history, clock_mode=False)

        self.assertTrue(window.trace_heatmap_image.visible)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][0], 15.0, places=6)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 20.15, places=6)
        self.assertAlmostEqual(window.trace_plot.y_ranges[-1][0], 610.0, places=6)
        self.assertAlmostEqual(window.trace_plot.y_ranges[-1][1], 620.0, places=6)
        self.assertLessEqual(window.trace_heatmap_image.image.shape[1], 2)

    def test_trace_downsampling_prefers_visible_window(self) -> None:
        x = np.arange(0.0, 1000.0, dtype=np.float64)
        y = np.sin(x / 20.0)

        sampled_x, sampled_y = downsample_trace_series_for_view(
            x,
            y,
            view_x_min=400.0,
            view_x_max=500.0,
            view_width_px=80.0,
        )

        self.assertLessEqual(len(sampled_x), 160)
        self.assertGreaterEqual(float(sampled_x[0]), 399.0)
        self.assertLessEqual(float(sampled_x[-1]), 501.0)
        self.assertEqual(sampled_x.shape, sampled_y.shape)

    def test_spectrum_downsampling_prefers_visible_window(self) -> None:
        x = np.arange(0.0, 1200.0, dtype=np.float64)
        y = np.cos(x / 25.0)

        sampled_x, sampled_y = downsample_spectrum_series_for_view(
            x,
            y,
            view_x_min=500.0,
            view_x_max=650.0,
            view_width_px=100.0,
        )

        self.assertLessEqual(len(sampled_x), 192)
        self.assertGreaterEqual(float(sampled_x[0]), 499.0)
        self.assertLessEqual(float(sampled_x[-1]), 651.0)
        self.assertEqual(sampled_x.shape, sampled_y.shape)

    def test_clip_series_to_window_limits_display_to_fit_region(self) -> None:
        x = np.asarray([400.0, 500.0, 600.0, 700.0, 800.0], dtype=np.float64)
        y = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float64)

        clipped_x, clipped_y = clip_series_to_window(x, y, window_min=550.0, window_max=750.0)

        self.assertListEqual(clipped_x.tolist(), [600.0, 700.0])
        self.assertListEqual(clipped_y.tolist(), [3.0, 4.0])

    def test_heatmap_history_downsampling_prefers_visible_window(self) -> None:
        history = [
            (float(index), np.asarray([float(index), float(index) + 1.0], dtype=np.float64))
            for index in range(20)
        ]

        sampled = downsample_sensorgram_history_for_view(
            history,
            view_x_min=6.0,
            view_x_max=12.0,
            max_rows=4,
        )

        self.assertLessEqual(len(sampled), 4)
        self.assertTrue(all(6.0 <= float(time_value) <= 12.0 for time_value, _ in sampled))
        self.assertTrue(all(row.shape == (2,) for _, row in sampled))

    def test_heatmap_history_downsampling_caps_to_view_height(self) -> None:
        history = [
            (float(index), np.asarray([float(index), float(index) + 1.0], dtype=np.float64))
            for index in range(400)
        ]

        sampled = downsample_sensorgram_history_for_view(
            history,
            max_rows=1000,
            view_height_px=100.0,
        )

        self.assertLessEqual(len(sampled), 256)
        self.assertGreaterEqual(len(sampled), 1)
        self.assertEqual(sampled[0][1].shape, (2,))

    def test_clear_trace_history_clears_heatmap_state(self) -> None:
        window = SimpleNamespace(
            _peak_history={"smoothed_max": [(1.0, 2.0)]},
            _sensorgram_heatmap_history=[(1.0, np.asarray([0.1, 0.2], dtype=np.float64))],
            _sensorgram_heatmap_wavelengths=np.asarray([610.0, 620.0], dtype=np.float64),
            _session=SimpleNamespace(state=SimpleNamespace(absorbance=None, sample=None)),
            _get_analysis_processed_spectrum=lambda signal: (signal, None),
            _peak_reference_processed=None,
            _refresh_trace_plot=lambda trace_label: None,
            _update_trace_stats=lambda: None,
            status_label=SimpleNamespace(setText=lambda text: None),
        )

        clear_trace_history_for(window)

        self.assertEqual(window._peak_history, {})
        self.assertEqual(window._sensorgram_heatmap_history, [])
        self.assertIsNone(window._sensorgram_heatmap_wavelengths)


if __name__ == "__main__":
    unittest.main()
