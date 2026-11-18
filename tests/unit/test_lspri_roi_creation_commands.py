"""The toolbox commands behind phase 3's UI (apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md): pixel-mask ROIs made from a selection, a
rectangle's size and angle, and Link / Unlink of the selected ROIs with the line each says to the status bar.

Pure logic and the toolbox; no window, no files.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_shapes import Circle, MaskShape, Rectangle
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.link_actions import can_link, can_unlink, link_selected, split_by_role, unlink_selected
    from lspr_imaging_app.roi.mask_regions import mask_regions
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def _plane(*boxes, shape=(60, 80)) -> np.ndarray:
    plane = np.zeros(shape, dtype=bool)
    for y0, y1, x0, x1 in boxes:
        plane[y0:y1, x0:x1] = True
    return plane


class MaskRegionsTest(unittest.TestCase):
    def test_one_region_per_blob_in_reading_order_with_their_own_crop_and_centre(self) -> None:
        plane = _plane((30, 34, 10, 16), (5, 8, 40, 42), (5, 7, 10, 12))  # a lower blob, then two upper ones (right, left)
        regions = mask_regions(plane, together=False)
        self.assertEqual(len(regions), 3)
        first = regions[0]  # the upper left one: label() numbers blobs in raster order of their first pixel, row by row
        self.assertEqual((first[0].x0, first[0].y0, first[0].mask.shape), (10, 5, (2, 2)))
        self.assertEqual((first[1], first[2]), (10.5, 5.5))  # mean of the pixel indices
        self.assertEqual([(r.x0, r.y0) for r, _, _ in regions], [(10, 5), (40, 5), (10, 30)])
        for mask, _x, _y in regions:
            self.assertTrue(mask.mask.all())

    def test_together_is_one_region_holding_every_pixel_even_apart(self) -> None:
        plane = _plane((5, 8, 10, 13), (40, 44, 60, 66))
        (mask, cx, cy), = mask_regions(plane, together=True)
        self.assertEqual((mask.x0, mask.y0, mask.mask.shape), (10, 5, (39, 56)))
        self.assertEqual(int(mask.mask.sum()), int(plane.sum()))
        self.assertAlmostEqual(cx, float(np.nonzero(plane)[1].mean()))
        self.assertAlmostEqual(cy, float(np.nonzero(plane)[0].mean()))

    def test_blobs_touching_at_a_corner_are_one_blob(self) -> None:
        plane = np.zeros((10, 10), dtype=bool)
        plane[2, 2] = plane[3, 3] = True
        self.assertEqual(len(mask_regions(plane, together=False)), 1)

    def test_an_empty_plane_gives_nothing(self) -> None:
        self.assertEqual(mask_regions(np.zeros((8, 8), dtype=bool), together=False), [])
        self.assertEqual(mask_regions(np.zeros((8, 8), dtype=bool), together=True), [])

    def test_a_hole_stays_a_hole(self) -> None:
        plane = _plane((10, 20, 10, 20))
        plane[14:16, 14:16] = False
        (mask, _x, _y), = mask_regions(plane, together=False)
        self.assertFalse(mask.mask[4:6, 4:6].any())
        self.assertEqual(int(mask.mask.sum()), 100 - 4)


class AddMaskRoisTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.events = []
        self.toolbox.geometry_changed.connect(self.events.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_blobs_become_reference_rois_unlinked_in_one_undo_step_and_one_signal(self) -> None:
        ids = self.toolbox.add_mask_rois(_plane((5, 9, 10, 14), (30, 34, 40, 44), (50, 53, 5, 9)), together=False)
        self.assertEqual(ids, (1, 2, 3))
        rois = self.toolbox.rois()
        self.assertTrue(all(r.role == "reference" and r.reference_roi_ids == () and r.border is None for r in rois))
        self.assertTrue(all(isinstance(r.shape, MaskShape) for r in rois))
        self.assertEqual(len(self.events), 1)
        self.assertEqual(tuple(self.events[0].roi_ids), (1, 2, 3))
        undo_manager.undo()
        self.assertEqual(self.toolbox.rois(), ())
        undo_manager.redo()
        self.assertEqual([r.area_roi_id for r in self.toolbox.rois()], [1, 2, 3])

    def test_the_role_can_be_asked_for_and_together_makes_one_roi(self) -> None:
        ids = self.toolbox.add_mask_rois(_plane((5, 9, 10, 14), (30, 34, 40, 44)), together=True, role="sample")
        self.assertEqual(ids, (1,))
        self.assertEqual(self.toolbox.rois()[0].role, "sample")

    def test_the_centre_is_the_regions_centre_and_the_pixels_are_where_they_were_selected(self) -> None:
        self.toolbox.add_mask_rois(_plane((10, 14, 20, 26)), together=False)
        roi = self.toolbox.rois()[0]
        self.assertEqual((roi.center_x, roi.center_y), (22.5, 11.5))
        self.assertEqual((roi.shape.mask.x0, roi.shape.mask.y0, roi.shape.mask.mask.shape), (20, 10, (4, 6)))

    def test_nothing_selected_is_refused_and_changes_nothing(self) -> None:
        with self.assertRaisesRegex(ValueError, "nothing selected"):
            self.toolbox.add_mask_rois(np.zeros((20, 20), dtype=bool), together=False)
        self.assertEqual(self.toolbox.rois(), ())
        self.assertEqual(self.events, [])
        with self.assertRaises(ValueError):
            self.toolbox.add_mask_rois(_plane((1, 3, 1, 3)), together=False, role="weird")

    def test_new_ids_continue_after_existing_rois(self) -> None:
        self.toolbox.add_roi(10.0, 10.0)
        self.assertEqual(self.toolbox.add_mask_rois(_plane((5, 9, 10, 14)), together=False), (2,))


class ResizeRectanglesTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.rect = self.toolbox.add_shaped_roi(30.0, 30.0, Rectangle(20.0, 10.0, 0.0))
        self.circle = self.toolbox.add_roi(60.0, 30.0)
        undo_manager.clear()

    def tearDown(self) -> None:
        undo_manager.clear()

    def _shape(self, roi_id):
        return next(r for r in self.toolbox.rois() if r.area_roi_id == roi_id).shape

    def test_width_height_and_angle_change_alone_or_together_in_one_undo_step(self) -> None:
        self.toolbox.resize_rectangles([self.rect], width_px=24.0)
        self.assertEqual(self._shape(self.rect), Rectangle(24.0, 10.0, 0.0))
        self.toolbox.resize_rectangles([self.rect], height_px=12.0, angle_deg=30.0)
        self.assertEqual(self._shape(self.rect), Rectangle(24.0, 12.0, 30.0))
        undo_manager.undo()
        self.assertEqual(self._shape(self.rect), Rectangle(24.0, 10.0, 0.0))
        undo_manager.undo()
        self.assertEqual(self._shape(self.rect), Rectangle(20.0, 10.0, 0.0))

    def test_a_change_that_changes_nothing_pushes_no_undo_step(self) -> None:
        self.toolbox.resize_rectangles([self.rect], width_px=20.0, angle_deg=0.0)
        self.assertFalse(undo_manager.can_undo)

    def test_a_circle_a_bad_number_or_a_tiny_size_is_refused_before_anything_changes(self) -> None:
        for kwargs in ({"width_px": 30.0}, {"width_px": float("nan")}, {"height_px": 0.5}):
            with self.assertRaises(ValueError):
                self.toolbox.resize_rectangles([self.rect, self.circle], **kwargs)
        self.assertEqual(self._shape(self.rect), Rectangle(20.0, 10.0, 0.0))
        self.assertEqual(self._shape(self.circle), Circle(20.0))
        self.assertFalse(undo_manager.can_undo)


class LinkActionsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        for x in (10.0, 20.0, 30.0, 40.0, 50.0):
            self.toolbox.add_roi(x, 5.0)
        self.toolbox.set_roi_role([4, 5], "reference")  # samples 1, 2, 3; references 4, 5
        undo_manager.clear()

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_the_selection_is_split_by_role_and_unknown_ids_are_left_out(self) -> None:
        self.assertEqual(split_by_role(self.toolbox, [5, 1, 99, 3]), ((1, 3), (5,)))

    def test_link_joins_every_selected_sample_to_every_selected_reference(self) -> None:
        message = link_selected(self.toolbox, [1, 2, 4, 5])
        self.assertEqual(message, "Linked 2 sample ROIs to 2 reference ROIs.")
        self.assertEqual(self.toolbox.references_of(1), (4, 5))
        self.assertEqual(self.toolbox.references_of(2), (4, 5))
        self.assertEqual(self.toolbox.references_of(3), ())
        undo_manager.undo()
        self.assertEqual(self.toolbox.references_of(1), ())

    def test_link_needs_both_kinds_and_says_so(self) -> None:
        for ids in ([1, 2], [4, 5], []):
            with self.assertRaisesRegex(ValueError, "at least one sample ROI and one reference ROI"):
                link_selected(self.toolbox, ids)
        self.assertFalse(undo_manager.can_undo)

    def test_linking_what_is_linked_already_does_nothing_and_says_so(self) -> None:
        link_selected(self.toolbox, [1, 4])
        undo_manager.clear()
        self.assertIn("already linked", link_selected(self.toolbox, [1, 4]))
        self.assertFalse(undo_manager.can_undo)

    def test_unlink_with_samples_and_references_takes_apart_only_those_links(self) -> None:
        link_selected(self.toolbox, [1, 2, 4, 5])
        message = unlink_selected(self.toolbox, [1, 4])
        self.assertEqual(message, "Unlinked 1 sample ROI from 1 reference ROI.")
        self.assertEqual(self.toolbox.references_of(1), (5,))
        self.assertEqual(self.toolbox.references_of(2), (4, 5))

    def test_unlink_with_only_samples_removes_all_their_links(self) -> None:
        link_selected(self.toolbox, [1, 2, 4, 5])
        self.assertEqual(unlink_selected(self.toolbox, [1]), "Removed the linked references of 1 sample ROI.")
        self.assertEqual(self.toolbox.references_of(1), ())
        self.assertEqual(self.toolbox.references_of(2), (4, 5))

    def test_unlink_with_only_references_takes_them_from_every_sample_that_uses_them(self) -> None:
        link_selected(self.toolbox, [1, 2, 3, 4, 5])
        self.assertEqual(unlink_selected(self.toolbox, [4]), "Unlinked 1 reference ROI from 3 sample ROIs.")
        for sample in (1, 2, 3):
            self.assertEqual(self.toolbox.references_of(sample), (5,))
        undo_manager.undo()  # one step
        for sample in (1, 2, 3):
            self.assertEqual(self.toolbox.references_of(sample), (4, 5))

    def test_unlinking_what_is_not_linked_says_so_without_an_undo_step(self) -> None:
        for ids, text in (([1, 4], "not linked to the selected"), ([1], "no linked references"), ([4], "No sample is linked")):
            self.assertIn(text, unlink_selected(self.toolbox, ids))
        self.assertFalse(undo_manager.can_undo)
        with self.assertRaisesRegex(ValueError, "Select the ROIs"):
            unlink_selected(self.toolbox, [])

    def test_the_menu_states(self) -> None:
        self.assertTrue(can_link(self.toolbox, [1, 4]))
        self.assertFalse(can_link(self.toolbox, [1, 2]))
        self.assertFalse(can_unlink(self.toolbox, [1, 4]))
        link_selected(self.toolbox, [1, 4])
        self.assertFalse(can_link(self.toolbox, [1, 4]))
        for ids in ([1, 4], [1], [4]):
            self.assertTrue(can_unlink(self.toolbox, ids), ids)
        self.assertFalse(can_unlink(self.toolbox, [2, 5]))


if __name__ == "__main__":
    unittest.main()
