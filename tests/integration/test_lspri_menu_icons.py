"""The icons of the menu entries (`panels/menu_icons.py`, 2026-11-19): one table, one icon per command in every menu."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QApplication, QMenu
    from lspr_ui import available_tabler_icon_names, get_active_theme, set_active_theme

    from lspr_imaging_app.gui.app_theme import LSPRI_BRIGHT_THEME, LSPRI_DARK_THEME
    from lspr_imaging_app.panels import menu_icons
    from lspr_imaging_app.panels.image.mask_rows import ENTRY_ICONS
    from lspr_imaging_app.panels.image.interaction import _TOOL_MENU_ICONS
except ImportError as error:  # pragma: no cover
    skip_if_lspri_rewrite_absent(error)
    raise

_APP = QApplication.instance() or QApplication([])
_PACKAGE = APP_SRC / "lspr_imaging_app"


def _keys_written_in_the_source() -> set[str]:
    """Every command key written as a string in a `give_icon(x, "key")`, `give_persistent_icon(x, "key")` or `menu_icon("key")` call of the app."""
    keys: set[str] = set()
    for path in _PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in ("give_icon", "give_persistent_icon") and len(node.args) >= 2:
                argument = node.args[1]
            elif name == "menu_icon" and path.name != "selection_menu_icons.py" and len(node.args) == 1:
                argument = node.args[0]
            else:
                continue
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                keys.add(argument.value)
    return keys


class MenuIconTableTest(unittest.TestCase):
    def tearDown(self) -> None:
        set_active_theme(LSPRI_DARK_THEME)

    def test_every_icon_the_table_names_is_vendored(self) -> None:
        have = set(available_tabler_icon_names())
        missing = {key: name for key, name in menu_icons.ICONS.items() if isinstance(name, str) and name not in have}
        self.assertEqual(missing, {})

    def test_every_key_draws_an_icon(self) -> None:
        for key in menu_icons.ICONS:
            self.assertFalse(menu_icons.menu_icon(key).isNull(), key)

    def test_every_key_the_menus_use_is_in_the_table(self) -> None:
        written = _keys_written_in_the_source()
        self.assertGreater(len(written), 40, "the scan found almost nothing: it is no longer reading the calls")
        self.assertEqual(sorted(written - set(menu_icons.ICONS)), [])
        for table in (ENTRY_ICONS, _TOOL_MENU_ICONS, menu_icons.PANEL_ICON_KEYS):  # keys held in tables of their own
            self.assertEqual(sorted(set(table.values()) - set(menu_icons.ICONS)), [])

    def test_an_unknown_key_gives_no_icon_and_does_not_stop_the_menu(self) -> None:
        action = QAction("x")
        with self.assertLogs(menu_icons.logger, level="WARNING"):
            menu_icons.give_icon(action, "no_such_command")
        self.assertTrue(action.icon().isNull())

    def test_a_submenu_gets_its_icon_through_its_own_action(self) -> None:
        menu = QMenu()
        sub = menu.addMenu("Sub")
        menu_icons.give_icon(sub, "group")
        self.assertFalse(sub.menuAction().icon().isNull())

    def test_the_same_command_is_the_same_picture_everywhere(self) -> None:
        first, second = QAction("a"), QAction("b")
        menu_icons.give_icon(first, "delete")
        menu_icons.give_icon(second, "delete")
        self.assertEqual(first.icon().pixmap(36, 36).toImage(), second.icon().pixmap(36, 36).toImage())

    def test_a_persistent_icon_is_redrawn_in_the_new_theme(self) -> None:
        set_active_theme(LSPRI_DARK_THEME)
        action = QAction("x")
        menu_icons.give_persistent_icon(action, "copy")
        before = action.icon().pixmap(36, 36).toImage()
        set_active_theme(LSPRI_BRIGHT_THEME)
        menu_icons.refresh_menu_icons()
        self.assertNotEqual(action.icon().pixmap(36, 36).toImage(), before)
        self.assertEqual(get_active_theme(), LSPRI_BRIGHT_THEME)

    def test_a_plain_icon_is_not_redrawn_and_a_gone_action_is_forgotten(self) -> None:
        action = QAction("x")
        menu_icons.give_icon(action, "copy")
        before = action.icon().pixmap(36, 36).toImage()
        set_active_theme(LSPRI_BRIGHT_THEME)
        menu_icons.refresh_menu_icons()  # (the plain icon is for a menu built when it pops up: it reads the theme then)
        self.assertEqual(action.icon().pixmap(36, 36).toImage(), before)
        gone = QAction("y")
        menu_icons.give_persistent_icon(gone, "copy")
        del gone
        menu_icons.refresh_menu_icons()  # must not raise on the dead entry

    def test_the_painted_icons_draw(self) -> None:
        for key in ("sample", "reference", "step_before", "step_after", "step_remove", "step_restore", "step_merge", "step_split"):
            self.assertFalse(menu_icons.menu_icon(key).isNull(), key)


if __name__ == "__main__":
    unittest.main()
