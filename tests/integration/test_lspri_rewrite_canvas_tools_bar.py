"""Tests for the LSPRimaging Evaluation rewrite's Image-panel canvas tools
bar (`panels/image/canvas_tools.py`, 2026-09-30) - the Select/Add ROI icon
column docked to the canvas, separate from the Workflow panel's Transforms
row, and the click-to-add-ROI gesture it drives through `ImagePanel._on_
scene_clicked`.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.

Driven by direct method calls (real `QToolButton.click()`, `ImagePanel.
_on_scene_clicked` with a small stand-in event) - never screen coordinates,
the same pattern `test_lspri_rewrite_rotate_tool.py` already established.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QAction

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        ImageTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.canvas_tools import CanvasToolsBar, ToolVariant, _ToolGroupButton
    from lspr_imaging_app.panels.image.image_controls import controls_for, controls_text
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402


def _pump(seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _pick_first_enabled_action(menu: QtWidgets.QMenu, *_args: object, **_kwargs: object) -> QAction | None:
    for action in menu.actions():
        if action.isEnabled():
            return action
    return None


def _write_dataset(root: Path) -> ImageDataset:
    rng = np.random.default_rng(3)
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(64, 80)).astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class _Click:
    """The slice of pyqtgraph's MouseClickEvent the panel reads."""

    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton) -> None:
        self._pos, self._button = scene_pos, button

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


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
            "circle-plus", "Add ROI", lambda: active_tool.active() is ImageTool.ADD_ROI,
            lambda: active_tool.set_active(ImageTool.ADD_ROI, True),
        )
        button = _ToolGroupButton([variant])
        self.assertFalse(button.isChecked())
        active_tool.set_active(ImageTool.ADD_ROI, True)  # changed from outside, not via the button
        button.refresh()
        self.assertTrue(button.isChecked())


class CanvasToolsBarButtonsTest(unittest.TestCase):
    """Mirrors test_lspri_rewrite_rotate_tool.py's TransformsButtonsTest,
    for this bar's own two buttons."""

    def setUp(self) -> None:
        self.active_tool = ActiveToolModule()
        self.roi_toolbox = RoiToolbox()
        self.bar = CanvasToolsBar(self.roi_toolbox, self.active_tool)

    def test_select_is_checked_with_no_active_tool_by_default(self) -> None:
        self.assertTrue(self.bar._select_button.isChecked())
        self.assertFalse(self.bar._add_roi_button.isChecked())

    def test_add_roi_button_drives_the_active_tool(self) -> None:
        self.bar._add_roi_button.click()
        self.assertIs(self.active_tool.active(), ImageTool.ADD_ROI)
        self.assertTrue(self.bar._add_roi_button.isChecked())
        self.assertFalse(self.bar._select_button.isChecked())

    def test_select_button_clears_whatever_tool_is_active(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)  # e.g. from the Transforms row
        self.bar._select_button.click()
        self.assertIsNone(self.active_tool.active())

    def test_buttons_follow_an_outside_change(self) -> None:
        self.bar._add_roi_button.click()
        self.active_tool.clear()  # e.g. a dataset closing
        self.assertFalse(self.bar._add_roi_button.isChecked())
        self.assertTrue(self.bar._select_button.isChecked())

    # -- sizing and border (2026-09-30, maintainer request; flipped from a
    # vertical strip on the canvas's left edge to a horizontal bar across
    # its top, same day) -----------------------------------------------------

    def test_buttons_are_sized_to_hug_the_icons(self) -> None:
        """Shrunk from the first pass's 28px (borrowed from the horizontal
        Transforms row) - this strip should read as compact, not a second
        toolbar's worth of size."""
        from lspr_imaging_app.panels.image.canvas_tools import _BUTTON_SIZE, _ICON_SIZE

        self.assertEqual(self.bar._select_button.size().width(), _BUTTON_SIZE)
        self.assertEqual(self.bar._select_button.iconSize().width(), _ICON_SIZE)
        self.assertLess(_BUTTON_SIZE, 28)

    def test_bar_size_hint_hugs_both_buttons(self) -> None:
        """Horizontal now (2026-09-30, flipped from vertical) - the bar's
        own size should not meaningfully exceed its two buttons' width/
        height plus margins and the spacing between them, not leave visible
        slack around the icons."""
        from lspr_imaging_app.panels.image.canvas_tools import _BAR_MARGIN, _BAR_SPACING, _BUTTON_SIZE

        self.assertEqual(self.bar.sizeHint().width(), 2 * _BUTTON_SIZE + _BAR_SPACING + 2 * _BAR_MARGIN)
        self.assertEqual(self.bar.sizeHint().height(), _BUTTON_SIZE + 2 * _BAR_MARGIN)

    def test_bar_has_no_border_of_its_own(self) -> None:
        """The seam moved to `panel.py`'s wrapping top bar (2026-09-30,
        flipped from a vertical strip to a horizontal one) - this widget no
        longer sits directly against the canvas, so it draws none itself."""
        style = self.bar.styleSheet()
        self.assertIn("border: none", style)
        self.assertNotIn("border-right", style)
        self.assertNotIn("border-left", style)
        self.assertNotIn("border-top", style)
        self.assertNotIn("border-bottom", style)

    def test_refresh_theme_does_not_raise(self) -> None:
        """Still called on every live theme switch (`ImagePanel.
        refresh_theme`) even though it sets no border color today - pinned
        so a future change here can't silently reintroduce a crash on
        theme switch without a test catching it."""
        from lspr_ui import BRIGHT_THEME

        self.bar.refresh_theme(BRIGHT_THEME)
        self.assertIn("border: none", self.bar.styleSheet())


class AddRoiToolTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))

        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.active_tool = ActiveToolModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, MaskModule(), ChromaticModule(), BackgroundModule(),
            self.roi_toolbox, self.selection, self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )
        self.enterContext(mock.patch.object(QtWidgets.QMenu, "exec", _pick_first_enabled_action))
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        _pump(0.1)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def _click(self, x: float, y: float, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        vb = self.panel._plot.vb
        scene_pos = vb.mapViewToScene(QPointF(x, y))
        self.panel._on_scene_clicked(_Click(scene_pos, button))

    def test_clicks_do_nothing_until_the_tool_is_active(self) -> None:
        self._click(10.0, 10.0)
        self.assertEqual(self.roi_toolbox.rois(), ())

    def test_click_places_a_real_roi_at_the_clicked_point(self) -> None:
        self.active_tool.set_active(ImageTool.ADD_ROI, True)  # no icon any more: armed from code
        self._click(40.0, 30.0)
        rois = self.roi_toolbox.rois()
        self.assertEqual(len(rois), 1)
        self.assertAlmostEqual(rois[0].center_x, 40.0, places=5)
        self.assertAlmostEqual(rois[0].center_y, 30.0, places=5)

    def test_tool_stays_active_for_placing_several_rois_in_a_row(self) -> None:
        self.active_tool.set_active(ImageTool.ADD_ROI, True)  # no icon any more: armed from code
        self._click(20.0, 20.0)
        self.assertIs(self.active_tool.active(), ImageTool.ADD_ROI)
        self._click(50.0, 40.0)
        self.assertEqual(len(self.roi_toolbox.rois()), 2)

    def test_each_placement_is_its_own_undo_step(self) -> None:
        self.active_tool.set_active(ImageTool.ADD_ROI, True)  # no icon any more: armed from code
        self._click(20.0, 20.0)
        self._click(50.0, 40.0)
        self.assertEqual(len(self.roi_toolbox.rois()), 2)
        undo_manager.undo()
        self.assertEqual(len(self.roi_toolbox.rois()), 1)

    def test_right_click_menu_exit_tool_action_exits_the_tool(self) -> None:
        self.active_tool.set_active(ImageTool.ADD_ROI, True)  # no icon any more: armed from code
        self._click(0.0, 0.0, Qt.MouseButton.RightButton)
        self.assertIsNone(self.active_tool.active())
        self.assertEqual(self.roi_toolbox.rois(), ())  # right-click placed nothing

    def test_roi_overlay_stays_visible_while_adding_unlike_rotate(self) -> None:
        """Add ROI is not a _PREVIEW_TOOLS member - it works in already-
        processed/cropped space, so unlike Rotate/Crop the ROI overlay must
        not be hidden while it is active."""
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        _pump()
        self.active_tool.set_active(ImageTool.ADD_ROI, True)  # no icon any more: armed from code
        _pump()
        xs = self.panel._roi_overlay.sample_curve.getData()[0]
        self.assertIsNotNone(xs)
        self.assertGreater(len(xs), 0)

    def test_mutually_exclusive_with_rotate(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.active_tool.set_active(ImageTool.ADD_ROI, True)  # no icon any more: armed from code
        self.assertIs(self.active_tool.active(), ImageTool.ADD_ROI)
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.assertIs(self.active_tool.active(), ImageTool.ROTATE)

    def test_help_text_lists_the_tools_controls(self) -> None:
        text = controls_text(ImageTool.ADD_ROI)
        for expected in ("Left-click", "Right-click", "Middle-drag", "Wheel"):
            self.assertIn(expected, text)
        self.assertEqual(controls_for(ImageTool.ADD_ROI)[-2:], controls_for(None)[-2:])


if __name__ == "__main__":
    unittest.main()
