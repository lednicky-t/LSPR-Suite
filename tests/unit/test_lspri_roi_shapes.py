"""ROI shapes and borders (phase 2.1 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

A ROI has a shape (circle, rectangle, mask) and a border (gap + thickness from the shape's edge). This file checks the new behaviour: the
exact ring radii of a circle's border, the pixels of a rectangle and of a border against a brute-force test written here, a scan with
shapes, the session file (new form, and a session written before this change) and the toolbox commands. That circles give exactly the
pixels and numbers they always gave is pinned by `test_lspri_scan_circle_golden.py`.

No Qt, no files (but the old-session fixture), small synthetic images.
"""

from __future__ import annotations

import json
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import frame_reduce as fr
    from lspr_imaging_app.domain.roi_model import AreaRoi, AreaRoiDetectionSettings, RoiGeometry, RoiTimeline
    from lspr_imaging_app.domain.roi_shapes import (
        NO_BORDER,
        Border,
        Circle,
        MaskShape,
        Rectangle,
        reach_radius,
        ring_diameters,
        ring_radii,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.storage import session as sess
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
from scipy.spatial.distance import cdist  # noqa: E402

from tests.unit import test_lspri_scan_circle_golden as golden  # noqa: E402

HEIGHT, WIDTH = 100, 140
SHAPE = (HEIGHT, WIDTH)
IDENTITY = golden.IDENTITY
CHROMATIC = golden.CHROMATIC
XX, YY = np.meshgrid(np.arange(WIDTH, dtype=np.float64), np.arange(HEIGHT, dtype=np.float64))  # pixel centres, [row, col]
DEFAULT_BORDER = Border(4.0, 4.0)


def _rect(w: float, h: float, angle: float, cx: float = 60.0, cy: float = 50.0, border: Border | None = NO_BORDER, roi_id: int = 1) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=cx, center_y=cy, shape=Rectangle(w, h, angle), border=border)


def _rect_distance(x, y, cx, cy, w, h, angle_deg) -> np.ndarray:
    """Signed distance from a rotated rectangle's edge (outside positive), written independently of the code under test."""
    a = np.radians(angle_deg)
    u = (x - cx) * np.cos(a) + (y - cy) * np.sin(a)
    v = (y - cy) * np.cos(a) - (x - cx) * np.sin(a)
    qx, qy = np.abs(u) - w / 2.0, np.abs(v) - h / 2.0
    return np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) + np.minimum(np.maximum(qx, qy), 0.0)


class BorderRadiiTests(unittest.TestCase):
    def test_ring_radii_reproduce_the_old_diameter_description_bit_for_bit(self) -> None:
        rng = np.random.default_rng(7)
        for _ in range(3000):
            d = rng.uniform(4.0, 60.0)
            inner = d * rng.uniform(0.7, 2.0)
            outer = inner * rng.uniform(1.0, 2.0)
            border = Border.from_ring_diameters(d, inner, outer)
            got_inner, got_outer = ring_radii(Circle(d), border)
            want_inner = max(inner, 0.0) / 2.0
            want_outer = max(outer / 2.0, want_inner)
            self.assertEqual((got_inner, got_outer), (want_inner, want_outer), f"d={d!r} inner={inner!r} outer={outer!r}")

    def test_the_edge_cases_of_a_ring(self) -> None:
        self.assertEqual(ring_radii(Circle(24.0), Border.from_ring_diameters(24.0, 16.0, 30.0)), (8.0, 15.0))  # starts inside the sample
        self.assertEqual(ring_radii(Circle(20.0), Border.from_ring_diameters(20.0, 0.0, 30.0)), (0.0, 15.0))  # a disc
        self.assertEqual(ring_radii(Circle(14.0), Border.from_ring_diameters(14.0, 30.0, 20.0)), (15.0, 15.0))  # outer < inner: clamped
        self.assertEqual(ring_radii(Circle(10.0), Border(-20.0, 3.0)), (0.0, 3.0))  # a gap deeper than the radius never goes below 0

    def test_the_border_follows_the_shape(self) -> None:
        self.assertEqual(ring_diameters(Circle(20.0), Border(4.0, 4.0)), (28.0, 36.0))  # the ring the app always started with
        self.assertEqual(ring_diameters(Circle(30.0), Border(4.0, 4.0)), (38.0, 46.0))

    def test_a_negative_thickness_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Border(1.0, -0.1)
        with self.assertRaises(ValueError):
            Border(1.0, float("nan"))

    def test_reach_radius(self) -> None:
        self.assertEqual(reach_radius(Circle(20.0), 5.0, 5.0), 10.0)
        self.assertAlmostEqual(reach_radius(Rectangle(6.0, 8.0, 33.0), 5.0, 5.0), 5.0)  # half the diagonal, whatever the angle
        blob = np.zeros((4, 6), dtype=bool)
        blob[:] = True
        far = reach_radius(MaskShape(rz.RoiMask(10, 20, blob)), 10.0, 20.0)  # the far corner of the box [9.5, 15.5] x [19.5, 23.5]
        self.assertAlmostEqual(far, float(np.hypot(5.5, 3.5)))


class RectangleRasterTests(unittest.TestCase):
    def test_an_axis_aligned_rectangle_covers_exactly_the_pixels_inside_it(self) -> None:
        got = rz.rasterize_sample(_rect(10.6, 6.2, 0.0), SHAPE, IDENTITY)
        np.testing.assert_array_equal(got, (np.abs(XX - 60.0) <= 5.3) & (np.abs(YY - 50.0) <= 3.1))

    def test_a_quarter_turn_swaps_width_and_height(self) -> None:
        turned = rz.rasterize_sample(_rect(10.6, 6.2, 90.0), SHAPE, IDENTITY)
        swapped = rz.rasterize_sample(_rect(6.2, 10.6, 0.0), SHAPE, IDENTITY)
        np.testing.assert_array_equal(turned, swapped)

    def test_a_positive_angle_turns_clockwise_on_the_screen(self) -> None:
        got = rz.rasterize_sample(_rect(24.0, 4.0, 45.0), SHAPE, IDENTITY)  # a long bar, its x axis pointing down-right
        self.assertTrue(got[57, 67])  # (x, y) = (cx + 7, cy + 7)
        self.assertFalse(got[43, 67])  # (cx + 7, cy - 7)

    def test_under_a_chromatic_affine_it_is_the_brute_force_inverse_map(self) -> None:
        roi = _rect(18.4, 9.2, 27.0)
        got = rz.rasterize_sample(roi, SHAPE, CHROMATIC)
        inverse = np.linalg.inv(np.vstack((CHROMATIC, [0.0, 0.0, 1.0])))
        sx = inverse[0, 0] * XX + inverse[0, 1] * YY + inverse[0, 2]
        sy = inverse[1, 0] * XX + inverse[1, 1] * YY + inverse[1, 2]
        np.testing.assert_array_equal(got, _rect_distance(sx, sy, 60.0, 50.0, 18.4, 9.2, 27.0) <= 0.0)
        det = abs(np.linalg.det(CHROMATIC[:, :2]))
        self.assertAlmostEqual(got.sum() / (18.4 * 9.2 * det), 1.0, delta=0.05)  # the area follows the affine's stretch

    def test_the_patch_variant_is_the_crop_of_the_full_plane(self) -> None:
        roi = _rect(18.4, 9.2, 27.0)
        full = rz.rasterize_sample(roi, SHAPE, CHROMATIC)
        np.testing.assert_array_equal(rz.rasterize_sample_for_patch(roi, (45, 35), (25, 30), CHROMATIC), full[35:60, 45:75])

    def test_a_rectangle_outside_the_image_is_empty_and_one_at_the_edge_is_clipped(self) -> None:
        self.assertFalse(rz.rasterize_sample(_rect(10.0, 10.0, 0.0, cx=400.0, cy=400.0), SHAPE, IDENTITY).any())
        edge = rz.rasterize_sample(_rect(10.6, 6.2, 0.0, cx=1.0, cy=50.0), SHAPE, IDENTITY)
        np.testing.assert_array_equal(edge, (np.abs(XX - 1.0) <= 5.3) & (np.abs(YY - 50.0) <= 3.1))


class BorderRasterTests(unittest.TestCase):
    def test_a_circles_border_is_the_ring(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=60.0, center_y=50.0, shape=Circle(20.0), border=Border(4.0, 4.0))
        got = rz.rasterize_reference(roi, SHAPE, IDENTITY)
        d2 = (XX - 60.0) ** 2 + (YY - 50.0) ** 2
        np.testing.assert_array_equal(got, (d2 <= 18.0**2) & (d2 >= 14.0**2))

    def test_no_border_and_the_default_border(self) -> None:
        own = AreaRoi(area_roi_id=1, center_x=60.0, center_y=50.0, shape=Circle(20.0), border=NO_BORDER)
        self.assertFalse(rz.rasterize_reference(own, SHAPE, IDENTITY, default_border=DEFAULT_BORDER).any())  # its own "none" beats the default
        follower = AreaRoi(area_roi_id=1, center_x=60.0, center_y=50.0, shape=Circle(20.0))
        self.assertTrue(rz.rasterize_reference(follower, SHAPE, IDENTITY, default_border=DEFAULT_BORDER).any())
        self.assertFalse(rz.rasterize_reference(follower, SHAPE, IDENTITY).any())  # no default given: no border

    def test_a_rectangles_border_is_the_band_at_distance_g_to_g_plus_t_with_rounded_corners(self) -> None:
        for angle in (0.0, 30.0):
            roi = _rect(14.6, 8.2, angle, border=Border(2.0, 3.0))
            got = rz.rasterize_reference(roi, SHAPE, IDENTITY)
            distance = _rect_distance(XX, YY, 60.0, 50.0, 14.6, 8.2, angle)
            np.testing.assert_array_equal(got, (distance >= 2.0) & (distance <= 5.0), err_msg=f"angle {angle}")
            self.assertFalse((got & rz.rasterize_sample(roi, SHAPE, IDENTITY)).any())  # a border with a positive gap lies outside the shape
        corner = _rect(14.6, 8.2, 0.0, border=Border(2.0, 3.0))
        got = rz.rasterize_reference(corner, SHAPE, IDENTITY)
        # the square corner of the band's bounding box (7.3 + 4.5, 4.1 + 4.5) is 6.4 px from the rectangle: outside a band that ends at 5 px
        self.assertFalse(got[int(50 + 4.1 + 4.5), int(60 + 7.3 + 4.5)])

    def test_a_negative_gap_starts_inside_the_shape(self) -> None:
        roi = _rect(20.6, 12.2, 0.0, border=Border(-3.0, 2.0))
        got = rz.rasterize_reference(roi, SHAPE, IDENTITY)
        distance = _rect_distance(XX, YY, 60.0, 50.0, 20.6, 12.2, 0.0)
        np.testing.assert_array_equal(got, (distance >= -3.0) & (distance <= -1.0))
        self.assertFalse((got & ~rz.rasterize_sample(roi, SHAPE, IDENTITY)).any())  # all of it lies inside the shape
        self.assertGreater(got.sum(), 0)

    def test_a_masks_border_is_the_band_around_its_edge(self) -> None:
        disc = (XX - 60.0) ** 2 + (YY - 50.0) ** 2 <= 36.0
        roi = AreaRoi(area_roi_id=1, center_x=60.0, center_y=50.0, shape=MaskShape(rz.crop_mask(disc)), border=Border(2.0, 3.0))
        got = rz.rasterize_reference(roi, SHAPE, IDENTITY)
        points = np.column_stack((XX.ravel(), YY.ravel()))
        inside = disc.ravel()
        signed = np.empty(points.shape[0])
        signed[~inside] = cdist(points[~inside], points[inside]).min(axis=1) - 0.5  # the edge is half a pixel from the pixel centres
        signed[inside] = 0.5 - cdist(points[inside], points[~inside]).min(axis=1)
        np.testing.assert_array_equal(got.ravel(), (signed >= 2.0) & (signed <= 5.0))
        self.assertFalse((got & disc).any())
        self.assertGreater(got.sum(), 0)

    def test_a_mask_sample_is_still_the_mask(self) -> None:
        disc = (XX - 60.0) ** 2 + (YY - 50.0) ** 2 <= 36.0
        roi = AreaRoi(area_roi_id=1, center_x=60.0, center_y=50.0, shape=MaskShape(rz.crop_mask(disc)))
        np.testing.assert_array_equal(rz.rasterize_sample(roi, SHAPE, IDENTITY), disc)

    def test_union_masks_and_the_fractional_coverage_accept_every_shape(self) -> None:
        rois = [
            AreaRoi(area_roi_id=1, center_x=30.0, center_y=30.0, shape=Circle(12.0), border=Border(2.0, 3.0)),
            _rect(14.6, 8.2, 20.0, cx=90.0, cy=60.0, border=Border(2.0, 3.0), roi_id=2),
        ]
        sample, reference = rz.union_roi_masks(rois, SHAPE, CHROMATIC)
        want_sample = np.zeros(SHAPE, dtype=bool)
        want_reference = np.zeros(SHAPE, dtype=bool)
        for roi in rois:
            want_sample |= rz.rasterize_sample(roi, SHAPE, CHROMATIC)
            want_reference |= rz.rasterize_reference(roi, SHAPE, CHROMATIC)
        np.testing.assert_array_equal(sample, want_sample)
        np.testing.assert_array_equal(reference, want_reference)
        coverage = rz.rasterize_fractional(rois[1], "sample", SHAPE, IDENTITY)
        self.assertAlmostEqual(float(coverage.sum()), 14.6 * 8.2, delta=1.0)  # the fractional area is the rectangle's area
        self.assertTrue(rz.rasterize_fractional(rois[1], "reference", SHAPE, IDENTITY, default_border=DEFAULT_BORDER).any())


class ScanWithShapesTests(unittest.TestCase):
    def test_a_rectangle_roi_is_read_through_the_per_roi_path_and_averages_its_own_pixels(self) -> None:
        roi = _rect(14.6, 8.2, 20.0, border=Border(2.0, 3.0))
        rs = fr.build_roi_set([roi], DEFAULT_BORDER)
        self.assertFalse(rs.vectorised[0])
        image = np.random.default_rng(1).normal(1000.0, 40.0, SHAPE).astype(np.float32)
        red = fr.reduce_frame(rs, image, None, CHROMATIC, ("mean",))
        sample = rz.rasterize_sample(roi, SHAPE, CHROMATIC)
        ring = rz.rasterize_reference(roi, SHAPE, CHROMATIC) & ~sample
        self.assertEqual(int(red.n_valid[0, fr.SAMPLE]), int(sample.sum()))
        self.assertEqual(int(red.n_valid[0, fr.REFERENCE]), int(ring.sum()))
        self.assertAlmostEqual(float(red.values["mean"][0, fr.SAMPLE]), float(image[sample].astype(np.float64).mean()), places=9)
        self.assertAlmostEqual(float(red.values["mean"][0, fr.REFERENCE]), float(image[ring].astype(np.float64).mean()), places=9)

    def test_a_circle_is_unchanged_by_a_far_rectangle_and_loses_the_pixels_of_a_near_one(self) -> None:
        circle = AreaRoi(area_roi_id=1, center_x=30.0, center_y=30.0, shape=Circle(20.0))
        far = _rect(10.0, 6.0, 0.0, cx=110.0, cy=80.0, roi_id=2)
        near = _rect(10.0, 6.0, 0.0, cx=48.0, cy=30.0, roi_id=2)  # inside the circle's ring
        alone = fr.build_layout(fr.build_roi_set([circle], DEFAULT_BORDER), IDENTITY, SHAPE)
        with_far = fr.build_layout(fr.build_roi_set([circle, far], DEFAULT_BORDER), IDENTITY, SHAPE)
        with_near = fr.build_layout(fr.build_roi_set([circle, near], DEFAULT_BORDER), IDENTITY, SHAPE)
        np.testing.assert_array_equal(with_far.ring_flat[with_far.ring_roi == 0], alone.ring_flat)
        self.assertLess(int((with_near.ring_roi == 0).sum()), int(alone.ring_count[0]))  # the rectangle's pixels are not in the ring any more
        sample_near = with_near.sample_flat[with_near.sample_roi == 1]
        np.testing.assert_array_equal(sample_near, np.flatnonzero(rz.rasterize_sample(near, SHAPE, IDENTITY)))

    def test_a_masks_digest_and_a_rectangles_digest_change_with_their_shape(self) -> None:
        a = fr.build_roi_set([_rect(10.0, 6.0, 0.0, border=Border(2, 2))], DEFAULT_BORDER)
        for other in (_rect(10.0, 6.5, 0.0, border=Border(2, 2)), _rect(10.0, 6.0, 5.0, border=Border(2, 2)), _rect(10.0, 6.0, 0.0, border=Border(2, 3)),
                      _rect(10.0, 6.0, 0.0, cx=61.0, border=Border(2, 2))):
            self.assertNotEqual(fr.roi_row_digests(a), fr.roi_row_digests(fr.build_roi_set([other], DEFAULT_BORDER)))
        follower = fr.build_roi_set([_rect(10.0, 6.0, 0.0, border=None)], Border(2.0, 2.0))
        explicit = fr.build_roi_set([_rect(10.0, 6.0, 0.0, border=Border(2.0, 2.0))], Border(9.0, 9.0))
        self.assertEqual(fr.roi_row_digests(follower), fr.roi_row_digests(explicit))  # the resolved border decides, not where it came from


class SessionShapesTests(unittest.TestCase):
    def _round_trip(self, rois: list[AreaRoi]) -> list[AreaRoi]:
        return sess._decode_area_rois(json.loads(json.dumps([sess._encode_area_roi(r) for r in rois])))

    def test_every_shape_and_border_survives_the_session_file(self) -> None:
        disc = (XX - 60.0) ** 2 + (YY - 50.0) ** 2 <= 36.0
        timeline = RoiTimeline().with_change(3, "persistent", RoiGeometry(11.0, 12.0, Rectangle(9.0, 4.0, 15.0), Border(1.0, 2.0)))
        rois = [
            AreaRoi(area_roi_id=1, center_x=10.0, center_y=20.0, shape=Circle(18.5)),
            AreaRoi(area_roi_id=2, center_x=30.0, center_y=40.0, shape=Circle(12.0), border=Border(-1.5, 2.5)),
            AreaRoi(area_roi_id=3, center_x=50.0, center_y=60.0, shape=Rectangle(20.0, 8.0, 33.0), border=NO_BORDER, timeline=timeline),
            AreaRoi(area_roi_id=4, center_x=60.0, center_y=50.0, shape=MaskShape(rz.crop_mask(disc)), border=Border(2.0, 3.0), role="reference"),
        ]
        back = self._round_trip(rois)
        self.assertEqual(len(back), 4)
        for want, got in zip(rois, back, strict=True):
            self.assertEqual(got.shape, want.shape)
            self.assertEqual(got.border, want.border)
            self.assertEqual(got.role, want.role)
            self.assertEqual(got.timeline, want.timeline)

    def test_a_roi_without_its_own_border_writes_no_border_key(self) -> None:
        payload = sess._encode_area_roi(AreaRoi(area_roi_id=1, center_x=1.0, center_y=2.0))
        self.assertNotIn("border", payload)
        self.assertEqual(payload["shape"], {"kind": "circle", "diameter_px": 20.0})
        self.assertNotIn("sample_diameter_px", payload)

    def test_an_unreadable_shape_skips_the_roi_and_does_not_fail_the_file(self) -> None:
        good = sess._encode_area_roi(AreaRoi(area_roi_id=1, center_x=1.0, center_y=2.0))
        bad = dict(good, area_roi_id=2, shape={"kind": "hexagon"})
        with self.assertLogs("lspr_imaging_app.storage.session", level="WARNING"):
            decoded = sess._decode_area_rois([good, bad])
        self.assertEqual([r.area_roi_id for r in decoded], [1])

    def test_a_session_written_before_shapes_reads_as_the_same_circles(self) -> None:
        """`tests/data/lspri_golden_rois_session_1_11.json` was written by the session code of schema 1.11 (loose diameter fields)."""
        with open(REPO_ROOT / "tests" / "data" / "lspri_golden_rois_session_1_11.json", encoding="utf-8") as f:
            raw = json.load(f)
        rois = sess._decode_area_rois(raw["rois"])
        self.assertEqual(len(rois), 12)
        self.assertEqual(golden.radii_digest(rois), golden.PINNED["radii"])  # bit-identical radii, so nothing rescans for the ring
        self.assertEqual(golden.pixels_digest(rois), golden.PINNED["pixels"])
        self.assertEqual(golden.numbers_digest(rois), golden.PINNED["numbers"])
        masks = sess._decode_area_rois(raw["mask_rois"])
        self.assertIsInstance(masks[1].shape, MaskShape)
        self.assertEqual(golden.mask_sample_digest(masks), golden.PINNED["mask_sample"])

    def test_an_old_timeline_entry_reads_as_a_circle_with_its_ring_as_a_border(self) -> None:
        entry = {"cube": 4, "x": 5.0, "y": 6.0, "sample_diameter_px": 20.0, "reference_inner_diameter_px": 28.0, "reference_outer_diameter_px": 36.0}
        timeline = sess._decode_timeline({"persistent": [entry], "individual": []})
        geometry = timeline.geometry_at(4)
        self.assertEqual(geometry.shape, Circle(20.0))
        self.assertEqual(ring_diameters(geometry.shape, geometry.border), (28.0, 36.0))

    def test_an_old_session_with_its_own_default_ring_keeps_every_rois_ring(self) -> None:
        legacy = sess._legacy_of_settings({"reference_inner_diameter_px": 30.0, "reference_outer_diameter_px": 40.0}, 20.0)
        self.assertEqual(legacy, ((30.0, 40.0), Border(5.0, 5.0)))  # the same ring as a default gap and thickness
        entry = {"area_roi_id": 1, "center_x": 1.0, "center_y": 2.0, "sample_diameter_px": 20.0}
        self.assertIsNone(sess._decode_area_rois([entry], legacy)[0].border)  # the default ring around a 20 px sample: it can follow the default
        wide = dict(entry, sample_diameter_px=24.0)
        border = sess._decode_area_rois([wide], legacy)[0].border  # a 24 px sample was measured with the absolute 30 / 40 ring
        self.assertEqual(ring_diameters(Circle(24.0), border), (30.0, 40.0))
        own = dict(entry, reference_inner_diameter_px=26.0)  # its own inner only: the outer is the session's default, as it always was
        self.assertEqual(ring_diameters(Circle(20.0), sess._decode_area_rois([own], legacy)[0].border), (26.0, 40.0))

    def test_detection_settings_carry_the_default_border(self) -> None:
        settings = AreaRoiDetectionSettings()
        self.assertEqual(settings.default_border(), Border(4.0, 4.0))
        self.assertEqual(ring_diameters(Circle(settings.sample_diameter_px), settings.default_border()), (28.0, 36.0))


class ToolboxShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()

    def tearDown(self) -> None:
        undo_manager.clear()

    def _roi(self, roi_id: int) -> AreaRoi:
        return next(r for r in self.toolbox.rois() if r.area_roi_id == roi_id)

    def _ring(self, roi_id: int) -> tuple[float, float]:
        roi = self._roi(roi_id)
        return ring_diameters(roi.shape, roi.border if roi.border is not None else self.toolbox.detection_settings().default_border())

    def test_a_roi_that_follows_the_default_keeps_following_and_its_ring_follows_its_size(self) -> None:
        roi_id = self.toolbox.add_roi(50.0, 50.0)
        self.assertIsNone(self._roi(roi_id).border)
        self.assertEqual(self._ring(roi_id), (28.0, 36.0))
        self.toolbox.resize_roi(roi_id, sample_diameter_px=24.0)
        self.assertIsNone(self._roi(roi_id).border)
        self.assertEqual(self._ring(roi_id), (32.0, 40.0))  # the ring keeps its gap and thickness
        undo_manager.undo()
        self.assertEqual(self._roi(roi_id).shape, Circle(20.0))

    def test_a_ring_edit_stores_the_border_that_gives_those_diameters(self) -> None:
        roi_id = self.toolbox.add_roi(50.0, 50.0)
        self.toolbox.resize_roi(roi_id, reference_inner_diameter_px=30.0, reference_outer_diameter_px=44.0)
        self.assertEqual(self._ring(roi_id), (30.0, 44.0))
        self.toolbox.resize_roi(roi_id, reference_outer_diameter_px=50.0)  # one side alone: the other keeps its diameter
        self.assertEqual(self._ring(roi_id), (30.0, 50.0))
        self.toolbox.resize_roi(roi_id, sample_diameter_px=30.0)  # a sample resize carries the ring
        self.assertEqual(self._ring(roi_id), (40.0, 60.0))
        self.toolbox.resize_roi(roi_id, sample_diameter_px=10.0, reference_inner_diameter_px=30.0, reference_outer_diameter_px=44.0)  # absolute again
        self.assertEqual(self._ring(roi_id), (30.0, 44.0))
        self.assertEqual(self._roi(roi_id).shape, Circle(10.0))

    def test_a_bad_ring_is_refused_before_anything_changes(self) -> None:
        roi_id = self.toolbox.add_roi(50.0, 50.0)
        before = (self._roi(roi_id).shape, self._roi(roi_id).border)
        steps = undo_manager.undo_label
        with self.assertRaises(ValueError):
            self.toolbox.resize_roi(roi_id, reference_inner_diameter_px=40.0, reference_outer_diameter_px=30.0)
        with self.assertRaises(ValueError):
            self.toolbox.resize_roi(roi_id, reference_inner_diameter_px=40.0)  # the current outer is 36
        self.assertEqual((self._roi(roi_id).shape, self._roi(roi_id).border), before)
        self.assertEqual(undo_manager.undo_label, steps)

    def test_reset_puts_the_shared_defaults_back(self) -> None:
        roi_id = self.toolbox.add_roi(50.0, 50.0, sample_diameter_px=33.0)
        self.toolbox.resize_roi(roi_id, reference_inner_diameter_px=50.0, reference_outer_diameter_px=60.0)
        self.toolbox.reset_roi_diameters([roi_id])
        self.assertEqual(self._roi(roi_id).shape, Circle(20.0))
        self.assertIsNone(self._roi(roi_id).border)

    def test_adding_a_rectangle_is_one_undo_step_and_resizing_it_by_diameter_is_refused(self) -> None:
        roi_id = self.toolbox.add_shaped_roi(50.0, 40.0, Rectangle(20.0, 6.0, 10.0), border=Border(2.0, 2.0))
        self.assertEqual(self._roi(roi_id).shape, Rectangle(20.0, 6.0, 10.0))
        with self.assertRaises(ValueError):
            self.toolbox.resize_roi(roi_id, sample_diameter_px=30.0)
        self.assertEqual(self._roi(roi_id).shape, Rectangle(20.0, 6.0, 10.0))
        undo_manager.undo()
        self.assertEqual(len(self.toolbox.rois()), 0)
        undo_manager.redo()
        self.assertEqual(len(self.toolbox.rois()), 1)

    def test_a_rotate_or_flip_of_the_image_turns_a_rectangle_with_it(self) -> None:
        roi_id = self.toolbox.add_shaped_roi(50.0, 40.0, Rectangle(20.0, 6.0, 0.0))
        self.toolbox.remap_all(lambda x, y: (-y, x), lambda mask: mask)  # a quarter turn: (x, y) -> (-y, x)
        roi = self._roi(roi_id)
        self.assertAlmostEqual(roi.shape.angle_deg, 90.0)
        self.assertEqual((roi.shape.width_px, roi.shape.height_px), (20.0, 6.0))
        self.assertEqual((roi.center_x, roi.center_y), (-40.0, 50.0))
        undo_manager.undo()
        self.assertEqual(self._roi(roi_id).shape.angle_deg, 0.0)
        self.toolbox.remap_all(lambda x, y: (-x, y), lambda mask: mask)  # a mirror image
        self.assertAlmostEqual(abs(self._roi(roi_id).shape.angle_deg), 180.0)

    def test_the_timeline_follows_a_rotate_too(self) -> None:
        timeline = RoiTimeline().with_change(3, "persistent", RoiGeometry(10.0, 5.0, Rectangle(10.0, 4.0, 0.0), None))
        moved = timeline.mapped(lambda x, y: (-y, x)).geometry_at(3)
        self.assertAlmostEqual(moved.shape.angle_deg, 90.0)
        self.assertEqual((moved.center_x, moved.center_y), (-5.0, 10.0))

    def test_a_mask_follows_remap_and_one_that_warps_to_nothing_is_reported(self) -> None:
        disc = (XX - 60.0) ** 2 + (YY - 50.0) ** 2 <= 36.0
        roi_id = self.toolbox.add_shaped_roi(60.0, 50.0, MaskShape(rz.crop_mask(disc)))
        shifted = rz.RoiMask(5, 7, np.ones((3, 3), dtype=bool))
        report = self.toolbox.remap_all(lambda x, y: (x, y), lambda mask: shifted)
        self.assertEqual(report.mask_shapes_remapped, 1)
        self.assertIs(self._roi(roi_id).shape.mask, shifted)
        report = self.toolbox.remap_all(lambda x, y: (x, y), lambda mask: None)
        self.assertEqual(report.mask_shapes_lost, (roi_id,))
        self.assertIs(self._roi(roi_id).shape.mask, shifted)  # left as it was, not emptied


if __name__ == "__main__":
    unittest.main()
