"""Pure-logic tests for the OME-Zarr export chunk layout suggestions of the LSPRimaging Evaluation rewrite.

Covers `lspr_imaging_app.dataset.chunk_plan`: the breakpoint grid alignment (checked against a brute force over every
offset), the exact 2-D chunk count, the compressibility rule, pitch estimation, the suggestion list, the
lossless/lossy export frames and the ROI read boxes.

Like the other rewrite tests this only runs when the `apps/LSPRi/eva` submodule is on its `rewrite` branch. No Qt, no
files, small synthetic layouts.
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
    from lspr_imaging_app.dataset import chunk_plan as cp
    from lspr_imaging_app.roi.model import AreaRoi
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def lattice_boxes(rows: int, cols: int, pitch: float, size: int, x0: float = 13.0, y0: float = 21.0, tilt_deg: float = 0.0) -> np.ndarray:
    t = np.deg2rad(tilt_deg)
    out = []
    for r in range(rows):
        for c in range(cols):
            cx = x0 + c * pitch * np.cos(t) - r * pitch * np.sin(t)
            cy = y0 + c * pitch * np.sin(t) + r * pitch * np.cos(t)
            bx, by = int(np.floor(cx)), int(np.floor(cy))
            out.append((bx, by, bx + size, by + size))
    return np.array(out, dtype=np.int64)


def brute_axis(lo, hi, members_list, c, o):
    """Chunk columns per unit for grid lines at o + k*c, by listing them."""
    counts = []
    for members in members_list:
        cols = set()
        for i in members:
            cols.update(range((lo[i] - o) // c, (hi[i] - 1 - o) // c + 1))
        counts.append(len(cols))
    return counts


def brute_chunks(boxes, layout, members):
    keys = set()
    for i in members:
        x0, y0, x1, y1 = boxes[i]
        for kx in range((x0 - layout.offset_x) // layout.chunk_w, (x1 - 1 - layout.offset_x) // layout.chunk_w + 1):
            for ky in range((y0 - layout.offset_y) // layout.chunk_h, (y1 - 1 - layout.offset_y) // layout.chunk_h + 1):
                keys.add((kx, ky))
    return len(keys)


class GridAlignmentTests(unittest.TestCase):
    SHAPE = (400, 520)

    def test_best_offset_matches_brute_force(self):
        rng = np.random.default_rng(3)
        lo = rng.integers(0, 300, size=25)
        hi = lo + rng.integers(1, 60, size=25)
        valid = np.ones(25, dtype=bool)
        groups = [list(range(0, 10)), list(range(10, 25))]
        for pattern in cp.PATTERNS:
            units = cp._sorted_units(cp._make_units(valid, pattern, groups), lo)
            members_list = [list(m) for m in units.members]
            for c in (7, 16, 33, 64, 150):
                off, tol = cp._best_offset(lo, hi, units, c)
                brute = [sum(brute_axis(lo, hi, members_list, c, o)) for o in range(c)]
                with self.subTest(pattern=pattern, c=c):
                    self.assertEqual(brute[off], min(brute))
                    self.assertEqual(int(cp._axis_counts(lo, hi, units, c, np.array([off])).sum()), min(brute))
                    for d in range(-tol, tol + 1):  # the whole tolerance window is optimal
                        self.assertEqual(brute[(off + d) % c], min(brute))

    def test_saturated_group_ties_are_broken_by_single_rois(self):
        # 2026-10-20 bug: a column of ROIs wider than one chunk ties at 2 chunks per column for every offset; the
        # tie must go to the fewest chunks per ROI, not put a grid line through every column.
        boxes = lattice_boxes(10, 6, pitch=80.0, size=69, tilt_deg=1.4)
        columns = cp.lattice_groups((boxes[:, :2] + boxes[:, 2:]) / 2.0, axis="columns", tolerance_px=40.0)
        layout = cp.align_grid(boxes, 80, 900, pattern="group", groups=columns)
        lo, hi = boxes[:, 0], boxes[:, 2]
        group_cost = [sum(brute_axis(lo, hi, columns, 80, o)) for o in range(80)]
        single_cost = [sum(brute_axis(lo, hi, [[i] for i in range(len(boxes))], 80, o)) for o in range(80)]
        best_single_among_best_group = min(s for g, s in zip(group_cost, single_cost) if g == min(group_cost))
        self.assertEqual(group_cost[layout.offset_x], min(group_cost))
        self.assertEqual(single_cost[layout.offset_x], best_single_among_best_group)
        self.assertLess(cp.score_layout(boxes, layout)["single"].chunks, 2.0)

    def test_pitch_sized_chunk_gives_one_chunk_per_roi(self):
        boxes = lattice_boxes(8, 11, pitch=40.0, size=30)
        layout = cp.align_grid(boxes, 40, 40)
        self.assertEqual(cp.score_layout(boxes, layout)["single"].chunks, 1.0)
        self.assertGreaterEqual(layout.tolerance_x, 4)  # 10 px slack, centred: about +-4..5 px

    def test_non_integer_pitch_drift_is_absorbed_when_within_slack(self):
        boxes = lattice_boxes(1, 15, pitch=40.3, size=30)  # drift 0.3 px x 14 = 4.2 px < 10 px slack
        self.assertEqual(cp.score_layout(boxes, cp.align_grid(boxes, 40, 40))["single"].chunks, 1.0)

    def test_drift_beyond_slack_costs_extra_chunks(self):
        boxes = lattice_boxes(1, 12, pitch=41.5, size=34)  # drift 1.5 px x 11 = 16.5 px > 6 px slack
        self.assertGreater(cp.score_layout(boxes, cp.align_grid(boxes, 40, 40))["single"].chunks, 1.0)

    def test_exact_score_matches_brute_force(self):
        boxes = lattice_boxes(5, 6, pitch=37.0, size=26, tilt_deg=1.5)
        groups = [list(range(0, 15)), list(range(15, 30))]
        layout = cp.ChunkLayout(48, 31, 5, 17)
        scores = cp.score_layout(boxes, layout, groups=groups)
        self.assertAlmostEqual(scores["single"].chunks, np.mean([brute_chunks(boxes, layout, [i]) for i in range(30)]))
        self.assertAlmostEqual(scores["group"].chunks, np.mean([brute_chunks(boxes, layout, g) for g in groups]))
        self.assertEqual(scores["all"].chunks, brute_chunks(boxes, layout, range(30)))

    def test_no_units_gives_none(self):
        self.assertIsNone(cp.align_grid(np.zeros((0, 4)), 32, 32))
        self.assertIsNone(cp.align_grid(lattice_boxes(2, 2, 40.0, 30), 32, 32, pattern="group"))


class RulesTests(unittest.TestCase):
    def test_compressible_pixel_counts(self):
        self.assertTrue(cp.compresses(80, 80))
        self.assertTrue(cp.compresses(80, 900))
        self.assertFalse(cp.compresses(79, 79))
        self.assertFalse(cp.compresses(150, 150))
        self.assertEqual(cp._snap_compressible(79, 900), 80)
        self.assertEqual(cp._snap_compressible(79, 1, minimum=82), 88)

    def test_warnings(self):
        self.assertEqual(cp.chunk_warnings(256, 256), ())
        self.assertEqual(len(cp.chunk_warnings(79, 79)), 2)  # small and not compressible
        self.assertEqual(len(cp.chunk_warnings(79, 79, compression=False)), 1)
        self.assertEqual(cp.chunk_warnings(80, 900), ())  # a column is not "small"

    def test_estimate_pitch(self):
        boxes = lattice_boxes(6, 9, pitch=41.0, size=30, tilt_deg=1.4)
        pitch = cp.estimate_pitch((boxes[:, :2] + boxes[:, 2:]) / 2.0)
        self.assertAlmostEqual(pitch[0], 41.0, delta=1.0)
        self.assertAlmostEqual(pitch[1], 41.0, delta=1.0)
        self.assertIsNone(cp.estimate_pitch(np.array([[0, 0], [40, 0], [80, 0], [120, 0]], dtype=float)))  # one row


class SuggestionTests(unittest.TestCase):
    SHAPE = (400, 520)

    def test_lattice_suggestions(self):
        boxes = lattice_boxes(8, 11, pitch=40.0, size=30)
        out = cp.suggest_layouts(boxes, self.SHAPE)
        self.assertEqual([s.key for s in out], ["columns", "square_pitch2", "square_256", "rows", "per_roi"])
        self.assertTrue(out[0].recommended and not any(s.recommended for s in out[1:]))
        columns = out[0]
        self.assertEqual(columns.layout.chunk_h, 400)
        self.assertEqual(columns.layout.offset_y, 0)
        self.assertEqual(columns.scores["group"].chunks, 1.0)  # one chunk per column of ROIs
        self.assertEqual(columns.scores["single"].chunks, 1.0)
        self.assertEqual(columns.lossless.chunks_y, 1)
        per_roi = next(s for s in out if s.key == "per_roi")
        self.assertEqual(per_roi.layout.chunk_w, 40)
        self.assertEqual(per_roi.scores["single"].chunks, 1.0)
        self.assertTrue(any("Small" in w for w in per_roi.warnings))
        for s in out:
            with self.subTest(s.key):
                self.assertTrue(cp.compresses(s.layout.chunk_w, s.layout.chunk_h))
                self.assertGreaterEqual(s.layout.chunk_w, 30)
                self.assertGreaterEqual(s.layout.chunk_h, 30)

    def test_roi_table_groups_are_scored_when_given(self):
        boxes = lattice_boxes(4, 4, pitch=40.0, size=30)
        out = cp.suggest_layouts(boxes, self.SHAPE, groups=[list(range(16))])
        self.assertEqual(out[0].scores["group"].units, 1)

    def test_without_lattice_offers_squares(self):
        boxes = np.array([[10, 10, 40, 40], [200, 50, 230, 80], [100, 300, 130, 330]])
        self.assertEqual([s.key for s in cp.suggest_layouts(boxes, self.SHAPE)], ["square_256", "square_128"])
        self.assertEqual(cp.suggest_layouts(np.zeros((0, 4)), self.SHAPE), [])


class ExportFrameTests(unittest.TestCase):
    SHAPE = (300, 410)

    def setUp(self):
        self.boxes = lattice_boxes(4, 5, pitch=40.0, size=30, x0=57.0, y0=63.0)
        self.layout = cp.ChunkLayout(40, 40, offset_x=12, offset_y=0)

    def test_lossless_pads_less_than_one_chunk_and_keeps_every_pixel(self):
        f = cp.export_frame(self.layout, self.boxes, self.SHAPE, lossless=True, bytes_per_pixel=4)
        self.assertEqual((f.origin_x, f.origin_y), (12 - 40, 0))
        self.assertEqual((f.width, f.height), (410 + 28, 300))
        self.assertEqual(f.discarded_px, 0)
        self.assertEqual(f.padded_px, 28 * 300)
        self.assertEqual((f.chunks_x, f.chunks_y, f.chunks_per_plane), (11, 8, 88))
        self.assertEqual(f.stored_bytes_per_plane, 11 * 8 * 40 * 40 * 4)
        self.assertEqual(f.dataset_bytes(10), f.stored_bytes_per_plane * 10)  # uncompressed: whole chunks
        self.assertEqual(f.dataset_bytes(10, 0.5), 410 * 300 * 4 * 10 // 2)  # compressed: real pixels only

    def test_lossy_keeps_the_aligned_rectangle_around_all_rois(self):
        f = cp.export_frame(self.layout, self.boxes, self.SHAPE, lossless=False)
        self.assertEqual(f.origin_x % 40, 12)
        self.assertEqual(f.origin_y % 40, 0)
        self.assertLessEqual(f.origin_x, self.boxes[:, 0].min())
        self.assertLessEqual(f.origin_y, self.boxes[:, 1].min())
        self.assertGreaterEqual(f.origin_x + f.width, self.boxes[:, 2].max())
        self.assertGreaterEqual(f.origin_y + f.height, self.boxes[:, 3].max())
        self.assertGreater(f.discarded_px, 0)
        self.assertLess(f.stored_bytes_per_plane, cp.export_frame(self.layout, self.boxes, self.SHAPE, lossless=True).stored_bytes_per_plane)


class ReadBoxTests(unittest.TestCase):
    def test_circle_box_matches_reach_rule_and_ring_dominates(self):
        roi = AreaRoi(area_roi_id=1, center_x=50.4, center_y=40.6, sample_diameter_px=10.0)
        box = cp.roi_read_boxes([roi], (100, 120), default_inner_diameter_px=14.0, default_outer_diameter_px=20.0)[0]
        self.assertEqual(box.tolist(), [40, 30, 61, 51])  # floor(50.4 - 10), floor(50.4 + 10) + 1, ...

    def test_affines_are_united_and_boxes_clipped(self):
        roi = AreaRoi(area_roi_id=1, center_x=5.0, center_y=40.0, sample_diameter_px=10.0)
        shift = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, -2.0]])
        box = cp.roi_read_boxes([roi], (100, 120), default_inner_diameter_px=0.0, default_outer_diameter_px=0.0,
                                affines=[np.eye(2, 3), shift])[0]
        self.assertEqual(box.tolist(), [0, 33, 14, 46])

    def test_roi_outside_image_gives_empty_box(self):
        roi = AreaRoi(area_roi_id=1, center_x=-50.0, center_y=40.0, sample_diameter_px=10.0)
        box = cp.roi_read_boxes([roi], (100, 120), default_inner_diameter_px=0.0, default_outer_diameter_px=0.0)[0]
        self.assertLessEqual(box[2], box[0])

    def test_map_boxes_shift_rotation_and_clip(self):
        boxes = np.array([[10, 20, 30, 40], [5, 5, 5, 9]])  # second one empty
        shifted = cp.map_boxes(boxes, np.array([[1.0, 0.0, -4.0], [0.0, 1.0, 3.0]]), (100, 100))
        self.assertEqual(shifted.tolist(), [[6, 23, 26, 43], [0, 0, 0, 0]])
        c, s = np.cos(np.radians(90)), np.sin(np.radians(90))  # 90 deg about the origin, then shift into view
        rotated = cp.map_boxes(boxes[:1], np.array([[c, -s, 50.0], [s, c, 0.0]]), (100, 100))
        self.assertEqual(rotated.tolist(), [[10, 10, 30, 30]])  # x' = 50 - y, y' = x: 20x20 stays 20x20
        clipped = cp.map_boxes(boxes[:1], np.array([[1.0, 0.0, -20.0], [0.0, 1.0, 0.0]]), (100, 100))
        self.assertEqual(clipped.tolist(), [[0, 20, 10, 40]])

    def test_lattice_groups_rows(self):
        centres = np.array([[10, 10], [50, 11], [10, 52], [51, 50], [90, 9]], dtype=float)
        self.assertEqual(cp.lattice_groups(centres, axis="rows", tolerance_px=20.0), [[0, 1, 4], [2, 3]])


class AffineReachTests(unittest.TestCase):
    """`affine_reach` + `grow_boxes`: the cheap stand-in for unioning a read box over every fitted affine."""

    @staticmethod
    def rois(n: int = 24):
        return [
            AreaRoi(area_roi_id=i + 1, center_x=50.0 + 79.6 * (i % 6), center_y=50.0 + 79.6 * (i // 6),
                    sample_diameter_px=33.7, reference_inner_diameter_px=49.5, reference_outer_diameter_px=59.9)
            for i in range(n)
        ]

    def test_no_affines_means_no_growth(self):
        self.assertEqual(cp.affine_reach(np.zeros((3, 2)), None), (0.0, 1.0))
        boxes = np.array([[10, 10, 20, 20]], dtype=np.int64)
        self.assertEqual(cp.grow_boxes(boxes, np.array([[15.0, 15.0]]), 0.0, 1.0).tolist(), boxes.tolist())

    def test_reach_is_the_worst_shift_and_the_largest_stretch(self):
        affines = [
            np.array([[1.0, 0.0, 3.0], [0.0, 1.0, -1.0]]),  # 3 px of shift
            np.array([[1.02, 0.0, 0.0], [0.0, 1.0, 0.0]]),  # 2 % stretch (and a shift that grows with x)
        ]
        centers = np.array([[100.0, 100.0], [200.0, 100.0]])
        shift, stretch = cp.affine_reach(centers, affines)
        self.assertAlmostEqual(stretch, 1.02, places=6)
        self.assertAlmostEqual(shift, 4.0, places=6)  # the stretch moves x=200 by 4 px, more than the 3 px shift

    def test_grown_boxes_contain_the_exact_per_affine_union(self):
        """The property that lets the cheap version replace the exact one: it is never smaller. A box a pixel too
        big costs a chunk or a slightly wider crop margin; one a pixel too small cuts into a measurement."""
        rng = np.random.default_rng(7)
        rois = self.rois()
        centers = np.array([(r.center_x, r.center_y) for r in rois], dtype=float)
        huge = (1 << 30, 1 << 30)
        for trial in range(12):
            affines = []
            for _ in range(rng.integers(1, 9)):
                m = np.eye(2, 3)
                m[:, :2] += rng.normal(0.0, 3e-3, (2, 2))  # a fitted chromatic affine: near-identity
                m[:, 2] = rng.normal(0.0, 2.5, 2)
                affines.append(m)
            with self.subTest(trial=trial):
                exact = cp.roi_read_boxes(rois, huge, default_inner_diameter_px=49.5,
                                          default_outer_diameter_px=59.9, affines=affines)
                base = cp.roi_read_boxes(rois, huge, default_inner_diameter_px=49.5, default_outer_diameter_px=59.9)
                grown = cp.grow_boxes(base, centers, *cp.affine_reach(centers, affines))
                self.assertTrue((grown[:, 0] <= exact[:, 0]).all(), "left edge must not cut into the union")
                self.assertTrue((grown[:, 1] <= exact[:, 1]).all())
                self.assertTrue((grown[:, 2] >= exact[:, 2]).all())
                self.assertTrue((grown[:, 3] >= exact[:, 3]).all())

    def test_empty_boxes_and_a_length_mismatch_are_left_alone(self):
        boxes = np.array([[0, 0, 0, 0], [10, 10, 20, 20]], dtype=np.int64)
        grown = cp.grow_boxes(boxes, np.array([[0.0, 0.0], [15.0, 15.0]]), 2.0, 1.0)
        self.assertEqual(grown[0].tolist(), [0, 0, 0, 0])  # a ROI with no box keeps none
        self.assertEqual(grown[1].tolist(), [8, 8, 22, 22])
        self.assertEqual(cp.grow_boxes(boxes, np.array([[15.0, 15.0]]), 2.0, 1.0).tolist(), boxes.tolist())


class LatticeCropTests(unittest.TestCase):
    """`lattice_crop`: the rectangle the Image tools' Auto crop keeps (half a lattice constant of border)."""

    @staticmethod
    def centres(rows: int, cols: int, pitch: float, x0: float = 100.0, y0: float = 80.0, tilt_deg: float = 0.0) -> np.ndarray:
        t = np.deg2rad(tilt_deg)
        return np.array(
            [[x0 + pitch * (c * np.cos(t) - r * np.sin(t)), y0 + pitch * (c * np.sin(t) + r * np.cos(t))]
             for r in range(rows) for c in range(cols)],
            dtype=float,
        )

    @staticmethod
    def boxes(centres: np.ndarray, reach: float) -> np.ndarray:
        return np.column_stack((centres[:, 0] - reach, centres[:, 1] - reach,
                                centres[:, 0] + reach, centres[:, 1] + reach)).astype(np.int64)

    def test_margin_is_half_the_pitch_and_the_size_is_a_whole_lattice(self):
        centres = self.centres(3, 5, 80.0)
        crop = cp.lattice_crop(centres, (600, 900), boxes=self.boxes(centres, 20.0), pitch=(80.0, 80.0))
        self.assertEqual((crop.x, crop.y), (60, 40))  # 100 - 40, 80 - 40
        self.assertEqual((crop.width, crop.height), (5 * 80, 3 * 80))  # cols x pitch, rows x pitch
        self.assertEqual((crop.cols, crop.rows), (5, 3))
        self.assertEqual((crop.margin_x, crop.margin_y), (40, 40))
        self.assertFalse(crop.widened or crop.clamped)
        self.assertAlmostEqual(crop.kept_fraction, (400 * 240) / (900 * 600))

    def test_the_kept_rectangle_aligns_a_pitch_sized_chunk_grid_from_its_own_corner(self):
        """Why half a pitch: inside the crop, chunks of one pitch need no grid offset and hold one ROI each."""
        centres = self.centres(3, 5, 80.0)
        crop = cp.lattice_crop(centres, (600, 900), boxes=self.boxes(centres, 20.0), pitch=(80.0, 80.0))
        local = centres - np.array([crop.x, crop.y], dtype=float)  # same pixels, counted from the crop's corner
        boxes = np.array([[cx - 20, cy - 20, cx + 20, cy + 20] for cx, cy in local], dtype=np.int64)
        layout = cp.align_grid(boxes, 80, 80)
        self.assertEqual((layout.offset_x, layout.offset_y), (0, 0))
        self.assertEqual(cp.score_layout(boxes, layout)["single"].chunks, 1.0)
        frame = cp.export_frame(layout, boxes, (crop.height, crop.width), lossless=True)
        self.assertEqual((frame.padded_px, frame.discarded_px), (0, 0))  # nothing padded, nothing trimmed
        self.assertEqual(frame.chunks_per_plane, 15)  # one chunk per ROI, no more

    def test_a_read_box_reaching_further_than_half_the_pitch_widens_the_margin(self):
        """The measured case: a 79 px pitch with a read box reaching ~43 px (reference ring plus the chromatic
        shift). Cropping at half the pitch would cut into the edge ROIs, so the margin grows instead."""
        centres = self.centres(2, 4, 79.0)
        crop = cp.lattice_crop(centres, (600, 900), boxes=self.boxes(centres, 43.0), pitch=(79.0, 79.0))
        self.assertTrue(crop.widened)
        self.assertEqual((crop.margin_x, crop.margin_y), (43, 43))  # the box's reach, not 39.5
        self.assertEqual(crop.x, 100 - 43)

    def test_the_reach_is_per_axis_and_only_from_real_boxes(self):
        """A wide, flat read box (a free-form mask, say) widens the crop sideways only; an empty box is ignored."""
        centres = self.centres(2, 3, 60.0)
        boxes = self.boxes(centres, 20.0)
        boxes[0] = (centres[0, 0] - 50, centres[0, 1] - 10, centres[0, 0] + 50, centres[0, 1] + 10)
        boxes[-1] = (0, 0, 0, 0)  # a ROI off the image: no box, so no say in the margin
        crop = cp.lattice_crop(centres, (600, 900), boxes=boxes, pitch=(60.0, 60.0))
        self.assertEqual((crop.margin_x, crop.margin_y), (50, 30))  # x from the wide box, y from half the pitch

    def test_a_box_count_that_does_not_match_the_centres_is_ignored(self):
        centres = self.centres(2, 3, 60.0)
        crop = cp.lattice_crop(centres, (600, 900), boxes=self.boxes(centres, 99.0)[:2], pitch=(60.0, 60.0))
        self.assertEqual((crop.margin_x, crop.margin_y), (30, 30))
        self.assertFalse(crop.widened)

    def test_the_image_edge_clamps_the_margin_and_is_reported(self):
        crop = cp.lattice_crop(self.centres(2, 3, 80.0, x0=10.0, y0=10.0), (200, 400), pitch=(80.0, 80.0))
        self.assertEqual((crop.x, crop.y), (0, 0))
        self.assertTrue(crop.clamped)
        self.assertEqual(crop.width, 170 + 40)  # last centre 170, full margin on the right only

    def test_without_a_given_pitch_it_is_estimated_and_a_rowless_layout_gives_none(self):
        crop = cp.lattice_crop(self.centres(3, 4, 60.0), (500, 500))
        self.assertAlmostEqual(crop.pitch_x, 60.0)
        self.assertEqual((crop.width, crop.height), (240, 180))
        self.assertIsNone(cp.lattice_crop(np.array([[10.0, 10.0], [70.0, 10.0], [130.0, 10.0], [190.0, 10.0]]), (500, 500)))
        self.assertIsNone(cp.lattice_crop(np.zeros((0, 2)), (500, 500)))
        self.assertIsNone(cp.lattice_crop(self.centres(2, 2, 60.0), (500, 500), pitch=(0.0, 60.0)))

    def test_lattice_tilt_is_measured_from_the_centres(self):
        for tilt in (0.0, 0.3, -1.4, 3.0):
            with self.subTest(tilt=tilt):
                centres = self.centres(4, 6, 70.0, tilt_deg=tilt)
                self.assertAlmostEqual(cp.lattice_tilt_deg(centres), tilt, delta=0.02)
                # Same answer from the pitch the caller already knows, and from a single row of ROIs.
                self.assertAlmostEqual(cp.lattice_tilt_deg(centres, pitch=(70.0, 70.0)), tilt, delta=0.02)
        single_column = self.centres(4, 1, 70.0, tilt_deg=2.0)
        self.assertAlmostEqual(cp.lattice_tilt_deg(single_column, pitch=(70.0, 70.0)), 2.0, delta=0.02)
        self.assertIsNone(cp.lattice_tilt_deg(self.centres(1, 1, 70.0), pitch=(70.0, 70.0)))
        self.assertIsNone(cp.lattice_tilt_deg(self.centres(4, 6, 70.0)[:3]))  # too few for a pitch


class CompressionTests(unittest.TestCase):
    def test_ratio_follows_the_multiple_of_8_rule_on_16_bit_data(self):
        rng = np.random.default_rng(0)
        plane = (1500 + rng.normal(0, 25, (320, 320))).astype(np.uint16)
        try:
            good = cp.estimate_compression_ratio(plane, 80, 80)
        except ImportError:  # pragma: no cover - numcodecs is an optional export dependency
            self.skipTest("numcodecs not installed")
        self.assertLess(good, 0.85)
        self.assertGreater(cp.estimate_compression_ratio(plane, 79, 79), 0.95)
        self.assertEqual(cp.estimate_compression_ratio(plane, 80, 80, cname=None), 1.0)
        both = cp.estimate_compression_ratio([plane, plane], 80, 80)
        self.assertAlmostEqual(both, good, places=6)


if __name__ == "__main__":
    unittest.main()
