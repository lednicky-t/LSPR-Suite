"""Tests for the LSPRimaging Evaluation rewrite's rotate-by-line tool, the
ActiveTool state it hangs off, and the Workflow buttons that drive it.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.

Driven by direct method calls (`RotateLineTool.on_left_click`, `.handle_key`,
`ImagePanel._on_scene_clicked` with a small stand-in event, real
`QToolButton.click()`) - never screen coordinates, the same pattern as
`test_lspri_rewrite_image_panel.py`. The angle math itself is pinned against
the real transform in `tests/unit/test_lspri_rewrite_rotation_alignment.py`;
what is pinned here is the *wiring*: the two-click state machine, that a
result is one undo step, that the tools are mutually exclusive, that the
preview is uncropped while rotating, and that ROIs are not clicked through.
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
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.image_controls import ImageViewBox, controls_for, controls_text
    from lspr_imaging_app.panels.workflow.transforms_settings import TransformsSection
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import ReferenceFrameModule, SelectionModule
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
    """A stand-in for `QMenu.exec()` that "clicks" the first enabled action,
    the way a user picking the one available choice would - see
    `RotateToolTest.setUp`."""
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


class ActiveToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = ActiveToolModule()
        self.emitted: list[object] = []
        self.module.active_tool_changed.connect(self.emitted.append)

    def test_starts_with_no_tool(self) -> None:
        self.assertIsNone(self.module.active())

    def test_activating_a_tool_replaces_the_previous_one(self) -> None:
        self.module.set_active(ImageTool.ROTATE, True)
        self.module.set_active(ImageTool.CROP, True)
        self.assertIs(self.module.active(), ImageTool.CROP)
        self.assertFalse(self.module.is_active(ImageTool.ROTATE))
        self.assertEqual(self.emitted, [ImageTool.ROTATE, ImageTool.CROP])

    def test_switching_off_a_tool_that_is_not_active_does_nothing(self) -> None:
        """A stale "off" from a button already replaced by another tool must
        not switch the new tool off."""
        self.module.set_active(ImageTool.ROTATE, True)
        self.module.set_active(ImageTool.CROP, True)
        self.module.set_active(ImageTool.ROTATE, False)
        self.assertIs(self.module.active(), ImageTool.CROP)

    def test_switching_off_the_active_tool_clears_it(self) -> None:
        self.module.set_active(ImageTool.ROTATE, True)
        self.module.set_active(ImageTool.ROTATE, False)
        self.assertIsNone(self.module.active())
        self.assertIsNone(self.emitted[-1])

    def test_no_signal_when_nothing_changes(self) -> None:
        self.module.set_active(ImageTool.ROTATE, True)
        self.module.set_active(ImageTool.ROTATE, True)
        self.module.clear()
        self.module.clear()
        self.assertEqual(len(self.emitted), 2)


class RotateToolTest(unittest.TestCase):
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
            self.roi_toolbox, self.selection, self.active_tool, ReferenceFrameModule(),
        )
        self.tool = self.panel._rotate_tool
        self.status_messages: list[str] = []
        self.panel.tool_status_changed.connect(self.status_messages.append)
        # QMenu.exec() blocks on a real event loop waiting for a click that
        # will never come under test - the right-click context menu
        # (panel.py's _show_rotate_context_menu) is instead driven by
        # patching exec() to auto-pick its one enabled action ("Cancel
        # rotation" is always enabled - see context_menu.py's docstring),
        # the same outcome a user choosing it produces. This mirrors every
        # existing right-click test's expectation (the tool exits
        # immediately); tests that need a *different* menu outcome (the
        # user dismissing it, or inspecting the menu's own contents without
        # choosing anything) patch exec() again locally, which shadows this
        # one for the duration of their `with` block.
        self.enterContext(mock.patch.object(QtWidgets.QMenu, "exec", _pick_first_enabled_action))
        # Shown and sized so the view box has a real on-screen rectangle and
        # a view range covering the image - the panel (rightly) ignores
        # clicks outside it, so an unshown zero-size view would swallow every
        # test click. Offscreen platform: nothing actually appears.
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

    def _rotation(self) -> float:
        return self.geometry.settings().rotation_angle_deg

    def assertPointAlmostEqual(self, actual, expected) -> None:  # noqa: N802
        # Clicks travel view -> scene -> view, which is not bit-exact.
        self.assertIsNotNone(actual)
        np.testing.assert_allclose(actual, expected, atol=1e-6)

    def _click(self, x: float, y: float, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        """A click at *view* coordinate (x, y), routed the way pyqtgraph
        would: view -> scene position -> the panel's own click handler."""
        vb = self.panel._plot.vb
        scene_pos = vb.mapViewToScene(QPointF(x, y))
        self.panel._on_scene_clicked(_Click(scene_pos, button))

    # -- two-click gesture ----------------------------------------------

    def test_clicks_do_nothing_until_the_tool_is_active(self) -> None:
        self._click(10.0, 10.0)
        self._click(70.0, 12.0)
        self.assertEqual(self._rotation(), 0.0)
        self.assertIsNone(self.tool.first_point())

    def test_two_left_clicks_rotate_so_the_points_are_level(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self.assertPointAlmostEqual(self.tool.first_point(), (10.0, 20.0))
        self.assertEqual(self._rotation(), 0.0)  # nothing applied by the first click

        self._click(70.0, 22.0)  # slopes down-right: tan(t) = 2/60
        self.assertIsNone(self.tool.first_point())  # ready for another pair
        self.assertEqual(self._rotation(), 1.91)  # 1.909152 rounded to 0.01 deg

    def test_second_pass_refines_instead_of_restarting(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.geometry.set_rotation(2.0)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertEqual(self._rotation(), 3.91)  # 2.0 + 1.909152, rounded to 0.01 deg

    def test_the_result_is_one_undo_step(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertNotEqual(self._rotation(), 0.0)
        undo_manager.undo()
        self.assertEqual(self._rotation(), 0.0)

    def test_right_click_menus_cancel_action_exits_rotate_mode(self) -> None:
        """The context menu's action, not the right-click itself - `setUp`'s
        patch auto-picks it, standing in for the user choosing it. "Cancel
        rotation" exits Rotate mode entirely (maintainer's spec, 2026-09-
        29 - "same like clicking on tool icon in workflow"), dropping the
        in-progress point along the way - it does not just drop the point
        and stay in Rotate mode ready for another pair."""
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self._click(0.0, 0.0, Qt.MouseButton.RightButton)
        self.assertIsNone(self.active_tool.active())
        self.assertIsNone(self.tool.first_point())
        self.assertEqual(self._rotation(), 0.0)

    def test_right_click_menu_cancel_action_is_always_enabled(self) -> None:
        """Regression (2026-09-29): an earlier version disabled "Cancel
        rotation" with no point 1 pending - which, since it's the menu's
        only item, meant a right-click with nothing pending opened a menu
        with nothing clickable in it at all (looked exactly like a broken
        menu). "Cancel" now always means "exit Rotate mode", which is
        always a valid thing to do, so it is always enabled."""
        self.active_tool.set_active(ImageTool.ROTATE, True)
        seen_menus: list[QtWidgets.QMenu] = []

        def _capture(menu: QtWidgets.QMenu, *_a: object, **_k: object) -> None:
            seen_menus.append(menu)
            return None

        with mock.patch.object(QtWidgets.QMenu, "exec", _capture):
            self._click(0.0, 0.0, Qt.MouseButton.RightButton)  # no point 1 pending
        self.assertEqual(len(seen_menus), 1)
        cancel_action = seen_menus[0].actions()[0]
        self.assertEqual(cancel_action.text(), "Cancel rotation")
        self.assertTrue(cancel_action.isEnabled())
        # The stand-in exec() above never returned an action, so nothing
        # was actually chosen - the tool must still be exactly as it was.
        self.assertIs(self.active_tool.active(), ImageTool.ROTATE)
        self.assertIsNone(self.tool.first_point())

    def test_right_click_menu_dismissed_keeps_point_one(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        with mock.patch.object(QtWidgets.QMenu, "exec", lambda *_a, **_k: None):
            self._click(0.0, 0.0, Qt.MouseButton.RightButton)
        self.assertPointAlmostEqual(self.tool.first_point(), (10.0, 20.0))  # menu dismissed, not cancelled

    def test_escape_cancels_point_one_and_is_only_consumed_when_it_does(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.assertFalse(self.tool.handle_key(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        self._click(10.0, 20.0)
        self.assertTrue(self.tool.handle_key(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        self.assertIsNone(self.tool.first_point())

    def test_middle_click_is_ignored_by_the_tool(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0, Qt.MouseButton.MiddleButton)
        self.assertIsNone(self.tool.first_point())

    def test_two_points_too_close_together_rotate_nothing(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self._click(10.5, 20.0)
        self.assertEqual(self._rotation(), 0.0)
        self.assertIsNone(self.tool.first_point())

    def test_flips_mirror_the_correction(self) -> None:
        self.geometry.set_flip(True, False)
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertEqual(self._rotation(), -1.91)

    def test_rubber_band_follows_the_cursor_only_after_point_one(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.tool.on_mouse_moved(30.0, 30.0)
        self.assertFalse(self.tool._band.isVisible())
        self._click(10.0, 20.0)
        self.tool.on_mouse_moved(70.0, 22.0)
        self.assertTrue(self.tool._band.isVisible())
        xs, ys = self.tool._band.getData()
        np.testing.assert_allclose([list(xs), list(ys)], [[10.0, 70.0], [20.0, 22.0]], atol=1e-6)
        self.assertIn("rotates by", self.status_messages[-1])
        self._click(0.0, 0.0, Qt.MouseButton.RightButton)
        self.assertFalse(self.tool._band.isVisible())

    def test_deactivating_drops_a_pending_point(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self.active_tool.set_active(ImageTool.ROTATE, False)
        self.assertIsNone(self.tool.first_point())
        # The info icon is permanent - it falls back to the plain-image
        # controls instead of disappearing.
        self.assertTrue(self.panel._tool_info.isVisible())
        self.assertEqual(self.panel._tool_info.toolTip(), controls_text(None))

    def test_image_tools_switched_off_blocks_rotation_with_a_message(self) -> None:
        self.geometry.set_image_tools_enabled(False)
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertEqual(self._rotation(), 0.0)
        self.assertIn("switched off", self.status_messages[-1])

    # -- arrow keys -------------------------------------------------------

    def test_arrow_keys_step_like_the_stable_app(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        none, ctrl, shift = (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.ControlModifier,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertTrue(self.tool.handle_key(Qt.Key.Key_Right, none))
        self.assertAlmostEqual(self._rotation(), 0.1)
        self.tool.handle_key(Qt.Key.Key_Up, ctrl)
        self.assertAlmostEqual(self._rotation(), 1.1)
        self.tool.handle_key(Qt.Key.Key_Right, shift)
        self.assertAlmostEqual(self._rotation(), 6.1)
        self.tool.handle_key(Qt.Key.Key_Left, none)
        self.tool.handle_key(Qt.Key.Key_Down, ctrl)
        self.assertAlmostEqual(self._rotation(), 5.0)

    def test_repeated_small_steps_do_not_accumulate_float_noise(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        for _ in range(3):
            self.tool.handle_key(Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(self._rotation(), 0.3)  # exactly, not 0.30000000000000004

    def test_keys_are_ignored_when_the_tool_is_off(self) -> None:
        self.assertFalse(self.tool.handle_key(Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self._rotation(), 0.0)

    def test_the_view_forwards_real_key_events_to_the_tool(self) -> None:
        """Through Qt's event filter, not a direct call - the arrows must
        beat the graphics view's own scrolling."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent

        self.active_tool.set_active(ImageTool.ROTATE, True)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(self.panel._view, event)
        self.assertAlmostEqual(self._rotation(), 0.1)

    # -- panel behavior while rotating --------------------------------------

    def test_preview_is_uncropped_with_a_crop_outline_and_returns_cropped(self) -> None:
        self.geometry.set_crop(8, 6, 48, 40)
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (40, 48))
        outline_x = self.panel._crop_outline_curve.getData()[0]
        self.assertTrue(outline_x is None or len(outline_x) == 0)  # no outline outside the tool

        self.active_tool.set_active(ImageTool.ROTATE, True)
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (64, 80))  # whole image
        xs, ys = self.panel._crop_outline_curve.getData()
        self.assertEqual((min(xs), max(xs), min(ys), max(ys)), (8.0, 56.0, 6.0, 46.0))

        # The crop itself is untouched by rotating, and re-applied on exit.
        self.assertTrue(self.geometry.settings().crop.enabled)
        self.active_tool.set_active(ImageTool.ROTATE, False)
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (40, 48))

    def test_roi_overlay_is_hidden_while_rotating_and_restored_after(self) -> None:
        self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        _pump()
        self.assertGreater(len(self.panel._sample_curve.getData()[0]), 0)
        self.active_tool.set_active(ImageTool.ROTATE, True)
        _pump()
        xs = self.panel._sample_curve.getData()[0]
        self.assertTrue(xs is None or len(xs) == 0)
        self.active_tool.set_active(ImageTool.ROTATE, False)
        _pump()
        self.assertGreater(len(self.panel._sample_curve.getData()[0]), 0)

    def test_clicks_never_select_rois_while_rotating(self) -> None:
        roi_id = self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        self.assertIsNotNone(roi_id)
        _pump()
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self._click(40.0, 30.0)  # exactly on the ROI
        self.assertEqual(self.selection.selected_roi_ids(), set())
        self.active_tool.set_active(ImageTool.ROTATE, False)
        self._click(40.0, 30.0)
        self.assertEqual(len(self.selection.selected_roi_ids()), 1)

    def test_middle_click_never_selects_or_clears(self) -> None:
        self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        _pump()
        self._click(40.0, 30.0)
        self.assertEqual(len(self.selection.selected_roi_ids()), 1)
        self._click(5.0, 5.0, Qt.MouseButton.MiddleButton)  # empty image: would have cleared
        self.assertEqual(len(self.selection.selected_roi_ids()), 1)

    def test_closing_the_dataset_switches_the_tool_off(self) -> None:
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.dataset.clear_dataset()
        _pump()
        self.assertIsNone(self.active_tool.active())

    # -- controls table ---------------------------------------------------

    def test_view_box_only_pans_with_the_middle_button(self) -> None:
        self.assertIsInstance(self.panel._plot.vb, ImageViewBox)

        class _Drag:
            def __init__(self, button: Qt.MouseButton) -> None:
                self._button, self.ignored = button, False

            def button(self) -> Qt.MouseButton:
                return self._button

            def ignore(self) -> None:
                self.ignored = True

        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            drag = _Drag(button)
            self.panel._plot.vb.mouseDragEvent(drag)
            self.assertTrue(drag.ignored, button)

    def test_help_text_lists_the_tools_controls_and_the_always_available_ones(self) -> None:
        text = controls_text(ImageTool.ROTATE)
        for expected in ("Left-click", "Right-click", "Arrow keys", "Middle-drag", "Wheel"):
            self.assertIn(expected, text)
        self.assertEqual(controls_for(ImageTool.ROTATE)[-2:], controls_for(None)[-2:])


class TransformsButtonsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry = GeometryModule()
        self.active_tool = ActiveToolModule()
        self.section = TransformsSection(self.geometry, self.active_tool)

    def test_rotate_button_drives_the_active_tool(self) -> None:
        self.section._rotate_button.click()
        self.assertIs(self.active_tool.active(), ImageTool.ROTATE)
        self.section._rotate_button.click()
        self.assertIsNone(self.active_tool.active())

    def test_rotate_and_crop_buttons_are_mutually_exclusive(self) -> None:
        self.section._rotate_button.click()
        self.section._crop_button.click()
        self.assertIs(self.active_tool.active(), ImageTool.CROP)
        self.assertFalse(self.section._rotate_button.isChecked())
        self.assertTrue(self.section._crop_button.isChecked())

    def test_buttons_follow_an_outside_change(self) -> None:
        self.section._rotate_button.click()
        self.active_tool.clear()  # e.g. a dataset closing
        self.assertFalse(self.section._rotate_button.isChecked())

    # -- rotation fill (2026-09-30, icon toggle -> plain checkbox: the old
    # amber-filled-square icon used the same amber as the Rotate tool
    # button's own "active" color, which read as ambiguous) -----------------

    def test_fill_checkbox_starts_unchecked_for_default_edge_stretch_fill(self) -> None:
        self.assertFalse(self.section._fill_checkbox.isChecked())

    def test_fill_checkbox_drives_rotation_fill_dark(self) -> None:
        self.section._fill_checkbox.click()
        self.assertTrue(self.geometry.settings().rotation_fill_dark)
        self.section._fill_checkbox.click()
        self.assertFalse(self.geometry.settings().rotation_fill_dark)

    def test_fill_checkbox_follows_an_outside_change(self) -> None:
        self.geometry.set_rotation_fill_dark(True)
        self.assertTrue(self.section._fill_checkbox.isChecked())


if __name__ == "__main__":
    unittest.main()
