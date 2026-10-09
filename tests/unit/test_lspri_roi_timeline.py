"""ROI geometry timeline: resolution (the Mask rule), toolbox commands with cube / scope, undo, remap, storage.

Design: apps/LSPRi/eva/docs/roi_timeline_design_2026-10-08.md. Pure model + toolbox, no Qt widgets.
"""

from __future__ import annotations

import json
import unittest

import numpy as np

from lspr_imaging_app.roi import RoiToolbox
from lspr_imaging_app.roi.model import (
    SCOPE_INDIVIDUAL,
    SCOPE_PERSISTENT,
    AreaRoi,
    RoiGeometry,
    RoiTimeline,
    geometry_at,
    resolved_at,
)
from lspr_imaging_app.storage.session import _decode_area_rois, _encode_area_roi
from lspr_imaging_app.undo import undo_manager


def _g(x: float, d: float = 20.0) -> RoiGeometry:
    return RoiGeometry(x, 10.0, d, None, None)


class TimelineResolutionTest(unittest.TestCase):
    def test_empty_timeline_is_the_base(self) -> None:
        roi = AreaRoi(1, 5.0, 6.0, 20.0)
        self.assertEqual(geometry_at(roi, 3), RoiGeometry(5.0, 6.0, 20.0, None, None))
        self.assertIs(resolved_at(roi, 3), roi)  # nothing to resolve: the very same object

    def test_persistent_means_this_cube_and_every_later_cube(self) -> None:
        timeline = RoiTimeline().with_change(5, SCOPE_PERSISTENT, _g(50.0))
        roi = AreaRoi(1, 5.0, 10.0, 20.0, timeline=timeline)
        self.assertEqual(geometry_at(roi, 4).center_x, 5.0)  # before: the base
        for cube in (5, 6, 100):
            self.assertEqual(geometry_at(roi, cube).center_x, 50.0)

    def test_a_later_persistent_change_supersedes_after_its_cube(self) -> None:
        timeline = RoiTimeline().with_change(5, SCOPE_PERSISTENT, _g(50.0)).with_change(8, SCOPE_PERSISTENT, _g(80.0))
        roi = AreaRoi(1, 5.0, 10.0, 20.0, timeline=timeline)
        self.assertEqual([geometry_at(roi, c).center_x for c in (4, 5, 7, 8, 9)], [5.0, 50.0, 50.0, 80.0, 80.0])

    def test_individual_wins_only_on_its_own_cube(self) -> None:
        timeline = RoiTimeline().with_change(5, SCOPE_PERSISTENT, _g(50.0)).with_change(7, SCOPE_INDIVIDUAL, _g(70.0))
        roi = AreaRoi(1, 5.0, 10.0, 20.0, timeline=timeline)
        self.assertEqual([geometry_at(roi, c).center_x for c in (6, 7, 8)], [50.0, 70.0, 50.0])

    def test_writing_the_same_cube_and_scope_replaces(self) -> None:
        timeline = RoiTimeline().with_change(5, SCOPE_PERSISTENT, _g(50.0)).with_change(5, SCOPE_PERSISTENT, _g(55.0))
        self.assertEqual(len(timeline.persistent), 1)
        self.assertEqual(timeline.geometry_at(5).center_x, 55.0)
        with self.assertRaises(ValueError):
            timeline.with_change(5, "nope", _g(1.0))

    def test_without_cube_removes_both_scopes_and_empties_cleanly(self) -> None:
        timeline = RoiTimeline().with_change(5, SCOPE_PERSISTENT, _g(50.0)).with_change(5, SCOPE_INDIVIDUAL, _g(51.0))
        self.assertEqual(timeline.cubes(), (5,))
        self.assertFalse(timeline.without_cube(5))

    def test_resolved_copy_has_the_geometry_and_no_timeline(self) -> None:
        roi = AreaRoi(1, 5.0, 10.0, 20.0, label="a", timeline=RoiTimeline().with_change(2, SCOPE_PERSISTENT, _g(50.0, 30.0)))
        at = resolved_at(roi, 3)
        self.assertEqual((at.center_x, at.sample_diameter_px, at.label, at.area_roi_id), (50.0, 30.0, "a", 1))
        self.assertIsNone(at.timeline)
        self.assertEqual(roi.center_x, 5.0)  # the stored ROI is untouched


class ToolboxTimelineTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.toolbox.add_roi(10.0, 10.0, sample_diameter_px=20.0)
        self.toolbox.add_roi(40.0, 10.0, sample_diameter_px=20.0)
        undo_manager.clear()

    def tearDown(self) -> None:
        undo_manager.clear()

    def x(self, roi_id: int, cube: int | None) -> float:
        return self.toolbox.geometry_at(roi_id, cube).center_x

    def test_no_cube_edits_the_base_like_before(self) -> None:
        self.toolbox.move_roi(1, 12.0, 11.0)
        self.assertEqual([self.x(1, c) for c in (0, 7, None)], [12.0, 12.0, 12.0])
        self.assertFalse(self.toolbox.has_timeline())

    def test_persistent_edit_at_a_cube_leaves_earlier_cubes_alone(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        self.assertEqual([self.x(1, c) for c in (0, 4, 5, 9)], [10.0, 10.0, 15.0, 15.0])
        self.assertEqual(self.x(2, 5), 40.0)  # other ROIs untouched
        self.assertTrue(self.toolbox.has_timeline())
        self.assertEqual(self.toolbox.timeline_cubes(), (5,))

    def test_individual_edit_changes_one_cube_only(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5, scope=SCOPE_INDIVIDUAL)
        self.assertEqual([self.x(1, c) for c in (4, 5, 6)], [10.0, 15.0, 10.0])

    def test_translate_starts_from_the_geometry_valid_at_that_cube(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        self.toolbox.translate_rois([1], 2.0, 0.0, cube=7)  # at cube 7 the ROI is at 15 (from cube 5), so 17
        self.assertEqual([self.x(1, c) for c in (5, 6, 7, 8)], [15.0, 15.0, 17.0, 17.0])

    def test_resize_and_reset_at_a_cube(self) -> None:
        self.toolbox.resize_rois([1, 2], sample_diameter_px=30.0, reference_inner_diameter_px=40.0, reference_outer_diameter_px=50.0, cube=3)
        self.assertEqual(self.toolbox.geometry_at(1, 2).sample_diameter_px, 20.0)
        g = self.toolbox.geometry_at(2, 3)
        self.assertEqual((g.sample_diameter_px, g.reference_inner_diameter_px, g.reference_outer_diameter_px), (30.0, 40.0, 50.0))
        self.toolbox.reset_roi_diameters([1], cube=6, scope=SCOPE_INDIVIDUAL)
        self.assertEqual(self.toolbox.geometry_at(1, 6).reference_inner_diameter_px, None)
        self.assertEqual(self.toolbox.geometry_at(1, 7).reference_inner_diameter_px, 40.0)
        with self.assertRaises(ValueError):
            self.toolbox.resize_rois([1], reference_inner_diameter_px=60.0, cube=3)  # inner >= outer (50)

    def test_each_edit_is_one_undo_step_and_restores_the_timeline(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        self.toolbox.move_roi(1, 18.0, 10.0, cube=8)
        undo_manager.undo()
        self.assertEqual([self.x(1, c) for c in (5, 8)], [15.0, 15.0])
        undo_manager.undo()
        self.assertEqual([self.x(1, c) for c in (5, 8)], [10.0, 10.0])
        self.assertFalse(self.toolbox.has_timeline())
        self.assertFalse(undo_manager.can_undo)
        undo_manager.redo()
        undo_manager.redo()
        self.assertEqual([self.x(1, c) for c in (4, 5, 8)], [10.0, 15.0, 18.0])

    def test_a_no_op_edit_pushes_nothing(self) -> None:
        self.toolbox.move_roi(1, 10.0, 10.0, cube=5)  # already there
        self.assertFalse(undo_manager.can_undo)
        self.assertFalse(self.toolbox.has_timeline())

    def test_apply_to_all_cubes_collapses_the_timeline(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        self.toolbox.move_roi(1, 18.0, 10.0, cube=8, scope=SCOPE_INDIVIDUAL)
        self.toolbox.apply_to_all_cubes([1], 5)
        self.assertEqual([self.x(1, c) for c in (0, 5, 8, 99)], [15.0, 15.0, 15.0, 15.0])
        self.assertFalse(self.toolbox.has_timeline())
        undo_manager.undo()
        self.assertEqual([self.x(1, c) for c in (0, 5, 8, 9)], [10.0, 15.0, 18.0, 15.0])

    def test_remove_cube_edit(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        self.toolbox.move_roi(1, 18.0, 10.0, cube=8)
        self.toolbox.remove_cube_edit([1], 8)
        self.assertEqual([self.x(1, c) for c in (5, 8, 9)], [15.0, 15.0, 15.0])
        self.toolbox.remove_cube_edit([1], 5)
        self.assertFalse(self.toolbox.has_timeline())
        self.toolbox.remove_cube_edit([1], 5)  # nothing there: a no-op
        undo_manager.undo()
        undo_manager.undo()
        self.assertEqual([self.x(1, c) for c in (5, 8)], [15.0, 18.0])

    def test_rois_at_resolves_for_drawing_and_analysis(self) -> None:
        self.assertEqual(self.toolbox.rois_at(3), self.toolbox.rois())  # no timeline: the stored ROIs themselves
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        at4, at5 = self.toolbox.rois_at(4), self.toolbox.rois_at(5)
        self.assertEqual([r.center_x for r in at4], [10.0, 40.0])
        self.assertEqual([r.center_x for r in at5], [15.0, 40.0])
        self.assertTrue(all(r.timeline is None for r in at5))
        self.assertEqual(self.toolbox.roi_by_id(1).center_x, 10.0)  # the stored base is untouched

    def test_display_positions_follow_the_cube(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        identity = np.eye(3)
        self.assertEqual(self.toolbox.display_position(1, (4, 600.0), identity)[0], 10.0)
        self.assertEqual(self.toolbox.display_position(1, (5, 600.0), identity)[0], 15.0)
        np.testing.assert_allclose(self.toolbox.display_positions((6, 600.0), identity)[:, 0], [15.0, 40.0])

    def test_the_timeline_follows_a_rotation_remap(self) -> None:
        self.toolbox.move_roi(1, 15.0, 10.0, cube=5)
        self.toolbox.remap_all(lambda x, y: (x + 100.0, y), lambda mask: mask)
        self.assertEqual([self.x(1, c) for c in (0, 5)], [110.0, 115.0])
        undo_manager.undo()
        self.assertEqual([self.x(1, c) for c in (0, 5)], [10.0, 15.0])

    def test_timeline_survives_delete_and_renumbering_of_other_rois(self) -> None:
        self.toolbox.move_roi(2, 45.0, 10.0, cube=5)
        self.toolbox.delete_rois((1,))
        self.assertEqual(self.toolbox.roi_by_id(1).area_roi_id, 1)  # ROI 2 became 1
        self.assertEqual([self.x(1, c) for c in (4, 5)], [40.0, 45.0])

    def test_refine_rois_writes_a_timeline_change(self) -> None:
        self.toolbox.refine_rois({1: (11.0, 10.5, 22.0, 30.0, 40.0)}, cube=4)
        g = self.toolbox.geometry_at(1, 4)
        self.assertEqual((g.center_x, g.sample_diameter_px, g.reference_inner_diameter_px), (11.0, 22.0, 30.0))
        self.assertEqual(self.toolbox.geometry_at(1, 3).sample_diameter_px, 20.0)
        undo_manager.undo()
        self.assertFalse(self.toolbox.has_timeline())
        self.assertFalse(undo_manager.can_undo)


class TimelineStorageTest(unittest.TestCase):
    def test_round_trip_through_the_session_encoding(self) -> None:
        timeline = (
            RoiTimeline()
            .with_change(5, SCOPE_PERSISTENT, RoiGeometry(50.0, 11.0, 22.0, 30.0, 40.0))
            .with_change(7, SCOPE_INDIVIDUAL, RoiGeometry(70.0, 12.0, 24.0, None, None))
        )
        roi = AreaRoi(3, 5.0, 10.0, 20.0, label="x", timeline=timeline)
        payload = json.loads(json.dumps(_encode_area_roi(roi)))  # real JSON: nothing but plain values may be in it
        (decoded,) = _decode_area_rois([payload])
        self.assertEqual(decoded.timeline, timeline)
        self.assertEqual(decoded.center_x, 5.0)

    def test_a_roi_without_a_timeline_looks_as_before(self) -> None:
        payload = _encode_area_roi(AreaRoi(1, 5.0, 10.0, 20.0))
        self.assertIsNone(payload["timeline"])
        (decoded,) = _decode_area_rois([json.loads(json.dumps(payload))])
        self.assertIsNone(decoded.timeline)

    def test_an_old_session_without_the_key_loads(self) -> None:
        payload = json.loads(json.dumps(_encode_area_roi(AreaRoi(1, 5.0, 10.0, 20.0))))
        del payload["timeline"]
        (decoded,) = _decode_area_rois([payload])
        self.assertIsNone(decoded.timeline)

    def test_malformed_changes_are_skipped_not_fatal(self) -> None:
        payload = json.loads(json.dumps(_encode_area_roi(AreaRoi(1, 5.0, 10.0, 20.0))))
        payload["timeline"] = {
            "persistent": [{"cube": 2, "x": 1.0, "y": 2.0, "sample_diameter_px": 20.0}, {"cube": "bad"}, 7],
            "individual": "nonsense",
        }
        (decoded,) = _decode_area_rois([payload])
        self.assertEqual([c.cube for c in decoded.timeline.persistent], [2])
        self.assertEqual(decoded.timeline.individual, ())


if __name__ == "__main__":
    unittest.main()
