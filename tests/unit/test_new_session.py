"""Unit coverage for File > New Session's _start_new_session_for
(gui/main_window_new_session.py).

Regression coverage for: starting a new session while live acquisition was
still running used to leave window._live_trace_started_at untouched, so the
next session file's t=0 anchor (ensure_session_writer, see
storage/measurement_archive.py) inherited the *original* Play-click
timestamp instead of resetting to "now" - the sensorgram kept counting up
from the old run instead of restarting at 0. See
docs/sensorgram_improvements.md and gui/sensorgram_time_anchor.py.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import SessionState, Spectrum
from lspr_app.gui.main_window_new_session import _start_new_session_for


def _make_spectrum() -> Spectrum:
    import numpy as np

    return Spectrum(
        wavelengths_nm=np.array([400.0, 500.0]),
        values=np.array([0.1, 0.2]),
        y_label="Sample",
        acquired_at=datetime.now(timezone.utc),
    )


def _make_window(**overrides) -> SimpleNamespace:
    session = SimpleNamespace(state=SessionState(dark=_make_spectrum(), reference=_make_spectrum()))
    window = SimpleNamespace(
        _session=session,
        _live_trace_started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        _update_dark_reference_button_icons=lambda: None,
        _refresh_plot=lambda: None,
        _log_success=lambda message: None,
        status_label=SimpleNamespace(setText=lambda text: None),
    )
    for key, value in overrides.items():
        setattr(window, key, value)
    return window


class StartNewSessionTests(unittest.TestCase):
    def test_clears_live_trace_started_at_so_the_next_session_anchors_to_now(self) -> None:
        window = _make_window()
        self.assertIsNotNone(window._live_trace_started_at)

        with patch("lspr_app.storage.app_config.save_dark_reference_cache"), patch(
            "lspr_app.gui.main_window_plotting.clear_trace_history_for"
        ):
            _start_new_session_for(window, keep_dark=True, keep_reference=True)

        self.assertIsNone(window._live_trace_started_at)

    def test_clears_live_trace_started_at_even_when_dark_and_reference_are_discarded(self) -> None:
        window = _make_window()

        with patch("lspr_app.storage.app_config.save_dark_reference_cache"), patch(
            "lspr_app.gui.main_window_plotting.clear_trace_history_for"
        ):
            _start_new_session_for(window, keep_dark=False, keep_reference=False)

        self.assertIsNone(window._live_trace_started_at)
        self.assertIsNone(window._session.state.dark)
        self.assertIsNone(window._session.state.reference)

    def test_keeps_dark_and_reference_when_requested(self) -> None:
        window = _make_window()
        original_dark = window._session.state.dark
        original_reference = window._session.state.reference

        with patch("lspr_app.storage.app_config.save_dark_reference_cache"), patch(
            "lspr_app.gui.main_window_plotting.clear_trace_history_for"
        ):
            _start_new_session_for(window, keep_dark=True, keep_reference=True)

        self.assertIs(window._session.state.dark, original_dark)
        self.assertIs(window._session.state.reference, original_reference)


if __name__ == "__main__":
    unittest.main()
