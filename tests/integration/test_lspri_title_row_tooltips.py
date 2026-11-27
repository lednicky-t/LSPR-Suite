"""The panel title rows leave no bare style-sheet declaration behind (2026-11-27).

Qt styles a tooltip as a child of the widget it belongs to, and a widget's sheet beats the application's whatever the specificity. A *bare*
declaration (``background: transparent;``, ``color: ...;``) applies to the widget and everything under it, tooltip included, so the tooltips
of a panel's title row (``ROI / Groups``, the status fields, the buttons) came out transparent / in a stale colour: dark on the Bright theme.
`dock_container.own_style` scopes a declaration to a property only the widget itself carries, which a tooltip does not match.

Non-visual: the tests read the widgets' style sheets, they take no screenshot. A real `QApplication`, never `.exec()`.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.dock_container import PanelContainer, TitleChoice, own_style
    from lspr_imaging_app.panels.subtitle_column import SubtitleColumn
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _is_bare(sheet: str) -> bool:
    """A sheet with no selector at all (``"color: red;"``): it applies to the widget and to every tooltip shown for it."""
    return bool(sheet.strip()) and "{" not in sheet


class TitleRowTooltipsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = QtWidgets.QMainWindow()
        self.dock = PanelContainer("ROI / Groups", QtWidgets.QWidget(), self.window)
        self.dock.set_title_choices((TitleChoice("ROI", "roi tip"), TitleChoice("Groups", "groups tip")), current=0)
        self.dock.set_subtitle("subtitle", "subtitle tip")
        self.window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.window.show()
        _APP.processEvents()
        self.addCleanup(self.window.close)

    def _title_row_widgets(self) -> list[QtWidgets.QWidget]:
        bar = self.dock.titleBarWidget()
        self.assertIsNotNone(bar)
        return [bar, *bar.findChildren(QtWidgets.QWidget)]

    def _assert_no_bare_sheet(self) -> None:
        widgets = self._title_row_widgets()
        self.assertGreater(len(widgets), 6)  # the bar, its rows, the words, the subtitle, the buttons
        for widget in widgets:
            self.assertFalse(_is_bare(widget.styleSheet()), f"{type(widget).__name__} has a bare style sheet: {widget.styleSheet()!r}")

    def test_no_title_row_widget_has_a_bare_style_sheet(self) -> None:
        self._assert_no_bare_sheet()

    def test_still_none_after_a_theme_refresh_and_with_status_fields(self) -> None:
        self.dock.set_subtitle_columns((SubtitleColumn("Dark", "role tip", "#ff8800"), SubtitleColumn("", "comment tip", None)))
        self.dock.refresh_theme()
        _APP.processEvents()
        self._assert_no_bare_sheet()

    def test_the_title_words_keep_their_tooltips(self) -> None:
        tips = {widget.toolTip() for widget in self._title_row_widgets()}
        self.assertTrue({"roi tip", "groups tip"} <= tips)

    def test_own_style_marks_only_its_widget(self) -> None:
        label = QtWidgets.QLabel("x")
        own_style(label, "color: red;")
        self.assertEqual(label.property("lsprOwnStyle"), "1")
        self.assertFalse(_is_bare(label.styleSheet()))
        self.assertIn('[lsprOwnStyle="1"]', label.styleSheet())
        self.assertIn("color: red;", label.styleSheet())


if __name__ == "__main__":
    unittest.main()
