"""Regression tests for two related restoreState() gaps in
LayoutStateController's floating-panel handling:

1. ensure_floating_panels_on_screen extends the multi-monitor fallback that
   already existed for the main window (best_restore_screen_geometry, see
   test_lspri_layout_screen_restore.py) to floating dock panels.
   window.restoreState() restores each floating QDockWidget's geometry from
   its own opaque blob with no equivalent fallback, so a panel left floating
   on a monitor that's since been unplugged/renamed/reordered would
   otherwise reopen off-screen and be unreachable without dragging the
   whole app window there first.

2. ensure_panel_visibility_restored works around window.restoreState() not
   reliably re-showing a floating dock widget that was visible when the app
   last closed - its saved geometry and floating flag come back correctly,
   but the "show it" step doesn't, leaving the panel hidden until the user
   manually toggles it via the View menu. Each panel's own visibility is
   now tracked independently (layout/panel_visible/<name>) and re-asserted
   after every restoreState() call instead of trusting that bit of the blob.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QByteArray, QRect

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.layout_state_controller import LayoutStateController  # noqa: E402


def _fake_screen(name: str, rect: tuple[int, int, int, int]):
    screen = mock.MagicMock()
    screen.name.return_value = name
    screen.availableGeometry.return_value = QRect(*rect)
    return screen


def _fake_panel(*, floating: bool = False, rect: tuple[int, int, int, int] = (0, 0, 100, 100), hidden: bool = False):
    panel = mock.MagicMock()
    panel.isFloating.return_value = floating
    panel.frameGeometry.return_value = QRect(*rect)
    panel.isHidden.return_value = hidden
    return panel


class _FakeWindow:
    """Duck-typed stand-in exposing only what ensure_floating_panels_on_screen
    reads, so it can be tested without a real MainWindow/QMainWindow."""

    def __init__(self, screen=None) -> None:
        self._screen = screen

    def screen(self):
        return self._screen


class TestEnsureFloatingPanelsOnScreen(unittest.TestCase):
    def test_docked_panel_is_left_alone(self) -> None:
        window = _FakeWindow(screen=_fake_screen("Monitor-A", (0, 0, 1920, 1080)))
        controller = LayoutStateController(window)
        panel = _fake_panel(floating=False, rect=(5000, 5000, 300, 200))
        controller.panel_layout_panels = lambda: [("p", panel)]

        with mock.patch("lspr_imaging_app.gui.layout_state_controller.QGuiApplication") as mock_app:
            mock_app.screens.return_value = [window.screen()]
            controller.ensure_floating_panels_on_screen()

        panel.move.assert_not_called()

    def test_floating_panel_on_a_disconnected_monitor_is_rehomed_onto_the_windows_screen(self) -> None:
        current_screen = _fake_screen("Monitor-A", (0, 0, 1920, 1080))
        window = _FakeWindow(screen=current_screen)
        controller = LayoutStateController(window)
        # Was floating on a second monitor to the right that is now gone.
        panel = _fake_panel(floating=True, rect=(2500, 100, 400, 300))
        controller.panel_layout_panels = lambda: [("p", panel)]

        with mock.patch("lspr_imaging_app.gui.layout_state_controller.QGuiApplication") as mock_app:
            mock_app.screens.return_value = [current_screen]  # only the one screen left
            controller.ensure_floating_panels_on_screen()

        panel.move.assert_called_once()
        x, y = panel.move.call_args.args
        self.assertTrue(0 <= x <= 1920 - 400)
        self.assertTrue(0 <= y <= 1080 - 300)

    def test_floating_panel_still_overlapping_a_connected_screen_is_only_clamped_not_rehomed(self) -> None:
        screen_a = _fake_screen("Monitor-A", (0, 0, 1920, 1080))
        screen_b = _fake_screen("Monitor-B", (1920, 0, 1920, 1080))
        window = _FakeWindow(screen=screen_a)
        controller = LayoutStateController(window)
        # Mostly on screen B, still connected - just slightly past its right edge.
        panel = _fake_panel(floating=True, rect=(3700, 100, 400, 300))
        controller.panel_layout_panels = lambda: [("p", panel)]

        with mock.patch("lspr_imaging_app.gui.layout_state_controller.QGuiApplication") as mock_app:
            mock_app.screens.return_value = [screen_a, screen_b]
            controller.ensure_floating_panels_on_screen()

        panel.move.assert_called_once()
        x, _y = panel.move.call_args.args
        # Clamped to fit inside screen B (x in [1920, 1920 + 1920 - 400]), not
        # re-homed onto screen A just because the window itself lives there.
        self.assertTrue(1920 <= x <= 1920 + 1920 - 400)

    def test_no_connected_screens_is_a_no_op(self) -> None:
        window = _FakeWindow(screen=None)
        controller = LayoutStateController(window)
        panel = _fake_panel(floating=True, rect=(0, 0, 400, 300))
        controller.panel_layout_panels = lambda: [("p", panel)]

        with mock.patch("lspr_imaging_app.gui.layout_state_controller.QGuiApplication") as mock_app:
            mock_app.screens.return_value = []
            controller.ensure_floating_panels_on_screen()

        panel.move.assert_not_called()


class TestRestoreSavedPanelLayoutStateTriggersScreenFix(unittest.TestCase):
    """restore_saved_panel_layout_state() (the method that actually calls
    window.restoreState()) must trigger ensure_floating_panels_on_screen()
    itself, not a caller that only sometimes wraps it - app.py's startup
    flow calls restore_saved_panel_layout_state() a second time, later and
    directly, right before the window is shown (see after_restore_flow in
    apps/LSPRi/eva/src/lspr_imaging_app/app.py). Attaching the on-screen fix
    only to the first, earlier caller left that second call free to
    silently re-apply the raw saved blob - including a floating panel's
    stale, possibly now off-screen geometry - undoing the fix again before
    the window ever became visible."""

    def test_successful_restore_calls_ensure_floating_panels_on_screen(self) -> None:
        window = mock.MagicMock()
        window._dock_layout_built = True
        window._settings.value.return_value = QByteArray(b"fake-blob")
        window.restoreState.return_value = True
        controller = LayoutStateController(window)
        controller.ensure_floating_panels_on_screen = mock.MagicMock()
        controller.ensure_panel_visibility_restored = mock.MagicMock()

        result = controller.restore_saved_panel_layout_state()

        self.assertTrue(result)
        controller.ensure_floating_panels_on_screen.assert_called_once()
        # include_floating=False: this call always happens before the main
        # window itself is shown (see ensure_panel_visibility_restored's own
        # docstring) - showing a floating panel here would pop it up on
        # screen before the main window appears.
        controller.ensure_panel_visibility_restored.assert_called_once_with(include_floating=False)

    def test_failed_restore_does_not_call_ensure_floating_panels_on_screen(self) -> None:
        window = mock.MagicMock()
        window._dock_layout_built = True
        window._settings.value.return_value = QByteArray(b"fake-blob")
        window.restoreState.return_value = False
        controller = LayoutStateController(window)
        controller.ensure_floating_panels_on_screen = mock.MagicMock()

        result = controller.restore_saved_panel_layout_state()

        self.assertFalse(result)
        controller.ensure_floating_panels_on_screen.assert_not_called()

    def test_no_saved_state_does_not_call_ensure_floating_panels_on_screen(self) -> None:
        window = mock.MagicMock()
        window._dock_layout_built = True
        window._settings.value.return_value = None
        controller = LayoutStateController(window)
        controller.ensure_floating_panels_on_screen = mock.MagicMock()

        result = controller.restore_saved_panel_layout_state()

        self.assertFalse(result)
        controller.ensure_floating_panels_on_screen.assert_not_called()


class TestSavePanelLayoutPreferencesTracksVisibility(unittest.TestCase):
    def test_persists_each_panels_visibility_based_on_is_hidden(self) -> None:
        window = mock.MagicMock()
        window._dock_layout_built = True
        controller = LayoutStateController(window)
        visible_panel = _fake_panel(hidden=False)
        hidden_panel = _fake_panel(hidden=True)
        controller.panel_layout_panels = lambda: [("visible_one", visible_panel), ("hidden_one", hidden_panel)]

        controller.save_panel_layout_preferences()

        window._settings.setValue.assert_any_call("layout/panel_visible/visible_one", True)
        window._settings.setValue.assert_any_call("layout/panel_visible/hidden_one", False)

    def test_does_nothing_before_the_dock_layout_is_built(self) -> None:
        window = mock.MagicMock()
        window._dock_layout_built = False
        controller = LayoutStateController(window)
        controller.panel_layout_panels = mock.MagicMock()

        controller.save_panel_layout_preferences()

        controller.panel_layout_panels.assert_not_called()
        window._settings.setValue.assert_not_called()


class TestEnsurePanelVisibilityRestored(unittest.TestCase):
    def test_shows_a_panel_restoreState_left_hidden_but_should_be_visible(self) -> None:
        window = mock.MagicMock()
        window._settings_bool.return_value = True  # saved truth: should be visible
        controller = LayoutStateController(window)
        panel = _fake_panel(hidden=True)  # restoreState() left it hidden - the bug this works around
        controller.panel_layout_panels = lambda: [("spectra_panel", panel)]

        controller.ensure_panel_visibility_restored()

        panel.setVisible.assert_called_once_with(True)

    def test_raises_a_floating_panel_it_just_made_visible(self) -> None:
        window = mock.MagicMock()
        window._settings_bool.return_value = True
        controller = LayoutStateController(window)
        panel = _fake_panel(floating=True, hidden=True)
        controller.panel_layout_panels = lambda: [("spectra_panel", panel)]

        controller.ensure_panel_visibility_restored()

        panel.raise_.assert_called_once()

    def test_does_not_raise_a_docked_panel_it_just_made_visible(self) -> None:
        window = mock.MagicMock()
        window._settings_bool.return_value = True
        controller = LayoutStateController(window)
        panel = _fake_panel(floating=False, hidden=True)
        controller.panel_layout_panels = lambda: [("spectra_panel", panel)]

        controller.ensure_panel_visibility_restored()

        panel.raise_.assert_not_called()

    def test_hides_a_panel_restoreState_left_visible_but_should_be_hidden(self) -> None:
        window = mock.MagicMock()
        window._settings_bool.return_value = False  # saved truth: should be hidden
        controller = LayoutStateController(window)
        panel = _fake_panel(hidden=False)
        controller.panel_layout_panels = lambda: [("roi_list_panel", panel)]

        controller.ensure_panel_visibility_restored()

        panel.setVisible.assert_called_once_with(False)

    def test_leaves_a_panel_alone_when_its_current_state_already_matches(self) -> None:
        window = mock.MagicMock()
        window._settings_bool.return_value = True
        controller = LayoutStateController(window)
        panel = _fake_panel(hidden=False)  # already visible, matches saved truth
        controller.panel_layout_panels = lambda: [("spectra_panel", panel)]

        controller.ensure_panel_visibility_restored()

        panel.setVisible.assert_not_called()

    def test_include_floating_false_skips_a_floating_panel_entirely(self) -> None:
        # The main window itself is still hidden at every call site that
        # passes include_floating=False - showing a floating panel there
        # would pop it up on screen before the main window ever appears.
        window = mock.MagicMock()
        window._settings_bool.return_value = True  # saved truth: should be visible
        controller = LayoutStateController(window)
        panel = _fake_panel(floating=True, hidden=True)
        controller.panel_layout_panels = lambda: [("spectra_panel", panel)]

        controller.ensure_panel_visibility_restored(include_floating=False)

        panel.setVisible.assert_not_called()
        panel.raise_.assert_not_called()

    def test_include_floating_false_still_corrects_a_docked_panel(self) -> None:
        # Docked panels don't paint until their hidden ancestor is shown, so
        # correcting them early is harmless and keeps things consistent
        # sooner rather than waiting for the later, floating-inclusive pass.
        window = mock.MagicMock()
        window._settings_bool.return_value = True
        controller = LayoutStateController(window)
        panel = _fake_panel(floating=False, hidden=True)
        controller.panel_layout_panels = lambda: [("roi_list_panel", panel)]

        controller.ensure_panel_visibility_restored(include_floating=False)

        panel.setVisible.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
