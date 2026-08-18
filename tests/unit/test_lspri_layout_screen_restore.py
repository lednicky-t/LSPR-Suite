"""Regression test: restoring window geometry on startup now falls back to
the *closest-overlapping* connected monitor when the saved monitor name no
longer matches one (renamed/unplugged/reordered), instead of always falling
back straight to the primary/current screen.

best_restore_screen_geometry's overlap-matching branch already existed but
was unreachable: its only caller (restore_window_geometry) always passed
saved_rect=None, even though the exact data that branch needs
("window_geometry_rect", the window's last-closed screen-pixel rect) is
saved on every close and just never read back. This wires it up via a new
_saved_window_geometry_rect() helper.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QRect

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.layout_state_controller import LayoutStateController  # noqa: E402


class _FakeSettings:
    def __init__(self, values: dict) -> None:
        self._values = values

    def value(self, key, default=None):
        return self._values.get(key, default)


class _FakeWindow:
    """Duck-typed stand-in exposing only what LayoutStateController's screen
    logic reads, so it can be tested without a real MainWindow/QMainWindow."""

    def __init__(self, settings: _FakeSettings, screen=None) -> None:
        self._settings = settings
        self._screen = screen

    def screen(self):
        return self._screen


def _fake_screen(name: str, rect: tuple[int, int, int, int]):
    screen = mock.MagicMock()
    screen.name.return_value = name
    screen.availableGeometry.return_value = QRect(*rect)
    return screen


class TestSavedWindowGeometryRectParsing(unittest.TestCase):
    def test_valid_list_parses_to_a_qrectf(self) -> None:
        controller = LayoutStateController(_FakeWindow(_FakeSettings({"window_geometry_rect": [10, 20, 800, 600]})))
        rect = controller._saved_window_geometry_rect()
        self.assertIsNotNone(rect)
        self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()), (10.0, 20.0, 800.0, 600.0))

    def test_missing_value_returns_none(self) -> None:
        controller = LayoutStateController(_FakeWindow(_FakeSettings({})))
        self.assertIsNone(controller._saved_window_geometry_rect())

    def test_wrong_length_returns_none(self) -> None:
        controller = LayoutStateController(_FakeWindow(_FakeSettings({"window_geometry_rect": [10, 20, 800]})))
        self.assertIsNone(controller._saved_window_geometry_rect())

    def test_non_numeric_values_return_none(self) -> None:
        controller = LayoutStateController(_FakeWindow(_FakeSettings({"window_geometry_rect": [10, "bad", 800, 600]})))
        self.assertIsNone(controller._saved_window_geometry_rect())

    def test_non_positive_size_returns_none(self) -> None:
        controller = LayoutStateController(_FakeWindow(_FakeSettings({"window_geometry_rect": [10, 20, 0, 600]})))
        self.assertIsNone(controller._saved_window_geometry_rect())


class TestBestRestoreScreenGeometry(unittest.TestCase):
    def test_picks_the_screen_with_the_most_overlap_when_saved_name_is_stale(self) -> None:
        screen_a = _fake_screen("Monitor-A", (0, 0, 1920, 1080))
        screen_b = _fake_screen("Monitor-B", (1920, 0, 1920, 1080))
        settings = _FakeSettings(
            {
                "window_screen_name": "Monitor-Unplugged",  # no longer a connected screen
                "window_geometry_rect": [1950, 50, 800, 600],  # mostly on screen_b
            }
        )
        window = _FakeWindow(settings, screen=screen_a)
        controller = LayoutStateController(window)

        with mock.patch("lspr_imaging_app.gui.layout_state_controller.QGuiApplication") as mock_app:
            mock_app.screens.return_value = [screen_a, screen_b]
            saved_rect = controller._saved_window_geometry_rect()
            result = controller.best_restore_screen_geometry(saved_rect)

        self.assertEqual(result, screen_b.availableGeometry())

    def test_falls_back_to_current_screen_when_nothing_saved(self) -> None:
        screen_a = _fake_screen("Monitor-A", (0, 0, 1920, 1080))
        settings = _FakeSettings({})  # first-ever launch: no saved name or rect
        window = _FakeWindow(settings, screen=screen_a)
        controller = LayoutStateController(window)

        with mock.patch("lspr_imaging_app.gui.layout_state_controller.QGuiApplication") as mock_app:
            mock_app.screens.return_value = [screen_a]
            saved_rect = controller._saved_window_geometry_rect()
            self.assertIsNone(saved_rect)
            result = controller.best_restore_screen_geometry(saved_rect)

        self.assertEqual(result, screen_a.availableGeometry())

    def test_exact_name_match_wins_even_with_a_saved_rect(self) -> None:
        screen_a = _fake_screen("Monitor-A", (0, 0, 1920, 1080))
        screen_b = _fake_screen("Monitor-B", (1920, 0, 1920, 1080))
        settings = _FakeSettings(
            {
                "window_screen_name": "Monitor-A",
                "window_geometry_rect": [1950, 50, 800, 600],  # overlaps B, but A matches by name
            }
        )
        window = _FakeWindow(settings, screen=screen_a)
        controller = LayoutStateController(window)

        with mock.patch("lspr_imaging_app.gui.layout_state_controller.QGuiApplication") as mock_app:
            mock_app.screens.return_value = [screen_a, screen_b]
            result = controller.best_restore_screen_geometry(controller._saved_window_geometry_rect())

        self.assertEqual(result, screen_a.availableGeometry())


if __name__ == "__main__":
    unittest.main()
