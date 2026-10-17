"""From scan pointers to masks: `transient_masks.py` and `ContourMask.grown` / `paste_or` (2026-10-17).

Pure numpy, no Qt. What is worth pinning: only particles that are still standing are masked, the contour is grown by the user's N
pixels on its small box (clipped at the image border), and what is in effect at a frame is merged in, never overwritten.
Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask import transient_masks as tm
    from lspr_imaging_app.image_tools.mask.transient_detection import (
        KIND_ARTEFACT,
        KIND_GLOBAL,
        KIND_PARTICLE,
        ContourMask,
        TransientEvent,
    )
    from lspr_imaging_app.image_tools.mask.transient_events_state import EventRecord
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

SHAPE = (200, 300)


def _disk(radius: int = 5) -> np.ndarray:
    y, x = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    return (x**2 + y**2) <= radius**2


def _record(event_id, frame, x0=50, y0=60, kind=KIND_PARTICLE, dismissed=False, contour=True, inferred=False) -> EventRecord:
    mask = _disk()
    event = TransientEvent(
        frame=frame, kind=kind, area_px=int(mask.sum()), centroid_xy=(x0 + 5.0, y0 + 5.0), bbox_xyxy=(x0, y0, x0 + 11, y0 + 11),
        depth=0.8, z=100.0, contour=ContourMask(x0=x0, y0=y0, mask=mask) if contour else None, inferred=inferred,
    )
    return EventRecord(id=event_id, event=event, dismissed=dismissed)


class SelectionTests(unittest.TestCase):
    def test_only_standing_particles_with_a_contour_are_masked(self) -> None:
        records = [
            _record(1, (3, 500.0)),
            _record(2, (3, 510.0), dismissed=True),
            _record(3, (3, 520.0), kind=KIND_GLOBAL, contour=False),
            _record(4, (3, 530.0), kind=KIND_ARTEFACT),
            _record(5, (3, 540.0), contour=False),
            _record(6, (3, 550.0), inferred=True),
        ]
        self.assertEqual([r.id for r in tm.maskable(records)], [1, 6])
        self.assertEqual([r.id for r in tm.maskable(records, include_inferred=False)], [1])

    def test_records_are_grouped_by_frame_in_acquisition_order(self) -> None:
        records = [_record(1, (4, 500.0)), _record(2, (3, 600.0)), _record(3, (3, 500.0)), _record(4, (3, 600.0), x0=150)]
        grouped = tm.group_by_frame(records)
        self.assertEqual(list(grouped), [(3, 500.0), (3, 600.0), (4, 500.0)])
        self.assertEqual([r.id for r in grouped[(3, 600.0)]], [2, 4])


class GrowingTests(unittest.TestCase):
    def test_growing_adds_a_ring_of_the_given_width(self) -> None:
        contour = ContourMask(x0=100, y0=80, mask=_disk(5))
        grown = contour.grown(3, SHAPE)
        self.assertEqual((grown.x0, grown.y0), (97, 77))
        self.assertEqual(grown.mask.shape, (17, 17))
        self.assertAlmostEqual(grown.mask.sum(), np.pi * 8**2, delta=20)
        full = contour.to_array(SHAPE, dilate_px=3)
        np.testing.assert_array_equal(full[77:94, 97:114], grown.mask)
        self.assertEqual(int(full.sum()), int(grown.mask.sum()))
        self.assertTrue(np.all(full[contour.to_array(SHAPE)]))  # the contour is inside the grown one

    def test_no_growth_is_the_contour_itself(self) -> None:
        contour = ContourMask(x0=100, y0=80, mask=_disk(5))
        self.assertIs(contour.grown(0, SHAPE), contour)
        self.assertEqual(int(contour.to_array(SHAPE).sum()), int(_disk(5).sum()))

    def test_growing_is_clipped_at_the_image_border(self) -> None:
        corner = ContourMask(x0=0, y0=0, mask=_disk(5))
        grown = corner.grown(4, SHAPE)
        self.assertEqual((grown.x0, grown.y0), (0, 0))
        full = corner.to_array(SHAPE, dilate_px=4)
        self.assertGreater(int(full.sum()), int(_disk(5).sum()))
        far = ContourMask(x0=SHAPE[1] - 8, y0=SHAPE[0] - 8, mask=_disk(5)[:8, :8])
        self.assertEqual(far.to_array(SHAPE, dilate_px=5).shape, SHAPE)  # nothing is written outside the plane

    def test_pasting_ors_into_the_plane_in_place(self) -> None:
        plane = np.zeros(SHAPE, dtype=bool)
        plane[60:70, 50:60] = True
        ContourMask(x0=55, y0=62, mask=_disk(5)).paste_or(plane)
        self.assertTrue(plane[60, 50])  # what was there stays
        self.assertTrue(plane[67, 60])  # and the contour is added


class FrameMaskTests(unittest.TestCase):
    def test_the_contours_of_a_frame_are_merged_onto_what_is_in_effect_without_changing_it(self) -> None:
        base = np.zeros(SHAPE, dtype=bool)
        base[0:20, 0:20] = True
        before = base.copy()
        records = [_record(1, (3, 500.0), x0=50, y0=60), _record(2, (3, 500.0), x0=200, y0=100)]
        plane = tm.frame_mask(records, SHAPE, 2, base)
        np.testing.assert_array_equal(base, before)
        self.assertTrue(plane[10, 10])  # the base
        self.assertTrue(plane[65, 55])  # the first contour
        self.assertTrue(plane[105, 205])  # the second contour
        self.assertGreater(int(plane.sum()), int(base.sum()) + 2 * int(_disk().sum()))  # grown by 2 px

    def test_without_a_base_the_mask_is_the_contours_alone(self) -> None:
        plane = tm.frame_mask([_record(1, (3, 500.0))], SHAPE, 0)
        self.assertEqual(int(plane.sum()), int(_disk().sum()))

    def test_a_base_of_another_shape_is_ignored(self) -> None:
        plane = tm.frame_mask([_record(1, (3, 500.0))], SHAPE, 0, np.ones((10, 10), dtype=bool))
        self.assertEqual(plane.shape, SHAPE)
        self.assertEqual(int(plane.sum()), int(_disk().sum()))

    def test_build_masks_makes_one_individual_mask_per_frame_that_has_a_maskable_event(self) -> None:
        persistent = np.zeros(SHAPE, dtype=bool)
        persistent[150:160, 250:260] = True
        records = [
            _record(1, (3, 500.0)),
            _record(2, (3, 510.0), dismissed=True),
            _record(3, (4, 500.0), kind=KIND_ARTEFACT),
            _record(4, (5, 520.0)),
        ]
        asked: list = []

        def resolve(frame):
            asked.append(frame)
            return persistent

        built = tm.build_masks(records, SHAPE, 1, resolve)
        self.assertEqual([(frame, scope) for frame, scope, _ in built], [((3, 500.0), "individual"), ((5, 520.0), "individual")])
        self.assertEqual(asked, [(3, 500.0), (5, 520.0)])  # the base is only asked for frames that get a mask
        for _frame, _scope, plane in built:
            self.assertTrue(np.all(plane[persistent]))
            self.assertTrue(plane[65, 55])


if __name__ == "__main__":
    unittest.main()
