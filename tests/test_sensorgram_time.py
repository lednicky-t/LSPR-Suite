from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import numpy as np
from PyQt6.QtWidgets import QApplication

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import Spectrum
from lspr_app.gui.acquisition_controller import append_processed_trace_history
import lspr_app.gui.main_window as main_window_module
from lspr_app.gui.main_window_plotting import apply_processing_range_to_spectrum_plot_for, clear_trace_history_for
from lspr_app.gui.processing_helpers import get_analysis_metrics
from lspr_app.gui.plot_controller import (
    clip_series_to_window,
    autoscale_trace_plot,
    downsample_sensorgram_history_for_view,
    downsample_spectrum_series_for_view,
    downsample_trace_series_for_view,
    render_sensorgram_heatmap,
    render_trace_series,
    request_trace_autoscale,
)
from lspr_app.gui.spectrum_plot_controller import (
    clear_residual_display,
    render_residual_display,
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
    def __init__(self, view_range: list[list[float]] | None = None) -> None:
        self.ranges: list[tuple[float, float]] = []
        self.y_ranges: list[tuple[float, float]] = []
        self._view_range = view_range or [[0.0, 20.0], [0.0, 1.0]]

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
        return self._view_range

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
        self.levels = None

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)

    def setImage(self, image, autoLevels=True) -> None:  # noqa: N802 - Qt-style API
        self.image = np.asarray(image, dtype=np.float64)

    def setRect(self, rect) -> None:  # noqa: N802 - Qt-style API
        self.rect = rect

    def setLookupTable(self, table) -> None:  # noqa: N802 - Qt-style API
        self.lookup_table = np.asarray(table)

    def setLevels(self, levels) -> None:  # noqa: N802 - Qt-style API
        self.levels = tuple(float(value) for value in levels)


class _FakeTextItem:
    def __init__(self) -> None:
        self.visible = None
        self.text = ""
        self.html = ""
        self.pos = None

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)

    def setText(self, value) -> None:  # noqa: N802 - Qt-style API
        self.text = str(value)

    def setHtml(self, value) -> None:  # noqa: N802 - Qt-style API
        self.html = str(value)

    def setPos(self, x, y) -> None:  # noqa: N802 - Qt-style API
        self.pos = (float(x), float(y))


class _FakeLegend:
    def __init__(self) -> None:
        self.visible = None

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)


class _FakeResidualCurve:
    def __init__(self) -> None:
        self.data = None
        self.pen = None

    def setData(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        if args and len(args) >= 2:
            x, y = args[0], args[1]
        else:
            x = kwargs.get("x", [])
            y = kwargs.get("y", [])
        self.data = (np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))

    def setPen(self, pen) -> None:  # noqa: N802 - Qt-style API
        self.pen = pen


class _FakeResidualView:
    def __init__(self) -> None:
        self.items = []
        self.visible = None

    def addItem(self, item) -> None:  # noqa: N802 - Qt-style API
        self.items.append(item)

    def removeItem(self, item) -> None:  # noqa: N802 - Qt-style API
        if item in self.items:
            self.items.remove(item)

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)

    def enableAutoRange(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        return None

    def setYRange(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        return None

    def setGeometry(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        return None

    def linkedViewChanged(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        return None


class _FakeTimer:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True


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

        self.assertIn("smoothed_max", window._peak_history_buffers)
        x_values, y_values = window._peak_history_buffers["smoothed_max"].to_arrays()
        self.assertAlmostEqual(float(x_values[0]), 5.0, places=6)
        self.assertAlmostEqual(float(y_values[0]), 42.0, places=6)
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

        self.assertIn("smoothed_max", window._peak_history_buffers)
        x_values, y_values = window._peak_history_buffers["smoothed_max"].to_arrays()
        self.assertAlmostEqual(float(x_values[0]), acquired_at.timestamp(), places=6)
        self.assertAlmostEqual(float(y_values[0]), 42.0, places=6)

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

        self.assertEqual(len(window._peak_history_buffers["smoothed_max"]), 2)

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

        x_values, y_values = window._peak_history_buffers["smoothed_max"].to_arrays()
        self.assertEqual(len(x_values), 2)
        self.assertAlmostEqual(float(x_values[0]), (started_at + timedelta(seconds=1)).timestamp(), places=6)
        self.assertAlmostEqual(float(x_values[1]), (started_at + timedelta(seconds=2)).timestamp(), places=6)
        self.assertListEqual([float(item) for item in y_values.tolist()], [42.0, 42.0])

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
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 20.1, places=6)

        window._sensorgram_view_mode = "rolling"
        autoscale_trace_plot(window)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][0], 15.0, places=6)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 20.025, places=6)

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
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 20.025, places=6)
        self.assertAlmostEqual(window.trace_plot.y_ranges[-1][0], 610.0, places=6)
        self.assertAlmostEqual(window.trace_plot.y_ranges[-1][1], 620.0, places=6)
        self.assertLessEqual(window.trace_heatmap_image.image.shape[1], 2)

    def test_heatmap_renderer_disabled_shows_placeholder(self) -> None:
        wavelengths = np.asarray([610.0, 620.0], dtype=np.float64)
        history = [
            (0.0, np.asarray([0.1, 0.2], dtype=np.float64)),
            (5.0, np.asarray([0.3, 0.4], dtype=np.float64)),
        ]
        window = SimpleNamespace(
            trace_heatmap_image=_FakeImageItem(),
            trace_heatmap_notice_item=_FakeTextItem(),
            trace_plot=_FakeTracePlot(view_range=[[0.0, 10.0], [600.0, 630.0]]),
            trace_curves={"smoothed_max": _FakeCurve()},
            trace_legend=_FakeLegend(),
            _sensorgram_heatmap_wavelengths=wavelengths,
            _visible_trace_x=None,
            _visible_trace_y=None,
            _visible_trace_mode=None,
            _sensorgram_view_mode="absolute",
            _trace_view_locked=False,
            _trace_display_window_s=5.0,
            _sensorgram_heatmap_enabled=False,
        )

        render_sensorgram_heatmap(window, history, clock_mode=False)

        self.assertFalse(window.trace_heatmap_image.visible)
        self.assertTrue(window.trace_heatmap_notice_item.visible)
        self.assertIn("Heatmap unavailable", window.trace_heatmap_notice_item.html or window.trace_heatmap_notice_item.text)
        self.assertFalse(window.trace_curves["smoothed_max"].visible)
        self.assertFalse(window.trace_legend.visible)

    def test_metric_renderer_disabled_shows_placeholder(self) -> None:
        window = SimpleNamespace(
            trace_heatmap_image=_FakeImageItem(),
            trace_heatmap_notice_item=_FakeTextItem(),
            trace_plot=_FakeTracePlot(),
            trace_curves={"smoothed_max": _FakeCurve()},
            trace_legend=_FakeLegend(),
            trace_time_axis=_FakeTraceAxis("elapsed"),
            _measurement_active=False,
            _sensorgram_content_mode="metric",
            _metric_plot_enabled=False,
            _sensorgram_heatmap_history=[],
            _sensorgram_heatmap_wavelengths=None,
            _visible_trace_x=None,
            _visible_trace_y=None,
            _visible_trace_mode=None,
            _sensorgram_view_mode="absolute",
            _trace_view_locked=False,
            _sensorgram_downsampling_enabled=True,
            _trace_display_window_s=5.0,
            _plots_frozen=False,
            _active_trace_series=lambda: {"smoothed_max": (np.asarray([0.0, 1.0]), np.asarray([1.0, 2.0]))},
        )

        from lspr_app.gui.plot_controller import refresh_metric_plot

        refresh_metric_plot(window, "Peak position (nm)")

        self.assertFalse(window.trace_heatmap_image.visible)
        self.assertTrue(window.trace_heatmap_notice_item.visible)
        self.assertIn("Metric plot unavailable", window.trace_heatmap_notice_item.html or window.trace_heatmap_notice_item.text)
        self.assertFalse(window.trace_curves["smoothed_max"].visible)

    def test_trace_renderer_absolute_ignores_stale_viewport(self) -> None:
        window = SimpleNamespace(
            trace_curves={"smoothed_max": _FakeCurve()},
            _selected_trace_metrics=lambda: ["smoothed_max"],
            _primary_trace_metric=lambda: "smoothed_max",
            _visible_trace_x=None,
            _visible_trace_y=None,
            _sensorgram_view_mode="absolute",
            _trace_view_locked=False,
            _sensorgram_downsampling_enabled=True,
            _trace_display_window_s=5.0,
            trace_plot=_FakeTracePlot(view_range=[[40.0, 60.0], [0.0, 1.0]]),
        )
        history = {
            "smoothed_max": [(float(index), float(index)) for index in range(100)],
        }

        render_trace_series(window, history, clock_mode=False)

        x_values, y_values = window.trace_curves["smoothed_max"].data
        self.assertEqual(len(x_values), 100)
        self.assertListEqual(x_values[:3].tolist(), [0.0, 1.0, 2.0])
        self.assertListEqual(x_values[-3:].tolist(), [97.0, 98.0, 99.0])
        self.assertListEqual(y_values[:3].tolist(), [0.0, 1.0, 2.0])

    def test_downsampling_toggle_can_bypass_reduction(self) -> None:
        x = np.arange(0.0, 1000.0, dtype=np.float64)
        y = np.sin(x / 20.0)

        sampled_x, sampled_y = downsample_trace_series_for_view(
            x,
            y,
            view_width_px=20.0,
            enabled=False,
        )

        self.assertEqual(sampled_x.shape, x.shape)
        self.assertEqual(sampled_y.shape, y.shape)
        self.assertTrue(np.array_equal(sampled_x, x))
        self.assertTrue(np.array_equal(sampled_y, y))

    def test_heatmap_downsampling_toggle_can_bypass_reduction(self) -> None:
        history = [
            (float(index), np.asarray([float(index), float(index) + 1.0], dtype=np.float64))
            for index in range(600)
        ]

        sampled = downsample_sensorgram_history_for_view(
            history,
            view_height_px=50.0,
            enabled=False,
        )

        self.assertEqual(len(sampled), len(history))
        self.assertTrue(all(np.array_equal(sampled[index][1], history[index][1]) for index in range(len(history))))

    def test_heatmap_renderer_absolute_ignores_stale_viewport(self) -> None:
        wavelengths = np.asarray([610.0, 620.0], dtype=np.float64)
        history = [
            (0.0, np.asarray([0.1, 0.2], dtype=np.float64)),
            (10.0, np.asarray([0.3, 0.4], dtype=np.float64)),
            (20.0, np.asarray([0.5, 0.6], dtype=np.float64)),
        ]
        window = SimpleNamespace(
            trace_heatmap_image=_FakeImageItem(),
            trace_plot=_FakeTracePlot(view_range=[[40.0, 60.0], [0.0, 1.0]]),
            trace_curves={"smoothed_max": _FakeCurve()},
            trace_legend=_FakeLegend(),
            _sensorgram_heatmap_wavelengths=wavelengths,
            _visible_trace_x=None,
            _visible_trace_y=None,
            _visible_trace_mode=None,
            _sensorgram_view_mode="absolute",
            _trace_view_locked=False,
            _trace_display_window_s=5.0,
            _sensorgram_downsampling_enabled=True,
        )

        render_sensorgram_heatmap(window, history, clock_mode=False)

        self.assertTrue(window.trace_heatmap_image.visible)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][0], 0.0, places=6)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 20.1, places=6)

    def test_residual_renderer_reuses_segment_items(self) -> None:
        app = QApplication.instance() or QApplication([])
        _ = app
        window = SimpleNamespace(
            residual_view=_FakeResidualView(),
            residual_curve=_FakeResidualCurve(),
            _residual_segment_items=[],
            _residual_pen_cache={},
        )
        x = np.asarray([600.0, 610.0, 620.0, 630.0], dtype=np.float64)
        y = np.asarray([0.1, 0.2, 0.3, 0.4], dtype=np.float64)

        render_residual_display(window, x, y)
        first_items = list(window._residual_segment_items)
        first_count = len(window.residual_view.items)

        render_residual_display(window, x, y)

        self.assertEqual(len(window.residual_view.items), first_count)
        self.assertEqual(window._residual_segment_items, first_items)
        self.assertEqual(len(window._residual_segment_items), 3)

    def test_residual_renderer_caps_segment_count_for_dense_series(self) -> None:
        app = QApplication.instance() or QApplication([])
        _ = app
        window = SimpleNamespace(
            residual_view=_FakeResidualView(),
            residual_curve=_FakeResidualCurve(),
            _residual_segment_items=[],
            _residual_pen_cache={},
        )
        x = np.linspace(500.0, 800.0, 1200, dtype=np.float64)
        y = np.sin(x / 13.0) * 0.5

        render_residual_display(window, x, y)

        self.assertLessEqual(len(window._residual_segment_items), 191)
        self.assertLessEqual(len(window.residual_view.items), 191)

    def test_spectrum_freeze_does_not_block_sensorgram_autoscale_timer(self) -> None:
        window = SimpleNamespace(
            _plots_frozen=True,
            _sensorgram_frozen=False,
            _trace_autoscale_timer=_FakeTimer(),
        )

        request_trace_autoscale(window)

        self.assertTrue(window._trace_autoscale_timer.started)

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

    def test_processing_range_autoscale_sets_spectrum_bounds_immediately(self) -> None:
        class _FakeSpectrumPlot:
            def __init__(self) -> None:
                self.x_ranges: list[tuple[float, float]] = []

            def setXRange(self, x_min, x_max, *, padding=0.0) -> None:  # noqa: N802 - Qt-style API
                self.x_ranges.append((float(x_min), float(x_max)))

        window = SimpleNamespace(
            _current_processing_settings=lambda: SimpleNamespace(wavelength_min_nm=501.0, wavelength_max_nm=800.0),
            spectrum_plot=_FakeSpectrumPlot(),
            _spectrum_render_cache_key=("stale",),
        )

        apply_processing_range_to_spectrum_plot_for(window)

        self.assertEqual(window.spectrum_plot.x_ranges, [(501.0, 800.0)])
        self.assertIsNone(window._spectrum_render_cache_key)

    def test_spectrum_autoscale_also_applies_processing_range_immediately(self) -> None:
        window = SimpleNamespace(
            spectrum_plot=SimpleNamespace(),
            _current_processing_settings=lambda: SimpleNamespace(wavelength_min_nm=501.0, wavelength_max_nm=800.0),
        )
        calls: list[str] = []

        def _autoscale_stub(_window) -> None:
            calls.append("autoscale")

        def _apply_stub(_window) -> None:
            calls.append("apply")

        original_autoscale = main_window_module.autoscale_spectrum_plot_for
        original_apply = main_window_module.apply_processing_range_to_spectrum_plot_for
        try:
            main_window_module.autoscale_spectrum_plot_for = _autoscale_stub  # type: ignore[assignment]
            main_window_module.apply_processing_range_to_spectrum_plot_for = _apply_stub  # type: ignore[assignment]
            main_window_module.MainWindow._autoscale_spectrum_plot(window)  # type: ignore[arg-type]
        finally:
            main_window_module.autoscale_spectrum_plot_for = original_autoscale  # type: ignore[assignment]
            main_window_module.apply_processing_range_to_spectrum_plot_for = original_apply  # type: ignore[assignment]

        self.assertEqual(calls, ["autoscale", "apply"])

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

    def test_smoothed_max_and_centroid_do_not_depend_on_fit(self) -> None:
        processed = Spectrum(
            wavelengths_nm=np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float64),
            values=np.asarray([0.0, 3.0, 1.0, 0.0], dtype=np.float64),
            y_label="sample",
            acquired_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )
        fit = Spectrum(
            wavelengths_nm=np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float64),
            values=np.asarray([0.0, 2.0, 4.0, 2.0], dtype=np.float64),
            y_label="sample",
            acquired_at=processed.acquired_at,
            metadata={
                "fit_method": "poly",
                "polynomial_peak_nm": 3.0,
            },
        )
        settings = SimpleNamespace(
            crop_method="fixed_width",
            crop_fraction=0.7,
            fit_method="poly",
            fit_window_width_nm=10.0,
            analysis_resolution_nm=0.1,
            polynomial_order=2,
            peak_tracking_mode="smoothed_max",
            smoothing_method="none",
            smoothing_window=1,
            baseline_method="none",
            trace_metrics=["smoothed_max"],
        )

        without_fit = get_analysis_metrics(processed, None, settings)
        with_fit = get_analysis_metrics(processed, fit, settings)

        self.assertAlmostEqual(float(with_fit["smoothed_max"]), float(without_fit["smoothed_max"]), places=6)
        self.assertAlmostEqual(float(with_fit["centroid"]), float(without_fit["centroid"]), places=6)
        self.assertNotEqual(float(with_fit["poly_max"]), float(without_fit["poly_max"]))
        self.assertAlmostEqual(float(without_fit["smoothed_max"]), 2.01, places=6)
        self.assertAlmostEqual(float(without_fit["poly_max"]), 2.01, places=6)
        self.assertAlmostEqual(float(with_fit["poly_max"]), 3.0, places=6)


if __name__ == "__main__":
    unittest.main()
