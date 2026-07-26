"""Sensorgram time-axis mode coverage: the elapsed (HH:MM:SS) / seconds
(plain relative seconds) / clock (absolute local HH:MM:SS) three-way toggle.

Pure-logic tests only - no Qt objects are constructed. apply/toggle read
their target widgets through getattr(..., None)/hasattr() guards, so a
SimpleNamespace stand-in for the window (with plain stub axis/plot objects)
exercises the same code path a real MainWindow would, without needing a
QApplication.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window_sensorgram import (
    SENSORGRAM_TIME_AXIS_MODES,
    apply_sensorgram_time_axis_mode,
    normalize_sensorgram_time_axis_mode,
    sensorgram_time_axis_label_text,
    toggle_sensorgram_time_axis_mode,
)
from lspr_app.gui.plot_controller import _sensorgram_format_time_value


class _FakeAxis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def set_time_mode(self, mode: str, *, start_datetime=None) -> None:
        self.calls.append((mode, start_datetime))


class _FakePlot:
    def __init__(self) -> None:
        self.labels: dict[str, str] = {}

    def setLabel(self, axis: str, text: str) -> None:
        self.labels[axis] = text


def _make_window(**overrides) -> SimpleNamespace:
    window = SimpleNamespace(
        _sensorgram_time_axis_mode="elapsed",
        trace_time_axis=_FakeAxis(),
        trace_plot=_FakePlot(),
        _schedule_ui_state_persist=lambda: None,
    )
    for key, value in overrides.items():
        setattr(window, key, value)
    return window


class NormalizeModeTests(unittest.TestCase):
    def test_valid_modes_pass_through(self) -> None:
        for mode in SENSORGRAM_TIME_AXIS_MODES:
            self.assertEqual(normalize_sensorgram_time_axis_mode(mode), mode)

    def test_unknown_or_missing_falls_back_to_elapsed(self) -> None:
        self.assertEqual(normalize_sensorgram_time_axis_mode("bogus"), "elapsed")
        self.assertEqual(normalize_sensorgram_time_axis_mode(None), "elapsed")
        self.assertEqual(normalize_sensorgram_time_axis_mode(""), "elapsed")
        self.assertEqual(normalize_sensorgram_time_axis_mode(123), "elapsed")

    def test_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(normalize_sensorgram_time_axis_mode(" Clock \n"), "clock")
        self.assertEqual(normalize_sensorgram_time_axis_mode("SECONDS"), "seconds")


class LabelTextTests(unittest.TestCase):
    def test_every_mode_states_its_units(self) -> None:
        self.assertIn("HH:MM:SS", sensorgram_time_axis_label_text("elapsed"))
        self.assertIn("(s)", sensorgram_time_axis_label_text("seconds"))
        self.assertIn("HH:MM:SS", sensorgram_time_axis_label_text("clock"))
        self.assertIn("local", sensorgram_time_axis_label_text("clock"))

    def test_labels_are_distinct_per_mode(self) -> None:
        labels = {sensorgram_time_axis_label_text(mode) for mode in SENSORGRAM_TIME_AXIS_MODES}
        self.assertEqual(len(labels), len(SENSORGRAM_TIME_AXIS_MODES))


class ApplyModeTests(unittest.TestCase):
    def test_apply_sets_axis_and_label_for_seconds_mode(self) -> None:
        window = _make_window(_sensorgram_time_axis_mode="seconds")
        apply_sensorgram_time_axis_mode(window)

        self.assertEqual(window.trace_time_axis.calls, [("seconds", None)])
        self.assertEqual(window.trace_plot.labels["bottom"], "Elapsed time (s)")

    def test_apply_normalizes_invalid_stored_mode(self) -> None:
        window = _make_window(_sensorgram_time_axis_mode="not-a-real-mode")
        apply_sensorgram_time_axis_mode(window)

        self.assertEqual(window._sensorgram_time_axis_mode, "elapsed")

    def test_apply_works_without_axis_or_plot_widgets(self) -> None:
        # Guards against a widget not having been constructed yet - matches
        # how MainWindow.__init__ calls this before trace_plot exists.
        window = SimpleNamespace(_sensorgram_time_axis_mode="clock")
        apply_sensorgram_time_axis_mode(window)  # must not raise


class ToggleModeTests(unittest.TestCase):
    def test_cycles_elapsed_to_seconds_to_clock_and_back(self) -> None:
        window = _make_window(_sensorgram_time_axis_mode="elapsed")

        toggle_sensorgram_time_axis_mode(window)
        self.assertEqual(window._sensorgram_time_axis_mode, "seconds")

        toggle_sensorgram_time_axis_mode(window)
        self.assertEqual(window._sensorgram_time_axis_mode, "clock")

        toggle_sensorgram_time_axis_mode(window)
        self.assertEqual(window._sensorgram_time_axis_mode, "elapsed")

    def test_toggle_persists_ui_state(self) -> None:
        persist_calls: list[int] = []
        window = _make_window(_schedule_ui_state_persist=lambda: persist_calls.append(1))
        toggle_sensorgram_time_axis_mode(window)
        self.assertEqual(persist_calls, [1])

    def test_toggle_from_invalid_stored_mode_starts_the_cycle_over(self) -> None:
        window = _make_window(_sensorgram_time_axis_mode="corrupted")
        toggle_sensorgram_time_axis_mode(window)
        self.assertEqual(window._sensorgram_time_axis_mode, "seconds")


class FormatTimeValueTests(unittest.TestCase):
    def test_elapsed_formats_as_hhmmss(self) -> None:
        window = _make_window()
        self.assertEqual(_sensorgram_format_time_value(window, "elapsed", 3725.0), "01:02:05")

    def test_seconds_formats_as_plain_number(self) -> None:
        window = _make_window()
        self.assertEqual(_sensorgram_format_time_value(window, "seconds", 3725.4), "3725 s")

    def test_clock_without_start_datetime_falls_back_to_elapsed(self) -> None:
        window = _make_window()
        self.assertEqual(_sensorgram_format_time_value(window, "clock", 65.0), "00:01:05")


if __name__ == "__main__":
    unittest.main()
