"""The shortcut table (`lspr_imaging_app/shortcuts.py`): no two bindings fight for a key, every id the source uses exists, every
key in the table is used by the source, matching is exact. Pure logic: no window, no application.

Design record: `apps/LSPRi/eva/docs/keyboard_shortcut_registry_design_2026-10-30.md`.
"""

from __future__ import annotations

import re
import sys
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

_APP = QApplication.instance() or QApplication([])  # an action (one test) needs an application behind it
ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import shortcuts
    from lspr_imaging_app.panels.layout_presets import PRESET_NAMES
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

SOURCE = APP_SRC / "lspr_imaging_app"
_NONE = Qt.KeyboardModifier.NoModifier
_CTRL = Qt.KeyboardModifier.ControlModifier


def _source_literals() -> set[str]:
    """Every string literal in the app's source (outside the table itself) that looks like a binding id."""
    prefixes = "|".join(sorted({item.id.split(".")[0] for item in shortcuts.BINDINGS}))
    pattern = re.compile(rf'["\']((?:{prefixes})\.[a-z_]+)["\']')
    found: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        if path.name != "shortcuts.py":
            found.update(pattern.findall(path.read_text(encoding="utf-8")))
    return found


class TestTheTable(unittest.TestCase):
    def test_no_two_bindings_fight_for_a_key(self) -> None:
        self.assertEqual(shortcuts.find_conflicts(), [])

    def test_the_conflict_finder_does_catch_a_clash(self) -> None:
        a = shortcuts.Binding("a.one", "S", "One", keys=("Ctrl+K",), scope="Table")
        b = shortcuts.Binding("a.two", "S", "Two", keys=("Ctrl+K",), scope="Table")
        c = shortcuts.Binding("a.three", "S", "Three", keys=("Ctrl+K",), scope="Other table")
        d = shortcuts.Binding("a.four", "S", "Four", keys=("Ctrl+K",))  # EVERYWHERE
        self.assertEqual(shortcuts.find_conflicts([a, b]), [("Ctrl+K", "a.one", "a.two")])
        self.assertEqual(shortcuts.find_conflicts([a, c]), [], "two local scopes may share a key")
        self.assertEqual(len(shortcuts.find_conflicts([a, d])), 1, "a menu key beats a widget's key")

    def test_every_key_text_is_a_single_valid_key(self) -> None:
        for item in shortcuts.BINDINGS:
            for text in item.keys:
                sequence = QKeySequence(text)
                self.assertEqual(sequence.count(), 1, f"{item.id}: {text!r} is not one key")
                self.assertNotEqual(sequence.toString(QKeySequence.SequenceFormat.PortableText), "", f"{item.id}: {text!r}")

    def test_an_entry_has_keys_or_a_gesture_never_neither(self) -> None:
        for item in shortcuts.BINDINGS:
            self.assertTrue(item.keys or item.gesture, item.id)
            self.assertTrue(item.label.strip(), item.id)

    def test_panel_and_preset_entries_exist_for_every_panel_and_preset(self) -> None:
        ids = {item.id for item in shortcuts.BINDINGS}
        self.assertTrue(set(shortcuts.PANEL_TOGGLE_IDS.values()) <= ids)
        self.assertTrue(set(shortcuts.PRESET_IDS.values()) <= ids)
        self.assertEqual(set(PRESET_NAMES), set(shortcuts.PRESET_IDS), "a layout preset without a key (or a key without a preset)")


class TestTheSourceAgreesWithTheTable(unittest.TestCase):
    def test_every_id_the_source_uses_is_in_the_table(self) -> None:
        unknown = _source_literals() - {item.id for item in shortcuts.BINDINGS}
        self.assertEqual(unknown, set(), "an id used in the source but missing from shortcuts.py (a typo?)")

    def test_every_key_in_the_table_is_used_by_the_source(self) -> None:
        generated = set(shortcuts.PANEL_TOGGLE_IDS.values()) | set(shortcuts.PRESET_IDS.values())  # bound through their dicts
        unused = {item.id for item in shortcuts.BINDINGS if item.keys} - generated - _source_literals()
        self.assertEqual(unused, set(), "a key listed in shortcuts.py that nothing binds or reacts to")

    def test_an_unknown_id_names_the_file_to_edit(self) -> None:
        with self.assertRaises(KeyError) as caught:
            shortcuts.binding("edit.nothing")
        self.assertIn("shortcuts.py", str(caught.exception))


class TestMatching(unittest.TestCase):
    def test_a_key_matches_with_exactly_its_modifiers(self) -> None:
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_D, _CTRL, "roi_table.fill_down"))
        self.assertFalse(shortcuts.key_matches(Qt.Key.Key_D, _NONE, "roi_table.fill_down"))
        self.assertFalse(shortcuts.key_matches(Qt.Key.Key_D, _CTRL | Qt.KeyboardModifier.ShiftModifier, "roi_table.fill_down"))

    def test_every_alternative_of_a_binding_matches(self) -> None:
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Delete, _NONE, "roi_table.delete"))
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Backspace, _NONE, "roi_table.delete"))
        self.assertFalse(shortcuts.key_matches(Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier, "roi_table.delete"))

    def test_the_keypad_flag_is_ignored(self) -> None:
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Up, Qt.KeyboardModifier.KeypadModifier, "roi_table.cell_step"))

    def test_shift_arrows_are_the_large_step_and_plain_arrows_the_normal_one(self) -> None:
        shift = Qt.KeyboardModifier.ShiftModifier
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Up, shift, "roi_table.cell_step_large"))
        self.assertFalse(shortcuts.key_matches(Qt.Key.Key_Up, shift, "roi_table.cell_step"))
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Down, _NONE, "roi_table.cell_step"))

    def test_the_rotate_nudge_has_one_binding_per_step_size(self) -> None:
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Left, _NONE, "tools.rotate_nudge"))
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Left, _CTRL, "tools.rotate_nudge_medium"))
        self.assertTrue(shortcuts.key_matches(Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier, "tools.rotate_nudge_coarse"))

    def test_a_mouse_entry_never_matches_a_key(self) -> None:
        self.assertFalse(shortcuts.key_matches(Qt.Key.Key_A, _NONE, "mouse.view.pan"))


class TestDisplayAndText(unittest.TestCase):
    def test_keys_are_shown_joined_and_a_gesture_is_shown_as_is(self) -> None:
        self.assertEqual(shortcuts.display("help.shortcuts"), "F1")
        self.assertEqual(shortcuts.display("edit.undo"), "Ctrl+Z or Alt+Backspace")
        self.assertEqual(shortcuts.display("mouse.view.pan"), "Middle-drag")

    def test_the_panel_keys_run_ctrl_1_to_6_in_view_menu_order(self) -> None:
        keys = [shortcuts.display(binding_id) for binding_id in shortcuts.PANEL_TOGGLE_IDS.values()]
        self.assertEqual(keys, [f"Ctrl+{n}" for n in range(1, 7)])

    def test_the_text_export_lists_every_binding_under_its_section(self) -> None:
        text = shortcuts.shortcuts_as_text()
        for section in shortcuts.sections():
            self.assertIn(f"\n{section}\n", f"\n{text}")
        for item in shortcuts.BINDINGS:
            self.assertIn(f"{shortcuts.display(item.id)}: {item.label}", text)

    def test_apply_to_an_action_sets_its_keys(self) -> None:
        from PyQt6.QtGui import QAction

        action = QAction("x")
        shortcuts.bind_action(action, "edit.preferences", context=Qt.ShortcutContext.ApplicationShortcut)
        self.assertEqual([s.toString() for s in action.shortcuts()], ["Ctrl+,"])
        self.assertEqual(action.shortcutContext(), Qt.ShortcutContext.ApplicationShortcut)


if __name__ == "__main__":
    unittest.main()
