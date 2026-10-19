"""Tests for the View menu layout of the LSPRi rewrite (2026-10-19).

Panels (Show/Hide all, then one checkable entry per panel with Ctrl+1..6) sit
directly at the top of View - no "Panels" submenu - then Panel Presets, then
Theme at the bottom. ROI / Groups is listed right under Histogram.

A real `QApplication` built in-process, never `.exec()`; the window is the real
`build_main_window()`.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.app_rewrite import build_main_window
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_PANEL_ORDER = ["Workflow", "Image", "Histogram", "ROI / Groups", "Spectra", "Sensorgram"]


class ViewMenuLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = build_main_window()
        view = next(a.menu() for a in self.window.menuBar().actions() if a.text() == "&View")
        self.entries = [a for a in view.actions() if not a.isSeparator()]

    def tearDown(self) -> None:
        self.window.close()

    def test_panels_come_first_in_the_requested_order(self) -> None:
        texts = [a.text() for a in self.entries]
        self.assertEqual(texts[:2], ["Show all panels", "Hide all panels"])
        self.assertEqual(texts[2:8], _PANEL_ORDER)

    def test_presets_follow_the_panels_and_theme_is_last(self) -> None:
        texts = [a.text() for a in self.entries]
        self.assertEqual(texts[8:], ["Panel Presets", "Theme"])
        self.assertNotIn("Panels", texts, "the old Panels submenu must be gone")

    def test_shortcuts_follow_the_menu_order(self) -> None:
        shortcuts = {a.text(): a.shortcut().toString() for a in self.entries[2:8]}
        self.assertEqual(
            shortcuts,
            dict(zip(_PANEL_ORDER, ["Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6"])),
        )

    def test_panel_entries_toggle_their_dock(self) -> None:
        self.window.show()
        _APP.processEvents()
        entry = next(a for a in self.entries if a.text() == "ROI / Groups")
        self.assertTrue(entry.isChecked())
        entry.trigger()
        _APP.processEvents()
        self.assertFalse(entry.isChecked())
        hide_all = next(a for a in self.entries if a.text() == "Hide all panels")
        show_all = next(a for a in self.entries if a.text() == "Show all panels")
        hide_all.trigger()
        _APP.processEvents()
        self.assertFalse(any(a.isChecked() for a in self.entries[2:8]))
        show_all.trigger()
        _APP.processEvents()
        self.assertTrue(all(a.isChecked() for a in self.entries[2:8]))


if __name__ == "__main__":
    unittest.main()
