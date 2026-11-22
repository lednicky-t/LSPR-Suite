"""Which line is drawn over which (2026-11-22, `panels/sensorgram/draw_order.py`): pure rules, no Qt.

Pinned: Auto puts the roughest line at the back and the smoothest in front (ties keep the own order, the later line in front); Legend = the first legend row in front; Manual = the first
row in front and a new line in front of the listed ones; the table's rows; the four moves (top / up / down / bottom) with several rows selected, and `can_move`.
"""

from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()
APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.panels.sensorgram.draw_order import (  # noqa: E402
    DRAW_AUTO,
    DRAW_LEGEND,
    DRAW_MANUAL,
    MOVE_BOTTOM,
    MOVE_DOWN,
    MOVE_TOP,
    MOVE_UP,
    back_to_front,
    can_move,
    manual_rows,
    moved,
    roughness,
)

KEYS = ["a", "b", "c", "d"]


class RoughnessTest(unittest.TestCase):
    def test_a_ramp_is_smooth_and_jitter_is_not(self) -> None:
        self.assertEqual(roughness([1.0, 2.0, 3.0, 4.0, 5.0]), 0.0)
        self.assertAlmostEqual(roughness([0.0, 1.0, 0.0, 1.0, 0.0]), 2.0)

    def test_a_step_edge_does_not_count_but_noise_does(self) -> None:
        step = [0.0] * 10 + [5.0] * 10
        self.assertEqual(roughness(step), 0.0)  # one jump: the median ignores it

    def test_too_few_or_non_finite_points_score_zero(self) -> None:
        self.assertEqual(roughness([1.0, 2.0]), 0.0)
        self.assertEqual(roughness([math.nan, math.nan, math.nan]), 0.0)
        self.assertAlmostEqual(roughness([0.0, math.nan, 1.0, 0.0, 1.0]), 2.0)  # NaN is skipped, not a point


class ModesTest(unittest.TestCase):
    def test_auto_has_the_roughest_at_the_back_and_ties_keep_the_later_line_in_front(self) -> None:
        self.assertEqual(back_to_front(KEYS, DRAW_AUTO, roughness_of=[1.0, 5.0, 0.0, 5.0]), [1, 3, 0, 2])
        self.assertEqual(back_to_front(KEYS, DRAW_AUTO, roughness_of=[0.0, 0.0, 0.0, 0.0]), [0, 1, 2, 3])
        self.assertEqual(back_to_front(KEYS, DRAW_AUTO, roughness_of=[math.nan, 2.0, 0.0, 0.0]), [1, 0, 2, 3])  # no number = smooth

    def test_legend_puts_the_first_row_in_front(self) -> None:
        self.assertEqual(back_to_front(KEYS, DRAW_LEGEND, legend_order=[2, 0, 3, 1]), [1, 3, 0, 2])

    def test_manual_puts_the_first_row_in_front_and_a_new_line_in_front_of_the_listed(self) -> None:
        self.assertEqual(back_to_front(KEYS, DRAW_MANUAL, manual=["c", "a", "b", "d"]), [3, 1, 0, 2])  # front to back c, a, b, d
        # "b" and "d" are not in the list: in front of the listed ones (own order b, d), then c, then a: front to back b, d, c, a
        self.assertEqual(back_to_front(KEYS, DRAW_MANUAL, manual=["c", "a"]), [0, 2, 3, 1])
        self.assertEqual(back_to_front(KEYS, DRAW_MANUAL, manual=["gone", "c"]), [2, 3, 1, 0])  # a name that is not a line is ignored

    def test_manual_with_an_empty_list_is_the_table_in_its_own_order(self) -> None:
        self.assertEqual(back_to_front(KEYS, DRAW_MANUAL), [3, 2, 1, 0])  # the first row (line 0) in front, as the table shows it
        self.assertEqual(back_to_front([], DRAW_MANUAL, manual=["a"]), [])

    def test_legend_and_auto_with_nothing_to_go_by_draw_the_own_order(self) -> None:
        self.assertEqual(back_to_front(KEYS, DRAW_LEGEND), [0, 1, 2, 3])
        self.assertEqual(back_to_front(KEYS, DRAW_AUTO), [0, 1, 2, 3])


class TableRowsTest(unittest.TestCase):
    def test_new_lines_first_then_the_saved_list_without_the_gone(self) -> None:
        self.assertEqual(manual_rows(KEYS, []), KEYS)
        self.assertEqual(manual_rows(KEYS, ["c", "gone", "a"]), ["b", "d", "c", "a"])
        self.assertEqual(manual_rows(["a", "a", "b"], ["b"]), ["a", "b"])  # one row per name


class MovesTest(unittest.TestCase):
    ROWS = ["a", "b", "c", "d", "e"]

    def test_top_and_bottom_keep_the_selected_rows_in_their_order(self) -> None:
        self.assertEqual(moved(self.ROWS, {"d", "b"}, MOVE_TOP), ["b", "d", "a", "c", "e"])
        self.assertEqual(moved(self.ROWS, {"d", "b"}, MOVE_BOTTOM), ["a", "c", "e", "b", "d"])

    def test_up_and_down_move_one_place_and_a_block_stays_together(self) -> None:
        self.assertEqual(moved(self.ROWS, {"c"}, MOVE_UP), ["a", "c", "b", "d", "e"])
        self.assertEqual(moved(self.ROWS, {"c", "d"}, MOVE_UP), ["a", "c", "d", "b", "e"])
        self.assertEqual(moved(self.ROWS, {"b", "c"}, MOVE_DOWN), ["a", "d", "b", "c", "e"])

    def test_a_row_at_the_end_stays_and_can_move_says_so(self) -> None:
        self.assertEqual(moved(self.ROWS, {"a"}, MOVE_UP), self.ROWS)
        self.assertFalse(can_move(self.ROWS, {"a"}, MOVE_UP))
        self.assertFalse(can_move(self.ROWS, {"a"}, MOVE_TOP))
        self.assertFalse(can_move(self.ROWS, {"e"}, MOVE_BOTTOM))
        self.assertTrue(can_move(self.ROWS, {"a"}, MOVE_DOWN))
        self.assertFalse(can_move(self.ROWS, set(), MOVE_DOWN))
        self.assertEqual(moved(self.ROWS, {"a", "b"}, MOVE_UP), self.ROWS)  # the block is already at the top


if __name__ == "__main__":
    unittest.main()
