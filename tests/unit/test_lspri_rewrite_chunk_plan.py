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

    def test_lattice_groups_rows(self):
        centres = np.array([[10, 10], [50, 11], [10, 52], [51, 50], [90, 9]], dtype=float)
        self.assertEqual(cp.lattice_groups(centres, axis="rows", tolerance_px=20.0), [[0, 1, 4], [2, 3]])


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
