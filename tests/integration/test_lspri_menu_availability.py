"""Context menus offer only what can be done now (2026-11-19, maintainer): an entry whose prerequisite is not met is **left out**, not greyed.

The ROI menu, the ROI table's menus, the plan step menu, the Mask rows' menus and the anchor menus have their rules tested beside their other
behaviour (`test_lspri_roi_phase3_ui.py`, `test_lspri_rewrite_roi_table_panel.py`, `test_lspri_plan_bar_gestures.py`, `test_lspri_rewrite_mask_rows.py`,
`test_lspri_sensorgram_drift_baseline.py`); this file holds the generic menus.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QContextMenuEvent
    from PyQt6.QtWidgets import QApplication, QMenu, QWidget

    from lspr_imaging_app.panels.image import context_menu, mask_row_menu
    from lspr_imaging_app.panels.image.data_axis_slider import DataAxisSlider
    from lspr_imaging_app.panels.image.mask_row_menu import MenuEntry
except ImportError as error:  # pragma: no cover
    skip_if_lspri_rewrite_absent(error)
    raise

_APP = QApplication.instance() or QApplication([])


def _recording(module):
    """Patch `module.QMenu` with one that records what it would show instead of opening: (patcher, the menus built)."""
    built: list = []

    class Recording(QMenu):
        def exec(self, *_args):  # noqa: A003 - Qt's name
            built.append([a.text() for a in self.actions() if not a.isSeparator()])
            return None

    return mock.patch.object(module, "QMenu", Recording), built


class ToolContextMenuTest(unittest.TestCase):
    def test_an_entry_that_is_not_available_is_left_out(self) -> None:
        patcher, built = _recording(context_menu)
        with patcher:
            chosen = context_menu.show_tool_context_menu(QWidget(), [("Apply crop", False), ("Cancel crop", True)])
        self.assertIsNone(chosen)
        self.assertEqual(built, [["Cancel crop"]])
        patcher, built = _recording(context_menu)
        with patcher:
            context_menu.show_tool_context_menu(QWidget(), [("Apply crop", True), ("Cancel crop", True)])
        self.assertEqual(built, [["Apply crop", "Cancel crop"]])

    def test_a_menu_with_nothing_to_offer_does_not_open(self) -> None:
        patcher, built = _recording(context_menu)
        with patcher:
            self.assertIsNone(context_menu.show_tool_context_menu(QWidget(), [("Apply crop", False)]))
        self.assertEqual(built, [])

    def test_a_separator_is_not_put_above_an_entry_that_is_left_out(self) -> None:
        menu_parent = QWidget()
        seen: list[list[bool]] = []

        class Spy(QMenu):
            def exec(self, *_args):  # noqa: A003
                seen.append([a.isSeparator() for a in self.actions()])
                return None

        with mock.patch.object(context_menu, "QMenu", Spy):
            context_menu.show_tool_context_menu(menu_parent, [("Lock", True), ("Invert", False), ("Exit", True)], separate_before={"Invert"})
            context_menu.show_tool_context_menu(menu_parent, [("Lock", True), ("Invert", True), ("Exit", True)], separate_before={"Invert"})
        self.assertEqual(seen, [[False, False], [False, True, False, False]])


class EntriesMenuTest(unittest.TestCase):
    def test_the_mask_row_menu_leaves_out_the_entries_that_are_not_enabled(self) -> None:
        patcher, built = _recording(mask_row_menu)
        entries = [
            MenuEntry("go", "Go to this cube"),
            MenuEntry("make", "Make it individual", enabled=False, separator_before=True),
            MenuEntry("del", "Delete", enabled=True),
        ]
        with patcher:
            mask_row_menu.show_entries_menu(QWidget(), header="Cube 3 - persistent mask", entries=entries)
        self.assertEqual(built, [["Cube 3 - persistent mask", "Go to this cube", "Delete"]])


class SliderMenuTest(unittest.TestCase):
    def _right_click(self, slider: DataAxisSlider) -> list:
        opened: list = []
        patcher = mock.patch.object(QMenu, "exec", lambda menu, *_a: opened.append([a.text() for a in menu.actions()]))
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5))
        with patcher:
            slider.contextMenuEvent(event)
        return opened

    def test_without_a_reference_tick_no_menu_opens(self) -> None:
        slider = DataAxisSlider()
        slider.setRange(0, 9)
        self.assertEqual(self._right_click(slider), [])

    def test_with_a_reference_tick_the_menu_offers_go_to_reference(self) -> None:
        slider = DataAxisSlider()
        slider.setRange(0, 9)
        slider.set_reference_tick(4, "#ff0000")  # (without a colour the mark is cleared)
        self.assertEqual(self._right_click(slider), [["Go to reference"]])


if __name__ == "__main__":
    unittest.main()
