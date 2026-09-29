"""Tests for the LSPRimaging Evaluation rewrite's crop tool
(`panels/image/crop_tool.py`), the shared right-click context-menu helper
it introduced (`panels/image/context_menu.py`), and the floating size
fields (`panels/image/crop_size_controls.py`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.

Two layers, matching `test_lspri_rewrite_rotate_tool.py`'s split:

- `CropToolTest` drives `CropTool` directly (`begin_gesture`/
  `update_gesture`/`end_gesture`/`set_size`/`apply`/`cancel`), no
  `ImagePanel` involved - this is where the clamping rules live, and they
  are the highest-risk part of the whole feature (two deliberately
  *different* clamp behaviors - see `crop_tool.py`'s module docstring -
  are an easy place for a copy-paste mistake to go unnoticed).
- `CropToolPanelIntegrationTest` builds a real `ImagePanel` (like
  `RotateToolTest`) and confirms the wiring: a real drag through
  `ImageViewBox`'s left-drag handler reaches the tool, a plain click still
  doesn't select ROIs, the right-click menu, the preview-uncropped render,
  and the floating size-controls widget.

Never screen coordinates - direct method calls and small stand-in events,
the same pattern as the rest of this app's GUI tests.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pyqtgraph as pg
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
    from lspr_imaging_app.panels.image.crop_tool import CropTool
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
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
    rng = np.random.default_rng(4)
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(64, 80)).astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class CropToolTest(unittest.TestCase):
    """`CropTool` on a real (shown, ranged) `pg.PlotItem` - no `ImagePanel`,
    no dataset. A real, sized view is required: hit-testing's constant-
    screen-pixel grab margin (`viewPixelSize()`) needs an actual scene
    geometry to convert from, which a bare, unshown `pg.PlotItem()` does
    not have. Frame size is a 100x80 rectangle throughout unless a test
    says otherwise."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.geometry = GeometryModule()
        self.view = pg.GraphicsLayoutWidget()
        self.plot = self.view.addPlot()
        self.view.resize(400, 400)
        self.view.show()
        self.plot.vb.setRange(xRange=(0.0, 100.0), yRange=(0.0, 80.0), padding=0.0)
        self.tool = CropTool(self.plot, self.geometry)
        self.tool.set_frame_size(100, 80)

    def tearDown(self) -> None:
        self.view.close()
        undo_manager.clear()

    # -- activation / pre-fill -------------------------------------------

    def test_starts_inactive_with_no_rect(self) -> None:
        self.assertFalse(self.tool.is_active())
        self.assertIsNone(self.tool.rect())

    def test_activating_with_no_existing_crop_starts_empty(self) -> None:
        self.tool.set_active(True)
        self.assertIsNone(self.tool.rect())

    def test_activating_pre_fills_from_an_existing_crop(self) -> None:
        """Maintainer's explicit choice (2026-09-29): re-entering Crop with
        one already applied starts right there, not from a blank slate."""
        self.geometry.set_crop(10, 5, 40, 30)
        self.tool.set_active(True)
        self.assertEqual(self.tool.rect(), (10, 5, 40, 30))

    def test_deactivating_clears_the_rect(self) -> None:
        self.geometry.set_crop(10, 5, 40, 30)
        self.tool.set_active(True)
        self.tool.set_active(False)
        self.assertIsNone(self.tool.rect())

    # -- "new" gesture: draw from scratch ---------------------------------

    def test_new_drag_defines_a_rectangle(self) -> None:
        self.tool.set_active(True)
        self.assertTrue(self.tool.begin_gesture(10, 10))
        self.tool.update_gesture(50, 40)
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (10, 10, 40, 30))

    def test_new_drag_normalizes_reversed_corners(self) -> None:
        """Dragging from the bottom-right corner up to the top-left must
        produce the same normalized rectangle as the other direction."""
        self.tool.set_active(True)
        self.tool.begin_gesture(50, 40)
        self.tool.update_gesture(10, 10)
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (10, 10, 40, 30))

    def test_new_drag_too_small_is_discarded(self) -> None:
        """An accidental sub-threshold click-drag in the darkened area
        outside an existing rectangle must not replace it with a sliver."""
        self.geometry.set_crop(10, 10, 40, 30)
        self.tool.set_active(True)
        self.tool.begin_gesture(90, 5)  # well outside the existing rect
        self.tool.update_gesture(91, 6)  # 1x1 px - below _MIN_SIZE
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (10, 10, 40, 30))

    def test_new_drag_clamped_to_frame(self) -> None:
        self.tool.set_active(True)
        self.tool.begin_gesture(-20, -20)
        self.tool.update_gesture(150, 120)
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (0, 0, 100, 80))

    # -- "move" gesture: drag the interior ---------------------------------

    def test_drag_interior_moves_the_rectangle(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.begin_gesture(15, 15)  # inside the rect
        self.tool.update_gesture(25, 30)  # +10, +15
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (20, 25, 20, 20))

    def test_move_clamped_at_frame_edge_without_resizing(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.begin_gesture(15, 15)
        self.tool.update_gesture(-500, -500)  # drag far past the top-left corner
        self.tool.end_gesture()
        x, y, w, h = self.tool.rect()
        self.assertEqual((x, y), (0, 0))
        self.assertEqual((w, h), (20, 20))  # size never changes on a move

    # -- resize: edges and corners -----------------------------------------

    def test_drag_right_edge_resizes_width_only(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.assertEqual(self.tool.begin_gesture(30, 20), True)  # right edge, x=30
        self.tool.update_gesture(50, 20)
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (10, 10, 40, 20))

    def test_drag_left_edge_keeps_the_right_edge_fixed(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.begin_gesture(10, 20)  # left edge
        self.tool.update_gesture(0, 20)
        self.tool.end_gesture()
        x, y, w, h = self.tool.rect()
        self.assertEqual(x + w, 30)  # right edge (10 + 20) never moved
        self.assertEqual((x, w), (0, 30))

    def test_drag_corner_resizes_two_sides(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.begin_gesture(10, 10)  # top-left corner
        self.tool.update_gesture(0, 0)
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (0, 0, 30, 30))  # right/bottom (30, 30) fixed

    def test_resize_never_reflects_past_the_frame_boundary(self) -> None:
        """Unlike a size-field edit, dragging the right edge past the frame
        boundary simply stops there - the left edge must never move."""
        self.geometry.set_crop(60, 10, 20, 20)  # right edge at 80
        self.tool.set_active(True)
        self.tool.begin_gesture(80, 20)  # right edge
        self.tool.update_gesture(500, 20)  # drag far past the frame (100 wide)
        self.tool.end_gesture()
        self.assertEqual(self.tool.rect(), (60, 10, 40, 20))  # capped at x=100, left untouched

    def test_resize_respects_minimum_size(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.begin_gesture(30, 20)  # right edge
        self.tool.update_gesture(5, 20)  # drag past the left edge
        self.tool.end_gesture()
        _x, _y, w, _h = self.tool.rect()
        self.assertGreaterEqual(w, 4)

    # -- hover hinting -------------------------------------------------

    def test_hover_handle_identifies_corners_edges_and_move(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)  # x in [10, 30], y in [10, 30]
        self.tool.set_active(True)
        self.assertEqual(self.tool.hover_handle(10, 10), "nw")
        self.assertEqual(self.tool.hover_handle(30, 10), "ne")
        self.assertEqual(self.tool.hover_handle(10, 30), "sw")
        self.assertEqual(self.tool.hover_handle(30, 30), "se")
        self.assertEqual(self.tool.hover_handle(20, 10), "n")
        self.assertEqual(self.tool.hover_handle(20, 20), "move")
        self.assertIsNone(self.tool.hover_handle(90, 70))

    def test_hover_handle_none_when_inactive(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.assertIsNone(self.tool.hover_handle(20, 20))

    # -- size fields ------------------------------------------------------

    def test_set_size_resizes_from_the_top_left_anchor(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.set_size(30, 15)
        self.assertEqual(self.tool.rect(), (10, 10, 30, 15))

    def test_set_size_reflects_the_anchor_when_it_would_overflow(self) -> None:
        """Maintainer's spec: if the requested size doesn't fit from the
        current anchor, the anchor is pushed back just enough to fit."""
        self.geometry.set_crop(60, 10, 20, 20)  # right edge at 80, frame is 100 wide
        self.tool.set_active(True)
        self.tool.set_size(60, 20)  # would need right=120
        x, y, w, h = self.tool.rect()
        self.assertEqual(w, 60)
        self.assertEqual(x + w, 100)  # pushed left just enough to fit inside the frame
        self.assertEqual(x, 40)

    def test_set_size_clamped_to_frame_max(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.set_size(500, 500)
        self.assertEqual(self.tool.rect(), (0, 0, 100, 80))

    # -- apply / cancel -----------------------------------------------------

    def test_apply_commits_to_geometry_module_as_one_undo_step(self) -> None:
        self.tool.set_active(True)
        self.tool.begin_gesture(10, 10)
        self.tool.update_gesture(50, 40)
        self.tool.end_gesture()
        self.assertTrue(self.tool.apply())
        self.assertEqual(
            (self.geometry.settings().crop.x, self.geometry.settings().crop.y),
            (10, 10),
        )
        self.assertEqual(
            (self.geometry.settings().crop.width, self.geometry.settings().crop.height),
            (40, 30),
        )
        undo_manager.undo()
        self.assertFalse(self.geometry.settings().crop.enabled)

    def test_apply_stays_editable_and_can_be_applied_again(self) -> None:
        """The core "stays live" requirement: apply does not end the
        session - a second drag and a second apply must both still work."""
        self.tool.set_active(True)
        self.tool.begin_gesture(10, 10)
        self.tool.update_gesture(50, 40)
        self.tool.end_gesture()
        self.tool.apply()
        self.assertTrue(self.tool.is_active())
        self.assertEqual(self.tool.rect(), (10, 10, 40, 30))

        # Refine and re-apply.
        self.tool.begin_gesture(50, 40)  # bottom-right corner
        self.tool.update_gesture(70, 60)
        self.tool.end_gesture()
        self.assertTrue(self.tool.apply())
        self.assertEqual(
            (self.geometry.settings().crop.width, self.geometry.settings().crop.height),
            (60, 50),
        )

    def test_apply_blocked_when_image_tools_disabled(self) -> None:
        self.geometry.set_image_tools_enabled(False)
        self.tool.set_active(True)
        self.tool.begin_gesture(10, 10)
        self.tool.update_gesture(50, 40)
        self.tool.end_gesture()
        self.assertFalse(self.tool.apply())
        self.assertFalse(self.geometry.settings().crop.enabled)

    def test_cancel_reverts_unapplied_edits_to_the_last_applied_crop(self) -> None:
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.set_size(50, 50)
        self.assertTrue(self.tool.cancel())
        self.assertEqual(self.tool.rect(), (10, 10, 20, 20))

    def test_cancel_with_nothing_applied_clears_the_rect(self) -> None:
        self.tool.set_active(True)
        self.tool.begin_gesture(10, 10)
        self.tool.update_gesture(50, 40)
        self.tool.end_gesture()
        self.assertTrue(self.tool.cancel())
        self.assertIsNone(self.tool.rect())

    def test_cancel_does_nothing_when_nothing_is_pending(self) -> None:
        self.tool.set_active(True)
        self.assertFalse(self.tool.cancel())

    def test_escape_discards_a_pending_edit_but_stays_active(self) -> None:
        """Esc (`handle_key`) is the one real, reachable caller of
        `cancel()` left in the app - the right-click menu's "Cancel crop"
        goes straight to deactivating the tool instead (panel.py), the
        same way Rotate's Esc/right-click-menu split works."""
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.set_size(50, 50)  # an unapplied edit
        self.assertTrue(self.tool.handle_key(Qt.Key.Key_Escape))
        self.assertEqual(self.tool.rect(), (10, 10, 20, 20))
        self.assertTrue(self.tool.is_active())

    def test_escape_is_not_consumed_when_there_is_nothing_to_cancel(self) -> None:
        self.tool.set_active(True)
        self.assertFalse(self.tool.handle_key(Qt.Key.Key_Escape))

    def test_other_keys_are_never_consumed(self) -> None:
        self.tool.set_active(True)
        self.assertFalse(self.tool.handle_key(Qt.Key.Key_Right))

    def test_has_pending_changes_tracks_the_diff_from_applied(self) -> None:
        self.tool.set_active(True)
        self.assertFalse(self.tool.has_pending_changes())
        self.tool.begin_gesture(10, 10)
        self.tool.update_gesture(50, 40)
        self.tool.end_gesture()
        self.assertTrue(self.tool.has_pending_changes())
        self.tool.apply()
        self.assertFalse(self.tool.has_pending_changes())

    def test_external_crop_change_while_active_is_adopted(self) -> None:
        """Reset Crop (or an undo/redo) firing while this tool is active
        and idle must not leave a stale rectangle on screen."""
        self.geometry.set_crop(10, 10, 20, 20)
        self.tool.set_active(True)
        self.tool.set_size(50, 50)  # an unapplied edit
        self.geometry.clear_crop()  # external change, e.g. the Reset-crop button
        self.assertIsNone(self.tool.rect())

    # -- frame-size reclamp -------------------------------------------------

    def test_shrinking_the_frame_repositions_an_out_of_bounds_rect(self) -> None:
        self.geometry.set_crop(70, 50, 20, 20)  # fits the initial 100x80 frame
        self.tool.set_active(True)
        self.tool.set_frame_size(60, 40)  # dataset switched to a smaller frame
        x, y, w, h = self.tool.rect()
        self.assertEqual((w, h), (20, 20))  # size preserved
        self.assertEqual(x + w, 60)
        self.assertEqual(y + h, 40)


class CropToolPanelIntegrationTest(unittest.TestCase):
    """`CropTool` wired into a real `ImagePanel` - the drag plumbing
    through `ImageViewBox`, ROI-click suppression, the right-click menu,
    and the floating size-controls widget."""

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
            self.roi_toolbox, self.selection, self.active_tool,
        )
        self.tool = self.panel._crop_tool
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

    def _drag_event(self, x: float, y: float, *, start: bool = False, finish: bool = False) -> _Drag:
        scene_pos = self.panel._plot.vb.mapViewToScene(QPointF(x, y))
        return _Drag(scene_pos, start=start, finish=finish)

    def test_activating_crop_makes_the_tool_active(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.assertTrue(self.tool.is_active())
        self.active_tool.set_active(ImageTool.CROP, False)
        self.assertFalse(self.tool.is_active())

    def test_drag_through_the_view_box_creates_a_rect(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.assertTrue(self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True)))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.assertEqual(self.tool.rect(), (10, 10, 40, 30))

    def test_left_drag_does_nothing_when_crop_is_not_active(self) -> None:
        self.assertFalse(self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True)))
        self.assertIsNone(self.tool.rect())

    def test_plain_click_does_not_select_rois_while_crop_is_active(self) -> None:
        roi_id = self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        self.assertIsNotNone(roi_id)
        _pump()
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_scene_clicked(_Click(self.panel._plot.vb.mapViewToScene(QPointF(40.0, 30.0)), Qt.MouseButton.LeftButton))
        self.assertEqual(self.selection.selected_roi_ids(), set())

    def test_right_click_menu_apply_commits_the_crop(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        # Both actions enabled (there is something pending) - the patched
        # QMenu.exec auto-picks the first enabled one: "Apply crop".
        self.panel._on_scene_clicked(_Click(self.panel._plot.vb.mapViewToScene(QPointF(0.0, 0.0)), Qt.MouseButton.RightButton))
        self.assertTrue(self.geometry.settings().crop.enabled)
        self.assertEqual((self.geometry.settings().crop.width, self.geometry.settings().crop.height), (40, 30))
        self.assertIsNone(self.active_tool.active())  # apply also exits Crop mode

    def test_right_click_menu_cancel_action_is_always_enabled(self) -> None:
        """Regression (2026-09-29): an earlier version disabled both "Apply
        crop" and "Cancel crop" with nothing pending - the whole menu had
        nothing clickable in it, which read as "the menu is broken" (not
        "there's nothing to do"). "Cancel" now always means "exit Crop
        mode", always a valid thing to do, so it stays enabled even when
        "Apply" is grayed out."""
        self.active_tool.set_active(ImageTool.CROP, True)
        seen_menus: list[QtWidgets.QMenu] = []

        def _capture(menu: QtWidgets.QMenu, *_a: object, **_k: object) -> None:
            seen_menus.append(menu)
            return None

        with mock.patch.object(QtWidgets.QMenu, "exec", _capture):
            self.panel._on_scene_clicked(_Click(self.panel._plot.vb.mapViewToScene(QPointF(0.0, 0.0)), Qt.MouseButton.RightButton))
        self.assertEqual(len(seen_menus), 1)
        apply_action, cancel_action = seen_menus[0].actions()
        self.assertEqual((apply_action.text(), cancel_action.text()), ("Apply crop", "Cancel crop"))
        self.assertFalse(apply_action.isEnabled())  # nothing new to commit
        self.assertTrue(cancel_action.isEnabled())
        self.assertIs(self.active_tool.active(), ImageTool.CROP)  # nothing chosen above, unaffected

    def test_right_click_menu_cancel_exits_crop_mode_with_nothing_pending(self) -> None:
        """setUp's default patch auto-picks the first *enabled* action -
        with nothing pending that's "Cancel crop" (index 1, since "Apply
        crop" at index 0 is disabled) - standing in for the user picking
        it themselves."""
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_scene_clicked(_Click(self.panel._plot.vb.mapViewToScene(QPointF(0.0, 0.0)), Qt.MouseButton.RightButton))
        self.assertIsNone(self.active_tool.active())
        self.assertFalse(self.geometry.settings().crop.enabled)  # nothing was ever applied

    def test_right_click_menu_cancel_discards_a_pending_edit_and_exits(self) -> None:
        """maintainer's spec (2026-09-29): "Cancel" exits the tool - same
        as clicking the Workflow panel's Crop button again - dropping any
        not-yet-applied resize/move along the way, without touching
        whatever was last actually applied."""
        self.geometry.set_crop(10, 10, 20, 20)
        self.active_tool.set_active(ImageTool.CROP, True)
        self.tool.set_size(50, 50)  # an unapplied edit
        with mock.patch.object(QtWidgets.QMenu, "exec", lambda menu, *_a, **_k: menu.actions()[1]):  # "Cancel crop"
            self.panel._on_scene_clicked(_Click(self.panel._plot.vb.mapViewToScene(QPointF(0.0, 0.0)), Qt.MouseButton.RightButton))
        self.assertIsNone(self.active_tool.active())
        self.assertEqual((self.geometry.settings().crop.width, self.geometry.settings().crop.height), (20, 20))

    def test_preview_is_uncropped_while_crop_is_active(self) -> None:
        self.geometry.set_crop(8, 6, 48, 40)
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (40, 48))
        self.active_tool.set_active(ImageTool.CROP, True)
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (64, 80))  # whole frame
        self.assertEqual(self.tool.frame_size(), (80, 64))

    def test_size_controls_widget_shows_and_tracks_the_rect(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.assertTrue(self.panel._crop_controls.isVisible())
        self.assertEqual(self.panel._crop_controls._width_spin.value(), 40)
        self.assertEqual(self.panel._crop_controls._height_spin.value(), 30)

    def test_size_controls_right_edge_aligns_with_the_rectangles_right_edge(self) -> None:
        """Maintainer's spec (2026-09-29): the apply button - the last,
        rightmost widget in the layout - sits flush with the crop
        rectangle's right edge, not the old bottom-left anchor."""
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        expected_scene = self.panel._plot.vb.mapViewToScene(QPointF(50.0, 40.0))
        expected_view = self.panel._view.mapFromScene(expected_scene)
        controls = self.panel._crop_controls
        self.assertEqual(controls.x() + controls.width(), expected_view.x())

    def test_size_fields_have_a_compact_fixed_width(self) -> None:
        """Maintainer's spec (2026-09-29): no more than 4-digit numbers'
        worth of width - QSpinBox's own default sizeHint is much wider,
        which was the big-gaps complaint."""
        controls = self.panel._crop_controls
        self.assertLess(controls._width_spin.width(), 80)
        self.assertEqual(controls._width_spin.width(), controls._height_spin.width())

    def test_apply_button_has_its_own_cursor(self) -> None:
        """Regression (2026-09-29): the size-controls widget is a child of
        the image view's viewport, whose cursor `_on_scene_moved` keeps
        changing to a resize/move shape as the mouse crosses the crop
        rectangle's edges. A widget with no cursor of its own inherits its
        parent's, so without an explicit override that stray resize cursor
        bled onto the whole floating widget - including the apply button,
        which gave no "this is clickable" hint at all when hovered."""
        controls = self.panel._crop_controls
        self.assertEqual(controls.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(controls._apply_button.cursor().shape(), Qt.CursorShape.PointingHandCursor)

    def test_size_controls_show_the_full_prefilled_size_on_first_activation(self) -> None:
        """Regression: the spin boxes start life range-limited to [1, 1]
        (crop_size_controls.py's construction-time default) - if
        `_on_crop_tool_changed` ever pushed the size before the max on a
        *pre-filled* activation (a real crop already applied, frame size
        already known from an earlier render - the common case, not the
        empty-rectangle one the previous test drags out by hand), Qt's own
        `QSpinBox.setValue()` would silently clip it to that stale max."""
        self.geometry.set_crop(8, 6, 48, 40)  # comfortably larger than [1, 1]
        _pump()  # a real render, so frame_size is already known before activation
        self.active_tool.set_active(ImageTool.CROP, True)
        self.assertEqual(self.panel._crop_controls._width_spin.value(), 48)
        self.assertEqual(self.panel._crop_controls._height_spin.value(), 40)

    def test_size_controls_hidden_with_no_rectangle(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.assertFalse(self.panel._crop_controls.isVisible())

    def test_editing_the_size_fields_resizes_the_rect(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.panel._crop_controls.size_edited.emit(20, 15)
        self.assertEqual(self.tool.rect(), (10, 10, 20, 15))

    def test_apply_button_click_applies_the_crop(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.panel._crop_controls._apply_button.click()
        self.assertTrue(self.geometry.settings().crop.enabled)

    def test_apply_exits_crop_mode_and_renders_the_cropped_image(self) -> None:
        """Maintainer's spec (2026-09-29): applying doesn't just commit the
        crop, it also ends the session and shows the result - while Crop
        stays active the panel always renders the *uncropped* frame
        (`_PREVIEW_TOOLS`), so without exiting, apply would commit
        correctly but look like nothing happened."""
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.panel._crop_controls._apply_button.click()
        self.assertIsNone(self.active_tool.active())
        self.assertFalse(self.tool.is_active())
        self.assertFalse(self.panel._crop_controls.isVisible())
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (30, 40))  # the cropped result, not the full 64x80 frame

    def test_a_failed_apply_leaves_the_session_running(self) -> None:
        """Image Tools switched off blocks the commit (same rule Rotate
        follows) - and must not exit the tool over nothing having changed."""
        self.geometry.set_image_tools_enabled(False)
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.panel._crop_controls._apply_button.click()
        self.assertFalse(self.geometry.settings().crop.enabled)
        self.assertIs(self.active_tool.active(), ImageTool.CROP)
        self.assertTrue(self.tool.is_active())

    def test_deactivating_crop_hides_the_size_controls(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.active_tool.set_active(ImageTool.CROP, False)
        self.assertFalse(self.panel._crop_controls.isVisible())

    def test_crop_and_rotate_remain_mutually_exclusive(self) -> None:
        self.active_tool.set_active(ImageTool.CROP, True)
        self.panel._on_crop_drag_event(self._drag_event(10.0, 10.0, start=True))
        self.panel._on_crop_drag_event(self._drag_event(50.0, 40.0, finish=True))
        self.active_tool.set_active(ImageTool.ROTATE, True)
        self.assertFalse(self.tool.is_active())
        self.assertIsNone(self.tool.rect())

    def test_real_escape_key_event_discards_a_pending_edit(self) -> None:
        """Through the view's event filter, not a direct call - same
        pattern as test_lspri_rewrite_rotate_tool.py's equivalent test."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent

        self.geometry.set_crop(10, 10, 20, 20)
        self.active_tool.set_active(ImageTool.CROP, True)
        self.tool.set_size(50, 50)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(self.panel._view, event)
        self.assertEqual(self.tool.rect(), (10, 10, 20, 20))
        self.assertTrue(self.tool.is_active())


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


class _Drag:
    """The slice of pyqtgraph's MouseDragEvent `_on_crop_drag_event` reads."""

    def __init__(self, scene_pos: QPointF, *, start: bool, finish: bool) -> None:
        self._pos, self._start, self._finish = scene_pos, start, finish

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def isStart(self) -> bool:  # noqa: N802 - Qt/pyqtgraph naming
        return self._start

    def isFinish(self) -> bool:  # noqa: N802 - Qt/pyqtgraph naming
        return self._finish


if __name__ == "__main__":
    unittest.main()
