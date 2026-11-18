"""Scan path and reach of rectangles and masks (phase 2.2 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

Before phase 2.2 a rectangle or a mask was rasterised ROI by ROI on full planes (a layout of 160 masks took 1.1 s, 160 circles 20 ms) and counted as
"reaching every other ROI" (so moving one made every cell stale). Now each is found inside its own box, and it has a *reach*: the radius of the disc about
its centre that holds its pixels and its border. Two things must therefore hold, and this file is where both are checked against the reference
rasterizer (`roi.rasterize`), the one the drawing and the histogram use:

* the scan finds exactly the pixels the reference rasterizer finds (`sample_flat_indices`, `reference_flat_indices`, `locate_pixels`), and
* the reach is never too small: no pixel a ROI can read lies outside its window box (`roi_window_boxes`, the mask part of the cell hash relies on it)
  or its read box (`chunk_plan.roi_read_boxes`, partial reads of an export rely on it), and whenever a ring and a sample region share a pixel the
  neighbour test (`ring_reach_matrix`, which cells a moved ROI makes stale) says so, **with no safety margin** (``margin_px=0``).

That circles are unchanged is pinned by `test_lspri_scan_circle_golden.py`.

No Qt, no files, small synthetic images.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import frame_reduce as fr
    from lspr_imaging_app.dataset.chunk_plan import roi_read_boxes
    from lspr_imaging_app.domain import roi_shapes
    from lspr_imaging_app.domain.roi_model import AreaRoi
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape, Rectangle, RoiMask, border_reach_radius, reach_radius
    from lspr_imaging_app.roi import rasterize as rz
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

HEIGHT, WIDTH = 100, 140
SHAPE = (HEIGHT, WIDTH)
DEFAULT_BORDER = Border(4.0, 4.0)
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
AFFINES = (
    IDENTITY,
    np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]]),  # a fitted chromatic correction
    np.array([[0.99819, -0.08733, 3.7], [0.08733, 0.99819, -2.9]]),  # about 5 degrees
    np.array([[1.08, -0.04, 5.0], [0.03, 0.93, -4.0]]),  # a strong stretch and shear: the largest stretch is what the boxes are scaled by
    np.array([[0.9, 0.0, -9.5], [0.0, 1.12, 7.25]]),
)


def _random_mask(rng: np.random.Generator, x: float, y: float) -> RoiMask:
    """A ragged blob (holes included) whose bounding box is placed near ``(x, y)``; its ROI centre will be placed elsewhere in or near the box."""
    h, w = int(rng.integers(3, 26)), int(rng.integers(3, 26))
    pixels = rng.random((h, w)) < 0.7
    pixels[h // 2, w // 2] = True
    return RoiMask(int(round(x)) - w // 2, int(round(y)) - h // 2, pixels)


def _random_border(rng: np.random.Generator) -> Border | None:
    pick = rng.random()
    if pick < 0.12:
        return NO_BORDER
    if pick < 0.24:
        return None  # follows the shared default
    return Border(float(rng.uniform(-3.0, 6.0)), float(rng.choice([0.0, rng.uniform(0.5, 8.0)])))


def _random_roi(rng: np.random.Generator, roi_id: int, kind: str, x: float, y: float) -> AreaRoi:
    if kind == "circle":
        shape = Circle(float(rng.uniform(4.0, 24.0)))
        cx, cy = x, y
    elif kind == "rectangle":
        shape = Rectangle(float(rng.uniform(2.0, 30.0)), float(rng.uniform(2.0, 30.0)), float(rng.uniform(0.0, 180.0)))
        cx, cy = x, y
    else:
        mask = _random_mask(rng, x, y)
        shape = MaskShape(mask)
        # the ROI centre of a mask is not tied to its pixels: put it up to 10 px away from the middle of the box
        cx, cy = x + float(rng.uniform(-10.0, 10.0)), y + float(rng.uniform(-10.0, 10.0))
    return AreaRoi(roi_id, float(cx), float(cy), shape, border=_random_border(rng))


def _plane_pixels(plane: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.nonzero(plane)
    return rows, cols


class FlatIndexTests(unittest.TestCase):
    """The scan asks the rasterizer for flat pixel indices; they must be the reference planes' own pixels."""

    def test_flat_indices_are_the_pixels_of_the_reference_planes(self) -> None:
        rng = np.random.default_rng(21)
        checked = {"rectangle": 0, "mask": 0, "circle": 0}
        for trial in range(240):
            kind = ("rectangle", "mask", "circle")[trial % 3]
            roi = _random_roi(rng, 1, kind, float(rng.uniform(-12, WIDTH + 12)), float(rng.uniform(-12, HEIGHT + 12)))  # some outside the image
            affine = AFFINES[trial % len(AFFINES)]
            sample = rz.sample_flat_indices(roi, SHAPE, affine)
            ring = rz.reference_flat_indices(roi, SHAPE, affine, default_border=DEFAULT_BORDER)
            self.assertEqual(sample.dtype, np.int64)
            self.assertTrue(np.array_equal(sample, np.flatnonzero(rz.rasterize_sample(roi, SHAPE, affine))), f"{kind} sample, trial {trial}")
            self.assertTrue(
                np.array_equal(ring, np.flatnonzero(rz.rasterize_reference(roi, SHAPE, affine, default_border=DEFAULT_BORDER))), f"{kind} border, trial {trial}"
            )
            self.assertTrue(np.all(np.diff(sample) > 0) and np.all(np.diff(ring) > 0), "ascending and unique")
            checked[kind] += int(sample.size > 0)
        self.assertTrue(all(count > 30 for count in checked.values()), f"too few non-empty cases to mean anything: {checked}")

    def test_a_region_outside_the_image_or_a_border_switched_off_gives_no_pixels(self) -> None:
        for roi in (AreaRoi(1, -80.0, -80.0, Rectangle(10.0, 10.0, 30.0), border=Border(1.0, 3.0)), AreaRoi(2, 70.0, 50.0, Rectangle(10.0, 10.0), border=NO_BORDER)):
            ring = rz.reference_flat_indices(roi, SHAPE, IDENTITY, default_border=DEFAULT_BORDER)
            self.assertEqual(ring.size, 0)
            self.assertEqual(ring.dtype, np.int64)
        self.assertEqual(rz.sample_flat_indices(AreaRoi(3, -80.0, -80.0, Rectangle(10.0, 10.0)), SHAPE, IDENTITY).size, 0)

    def test_a_mask_is_found_inside_its_own_box_and_agrees_with_the_patch_variant(self) -> None:
        rng = np.random.default_rng(4)
        for trial in range(60):
            roi = _random_roi(rng, 1, "mask", float(rng.uniform(5, WIDTH - 5)), float(rng.uniform(5, HEIGHT - 5)))
            affine = AFFINES[trial % len(AFFINES)]
            plane = rz.rasterize_sample(roi, SHAPE, affine)
            for origin, patch in (((0, 0), SHAPE), ((20, 10), (50, 60)), ((100, 60), (40, 40))):
                crop = plane[origin[1]:origin[1] + patch[0], origin[0]:origin[0] + patch[1]]
                self.assertTrue(np.array_equal(rz.rasterize_sample_for_patch(roi, origin, patch, affine), crop), f"trial {trial}, patch {origin}")

    def test_a_mask_at_the_image_edge_keeps_its_edge_pixels(self) -> None:
        """The full-canvas scipy warp the scan used before phase 2.2 dropped mask pixels within half a pixel of the image border under a small shift;
        the box warp keeps them (a mask on the edge column stays on the edge column)."""
        pixels = np.ones((6, 5), dtype=bool)
        roi = AreaRoi(1, 2.0, 3.0, MaskShape(RoiMask(0, 0, pixels)), border=NO_BORDER)
        plane = rz.rasterize_sample(roi, SHAPE, np.array([[1.0, 0.0, 0.2], [0.0, 1.0, 0.2]]))
        expected = np.zeros(SHAPE, dtype=bool)
        expected[:6, :5] = True
        self.assertTrue(np.array_equal(plane, expected))

    def test_the_mask_distance_map_is_cached_for_many_masks(self) -> None:
        """A cache smaller than the number of mask ROIs makes every layout recompute every distance map (160 masks: 169 ms against 99 ms per layout)."""
        rng = np.random.default_rng(9)
        rois = [_random_roi(rng, i + 1, "mask", float(rng.uniform(10, WIDTH - 10)), float(rng.uniform(10, HEIGHT - 10))) for i in range(150)]
        for roi in rois:
            roi.border = Border(2.0, 3.0)
        rz._mask_sdf.cache_clear()
        for roi in rois:
            rz.reference_flat_indices(roi, SHAPE, IDENTITY, default_border=DEFAULT_BORDER)
        misses = rz._mask_sdf.cache_info().misses
        for roi in rois:
            rz.reference_flat_indices(roi, SHAPE, IDENTITY, default_border=DEFAULT_BORDER)
        self.assertEqual(rz._mask_sdf.cache_info().misses, misses, "a second pass over the same masks must not compute a distance map again")


class ScanPathTests(unittest.TestCase):
    def _mixed(self) -> list[AreaRoi]:
        rng = np.random.default_rng(3)
        rois = []
        for i in range(24):
            kind = ("circle", "rectangle", "mask")[i % 3]
            rois.append(_random_roi(rng, i + 1, kind, float(rng.uniform(5, WIDTH - 5)), float(rng.uniform(5, HEIGHT - 5))))
        rois[4].role = "reference"  # a rectangle: one region, no ring
        rois[5].role = "reference"  # a mask
        return rois

    def test_locate_pixels_equals_the_reference_planes_roi_by_roi(self) -> None:
        rois = self._mixed()
        rs = fr.build_roi_set(rois, DEFAULT_BORDER)
        for affine in AFFINES:
            s_roi, s_flat, r_roi, r_flat = fr.locate_pixels(rs, affine, SHAPE)
            for i, roi in enumerate(rois):
                want_sample = np.flatnonzero(rz.rasterize_sample(roi, SHAPE, affine))
                want_ring = np.zeros(0, dtype=np.int64) if rs.is_ref[i] else np.flatnonzero(rz.rasterize_reference(roi, SHAPE, affine, default_border=DEFAULT_BORDER))
                self.assertTrue(np.array_equal(s_flat[s_roi == i], want_sample), f"ROI {i} sample")
                self.assertTrue(np.array_equal(r_flat[r_roi == i], want_ring), f"ROI {i} ring")
            self.assertTrue(np.all(np.diff(s_roi) >= 0) and np.all(np.diff(r_roi) >= 0), "sorted by ROI")

    def test_the_scan_does_not_build_a_full_plane_for_a_rectangle_or_a_mask(self) -> None:
        rois = [r for r in self._mixed() if not isinstance(r.shape, Circle)]
        rs = fr.build_roi_set(rois, DEFAULT_BORDER)
        boom = AssertionError("a full plane was made")
        with mock.patch.object(rz, "rasterize_sample", side_effect=boom), mock.patch.object(rz, "rasterize_reference", side_effect=boom):
            layout = fr.build_layout(rs, AFFINES[1], SHAPE)
        self.assertGreater(int(layout.sample_count.sum()), 0)

    def test_a_subset_layout_equals_the_restricted_full_layout_with_rectangles_and_masks(self) -> None:
        rois = self._mixed()
        rs = fr.build_roi_set(rois, DEFAULT_BORDER)
        rng = np.random.default_rng(12)
        for mode in (fr.EXCLUDE_ALL_SAMPLE_ROIS, "none"):
            for affine in (IDENTITY, AFFINES[2]):
                full = fr.build_layout(rs, affine, SHAPE, mode)
                for _ in range(6):
                    subset = [int(i) for i in rng.choice(rs.n, size=int(rng.integers(1, 5)), replace=False)]
                    got, want = fr.build_subset_layout(rs, affine, SHAPE, mode, subset), full.restrict(subset)
                    for name in ("sample_roi", "sample_flat", "ring_roi", "ring_flat", "sample_count", "ring_count", "needed_flat"):
                        self.assertTrue(np.array_equal(getattr(got, name), getattr(want, name)), f"{name}, subset {subset}, mode {mode}")

    def test_reduced_numbers_are_those_of_the_reference_pixels(self) -> None:
        rois = self._mixed()
        rs = fr.build_roi_set(rois, DEFAULT_BORDER)
        image = np.random.default_rng(6).normal(1000.0, 40.0, SHAPE).astype(np.float32)
        result = fr.reduce_frame(rs, image, None, AFFINES[1], ("mean",), exclusion_mode="none")
        compared = 0
        for i, roi in enumerate(rois):
            sample = image[rz.rasterize_sample(roi, SHAPE, AFFINES[1])]
            ring = np.zeros(0, dtype=np.float32) if rs.is_ref[i] else image[rz.rasterize_reference(roi, SHAPE, AFFINES[1], default_border=DEFAULT_BORDER)]
            self.assertEqual(int(result.n_nominal[i, fr.SAMPLE]), sample.size, f"ROI {i}")
            self.assertEqual(int(result.n_nominal[i, fr.REFERENCE]), ring.size, f"ROI {i}")
            if sample.size and ring.size:  # a ROI whose sample or ring has no pixel is NaN on both sides by design
                compared += 1
                self.assertAlmostEqual(float(result.values["mean"][i, fr.SAMPLE]), float(sample.astype(np.float64).mean()), places=9)
                self.assertAlmostEqual(float(result.values["mean"][i, fr.REFERENCE]), float(ring.astype(np.float64).mean()), places=9)
        self.assertGreater(compared, 10)

    def test_an_empty_mask_has_no_sample_and_reaches_nobody(self) -> None:
        roi = AreaRoi(1, 30.0, 30.0, MaskShape(RoiMask(25, 25, np.zeros((6, 6), dtype=bool))), border=Border(1.0, 3.0))
        other = AreaRoi(2, 32.0, 30.0, Circle(10.0), border=Border(2.0, 4.0))
        rs = fr.build_roi_set([roi, other], DEFAULT_BORDER)
        self.assertFalse(bool(rs.sample_on[0]))
        self.assertFalse(bool(fr.ring_reach_matrix(rs, [1], margin_px=0.0)[0, 0]))  # the circle's ring does not lose anything to an empty mask


class LocalKeyTests(unittest.TestCase):
    """Moving a rectangle or a mask makes stale only the ROIs it can touch (before phase 2.2: all of them)."""

    def _keys(self, rois):
        return fr.local_roi_keys(fr.build_roi_set(rois, DEFAULT_BORDER), fr.EXCLUDE_ALL_SAMPLE_ROIS)

    def test_a_rectangle_far_from_the_circles_changes_only_its_own_key(self) -> None:
        def build(x):
            circles = [AreaRoi(i + 1, cx, 20.0, Circle(12.0)) for i, cx in enumerate((15.0, 50.0, 85.0))]
            return circles + [AreaRoi(4, x, 80.0, Rectangle(16.0, 10.0, 30.0), border=Border(2.0, 4.0))]

        before, after = self._keys(build(100.0)), self._keys(build(104.0))
        self.assertEqual(before[:3], after[:3])
        self.assertNotEqual(before[3], after[3])

    def test_a_rectangle_beside_a_ring_changes_that_rings_key(self) -> None:
        def build(x):
            return [AreaRoi(1, 40.0, 40.0, Circle(12.0)), AreaRoi(2, x, 40.0, Rectangle(10.0, 10.0), border=NO_BORDER), AreaRoi(3, 120.0, 90.0, Circle(12.0))]

        before, after = self._keys(build(62.0)), self._keys(build(63.0))  # the ring of ROI 1 reaches out to radius 6 + 4 + 4 = 14; the square's edge is at 57..58
        self.assertNotEqual(before[0], after[0])
        self.assertEqual(before[2], after[2])


class ReachTests(unittest.TestCase):
    """The reach may never be too small. Random shapes, borders (negative gap, zero thickness, none), affines; the comparison is with the pixels of the
    reference rasterizer."""

    def _rois(self, seed: int, n: int, kinds=("rectangle", "mask")) -> list[AreaRoi]:
        rng = np.random.default_rng(seed)
        return [_random_roi(rng, i + 1, kinds[i % len(kinds)], float(rng.uniform(-6, WIDTH + 6)), float(rng.uniform(-6, HEIGHT + 6))) for i in range(n)]

    def test_every_pixel_a_roi_can_read_is_inside_its_window_box(self) -> None:
        for seed in range(6):
            rois = self._rois(seed, 30)
            rs = fr.build_roi_set(rois, DEFAULT_BORDER)
            for affine in AFFINES:
                y0, y1, x0, x1, bounded = fr.roi_window_boxes(rs, affine, SHAPE)
                self.assertTrue(bounded.all())
                for i, roi in enumerate(rois):
                    planes = [rz.rasterize_sample(roi, SHAPE, affine), rz.rasterize_reference(roi, SHAPE, affine, default_border=DEFAULT_BORDER)]
                    for plane in planes:
                        rows, cols = _plane_pixels(plane)
                        self.assertTrue(np.all((rows >= y0[i]) & (rows < y1[i]) & (cols >= x0[i]) & (cols < x1[i])), f"seed {seed}, ROI {i} ({type(roi.shape).__name__})")

    def test_every_pixel_a_roi_can_read_is_inside_its_read_box(self) -> None:
        for seed in range(6):
            rois = self._rois(seed + 100, 30)
            boxes = roi_read_boxes(rois, SHAPE, default_border=DEFAULT_BORDER, affines=list(AFFINES))
            for i, roi in enumerate(rois):
                for affine in AFFINES:
                    for plane in (rz.rasterize_sample(roi, SHAPE, affine), rz.rasterize_reference(roi, SHAPE, affine, default_border=DEFAULT_BORDER)):
                        rows, cols = _plane_pixels(plane)
                        bx0, by0, bx1, by1 = boxes[i]
                        self.assertTrue(np.all((cols >= bx0) & (cols < bx1) & (rows >= by0) & (rows < by1)), f"seed {seed}, ROI {i} ({type(roi.shape).__name__})")

    def test_a_ring_that_shares_a_pixel_with_a_sample_region_is_always_a_neighbour(self) -> None:
        """No margin: the radii alone must be enough. Pairs of ROIs of every shape are put so close that their regions often meet."""
        meetings = {}
        kinds = ("circle", "rectangle", "mask")
        for seed in range(8):
            rng = np.random.default_rng(300 + seed)
            for pair in range(30):
                jk = (kinds[int(rng.integers(0, 3))], kinds[int(rng.integers(0, 3))])
                x, y = float(rng.uniform(30, WIDTH - 30)), float(rng.uniform(30, HEIGHT - 30))
                a = _random_roi(rng, 1, jk[0], x, y)
                b = _random_roi(rng, 2, jk[1], x + float(rng.uniform(-28, 28)), y + float(rng.uniform(-28, 28)))
                rs = fr.build_roi_set([a, b], DEFAULT_BORDER)
                affine = AFFINES[(seed + pair) % len(AFFINES)]
                near = fr.ring_reach_matrix(rs, [0, 1], margin_px=0.0)
                ring = [rz.rasterize_reference(r, SHAPE, affine, default_border=DEFAULT_BORDER) for r in (a, b)]
                sample = [rz.rasterize_sample(r, SHAPE, affine) for r in (a, b)]
                for j, k in ((0, 1), (1, 0)):
                    if (ring[j] & sample[k]).any():
                        meetings[jk] = meetings.get(jk, 0) + 1
                        self.assertTrue(bool(near[j, k]), f"the ring of a {jk[j]} met the sample of a {jk[1 - j]} but the pair is not a neighbour (seed {seed}, pair {pair})")
        self.assertGreater(sum(meetings.values()), 100, f"the test hardly saw any meeting: {meetings}")
        for shape_of_ring in kinds:
            self.assertTrue(any(key[0] == shape_of_ring for key in meetings), f"no case with a {shape_of_ring} ring: {meetings}")

    def test_a_masks_border_reaches_a_small_circle_even_at_the_worst_alignment(self) -> None:
        """The slack of a mask's border (`MASK_BORDER_SLACK_PX`): a mask ROI and a 1-3 px circle at 4-14 px, under a shift of 0.49 px (a mapped point
        at the corner of a pixel, the worst case of the distance map's rounding) and a rotation. With no slack at all 1 pair in 1,500 that meet is missed."""
        affines = (np.array([[1.0, 0.0, 0.49], [0.0, 1.0, 0.49]]), AFFINES[2])
        rng = np.random.default_rng(2)
        meetings = misses = 0
        for _ in range(5000):  # with no slack the first miss is trial 4,376 of this seed: fewer trials would not see it
            h, w = int(rng.integers(2, 12)), int(rng.integers(2, 12))
            pixels = np.ones((h, w), dtype=bool) if rng.random() < 0.5 else rng.random((h, w)) < 0.75
            pixels[0, 0] = True
            mask_roi = AreaRoi(
                1, 60 + w / 2 + float(rng.uniform(-2, 2)), 50 + h / 2 + float(rng.uniform(-2, 2)), MaskShape(RoiMask(60, 50, pixels)),
                border=Border(float(rng.choice([0.0, 1.0, 2.5])), float(rng.choice([1.0, 3.0]))),
            )
            angle, distance = float(rng.uniform(0.0, 2.0 * np.pi)), float(rng.uniform(4.0, 14.0))
            small = AreaRoi(
                2, mask_roi.center_x + distance * np.cos(angle), mask_roi.center_y + distance * np.sin(angle), Circle(float(rng.uniform(1.0, 3.0))),
                border=Border(1.0, 1.0),
            )
            near = fr.ring_reach_matrix(fr.build_roi_set([mask_roi, small], DEFAULT_BORDER), [0], margin_px=0.0)[0, 1]
            affine = affines[int(rng.integers(0, 2))]
            if (rz.rasterize_reference(mask_roi, SHAPE, affine) & rz.rasterize_sample(small, SHAPE, affine)).any():
                meetings += 1
                misses += int(not near)
        self.assertGreater(meetings, 1000)  # 1,250 or so
        self.assertEqual(misses, 0)

    def test_the_reach_functions(self) -> None:
        rect = Rectangle(8.0, 6.0, 33.0)
        self.assertAlmostEqual(reach_radius(rect, 10.0, 10.0), 5.0)
        self.assertAlmostEqual(border_reach_radius(rect, 10.0, 10.0, Border(2.0, 3.0)), 10.0)
        self.assertAlmostEqual(border_reach_radius(rect, 10.0, 10.0, Border(-9.0, 3.0)), 5.0, msg="a border that lies inside the shape reaches no farther than the shape")
        self.assertAlmostEqual(border_reach_radius(Circle(10.0), 0.0, 0.0, Border(2.0, 3.0)), 10.0)
        mask = MaskShape(RoiMask(0, 0, np.ones((3, 3), dtype=bool)))
        self.assertAlmostEqual(
            border_reach_radius(mask, 1.0, 1.0, Border(1.0, 1.0)), reach_radius(mask, 1.0, 1.0) + 2.0 + roi_shapes.MASK_BORDER_SLACK_PX
        )


if __name__ == "__main__":
    unittest.main()
