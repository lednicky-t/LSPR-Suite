"""Coverage for MainWindow._update_selection_dependent_plots(prompt_live_
preview=True): the live-preview prompt (_handle_live_preview_selection_
change, which can open a modal QMessageBox) must be scheduled via
QTimer.singleShot(0, ...), never called synchronously.

Every caller of _update_selection_dependent_plots with prompt_live_preview=
True runs from inside a mouse press/release handler
(image_interaction_controller.py's eventFilter). Opening a modal dialog's
nested event loop while Qt is still mid-delivery of that same mouse event is
a known Qt/PyQt trap: the framework can still see the mouse button as held
once the dialog closes, so the next click reads as a drag continuation
instead of a clean new press - this is what left the ROI rubber-band
selection stuck on the image view after the prompt appeared. Deferring one
event-loop tick avoids it; this test guards against that fix regressing back
to a direct, synchronous call.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.main_window import MainWindow


class TestLivePreviewPromptDeferred(unittest.TestCase):
    def _make_window(self, *, live_preview_enabled: bool = True) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window._selected_roi_ids = {1, 2}
        window._selection_plot_highlight_signature = None
        window._analysis_live_preview_enabled = live_preview_enabled
        window._refresh_visible_spectrum_from_cache = Mock()
        window._analysis_controller = SimpleNamespace(
            update_selection_highlight=Mock(),
            schedule_cube_slider_cache_refresh=Mock(),
            # _update_selection_dependent_plots also unconditionally
            # consults the sensorgram cache now (same treatment as
            # _refresh_visible_spectrum_from_cache above) - see its own
            # comment for why. Unrelated to what this test file covers
            # (the deferred-prompt scheduling), just needs to exist so the
            # call doesn't raise.
            preview_sensorgram_from_cache=Mock(),
        )
        window._handle_live_preview_selection_change = Mock()
        return window

    def _capture_scheduled_callback(self, window: MainWindow, **kwargs) -> object | None:
        calls: list[tuple[int, object]] = []
        with patch(
            "lspr_imaging_app.gui.main_window.QTimer.singleShot",
            side_effect=lambda delay_ms, callback: calls.append((delay_ms, callback)),
        ):
            window._update_selection_dependent_plots(**kwargs)
        return calls

    def test_prompt_is_scheduled_not_called_synchronously(self) -> None:
        window = self._make_window()
        calls = self._capture_scheduled_callback(window, prompt_live_preview=True)
        window._handle_live_preview_selection_change.assert_not_called()
        self.assertEqual(len(calls), 1)
        delay_ms, callback = calls[0]
        self.assertEqual(delay_ms, 0)
        self.assertIs(callback, window._handle_live_preview_selection_change)

    def test_scheduled_callback_fires_the_real_prompt_handler(self) -> None:
        window = self._make_window()
        _delay_ms, callback = self._capture_scheduled_callback(window, prompt_live_preview=True)[0]
        callback()
        window._handle_live_preview_selection_change.assert_called_once()

    def test_prompt_live_preview_false_schedules_nothing(self) -> None:
        window = self._make_window()
        calls = self._capture_scheduled_callback(window, prompt_live_preview=False)
        self.assertEqual(calls, [])
        window._handle_live_preview_selection_change.assert_not_called()

    def test_force_schedules_nothing_even_with_prompt_live_preview(self) -> None:
        window = self._make_window()
        calls = self._capture_scheduled_callback(window, force=True, prompt_live_preview=True)
        self.assertEqual(calls, [])

    def test_live_preview_disabled_schedules_nothing(self) -> None:
        window = self._make_window(live_preview_enabled=False)
        calls = self._capture_scheduled_callback(window, prompt_live_preview=True)
        self.assertEqual(calls, [])

    def test_unchanged_selection_signature_is_a_no_op(self) -> None:
        # Early-return guard (selected_signature == cached signature) - must
        # still work with the deferred prompt in place.
        window = self._make_window()
        window._selection_plot_highlight_signature = (1, 2)
        calls = self._capture_scheduled_callback(window, prompt_live_preview=True)
        self.assertEqual(calls, [])
        window._refresh_visible_spectrum_from_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
