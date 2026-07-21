from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

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
    autoscale_metric_plot,
    downsample_spectrum_series_for_view,
    downsample_metric_series_for_view,
    render_metric_series,
    request_metric_autoscale,
)
from lspr_app.gui.spectrum_plot_controller import (
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
        self.set_data_calls = 0

    def setData(self, x, y, **_kwargs) -> None:  # noqa: N802 - Qt-style API
        self.set_data_calls += 1
        self.data = (np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))

    def setVisible(self, value) -> None:  # noqa: N802 - Qt-style API
        self.visible = bool(value)

    def setDownsampling(self, *, auto: bool = False, ds: int = 1) -> None:  # noqa: N802 - Qt-style API
        return None


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

    def setLabel(self, *args, **kwargs) -> None:  # noqa: N802 - Qt-style API
        return None


class _FakeTraceAxis:
    def __init__(self, mode: str) -> None:
        self._mode = mode


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

    def setSymbol(self, symbol) -> None:  # noqa: N802 - Qt-style API
        return None


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

    def isActive(self) -> bool:  # noqa: N802 - Qt-style API
        return self.started


class _FakeMetricCache:
    """Minimal stand-in for the real PlotViewCache's live-absolute metric API.

    append_processed_trace_history() no longer keeps its own in-memory history
    buffer; it forwards each point to window._plot_view_cache instead. This fake
    just records the calls so tests can assert on the elapsed-time value computed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, float, float]] = []

    def append_live_absolute_metric_point(self, metric_name, x_value, y_value, **_kwargs) -> None:
        self.calls.append((metric_name, float(x_value), float(y_value)))


class _FakeAbsoluteViewCache:
    """Minimal fake for the non-live "absolute view" downsampling path in
    render_metric_series: passes the series through unchanged and reports a
    stable per-series state so repeated renders of the same data are deduped.
    """

    def absolute_metric_view(self, series_token, x, y, **_kwargs):
        return x, y

    def absolute_metric_display_state(self, series_token):
        return ("stable", series_token)


class SensorgramTimeTests(unittest.TestCase):
    def _make_window(self, *, measurement_active: bool, measurement_started_at: datetime | None) -> SimpleNamespace:
        return SimpleNamespace(
            _live_active=True,
            _measurement_active=measurement_active,
            _measurement_started_at=measurement_started_at,
            _measurement_writer=_FakeWriter() if measurement_active else None,
            _live_trace_started_at=None,
            _plot_view_cache=_FakeMetricCache(),
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

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=None):
            append_processed_trace_history(window, processed, None)

        self.assertEqual(window._plot_view_cache.calls, [("smoothed_max", 5.0, 42.0)])
        expected_unix_ms = int(round(processed.acquired_at.timestamp() * 1000.0))
        self.assertEqual(window._measurement_writer.rows[0]["acquired_at_unix_ms"], expected_unix_ms)

    def test_live_trace_uses_elapsed_time_since_live_start(self) -> None:
        live_started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        acquired_at = live_started_at + timedelta(seconds=3)
        processed = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0], dtype=np.float64),
            values=np.asarray([1.0, 2.0], dtype=np.float64),
            y_label="sample",
            acquired_at=acquired_at,
        )
        window = self._make_window(measurement_active=False, measurement_started_at=None)
        window._live_trace_started_at = live_started_at

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=None):
            append_processed_trace_history(window, processed, None)

        self.assertEqual(window._plot_view_cache.calls, [("smoothed_max", 3.0, 42.0)])

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

        render_metric_series(window, history, clock_mode=True)

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

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=None):
            append_processed_trace_history(window, first, None)
            append_processed_trace_history(window, second, None)

        # Recording writes must stay lossless regardless of the display window:
        # both points are written even though _trace_display_window_s (1.0s) is
        # smaller than the 5s gap between them.
        self.assertEqual(len(window._measurement_writer.rows), 2)
        self.assertEqual(
            window._measurement_writer.rows[0]["acquired_at_unix_ms"],
            int(round(first.acquired_at.timestamp() * 1000.0)),
        )
        self.assertEqual(
            window._measurement_writer.rows[1]["acquired_at_unix_ms"],
            int(round(second.acquired_at.timestamp() * 1000.0)),
        )

    # NOTE: point-count bounding for live display history now lives inside
    # PlotViewCache/MetricDisplayCache (see tests/unit/test_plot_view_cache.py),
    # not in append_processed_trace_history -- there is no longer an in-memory
    # buffer here to bound, so the old test for that behavior was removed.

    def test_autoscale_metric_plot_respects_absolute_mode(self) -> None:
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

        autoscale_metric_plot(window)
        self.assertAlmostEqual(window.trace_plot.ranges[-1][0], 0.0, places=6)
        # x_max now trails the latest point by a "follow latest" buffer that scales
        # with refresh rate and series span (see _metric_follow_latest_buffer_s),
        # rather than the old fixed +0.1 padding. With defaults (4 Hz, 20s span)
        # that buffer is 1.0s.
        self.assertAlmostEqual(window.trace_plot.ranges[-1][1], 21.0, places=6)


    def test_metric_renderer_disabled_shows_placeholder(self) -> None:
        window = SimpleNamespace(
            trace_plot=_FakeTracePlot(),
            trace_curves={"smoothed_max": _FakeCurve()},
            trace_legend=_FakeLegend(),
            trace_time_axis=_FakeTraceAxis("elapsed"),
            _measurement_active=False,
            _metric_plot_enabled=False,
            _visible_trace_x=None,
            _visible_trace_y=None,
            _visible_trace_mode=None,
            _trace_view_locked=False,
            _sensorgram_downsampling_enabled=True,
            _trace_display_window_s=5.0,
            _plots_frozen=False,
            _active_trace_series=lambda: {"smoothed_max": (np.asarray([0.0, 1.0]), np.asarray([1.0, 2.0]))},
        )

        from lspr_app.gui.plot_controller import refresh_metric_plot

        refresh_metric_plot(window, "Peak position (nm)")

        # There is no separate "unavailable" notice widget anymore -- a disabled
        # metric plot just hides the curves/legend (see _show_trace_plot_unavailable).
        self.assertFalse(window.trace_curves["smoothed_max"].visible)
        self.assertFalse(window.trace_legend.visible)
        self.assertEqual(window._visible_trace_mode, "elapsed")

    def test_metric_renderer_skips_redundant_setdata_for_unchanged_series(self) -> None:
        class _CopyingBuffer:
            def __init__(self) -> None:
                self._times = np.asarray([0.0, 1.0, 2.0], dtype=np.float64)
                self._values = np.asarray([10.0, 11.0, 12.0], dtype=np.float64)

            def __len__(self) -> int:
                return len(self._times)

            @property
            def revision(self) -> int:
                return 7

            def to_arrays(self) -> tuple[np.ndarray, np.ndarray]:
                return self._times.copy(), self._values.copy()

        curve = _FakeCurve()
        buffer = _CopyingBuffer()
        window = SimpleNamespace(
            trace_plot=_FakeTracePlot(),
            trace_curves={"smoothed_max": curve},
            trace_legend=_FakeLegend(),
            trace_time_axis=_FakeTraceAxis("elapsed"),
            _measurement_active=False,
            _metric_plot_enabled=True,
            _visible_trace_x=None,
            _visible_trace_y=None,
            _visible_trace_mode=None,
            _trace_view_locked=False,
            _sensorgram_downsampling_enabled=True,
            _trace_display_window_s=5.0,
            _plots_frozen=False,
            _selected_trace_metrics=lambda: ["smoothed_max"],
            _primary_trace_metric=lambda: "smoothed_max",
            _peak_history={"smoothed_max": buffer},
            _plot_view_cache=_FakeAbsoluteViewCache(),
        )

        from lspr_app.gui.plot_controller import render_metric_series

        history = {"smoothed_max": [(0.0, 10.0), (1.0, 11.0), (2.0, 12.0)]}
        render_metric_series(window, history, clock_mode=True)
        render_metric_series(window, history, clock_mode=True)

        self.assertEqual(curve.set_data_calls, 1)
        self.assertIsNotNone(curve.data)
        self.assertListEqual(curve.data[0].tolist(), [0.0, 1.0, 2.0])
        self.assertListEqual(curve.data[1].tolist(), [10.0, 11.0, 12.0])

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

        render_metric_series(window, history, clock_mode=False)

        x_values, y_values = window.trace_curves["smoothed_max"].data
        self.assertEqual(len(x_values), 100)
        self.assertListEqual(x_values[:3].tolist(), [0.0, 1.0, 2.0])
        self.assertListEqual(x_values[-3:].tolist(), [97.0, 98.0, 99.0])
        self.assertListEqual(y_values[:3].tolist(), [0.0, 1.0, 2.0])

    def test_downsampling_toggle_can_bypass_reduction(self) -> None:
        x = np.arange(0.0, 1000.0, dtype=np.float64)
        y = np.sin(x / 20.0)

        sampled_x, sampled_y = downsample_metric_series_for_view(
            x,
            y,
            view_width_px=20.0,
            enabled=False,
        )

        self.assertEqual(sampled_x.shape, x.shape)
        self.assertEqual(sampled_y.shape, y.shape)
        self.assertTrue(np.array_equal(sampled_x, x))
        self.assertTrue(np.array_equal(sampled_y, y))

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

        request_metric_autoscale(window)

        self.assertTrue(window._trace_autoscale_timer.started)

    def test_trace_downsampling_prefers_visible_window(self) -> None:
        x = np.arange(0.0, 1000.0, dtype=np.float64)
        y = np.sin(x / 20.0)

        sampled_x, sampled_y = downsample_metric_series_for_view(
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
            _plots_frozen=False,
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

    def test_clear_trace_history_clears_metric_caches(self) -> None:
        class _FakeClearable:
            def __init__(self) -> None:
                self.cleared = False

            def clear(self) -> None:
                self.cleared = True

        plot_view_cache = _FakeClearable()
        display_cache = _FakeClearable()
        state_cache = _FakeClearable()
        status_messages: list[str] = []
        window = SimpleNamespace(
            _metric_render_display_cache=display_cache,
            _metric_render_state_cache=state_cache,
            _plot_view_cache=plot_view_cache,
            _last_metric_autoscale_range=(0.0, 10.0),
            _session=SimpleNamespace(state=SimpleNamespace(absorbance=None, sample=None)),
            _get_analysis_processed_spectrum=lambda signal: (signal, None),
            _metric_reference_processed=object(),
            status_label=SimpleNamespace(setText=lambda text: status_messages.append(text)),
        )

        clear_trace_history_for(window)

        self.assertTrue(plot_view_cache.cleared)
        self.assertTrue(display_cache.cleared)
        self.assertTrue(state_cache.cleared)
        self.assertIsNone(window._last_metric_autoscale_range)
        self.assertIsNone(window._metric_reference_processed)
        self.assertTrue(status_messages)
        self.assertIn("Metric history cleared", status_messages[-1])

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
            spectrum_tracking_mode="smoothed_max",
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
