"""Regression test for the actual root cause behind "a floating panel that
was visible when the app closed comes back hidden": every panel's own
visibilityChanged signal is wired to on_panel_visibility_changed(), which
calls LayoutStateController.save_layout_preferences() - including when Qt
itself fires that signal while hiding every dock widget as part of the real
window teardown (super().closeEvent()), which runs *after* MainWindow's own
explicit, correct save. Unguarded, that teardown cascade re-saves
"everything hidden" (an artifact of the window closing, not a real layout
choice) right on top of the correct snapshot moments earlier - so the saved
truth was wrong before the app ever restarted, regardless of how good the
restore-side logic was. MainWindow.closeEvent() now sets
self._suspend_layout_save = True immediately after its own save, so this
guard (already used by set_all_panel_visibility() for the same class of
problem) also covers the window's own close-time cascade.

This only exercises the guard itself (LayoutStateController.save_layout_preferences
respecting window._suspend_layout_save) - closeEvent() is a full MainWindow
method too heavy to construct here.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.layout_state_controller import LayoutStateController  # noqa: E402


class TestSaveLayoutPreferencesRespectsSuspendGuard(unittest.TestCase):
    def test_suspended_save_writes_nothing(self) -> None:
        window = mock.MagicMock()
        window._layout_preferences_ready = True
        window._suspend_layout_save = True
        controller = LayoutStateController(window)

        controller.save_layout_preferences()

        window._settings.setValue.assert_not_called()

    def test_unsuspended_save_proceeds(self) -> None:
        window = mock.MagicMock()
        window._layout_preferences_ready = True
        window._suspend_layout_save = False
        controller = LayoutStateController(window)

        controller.save_layout_preferences()

        self.assertTrue(window._settings.setValue.called)

    def test_not_ready_yet_also_writes_nothing_even_if_not_suspended(self) -> None:
        window = mock.MagicMock()
        window._layout_preferences_ready = False
        window._suspend_layout_save = False
        controller = LayoutStateController(window)

        controller.save_layout_preferences()

        window._settings.setValue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
