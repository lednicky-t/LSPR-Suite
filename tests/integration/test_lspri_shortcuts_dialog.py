"""Help -> Keyboard shortcuts (F1) and the real menus' keys, both read from `shortcuts.py`.

Driven through real widgets (`.click()`, `.trigger()`, `setText`), never by screen coordinate. The dialog is built on its own for the
search / copy / remembered-state cases and inside the real window for the menu cases.

Design record: `apps/LSPRi/eva/docs/keyboard_shortcut_registry_design_2026-10-30.md`.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QKeySequence

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import shortcuts
    from lspr_imaging_app.app_rewrite import build_main_window
    from lspr_imaging_app.panels.shortcuts_dialog import DEFAULT_SIZE, ShortcutsDialog
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.storage.ui_state_keys import SHORTCUTS_DIALOG_COLLAPSED, SHORTCUTS_DIALOG_SIZE
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class TheDialogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.saved: list[dict] = []
        self.store = UiStateStore({}, on_changed=self.saved.append)
        self.dialog = ShortcutsDialog(self.store)

    def tearDown(self) -> None:
        self.dialog.deleteLater()

    def _section_item(self, name: str):
        return next(self.dialog.tree.topLevelItem(i) for i in range(self.dialog.tree.topLevelItemCount()) if self.dialog.tree.topLevelItem(i).text(0) == name)

    def test_it_lists_every_binding_with_its_keys_and_scope(self) -> None:
        self.assertEqual(self.dialog.row_count(), len(shortcuts.all_bindings()))
        self.assertEqual(self.dialog.visible_row_count(), len(shortcuts.all_bindings()))
        shown = []
        for section_index in range(self.dialog.tree.topLevelItemCount()):
            section = self.dialog.tree.topLevelItem(section_index)
            for row_index in range(section.childCount()):
                row = section.child(row_index)
                shown.append((row.text(0), row.text(1), row.text(2)))
        expected = [
            (item.label, shortcuts.display(item.id), item.scope)
            for section in shortcuts.sections()
            for item in shortcuts.all_bindings()
            if item.section == section
        ]
        self.assertEqual(shown, expected)

    def test_the_sections_come_in_table_order(self) -> None:
        names = [self.dialog.tree.topLevelItem(i).text(0) for i in range(self.dialog.tree.topLevelItemCount())]
        self.assertEqual(names, list(shortcuts.sections()))

    def test_a_search_narrows_by_action_text_keys_or_section(self) -> None:
        self.dialog.search.setText("fill the top")
        self.assertEqual(self.dialog.visible_row_count(), 1)
        self.dialog.search.setText("ctrl+shift+l")
        self.assertEqual(self.dialog.visible_row_count(), 1)
        self.dialog.search.setText("mouse: sliders")
        self.assertTrue(0 < self.dialog.visible_row_count() < len(shortcuts.all_bindings()))
        self.assertTrue(self._section_item("Edit").isHidden(), "a section with no match is hidden")

    def test_a_search_with_no_match_says_so_instead_of_showing_an_empty_tree(self) -> None:
        self.dialog.search.setText("zzzz")
        self.assertEqual(self.dialog.visible_row_count(), 0)
        self.assertTrue(self.dialog.tree.isHidden())
        self.assertFalse(self.dialog.empty_label.isHidden())
        self.assertEqual(self.dialog.empty_label.text(), "No shortcut matches 'zzzz'.")
        self.dialog.search.clear()
        self.assertFalse(self.dialog.tree.isHidden())
        self.assertTrue(self.dialog.empty_label.isHidden())
        self.assertEqual(self.dialog.visible_row_count(), len(shortcuts.all_bindings()))

    def test_copy_as_text_puts_the_whole_table_on_the_clipboard(self) -> None:
        self.dialog.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), shortcuts.shortcuts_as_text())
        self.assertEqual(self.dialog.copy_button.text(), "Copied")

    def test_collapsed_sections_are_remembered_and_a_search_does_not_change_them(self) -> None:
        self.dialog.tree.collapseItem(self._section_item("Panels"))
        self.assertEqual(self.store.get(SHORTCUTS_DIALOG_COLLAPSED), ["Panels"])
        self.dialog.search.setText("panel")  # a search opens what it finds ...
        self.assertTrue(self._section_item("Panels").isExpanded())
        self.assertEqual(self.store.get(SHORTCUTS_DIALOG_COLLAPSED), ["Panels"], "... without touching the saved state")
        self.dialog.search.clear()
        self.assertFalse(self._section_item("Panels").isExpanded(), "the user's collapse comes back")

        again = ShortcutsDialog(UiStateStore({SHORTCUTS_DIALOG_COLLAPSED: ["Panels"]}))
        self.addCleanup(again.deleteLater)
        collapsed = [again.tree.topLevelItem(i).text(0) for i in range(again.tree.topLevelItemCount()) if not again.tree.topLevelItem(i).isExpanded()]
        self.assertEqual(collapsed, ["Panels"])

    def test_the_size_is_remembered_and_a_bad_saved_value_falls_back_to_the_default(self) -> None:
        self.dialog.resize(640, 480)
        self.dialog.show()
        self.dialog.hide()
        self.assertEqual(self.store.get(SHORTCUTS_DIALOG_SIZE), [640, 480])
        for bad in ("wide", [640], [10, 10], [True, False], None):
            dialog = ShortcutsDialog(UiStateStore({SHORTCUTS_DIALOG_SIZE: bad}))
            self.addCleanup(dialog.deleteLater)
            self.assertEqual((dialog.width(), dialog.height()), DEFAULT_SIZE, repr(bad))

    def test_reopening_clears_the_search(self) -> None:
        self.dialog.search.setText("zzzz")
        self.dialog.reset_search()
        self.assertEqual(self.dialog.search.text(), "")
        self.assertEqual(self.dialog.visible_row_count(), len(shortcuts.all_bindings()))

    def test_close_button_closes(self) -> None:
        self.dialog.show()
        self.dialog.close_button.click()
        self.assertFalse(self.dialog.isVisible())


class TheRealMenusTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.window = build_main_window()

    def tearDown(self) -> None:
        undo_manager.clear()
        self.window.deleteLater()

    def _menu(self, title: str):
        return next(a.menu() for a in self.window.menuBar().actions() if a.text() == title)

    def _action(self, menu_title: str, text: str):
        return next(a for a in self._menu(menu_title).actions() if a.text() == text)

    def _dialog(self) -> ShortcutsDialog | None:
        return self.window.findChild(ShortcutsDialog)

    def test_help_has_keyboard_shortcuts_on_f1(self) -> None:
        action = self._action("&Help", "Keyboard shortcuts")
        self.assertEqual([s.toString() for s in action.shortcuts()], ["F1"])
        self.assertEqual(action.shortcutContext(), Qt.ShortcutContext.ApplicationShortcut)
        self.assertEqual(self._menu("&Help").actions()[0].text(), "Keyboard shortcuts", "the first Help entry")

    def test_the_action_opens_the_dialog_and_a_second_press_closes_it(self) -> None:
        action = self._action("&Help", "Keyboard shortcuts")
        self.assertIsNone(self._dialog())
        action.trigger()
        dialog = self._dialog()
        self.assertIsNotNone(dialog)
        self.assertFalse(dialog.isHidden())
        self.assertEqual(dialog.row_count(), len(shortcuts.all_bindings()))
        action.trigger()
        self.assertTrue(dialog.isHidden())
        dialog.search.setText("zzzz")
        action.trigger()  # opened again: the search is cleared
        self.assertEqual(dialog.search.text(), "")
        self.assertIs(self._dialog(), dialog, "one dialog, reused")
        dialog.close()

    def test_the_view_menu_panels_and_presets_use_the_table_keys(self) -> None:
        view = self._menu("&View")
        for title, binding_id in shortcuts.PANEL_TOGGLE_IDS.items():
            action = next(a for a in view.actions() if a.text() == title)
            self.assertEqual(action.shortcuts(), list(shortcuts.sequences(binding_id)), title)
        presets = next(a.menu() for a in view.actions() if a.text() == "Panel Presets")
        for name, binding_id in shortcuts.PRESET_IDS.items():
            action = next(a for a in presets.actions() if a.text() == name)
            self.assertEqual(action.shortcuts(), list(shortcuts.sequences(binding_id)), name)

    def test_edit_and_help_actions_use_the_table_keys(self) -> None:
        pairs = (
            ("&Edit", "Preferences...", "edit.preferences"),
            ("&Help", "Log Console", "help.log_console"),
        )
        for menu_title, text, binding_id in pairs:
            self.assertEqual(self._action(menu_title, text).shortcuts(), list(shortcuts.sequences(binding_id)), text)
        edit = self._menu("&Edit")
        undo = next(a for a in edit.actions() if a.text().startswith("Undo"))
        redo = next(a for a in edit.actions() if a.text().startswith("Redo"))
        self.assertEqual(undo.shortcuts(), list(shortcuts.sequences("edit.undo")))
        self.assertEqual(redo.shortcuts(), list(shortcuts.sequences("edit.redo")))
        self.assertIn(QKeySequence("Ctrl+Y"), redo.shortcuts())


if __name__ == "__main__":
    unittest.main()
