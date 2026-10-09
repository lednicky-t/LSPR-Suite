"""`RoiToolbox.place_array` / `refine_rois`: store an Array result as one undo step, renumbering like `delete_rois`."""

from __future__ import annotations

import unittest

import numpy as np

from lspr_imaging_app.roi import RoiToolbox
from lspr_imaging_app.undo import undo_manager


def _grid(rows: int, cols: int, pitch: float = 50.0, origin=(100.0, 100.0)):
    nodes = np.array([(origin[0] + c * pitch, origin[1] + r * pitch) for r in range(rows) for c in range(cols)])
    count = rows * cols
    return nodes, np.full(count, 30.0), np.full(count, 40.0), np.full(count, 50.0)


class PlaceArrayTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.maps: list[dict] = []
        self.toolbox.roi_ids_renumbered.connect(self.maps.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_adds_the_array_with_its_recipe(self) -> None:
        nodes, s, i, o = _grid(2, 3)
        ids = self.toolbox.place_array(nodes, s, i, o, rows=2, cols=3, pitch_x_px=50.0, pitch_y_px=50.0, rotation_deg=1.5)
        self.assertEqual(ids, [1, 2, 3, 4, 5, 6])
        roi = self.toolbox.roi_by_id(4)
        self.assertEqual((roi.center_x, roi.center_y), (100.0, 150.0))
        self.assertEqual((roi.sample_diameter_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px), (30.0, 40.0, 50.0))
        (array,) = self.toolbox.array_groups()
        self.assertEqual((array.rows, array.cols, array.spacing_x_px, array.rotation_deg), (2, 3, 50.0, 1.5))
        self.assertEqual(array.member_area_roi_ids, ids)
        self.assertEqual((array.anchor_x_px, array.anchor_y_px), (100.0, 100.0))
        self.assertEqual(roi.array_id, array.array_id)

    def test_is_one_undo_step(self) -> None:
        nodes, s, i, o = _grid(2, 2)
        self.toolbox.place_array(nodes, s, i, o, rows=2, cols=2, pitch_x_px=50.0, pitch_y_px=50.0)
        self.assertTrue(undo_manager.can_undo)
        undo_manager.undo()
        self.assertEqual(self.toolbox.rois(), ())
        self.assertEqual(self.toolbox.array_groups(), ())
        self.assertFalse(undo_manager.can_undo)
        undo_manager.redo()
        self.assertEqual(len(self.toolbox.rois()), 4)

    def test_replaces_only_the_selection_and_renumbers_the_rest(self) -> None:
        for x in (10.0, 20.0, 30.0, 40.0):
            self.toolbox.add_roi(x, 5.0)  # ids 1..4
        self.toolbox.group_rois((1, 4), "keep")
        undo_manager.clear()
        nodes, s, i, o = _grid(1, 2)
        ids = self.toolbox.place_array(nodes, s, i, o, rows=1, cols=2, pitch_x_px=50.0, pitch_y_px=50.0, replace_ids=(2, 3))
        # survivors 1 and 4 become 1 and 2, the new array is 3 and 4
        self.assertEqual(ids, [3, 4])
        self.assertEqual([r.center_x for r in self.toolbox.rois()], [10.0, 40.0, 100.0, 150.0])
        self.assertEqual(self.maps[-1], {1: 1, 4: 2})
        (group,) = self.toolbox.groups()
        self.assertEqual(group.area_roi_ids, [1, 2])  # the group followed its members
        undo_manager.undo()
        self.assertEqual([r.center_x for r in self.toolbox.rois()], [10.0, 20.0, 30.0, 40.0])
        self.assertEqual([r.area_roi_id for r in self.toolbox.rois()], [1, 2, 3, 4])
        self.assertEqual(self.maps[-1], {1: 1, 2: 4})
        (group,) = self.toolbox.groups()
        self.assertEqual(group.area_roi_ids, [1, 4])
        self.assertEqual(self.toolbox.array_groups(), ())

    def test_replacing_every_member_prunes_the_old_array(self) -> None:
        nodes, s, i, o = _grid(2, 2)
        self.toolbox.place_array(nodes, s, i, o, rows=2, cols=2, pitch_x_px=50.0, pitch_y_px=50.0)
        nodes2, s2, i2, o2 = _grid(1, 3)
        self.toolbox.place_array(nodes2, s2, i2, o2, rows=1, cols=3, pitch_x_px=50.0, pitch_y_px=50.0, replace_ids=(1, 2, 3, 4))
        (array,) = self.toolbox.array_groups()
        self.assertEqual((array.rows, array.cols), (1, 3))
        self.assertEqual(len(self.toolbox.rois()), 3)
        undo_manager.undo()
        (array,) = self.toolbox.array_groups()
        self.assertEqual((array.rows, array.cols), (2, 2))

    def test_a_second_array_gets_its_own_id(self) -> None:
        nodes, s, i, o = _grid(1, 2)
        self.toolbox.place_array(nodes, s, i, o, rows=1, cols=2, pitch_x_px=50.0, pitch_y_px=50.0)
        self.toolbox.place_array(nodes + 500.0, s, i, o, rows=1, cols=2, pitch_x_px=50.0, pitch_y_px=50.0)
        self.assertEqual(len({a.array_id for a in self.toolbox.array_groups()}), 2)
        self.assertEqual([r.area_roi_id for r in self.toolbox.rois()], [1, 2, 3, 4])

    def test_unlocated_spots_are_marked_inferred(self) -> None:
        nodes, s, i, o = _grid(1, 3)
        self.toolbox.place_array(nodes, s, i, o, rows=1, cols=3, pitch_x_px=50.0, pitch_y_px=50.0, located=[True, False, True])
        self.assertEqual([r.inferred for r in self.toolbox.rois()], [False, True, False])

    def test_bad_input_changes_nothing(self) -> None:
        nodes, s, i, o = _grid(1, 2)
        for bad in (
            dict(sample_diameters_px=[30.0, 1.0]),  # below the minimum
            dict(ring_inner_diameters_px=[40.0, 60.0]),  # inner >= outer
            dict(sample_diameters_px=[30.0, float("nan")]),
        ):
            args = dict(centers_xy=nodes, sample_diameters_px=s, ring_inner_diameters_px=i, ring_outer_diameters_px=o)
            args.update(bad)
            with self.assertRaises(ValueError):
                self.toolbox.place_array(**args, rows=1, cols=2, pitch_x_px=50.0, pitch_y_px=50.0)
        with self.assertRaises(ValueError):
            self.toolbox.place_array(nodes, s, i, o, rows=2, cols=2, pitch_x_px=50.0, pitch_y_px=50.0)  # count mismatch
        self.assertEqual(self.toolbox.rois(), ())
        self.assertFalse(undo_manager.can_undo)


class RefineRoisTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        for x in (10.0, 60.0):
            self.toolbox.add_roi(x, 20.0, sample_diameter_px=20.0)
        undo_manager.clear()

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_positions_and_diameters_in_one_undo_step(self) -> None:
        self.toolbox.refine_rois({1: (11.2, 20.4, 24.0, 32.0, 40.0), 2: (59.0, 19.5, 24.0, 32.0, 40.0)})
        roi = self.toolbox.roi_by_id(1)
        self.assertEqual((roi.center_x, roi.center_y, roi.sample_diameter_px, roi.reference_inner_diameter_px), (11.2, 20.4, 24.0, 32.0))
        undo_manager.undo()
        roi = self.toolbox.roi_by_id(1)
        self.assertEqual((roi.center_x, roi.center_y, roi.sample_diameter_px, roi.reference_inner_diameter_px), (10.0, 20.0, 20.0, None))
        self.assertFalse(undo_manager.can_undo)

    def test_bad_values_change_nothing(self) -> None:
        with self.assertRaises(ValueError):
            self.toolbox.refine_rois({1: (11.0, 20.0, 24.0, 40.0, 32.0)})
        with self.assertRaises(ValueError):
            self.toolbox.refine_rois({1: (float("nan"), 20.0, 24.0, 32.0, 40.0)})
        with self.assertRaises(KeyError):
            self.toolbox.refine_rois({99: (11.0, 20.0, 24.0, 32.0, 40.0)})
        self.assertEqual(self.toolbox.roi_by_id(1).center_x, 10.0)
        self.assertFalse(undo_manager.can_undo)


if __name__ == "__main__":
    unittest.main()
