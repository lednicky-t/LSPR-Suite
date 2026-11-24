"""Esc leaves the active canvas tool (`panels/image/tool_escape.py`, 2026-11-19, maintainer: "make every tool exit by Esc")."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDoubleSpinBox, QPushButton, QVBoxLayout, QWidget

    from lspr_imaging_app.dataset import DatasetModule
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
    from lspr_imaging_app.panels.image.tool_escape import ToolEscape
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import AreaSelectionMode, HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.shortcuts import binding, display
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
except ImportError as exc:  # pragma: no cover
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])


def _press_esc(widget: QWidget) -> None:
    """A real key press, through the shortcut system (`QTest` sends the shortcut override first, as a key from the keyboard does)."""
    QTest.keyClick(widget, Qt.Key.Key_Escape)
    _pump(0.05)


class ToolEscapeUnitTest(unittest.TestCase):
    """The action alone, on a bare widget with a fake tool key handler."""

    def setUp(self) -> None:
        self.window = QWidget()
        layout = QVBoxLayout(self.window)
        self.button = QPushButton("tool", self.window)
        self.spin = QDoubleSpinBox(self.window)
        self.elsewhere = QPushButton("elsewhere")
        layout.addWidget(self.button)
        layout.addWidget(self.spin)
        self.tool = ActiveToolModule()
        self.dropped: list[bool] = []  # what the fake `handle_key` answers, in turn
        self.keys: list[int] = []

        def handle_key(key, modifiers):
            self.keys.append(int(key))
            return self.dropped.pop(0) if self.dropped else False

        self.escape = ToolEscape(self.tool, handle_key, action_parent=self.window)
        self.elsewhere.show()  # (first: the window that is shown and activated last is the active one)
        self.window.show()
        self.window.activateWindow()
        _pump(0.1)

    def tearDown(self) -> None:
        self.window.close()
        self.elsewhere.close()

    def test_the_key_is_esc_and_lives_in_the_shortcut_table(self) -> None:
        self.assertEqual(self.escape.action.shortcut(), QKeySequence("Esc"))
        self.assertEqual(binding("tools.cancel").keys, ("Esc",))
        self.assertEqual(display("tools.cancel"), "Esc")

    def test_with_no_tool_on_the_action_is_disabled_so_esc_reaches_whatever_has_focus(self) -> None:
        self.assertFalse(self.escape.action.isEnabled())
        self.assertFalse(self.escape.escape())
        self.tool.set_active(ImageTool.MEASURE, True)
        self.assertTrue(self.escape.action.isEnabled())
        self.tool.set_active(ImageTool.MEASURE, False)
        self.assertFalse(self.escape.action.isEnabled())

    def test_every_tool_is_left_by_esc_when_it_has_nothing_in_progress(self) -> None:
        for tool in ImageTool:
            self.tool.set_active(tool, True)
            self.button.setFocus()
            _pump(0.02)
            _press_esc(self.button)
            self.assertIsNone(self.tool.active(), f"{tool} is still on after Esc")

    def test_esc_is_two_steps_when_the_tool_is_in_the_middle_of_something(self) -> None:
        self.tool.set_active(ImageTool.ROTATE, True)
        self.dropped = [True]  # the first Esc: the tool dropped its first point
        self.button.setFocus()
        _pump(0.02)
        _press_esc(self.button)
        self.assertIs(self.tool.active(), ImageTool.ROTATE, "the first Esc only drops what the tool was doing")
        _press_esc(self.button)
        self.assertIsNone(self.tool.active(), "the next one leaves the tool")
        self.assertEqual(self.keys, [int(Qt.Key.Key_Escape)] * 2)

    def test_esc_in_a_text_field_belongs_to_the_field(self) -> None:
        self.tool.set_active(ImageTool.CROP, True)
        self.spin.setFocus()
        _pump(0.05)
        self.assertFalse(self.escape.action.isEnabled(), "typing a value: the tool is not switched off under the user")
        _press_esc(self.spin)
        self.assertIs(self.tool.active(), ImageTool.CROP)
        self.button.setFocus()
        _pump(0.05)
        self.assertTrue(self.escape.action.isEnabled())

    def test_esc_in_another_window_does_nothing(self) -> None:
        self.tool.set_active(ImageTool.MEASURE, True)
        self.elsewhere.activateWindow()
        self.elsewhere.setFocus()
        _pump(0.05)
        _press_esc(self.elsewhere)
        self.assertIs(self.tool.active(), ImageTool.MEASURE)


class ImagePanelEscapeTest(unittest.TestCase):
    """The real Image panel: every tool of the toolbars, left with Esc."""

    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.selection = SelectionModule()
        self.active_tool = ActiveToolModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            self.selection, self.active_tool, ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.panel.activateWindow()
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump(0.3)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def test_the_panel_has_the_esc_action(self) -> None:
        self.assertIn(self.panel._tool_escape.action, self.panel.actions())

    def test_every_canvas_tool_is_left_by_esc_from_the_image(self) -> None:
        for tool in ImageTool:
            self.active_tool.set_active(tool, True)
            _pump(0.05)
            _press_esc(self.panel._view)
            self.assertIsNone(self.active_tool.active(), f"{tool}: Esc on the image did not leave it")

    def test_every_selection_tool_is_left_by_esc_and_the_selection_stays(self) -> None:
        for mode in (m for m in AreaSelectionMode if m is not AreaSelectionMode.ALL):
            self.panel._area_selection.set_mode(mode)
            self.active_tool.set_active(ImageTool.SELECT_AREA, True)
            _pump(0.05)
            _press_esc(self.panel._view)
            self.assertIsNone(self.active_tool.active(), f"{mode}: Esc did not leave the tool")

    def test_esc_works_with_the_focus_on_a_toolbar_button_of_the_panel(self) -> None:
        """The focus is not on the image: the selection toolbar's buttons are in the panel too."""
        self.panel._area_selection.set_mode(AreaSelectionMode.RECTANGLE)
        self.active_tool.set_active(ImageTool.SELECT_AREA, True)
        button = self.panel._selection_tools.toolbar._tool_buttons[AreaSelectionMode.RECTANGLE]
        button.setFocus()
        _pump(0.05)
        _press_esc(button)
        self.assertIsNone(self.active_tool.active())

    def test_arming_a_tool_puts_the_keyboard_on_the_image(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        _pump(0.05)
        self.assertIs(QApplication.focusWidget(), self.panel._view)

    def test_rotate_drops_its_first_point_with_the_first_esc_and_is_left_by_the_second(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        _pump(0.05)
        self.panel._rotate_tool.on_left_click(10.0, 10.0)
        self.assertIsNotNone(self.panel._rotate_tool.first_point())
        _press_esc(self.panel._view)
        self.assertIsNone(self.panel._rotate_tool.first_point())
        self.assertIs(self.active_tool.active(), ImageTool.ROTATE)
        _press_esc(self.panel._view)
        self.assertIsNone(self.active_tool.active())

    def test_with_no_tool_on_esc_does_nothing(self) -> None:
        self.assertIsNone(self.active_tool.active())
        self.assertFalse(self.panel._tool_escape.action.isEnabled())
        _press_esc(self.panel._view)
        self.assertIsNone(self.active_tool.active())


if __name__ == "__main__":
    unittest.main()
