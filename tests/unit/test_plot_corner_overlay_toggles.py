"""Coverage for the spectrum/sensorgram cursor-label click-to-toggle behavior
added when their stats/cursor rows moved into a top-left corner overlay on
each plot (see main_window_plotting.py's build_*_corner_overlay_for /
toggle_*_cursor_enabled_for, and plot_controller.py's
_spectrum_cursor_display_text / _trace_cursor_display_text).

These are pure-logic checks against fake window objects - no real Qt widgets
needed, since toggle_*_cursor_enabled_for degrades gracefully (via getattr
None-checks) when the corner-overlay attributes it optionally repositions
aren't present.
"""
from __future__ import annotations

import unittest

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[2] / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window_plotting import (
    _CURSOR_OFF_TEXT,
    _STATS_OFF_TEXT,
    _sensorgram_overlay_top_margin_px,
    _show_off_state,
    toggle_spectrum_cursor_enabled_for,
    toggle_spectrum_stats_enabled_for,
    toggle_trace_cursor_enabled_for,
    toggle_trace_stats_enabled_for,
)
from lspr_app.gui.plot_controller import (
    _spectrum_cursor_display_text,
    _trace_cursor_display_text,
    update_metric_stats,
    update_spectrum_stats,
)


class _FakeLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


class _FakePixmapLabel:
    """Like _FakeLabel, but also records setPixmap calls so tests can verify
    the icon-while-disabled path (see _show_off_state in
    main_window_plotting.py) without needing a real QApplication/QIcon.
    """

    def __init__(self) -> None:
        self.text = ""
        self.pixmap = None

    def setText(self, text: str) -> None:
        self.text = text
        self.pixmap = None

    def setPixmap(self, pixmap) -> None:
        self.pixmap = pixmap
        self.text = ""

    def fontMetrics(self):
        return type("_FM", (), {"height": lambda _self: 14})()


class _FakeCrosshairLine:
    def __init__(self) -> None:
        self.visible = True

    def setVisible(self, visible: bool) -> None:
        self.visible = bool(visible)


class _FakeIcon:
    """Stand-in for the real crosshair_cursor_icon() QIcon - records the
    requested pixmap size instead of touching real Qt/QApplication state,
    which this pure-logic test file deliberately avoids (see module
    docstring)."""

    def pixmap(self, width: int, height: int):
        return ("pixmap", width, height)


def _make_window(**extra):
    window = type("_FakeWindow", (), {})()
    window.spectrum_cursor_label = _FakeLabel()
    window.trace_cursor_label = _FakeLabel()
    window._spectrum_cursor_text = "500.000 nm, 123.456"
    window._trace_cursor_text = "12.3 s, 603.210 nm"
    window.spectrum_vline = _FakeCrosshairLine()
    window.spectrum_hline = _FakeCrosshairLine()
    window.trace_vline = _FakeCrosshairLine()
    window.trace_hline = _FakeCrosshairLine()
    for key, value in extra.items():
        setattr(window, key, value)
    return window


class SpectrumCursorToggleTests(unittest.TestCase):
    def test_defaults_to_enabled_and_shows_live_text(self) -> None:
        window = _make_window()
        self.assertEqual(_spectrum_cursor_display_text(window), window._spectrum_cursor_text)

    def test_toggle_off_shows_placeholder(self) -> None:
        window = _make_window()
        toggle_spectrum_cursor_enabled_for(window)
        self.assertFalse(window._spectrum_cursor_enabled)
        self.assertEqual(window.spectrum_cursor_label.text, "cursor: off (click to enable)")

    def test_toggle_twice_returns_to_live_text(self) -> None:
        window = _make_window()
        toggle_spectrum_cursor_enabled_for(window)
        toggle_spectrum_cursor_enabled_for(window)
        self.assertTrue(window._spectrum_cursor_enabled)
        self.assertEqual(window.spectrum_cursor_label.text, window._spectrum_cursor_text)

    def test_disabled_cursor_is_not_overwritten_by_stats_refresh(self) -> None:
        # update_spectrum_stats normally copies _spectrum_cursor_text into the
        # label on every refresh (e.g. after a new frame arrives) - while
        # disabled, _spectrum_cursor_display_text returns None and the
        # refresh must skip touching the label entirely, so it keeps
        # whatever toggle_spectrum_cursor_enabled_for last put there (the
        # off-placeholder here, or a crosshair icon in the real app) instead
        # of being clobbered every frame.
        window = _make_window(
            _get_analysis_metrics=lambda *_a, **_k: {},
            _current_processing_settings=lambda: type("S", (), {"spectrum_tracking_mode": "smoothed_max"})(),
            _compute_peak_metric_nm=lambda *_a, **_k: 500.0,
            _compute_centroid_nm=lambda *_a, **_k: 500.0,
        )
        window.spectrum_stats_label = _FakeLabel()
        toggle_spectrum_cursor_enabled_for(window)

        update_spectrum_stats(window, processed=None, fit=None)

        self.assertEqual(window.spectrum_cursor_label.text, "cursor: off (click to enable)")

    def test_disabled_cursor_icon_is_not_overwritten_by_stats_refresh(self) -> None:
        # Same as above, but with a real icon precomputed (as
        # build_spectrum_corner_overlay_for does at startup) and a label that
        # actually supports setPixmap - the stats refresh must leave the icon
        # in place rather than replacing it with text.
        window = _make_window(
            _get_analysis_metrics=lambda *_a, **_k: {},
            _current_processing_settings=lambda: type("S", (), {"spectrum_tracking_mode": "smoothed_max"})(),
            _compute_peak_metric_nm=lambda *_a, **_k: 500.0,
            _compute_centroid_nm=lambda *_a, **_k: 500.0,
            spectrum_cursor_label=_FakePixmapLabel(),
            _spectrum_cursor_off_icon=_FakeIcon(),
        )
        window.spectrum_stats_label = _FakeLabel()
        toggle_spectrum_cursor_enabled_for(window)
        self.assertEqual(window.spectrum_cursor_label.pixmap, ("pixmap", 14, 14))

        update_spectrum_stats(window, processed=None, fit=None)

        self.assertEqual(window.spectrum_cursor_label.pixmap, ("pixmap", 14, 14))
        self.assertEqual(window.spectrum_cursor_label.text, "")


class SpectrumStatsColorConsistencyTests(unittest.TestCase):
    """spectrum_stats_label shows every peak-position metric currently
    selected/visible on the sensorgram (not just the "primary" tracked mode
    plus a hardcoded centroid line), each tinted to match its own sensorgram
    trace color (TRACE_METRIC_COLORS / secondary_axis_color_for) - see the
    maintainer's reports: first that P_Max (spectrum) and S_Max (sensorgram)
    looked like unrelated numbers by default (spectrum_tracking_mode's
    default disagreed with trace_metrics' own default), then that S_Max was
    simply missing from the spectrum panel whenever the primary tracking
    mode was set to something else."""

    def _make_stats_window(self, *, peak_mode: str, selected_metrics: list[str] | None = None):
        window = type("_FakeWindow", (), {})()
        window.spectrum_stats_label = _FakeLabel()
        window.spectrum_cursor_label = _FakeLabel()
        window._spectrum_cursor_enabled = True
        window._spectrum_cursor_text = "500.000 nm, 1.0"
        window.TRACE_METRIC_COLORS = {
            "smoothed_max": "#0072B2",
            "poly_max": "#E69F00",
            "centroid": "#009E73",
            "gaussian_center": "#D55E00",
        }
        window.TRACE_METRIC_SHORT_LABELS = {
            "smoothed_max": "S_Max",
            "poly_max": "P_Max",
            "centroid": "Cent",
            "gaussian_center": "G_Ctr",
        }
        window._current_processing_settings = lambda: type("S", (), {"spectrum_tracking_mode": peak_mode})()
        window._get_analysis_metrics = lambda *_a, **_k: {
            "smoothed_max": 605.1,
            "poly_max": 605.234,
            "centroid": 604.9,
            "gaussian_center": 605.05,
            "fit_r": 0.9999,
            "mse": 12.3,
            "snr": 2.7,
            "fwhm": "47.1 nm",
        }
        window._compute_metric_nm = lambda mode, *_a, **_k: {
            "smoothed_max": 605.1,
            "poly_max": 605.234,
            "centroid": 604.9,
            "gaussian_center": 605.05,
        }[mode]
        window._selected_trace_metrics = lambda: list(selected_metrics or ["smoothed_max", "centroid"])
        return window

    def test_smoothed_max_line_uses_the_sensorgrams_smoothed_max_color(self) -> None:
        window = self._make_stats_window(peak_mode="smoothed_max")

        update_spectrum_stats(window, processed=object(), fit=None)

        text = window.spectrum_stats_label.text
        self.assertIn("#0072B2", text)  # smoothed_max's trace color
        self.assertIn("S_Max", text)

    def test_poly_max_line_uses_the_sensorgrams_poly_max_color_not_smoothed_max(self) -> None:
        window = self._make_stats_window(peak_mode="poly_max", selected_metrics=["poly_max", "centroid"])

        update_spectrum_stats(window, processed=object(), fit=None)

        text = window.spectrum_stats_label.text
        self.assertIn("#E69F00", text)  # poly_max's own trace color
        self.assertIn("P_Max", text)

    def test_smoothed_max_still_shows_even_when_a_different_mode_is_primary(self) -> None:
        # The exact regression reported: primary tracking mode set to
        # poly_max, but S_Max is still selected/visible on the sensorgram
        # (the default) - it must still appear in the spectrum stats box.
        window = self._make_stats_window(peak_mode="poly_max", selected_metrics=["smoothed_max", "centroid"])

        update_spectrum_stats(window, processed=object(), fit=None)

        text = window.spectrum_stats_label.text
        self.assertIn("S_Max", text)
        self.assertIn("#0072B2", text)  # smoothed_max's own color, not poly_max's
        self.assertIn("P_Max", text)  # primary mode is still included too

    def test_centroid_and_fwhm_lines_are_colored_independently_of_peak_mode(self) -> None:
        window = self._make_stats_window(peak_mode="poly_max", selected_metrics=["poly_max", "centroid"])

        update_spectrum_stats(window, processed=object(), fit=None)

        text = window.spectrum_stats_label.text
        self.assertIn("#009E73", text)  # centroid's trace color
        self.assertIn("#CC79A7", text)  # fwhm's default secondary-axis color

    def test_mse_r_snr_stay_uncolored(self) -> None:
        window = self._make_stats_window(peak_mode="smoothed_max")

        update_spectrum_stats(window, processed=object(), fit=None)

        text = window.spectrum_stats_label.text
        self.assertIn("MSE: 12.3", text)
        self.assertIn("R: 0.9999", text)
        self.assertIn("S/N: 2.7", text)


class TraceCursorToggleTests(unittest.TestCase):
    def test_defaults_to_enabled_and_shows_live_text(self) -> None:
        window = _make_window()
        self.assertEqual(_trace_cursor_display_text(window), window._trace_cursor_text)

    def test_toggle_off_shows_placeholder(self) -> None:
        window = _make_window()
        toggle_trace_cursor_enabled_for(window)
        self.assertFalse(window._trace_cursor_enabled)
        self.assertEqual(window.trace_cursor_label.text, "cursor: off (click to enable)")

    def test_toggle_twice_returns_to_live_text(self) -> None:
        window = _make_window()
        toggle_trace_cursor_enabled_for(window)
        toggle_trace_cursor_enabled_for(window)
        self.assertTrue(window._trace_cursor_enabled)
        self.assertEqual(window.trace_cursor_label.text, window._trace_cursor_text)

    def test_disabled_cursor_is_not_overwritten_by_stats_refresh_with_no_series(self) -> None:
        window = _make_window(
            _active_trace_series=lambda: {},
            _trace_stats_metric=lambda: "smoothed_max",
            TRACE_METRIC_SHORT_LABELS={"smoothed_max": "S_Max"},
            TRACE_METRIC_COLORS={"smoothed_max": "#444444"},
            trace_curves={},
        )
        window.trace_stats_label = _FakeLabel()
        window.trace_noise_summary_label = _FakeLabel()
        toggle_trace_cursor_enabled_for(window)

        update_metric_stats(window)

        self.assertEqual(window.trace_cursor_label.text, "cursor: off (click to enable)")

    def test_disabled_cursor_icon_is_not_overwritten_by_stats_refresh(self) -> None:
        window = _make_window(
            _active_trace_series=lambda: {},
            _trace_stats_metric=lambda: "smoothed_max",
            TRACE_METRIC_SHORT_LABELS={"smoothed_max": "S_Max"},
            TRACE_METRIC_COLORS={"smoothed_max": "#444444"},
            trace_curves={},
            trace_cursor_label=_FakePixmapLabel(),
            _trace_cursor_off_icon=_FakeIcon(),
        )
        window.trace_stats_label = _FakeLabel()
        window.trace_noise_summary_label = _FakeLabel()
        toggle_trace_cursor_enabled_for(window)
        self.assertEqual(window.trace_cursor_label.pixmap, ("pixmap", 14, 14))

        update_metric_stats(window)

        self.assertEqual(window.trace_cursor_label.pixmap, ("pixmap", 14, 14))
        self.assertEqual(window.trace_cursor_label.text, "")


class CursorOffIconTests(unittest.TestCase):
    """The cursor-readout label collapses to a small crosshair icon while
    disabled (instead of a "click to enable" sentence), and returns to plain
    "X, Y" text (no "cursor:" prefix) when re-enabled - see
    _show_off_state and toggle_*_cursor_enabled_for in
    main_window_plotting.py.
    """

    def test_show_off_state_sets_pixmap_when_icon_and_label_support_it(self) -> None:
        label = _FakePixmapLabel()
        _show_off_state(label, _FakeIcon(), _CURSOR_OFF_TEXT)
        self.assertEqual(label.pixmap, ("pixmap", 14, 14))
        self.assertEqual(label.text, "")

    def test_show_off_state_falls_back_to_text_without_an_icon(self) -> None:
        label = _FakePixmapLabel()
        _show_off_state(label, None, _CURSOR_OFF_TEXT)
        self.assertEqual(label.text, "cursor: off (click to enable)")

    def test_show_off_state_falls_back_to_text_when_label_cant_take_a_pixmap(self) -> None:
        label = _FakeLabel()
        _show_off_state(label, _FakeIcon(), _CURSOR_OFF_TEXT)
        self.assertEqual(label.text, "cursor: off (click to enable)")

    def test_toggle_spectrum_off_shows_icon_when_precomputed(self) -> None:
        window = _make_window(
            spectrum_cursor_label=_FakePixmapLabel(),
            _spectrum_cursor_off_icon=_FakeIcon(),
        )
        toggle_spectrum_cursor_enabled_for(window)
        self.assertEqual(window.spectrum_cursor_label.pixmap, ("pixmap", 14, 14))

    def test_toggle_spectrum_back_on_shows_plain_text_no_cursor_prefix(self) -> None:
        window = _make_window(
            spectrum_cursor_label=_FakePixmapLabel(),
            _spectrum_cursor_off_icon=_FakeIcon(),
        )
        toggle_spectrum_cursor_enabled_for(window)
        toggle_spectrum_cursor_enabled_for(window)
        self.assertEqual(window.spectrum_cursor_label.text, "500.000 nm, 123.456")
        self.assertIsNone(window.spectrum_cursor_label.pixmap)

    def test_toggle_trace_off_shows_icon_when_precomputed(self) -> None:
        window = _make_window(
            trace_cursor_label=_FakePixmapLabel(),
            _trace_cursor_off_icon=_FakeIcon(),
        )
        toggle_trace_cursor_enabled_for(window)
        self.assertEqual(window.trace_cursor_label.pixmap, ("pixmap", 14, 14))

    def test_toggle_trace_back_on_shows_plain_text_no_cursor_prefix(self) -> None:
        window = _make_window(
            trace_cursor_label=_FakePixmapLabel(),
            _trace_cursor_off_icon=_FakeIcon(),
        )
        toggle_trace_cursor_enabled_for(window)
        toggle_trace_cursor_enabled_for(window)
        self.assertEqual(window.trace_cursor_label.text, "12.3 s, 603.210 nm")
        self.assertIsNone(window.trace_cursor_label.pixmap)


class StatsOffIconTests(unittest.TestCase):
    """Double-clicking a plot's stats box hides it behind a small list icon
    (instead of leaving the panel up), and restores its last-known content
    when double-clicked again - see toggle_spectrum_stats_enabled_for /
    toggle_trace_stats_enabled_for (main_window_plotting.py) and
    _sync_spectrum_stats_label / _sync_trace_stats_label (plot_controller.py),
    which remember the latest computed HTML so it doesn't need to wait for
    another frame to reappear.
    """

    def _make_stats_toggle_window(self, **extra):
        window = type("_FakeWindow", (), {})()
        window.spectrum_stats_label = _FakePixmapLabel()
        window.trace_stats_label = _FakePixmapLabel()
        window.spectrum_cursor_label = _FakeLabel()
        window.trace_cursor_label = _FakeLabel()
        window._spectrum_cursor_text = "500.000 nm, 1.0"
        window._trace_cursor_text = "12.3 s, 603.210 nm"
        for key, value in extra.items():
            setattr(window, key, value)
        return window

    def test_toggle_spectrum_stats_off_shows_icon_when_precomputed(self) -> None:
        window = self._make_stats_toggle_window(_spectrum_stats_off_icon=_FakeIcon())

        toggle_spectrum_stats_enabled_for(window)

        self.assertFalse(window._spectrum_stats_enabled)
        self.assertEqual(window.spectrum_stats_label.pixmap, ("pixmap", 14, 14))

    def test_toggle_spectrum_stats_back_on_restores_last_html(self) -> None:
        window = self._make_stats_toggle_window(
            _spectrum_stats_off_icon=_FakeIcon(),
            _spectrum_stats_html="<b>S_Max</b>: 605.123 nm",
        )
        toggle_spectrum_stats_enabled_for(window)

        toggle_spectrum_stats_enabled_for(window)

        self.assertTrue(window._spectrum_stats_enabled)
        self.assertEqual(window.spectrum_stats_label.text, "<b>S_Max</b>: 605.123 nm")
        self.assertIsNone(window.spectrum_stats_label.pixmap)

    def test_toggle_trace_stats_off_shows_icon_when_precomputed(self) -> None:
        window = self._make_stats_toggle_window(_trace_stats_off_icon=_FakeIcon())

        toggle_trace_stats_enabled_for(window)

        self.assertFalse(window._trace_stats_enabled)
        self.assertEqual(window.trace_stats_label.pixmap, ("pixmap", 14, 14))

    def test_toggle_trace_stats_back_on_restores_last_html(self) -> None:
        window = self._make_stats_toggle_window(
            _trace_stats_off_icon=_FakeIcon(),
            _trace_stats_html="S_Max: 12.3 s, 605.123 nm",
        )
        toggle_trace_stats_enabled_for(window)

        toggle_trace_stats_enabled_for(window)

        self.assertTrue(window._trace_stats_enabled)
        self.assertEqual(window.trace_stats_label.text, "S_Max: 12.3 s, 605.123 nm")
        self.assertIsNone(window.trace_stats_label.pixmap)

    def test_falls_back_to_text_placeholder_without_a_precomputed_icon(self) -> None:
        window = self._make_stats_toggle_window()

        toggle_spectrum_stats_enabled_for(window)

        self.assertEqual(window.spectrum_stats_label.text, _STATS_OFF_TEXT)

    def test_disabled_spectrum_stats_icon_is_not_overwritten_by_refresh(self) -> None:
        window = self._make_stats_toggle_window(
            _spectrum_stats_off_icon=_FakeIcon(),
            _get_analysis_metrics=lambda *_a, **_k: {},
            _current_processing_settings=lambda: type("S", (), {"spectrum_tracking_mode": "smoothed_max"})(),
        )
        toggle_spectrum_stats_enabled_for(window)

        update_spectrum_stats(window, processed=None, fit=None)

        self.assertEqual(window.spectrum_stats_label.pixmap, ("pixmap", 14, 14))

    def test_disabled_trace_stats_icon_is_not_overwritten_by_refresh(self) -> None:
        window = self._make_stats_toggle_window(
            _trace_stats_off_icon=_FakeIcon(),
            _active_trace_series=lambda: {},
            _trace_stats_metric=lambda: "smoothed_max",
            TRACE_METRIC_SHORT_LABELS={"smoothed_max": "S_Max"},
            TRACE_METRIC_COLORS={"smoothed_max": "#444444"},
            trace_curves={},
        )
        window.trace_noise_summary_label = _FakeLabel()
        toggle_trace_stats_enabled_for(window)

        update_metric_stats(window)

        self.assertEqual(window.trace_stats_label.pixmap, ("pixmap", 14, 14))


class CrosshairVisibilityTests(unittest.TestCase):
    """Toggling a plot's cursor readout off also hides its crosshair lines,
    and back on restores them - see toggle_*_cursor_enabled_for.
    """

    def test_toggling_spectrum_cursor_off_hides_the_crosshair(self) -> None:
        window = _make_window()
        toggle_spectrum_cursor_enabled_for(window)
        self.assertFalse(window.spectrum_vline.visible)
        self.assertFalse(window.spectrum_hline.visible)

    def test_toggling_spectrum_cursor_back_on_restores_the_crosshair(self) -> None:
        window = _make_window()
        toggle_spectrum_cursor_enabled_for(window)
        toggle_spectrum_cursor_enabled_for(window)
        self.assertTrue(window.spectrum_vline.visible)
        self.assertTrue(window.spectrum_hline.visible)

    def test_toggling_trace_cursor_off_hides_the_crosshair(self) -> None:
        window = _make_window()
        toggle_trace_cursor_enabled_for(window)
        self.assertFalse(window.trace_vline.visible)
        self.assertFalse(window.trace_hline.visible)

    def test_toggling_trace_cursor_back_on_restores_the_crosshair(self) -> None:
        window = _make_window()
        toggle_trace_cursor_enabled_for(window)
        toggle_trace_cursor_enabled_for(window)
        self.assertTrue(window.trace_vline.visible)
        self.assertTrue(window.trace_hline.visible)


class SensorgramOverlayTopMarginTests(unittest.TestCase):
    """The sensorgram's corner overlays shift down to clear the
    experiment-control-step timeline bar when it's shown at the top of the
    plot, and sit at the very top otherwise - see
    _sensorgram_overlay_top_margin_px.
    """

    def test_no_margin_when_bar_disabled(self) -> None:
        window = _make_window(
            _sensorgram_control_step_overlay_enabled=False,
            _sensorgram_control_step_overlay_position="top",
            _sensorgram_control_step_overlay_bar_height_px=18,
        )
        self.assertEqual(_sensorgram_overlay_top_margin_px(window), 0)

    def test_no_margin_when_bar_is_at_the_bottom(self) -> None:
        window = _make_window(
            _sensorgram_control_step_overlay_enabled=True,
            _sensorgram_control_step_overlay_position="bottom",
            _sensorgram_control_step_overlay_bar_height_px=18,
        )
        self.assertEqual(_sensorgram_overlay_top_margin_px(window), 0)

    def test_margin_added_when_bar_is_enabled_at_the_top(self) -> None:
        window = _make_window(
            _sensorgram_control_step_overlay_enabled=True,
            _sensorgram_control_step_overlay_position="top",
            _sensorgram_control_step_overlay_bar_height_px=18,
        )
        self.assertEqual(_sensorgram_overlay_top_margin_px(window), 24)

    def test_margin_scales_with_configured_bar_height(self) -> None:
        window = _make_window(
            _sensorgram_control_step_overlay_enabled=True,
            _sensorgram_control_step_overlay_position="top",
            _sensorgram_control_step_overlay_bar_height_px=40,
        )
        self.assertEqual(_sensorgram_overlay_top_margin_px(window), 46)


if __name__ == "__main__":
    unittest.main()
