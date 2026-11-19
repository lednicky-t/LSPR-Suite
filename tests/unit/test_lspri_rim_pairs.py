"""Rim pair <-> linked pair: `RoiToolbox.rims_to_references` and `add_rim` (apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md, "Rim pairs, linked pairs, the Type column").

A *rim pair* is a sample measured against its own built-in rim; a *linked pair* is a sample linked to a reference ROI. Turning the first into the second makes the rim's pixels
a pixel-map reference ROI linked to the sample (the sample's own rim stays, dormant). One undo step each; a refusal changes nothing.

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
    from lspr_imaging_app.domain.roi_links import SOURCE_BUILTIN, SOURCE_LINKED
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
        new_ids = self.toolbox.rims_to_references([self.circle, self.rectangle, self.blobs], SHAPE)
        for sample_id, new_id in zip((self.circle, self.rectangle, self.blobs), new_ids):
            got = rz.rasterize_sample(_roi_of(self.toolbox, new_id), SHAPE, IDENTITY)
            np.testing.assert_array_equal(got, self._rim_minus_sample(sample_id, IDENTITY), f"ROI {sample_id}")
            self.assertGreater(int(got.sum()), 100)

    def test_under_a_chromatic_shift_only_the_edge_pixels_differ(self) -> None:
        """The map is warped (nearest neighbour), the rim is measured: measured 2026-11-19 on 1200 x 1000 frames, about 3 % of the rim's pixels differ for a ~2.5 px shift,
        about 8 % for a ~10 px one, a pixel in on one side for a pixel out on the other. Pinned at 10 %."""
        new_ids = self.toolbox.rims_to_references([self.circle, self.rectangle], SHAPE)
        for sample_id, new_id in zip((self.circle, self.rectangle), new_ids):
            rim = self._rim_minus_sample(sample_id, CHROMATIC)
            got = rz.rasterize_sample(_roi_of(self.toolbox, new_id), SHAPE, CHROMATIC) & ~rz.rasterize_sample(_roi_of(self.toolbox, sample_id), SHAPE, CHROMATIC)
            differing = int((rim ^ got).sum())
            self.assertLess(differing, 0.10 * rim.sum(), f"ROI {sample_id}: {differing} of {int(rim.sum())} pixels differ")
            self.assertGreater(int((rim & got).sum()), 0.9 * rim.sum())

    def test_the_samples_own_rim_is_kept_dormant_and_comes_back_when_unlinked(self) -> None:
        own = _roi_of(self.toolbox, self.circle).border
        (new_id,) = self.toolbox.rims_to_references([self.circle], SHAPE)
        self.assertEqual(_roi_of(self.toolbox, self.circle).border, own)  # the rim itself is untouched
        self.toolbox.unlink_references([self.circle])
        self.assertEqual(self.toolbox.reference_source(self.circle), SOURCE_BUILTIN)
        self.assertIn(new_id, [r.area_roi_id for r in self.toolbox.rois()])  # the new reference stays, nobody uses it

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

    def test_the_conversion_makes_a_map_that_cannot_be_moved(self) -> None:
        """A map is its pixels (plan 2.4): the new reference is refused by `move_refusal` like every map; its sample can still be moved."""
        (new_id,) = self.toolbox.rims_to_references([self.circle], SHAPE)
        self.assertIsNotNone(self.toolbox.move_refusal([new_id]))
        self.assertIsNone(self.toolbox.move_refusal([self.circle]))


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


def _crop(plane: np.ndarray):
    return rz.crop_mask(plane)


def _roi_of(toolbox: RoiToolbox, roi_id: int):
    return next(roi for roi in toolbox.rois() if roi.area_roi_id == roi_id)


if __name__ == "__main__":
    unittest.main()
