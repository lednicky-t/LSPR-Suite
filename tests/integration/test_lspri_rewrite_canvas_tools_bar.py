"""Tests for the flyout-group button in `panels/image/canvas_tools.py` (`_ToolGroupButton`, 2026-09-30).

The Image panel's own tool bar (`CanvasToolsBar`: Select, Add ROI), the click-to-add ROI tool (`ImageTool.ADD_ROI`) and the ROIs tab's Shape picker
were deleted 2026-11-24 (nothing could turn the tool on any more since 2026-10-07), and their tests with them. What stays is the flyout button itself,
which is kept ready for a future multi-member tool group.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.

Driven by direct method calls (real `QToolButton.click()`), never screen coordinates.
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
    from lspr_imaging_app.image_tools import ActiveToolModule, ImageTool
    from lspr_imaging_app.panels.image.canvas_tools import ToolVariant, _ToolGroupButton
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class ToolGroupButtonTest(unittest.TestCase):
    """The flyout mechanism itself, independent of any real tool - a
    single-variant group behaves like a plain toggle button; a multi-variant
    group (synthetic here, since no real tool needs grouping yet) switches
    into MenuButtonPopup mode and remembers whichever variant was last
    picked from the menu."""

    def test_single_variant_has_no_popup_menu(self) -> None:
        calls: list[str] = []
        variant = ToolVariant("pointer", "Only one", lambda: False, lambda: calls.append("activated"))
        button = _ToolGroupButton([variant])
        self.assertIsNone(button.menu())
        button.click()
        self.assertEqual(calls, ["activated"])

    def test_multi_variant_builds_a_menu_and_switching_updates_the_icon(self) -> None:
        state = {"current": "a"}
        variant_a = ToolVariant("pointer", "A", lambda: state["current"] == "a", lambda: state.__setitem__("current", "a"))
        variant_b = ToolVariant("circle-plus", "B", lambda: state["current"] == "b", lambda: state.__setitem__("current", "b"))
        button = _ToolGroupButton([variant_a, variant_b])
        self.assertIsNotNone(button.menu())
        self.assertEqual(
            button.popupMode(), QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        actions = button.menu().actions()
        self.assertEqual([a.text() for a in actions], ["A", "B"])

        actions[1].trigger()
        self.assertEqual(state["current"], "b")
        # A plain click now re-runs B, the last-picked variant, not A.
        state["current"] = "a"
        button.click()
        self.assertEqual(state["current"], "b")

    def test_refresh_reflects_whichever_variant_reports_active_from_elsewhere(self) -> None:
        active_tool = ActiveToolModule()
        variant = ToolVariant(
            "ruler", "Measure", lambda: active_tool.active() is ImageTool.MEASURE,
            lambda: active_tool.set_active(ImageTool.MEASURE, True),
        )
        button = _ToolGroupButton([variant])
        self.assertFalse(button.isChecked())
        active_tool.set_active(ImageTool.MEASURE, True)  # changed from outside, not via the button
        button.refresh()
        self.assertTrue(button.isChecked())


if __name__ == "__main__":
    unittest.main()
