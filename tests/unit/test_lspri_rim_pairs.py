"""Rim pair <-> linked pair: `RoiToolbox.rims_to_references` and `add_rim` (apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md, "Rim pairs, linked pairs, the Type column").

A *rim pair* is a sample measured against its own built-in rim; a *linked pair* is a sample linked to a reference ROI. Turning the first into the second makes the rim's pixels
(where the sample is on the cube on screen) a pixel-map reference ROI linked to the sample, and the sample a rim-less ROI (`turn_rim_off`: on every cube, the rim's offset and thickness
kept for `add_rim`). One undo step each; a refusal changes nothing.

No Qt event loop, no files, small synthetic images.
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
    from lspr_imaging_app.domain.roi_links import SOURCE_BUILTIN, SOURCE_LINKED, SOURCE_NONE
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape, Rectangle
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

HEIGHT, WIDTH = 120, 160
SHAPE = (HEIGHT, WIDTH)
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])
YY, XX = np.mgrid[0:HEIGHT, 0:WIDTH]


def _disc(cx: float, cy: float, radius: float) -> np.ndarray:
    return (XX - cx) ** 2 + (YY - cy) ** 2 <= radius**2


class RimsToReferencesTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.circle = self.toolbox.add_roi(40.3, 50.6)  # a circle with the shared default rim (4 + 4 px)
        self.rectangle = self.toolbox.add_shaped_roi(100.0, 40.0, Rectangle(24.0, 12.0, 30.0), border=Border(3.0, 4.0))
        self.blobs = self.toolbox.add_shaped_roi(70.0, 90.0, MaskShape(_crop(_disc(60.0, 90.0, 5.0) | _disc(80.0, 90.0, 5.0))), border=Border(2.0, 3.0))
        self.geometry_events, self.link_events = [], []
        self.toolbox.geometry_changed.connect(self.geometry_events.append)
        self.toolbox.links_changed.connect(self.link_events.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _rim_minus_sample(self, roi_id: int, affine: np.ndarray) -> np.ndarray:
        roi = _roi_of(self.toolbox, roi_id)
        return rz.rasterize_reference(roi, SHAPE, affine, default_border=self.toolbox.detection_settings().default_border()) & ~rz.rasterize_sample(roi, SHAPE, affine)

    def test_each_rim_becomes_one_pixel_map_reference_linked_to_its_sample(self) -> None:
        new_ids = self.toolbox.rims_to_references([self.circle, self.rectangle, self.blobs], SHAPE)
        self.assertEqual(len(new_ids), 3)
        for sample_id, new_id in zip((self.circle, self.rectangle, self.blobs), new_ids):
            new = _roi_of(self.toolbox, new_id)
            self.assertEqual(new.role, "reference")
            self.assertIsInstance(new.shape, MaskShape)
            self.assertEqual(self.toolbox.references_of(sample_id), (new_id,))
            self.assertEqual(self.toolbox.reference_source(sample_id), SOURCE_LINKED)

    def test_at_the_reference_wavelength_the_map_is_exactly_the_rim(self) -> None:
        """Identity affine: the converted reference reads the very pixels the rim read (the sample's own pixels left out), for a circle, a rectangle and a two-blob map."""
        ids = (self.circle, self.rectangle, self.blobs)
        rims = {sample_id: self._rim_minus_sample(sample_id, IDENTITY) for sample_id in ids}  # before: the conversion switches the sample's rim off
        new_ids = self.toolbox.rims_to_references(list(ids), SHAPE)
        for sample_id, new_id in zip(ids, new_ids):
            got = rz.rasterize_sample(_roi_of(self.toolbox, new_id), SHAPE, IDENTITY)
            np.testing.assert_array_equal(got, rims[sample_id], f"ROI {sample_id}")
            self.assertGreater(int(got.sum()), 100)

    def test_under_a_chromatic_shift_only_the_edge_pixels_differ(self) -> None:
        """The map is warped (nearest neighbour), the rim is measured: measured 2026-11-19 on 1200 x 1000 frames, about 3 % of the rim's pixels differ for a ~2.5 px shift,
        about 8 % for a ~10 px one, a pixel in on one side for a pixel out on the other. Pinned at 10 %."""
        ids = (self.circle, self.rectangle)
        rims = {sample_id: self._rim_minus_sample(sample_id, CHROMATIC) for sample_id in ids}  # before: the conversion switches the sample's rim off
        new_ids = self.toolbox.rims_to_references(list(ids), SHAPE)
        for sample_id, new_id in zip(ids, new_ids):
            rim = rims[sample_id]
            got = rz.rasterize_sample(_roi_of(self.toolbox, new_id), SHAPE, CHROMATIC) & ~rz.rasterize_sample(_roi_of(self.toolbox, sample_id), SHAPE, CHROMATIC)
            differing = int((rim ^ got).sum())
            self.assertLess(differing, 0.10 * rim.sum(), f"ROI {sample_id}: {differing} of {int(rim.sum())} pixels differ")
            self.assertGreater(int((rim & got).sum()), 0.9 * rim.sum())

    def test_the_sample_is_rim_less_afterwards_its_rim_size_is_remembered_and_add_rim_brings_it_back(self) -> None:
        """The sample does not keep a rim next to its reference (it used to, dormant): the rim is switched off, its offset and thickness kept."""
        own = _roi_of(self.toolbox, self.circle).border  # None: it follows the shared default rim (4 + 4 px)
        self.assertIsNone(own)
        (new_id,) = self.toolbox.rims_to_references([self.circle], SHAPE)
        after = _roi_of(self.toolbox, self.circle).border
        self.assertEqual(after, Border(4.0, 4.0, enabled=False), "off, with the numbers it had")
        self.assertIsInstance(_roi_of(self.toolbox, self.circle).shape, Circle)  # the shape is untouched
        self.assertEqual(self.toolbox.reference_source(self.circle), SOURCE_LINKED)
        self.toolbox.unlink_references([self.circle])
        self.assertEqual(self.toolbox.reference_source(self.circle), SOURCE_NONE, "unlinked, the sample has no reference: its rim is off")
        self.assertIn(new_id, [r.area_roi_id for r in self.toolbox.rois()])  # the new reference stays, nobody uses it
        self.toolbox.add_rim([self.circle])
        self.assertEqual(_roi_of(self.toolbox, self.circle).border, Border(4.0, 4.0), "the same rim comes back")
        self.assertEqual(self.toolbox.reference_source(self.circle), SOURCE_BUILTIN)

    def test_a_rectangle_and_a_map_keep_their_own_rim_numbers_while_it_is_off(self) -> None:
        self.toolbox.rims_to_references([self.rectangle, self.blobs], SHAPE)
        self.assertEqual(_roi_of(self.toolbox, self.rectangle).border, Border(3.0, 4.0, enabled=False))
        self.assertEqual(_roi_of(self.toolbox, self.blobs).border, Border(2.0, 3.0, enabled=False))
        self.assertIsInstance(_roi_of(self.toolbox, self.blobs).shape, MaskShape)
        self.toolbox.add_rim([self.rectangle, self.blobs])
        self.assertEqual(_roi_of(self.toolbox, self.rectangle).border, Border(3.0, 4.0))
        self.assertEqual(_roi_of(self.toolbox, self.blobs).border, Border(2.0, 3.0))

    def test_a_move_made_on_a_later_cube_is_where_the_reference_is_taken(self) -> None:
        """The bug of 2026-11-19: dragging a sample on a cube after the first one is a timeline change, not the base position, and the reference was built from the base position
        (where the sample used to be). With ``cube`` the rim is the one the sample has there."""
        base = _roi_of(self.toolbox, self.circle)
        self.toolbox.move_roi(self.circle, 90.0, 80.0, cube=5)  # persistent from cube 5 on
        self.assertEqual((base.center_x, base.center_y), (40.3, 50.6), "the base position did not move: the move is on the timeline")
        moved = self.toolbox.rois_at(5)[0]
        self.assertEqual((moved.center_x, moved.center_y), (90.0, 80.0))
        default = self.toolbox.detection_settings().default_border()
        rim_there = rz.rasterize_reference(moved, SHAPE, IDENTITY, default_border=default) & ~rz.rasterize_sample(moved, SHAPE, IDENTITY)
        rim_before = rz.rasterize_reference(base, SHAPE, IDENTITY, default_border=default) & ~rz.rasterize_sample(base, SHAPE, IDENTITY)
        (new_id,) = self.toolbox.rims_to_references([self.circle], SHAPE, cube=5)
        got = rz.rasterize_sample(_roi_of(self.toolbox, new_id), SHAPE, IDENTITY)
        np.testing.assert_array_equal(got, rim_there)
        self.assertFalse((got & rim_before).any(), "nothing of it lies on the rim where the sample was before the move")

    def test_the_rim_goes_off_on_every_cube_and_one_undo_puts_everything_back(self) -> None:
        self.toolbox.move_roi(self.circle, 90.0, 80.0, cube=5)
        self.toolbox.resize_roi(self.circle, sample_diameter_px=14.0, cube=9)  # a second timeline change, with the rim as it was
        undo_manager.clear()
        (new_id,) = self.toolbox.rims_to_references([self.circle], SHAPE, cube=5)
        roi = _roi_of(self.toolbox, self.circle)
        self.assertFalse(any(self.toolbox._rim_states(roi)), "base and both timeline changes: rim-less")
        for cube in (None, 0, 5, 9, 40):
            self.assertFalse(self.toolbox.rois_at(cube)[0].border.enabled, f"cube {cube}")
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo, "one undo step for the reference, the link and the rim")
        roi = _roi_of(self.toolbox, self.circle)
        self.assertTrue(all(self.toolbox._rim_states(roi)))
        self.assertEqual(self.toolbox.references_of(self.circle), ())
        self.assertNotIn(new_id, [r.area_roi_id for r in self.toolbox.rois()])
        undo_manager.redo()
        self.assertFalse(any(self.toolbox._rim_states(_roi_of(self.toolbox, self.circle))))
        self.assertEqual(self.toolbox.references_of(self.circle), (new_id,))

    def test_it_is_one_undo_step_and_redo_brings_the_same_reference_back(self) -> None:
        before = len(self.toolbox.rois())
        undo_manager.clear()
        (new_id,) = self.toolbox.rims_to_references([self.circle], SHAPE)
        self.assertEqual(len(self.toolbox.rois()), before + 1)
        pixels = _roi_of(self.toolbox, new_id).shape.mask.mask.copy()
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo, "one undo step, not two")
        self.assertEqual(len(self.toolbox.rois()), before)
        self.assertEqual(self.toolbox.references_of(self.circle), ())
        undo_manager.redo()
        self.assertEqual(self.toolbox.references_of(self.circle), (new_id,))
        np.testing.assert_array_equal(_roi_of(self.toolbox, new_id).shape.mask.mask, pixels)

    def test_the_signals_name_the_new_rois_and_the_linked_samples(self) -> None:
        new_ids = self.toolbox.rims_to_references([self.circle, self.blobs], SHAPE)
        added = [e for e in self.geometry_events if e.reason == "added"][-1]
        self.assertEqual(added.roi_ids, tuple(sorted(new_ids)))
        self.assertEqual(self.link_events[-1].sample_ids, tuple(sorted((self.circle, self.blobs))))
        self.assertEqual(self.link_events[-1].reason, "linked")

    def test_a_refusal_changes_nothing(self) -> None:
        (ref_id,) = self.toolbox.rims_to_references([self.circle], SHAPE)
        count, events = len(self.toolbox.rois()), len(self.geometry_events)
        for ids, text in (([ref_id], "reference"), ([self.circle], "linked"), ([self.rectangle, self.circle], "linked")):
            with self.assertRaises(ValueError) as caught:
                self.toolbox.rims_to_references(ids, SHAPE)
            self.assertIn(text, str(caught.exception))
        self.assertEqual(len(self.toolbox.rois()), count)
        self.assertEqual(len(self.geometry_events), events)
        self.assertEqual(self.toolbox.references_of(self.rectangle), ())

    def test_a_sample_without_a_rim_and_a_rim_outside_the_image_are_refused(self) -> None:
        bare = self.toolbox.add_shaped_roi(30.0, 20.0, Circle(10.0), border=NO_BORDER)
        with self.assertRaises(ValueError) as caught:
            self.toolbox.rims_to_references([bare], SHAPE)
        self.assertIn("no rim", str(caught.exception))
        outside = self.toolbox.add_roi(500.0, 500.0)
        with self.assertRaises(ValueError) as caught:
            self.toolbox.rims_to_references([outside], SHAPE)
        self.assertIn("no pixel inside the image", str(caught.exception))

    def test_the_conversion_makes_a_map_that_moves_by_whole_pixels_on_its_own(self) -> None:
        """A map is its pixels: the new reference moves by whole pixels (`RoiToolbox._at_position`), alone, and its sample stays where it is."""
        (new_id,) = self.toolbox.rims_to_references([self.circle], SHAPE)
        start = self.toolbox.roi_by_id(new_id).shape.mask
        circle_at = (self.toolbox.roi_by_id(self.circle).center_x, self.toolbox.roi_by_id(self.circle).center_y)
        self.toolbox.translate_rois([new_id], 2.0, -1.0)
        moved = self.toolbox.roi_by_id(new_id).shape.mask
        self.assertEqual((moved.x0, moved.y0), (start.x0 + 2, start.y0 - 1))
        self.assertTrue(np.array_equal(moved.mask, start.mask))
        self.assertEqual((self.toolbox.roi_by_id(self.circle).center_x, self.toolbox.roi_by_id(self.circle).center_y), circle_at)


class AddRimTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.free = self.toolbox.add_shaped_roi(30.0, 30.0, MaskShape(_crop(_disc(30.0, 30.0, 5.0))), border=None, role="reference")
        self.used = self.toolbox.add_shaped_roi(60.0, 30.0, MaskShape(_crop(_disc(60.0, 30.0, 5.0))), border=None, role="reference")
        self.bare = self.toolbox.add_shaped_roi(90.0, 30.0, Circle(10.0), border=NO_BORDER)
        self.sample = self.toolbox.add_roi(120.0, 30.0)
        self.toolbox.link_references([self.sample], [self.used])

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_what_can_get_a_rim(self) -> None:
        self.assertTrue(self.toolbox.can_add_rim(self.free))  # a reference nobody uses
        self.assertFalse(self.toolbox.can_add_rim(self.used))  # it serves a sample
        self.assertTrue(self.toolbox.can_add_rim(self.bare))  # a sample whose rim is off
        self.assertFalse(self.toolbox.can_add_rim(self.sample))  # it has one already

    def test_a_free_reference_becomes_a_sample_with_the_default_rim(self) -> None:
        self.toolbox.add_rim([self.free])
        roi = _roi_of(self.toolbox, self.free)
        self.assertEqual(roi.role, "sample")
        self.assertEqual(self.toolbox.reference_source(self.free), SOURCE_BUILTIN)
        default = self.toolbox.detection_settings().default_border()
        self.assertEqual((default.gap_px, default.thickness_px), (4.0, 4.0))
        self.assertTrue(rz.rasterize_reference(roi, SHAPE, IDENTITY, default_border=default).any())

    def test_a_sample_whose_rim_is_off_gets_the_default_size(self) -> None:
        self.toolbox.add_rim([self.bare])
        border = _roi_of(self.toolbox, self.bare).border
        self.assertTrue(border.enabled)
        self.assertEqual((border.gap_px, border.thickness_px), (4.0, 4.0))

    def test_it_is_one_undo_step_for_the_role_and_the_rim(self) -> None:
        self.toolbox.set_borders({self.free: NO_BORDER})  # a reference with a rim of its own that is off: both parts have to change
        undo_manager.clear()
        self.toolbox.add_rim([self.free, self.bare])
        self.assertEqual(_roi_of(self.toolbox, self.free).role, "sample")
        self.assertTrue(_roi_of(self.toolbox, self.free).border.enabled)
        self.assertTrue(_roi_of(self.toolbox, self.bare).border.enabled)
        undo_manager.undo()  # one step undoes everything
        self.assertEqual(_roi_of(self.toolbox, self.free).role, "reference")
        self.assertFalse(_roi_of(self.toolbox, self.free).border.enabled)
        self.assertFalse(_roi_of(self.toolbox, self.bare).border.enabled)

    def test_a_reference_that_serves_a_sample_is_left_alone_and_nothing_to_do_pushes_no_step(self) -> None:
        undo_manager.clear()
        self.toolbox.add_rim([self.used, self.sample])
        self.assertEqual(_roi_of(self.toolbox, self.used).role, "reference")
        self.assertEqual(self.toolbox.references_of(self.sample), (self.used,))
        self.assertFalse(undo_manager.can_undo, "nothing to change, so no undo step")

    def test_an_unknown_id_is_a_caller_error(self) -> None:
        with self.assertRaises(KeyError):
            self.toolbox.add_rim([999])


class TurnRimOffTest(unittest.TestCase):
    """`RoiToolbox.turn_rim_off`: a sample becomes a rim-less ROI, whatever its shape, with the rim's numbers kept (maintainer, 2026-11-19)."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.circle = self.toolbox.add_roi(30.0, 30.0)  # follows the shared default rim
        self.rectangle = self.toolbox.add_shaped_roi(60.0, 30.0, Rectangle(24.0, 12.0, 30.0), border=Border(3.0, 4.0))
        self.blobs = self.toolbox.add_shaped_roi(90.0, 30.0, MaskShape(_crop(_disc(90.0, 30.0, 5.0))), border=Border(2.0, 3.0))
        self.bare = self.toolbox.add_shaped_roi(120.0, 30.0, Circle(10.0), border=NO_BORDER)
        self.reference = self.toolbox.add_shaped_roi(30.0, 70.0, MaskShape(_crop(_disc(30.0, 70.0, 5.0))), border=None, role="reference")
        self.linked = self.toolbox.add_shaped_roi(60.0, 70.0, Circle(12.0), border=Border(5.0, 6.0))
        self.toolbox.link_references([self.linked], [self.reference])
        self.events = []
        self.toolbox.geometry_changed.connect(self.events.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_what_can_have_its_rim_turned_off(self) -> None:
        for roi_id in (self.circle, self.rectangle, self.blobs, self.linked):
            self.assertTrue(self.toolbox.can_turn_rim_off(roi_id), roi_id)  # a linked sample still has the rim it had (an older file, a link made by hand)
        self.assertFalse(self.toolbox.can_turn_rim_off(self.bare), "no rim to turn off")
        self.assertFalse(self.toolbox.can_turn_rim_off(self.reference), "a reference has no rim")

    def test_the_shape_stays_and_the_numbers_are_kept_for_every_shape(self) -> None:
        shapes = {i: _roi_of(self.toolbox, i).shape for i in (self.circle, self.rectangle, self.blobs)}
        self.toolbox.turn_rim_off([self.circle, self.rectangle, self.blobs])
        self.assertEqual(_roi_of(self.toolbox, self.circle).border, Border(4.0, 4.0, enabled=False))  # the shared default, as it was
        self.assertEqual(_roi_of(self.toolbox, self.rectangle).border, Border(3.0, 4.0, enabled=False))
        self.assertEqual(_roi_of(self.toolbox, self.blobs).border, Border(2.0, 3.0, enabled=False))
        for roi_id, shape in shapes.items():
            self.assertIs(_roi_of(self.toolbox, roi_id).shape, shape)
            self.assertEqual(self.toolbox.reference_source(roi_id), SOURCE_NONE)
        default = self.toolbox.detection_settings().default_border()
        self.assertFalse(rz.rasterize_reference(_roi_of(self.toolbox, self.rectangle), SHAPE, IDENTITY, default_border=default).any(), "no rim pixels any more")

    def test_a_linked_sample_loses_its_rim_but_keeps_its_link(self) -> None:
        self.toolbox.turn_rim_off([self.linked])
        self.assertEqual(_roi_of(self.toolbox, self.linked).border, Border(5.0, 6.0, enabled=False))
        self.assertEqual(self.toolbox.references_of(self.linked), (self.reference,))
        self.assertEqual(self.toolbox.reference_source(self.linked), SOURCE_LINKED)

    def test_it_is_off_on_every_cube(self) -> None:
        self.toolbox.move_roi(self.rectangle, 70.0, 35.0, cube=4)
        self.toolbox.resize_roi(self.circle, sample_diameter_px=16.0, cube=6, scope="individual")
        self.toolbox.turn_rim_off([self.rectangle, self.circle])
        for roi_id in (self.rectangle, self.circle):
            self.assertFalse(any(self.toolbox._rim_states(_roi_of(self.toolbox, roi_id))), roi_id)
        for cube in (None, 0, 4, 6, 20):
            self.assertFalse(any(roi.border.enabled for roi in self.toolbox.rois_at(cube) if roi.area_roi_id in (self.rectangle, self.circle)), f"cube {cube}")
        moved = self.toolbox.rois_at(4)[self.rectangle - 1]  # the move and the size are untouched
        self.assertEqual((moved.center_x, moved.center_y), (70.0, 35.0))
        self.assertEqual(self.toolbox.rois_at(6)[self.circle - 1].shape.diameter_px, 16.0)
        self.toolbox.add_rim([self.rectangle, self.circle])  # ... and Add rim undoes it on every cube, with the numbers it had
        for cube in (None, 0, 4, 6, 20):
            by_id = {roi.area_roi_id: roi for roi in self.toolbox.rois_at(cube)}
            self.assertEqual(by_id[self.rectangle].border, Border(3.0, 4.0), f"cube {cube}")
            self.assertTrue(by_id[self.circle].border is None or by_id[self.circle].border.enabled, f"cube {cube}")

    def test_one_undo_step_one_signal_and_nothing_to_do_pushes_nothing(self) -> None:
        undo_manager.clear()
        self.toolbox.turn_rim_off([self.circle, self.rectangle, self.bare, self.reference])
        self.assertEqual(len(self.events), 1)
        self.assertEqual((self.events[0].roi_ids, self.events[0].reason), ((self.circle, self.rectangle), "resized"))
        undo_manager.undo()
        self.assertFalse(undo_manager.can_undo, "one step")
        self.assertIsNone(_roi_of(self.toolbox, self.circle).border)
        self.assertEqual(_roi_of(self.toolbox, self.rectangle).border, Border(3.0, 4.0))
        undo_manager.redo()
        self.assertFalse(_roi_of(self.toolbox, self.rectangle).border.enabled)
        undo_manager.clear()
        self.events.clear()
        self.toolbox.turn_rim_off([self.bare, self.reference])  # nothing to turn off
        self.assertFalse(undo_manager.can_undo)
        self.assertEqual(self.events, [])

    def test_the_rim_edits_say_to_add_a_rim_first_and_work_again_afterwards(self) -> None:
        self.toolbox.turn_rim_off([self.rectangle])
        with self.assertRaises(ValueError) as caught:
            self.toolbox.set_rim([self.rectangle], thickness_px=6.0)
        self.assertIn("Add rim", str(caught.exception))
        self.toolbox.add_rim([self.rectangle])
        self.toolbox.set_rim([self.rectangle], thickness_px=6.0)
        self.assertEqual(_roi_of(self.toolbox, self.rectangle).border, Border(3.0, 6.0))

    def test_a_rim_that_never_had_a_size_comes_back_with_the_default_size(self) -> None:
        self.toolbox.add_rim([self.bare])
        self.assertEqual(_roi_of(self.toolbox, self.bare).border, Border(4.0, 4.0))

    def test_the_off_rim_and_its_numbers_survive_saving_and_loading_the_session(self) -> None:
        """No format change: the session already stores gap, thickness and the switch together, for the base geometry and for every timeline change."""
        import json

        from lspr_imaging_app.storage.session import _decode_area_rois, _encode_area_roi

        self.toolbox.move_roi(self.rectangle, 70.0, 35.0, cube=4)
        self.toolbox.turn_rim_off([self.circle, self.rectangle])
        rois = [_roi_of(self.toolbox, i) for i in (self.circle, self.rectangle)]
        loaded = _decode_area_rois(json.loads(json.dumps([_encode_area_roi(roi) for roi in rois])))
        for before, after in zip(rois, loaded):
            self.assertEqual(after.border, before.border)
            self.assertEqual(self.toolbox._rim_borders(after), self.toolbox._rim_borders(before))
        self.assertEqual(loaded[0].border, Border(4.0, 4.0, enabled=False))
        self.assertEqual(loaded[1].timeline.persistent[0].geometry.border, Border(3.0, 4.0, enabled=False))

    def test_an_unknown_id_is_a_caller_error(self) -> None:
        with self.assertRaises(KeyError):
            self.toolbox.turn_rim_off([999])


class OnlyWhatChangedIsScannedAgainTest(unittest.TestCase):
    """The scan reads every ROI on its own and decides which reference a sample is measured against afterwards (`analysis/reference_pairing.py`), so a conversion must make stale only
    the ROIs whose own pixels it changes: the sample (it reads no rim any more) and the new reference (never scanned). Every other ROI keeps its cell key, so its stored numbers stay valid."""

    def test_only_the_converted_sample_and_the_new_reference_go_stale(self) -> None:
        from lspr_imaging_app.analysis.frame_reduce import build_roi_set, local_roi_keys

        undo_manager.clear()
        toolbox = RoiToolbox()
        circles = [toolbox.add_roi(30.0 + 60.0 * i, 30.0) for i in range(4)]
        rectangle = toolbox.add_shaped_roi(30.0, 90.0, Rectangle(24.0, 12.0, 30.0), border=Border(3.0, 4.0))
        blobs = toolbox.add_shaped_roi(120.0, 90.0, MaskShape(_crop(_disc(110.0, 90.0, 5.0) | _disc(130.0, 90.0, 5.0))), border=Border(2.0, 3.0))
        default = toolbox.detection_settings().default_border()

        def keys() -> dict[int, bytes]:
            rois = toolbox.rois_at(None)
            return dict(zip((roi.area_roi_id for roi in rois), local_roi_keys(build_roi_set(rois, default))))

        before = keys()
        toolbox.rims_to_references([circles[1], blobs], SHAPE)
        after = keys()
        self.assertEqual({i for i in before if before[i] != after[i]}, {circles[1], blobs}, "the two converted samples, nobody else")
        self.assertEqual(set(after) - set(before), {len(before) + 1, len(before) + 2}, "the two new references are new: nothing stored, so they are scanned")
        undo_manager.undo()
        self.assertEqual(keys(), before, "undo: every key as it was, so every stored number is valid again")
        undo_manager.clear()
        toolbox.turn_rim_off([rectangle])
        self.assertEqual({i for i in before if before[i] != keys()[i]}, {rectangle})


def _crop(plane: np.ndarray):
    return rz.crop_mask(plane)


def _roi_of(toolbox: RoiToolbox, roi_id: int):
    return next(roi for roi in toolbox.rois() if roi.area_roi_id == roi_id)


if __name__ == "__main__":
    unittest.main()
