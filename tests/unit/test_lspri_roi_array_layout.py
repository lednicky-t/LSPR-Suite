"""Manual array layout (`roi/array_layout.py`): where the copies of the selected ROI(s) go.

Pure geometry, no Qt, no files. Pins the definitions written in the module docstring: start corner, additive
pitch increment counted from the seed, triangular row shift, clockwise angle about the seed, the one
*regular cell* switch (square / 60-degree triangles), and units of several ROIs placed by their centroid.
"""

from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.roi.array_layout import ArraySpec, array_layout, numbering_order
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

TOL = 1e-9


def _grid(layout) -> np.ndarray:
    """Node positions as a (rows, cols, 2) array."""
    return layout.node_xy.reshape(layout.rows, layout.cols, 2)


class RectangularTests(unittest.TestCase):
    def test_top_left_start_grows_right_and_down(self) -> None:
        layout = array_layout(ArraySpec(rows=2, cols=3, pitch_x_px=10.0, pitch_y_px=20.0), [(100.0, 200.0)])
        grid = _grid(layout)
        np.testing.assert_allclose(grid[0, 0], (100.0, 200.0), atol=TOL)
        np.testing.assert_allclose(grid[0, 2], (120.0, 200.0), atol=TOL)
        np.testing.assert_allclose(grid[1, 0], (100.0, 220.0), atol=TOL)
        self.assertEqual((layout.seed_row, layout.seed_col), (0, 0))

    def test_bottom_right_start_grows_left_and_up(self) -> None:
        layout = array_layout(ArraySpec(rows=2, cols=3, pitch_x_px=10.0, pitch_y_px=20.0, start_x=1.0, start_y=1.0), [(100.0, 200.0)])
        grid = _grid(layout)
        np.testing.assert_allclose(grid[1, 2], (100.0, 200.0), atol=TOL)
        np.testing.assert_allclose(grid[0, 0], (80.0, 180.0), atol=TOL)
        self.assertEqual((layout.seed_row, layout.seed_col), (1, 2))

    def test_middle_start_odd_and_even_counts(self) -> None:
        odd = array_layout(ArraySpec(rows=3, cols=3, pitch_x_px=10.0, pitch_y_px=10.0, start_x=0.5, start_y=0.5), [(0.0, 0.0)])
        self.assertEqual((odd.seed_row, odd.seed_col), (1, 1))
        np.testing.assert_allclose(_grid(odd)[0, 0], (-10.0, -10.0), atol=TOL)
        even = array_layout(ArraySpec(rows=4, cols=4, pitch_x_px=10.0, pitch_y_px=10.0, start_x=0.5, start_y=0.5), [(0.0, 0.0)])
        self.assertEqual((even.seed_row, even.seed_col), (1, 1))  # the lower-index node of the two middle ones

    def test_seed_node_is_exactly_the_selection(self) -> None:
        seed = [(12.345678901234, 98.7654321098)]
        layout = array_layout(ArraySpec(rows=3, cols=3, pitch_x_px=7.3, pitch_y_px=7.3, angle_deg=17.0, start_x=0.5, start_y=0.5), seed)
        self.assertTrue(np.array_equal(layout.member_xy[layout.seed_node, 0], np.array(seed[0])))

    def test_new_members_leave_out_the_seed(self) -> None:
        layout = array_layout(ArraySpec(rows=2, cols=3), [(0.0, 0.0), (5.0, 0.0)])
        self.assertEqual(layout.new_member_xy().shape, (5 * 2, 2))


class IncrementTests(unittest.TestCase):
    """The pitch increment is a factor (1 = constant): the gap k steps from the seed is pitch * factor ** k."""

    def test_default_is_a_constant_pitch(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=4, pitch_x_px=10.0), [(0.0, 0.0)])
        np.testing.assert_allclose(_grid(layout)[0, :, 0], [0.0, 10.0, 20.0, 30.0], atol=TOL)

    def test_gaps_grow_with_distance_from_seed(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=4, pitch_x_px=10.0, pitch_factor_x=1.5), [(0.0, 0.0)])
        np.testing.assert_allclose(_grid(layout)[0, :, 0], [0.0, 10.0, 25.0, 47.5], atol=TOL)  # gaps 10, 15, 22.5

    def test_factor_below_one_shrinks_the_gaps(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=3, pitch_x_px=10.0, pitch_factor_x=0.5), [(0.0, 0.0)])
        np.testing.assert_allclose(_grid(layout)[0, :, 0], [0.0, 10.0, 15.0], atol=TOL)  # gaps 10, 5

    def test_middle_seed_grows_outward_on_both_sides(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=5, pitch_x_px=10.0, pitch_factor_x=2.0, start_x=0.5), [(0.0, 0.0)])
        np.testing.assert_allclose(_grid(layout)[0, :, 0], [-30.0, -10.0, 0.0, 10.0, 30.0], atol=TOL)

    def test_seed_at_the_far_end_grows_the_other_way(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=3, pitch_x_px=10.0, pitch_factor_x=2.0, start_x=1.0), [(0.0, 0.0)])
        np.testing.assert_allclose(_grid(layout)[0, :, 0], [-30.0, -10.0, 0.0], atol=TOL)

    def test_x_and_y_are_independent(self) -> None:
        layout = array_layout(ArraySpec(rows=3, cols=3, pitch_x_px=10.0, pitch_y_px=10.0, pitch_factor_y=2.0), [(0.0, 0.0)])
        grid = _grid(layout)
        np.testing.assert_allclose(grid[0, :, 0], [0.0, 10.0, 20.0], atol=TOL)
        np.testing.assert_allclose(grid[:, 0, 1], [0.0, 10.0, 30.0], atol=TOL)

    def test_a_factor_of_zero_or_less_is_refused(self) -> None:
        for factor in (0.0, -1.0):
            with self.subTest(factor=factor), self.assertRaisesRegex(ValueError, "increment x"):
                array_layout(ArraySpec(rows=1, cols=6, pitch_x_px=10.0, pitch_factor_x=factor), [(0.0, 0.0)])


class AngleTests(unittest.TestCase):
    def test_angle_is_clockwise_on_screen_about_the_seed(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=2, pitch_x_px=10.0, angle_deg=90.0), [(5.0, 5.0)])
        grid = _grid(layout)
        np.testing.assert_allclose(grid[0, 0], (5.0, 5.0), atol=TOL)
        np.testing.assert_allclose(grid[0, 1], (5.0, 15.0), atol=1e-9)  # +x turned clockwise on screen = down (+y)

    def test_angle_keeps_pitch(self) -> None:
        layout = array_layout(ArraySpec(rows=3, cols=3, pitch_x_px=10.0, pitch_y_px=14.0, angle_deg=23.0), [(0.0, 0.0)])
        grid = _grid(layout)
        self.assertAlmostEqual(float(np.linalg.norm(grid[0, 1] - grid[0, 0])), 10.0, places=9)
        self.assertAlmostEqual(float(np.linalg.norm(grid[1, 0] - grid[0, 0])), 14.0, places=9)


class TriangularTests(unittest.TestCase):
    def test_default_row_shift_is_half_a_pitch(self) -> None:
        layout = array_layout(ArraySpec(kind="triangular", rows=3, cols=2, pitch_x_px=10.0, pitch_y_px=8.0), [(0.0, 0.0)])
        grid = _grid(layout)
        np.testing.assert_allclose(grid[:, 0, 0], [0.0, 5.0, 0.0], atol=TOL)

    def test_shift_follows_growth_direction(self) -> None:
        layout = array_layout(ArraySpec(kind="triangular", rows=2, cols=2, pitch_x_px=10.0, pitch_y_px=8.0, start_x=1.0), [(0.0, 0.0)])
        np.testing.assert_allclose(_grid(layout)[1, 1, 0], -5.0, atol=TOL)  # seed at the right edge: shifted left

    def test_shift_counts_from_the_seed_row(self) -> None:
        layout = array_layout(ArraySpec(kind="triangular", rows=3, cols=1, pitch_x_px=10.0, pitch_y_px=8.0, start_y=1.0), [(0.0, 0.0)])
        np.testing.assert_allclose(_grid(layout)[:, 0, 0], [0.0, 5.0, 0.0], atol=TOL)  # seed row = last; the one above it is shifted

    def test_regular_cell_gives_60_degree_triangles(self) -> None:
        layout = array_layout(ArraySpec(kind="triangular", rows=2, cols=2, pitch_x_px=10.0, pitch_y_px=99.0, row_offset=0.1, regular_cell=True), [(0.0, 0.0)])
        grid = _grid(layout)
        edges = (grid[0, 1] - grid[0, 0], grid[1, 0] - grid[0, 0], grid[1, 0] - grid[0, 1])
        for edge in edges:
            self.assertAlmostEqual(float(np.linalg.norm(edge)), 10.0, places=9)  # equilateral


class RegularCellTests(unittest.TestCase):
    def test_rectangular_regular_cell_is_square_including_the_increment(self) -> None:
        spec = ArraySpec(rows=3, cols=3, pitch_x_px=10.0, pitch_y_px=99.0, pitch_factor_x=1.2, pitch_factor_y=3.0, regular_cell=True)
        resolved = spec.resolved()
        self.assertEqual((resolved.pitch_y_px, resolved.pitch_factor_y, resolved.row_offset), (10.0, 1.2, 0.0))
        grid = _grid(array_layout(spec, [(0.0, 0.0)]))
        np.testing.assert_allclose(grid[:, 0, 1], grid[0, :, 0], atol=TOL)

    def test_triangular_regular_cell_keeps_the_shape_with_an_increment(self) -> None:
        resolved = ArraySpec(kind="triangular", pitch_x_px=10.0, pitch_factor_x=1.5, regular_cell=True).resolved()
        self.assertAlmostEqual(resolved.pitch_y_px, 10.0 * math.sqrt(3.0) / 2.0, places=12)
        self.assertEqual(resolved.pitch_factor_y, 1.5)  # same factor on both axes: every step stays similar to the first
        self.assertEqual(resolved.row_offset, 0.5)


class UnitTests(unittest.TestCase):
    def test_unit_keeps_its_arrangement_and_sits_by_centroid(self) -> None:
        seed = [(0.0, 0.0), (4.0, 0.0)]  # centroid (2, 0)
        layout = array_layout(ArraySpec(rows=1, cols=2, pitch_x_px=10.0), seed)
        np.testing.assert_allclose(layout.node_xy[1], (12.0, 0.0), atol=TOL)
        np.testing.assert_allclose(layout.member_xy[1], [(10.0, 0.0), (14.0, 0.0)], atol=TOL)
        np.testing.assert_array_equal(layout.member_xy[0], np.array(seed))

    def test_unit_is_not_turned_with_the_lattice(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=2, pitch_x_px=10.0, angle_deg=90.0), [(0.0, 0.0), (4.0, 0.0)])
        offsets = layout.member_xy[1, 1] - layout.member_xy[1, 0]
        np.testing.assert_allclose(offsets, (4.0, 0.0), atol=TOL)


class NumberingTests(unittest.TestCase):
    """Row-major node indices (row * cols + col) in numbering order; a 2 x 3 array is  0 1 2 / 3 4 5."""

    def _order(self, **kwargs) -> list[int]:
        return numbering_order(ArraySpec(rows=2, cols=3, **kwargs), 2, 3)

    def test_the_four_row_wise_orders(self) -> None:
        self.assertEqual(self._order(), [0, 1, 2, 3, 4, 5])  # rows, from the left, top row first
        self.assertEqual(self._order(number_start_x=1.0), [2, 1, 0, 5, 4, 3])  # from the right
        self.assertEqual(self._order(number_start_y=1.0), [3, 4, 5, 0, 1, 2])  # bottom row first
        self.assertEqual(self._order(number_start_x=1.0, number_start_y=1.0), [5, 4, 3, 2, 1, 0])

    def test_the_four_column_wise_orders(self) -> None:
        self.assertEqual(self._order(number_by="columns"), [0, 3, 1, 4, 2, 5])  # columns, from the top, left column first
        self.assertEqual(self._order(number_by="columns", number_start_y=1.0), [3, 0, 4, 1, 5, 2])  # from the bottom
        self.assertEqual(self._order(number_by="columns", number_start_x=1.0), [2, 5, 1, 4, 0, 3])  # right column first
        self.assertEqual(self._order(number_by="columns", number_start_x=1.0, number_start_y=1.0), [5, 2, 4, 1, 3, 0])

    def test_every_node_once(self) -> None:
        for by in ("rows", "columns"):
            for sx in (0.0, 1.0):
                for sy in (0.0, 1.0):
                    self.assertEqual(sorted(self._order(number_by=by, number_start_x=sx, number_start_y=sy)), list(range(6)))

    def test_bad_numbering_is_refused(self) -> None:
        for kwargs in ({"number_by": "diagonal"}, {"number_start_x": 0.5}, {"number_start_y": 2.0}):
            with self.subTest(**kwargs), self.assertRaisesRegex(ValueError, "numbering"):
                array_layout(ArraySpec(**kwargs), [(0.0, 0.0)])


class ValidationTests(unittest.TestCase):
    def test_refusals(self) -> None:
        seed = [(0.0, 0.0)]
        for spec, text in (
            (ArraySpec(rows=0), "at least 1 row"),
            (ArraySpec(pitch_x_px=0.0), "pitch x"),
            (ArraySpec(pitch_y_px=-1.0), "pitch y"),
            (ArraySpec(pitch_factor_y=0.0), "increment y"),
            (ArraySpec(angle_deg=float("nan")), "finite"),
            (ArraySpec(kind="hexagonal"), "unknown lattice"),
            (ArraySpec(start_x=0.3), "start corner"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, text):
                array_layout(spec, seed)
        with self.assertRaisesRegex(ValueError, "select at least one"):
            array_layout(ArraySpec(), [])

    def test_one_by_one_array_is_just_the_seed(self) -> None:
        layout = array_layout(ArraySpec(rows=1, cols=1), [(3.0, 4.0)])
        self.assertEqual(layout.new_member_xy().shape, (0, 2))


if __name__ == "__main__":
    unittest.main()
