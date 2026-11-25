"""`RoiToolbox.stamp_array` (manual array, Array*): copies of the selection on the other lattice nodes, one recipe, one undo step."""

from __future__ import annotations

import unittest

import numpy as np

from lspr_imaging_app.domain.roi_shapes import Border, Circle, Rectangle
from lspr_imaging_app.roi import RoiToolbox
from lspr_imaging_app.roi.array_layout import ArraySpec
from lspr_imaging_app.undo import undo_manager


class StampArrayTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.signals: list[object] = []
        self.toolbox.geometry_changed.connect(self.signals.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _seed(self, x: float = 100.0, y: float = 50.0, diameter: float = 20.0) -> int:
        return self.toolbox.add_shaped_roi(x, y, Circle(diameter), border=Border(2.0, 3.0))

    def test_copies_keep_shape_and_rim_and_the_seed_stays(self) -> None:
        seed = self._seed()
        new = self.toolbox.stamp_array([seed], ArraySpec(rows=2, cols=3, pitch_x_px=40.0, pitch_y_px=30.0))
        self.assertEqual(len(new), 5)
        self.assertEqual((self.toolbox.roi_by_id(seed).center_x, self.toolbox.roi_by_id(seed).center_y), (100.0, 50.0))
        copy = self.toolbox.roi_by_id(new[-1])  # node (1, 2)
        self.assertEqual((copy.center_x, copy.center_y), (180.0, 80.0))
        self.assertEqual(copy.shape, Circle(20.0))
        self.assertEqual(copy.border, Border(2.0, 3.0))
        self.assertEqual(copy.created_by, "array")

    def test_recipe_holds_spec_members_and_seed_node(self) -> None:
        seed = self._seed()
        new = self.toolbox.stamp_array([seed], ArraySpec(rows=2, cols=2, pitch_x_px=10.0, pitch_y_px=10.0, start_x=1.0, start_y=1.0))
        (array,) = self.toolbox.array_groups()
        # The seed is the bottom-right node (row-major index 3); the default numbering (by rows from the top left) therefore gives it the LAST of
        # the four ids, and the copies 1, 2, 3 (its own id, 1, moved to 4).
        self.assertEqual(new, (1, 2, 3))
        self.assertEqual(array.member_area_roi_ids, [1, 2, 3, 4])
        self.assertEqual((self.toolbox.roi_by_id(4).center_x, self.toolbox.roi_by_id(4).center_y), (100.0, 50.0))  # the selected ROI, now number 4
        self.assertEqual((array.rows, array.cols, array.seed_node, array.unit_size), (2, 2, 3, 1))
        self.assertEqual(ArraySpec.from_dict(array.spec), ArraySpec(rows=2, cols=2, pitch_x_px=10.0, pitch_y_px=10.0, start_x=1.0, start_y=1.0))
        for roi_id in array.member_area_roi_ids:
            self.assertEqual(self.toolbox.roi_by_id(roi_id).array_id, array.array_id)

    def test_unit_of_two_rois_is_repeated_node_by_node(self) -> None:
        a, b = self._seed(100.0, 50.0), self._seed(120.0, 50.0)
        new = self.toolbox.stamp_array([b, a], ArraySpec(rows=1, cols=2, pitch_x_px=100.0))
        self.assertEqual(len(new), 2)
        self.assertEqual([(self.toolbox.roi_by_id(i).center_x) for i in new], [200.0, 220.0])  # the unit moves one pitch, its inner spacing (20) is kept
        self.assertEqual(self.toolbox.array_groups()[0].unit_size, 2)

    def test_numbering_by_columns_from_the_bottom_right(self) -> None:
        seed = self._seed(100.0, 50.0)
        spec = ArraySpec(rows=2, cols=2, pitch_x_px=10.0, pitch_y_px=10.0, number_by="columns", number_start_x=1.0, number_start_y=1.0)
        self.toolbox.stamp_array([seed], spec)
        position = {roi.area_roi_id: (roi.center_x, roi.center_y) for roi in self.toolbox.rois()}
        # 1 = bottom right, then up the right column, then the left column from the bottom
        self.assertEqual([position[i] for i in (1, 2, 3, 4)], [(110.0, 60.0), (110.0, 50.0), (100.0, 60.0), (100.0, 50.0)])

    def test_numbering_follows_a_unit_node_by_node(self) -> None:
        a, b = self._seed(0.0, 0.0), self._seed(5.0, 0.0)
        self.toolbox.stamp_array([a, b], ArraySpec(rows=1, cols=2, pitch_x_px=100.0, number_start_x=1.0))
        xs = [roi.center_x for roi in self.toolbox.rois()]
        self.assertEqual(xs, [100.0, 105.0, 0.0, 5.0])  # from the right: the right node's two ROIs first, each node in its own order

    def test_renumbering_is_one_undo_step_and_undo_restores_the_ids(self) -> None:
        seed = self._seed(100.0, 50.0)
        other = self._seed(300.0, 300.0)
        undo_manager.clear()
        spec = ArraySpec(rows=1, cols=3, pitch_x_px=10.0, start_x=1.0)  # the seed is the right-most node, so its own id changes
        self.toolbox.stamp_array([seed], spec)
        self.assertEqual(self.toolbox.roi_by_id(other).center_x, 300.0)  # a ROI outside the array keeps its number
        self.assertEqual(self.toolbox.roi_by_id(4).center_x, 100.0)  # the seed took the last of the ids the array holds (1, 3, 4)
        undo_manager.undo()
        self.assertEqual([r.area_roi_id for r in self.toolbox.rois()], [1, 2])
        self.assertEqual(self.toolbox.roi_by_id(1).center_x, 100.0)
        self.assertFalse(undo_manager.can_undo)
        undo_manager.redo()
        self.assertEqual(self.toolbox.roi_by_id(4).center_x, 100.0)

    def test_one_undo_step_and_redo(self) -> None:
        seed = self._seed()
        undo_manager.clear()
        self.toolbox.stamp_array([seed], ArraySpec(rows=2, cols=2, pitch_x_px=20.0, pitch_y_px=20.0))
        undo_manager.undo()
        self.assertEqual(len(self.toolbox.rois()), 1)
        self.assertEqual(self.toolbox.array_groups(), ())
        self.assertIsNone(self.toolbox.roi_by_id(seed).array_id)
        self.assertFalse(undo_manager.can_undo)
        undo_manager.redo()
        self.assertEqual(len(self.toolbox.rois()), 4)
        self.assertEqual(len(self.toolbox.array_groups()), 1)

    def test_next_array_gets_the_next_id_and_new_rois_the_next_free_ids(self) -> None:
        first, second = self._seed(0.0, 0.0), self._seed(500.0, 0.0)
        self.toolbox.stamp_array([first], ArraySpec(rows=1, cols=2, pitch_x_px=50.0))
        new = self.toolbox.stamp_array([second], ArraySpec(rows=1, cols=2, pitch_x_px=50.0))
        self.assertEqual(new, (4,))
        self.assertEqual([a.array_id for a in self.toolbox.array_groups()], ["array_1", "array_2"])

    def test_refusals_change_nothing(self) -> None:
        seed = self._seed()
        self.toolbox.stamp_array([seed], ArraySpec(rows=1, cols=2, pitch_x_px=50.0))
        before = len(self.toolbox.rois())
        with self.assertRaisesRegex(ValueError, "already belongs to array_1"):
            self.toolbox.stamp_array([seed], ArraySpec(rows=2, cols=2))
        other = self._seed(400.0, 400.0)
        with self.assertRaisesRegex(ValueError, "pitch x"):
            self.toolbox.stamp_array([other], ArraySpec(rows=2, cols=2, pitch_x_px=0.0))
        with self.assertRaises(ValueError):
            self.toolbox.stamp_array([], ArraySpec())
        with self.assertRaises(KeyError):
            self.toolbox.stamp_array([999], ArraySpec())
        self.assertEqual(len(self.toolbox.rois()), before + 1)
        self.assertEqual(len(self.toolbox.array_groups()), 1)

    def test_rectangle_seed_keeps_its_angle(self) -> None:
        seed = self.toolbox.add_shaped_roi(10.0, 10.0, Rectangle(8.0, 4.0, 30.0))
        (new,) = self.toolbox.stamp_array([seed], ArraySpec(rows=1, cols=2, pitch_x_px=20.0))
        self.assertEqual(self.toolbox.roi_by_id(new).shape, Rectangle(8.0, 4.0, 30.0))
        self.assertTrue(np.isclose(self.toolbox.roi_by_id(new).center_x, 30.0))


if __name__ == "__main__":
    unittest.main()
