"""Regression coverage for a real bug: double-clicking the embedded menu bar
(File/Edit/View/...) inside the custom frameless title bar could also
maximize/restore the window, since Qt sometimes delivers that second click's
synthesized MouseButtonDblClick event to the title bar widget itself rather
than to the menu bar (e.g. the second click of a quick
open-menu-then-close-it interaction). Fixed by checking the click's position
against the menu bar's own rect before toggling - see
event_filter_for (main_window_lifecycle.py) and build_title_bar storing
window._menu_bar (main_window_titlebar.py).
"""
from __future__ import annotations

import sys
import unittest

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtWidgets import QApplication

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from unittest.mock import patch

from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.session import MeasurementSession
from lspr_app.gui.main_window import MainWindow
from lspr_app.gui.main_window_lifecycle import event_filter_for


class _FakeDoubleClickEvent:
    def __init__(self, global_pos: QPointF) -> None:
        self._global_pos = global_pos

    def type(self):
        return QEvent.Type.MouseButtonDblClick

    def button(self):
        return Qt.MouseButton.LeftButton

    def globalPosition(self) -> QPointF:
        return self._global_pos


class TitleBarDoubleClickMaximizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        with patch.object(MainWindow, "_start_hardware_initialization", lambda self: None):
            self.window = MainWindow(SimulatedSpectrometer(), MeasurementSession(), None)
        self.window.show()
        self.app.processEvents()
        self.toggled: list[bool] = []
        self.window._toggle_window_max_restore = lambda: self.toggled.append(True)

    def test_double_click_on_menu_bar_does_not_maximize(self) -> None:
        menu_bar = self.window._menu_bar
        global_pos = menu_bar.mapToGlobal(menu_bar.rect().center())
        result = event_filter_for(self.window, self.window._title_bar_widget, _FakeDoubleClickEvent(QPointF(global_pos)))

        self.assertIsNone(result)
        self.assertEqual(self.toggled, [])

    def test_double_click_elsewhere_on_title_bar_still_maximizes(self) -> None:
        title_bar = self.window._title_bar_widget
        global_pos = title_bar.mapToGlobal(title_bar.rect().center())
        result = event_filter_for(self.window, title_bar, _FakeDoubleClickEvent(QPointF(global_pos)))

        self.assertTrue(result)
        self.assertEqual(self.toggled, [True])

    def test_double_click_within_menu_bars_width_but_outside_its_own_rect_vertically_does_not_maximize(self) -> None:
        # Regression the maintainer hit live: menu_bar's own widget height is
        # whatever QMenuBar's natural sizeHint is, vertically centered in the
        # (possibly taller) title bar row rather than stretched to fill it
        # (see build_title_bar's left_cluster) - so a double-click that's
        # horizontally over File/Edit/View/... but lands a few pixels above
        # or below the menu bar's own exact rect used to fall through the old
        # rect-containment check entirely and still maximize/restore the
        # window. Construct a point at the menu bar's horizontal center but
        # the title bar row's own top edge - outside menu_bar's rect
        # whenever it's shorter than the row (the reported case), a no-op
        # (still inside) otherwise, so this stays meaningful either way.
        menu_bar = self.window._menu_bar
        title_bar = self.window._title_bar_widget
        x = menu_bar.mapToGlobal(menu_bar.rect().center()).x()
        y = title_bar.mapToGlobal(title_bar.rect().topLeft()).y() + 1
        result = event_filter_for(self.window, title_bar, _FakeDoubleClickEvent(QPointF(x, y)))

        self.assertIsNone(result)
        self.assertEqual(self.toggled, [])


if __name__ == "__main__":
    unittest.main()
