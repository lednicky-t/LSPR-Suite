"""The handles of a selected rectangle ROI, pure geometry (phase 3 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

`shape_hits.rectangle_handle_points` (where the handles are drawn), `rectangle_handle_zone` (which one a point is on) and `rectangle_size_at` (what dragging one to a
point sets). The three must agree: a point on a drawn handle is that handle's zone, and dragging a handle to where another rectangle's handle is gives exactly
that rectangle's size or angle. Checked for unturned and turned rectangles, the identity and three chromatic / rotated / stretched frames.

No Qt, no files.
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
    from lspr_imaging_app.domain.roi_shapes import Rectangle
    from lspr_imaging_app.roi.overlay_geometry import rectangle_points
    from lspr_imaging_app.roi.shape_hits import (
        ZONE_CORNER,
        ZONE_HEIGHT,
        ZONE_ROTATE,
        ZONE_WIDTH,
        FrameMap,
        rectangle_handle_points,
        rectangle_handle_zone,
        rectangle_size_at,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])
ROTATED = np.array([[0.99819, -0.08733, 3.7], [0.08733, 0.99819, -2.9]])
STRETCHED = np.array([[1.08, -0.04, 5.0], [0.03, 0.93, -4.0]])
FRAMES = (IDENTITY, CHROMATIC, ROTATED, STRETCHED)
RECTANGLES = (Rectangle(40.0, 20.0, 0.0), Rectangle(40.0, 20.0, 90.0), Rectangle(36.0, 24.0, 33.0), Rectangle(30.0, 50.0, -120.0))
CENTRE_REF = np.array([100.0, 80.0])
TOLERANCE, KNOB = 4.0, 22.0


def _display_centre(affine) -> np.ndarray:
    return affine[:, :2] @ CENTRE_REF + affine[:, 2]


class HandlePointsTest(unittest.TestCase):
    def test_the_corners_are_the_corners_of_the_drawn_outline(self) -> None:
        for affine in FRAMES:
            for rect in RECTANGLES:
                centre = _display_centre(affine)
                points = rectangle_handle_points(rect, centre, affine[:, :2], KNOB)
                xs, ys = rectangle_points(centre[None, :], np.array([rect.width_px]), np.array([rect.height_px]), np.array([rect.angle_deg]), affine)
                outline = np.column_stack((xs[0], ys[0]))
                for name in ("ne", "nw", "se", "sw"):
                    nearest = float(np.min(np.hypot(*(outline - np.array(points[name])).T)))
                    self.assertLess(nearest, 1e-9, f"{name} of {rect} under {affine.tolist()}")

    def test_an_unturned_rectangle_in_the_identity_frame(self) -> None:
        points = rectangle_handle_points(Rectangle(40.0, 20.0, 0.0), (100.0, 80.0), np.eye(2), KNOB)
        self.assertEqual(points["e"], (120.0, 80.0))
        self.assertEqual(points["w"], (80.0, 80.0))
        self.assertEqual(points["s"], (100.0, 90.0))
        self.assertEqual(points["n"], (100.0, 70.0))
        self.assertEqual(points["ne"], (120.0, 70.0))
        self.assertEqual(points["knob"], (100.0, 70.0 - KNOB))  # straight out beyond the top edge, up on the screen (y points down)

    def test_the_edge_middles_sit_halfway_along_the_sides(self) -> None:
        for affine in FRAMES:
            for rect in RECTANGLES:
                p = rectangle_handle_points(rect, _display_centre(affine), affine[:, :2], KNOB)
                for edge, (a, b) in {"n": ("ne", "nw"), "s": ("se", "sw"), "e": ("ne", "se"), "w": ("nw", "sw")}.items():
                    self.assertTrue(np.allclose(np.array(p[edge]), (np.array(p[a]) + np.array(p[b])) / 2.0))

    def test_the_knob_is_the_same_distance_out_whatever_the_frame(self) -> None:
        for affine in FRAMES:
            rect = Rectangle(40.0, 20.0, 33.0)
            p = rectangle_handle_points(rect, _display_centre(affine), affine[:, :2], KNOB)
            self.assertAlmostEqual(float(np.hypot(*(np.array(p["knob"]) - np.array(p["n"])))), KNOB, places=9)


class HandleZoneTest(unittest.TestCase):
    def _zone(self, rect, affine, point):
        return rectangle_handle_zone(rect, _display_centre(affine), FrameMap.of(affine), point[0], point[1], TOLERANCE, KNOB)

    def test_every_drawn_handle_answers_with_its_zone(self) -> None:
        for affine in FRAMES:
            for rect in RECTANGLES:
                points = rectangle_handle_points(rect, _display_centre(affine), affine[:, :2], KNOB)
                self.assertEqual(self._zone(rect, affine, points["knob"]), ZONE_ROTATE)
                for corner in ("ne", "nw", "se", "sw"):
                    self.assertEqual(self._zone(rect, affine, points[corner]), ZONE_CORNER, f"{corner} {rect}")
                for edge in ("e", "w"):
                    self.assertEqual(self._zone(rect, affine, points[edge]), ZONE_WIDTH, f"{edge} {rect}")
                for edge in ("n", "s"):
                    self.assertEqual(self._zone(rect, affine, points[edge]), ZONE_HEIGHT, f"{edge} {rect}")

    def test_the_inside_and_the_far_outside_are_not_handles(self) -> None:
        for affine in FRAMES:
            for rect in RECTANGLES:
                centre = _display_centre(affine)
                self.assertIsNone(self._zone(rect, affine, centre))
                self.assertIsNone(self._zone(rect, affine, centre + np.array([300.0, 300.0])))

    def test_a_point_just_beyond_the_tolerance_misses_the_edge(self) -> None:
        rect = Rectangle(40.0, 20.0, 0.0)
        centre = _display_centre(IDENTITY)
        self.assertEqual(self._zone(rect, IDENTITY, centre + np.array([20.0 + TOLERANCE - 0.1, 0.0])), ZONE_WIDTH)
        self.assertIsNone(self._zone(rect, IDENTITY, centre + np.array([20.0 + TOLERANCE + 0.1, 0.0])))

    def test_a_small_rectangle_keeps_a_middle_to_grab_it_by(self) -> None:
        rect = Rectangle(6.0, 6.0, 0.0)
        self.assertIsNone(self._zone(rect, IDENTITY, _display_centre(IDENTITY)))

    def test_a_degenerate_frame_has_no_handles(self) -> None:
        flat = np.array([[1.0, 2.0, 0.0], [2.0, 4.0, 0.0]])
        self.assertIsNone(rectangle_handle_zone(Rectangle(20.0, 10.0, 0.0), (0.0, 0.0), FrameMap.of(flat), 10.0, 0.0, TOLERANCE, KNOB))


class SizeAtTest(unittest.TestCase):
    def test_dragging_an_edge_to_where_another_rectangles_edge_is_gives_that_size(self) -> None:
        for affine in FRAMES:
            for rect in RECTANGLES:
                target = Rectangle(rect.width_px + 11.0, rect.height_px + 7.0, rect.angle_deg)
                moved = rectangle_handle_points(target, _display_centre(affine), affine[:, :2], KNOB)
                frame, centre = FrameMap.of(affine), _display_centre(affine)
                width = rectangle_size_at(ZONE_WIDTH, rect.angle_deg, centre, frame, *moved["e"])
                height = rectangle_size_at(ZONE_HEIGHT, rect.angle_deg, centre, frame, *moved["s"])
                corner = rectangle_size_at(ZONE_CORNER, rect.angle_deg, centre, frame, *moved["se"])
                self.assertEqual(list(width), ["width_px"])
                self.assertAlmostEqual(width["width_px"], target.width_px, places=7)
                self.assertEqual(list(height), ["height_px"])
                self.assertAlmostEqual(height["height_px"], target.height_px, places=7)
                self.assertAlmostEqual(corner["width_px"], target.width_px, places=7)
                self.assertAlmostEqual(corner["height_px"], target.height_px, places=7)

    def test_dragging_the_knob_to_where_another_angles_knob_is_gives_that_angle(self) -> None:
        for affine in FRAMES:
            for start in (0.0, 33.0, -120.0):
                for target in (0.0, 45.0, 90.0, 170.0, -60.0):
                    knob = rectangle_handle_points(Rectangle(40.0, 20.0, target), _display_centre(affine), affine[:, :2], KNOB)["knob"]
                    got = rectangle_size_at(ZONE_ROTATE, start, _display_centre(affine), FrameMap.of(affine), *knob)["angle_deg"]
                    self.assertAlmostEqual((got - target + 180.0) % 360.0 - 180.0, 0.0, places=7, msg=f"{target} under {affine.tolist()}")

    def test_the_other_side_of_the_centre_gives_the_same_size_the_rectangle_is_symmetric(self) -> None:
        frame = FrameMap.of(IDENTITY)
        right = rectangle_size_at(ZONE_WIDTH, 0.0, (100.0, 80.0), frame, 125.0, 80.0)
        left = rectangle_size_at(ZONE_WIDTH, 0.0, (100.0, 80.0), frame, 75.0, 80.0)
        self.assertEqual(right, left)
        self.assertEqual(right["width_px"], 50.0)


if __name__ == "__main__":
    unittest.main()
