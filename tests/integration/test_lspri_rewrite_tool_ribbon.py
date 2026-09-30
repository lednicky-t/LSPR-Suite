"""Tests for the LSPRimaging Evaluation rewrite's Image-panel tool ribbon
(`panels/image/tool_ribbon.py`, 2026-09-30) - the two-row category-tab
structure (`ImageToolRibbon`) that replaced the Image panel's single-row
`CanvasToolsBar` embed. See that module's docstring for why the tabs are
plain `QToolButton`s in a `QButtonGroup`, not a `QTabWidget`.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.

Driven by direct method calls (real `QToolButton.click()`), never screen
coordinates - the same pattern `test_lspri_rewrite_canvas_tools_bar.py`
already established.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QLabel, QWidget

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.tool_ribbon import ImageToolRibbon, _ROW_HEIGHT
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


class ImageToolRibbonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.content = QWidget()
        self.ribbon = ImageToolRibbon(
            [("Image tools", None), ("Histogram", None), ("ROIs", self.content)]
        )

    def test_first_category_is_active_by_default(self) -> None:
        self.assertTrue(self.ribbon._tab_buttons[0].isChecked())
        self.assertFalse(self.ribbon._tab_buttons[1].isChecked())
        self.assertFalse(self.ribbon._tab_buttons[2].isChecked())
        self.assertEqual(self.ribbon._stack.currentIndex(), 0)

    def test_clicking_a_tab_switches_the_stack_and_is_exclusive(self) -> None:
        self.ribbon._tab_buttons[2].click()
        self.assertEqual(self.ribbon._stack.currentIndex(), 2)
        self.assertIs(self.ribbon._stack.currentWidget(), self.content)
        self.assertTrue(self.ribbon._tab_buttons[2].isChecked())
        self.assertFalse(self.ribbon._tab_buttons[0].isChecked())

    def test_none_content_renders_the_shared_not_built_yet_placeholder(self) -> None:
        """Same wording `workflow/panel.py`'s `_section_placeholder` uses for
        an unbuilt Workflow stage - an empty ribbon category should read the
        same way an empty Workflow stage already does elsewhere in this app."""
        placeholder = self.ribbon._stack.widget(0)
        self.assertIsInstance(placeholder, QLabel)
        self.assertEqual(placeholder.text(), "Image tools - not built yet.")

    def test_real_content_widget_is_used_as_is(self) -> None:
        self.assertIs(self.ribbon._stack.widget(2), self.content)

    def test_row_height_is_fixed_regardless_of_which_category_is_showing(self) -> None:
        """The whole point of a fixed-height stack (module docstring):
        switching tabs must never resize the Image panel's top bar."""
        self.ribbon._tab_buttons[0].click()
        self.assertEqual(self.ribbon._stack.height(), _ROW_HEIGHT)
        self.ribbon._tab_buttons[2].click()
        self.assertEqual(self.ribbon._stack.height(), _ROW_HEIGHT)

    def test_empty_categories_raises(self) -> None:
        with self.assertRaises(ValueError):
            ImageToolRibbon([])

    def test_refresh_theme_does_not_raise(self) -> None:
        from lspr_ui import BRIGHT_THEME

        self.ribbon.refresh_theme(BRIGHT_THEME)


if __name__ == "__main__":
    unittest.main()
