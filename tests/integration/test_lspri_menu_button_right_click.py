"""A ribbon icon with a menu opens it on a right click as well as on a left click (the right click is the documented way: a left click is often the tool itself)."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QToolButton

from lspr_imaging_app.panels.icon_button_style import _RightClickMenu, open_menu_on_right_click, style_general_icon_button


class MenuButtonRightClickTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _button(self) -> tuple[QToolButton, list[int]]:
        button = QToolButton()
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        style_general_icon_button(button)  # as every ribbon icon is built; the menu comes after, as in the panels
        menu = QMenu(button)
        menu.addAction("something")
        button.setMenu(menu)
        opened: list[int] = []
        # Qt runs an instant-popup menu in a blocking loop of its own: close it from a timer, or the click never returns
        menu.aboutToShow.connect(lambda: (opened.append(1), QTimer.singleShot(0, menu.close)))
        button.show()
        return button, opened

    def test_a_right_click_opens_the_menu_and_so_does_a_left_click(self) -> None:
        button, opened = self._button()
        QTest.mouseClick(button, Qt.MouseButton.RightButton)
        self.assertEqual(len(opened), 1)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(opened), 2)
        button.close()

    def test_a_button_without_a_menu_ignores_the_right_click_and_asking_twice_changes_nothing(self) -> None:
        button = QToolButton()
        open_menu_on_right_click(button)
        open_menu_on_right_click(button)
        self.assertEqual(len(button.findChildren(_RightClickMenu)), 1)
        button.show()
        QTest.mouseClick(button, Qt.MouseButton.RightButton)  # nothing to open: no error
        button.close()


if __name__ == "__main__":
    unittest.main()
