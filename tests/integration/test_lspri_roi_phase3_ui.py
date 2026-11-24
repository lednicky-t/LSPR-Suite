"""The ROI menu, buttons and shortcuts of phase 3 (apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md), on the real Image panel.

Right-click menu: Add ROI > Circle / Rectangle / Current selection > All together / Separate blobs; Make this ROI > Sample / Reference; Link references /
Unlink. The ROIs tab: Link, Unlink, the Links switch. Ctrl+L / Ctrl+K. What the overlay then shows: link lines, the
"R7" label, the reference colour. The menu itself is injected by patching `show_roi_context_menu` (as the gesture tests do); a real menu is checked
separately in `ContextMenuTest` by driving the QMenu without showing it.

No driving of the real app by eye or by mouse: every action is a call a click would make.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QKeySequence

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.domain.roi_shapes import MaskShape, Rectangle
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image import roi_context_menu as menu_module
    from lspr_imaging_app.panels.image.rectangle_size_dialog import RectangleSizeDialog
    from lspr_imaging_app.panels.image.roi_context_menu import (
        ADD_RECTANGLE,
        ADD_RIM,
        ADD_ROI,
        ADD_SELECTION_BLOBS,
        ADD_SELECTION_ONE,
        DESELECT_AREA,
        LINK,
        MAKE_REFERENCE,
        MAKE_SAMPLE,
        RIM_TO_REFERENCE,
        TURN_RIM_OFF,
        UNLINK,
        RoiMenuChoice,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.roi.overlay_geometry import CELL_OFFSET
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.panels import roi_selection_lock
    from lspr_imaging_app.shortcuts import display
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

_MENU = "lspr_imaging_app.panels.image.interaction.show_roi_context_menu"


class _Click:
    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton) -> None:
        self._pos, self._button = scene_pos, button

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


class Phase3UiTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))
        self.dataset = DatasetModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.active_tool = ActiveToolModule()
        self.chromatic = ChromaticModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), self.chromatic, BackgroundModule(), self.roi_toolbox,
            self.selection, self.active_tool, ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        self.panel._tool_ribbon.set_category("ROIs")
        _pump(0.1)
        for x, y in ((20.0, 20.0), (40.0, 30.0), (60.0, 45.0)):
            self.roi_toolbox.add_roi(x, y, sample_diameter_px=10.0)
        _pump(0.2)
        undo_manager.clear()
        self.messages: list[str] = []
        self.panel._roi_creation.status.connect(self.messages.append)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def scene(self, x: float, y: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(x, y))

    def right_click(self, x: float, y: float, choice: RoiMenuChoice | None):
        with mock.patch(_MENU, return_value=choice) as menu:
            self.panel._on_scene_clicked(_Click(self.scene(x, y), Qt.MouseButton.RightButton))
        return menu

    def roi(self, roi_id: int):
        return self.roi_toolbox.roi_by_id(roi_id)

    def snap(self, roi_id: int) -> SimpleNamespace:
        """The ROI as it is now: the toolbox changes its ROI objects in place, so a test that compares before and after keeps a copy of the numbers."""
        roi = self.roi(roi_id)
        return SimpleNamespace(shape=roi.shape, center_x=roi.center_x, center_y=roi.center_y)

    def drawn_centre(self, roi_id: int) -> np.ndarray:
        """Where the canvas draws the ROI's centre at the frame on screen (the reference frame here: no chromatic correction)."""
        queries = self.panel._roi_queries
        frame = (queries._current_cube(), queries._current_wavelength())
        index = [r.area_roi_id for r in self.roi_toolbox.rois()].index(roi_id)
        return queries.view_centers(frame, queries.current_affine())[index]

    def select_pixels(self, *boxes) -> np.ndarray:
        shape = self.panel._last_image_shape[:2]
        plane = np.zeros(shape, dtype=bool)
        for y0, y1, x0, x1 in boxes:
            plane[y0:y1, x0:x1] = True
        self.panel._area_selection.combine_region(plane)
        return plane

    # -- Add ROI > Circle / Rectangle ------------------------------------------------------

    def test_add_roi_circle_places_a_circle_where_clicked_and_selects_it(self) -> None:
        self.right_click(10.0, 55.0, RoiMenuChoice(ADD_ROI))
        self.assertEqual(len(self.roi_toolbox.rois()), 4)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({4}))
        # stored in pixel-index coordinates, half a cell back from the clicked view point (`overlay_geometry.CELL_OFFSET`) ...
        self.assertEqual((self.roi(4).center_x, self.roi(4).center_y), (10.0 - CELL_OFFSET, 55.0 - CELL_OFFSET))
        # ... which is what draws it under the pointer
        self.assertEqual(tuple(self.drawn_centre(4)), (10.0, 55.0))

    def test_add_roi_rectangle_places_a_20_px_square_where_clicked_in_one_undo_step(self) -> None:
        self.right_click(10.0, 55.0, RoiMenuChoice(ADD_RECTANGLE))
        new = self.roi(4)
        self.assertEqual(new.shape, Rectangle(20.0, 20.0, 0.0))
        self.assertEqual((new.center_x, new.center_y), (10.0 - CELL_OFFSET, 55.0 - CELL_OFFSET))
        self.assertEqual(tuple(self.drawn_centre(4)), (10.0, 55.0))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({4}))
        undo_manager.undo()
        self.assertEqual(len(self.roi_toolbox.rois()), 3)

    def test_a_rectangle_added_under_a_chromatic_correction_is_stored_in_the_reference_frame(self) -> None:
        frame = (1, self.panel._roi_queries._current_wavelength())
        affine = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, -2.0]])
        with mock.patch.object(self.chromatic, "affine_for", return_value=affine):
            self.panel._roi_creation.add_rectangle(30.0, 30.0)
        # the click is view point (30, 30): half a cell off, then the frame's shift undone
        self.assertEqual((self.roi(4).center_x, self.roi(4).center_y), (27.0 - CELL_OFFSET, 32.0 - CELL_OFFSET))
        np.testing.assert_allclose(self.panel._roi_queries.view_centers(frame, affine)[3], (30.0, 30.0))  # and that frame draws it back under the click

    # -- Add ROI > Current selection --------------------------------------------------------

    def test_separate_blobs_make_one_reference_roi_each_linked_to_nothing(self) -> None:
        self.select_pixels((5, 9, 50, 55), (30, 36, 10, 14))
        self.right_click(70.0, 10.0, RoiMenuChoice(ADD_SELECTION_BLOBS))
        new = [r for r in self.roi_toolbox.rois() if r.area_roi_id > 3]
        self.assertEqual(len(new), 2)
        self.assertTrue(all(r.role == "reference" and r.reference_roi_ids == () and isinstance(r.shape, MaskShape) for r in new))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({4, 5}))
        self.assertIn("2 reference ROIs", self.messages[-1])
        undo_manager.undo()
        self.assertEqual(len(self.roi_toolbox.rois()), 3)  # one step for both

    def test_all_together_makes_one_roi_holding_every_selected_pixel(self) -> None:
        plane = self.select_pixels((5, 9, 50, 55), (30, 36, 10, 14))
        self.right_click(70.0, 10.0, RoiMenuChoice(ADD_SELECTION_ONE))
        self.assertEqual(len(self.roi_toolbox.rois()), 4)
        mask = self.roi(4).shape.mask
        self.assertEqual(int(mask.mask.sum()), int(plane.sum()))
        self.assertEqual("1 reference ROI", self.messages[-1].split(" from ")[0].removeprefix("Added "))

    def test_without_a_selection_it_says_so_and_adds_nothing(self) -> None:
        self.panel._roi_creation.add_from_selection(together=False)
        self.assertEqual(len(self.roi_toolbox.rois()), 3)
        self.assertIn("no area selection", self.messages[-1])

    def test_a_selection_made_under_a_chromatic_correction_is_carried_back_to_the_reference_frame(self) -> None:
        affine = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])
        with mock.patch.object(self.chromatic, "affine_for", return_value=affine):
            plane = self.select_pixels((20, 34, 20, 38))
            self.panel._roi_creation.add_from_selection(together=True)
            roi = self.roi(4)
            back = rz.rasterize_sample(roi, plane.shape, affine)  # what the new ROI measures at the frame it was selected on
        overlap = float((back & plane).sum()) / float((back | plane).sum())
        self.assertGreater(overlap, 0.9, "the ROI must cover the pixels that were selected, within the nearest-neighbour rounding of the warp")

    # -- Make this ROI / Link / Unlink -------------------------------------------------------

    def test_make_reference_and_make_sample_change_the_role_of_the_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.right_click(20.0, 20.0, RoiMenuChoice(MAKE_REFERENCE))
        self.assertEqual([self.roi(i).role for i in (1, 2, 3)], ["reference", "reference", "sample"])
        self.assertIn("Made 2 ROIs references", self.messages[-1])
        self.selection.set_roi_selection({1})
        self.right_click(20.0, 20.0, RoiMenuChoice(MAKE_SAMPLE))
        self.assertEqual(self.roi(1).role, "sample")

    def test_link_and_unlink_from_the_menu_the_buttons_and_the_keys(self) -> None:
        self.roi_toolbox.set_roi_role([3], "reference")
        undo_manager.clear()
        self.selection.set_roi_selection({1, 3})
        self.right_click(20.0, 20.0, RoiMenuChoice(LINK))
        self.assertEqual(self.roi_toolbox.references_of(1), (3,))
        self.assertIn("Linked 1 sample ROI to 1 reference ROI", self.messages[-1])
        self.right_click(20.0, 20.0, RoiMenuChoice(UNLINK))
        self.assertEqual(self.roi_toolbox.references_of(1), ())
        self.panel._roi_tab.link_button.click()
        self.assertEqual(self.roi_toolbox.references_of(1), (3,))
        self.panel._roi_tab.unlink_button.click()
        self.assertEqual(self.roi_toolbox.references_of(1), ())
        creation = self.panel._roi_creation
        self.assertEqual(creation.link_action.shortcut(), QKeySequence("Ctrl+L"))
        self.assertEqual(creation.unlink_action.shortcut(), QKeySequence("Ctrl+K"))
        self.assertEqual(creation.link_action.shortcutContext(), Qt.ShortcutContext.ApplicationShortcut)
        creation.link_action.trigger()  # what Ctrl+L does
        self.assertEqual(self.roi_toolbox.references_of(1), (3,))
        creation.unlink_action.trigger()  # Ctrl+K
        self.assertEqual(self.roi_toolbox.references_of(1), ())

    def test_a_link_that_cannot_be_made_says_why_in_the_status_bar(self) -> None:
        self.selection.set_roi_selection({1, 2})  # two samples, no reference
        self.panel._roi_tab.link_button.click()
        self.assertIn("at least one sample ROI and one reference ROI", self.messages[-1])
        self.assertFalse(undo_manager.can_undo)

    def test_the_menu_is_told_what_can_be_linked_and_what_the_selection_holds(self) -> None:
        self.roi_toolbox.set_roi_role([3], "reference")
        self.selection.set_roi_selection({1, 3})
        menu = self.right_click(20.0, 20.0, None)
        kwargs = menu.call_args.kwargs
        self.assertEqual(kwargs["selected_roles"], (1, 1))
        self.assertTrue(kwargs["can_link"])
        self.assertFalse(kwargs["can_unlink"])
        self.assertFalse(kwargs["can_add_from_selection"])
        self.select_pixels((5, 9, 50, 55))
        self.assertTrue(self.right_click(20.0, 20.0, None).call_args.kwargs["can_add_from_selection"])

    # -- rim pairs and linked pairs (plan 2026-11-19) ------------------------------------------------

    def test_the_menu_is_told_what_can_get_a_rim_and_whose_rim_can_become_a_reference(self) -> None:
        self.selection.set_roi_selection({1})  # a sample with the default rim, no links
        kwargs = self.right_click(20.0, 20.0, None).call_args.kwargs
        self.assertTrue(kwargs["can_rim_to_reference"])
        self.assertFalse(kwargs["can_add_rim"], "it has a rim already")
        self.roi_toolbox.set_roi_role([2], "reference")  # a reference nobody uses
        self.selection.set_roi_selection({2})
        kwargs = self.right_click(40.0, 30.0, None).call_args.kwargs
        self.assertTrue(kwargs["can_add_rim"])
        self.assertFalse(kwargs["can_rim_to_reference"], "a reference has no rim to turn")

    def test_turn_rim_into_linked_reference_makes_a_map_reference_linked_to_the_sample_in_one_undo_step(self) -> None:
        self.selection.set_roi_selection({1})
        undo_manager.clear()
        self.right_click(20.0, 20.0, RoiMenuChoice(RIM_TO_REFERENCE))
        self.assertEqual(len(self.roi_toolbox.rois()), 4)
        self.assertEqual(self.roi_toolbox.references_of(1), (4,))
        self.assertIsInstance(self.roi(4).shape, MaskShape)
        self.assertEqual(self.roi(4).role, "reference")
        self.assertIn("Turned 1 rim into linked reference ROI 4", self.messages[-1])
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo, "one undo step")
        self.assertEqual((len(self.roi_toolbox.rois()), self.roi_toolbox.references_of(1)), (3, ()))

    def test_turn_rim_into_linked_reference_takes_the_rim_where_the_sample_is_on_the_cube_on_screen(self) -> None:
        """2026-11-19: a sample dragged on a cube after the first is a timeline change, and the reference used to be made where the sample was before. Cube 1 here is that cube."""
        self.roi_toolbox.move_roi(1, 30.0, 50.0, cube=1)  # persistent from cube 1 on; the base position stays (20, 20)
        self.assertEqual((self.roi(1).center_x, self.roi(1).center_y), (20.0, 20.0))
        self.selection.set_cube(1)
        _pump(0.1)
        self.selection.set_roi_selection({1})
        undo_manager.clear()
        self.right_click(30.0, 50.0, RoiMenuChoice(RIM_TO_REFERENCE))
        self.assertEqual(self.roi_toolbox.references_of(1), (4,))
        mask = self.roi(4).shape.mask
        rows, columns = np.nonzero(mask.mask)
        self.assertAlmostEqual(float(columns.mean()) + mask.x0, 30.0, delta=1.0)
        self.assertAlmostEqual(float(rows.mean()) + mask.y0, 50.0, delta=1.0)
        self.assertIn("no rim of its own", self.messages[-1])
        for cube in (None, 0, 1):  # the sample is rim-less on every cube, and one undo step takes everything back
            self.assertFalse(self.roi_toolbox.rois_at(cube)[0].border.enabled, f"cube {cube}")
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo)
        self.assertEqual((len(self.roi_toolbox.rois()), self.roi_toolbox.references_of(1)), (3, ()))
        self.assertIsNone(self.roi(1).border)

    def test_turn_rim_off_is_offered_to_a_sample_with_a_rim_only_and_turns_it_off_in_one_undo_step(self) -> None:
        self.selection.set_roi_selection({1})
        self.assertTrue(self.right_click(20.0, 20.0, None).call_args.kwargs["can_turn_rim_off"])
        undo_manager.clear()
        self.right_click(20.0, 20.0, RoiMenuChoice(TURN_RIM_OFF))
        self.assertFalse(self.roi(1).border.enabled)
        self.assertEqual(self.roi_toolbox.reference_source(1), "none")
        self.assertIn("Turned the rim off on 1 ROI", self.messages[-1])
        kwargs = self.right_click(20.0, 20.0, None).call_args.kwargs
        self.assertFalse(kwargs["can_turn_rim_off"], "it is off now")
        self.assertTrue(kwargs["can_add_rim"], "Add rim brings it back")
        self.assertFalse(kwargs["can_rim_to_reference"], "no rim, nothing to turn into a reference")
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo)
        self.assertIsNone(self.roi(1).border)
        self.roi_toolbox.set_roi_role([2], "reference")  # a reference has no rim to turn off
        self.selection.set_roi_selection({2})
        self.assertFalse(self.right_click(40.0, 30.0, None).call_args.kwargs["can_turn_rim_off"])

    def test_turn_rim_into_linked_reference_leaves_out_what_cannot_and_says_so(self) -> None:
        self.roi_toolbox.set_roi_role([3], "reference")
        self.selection.set_roi_selection({1, 3})
        self.right_click(20.0, 20.0, RoiMenuChoice(RIM_TO_REFERENCE))
        self.assertEqual(self.roi_toolbox.references_of(1), (4,))
        self.assertIn("1 selected ROI left out", self.messages[-1])

    def test_add_rim_turns_an_unused_reference_into_a_sample_with_a_rim(self) -> None:
        self.roi_toolbox.set_roi_role([2], "reference")
        self.selection.set_roi_selection({2})
        undo_manager.clear()
        self.right_click(40.0, 30.0, RoiMenuChoice(ADD_RIM))
        self.assertEqual(self.roi(2).role, "sample")
        self.assertIn("Added a rim to 1 ROI", self.messages[-1])
        undo_manager.undo()
        self.assertEqual(self.roi(2).role, "reference")

    # -- what the overlay shows ---------------------------------------------------------------

    def test_link_lines_a_reference_label_and_the_reference_colour(self) -> None:
        self.roi_toolbox.set_roi_role([3], "reference")
        self.roi_toolbox.link_references([1, 2], [3])
        self.panel._roi_overlay.labels_visible = True
        self.panel._draw_roi_overlay()
        overlay = self.panel._roi_overlay
        xs, _ys = overlay.link_curve.getData()
        self.assertEqual(int(np.isfinite(xs).sum()), 4)  # two lines of two ends
        self.assertEqual(sorted(entry[3] for entry in overlay.label_item._entries), ["1", "2", "R3"])
        reference_colour = overlay.reference.color.lower()
        colours = dict(zip((1, 2, 3), overlay.sample_circles._colors, strict=True))
        self.assertEqual(colours[3], reference_colour)
        self.assertNotEqual(colours[1], reference_colour)

    def test_the_links_switch_hides_and_shows_the_lines(self) -> None:
        self.roi_toolbox.set_roi_role([3], "reference")
        self.roi_toolbox.link_references([1], [3])
        self.panel._draw_roi_overlay()
        overlay = self.panel._roi_overlay
        self.assertTrue(self.panel._roi_tab.links_button.isChecked())
        self.assertGreater(int(np.isfinite(overlay.link_curve.getData()[0]).sum()), 0)
        self.panel._roi_tab.links_button.click()
        self.assertFalse(overlay.links_visible)
        hidden = overlay.link_curve.getData()[0]
        self.assertTrue(hidden is None or int(np.isfinite(hidden).sum()) == 0)
        self.panel._roi_tab.links_button.click()
        self.assertGreater(int(np.isfinite(overlay.link_curve.getData()[0]).sum()), 0)

    def test_unlinking_removes_the_line(self) -> None:
        self.roi_toolbox.set_roi_role([3], "reference")
        self.roi_toolbox.link_references([1], [3])
        self.roi_toolbox.unlink_references([1])
        self.panel._draw_roi_overlay()
        data = self.panel._roi_overlay.link_curve.getData()[0]
        self.assertTrue(data is None or int(np.isfinite(data).sum()) == 0)

    # (The Shape picker and the click-to-add Add ROI tool were deleted 2026-11-24; Add ROI > Circle / Rectangle is the right-click menu's.)

    def test_a_mask_roi_can_be_picked_and_grabbed_by_its_pixels(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        self.assertEqual(self.panel._roi_queries.roi_at(65.5, 15.5), 4)
        self.assertIsNone(self.panel._roi_queries.roi_at(75.5, 15.5))
        self.selection.set_roi_selection({4})
        apertures = self.panel._roi_queries.selected_apertures()
        self.assertEqual(list(apertures.resizable), [False])
        (grab,) = apertures.body  # a map is grabbed on one of its pixels (and moved by whole pixels)
        self.assertTrue(grab(65.5, 15.5))
        self.assertFalse(grab(75.5, 15.5))
        hit = self.panel._interaction._roi_gestures.hit(65.5, 15.5)
        self.assertEqual((hit.roi_id, hit.zone), (4, "body"))
        self.assertIsNone(self.panel._interaction._roi_gestures.hit(75.5, 15.5))

    def test_a_pixel_map_moves_by_whole_pixels_with_the_circles_and_one_undo_step_puts_it_back(self) -> None:
        plane = self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        undo_manager.clear()
        before = self.snap(4)
        shape = self.panel._last_image_shape[:2]
        affine = np.asarray(self.panel._roi_queries.current_affine(), dtype=np.float64)
        pixels_before = rz.rasterize_sample(self.roi(4), shape, affine)
        self.assertTrue(np.array_equal(pixels_before, plane))
        x0, y0, cx, cy = before.shape.mask.x0, before.shape.mask.y0, before.center_x, before.center_y
        self.roi_toolbox.translate_rois([1, 4], 3.4, -2.2)  # the circle moves as asked, the map by the nearest whole pixels (3, -2)
        self.assertEqual((self.roi(1).center_x, self.roi(1).center_y), (23.4, 17.8))
        moved = self.roi(4)
        self.assertEqual((moved.shape.mask.x0, moved.shape.mask.y0), (x0 + 3, y0 - 2))
        self.assertEqual((moved.center_x, moved.center_y), (cx + 3, cy - 2), "the label point goes with the pixels")
        self.assertTrue(np.array_equal(moved.shape.mask.mask, before.shape.mask.mask), "the same pixels, nothing resampled")
        pixels_after = rz.rasterize_sample(moved, shape, affine)
        self.assertTrue(np.array_equal(pixels_after, np.roll(pixels_before, (-2, 3), axis=(0, 1))), "what the scan measures moved with it")
        undo_manager.undo()
        self.assertEqual(self.roi(4).shape, before.shape)
        self.assertEqual((self.roi(4).center_x, self.roi(1).center_x), (cx, 20.0))
        self.assertFalse(undo_manager.can_undo)

    def test_a_typed_position_puts_a_pixel_map_on_the_nearest_whole_pixel_and_a_tiny_move_says_why(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        undo_manager.clear()
        start = self.snap(4)
        with self.assertRaisesRegex(ValueError, "pixel map.*whole pixels"):
            self.roi_toolbox.place_rois([4], x=start.center_x + 0.4)  # under half a pixel: nothing moves, and it says so
        with self.assertRaisesRegex(ValueError, "pixel map"):
            self.roi_toolbox.translate_rois([4], 0.0, -0.3)
        self.assertFalse(undo_manager.can_undo)
        self.roi_toolbox.place_rois([4], x=start.center_x + 5.6)
        self.assertEqual(self.roi(4).shape.mask.x0, start.shape.mask.x0 + 6)
        self.assertEqual(self.roi(4).center_x, start.center_x + 6)
        self.assertEqual(self.roi(4).shape.mask.y0, start.shape.mask.y0, "y was not asked for")
        # a wheel step of half a pixel goes the same distance either way (halves go away from zero, not to the even number)
        self.roi_toolbox.translate_rois([4], -0.5, 0.0)
        self.assertEqual(self.roi(4).shape.mask.x0, start.shape.mask.x0 + 5)
        self.roi_toolbox.translate_rois([4], 0.5, 0.0)
        self.assertEqual(self.roi(4).shape.mask.x0, start.shape.mask.x0 + 6)

    def test_a_mixed_move_that_is_under_half_a_pixel_moves_the_circle_and_leaves_the_map(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        start = self.snap(4)
        self.roi_toolbox.translate_rois([1, 4], 0.3, 0.0)
        self.assertEqual(self.roi(1).center_x, 20.3)
        self.assertEqual(self.roi(4).shape, start.shape)

    def _drag(self, ids, dx: float, dy: float, steps: int) -> None:
        """Press on the first selected map's pixel, move the pointer ``dx, dy`` view units in ``steps`` equal moves, release."""
        gestures = self.panel._interaction._roi_gestures
        self.selection.set_roi_selection(set(ids))
        start = (65.5, 15.5)
        self.assertTrue(gestures.begin(*start, additive=False))
        for k in range(1, steps + 1):
            gestures.update(start[0] + dx * k / steps, start[1] + dy * k / steps)
        gestures.end(start[0] + dx, start[1] + dy, additive=False)

    def test_dragging_a_selected_pixel_map_moves_it_by_the_distance_to_the_nearest_pixel(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        undo_manager.clear()
        start = self.snap(4)
        self._drag([4], 3.4, -5.6, steps=40)  # in 40 small steps, none of them as long as a pixel: the whole distance counts, nothing is lost
        moved = self.roi(4)
        self.assertEqual((moved.shape.mask.x0, moved.shape.mask.y0), (start.shape.mask.x0 + 3, start.shape.mask.y0 - 6))
        self.assertEqual((moved.center_x, moved.center_y), (start.center_x + 3, start.center_y - 6))
        undo_manager.undo()
        self.assertEqual(self.roi(4).shape, start.shape)
        self.assertFalse(undo_manager.can_undo, "the whole drag was one undo step")

    def test_dragging_a_map_with_a_circle_moves_the_circle_smoothly_and_the_map_by_pixels(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        undo_manager.clear()
        start = self.snap(4)
        self._drag([1, 4], 4.4, 0.0, steps=11)
        self.assertAlmostEqual(self.roi(1).center_x, 24.4, places=1)
        self.assertEqual(self.roi(4).shape.mask.x0, start.shape.mask.x0 + 4)
        undo_manager.undo()
        self.assertEqual((self.roi(1).center_x, self.roi(4).shape.mask.x0), (20.0, start.shape.mask.x0))
        self.assertFalse(undo_manager.can_undo, "one undo step for the circle and the map together")


    # -- Deselect area (2026-11-19): with no tool armed the ROI menu is the only one, and it must be able to drop the pixel selection ---------

    def test_the_menu_is_told_whether_there_is_an_area_selection(self) -> None:
        self.assertFalse(self.right_click(10.0, 55.0, None).call_args.kwargs["can_deselect_area"])
        self.select_pixels((10, 30, 10, 30))
        self.assertTrue(self.right_click(10.0, 55.0, None).call_args.kwargs["can_deselect_area"])

    def test_deselect_area_drops_the_area_selection_in_one_undo_step_and_leaves_the_roi_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.select_pixels((10, 30, 10, 30))
        undo_manager.clear()
        self.right_click(10.0, 55.0, RoiMenuChoice(DESELECT_AREA))
        self.assertFalse(self.panel._area_selection.has_selection())
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 2}), "only the pixel selection is dropped")
        undo_manager.undo()
        self.assertTrue(self.panel._area_selection.has_selection())

    # -- the size dialog (no menu entry opens it since 2026-11-19: the table's W / H / angle columns and the handles do the job) ------

    def test_the_dialog_reports_only_what_was_changed(self) -> None:
        dialog = RectangleSizeDialog(self.panel, Rectangle(20.0, 10.0, 5.0), 3)
        self.assertEqual(dialog.changed(), {})
        dialog.angle_box.setValue(45.0)
        dialog.width_box.setValue(24.5)
        self.assertEqual(dialog.changed(), {"width_px": 24.5, "angle_deg": 45.0})
        self.assertIn("3 rectangles", dialog.windowTitle())
        dialog.close()


class ContextMenuTest(unittest.TestCase):
    """The real QMenu, built and inspected without being shown."""

    def _build(self, **kwargs):
        built: list = []

        class Recording(QtWidgets.QMenu):
            def exec(self, *_args):  # noqa: A003 - Qt's name
                built.append(self)
                return None

        parent = QtWidgets.QWidget()
        with mock.patch.object(menu_module, "QMenu", Recording):
            menu_module.show_roi_context_menu(
                parent, selected_count=kwargs.pop("selected_count", 0), groups=kwargs.pop("groups", []),
                any_selected_grouped=kwargs.pop("any_selected_grouped", False), can_add=kwargs.pop("can_add", True), **kwargs,
            )
        return built[0], parent

    @staticmethod
    def _texts(menu) -> list[str]:
        """The entries of the menu itself (no separators), in order."""
        return [a.text() for a in menu.actions() if not a.isSeparator()]

    @staticmethod
    def _entry(menu, *path):
        for title in path[:-1]:
            menu = next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == title)
        return next(a for a in menu.actions() if a.text() == path[-1])

    def test_add_roi_is_a_submenu_with_circle_rectangle_and_current_selection(self) -> None:
        menu, _parent = self._build(can_add_from_selection=True)
        add = self._entry(menu, "Add ROI")
        self.assertIsNotNone(add.menu())
        self.assertEqual([a.text() for a in add.menu().actions()], ["Circle", "Rectangle", "Current selection"])
        self.assertEqual([a.text() for a in self._entry(menu, "Add ROI", "Current selection").menu().actions()], ["All together as one ROI", "Separate blobs"])

    def test_add_roi_offers_only_what_can_be_done_and_is_left_out_when_nothing_can(self) -> None:
        """2026-11-19 (maintainer): an entry whose prerequisite is not met is not in the menu."""
        menu, _parent = self._build(can_add_from_selection=False)
        self.assertEqual([a.text() for a in self._entry(menu, "Add ROI").menu().actions()], ["Circle", "Rectangle"])  # no area selection: no Current selection
        menu, _parent = self._build(can_add=False, can_add_from_selection=True)
        self.assertEqual([a.text() for a in self._entry(menu, "Add ROI").menu().actions()], ["Current selection"])  # no room for a circle here
        menu, _parent = self._build(can_add=False, can_add_from_selection=False)
        self.assertNotIn("Add ROI", self._texts(menu))

    def test_make_this_roi_checks_the_role_the_whole_selection_has(self) -> None:
        menu, _parent = self._build(selected_count=2, selected_roles=(0, 2))
        self.assertEqual(self._entry(menu, "Make these ROIs", "Reference").isChecked(), True)
        self.assertEqual(self._entry(menu, "Make these ROIs", "Sample").isChecked(), False)
        menu, _parent = self._build(selected_count=2, selected_roles=(1, 1))  # mixed: neither
        self.assertFalse(self._entry(menu, "Make these ROIs", "Reference").isChecked() or self._entry(menu, "Make these ROIs", "Sample").isChecked())
        menu, _parent = self._build(selected_count=0)
        self.assertFalse([t for t in self._texts(menu) if t.startswith("Make ")], "no ROI selected: nothing to make a sample or a reference")

    def test_the_selection_commands_come_first_together(self) -> None:
        """Add ROI, Delete, Select whole group, Deselect, Lock ROI selection: the first block (maintainer, 2026-11-19)."""
        menu, _parent = self._build(
            selected_count=2, selected_roles=(1, 1), can_add_from_selection=True, can_link=True, can_unlink=True, any_selected_grouped=True,
            clicked_group=("G", 3), groups=[("g1", "G")],
        )
        texts = self._texts(menu)
        first_separator = next(i for i, a in enumerate(menu.actions()) if a.isSeparator())
        self.assertEqual(
            [a.text() for a in menu.actions()[:first_separator]],
            ["Add ROI", "Delete 2 ROIs", 'Select whole group "G" (3 ROIs)', "Deselect 2 ROIs", roi_selection_lock.MENU_TEXT],
        )
        self.assertEqual(texts[5:], ["Make these ROIs", "Link references", "Unlink", "Group 2 selected ROIs...", "Add to group", "Ungroup"])

    def test_deselect_area_is_offered_only_with_an_area_selection_and_sits_by_the_roi_deselect(self) -> None:
        menu, _parent = self._build(selected_count=2)
        self.assertNotIn("Deselect area", self._texts(menu))
        menu, _parent = self._build(selected_count=2, can_deselect_area=True)
        texts = self._texts(menu)
        self.assertEqual(texts[texts.index("Deselect 2 ROIs") + 1], "Deselect area")
        menu, _parent = self._build(can_deselect_area=True)  # no ROI selected, no ROI to deselect: this one is still there
        self.assertEqual(self._texts(menu), ["Add ROI", "Deselect area", roi_selection_lock.MENU_TEXT])
        self.assertFalse(self._entry(menu, "Deselect area").icon().isNull())

    def test_without_a_selection_only_add_roi_and_the_lock_remain(self) -> None:
        menu, _parent = self._build()
        self.assertEqual(self._texts(menu), ["Add ROI", roi_selection_lock.MENU_TEXT])

    def test_deselect_and_select_whole_group_are_left_out_while_the_selection_is_locked(self) -> None:
        menu, _parent = self._build(selected_count=2, clicked_group=("G", 3), locked=True)
        texts = self._texts(menu)
        self.assertNotIn("Deselect 2 ROIs", texts)
        self.assertFalse([t for t in texts if t.startswith("Select whole group")])
        self.assertTrue(self._entry(menu, roi_selection_lock.MENU_TEXT).isChecked())
        self.assertIn("Delete 2 ROIs", texts)  # deleting does not change the selection

    def test_select_whole_group_is_left_out_once_every_member_is_selected(self) -> None:
        menu, _parent = self._build(selected_count=3, clicked_group=("G", 3), clicked_group_fully_selected=True)
        self.assertFalse([t for t in self._texts(menu) if t.startswith("Select whole group")])
        menu, _parent = self._build(selected_count=1, clicked_group=("G", 3))
        self.assertIn('Select whole group "G" (3 ROIs)', self._texts(menu))

    def test_ungroup_and_add_to_group_need_a_grouped_roi_and_a_group(self) -> None:
        menu, _parent = self._build(selected_count=1)
        self.assertNotIn("Ungroup", self._texts(menu))
        self.assertNotIn("Add to group", self._texts(menu))
        menu, _parent = self._build(selected_count=1, any_selected_grouped=True, groups=[("g1", "G")])
        self.assertIn("Ungroup", self._texts(menu))
        self.assertEqual([a.text() for a in self._entry(menu, "Add to group").menu().actions()], ["G"])

    def test_remove_this_cubes_edit_needs_an_edit_on_this_cube(self) -> None:
        menu, _parent = self._build(selected_count=1)
        self.assertNotIn("Remove this cube's edit", self._texts(menu))
        menu, _parent = self._build(selected_count=1, can_remove_cube_edit=True)
        self.assertIn("Remove this cube's edit", self._texts(menu))

    def test_the_menu_has_no_rectangle_size_entry(self) -> None:
        """Removed 2026-11-19 (maintainer): the ROI table's W / H / angle columns and the rectangle's handles replace it."""
        menu, _parent = self._build(selected_count=1)
        self.assertFalse([a.text() for a in menu.actions() if a.text().startswith("Rectangle size")])

    def test_every_entry_wears_an_icon_and_the_same_command_the_same_icon(self) -> None:
        menu, _parent = self._build(
            selected_count=2, selected_roles=(1, 1), can_add_from_selection=True, can_link=True, can_unlink=True,
            clicked_group=("G", 2),
        )
        entries = []

        def walk(node):
            for action in node.actions():
                if action.isSeparator():
                    continue
                entries.append(action)
                if action.menu() is not None:
                    walk(action.menu())

        walk(menu)
        self.assertGreater(len(entries), 15)
        self.assertEqual([a.text() for a in entries if a.icon().isNull()], [])
        from lspr_imaging_app.panels.menu_icons import menu_icon

        delete = self._entry(menu, "Delete 2 ROIs")
        self.assertEqual(delete.icon().pixmap(36, 36).toImage(), menu_icon("delete").pixmap(36, 36).toImage())  # the table's Delete is this bin too

    def test_the_rim_entries_are_in_the_menu_only_when_they_can_be_used_and_wear_icons(self) -> None:
        menu, _parent = self._build(selected_count=1, selected_roles=(1, 0))
        self.assertNotIn("Add rim", self._texts(menu))
        self.assertNotIn("Turn rim off", self._texts(menu))
        self.assertNotIn("Turn rim into linked reference", self._texts(menu))
        menu, _parent = self._build(selected_count=1, selected_roles=(1, 0), can_add_rim=True, can_turn_rim_off=True, can_rim_to_reference=True)
        texts = self._texts(menu)
        self.assertIn("Add rim", texts)
        self.assertIn("Turn rim off", texts)
        self.assertIn("Turn rim into linked reference", texts)
        for text in ("Add rim", "Turn rim off", "Turn rim into linked reference"):
            entry = self._entry(menu, text)
            self.assertFalse(entry.icon().isNull(), text)
            self.assertTrue(entry.toolTip(), text)

    def test_group_and_ungroup_show_their_keys(self) -> None:
        menu, _parent = self._build(selected_count=2, any_selected_grouped=True)
        group, ungroup = self._entry(menu, "Group 2 selected ROIs..."), self._entry(menu, "Ungroup")
        self.assertEqual(group.shortcut(), QKeySequence(display("groups.group")))
        self.assertEqual(ungroup.shortcut(), QKeySequence(display("groups.ungroup")))
        self.assertTrue(group.isShortcutVisibleInContextMenu() and ungroup.isShortcutVisibleInContextMenu())

    def test_link_and_unlink_are_in_the_menu_only_when_the_selection_holds_something_to_link_or_unlink(self) -> None:
        menu, _parent = self._build(selected_count=2, selected_roles=(1, 1), can_link=True, can_unlink=False)
        texts = self._texts(menu)
        self.assertIn("Link references", texts)
        self.assertNotIn("Unlink", texts, "nothing selected is linked: Unlink is not offered (2026-11-19)")
        menu, _parent = self._build(selected_count=2, selected_roles=(1, 1), can_link=False, can_unlink=True)  # already linked to each other
        texts = self._texts(menu)
        self.assertIn("Unlink", texts)
        self.assertNotIn("Link references", texts)
        link, unlink = None, self._entry(menu, "Unlink")
        self.assertEqual(unlink.shortcut(), QKeySequence(display("links.unlink")))
        menu, _parent = self._build(selected_count=2, selected_roles=(1, 1), can_link=True, can_unlink=True)
        link = self._entry(menu, "Link references")
        self.assertEqual(link.shortcut(), QKeySequence(display("links.link")))
        self.assertTrue(link.isShortcutVisibleInContextMenu())


if __name__ == "__main__":
    unittest.main()
