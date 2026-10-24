"""Tests for ``ScrollingRibbon`` / ``RibbonDrawer`` (`panels/scrolling_ribbon.py`, `panels/ribbon_drawer.py`, 2026-10-24):
a control row that reports a small minimum width and, when the panel is narrower than the row, scrolls with an arrow at
each edge instead of forcing the dock wider. Also the Image tool ribbon's rule that only the shown tab sets the row width.

A real `QApplication` built in-process, never `.exec()`; driven by button `.click()`, `ensure_visible` and a posted
wheel event, never screen coordinates.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QSpinBox, QWidget

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.tool_ribbon import ImageToolRibbon
    from lspr_imaging_app.panels.ribbon_drawer import RibbonDrawer
    from lspr_imaging_app.panels.scrolling_ribbon import ScrollingRibbon
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_BUTTONS = 30
_BUTTON_WIDTH = 24
_ROW_WIDTH = _BUTTONS * _BUTTON_WIDTH  # the width the row needs


def _row(buttons: int = _BUTTONS) -> tuple[QWidget, list[QPushButton]]:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    made = []
    for _ in range(buttons):
        button = QPushButton(row)
        button.setFixedSize(_BUTTON_WIDTH, 24)
        layout.addWidget(button)
        made.append(button)
    return row, made


class ScrollingRibbonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.row, self.buttons = _row()
        self.ribbon = ScrollingRibbon(self.row)

    def tearDown(self) -> None:
        self.ribbon.close()

    def _show(self, width: int) -> None:
        self.ribbon.resize(width, self.ribbon.sizeHint().height())
        self.ribbon.show()
        _APP.processEvents()

    def test_the_minimum_width_is_small_not_the_rows(self) -> None:
        self.assertGreater(self.row.minimumSizeHint().width(), _ROW_WIDTH - 1)
        self.assertLess(self.ribbon.minimumSizeHint().width(), 100)

    def test_the_height_is_the_rows_and_fixed(self) -> None:
        self.assertEqual(self.ribbon.sizeHint().height(), self.row.sizeHint().height())
        self.assertEqual(self.ribbon.minimumSizeHint().height(), self.row.sizeHint().height())
        self.assertEqual(self.ribbon.sizePolicy().verticalPolicy(), QtWidgets.QSizePolicy.Policy.Fixed)

    def test_no_arrows_while_the_row_fits(self) -> None:
        self._show(_ROW_WIDTH + 40)
        self.assertFalse(self.ribbon.is_overflowing())
        self.assertTrue(self.ribbon.left_arrow().isHidden())
        self.assertTrue(self.ribbon.right_arrow().isHidden())

    def test_arrows_appear_when_the_panel_is_narrower_than_the_row(self) -> None:
        self._show(300)
        self.assertTrue(self.ribbon.is_overflowing())
        self.assertFalse(self.ribbon.left_arrow().isHidden())
        self.assertFalse(self.ribbon.right_arrow().isHidden())
        self.assertGreater(self.ribbon.scroll_maximum(), 0)

    def test_arrows_go_away_again_when_the_panel_is_widened(self) -> None:
        self._show(300)
        self.ribbon.resize(_ROW_WIDTH + 40, self.ribbon.height())
        _APP.processEvents()
        self.assertFalse(self.ribbon.is_overflowing())
        self.assertTrue(self.ribbon.right_arrow().isHidden())

    def test_the_arrows_scroll_and_are_dimmed_at_their_end(self) -> None:
        self._show(300)
        self.assertEqual(self.ribbon.scroll_value(), 0)
        self.assertFalse(self.ribbon.left_arrow().isEnabled())  # at the start: nothing further left
        self.assertTrue(self.ribbon.right_arrow().isEnabled())
        self.ribbon.right_arrow().click()
        self.assertGreater(self.ribbon.scroll_value(), 0)
        self.assertTrue(self.ribbon.left_arrow().isEnabled())
        for _ in range(60):
            self.ribbon.right_arrow().click()
        self.assertEqual(self.ribbon.scroll_value(), self.ribbon.scroll_maximum())
        self.assertFalse(self.ribbon.right_arrow().isEnabled())
        self.ribbon.left_arrow().click()
        self.assertLess(self.ribbon.scroll_value(), self.ribbon.scroll_maximum())

    def test_the_last_control_is_reachable(self) -> None:
        """Nothing is clipped without a way to get at it: scrolled to the end, the last button is inside the view."""
        self._show(300)
        last = self.buttons[-1]
        self.ribbon.ensure_visible(last)
        _APP.processEvents()
        viewport = self.ribbon._area.viewport()
        left = last.mapTo(viewport, QPoint(0, 0)).x()
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(left + last.width(), viewport.width())

    def test_ensure_visible_does_nothing_while_the_row_fits(self) -> None:
        self._show(_ROW_WIDTH + 40)
        self.ribbon.ensure_visible(self.buttons[-1])
        self.assertEqual(self.ribbon.scroll_value(), 0)

    def test_the_wheel_over_an_empty_part_scrolls_sideways(self) -> None:
        self._show(300)
        event = QWheelEvent(
            QPointF(50, 10), QPointF(50, 10), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        _APP.sendEvent(self.ribbon._area.viewport(), event)  # what an ignored wheel (empty ribbon space, a button) reaches
        self.assertGreater(self.ribbon.scroll_value(), 0)

    def test_a_number_field_keeps_its_own_wheel(self) -> None:
        """A spin box under the pointer changes its value on the wheel; the ribbon must not scroll instead."""
        row = QWidget()
        layout = QHBoxLayout(row)
        spin = QSpinBox(row)
        spin.setRange(0, 100)
        spin.setFixedWidth(400)
        layout.addWidget(spin)
        ribbon = ScrollingRibbon(row)
        ribbon.resize(200, ribbon.sizeHint().height())
        ribbon.show()
        _APP.processEvents()
        self.addCleanup(ribbon.close)
        event = QWheelEvent(
            QPointF(20, 10), QPointF(20, 10), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        _APP.sendEvent(spin, event)
        self.assertEqual(spin.value(), 1)  # the field took the wheel...
        self.assertEqual(ribbon.scroll_value(), 0)  # ...so the ribbon did not move


class RibbonDrawerScrollingTest(unittest.TestCase):
    def test_the_drawer_shows_the_scrolling_wrapper_and_keeps_the_ribbon_as_content(self) -> None:
        row, _buttons = _row()
        drawer = RibbonDrawer(row, "Show the ribbon.")
        self.addCleanup(drawer.close)
        self.assertIsInstance(drawer.ribbon, ScrollingRibbon)
        self.assertIs(drawer.content, row)
        self.assertLess(drawer.minimumSizeHint().width(), 100)

    def test_rolling_up_still_hides_the_ribbon(self) -> None:
        row, _buttons = _row()
        drawer = RibbonDrawer(row, "Show the ribbon.")
        self.addCleanup(drawer.close)
        drawer.resize(300, 40)
        drawer.show()
        _APP.processEvents()
        drawer.set_expanded(False, animate=False)
        self.assertTrue(drawer.ribbon.isHidden())
        drawer.set_expanded(True, animate=False)
        self.assertFalse(drawer.ribbon.isHidden())

    def test_scrolling_can_be_turned_off(self) -> None:
        row, _buttons = _row()
        drawer = RibbonDrawer(row, "Show the ribbon.", scrolling=False)
        self.addCleanup(drawer.close)
        self.assertIs(drawer.ribbon, row)


class ToolRibbonWidthTest(unittest.TestCase):
    def test_only_the_shown_tab_sets_the_ribbons_width(self) -> None:
        wide, _ = _row(40)
        narrow, _ = _row(5)
        ribbon = ImageToolRibbon([("Wide", wide), ("Narrow", narrow)])
        self.addCleanup(ribbon.close)
        ribbon.show()  # a hidden tree does not re-lay-out, so its size hints would not move
        _APP.processEvents()
        self.assertGreaterEqual(ribbon.minimumSizeHint().width(), 40 * _BUTTON_WIDTH)
        ribbon._tab_buttons[1].click()
        _APP.processEvents()
        self.assertLess(ribbon.minimumSizeHint().width(), 40 * _BUTTON_WIDTH // 2)
        ribbon._tab_buttons[0].click()
        _APP.processEvents()
        self.assertGreaterEqual(ribbon.minimumSizeHint().width(), 40 * _BUTTON_WIDTH)


if __name__ == "__main__":
    unittest.main()
