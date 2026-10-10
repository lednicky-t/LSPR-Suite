"""`order_by_grid`: reading order of an array (pure function)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()
sys.path.insert(0, str(REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"))

from lspr_imaging_app.roi.ordering import order_by_grid  # noqa: E402

# ids 1..6 scrambled over a 2 x 3 grid (x, y), with a little scatter
IDS = [1, 2, 3, 4, 5, 6]
XY = [(61, 11), (9, 52), (11, 9), (59, 50), (36, 12), (34, 49)]


class OrderByGridTests(unittest.TestCase):
    def test_rows_start_top_left_and_run_left_to_right(self) -> None:
        self.assertEqual(order_by_grid(IDS, XY, 8.0), [3, 5, 1, 2, 6, 4])

    def test_columns_start_top_left_and_run_top_to_bottom(self) -> None:
        self.assertEqual(order_by_grid(IDS, XY, 8.0, column_major=True), [3, 2, 5, 6, 1, 4])

    def test_a_single_roi_and_empty_input(self) -> None:
        self.assertEqual(order_by_grid([7], [(1.0, 2.0)], 5.0), [7])
        self.assertEqual(order_by_grid([], [], 5.0), [])

    def test_mismatched_lengths_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            order_by_grid([1, 2], [(0.0, 0.0)], 5.0)


if __name__ == "__main__":
    unittest.main()
