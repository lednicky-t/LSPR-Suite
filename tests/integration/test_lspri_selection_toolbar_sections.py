"""The selection toolbar's five sections, divided by horizontal lines (2026-11-15, maintainer: "split left ribbon to sections with horizontal line
separator", "starting with lock/unlock, then selection tools rectangle, lasso, wand, draw ... then modes ... under it Histogram, Threshold,
contrast ..." and "deselect icon can be removed").
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.wheel_tool_button import WheelToolButton
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import AreaSelectionMode, AreaSelectionModule, HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_ui import get_active_theme
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class SelectionToolbarSectionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=AreaSelectionModule(),
        )
        self.toolbar = self.panel._selection_tools.toolbar
        self.panel.resize(1100, 1000)  # tall: no scrolling, every button in its place
        self.panel.show()
        for _ in range(5):
            _APP.processEvents()

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()

    def _y(self, widget: QtWidgets.QWidget) -> int:
        return widget.mapTo(self.toolbar.column.content, QPoint(0, 0)).y()

    def _sections(self) -> list[list[QtWidgets.QWidget]]:
        toolbar, tool = self.toolbar, self.toolbar.tool_button
        return [
            [toolbar.lock_button, toolbar.unlock_button],
            [tool(AreaSelectionMode.RECTANGLE), tool(AreaSelectionMode.LASSO), tool(AreaSelectionMode.WAND), tool(AreaSelectionMode.BRUSH)],
            [toolbar.mode_button],
            [tool(AreaSelectionMode.HISTOGRAM), tool(AreaSelectionMode.THRESHOLD), tool(AreaSelectionMode.CONTRAST)],
            [toolbar.modify_button],
        ]

    def test_the_buttons_are_in_five_sections_in_the_asked_order(self) -> None:
        flat = [self._y(w) for section in self._sections() for w in section]
        self.assertEqual(flat, sorted(flat), "top to bottom: lock / unlock, rectangle, lasso, wand, draw, mode, histogram, threshold, contrast, modify")
        self.assertEqual(len(set(flat)), len(flat))

    def test_a_horizontal_line_divides_each_pair_of_sections(self) -> None:
        lines = self.toolbar._separators
        self.assertEqual(len(lines), 5, "four between the five sections, and one above the View button pinned under the column")
        self.assertFalse(self.toolbar.column.content.isAncestorOf(lines[-1]))
        self.assertGreater(lines[-1].mapTo(self.toolbar, QPoint(0, 0)).y(), self.toolbar.column.geometry().bottom() - 1)
        self.assertLess(lines[-1].mapTo(self.toolbar, QPoint(0, 0)).y(), self.toolbar.view_button.geometry().top(), "above the View button")
        lines = lines[:4]
        sections = self._sections()
        for line, above, below in zip(lines, sections[:-1], sections[1:]):
            self.assertEqual(line.frameShape(), QtWidgets.QFrame.Shape.HLine)
            self.assertTrue(line.isVisible())
            self.assertGreater(self._y(line), self._y(above[-1]), "below the last button of the section above")
            self.assertLess(self._y(line), self._y(below[0]), "above the first button of the section below")
            self.assertLessEqual(line.width(), self.toolbar.width(), "narrower than the column")

    def test_no_line_sits_inside_a_section(self) -> None:
        for section in self._sections():
            ys = [self._y(w) for w in section]
            for line in self.toolbar._separators[:4]:  # the fifth is outside the column
                self.assertFalse(min(ys) < self._y(line) < max(ys), "a divider between two buttons of one section")

    def test_the_lines_are_drawn_in_the_themes_border_colour(self) -> None:
        for line in self.toolbar._separators:
            self.assertIn(get_active_theme().control_border, line.styleSheet())

    def test_the_deselect_button_and_the_single_value_button_are_gone(self) -> None:
        self.assertFalse(hasattr(self.toolbar, "deselect_button"))
        self.assertFalse(hasattr(self.toolbar, "value_button"))

    def test_only_the_wand_button_reads_the_wheel(self) -> None:
        """The other tool buttons must pass the wheel on, so the column scrolls over them."""
        wheel_tools = [
            mode for mode in AreaSelectionMode
            if mode is not AreaSelectionMode.ALL and isinstance(self.toolbar.tool_button(mode), WheelToolButton)
        ]
        self.assertEqual(wheel_tools, [AreaSelectionMode.WAND])
