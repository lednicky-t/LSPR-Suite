"""Bright-theme legibility of the ribbon icons (2026-11-27).

The Mask tab's plain icons were drawn in a literal ``#f8fafc`` (the *dark* theme's text colour: near-white), so they vanished on the Bright
theme's white ribbon; the reference toggle's near-white ring and the unstyled tab strips had the same problem. These tests pin the fixes
without a QApplication."""

from __future__ import annotations

from pathlib import Path

import unittest

from lspr_imaging_app.gui.app_theme import LSPRI_BRIGHT_THEME, LSPRI_DARK_THEME, popup_menu_stylesheet, tab_bar_stylesheet
from lspr_imaging_app.panels.image.roi_overlay_controls import REFERENCE_ON_COLOR, themed_on_color
from lspr_imaging_app.panels.mask_edit_common import plain_icon_color
from lspr_ui import get_active_theme, set_active_theme
from lspr_ui.icons import legible_on_light, tabler_icon_svg

_IMAGE_PANELS = Path(__file__).resolve().parents[2] / "apps" / "LSPRi" / "eva" / "src" / "lspr_imaging_app" / "panels" / "image"


class BrightThemeIconsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._before = get_active_theme()
        self.addCleanup(set_active_theme, self._before)

    def test_plain_icon_color_follows_the_active_theme(self) -> None:
        set_active_theme(LSPRI_DARK_THEME)
        self.assertEqual(plain_icon_color(), LSPRI_DARK_THEME.text_primary)
        set_active_theme(LSPRI_BRIGHT_THEME)
        self.assertEqual(plain_icon_color(), LSPRI_BRIGHT_THEME.text_primary)
        self.assertNotEqual(plain_icon_color(), LSPRI_DARK_THEME.text_primary)

    def test_reference_on_colour_is_darkened_only_on_bright(self) -> None:
        set_active_theme(LSPRI_DARK_THEME)
        self.assertEqual(themed_on_color(REFERENCE_ON_COLOR), REFERENCE_ON_COLOR)
        set_active_theme(LSPRI_BRIGHT_THEME)
        self.assertNotEqual(themed_on_color(REFERENCE_ON_COLOR), REFERENCE_ON_COLOR)
        self.assertEqual(themed_on_color("#c957e8"), "#c957e8")  # any other colour is left alone

    def test_tab_bar_stylesheet_uses_the_theme_colours(self) -> None:
        for theme in (LSPRI_DARK_THEME, LSPRI_BRIGHT_THEME):
            sheet = tab_bar_stylesheet(theme)
            self.assertIn(f"color: {theme.text_primary}", sheet)  # the selected tab's title
            self.assertIn(f"color: {theme.text_muted}", sheet)  # the other tabs' titles

    def test_pale_accent_colours_are_darkened_on_bright_only(self) -> None:
        for pale in ("#84cc16", "#f59e0b", "#fbbf24", "#2dd4bf", "#38bdf8"):
            self.assertNotEqual(legible_on_light(pale).lower(), pale, pale)
        for fine in ("#ef4444", "#1d2733", "#000000"):  # already readable on white: untouched
            self.assertEqual(legible_on_light(fine).lower(), fine)
        set_active_theme(LSPRI_DARK_THEME)
        self.assertIn("#84cc16", tabler_icon_svg("search", color="#84cc16"))  # the dark theme keeps its colours
        set_active_theme(LSPRI_BRIGHT_THEME)
        self.assertNotIn("#84cc16", tabler_icon_svg("search", color="#84cc16"))

    def test_a_submenu_gets_its_fill_back(self) -> None:
        """`QMenu QWidget` is transparent, and a submenu is a QWidget inside a QMenu: without its own rule it showed black."""
        sheet = popup_menu_stylesheet(LSPRI_BRIGHT_THEME)
        self.assertIn("QMenu QMenu", sheet)
        self.assertGreater(sheet.index("QMenu QMenu"), sheet.index("QMenu QWidget"))  # later wins at equal specificity

    def test_mask_ribbon_files_do_not_hard_code_the_dark_text_colour(self) -> None:
        for name in ("mask_edit_panels.py", "mask_scan_panels.py", "mask_selection_actions.py"):
            self.assertNotIn("#f8fafc", (_IMAGE_PANELS / name).read_text(encoding="utf-8").lower(), name)


if __name__ == "__main__":
    unittest.main()
