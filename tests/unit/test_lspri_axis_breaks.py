"""Axis breaks: the data <-> drawn x mapping (`domain/axis_breaks.py`), 2026-11-27.

Pure arithmetic, no widgets. What is pinned: the mapping is increasing and continuous and its inverse is exact everywhere (also inside a break); the gap is the same small size however much
is removed; points inside a break are the ones not drawn; breaks are sorted, merged and kept inside the data; the segments and gaps the axis draws its ticks and marks from; and a plot
with no break is the identity.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.axis_breaks import GAP_FRACTION, AxisBreak, BreakMap, normalized
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

EXTENT = (0.0, 1000.0)


def _map(*pairs: tuple[float, float]) -> BreakMap:
    return BreakMap([AxisBreak(a, b) for a, b in pairs], EXTENT)


class BreakMapTest(unittest.TestCase):
    def test_no_break_is_the_identity(self) -> None:
        bm = BreakMap()
        self.assertFalse(bm.active)
        x = np.array([0.0, 5.5, 1000.0])
        np.testing.assert_array_equal(bm.forward(x), x)
        np.testing.assert_array_equal(bm.inverse(x), x)
        self.assertEqual(bm.forward(3.0), 3.0)
        self.assertFalse(bm.inside(3.0))
        self.assertTrue(BreakMap([AxisBreak(1.0, 2.0)], None).forward(1.5) == 1.5)  # no extent: no gap size, so nothing is drawn broken

    def test_the_mapping_is_increasing_and_continuous_and_the_inverse_is_exact(self) -> None:
        bm = _map((100.0, 300.0), (500.0, 520.0), (900.0, 950.0))
        x = np.linspace(-20.0, 1020.0, 20001)
        v = bm.forward(x)
        self.assertTrue(np.all(np.diff(v) >= 0.0))
        self.assertLess(float(np.max(np.diff(v))), 0.2)  # no jump anywhere (steps of 0.052 in x)
        np.testing.assert_allclose(bm.inverse(v), x, atol=1e-9)  # also for x inside a break
        np.testing.assert_allclose(bm.forward(bm.inverse(v)), v, atol=1e-9)

    def test_a_break_removes_its_length_and_leaves_one_small_gap(self) -> None:
        bm = _map((100.0, 300.0))
        self.assertAlmostEqual(bm.gap, (1000.0 - 200.0) * GAP_FRACTION)
        self.assertAlmostEqual(bm.forward(100.0), 100.0)  # before the break: untouched
        self.assertAlmostEqual(bm.forward(300.0), 100.0 + bm.gap)  # the far side of the gap
        self.assertAlmostEqual(bm.forward(400.0), 200.0 + bm.gap)  # 100 further than the far side
        lo, hi = bm.drawn_extent()
        self.assertAlmostEqual(hi - lo, 800.0 + bm.gap)

    def test_the_gap_is_the_same_size_whatever_the_removed_stretch(self) -> None:
        small, large = _map((100.0, 110.0)), _map((100.0, 900.0))
        for bm in (small, large):
            lo, hi = bm.drawn_extent()
            kept = (1000.0 - sum(b.end - b.start for b in bm.breaks))
            self.assertAlmostEqual(bm.gap / kept, GAP_FRACTION)
            self.assertAlmostEqual((hi - lo) - bm.gap, kept)

    def test_points_inside_a_break_are_the_ones_left_out(self) -> None:
        bm = _map((100.0, 300.0))
        x = np.array([99.9, 100.0, 100.1, 200.0, 299.9, 300.0, 300.1, np.nan])
        np.testing.assert_array_equal(bm.inside(x), [False, False, True, True, True, False, False, False])  # the ends belong to the data around the break
        self.assertTrue(bm.inside(150.0))
        self.assertTrue(np.isnan(bm.forward(np.nan)))

    def test_the_gap_is_the_removed_stretch_squeezed_in_proportion(self) -> None:
        bm = _map((100.0, 300.0))
        start, end = bm.gaps()[0]
        self.assertAlmostEqual(bm.forward(200.0), (start + end) / 2.0)  # the middle of the stretch is the middle of the gap
        self.assertAlmostEqual(bm.inverse((start + end) / 2.0), 200.0)

    def test_arrays_and_scalars_agree(self) -> None:
        bm = _map((100.0, 300.0), (600.0, 700.0))
        x = np.array([50.0, 150.0, 450.0, 650.0, 800.0])
        np.testing.assert_allclose([bm.forward(float(v)) for v in x], bm.forward(x))
        np.testing.assert_allclose([bm.inverse(float(v)) for v in bm.forward(x)], x)
        self.assertIsInstance(bm.forward(1.0), float)

    def test_segments_and_gaps_are_what_the_axis_draws(self) -> None:
        bm = _map((100.0, 300.0), (600.0, 700.0))
        self.assertEqual(bm.segments(), [(0.0, 100.0), (300.0, 600.0), (700.0, 1000.0)])
        gaps = bm.gaps()
        self.assertEqual(len(gaps), 2)
        for (g0, g1), (_a, b) in zip(gaps, [(100.0, 300.0), (600.0, 700.0)]):
            self.assertAlmostEqual(g1 - g0, bm.gap)
        self.assertAlmostEqual(gaps[0][0], bm.forward(100.0))
        self.assertAlmostEqual(gaps[1][1], bm.forward(700.0))
        self.assertEqual(BreakMap().segments(), [])

    def test_the_break_under_the_pointer_can_be_left_out(self) -> None:
        bm = _map((100.0, 300.0), (600.0, 700.0))
        other = bm.without(0)
        self.assertEqual(other.breaks, (AxisBreak(600.0, 700.0),))
        self.assertAlmostEqual(other.forward(100.0), 100.0)  # the ground the first break stood on has no gap in it
        self.assertEqual(bm.break_at(650.0), 1)
        self.assertEqual(bm.break_at(300.0), 0)  # an end belongs to the break
        self.assertIsNone(bm.break_at(450.0))


class NormalizedTest(unittest.TestCase):
    def test_sorted_merged_and_valid(self) -> None:
        got = normalized([AxisBreak(500.0, 600.0), AxisBreak(100.0, 200.0), AxisBreak(150.0, 250.0), AxisBreak(250.0, 260.0), AxisBreak(300.0, 300.0)])
        self.assertEqual(got, (AxisBreak(100.0, 260.0), AxisBreak(500.0, 600.0)))  # overlapping and touching merged, an empty one dropped
        self.assertEqual(normalized([AxisBreak(600.0, 500.0)]), (AxisBreak(500.0, 600.0),))  # ends put in order
        self.assertEqual(normalized([AxisBreak(float("nan"), 3.0), AxisBreak(1.0, float("inf"))]), ())

    def test_a_break_must_lie_inside_the_data(self) -> None:
        got = normalized([AxisBreak(-10.0, 50.0), AxisBreak(100.0, 200.0), AxisBreak(900.0, 1000.0), AxisBreak(2000.0, 3000.0)], EXTENT)
        self.assertEqual(got, (AxisBreak(100.0, 200.0),))  # the first reaches the start, the third the end, the last is off the data: those are ranges, not breaks

    def test_the_payload_round_trips_and_is_read_leniently(self) -> None:
        item = AxisBreak(1.5, 9.0)
        self.assertEqual(AxisBreak.from_payload(item.to_payload()), item)
        self.assertIsNone(AxisBreak.from_payload({"start": "x", "end": 3}))
        self.assertIsNone(AxisBreak.from_payload([1, 2]))
        self.assertEqual(AxisBreak.from_payload({"start": 9, "end": 1.5}), item)


if __name__ == "__main__":
    unittest.main()
