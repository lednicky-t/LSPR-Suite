"""The ROI menu, buttons and shortcuts of phase 3 (apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md), on the real Image panel.

Right-click menu: Add ROI > Circle / Rectangle / Current selection > All together / Separate blobs; Make this ROI > Sample / Reference; Link references /
Unlink. The ROIs tab: Link, Unlink, the Links switch, the Shape picker (Circle, Rectangle). Ctrl+L / Ctrl+K. What the overlay then shows: link lines, the
"R7" label, the reference colour. The menu itself is injected by patching `show_roi_context_menu` (as the gesture tests do); a real menu is checked
separately in `ContextMenuTest` by driving the QMenu without showing it.

No driving of the real app by eye or by mouse: every action is a call a click would make.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
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
        ImageTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image import roi_context_menu as menu_module
    from lspr_imaging_app.panels.image.rectangle_size_dialog import RectangleSizeDialog
    from lspr_imaging_app.panels.image.roi_context_menu import (
        ADD_RECTANGLE,
        ADD_ROI,
        ADD_SELECTION_BLOBS,
        ADD_SELECTION_ONE,
        LINK,
        RESIZE_RECTANGLE,
        MAKE_REFERENCE,
        MAKE_SAMPLE,
        UNLINK,
        RoiMenuChoice,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
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
        self.assertEqual((self.roi(4).center_x, self.roi(4).center_y), (10.0, 55.0))

    def test_add_roi_rectangle_places_a_20_px_square_where_clicked_in_one_undo_step(self) -> None:
        self.right_click(10.0, 55.0, RoiMenuChoice(ADD_RECTANGLE))
        new = self.roi(4)
        self.assertEqual(new.shape, Rectangle(20.0, 20.0, 0.0))
        self.assertEqual((new.center_x, new.center_y), (10.0, 55.0))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({4}))
        undo_manager.undo()
        self.assertEqual(len(self.roi_toolbox.rois()), 3)

    def test_a_rectangle_added_under_a_chromatic_correction_is_stored_in_the_reference_frame(self) -> None:
        frame = (1, self.panel._roi_queries._current_wavelength())
        affine = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, -2.0]])
        with mock.patch.object(self.chromatic, "affine_for", return_value=affine):
            self.panel._roi_creation.add_rectangle(30.0, 30.0)
        self.assertEqual((self.roi(4).center_x, self.roi(4).center_y), (27.0, 32.0))
        del frame

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

    # -- the Shape picker and the Add ROI tool -----------------------------------------------

    def test_the_picker_offers_circle_and_rectangle_only(self) -> None:
        picker = self.panel._roi_tab.shape_picker
        self.assertEqual(sorted(picker._actions), ["circle", "rectangle"])
        self.assertTrue(all(action.isEnabled() for action in picker._actions.values()))

    def test_the_add_roi_tool_places_the_shape_the_picker_holds(self) -> None:
        self.active_tool.set_active(ImageTool.ADD_ROI, True)
        self.panel._on_scene_clicked(_Click(self.scene(10.0, 55.0), Qt.MouseButton.LeftButton))
        self.assertEqual(type(self.roi(4).shape).__name__, "Circle")
        self.panel._roi_tab.shape_picker.set_shape("rectangle")
        self.panel._on_scene_clicked(_Click(self.scene(30.0, 55.0), Qt.MouseButton.LeftButton))
        self.assertEqual(self.roi(5).shape, Rectangle(20.0, 20.0, 0.0))
        self.panel._roi_tab.shape_picker.set_shape("circle")
        self.panel._on_scene_clicked(_Click(self.scene(50.0, 55.0), Qt.MouseButton.LeftButton))
        self.assertEqual(type(self.roi(6).shape).__name__, "Circle")

    def test_a_mask_roi_can_be_picked_by_its_pixels_but_has_no_inside_to_drag_by(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        self.assertEqual(self.panel._roi_queries.roi_at(65.5, 15.5), 4)
        self.assertIsNone(self.panel._roi_queries.roi_at(75.5, 15.5))
        self.selection.set_roi_selection({4})
        apertures = self.panel._roi_queries.selected_apertures()
        self.assertEqual(list(apertures.resizable), [False])
        self.assertEqual(apertures.body, (None,))  # a mask's pixels cannot be moved, so there is nothing to grab it by

    def test_a_pixel_mask_is_not_moved_by_a_position_edit_and_nothing_moves_at_all(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        undo_manager.clear()
        before = {r.area_roi_id: (r.center_x, r.center_y) for r in self.roi_toolbox.rois()}
        with self.assertRaisesRegex(ValueError, "pixel mask"):
            self.roi_toolbox.translate_rois([1, 4], 3.0, 2.0)  # a circle with the mask: refused as a whole, not half-moved
        with self.assertRaisesRegex(ValueError, "pixel mask"):
            self.roi_toolbox.place_rois([4], x=10.0)
        self.assertEqual({r.area_roi_id: (r.center_x, r.center_y) for r in self.roi_toolbox.rois()}, before)
        self.assertFalse(undo_manager.can_undo)
        self.roi_toolbox.translate_rois([1], 3.0, 2.0)  # the circle alone still moves

    def test_a_drag_that_starts_with_a_mask_selected_is_refused_once_with_the_reason(self) -> None:
        self.select_pixels((10, 20, 60, 72))
        self.panel._roi_creation.add_from_selection(together=True)
        self.selection.set_roi_selection({1, 4})
        refused: list[str] = []
        gestures = self.panel._interaction._roi_gestures
        gestures.refused.connect(refused.append)
        from lspr_imaging_app.panels.image.roi_gestures import RoiHit

        self.assertTrue(gestures._begin_edit(RoiHit(1, "body", (20.0, 20.0)), 20.0, 20.0))
        self.assertEqual(len(refused), 1)
        self.assertIn("pixel mask", refused[0])
        self.assertEqual(self.roi(1).center_x, 20.0)


    # -- Rectangle size... ----------------------------------------------------------------------

    def _two_rectangles(self) -> None:
        self.roi_toolbox.add_shaped_roi(10.0, 55.0, Rectangle(20.0, 10.0, 0.0))  # id 4
        self.roi_toolbox.add_shaped_roi(40.0, 55.0, Rectangle(30.0, 12.0, 10.0))  # id 5
        undo_manager.clear()

    def test_rectangle_size_applies_only_the_fields_that_were_changed_to_every_selected_rectangle(self) -> None:
        self._two_rectangles()
        self.selection.set_roi_selection({1, 4, 5})  # a circle among them is left alone
        with mock.patch("lspr_imaging_app.panels.image.roi_creation.ask_rectangle_size", return_value={"angle_deg": 30.0}) as ask:
            self.right_click(10.0, 55.0, RoiMenuChoice(RESIZE_RECTANGLE))
        self.assertEqual([s.width_px for s in ask.call_args.args[1]], [20.0, 30.0])  # the dialog is offered the rectangles only
        self.assertEqual(self.roi(4).shape, Rectangle(20.0, 10.0, 30.0))  # its own width and height kept
        self.assertEqual(self.roi(5).shape, Rectangle(30.0, 12.0, 30.0))
        self.assertEqual(type(self.roi(1).shape).__name__, "Circle")
        self.assertIn("Resized 2 rectangle ROIs", self.messages[-1])
        undo_manager.undo()  # one step
        self.assertEqual((self.roi(4).shape.angle_deg, self.roi(5).shape.angle_deg), (0.0, 10.0))

    def test_cancelling_the_dialog_changes_nothing(self) -> None:
        self._two_rectangles()
        self.selection.set_roi_selection({4})
        with mock.patch("lspr_imaging_app.panels.image.roi_creation.ask_rectangle_size", return_value=None):
            self.right_click(10.0, 55.0, RoiMenuChoice(RESIZE_RECTANGLE))
        self.assertEqual(self.roi(4).shape, Rectangle(20.0, 10.0, 0.0))
        self.assertFalse(undo_manager.can_undo)

    def test_a_refused_size_says_why(self) -> None:
        self._two_rectangles()
        self.selection.set_roi_selection({4})
        with mock.patch("lspr_imaging_app.panels.image.roi_creation.ask_rectangle_size", return_value={"width_px": 0.2}):
            self.right_click(10.0, 55.0, RoiMenuChoice(RESIZE_RECTANGLE))
        self.assertIn("width must be at least", self.messages[-1])
        self.assertEqual(self.roi(4).shape, Rectangle(20.0, 10.0, 0.0))

    def test_the_menu_is_told_how_many_rectangles_are_selected(self) -> None:
        self._two_rectangles()
        self.selection.set_roi_selection({1, 4, 5})
        self.assertEqual(self.right_click(10.0, 55.0, None).call_args.kwargs["selected_rectangles"], 2)

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
                parent, selected_count=kwargs.pop("selected_count", 0), groups=[], any_selected_grouped=False, can_add=kwargs.pop("can_add", True), **kwargs
            )
        return built[0], parent

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

    def test_current_selection_is_greyed_without_an_area_selection_and_circle_rectangle_without_room(self) -> None:
        menu, _parent = self._build(can_add_from_selection=False)
        self.assertFalse(self._entry(menu, "Add ROI", "Current selection").isEnabled())
        self.assertTrue(self._entry(menu, "Add ROI", "Circle").isEnabled())
        menu, _parent = self._build(can_add=False, can_add_from_selection=True)
        self.assertFalse(self._entry(menu, "Add ROI", "Circle").isEnabled())
        self.assertFalse(self._entry(menu, "Add ROI", "Rectangle").isEnabled())
        self.assertTrue(self._entry(menu, "Add ROI", "Current selection").isEnabled())

    def test_make_this_roi_checks_the_role_the_whole_selection_has(self) -> None:
        menu, _parent = self._build(selected_count=2, selected_roles=(0, 2))
        self.assertEqual(self._entry(menu, "Make these ROIs", "Reference").isChecked(), True)
        self.assertEqual(self._entry(menu, "Make these ROIs", "Sample").isChecked(), False)
        menu, _parent = self._build(selected_count=2, selected_roles=(1, 1))  # mixed: neither
        self.assertFalse(self._entry(menu, "Make these ROIs", "Reference").isChecked() or self._entry(menu, "Make these ROIs", "Sample").isChecked())
        menu, _parent = self._build(selected_count=0)
        self.assertFalse(self._entry(menu, "Make these ROIs").isEnabled())

    def test_rectangle_size_is_enabled_only_with_a_rectangle_selected(self) -> None:
        menu, _parent = self._build(selected_count=1, selected_rectangles=0)
        self.assertFalse(self._entry(menu, "Rectangle size...").isEnabled())
        menu, _parent = self._build(selected_count=1, selected_rectangles=1)
        self.assertTrue(self._entry(menu, "Rectangle size...").isEnabled())

    def test_link_and_unlink_are_enabled_by_what_the_selection_holds_and_show_their_keys(self) -> None:
        menu, _parent = self._build(selected_count=2, selected_roles=(1, 1), can_link=True, can_unlink=False)
        link, unlink = self._entry(menu, "Link references"), self._entry(menu, "Unlink")
        self.assertTrue(link.isEnabled())
        self.assertFalse(unlink.isEnabled())
        self.assertEqual(link.shortcut(), QKeySequence(display("links.link")))
        self.assertEqual(unlink.shortcut(), QKeySequence(display("links.unlink")))
        self.assertTrue(link.isShortcutVisibleInContextMenu())


if __name__ == "__main__":
    unittest.main()
