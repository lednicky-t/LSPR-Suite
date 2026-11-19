"""A rectangle's reference border, as geometry: where its two edges are, which one a point is on, and which way dragging it moves
(apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md, phase 3; the maintainer found on 2026-11-18 that a rectangle's ring could not be resized like a circle's).

The checks are against the outlines the overlay really draws (`overlay_geometry.rectangle_points` at the border's two offsets): a point on the drawn inner edge is
the inner zone, on the drawn outer edge the outer zone, and the signed distance of any point of an outline is that outline's offset, in every frame (identity,
chromatic, rotated, stretched). Also: the vectorised handle calculation the drawing uses equals the one-rectangle form the hit test uses, and the cursor directions.

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
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Rectangle
    from lspr_imaging_app.roi.overlay_geometry import rectangle_points
    from lspr_imaging_app.roi.shape_hits import (
        HANDLE_NAMES,
        ZONE_BORDER_INNER,
        ZONE_BORDER_OUTER,
        ZONE_CORNER,
        ZONE_HEIGHT,
        ZONE_WIDTH,
        FrameMap,
        rectangle_border_zone,
        rectangle_handle_arrays,
        rectangle_handle_points,
        rectangle_signed_distance,
        rectangle_zone_direction,
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
BORDER = Border(4.0, 6.0)
TOLERANCE = 4.0


def _display_centre(affine) -> np.ndarray:
    return affine[:, :2] @ CENTRE_REF + affine[:, 2]


def _outline(rect: Rectangle, affine, offset: float) -> np.ndarray:
    """The drawn outline of the rectangle (offset 0) or of its border edge at ``offset`` from the rectangle's edge: ``(n, 2)`` display points."""
    xs, ys = rectangle_points(
        _display_centre(affine)[None, :], np.array([rect.width_px]), np.array([rect.height_px]), np.array([rect.angle_deg]), affine, offset_px=offset
    )
    return np.column_stack((xs[0], ys[0]))


class HandleArraysTest(unittest.TestCase):
    def test_many_rectangles_at_once_equal_the_one_rectangle_form(self) -> None:
        """The drawing paints from the arrays, the hit test asks the one-rectangle form: they must be the same numbers."""
        for affine in FRAMES:
            centres = np.array([_display_centre(affine) + (7.0 * k, -3.0 * k) for k in range(len(RECTANGLES))])
            arrays = rectangle_handle_arrays(
                [r.width_px for r in RECTANGLES], [r.height_px for r in RECTANGLES], [r.angle_deg for r in RECTANGLES], centres, affine[:, :2], 22.0
            )
            self.assertEqual(arrays.shape, (len(RECTANGLES), len(HANDLE_NAMES), 2))
            for row, rect in enumerate(RECTANGLES):
                single = rectangle_handle_points(rect, centres[row], affine[:, :2], 22.0)
                for column, name in enumerate(HANDLE_NAMES):
                    np.testing.assert_allclose(arrays[row, column], single[name], rtol=0, atol=1e-12, err_msg=f"{name} of {rect}")

    def test_no_rectangles_is_an_empty_array(self) -> None:
        self.assertEqual(rectangle_handle_arrays([], [], [], np.empty((0, 2)), np.eye(2), 22.0).shape, (0, len(HANDLE_NAMES), 2))


class SignedDistanceTest(unittest.TestCase):
    def test_known_points_of_an_unturned_rectangle(self) -> None:
        rect, frame, centre = Rectangle(40.0, 20.0, 0.0), FrameMap.of(IDENTITY), (100.0, 80.0)
        distance = lambda x, y: rectangle_signed_distance(rect, centre, frame, x, y)  # noqa: E731
        self.assertAlmostEqual(distance(130.0, 80.0), 10.0)  # 10 beyond the right edge (x = 120)
        self.assertAlmostEqual(distance(100.0, 65.0), 5.0)  # 5 above the top edge (y = 70)
        self.assertAlmostEqual(distance(125.0, 95.0), float(np.hypot(5.0, 5.0)))  # diagonal off the corner: the rounded outline
        self.assertAlmostEqual(distance(120.0, 80.0), 0.0)  # on the edge
        self.assertAlmostEqual(distance(100.0, 80.0), -10.0)  # the middle: the shorter half side
        self.assertAlmostEqual(distance(115.0, 80.0), -5.0)  # 5 inside the right edge

    def test_every_point_of_a_drawn_outline_is_at_its_offset_in_every_frame(self) -> None:
        for affine in FRAMES:
            frame, centre = FrameMap.of(affine), _display_centre(affine)
            for rect in RECTANGLES:
                for offset in (0.0, 4.0, 10.0, -3.0):
                    for x, y in _outline(rect, affine, offset)[:-1]:
                        got = rectangle_signed_distance(rect, centre, frame, float(x), float(y))
                        self.assertAlmostEqual(got, offset, places=6, msg=f"offset {offset} of {rect} under {affine.tolist()}")

    def test_a_degenerate_frame_has_no_distance(self) -> None:
        flat = FrameMap.of(np.array([[1.0, 2.0, 0.0], [2.0, 4.0, 0.0]]))
        self.assertIsNone(rectangle_signed_distance(Rectangle(20.0, 10.0, 0.0), (0.0, 0.0), flat, 5.0, 5.0))


class BorderZoneTest(unittest.TestCase):
    def zone(self, rect, affine, x, y, border=BORDER, tolerance=TOLERANCE):
        return rectangle_border_zone(rect, _display_centre(affine), FrameMap.of(affine), x, y, border, tolerance)

    def test_a_point_on_each_drawn_edge_of_the_band_is_that_zone(self) -> None:
        for affine in FRAMES:
            for rect in RECTANGLES:
                for offset, expected in ((BORDER.gap_px, ZONE_BORDER_INNER), (BORDER.gap_px + BORDER.thickness_px, ZONE_BORDER_OUTER)):
                    for x, y in _outline(rect, affine, offset)[:-1:3]:
                        found = self.zone(rect, affine, float(x), float(y))
                        self.assertIsNotNone(found, f"{expected} of {rect} under {affine.tolist()}")
                        self.assertEqual(found[0], expected)
                        self.assertAlmostEqual(found[1], 0.0, places=6)  # right on the edge

    def test_the_two_distances_say_how_far_from_the_band_edge_and_from_the_rectangle(self) -> None:
        rect, affine = Rectangle(40.0, 20.0, 0.0), IDENTITY
        zone, from_band, from_rectangle = self.zone(rect, affine, 100.0, 70.0 - 5.0)  # 5 above the top edge, the band's inner edge is at 4
        self.assertEqual(zone, ZONE_BORDER_INNER)
        self.assertAlmostEqual(from_band, 1.0)
        self.assertAlmostEqual(from_rectangle, 5.0)

    def test_the_zone_never_reaches_further_than_half_the_thickness(self) -> None:
        """So the two edges of a thin band do not swallow each other (as for a circle's ring)."""
        rect, affine, thin = Rectangle(40.0, 20.0, 0.0), IDENTITY, Border(4.0, 2.0)  # edges at 4 and 6, half thickness 1
        self.assertEqual(self.zone(rect, affine, 100.0, 70.0 - 4.9, thin, tolerance=50.0)[0], ZONE_BORDER_INNER)
        self.assertEqual(self.zone(rect, affine, 100.0, 70.0 - 5.1, thin, tolerance=50.0)[0], ZONE_BORDER_OUTER)
        self.assertIsNone(self.zone(rect, affine, 100.0, 70.0 - 7.2, thin, tolerance=50.0))  # 1.2 from the outer edge: beyond the half thickness

    def test_the_screen_tolerance_limits_the_zone_of_a_thick_band(self) -> None:
        rect, affine = Rectangle(40.0, 20.0, 0.0), IDENTITY
        thick = Border(10.0, 30.0)  # the half thickness (15) is far more than the tolerance
        self.assertIsNotNone(self.zone(rect, affine, 100.0, 70.0 - 10.0 - 3.9, thick))
        self.assertIsNone(self.zone(rect, affine, 100.0, 70.0 - 10.0 - 4.1, thick))

    def test_a_border_that_starts_inside_the_shape_has_its_edge_inside(self) -> None:
        rect, affine, inside = Rectangle(40.0, 20.0, 0.0), IDENTITY, Border(-6.0, 4.0)  # from 6 inside the edge to 2 inside
        found = self.zone(rect, affine, 100.0 + 14.0, 80.0, inside)  # 6 inside the right edge (x = 120)
        self.assertEqual(found[0], ZONE_BORDER_INNER)
        self.assertAlmostEqual(found[1], 0.0)

    def test_no_border_no_thickness_or_a_degenerate_frame_has_no_zone(self) -> None:
        rect = Rectangle(40.0, 20.0, 0.0)
        self.assertIsNone(self.zone(rect, IDENTITY, 100.0, 60.0, NO_BORDER))
        self.assertIsNone(self.zone(rect, IDENTITY, 100.0, 60.0, Border(4.0, 0.0)))
        flat = FrameMap.of(np.array([[1.0, 2.0, 0.0], [2.0, 4.0, 0.0]]))
        self.assertIsNone(rectangle_border_zone(rect, (100.0, 80.0), flat, 100.0, 60.0, BORDER, TOLERANCE))

    def test_far_from_the_band_and_in_the_middle_are_not_on_it(self) -> None:
        rect = Rectangle(40.0, 20.0, 0.0)
        self.assertIsNone(self.zone(rect, IDENTITY, 100.0, 80.0))
        self.assertIsNone(self.zone(rect, IDENTITY, 300.0, 300.0))


class ZoneDirectionTest(unittest.TestCase):
    def direction(self, zone, rect, affine, x, y):
        return np.array(rectangle_zone_direction(zone, rect, _display_centre(affine), FrameMap.of(affine), x, y))

    @staticmethod
    def line(vector) -> np.ndarray:
        """The unit direction of a double arrow: its sense does not matter."""
        unit = vector / np.hypot(*vector)
        return unit if (unit[0] > 1e-9 or (abs(unit[0]) <= 1e-9 and unit[1] > 0)) else -unit

    def test_width_and_height_edges_follow_the_rectangles_own_axes_on_the_screen(self) -> None:
        for affine in FRAMES:
            for rect in RECTANGLES:
                angle = np.radians(rect.angle_deg)
                axis_u = affine[:, :2] @ np.array([np.cos(angle), np.sin(angle)])
                axis_v = affine[:, :2] @ np.array([-np.sin(angle), np.cos(angle)])
                np.testing.assert_allclose(self.line(self.direction(ZONE_WIDTH, rect, affine, 0.0, 0.0)), self.line(axis_u), atol=1e-9)
                np.testing.assert_allclose(self.line(self.direction(ZONE_HEIGHT, rect, affine, 0.0, 0.0)), self.line(axis_v), atol=1e-9)

    def test_a_border_edge_points_along_the_outward_normal_where_the_pointer_is(self) -> None:
        rect, affine = Rectangle(40.0, 20.0, 0.0), IDENTITY
        above = self.direction(ZONE_BORDER_INNER, rect, affine, 100.0, 60.0)  # over the top edge: vertical
        beside = self.direction(ZONE_BORDER_OUTER, rect, affine, 135.0, 80.0)  # beside the right edge: horizontal
        np.testing.assert_allclose(self.line(above), [0.0, 1.0], atol=1e-9)
        np.testing.assert_allclose(self.line(beside), [1.0, 0.0], atol=1e-9)
        diagonal = self.direction(ZONE_BORDER_OUTER, rect, affine, 130.0, 100.0)  # off the corner (120, 90): along the line from the corner to the pointer
        np.testing.assert_allclose(self.line(diagonal), self.line(np.array([10.0, 10.0])), atol=1e-9)
        lopsided = self.direction(ZONE_BORDER_OUTER, rect, affine, 130.0, 95.0)  # (10, 5) from that corner
        np.testing.assert_allclose(self.line(lopsided), self.line(np.array([10.0, 5.0])), atol=1e-9)

    def test_a_corner_follows_the_diagonal_it_lies_on(self) -> None:
        rect, affine = Rectangle(40.0, 20.0, 0.0), IDENTITY
        ne = self.direction(ZONE_CORNER, rect, affine, 120.0, 70.0)  # the north-east corner: up and right on screen = the (+, -) diagonal
        np.testing.assert_allclose(self.line(ne), self.line(np.array([20.0, -10.0])), atol=1e-9)

    def test_a_degenerate_frame_gives_a_harmless_default(self) -> None:
        flat = FrameMap.of(np.array([[1.0, 2.0, 0.0], [2.0, 4.0, 0.0]]))
        self.assertEqual(rectangle_zone_direction(ZONE_BORDER_INNER, Rectangle(20.0, 10.0, 0.0), (0.0, 0.0), flat, 5.0, 5.0), (1.0, 0.0))


if __name__ == "__main__":
    unittest.main()
