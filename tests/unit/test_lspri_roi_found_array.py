"""`RoiToolbox.place_found_array` (Find*): the array the image gave becomes ROIs, one recipe, one undo step."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _array_synth import synth  # noqa: E402

from lspr_imaging_app.domain.roi_shapes import Border, Circle  # noqa: E402
from lspr_imaging_app.roi import RoiToolbox  # noqa: E402
from lspr_imaging_app.roi.array_finder import FinderSeed, FinderSettings, build_array, find_candidates  # noqa: E402
from lspr_imaging_app.roi.array_layout import ArraySpec  # noqa: E402
from lspr_imaging_app.undo import undo_manager  # noqa: E402


class PlaceFoundArrayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image, cls.centres = synth(rows=4, cols=5, missing=((1, 1),), tilt_deg=1.0)

    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()

    def tearDown(self) -> None:
        undo_manager.clear()

    def _find(self, seeds, **settings):
        s = FinderSettings(**settings)
        return build_array(find_candidates(self.image, tuple(seeds), s), s), s

    def _seed_roi(self, node=(0, 0), rim=Border(2.0, 3.0)) -> tuple[int, FinderSeed]:
        x, y = self.centres[node]
        return self.toolbox.add_shaped_roi(x + 0.7, y - 0.4, Circle(20.0), border=rim), FinderSeed(x + 0.7, y - 0.4, 20.0, Circle(20.0), rim)

    def test_copies_of_the_seed_at_the_measured_positions_and_the_seed_stays(self) -> None:
        seed_id, seed = self._seed_roi()
        result, settings = self._find([seed])
        new = self.toolbox.place_found_array(result, settings, [seed_id], ArraySpec())
        self.assertEqual(len(new), 18)  # 4 x 5 - 1 missing - the seed
        self.assertEqual((self.toolbox.roi_by_id(seed_id).center_x, self.toolbox.roi_by_id(seed_id).center_y), (round(self.centres[(0, 0)][0] + 0.7, 2), round(self.centres[(0, 0)][1] - 0.4, 2)))
        copy = self.toolbox.roi_by_id(new[-1])
        self.assertEqual((copy.shape, copy.border, copy.created_by), (Circle(20.0), Border(2.0, 3.0), "array"))
        self.assertLess(min(np.hypot(copy.center_x - x, copy.center_y - y) for x, y in self.centres.values()), 0.2)

    def test_recipe_lists_every_member_and_the_seed_joins_the_array(self) -> None:
        seed_id, seed = self._seed_roi()
        result, settings = self._find([seed])
        self.toolbox.place_found_array(result, settings, [seed_id], ArraySpec())
        recipe = next(iter(self.toolbox.array_groups()))
        self.assertEqual((recipe.rows, recipe.cols, len(recipe.member_area_roi_ids)), (4, 5, 19))
        self.assertAlmostEqual(recipe.spacing_x_px, 44.0, delta=0.2)
        self.assertEqual(self.toolbox.roi_by_id(seed_id).array_id, recipe.array_id)

    def test_one_undo_step_takes_it_all_back(self) -> None:
        seed_id, seed = self._seed_roi()
        undo_manager.clear()  # (drawing the seed was a step of its own)
        result, settings = self._find([seed])
        self.toolbox.place_found_array(result, settings, [seed_id], ArraySpec())
        undo_manager.undo()
        self.assertEqual([roi.area_roi_id for roi in self.toolbox.rois()], [seed_id])
        self.assertIsNone(self.toolbox.roi_by_id(seed_id).array_id)
        self.assertFalse(undo_manager.can_undo)
        undo_manager.redo()
        self.assertEqual(len(self.toolbox.rois()), 19)

    def test_numbering_by_columns_from_the_bottom_right(self) -> None:
        seed_id, seed = self._seed_roi()
        result, settings = self._find([seed])
        spec = ArraySpec(number_by="columns", number_start_x=1.0, number_start_y=1.0)
        self.toolbox.place_found_array(result, settings, [seed_id], spec)
        bottom_right = max(self.toolbox.rois(), key=lambda r: (r.center_x + r.center_y))
        self.assertEqual(bottom_right.area_roi_id, 1)
        undo_manager.undo()
        self.assertEqual([roi.area_roi_id for roi in self.toolbox.rois()], [seed_id])

    def test_without_a_guide_the_new_rois_are_circles_with_a_rim_of_their_own_area(self) -> None:
        result, settings = self._find([], fix_size=False)
        new = self.toolbox.place_found_array(result, settings, [], ArraySpec())
        self.assertEqual(len(new), 19)
        roi = self.toolbox.roi_by_id(new[0])
        self.assertIsInstance(roi.shape, Circle)
        gap = self.toolbox.detection_settings().default_border().gap_px
        self.assertEqual(roi.border.gap_px, gap)
        self.assertGreater(roi.border.thickness_px, 0.0)

    def test_a_seed_that_belongs_to_an_array_is_refused_and_nothing_changes(self) -> None:
        seed_id, seed = self._seed_roi()
        result, settings = self._find([seed])
        self.toolbox.place_found_array(result, settings, [seed_id], ArraySpec())
        before = len(self.toolbox.rois())
        with self.assertRaises(ValueError):
            self.toolbox.place_found_array(result, settings, [seed_id], ArraySpec())
        self.assertEqual(len(self.toolbox.rois()), before)


if __name__ == "__main__":
    unittest.main()
