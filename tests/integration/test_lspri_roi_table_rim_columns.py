"""The ROI table's Offset and Thickness columns (2026-11-19, maintainer: "the rim columns should be the same for rectangle, circle and map, thus offset from edge and
thickness of band/rim").

Two numbers describe a rim whatever the shape: how far it starts from the shape's edge (negative = inside the shape) and how wide the band is. They replaced the circle's
inner / outer ring diameters (d_r, D_r). A reference has no rim, nor has a ROI whose rim is switched off: those cells are blank and not editable.

Real panel and model, no window shown.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QMimeData, Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_model import RoiMask
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape, Rectangle, ring_radii
    from lspr_imaging_app.panels.roi_table.model import INHERITED_ROLE
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS, PIXELS, step_text
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_roi_table_panel import _PanelCase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

EDITABLE = Qt.ItemFlag.ItemIsEditable


class RimColumnsTest(_PanelCase):
    """ROIs 1-5 are circles (setUp of the base); 6 a rectangle, 7 a pixel map, 8 a reference, 9 a circle with its rim switched off."""

    def setUp(self) -> None:
        super().setUp()
        self.rectangle = self.toolbox.add_shaped_roi(60.0, 5.0, Rectangle(30.0, 14.0, 0.0))
        self.map = self.toolbox.add_shaped_roi(80.0, 5.0, MaskShape(RoiMask(75, 0, np.ones((8, 8), dtype=bool))))
        self.reference = self.toolbox.add_shaped_roi(100.0, 5.0, Circle(8.0), role="reference")
        self.bare = self.toolbox.add_shaped_roi(120.0, 5.0, Circle(8.0), border=NO_BORDER)
        self.refresh()
        undo_manager.clear()

    def border(self, roi_id: int):
        return self.toolbox.roi_by_id(roi_id).border

    def test_every_shape_shows_the_same_two_numbers_and_a_reference_or_a_bare_roi_shows_none(self) -> None:
        for roi_id in (1, self.rectangle, self.map):
            self.assertEqual((self.text(roi_id, COLUMN_RIM_OFFSET), self.text(roi_id, COLUMN_RIM_THICKNESS)), ("4.0", "4.0"), roi_id)
            self.assertTrue(self.model.flags(self.cell(roi_id, COLUMN_RIM_OFFSET)) & EDITABLE, roi_id)
            self.assertTrue(self.model.data(self.cell(roi_id, COLUMN_RIM_OFFSET), INHERITED_ROLE), "the shared default's: grey italic")
        for roi_id in (self.reference, self.bare):
            self.assertEqual((self.text(roi_id, COLUMN_RIM_OFFSET), self.text(roi_id, COLUMN_RIM_THICKNESS)), ("", ""), roi_id)
            self.assertFalse(self.model.flags(self.cell(roi_id, COLUMN_RIM_OFFSET)) & EDITABLE, roi_id)

    def test_editing_a_number_sets_the_rim_of_a_circle_a_rectangle_and_a_map_and_leaves_the_shape(self) -> None:
        shapes = {i: self.toolbox.roi_by_id(i).shape for i in (1, self.rectangle, self.map)}
        for roi_id in (1, self.rectangle, self.map):
            self.edit(roi_id, COLUMN_RIM_OFFSET, "7")
            self.edit(roi_id, COLUMN_RIM_THICKNESS, "2.5")
            self.assertEqual(self.border(roi_id), Border(7.0, 2.5), roi_id)
            self.assertEqual(self.toolbox.roi_by_id(roi_id).shape, shapes[roi_id], "the shape is untouched")
        self.refresh()
        self.assertFalse(self.model.data(self.cell(self.map, COLUMN_RIM_OFFSET), INHERITED_ROLE), "now the ROI's own")

    def test_it_is_one_undo_step_per_edit_and_undo_goes_back_to_following_the_default(self) -> None:
        self.edit(self.map, COLUMN_RIM_OFFSET, "9")
        self.assertEqual(self.border(self.map), Border(9.0, 4.0))
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo, "one step")
        self.assertIsNone(self.border(self.map), "no rim of its own again: it follows the shared default")

    def test_a_selection_of_mixed_shapes_gets_the_value_and_a_roi_without_a_rim_is_skipped(self) -> None:
        self.selection.set_roi_selection({1, self.rectangle, self.map, self.reference, self.bare})
        self.edit(1, COLUMN_RIM_THICKNESS, "6")
        for roi_id in (1, self.rectangle, self.map):
            self.assertEqual(self.border(roi_id), Border(4.0, 6.0), roi_id)
        self.assertEqual(self.border(self.bare), NO_BORDER)
        self.assertEqual(self.toolbox.roi_by_id(self.reference).role, "reference")
        self.assertIn("Set the rim thickness of 3 ROIs", self.messages[-1])

    def test_a_negative_offset_starts_the_rim_inside_the_shape_and_a_negative_thickness_is_refused(self) -> None:
        self.edit(self.rectangle, COLUMN_RIM_OFFSET, "-3")
        self.assertEqual(self.border(self.rectangle).gap_px, -3.0)
        self.edit(self.rectangle, COLUMN_RIM_THICKNESS, "-1")
        self.assertIn("thickness cannot be negative", self.footer())
        self.assertEqual(self.border(self.rectangle), Border(-3.0, 4.0), "nothing changed")

    def test_a_circles_numbers_are_the_same_rim_the_ring_diameters_made(self) -> None:
        """Offset 3 and thickness 4 round a 10 px circle are the ring from diameter 16 to 24: the same radii, bit for bit, as `resize_rois` stores for those diameters."""
        self.toolbox.resize_rois([1], sample_diameter_px=10.0)
        self.toolbox.resize_rois([2], sample_diameter_px=10.0, reference_inner_diameter_px=16.0, reference_outer_diameter_px=24.0)
        self.edit(1, COLUMN_RIM_OFFSET, "3")
        self.edit(1, COLUMN_RIM_THICKNESS, "4")
        first, second = self.toolbox.roi_by_id(1), self.toolbox.roi_by_id(2)
        self.assertEqual(ring_radii(first.shape, first.border), ring_radii(second.shape, second.border))
        self.assertEqual(ring_radii(first.shape, first.border), (8.0, 12.0))

    def test_the_numbers_move_the_rim_a_map_is_measured_with(self) -> None:
        shape = (40, 120)
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        near = rz.rasterize_reference(self.toolbox.roi_by_id(self.map), shape, identity, default_border=Border(4.0, 4.0))
        self.edit(self.map, COLUMN_RIM_OFFSET, "10")
        far = rz.rasterize_reference(self.toolbox.roi_by_id(self.map), shape, identity, default_border=Border(4.0, 4.0))
        self.assertGreater(int(near.sum()), 0)
        self.assertFalse((near & far).any(), "an offset of 10 puts the band beyond the one at 4 (both 4 px wide)")

    def test_a_paste_from_a_rectangle_goes_into_a_circle_and_a_map(self) -> None:
        from lspr_imaging_app.panels.roi_table import cell_clipboard as cc

        self.edit(self.rectangle, COLUMN_RIM_OFFSET, "8")
        self.edit(self.rectangle, COLUMN_RIM_THICKNESS, "5")
        block = cc.CellBlock((COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS), ((8.0, 5.0),))
        mime = QMimeData()
        mime.setData(cc.MIME_TYPE, cc.block_to_payload(block))
        QtWidgets.QApplication.clipboard().setMimeData(mime)
        self.selection.set_roi_selection({1, self.map})
        undo_manager.clear()
        self.panel._paste_cells()
        self.assertEqual((self.border(1), self.border(self.map)), (Border(8.0, 5.0), Border(8.0, 5.0)))
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo, "one undo step for the whole paste")
        self.assertIsNone(self.border(1))

    def test_a_paste_into_a_reference_is_refused_and_changes_nothing(self) -> None:
        from lspr_imaging_app.panels.roi_table import cell_clipboard as cc

        block = cc.CellBlock((COLUMN_RIM_OFFSET,), ((8.0,),))
        mime = QMimeData()
        mime.setData(cc.MIME_TYPE, cc.block_to_payload(block))
        QtWidgets.QApplication.clipboard().setMimeData(mime)
        self.selection.set_roi_selection({self.reference})
        self.panel._paste_cells()
        self.assertFalse(undo_manager.can_undo)
        self.assertEqual(self.toolbox.roi_by_id(self.reference).role, "reference")

    def test_the_editor_steps_an_offset_below_zero_but_not_a_thickness(self) -> None:
        self.assertEqual(step_text("0", -1, False, PIXELS, signed=True), "-0.5")
        self.assertEqual(step_text("0", -1, False, PIXELS), "0")
        self.assertEqual(step_text("-0.5", 1, True, PIXELS, signed=True), "4.5")

    def test_the_wheel_steps_an_offset_through_zero(self) -> None:
        self.edit(1, COLUMN_RIM_OFFSET, "0")
        self.panel._step_cell(self.cell(1, COLUMN_RIM_OFFSET), -1, False)  # one notch down, as the wheel and Up / Down do
        self.assertEqual(self.border(1).gap_px, -0.5)
        self.edit(1, COLUMN_RIM_THICKNESS, "0")
        self.panel._step_cell(self.cell(1, COLUMN_RIM_THICKNESS), -1, False)
        self.assertEqual(self.border(1).thickness_px, 0.0, "a thickness stops at zero")

    def test_sorting_puts_the_rois_with_no_rim_first_then_by_number(self) -> None:
        self.edit(1, COLUMN_RIM_OFFSET, "12")
        self.edit(2, COLUMN_RIM_OFFSET, "-2")
        self.model.set_sort(COLUMN_RIM_OFFSET, False)
        order = self.shown_ids()
        self.assertEqual(order[:2], [self.reference, self.bare], "no rim: first, by id")
        self.assertEqual(order[2], 2, "then the smallest offset (-2)")
        self.assertEqual(order[-1], 1, "and the largest (12)")


if __name__ == "__main__":
    unittest.main()
