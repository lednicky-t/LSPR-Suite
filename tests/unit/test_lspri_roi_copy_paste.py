"""Copy and paste of whole ROIs (2026-11-24): `RoiToolbox.copy_rois` / `paste_rois`. Pure logic and the toolbox; no window, no files."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape, Rectangle
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


class RoiCopyPasteTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()

    def _centres(self, ids):
        return [(self.toolbox.roi_by_id(i).center_x, self.toolbox.roi_by_id(i).center_y) for i in ids]

    def test_one_roi_lands_with_its_centre_on_the_point_and_keeps_shape_rim_and_role(self) -> None:
        rim = Border(3.0, 5.0)
        a = self.toolbox.add_shaped_roi(10.0, 20.0, Rectangle(12.0, 8.0, 30.0), border=rim, role="sample")
        clip = self.toolbox.copy_rois({a})
        (new,) = self.toolbox.paste_rois(clip, 50.0, 60.0)
        pasted = self.toolbox.roi_by_id(new)
        self.assertEqual((pasted.center_x, pasted.center_y), (50.0, 60.0))
        self.assertEqual(pasted.shape, Rectangle(12.0, 8.0, 30.0))
        self.assertEqual((pasted.border, pasted.role), (rim, "sample"))
        self.assertEqual((self.toolbox.roi_by_id(a).center_x, self.toolbox.roi_by_id(a).center_y), (10.0, 20.0))  # the original did not move

    def test_several_rois_keep_their_arrangement_with_the_middle_of_their_box_on_the_point(self) -> None:
        a = self.toolbox.add_roi(10.0, 10.0)
        b = self.toolbox.add_roi(30.0, 20.0)
        c = self.toolbox.add_roi(20.0, 40.0)
        clip = self.toolbox.copy_rois({a, b, c})
        self.assertEqual(clip.anchor, (20.0, 25.0))
        ids = self.toolbox.paste_rois(clip, 100.0, 100.0)
        self.assertEqual(ids, (4, 5, 6))  # the next free ids, in the clip's order
        self.assertEqual(self._centres(ids), [(90.0, 85.0), (110.0, 95.0), (100.0, 115.0)])

    def test_links_between_copied_rois_are_made_again_and_links_to_others_are_dropped(self) -> None:
        reference = self.toolbox.add_shaped_roi(40.0, 10.0, Circle(10.0), role="reference")
        other_reference = self.toolbox.add_shaped_roi(60.0, 10.0, Circle(10.0), role="reference")
        sample = self.toolbox.add_roi(10.0, 10.0)
        self.toolbox.link_references({sample}, {reference, other_reference})
        clip = self.toolbox.copy_rois({sample, reference})  # `other_reference` is not copied
        new_sample, new_reference = None, None
        ids = self.toolbox.paste_rois(clip, 200.0, 200.0)
        for i in ids:
            if self.toolbox.roi_by_id(i).role == "reference":
                new_reference = i
            else:
                new_sample = i
        self.assertEqual(self.toolbox.roi_by_id(new_sample).reference_roi_ids, (new_reference,))
        self.assertEqual(self.toolbox.roi_by_id(sample).reference_roi_ids, (reference, other_reference))  # the original is untouched

    def test_a_pixel_map_moves_by_whole_pixels(self) -> None:
        plane = np.zeros((40, 40), dtype=bool)
        plane[10:14, 10:16] = True
        (a,) = self.toolbox.add_mask_rois(plane, together=True)
        clip = self.toolbox.copy_rois({a})
        old = self.toolbox.roi_by_id(a)
        (new,) = self.toolbox.paste_rois(clip, old.center_x + 20.4, old.center_y - 3.6)
        moved = self.toolbox.roi_by_id(new)
        self.assertIsInstance(moved.shape, MaskShape)
        self.assertEqual((moved.shape.mask.x0 - old.shape.mask.x0, moved.shape.mask.y0 - old.shape.mask.y0), (20, -4))
        self.assertTrue(np.array_equal(moved.shape.mask.mask, old.shape.mask.mask))  # the pixels themselves are not resampled

    def test_the_copy_is_taken_where_the_roi_is_at_that_cube(self) -> None:
        a = self.toolbox.add_roi(10.0, 10.0)
        self.toolbox.move_roi(a, 30.0, 30.0, cube=5)  # from cube 5 on it sits elsewhere
        at_5 = self.toolbox.copy_rois({a}, cube=5)
        at_0 = self.toolbox.copy_rois({a}, cube=0)
        self.assertEqual(at_5.anchor, (30.0, 30.0))
        self.assertEqual(at_0.anchor, (10.0, 10.0))

    def test_paste_is_one_undo_step_and_undo_then_redo_restore_ids(self) -> None:
        a = self.toolbox.add_roi(10.0, 10.0)
        clip = self.toolbox.copy_rois({a})
        ids =self.toolbox.paste_rois(clip, 50.0, 50.0) + self.toolbox.paste_rois(clip, 70.0, 70.0)
        self.assertEqual(ids, (2, 3))
        undo_manager.undo()
        self.assertEqual([r.area_roi_id for r in self.toolbox.rois()], [1, 2])
        undo_manager.undo()
        self.assertEqual([r.area_roi_id for r in self.toolbox.rois()], [1])
        self.assertEqual(self.toolbox.add_roi(5.0, 5.0), 2)  # the id counter went back with the undo: no gap

    def test_nothing_to_copy_or_paste_says_so(self) -> None:
        with self.assertRaises(ValueError):
            self.toolbox.copy_rois(set())
        with self.assertRaises(KeyError):
            self.toolbox.copy_rois({99})
        a = self.toolbox.add_roi(1.0, 1.0)
        clip = self.toolbox.copy_rois({a})
        with self.assertRaises(ValueError):
            self.toolbox.paste_rois(clip, float("nan"), 0.0)

    def test_a_rim_less_sample_stays_rim_less(self) -> None:
        a = self.toolbox.add_shaped_roi(10.0, 10.0, Circle(10.0), border=NO_BORDER)
        (new,) = self.toolbox.paste_rois(self.toolbox.copy_rois({a}), 30.0, 30.0)
        self.assertEqual(self.toolbox.roi_by_id(new).border, NO_BORDER)


if __name__ == "__main__":
    unittest.main()
