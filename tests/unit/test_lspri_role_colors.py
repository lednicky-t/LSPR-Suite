"""The colours of the evaluation chips on the plan bar (`plan/role_colors.py`, 2026-11-13): one colour per pair with the baseline darker, and a grey scale for the S_bulk chip.
Pure logic, no Qt."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.plan import role_colors as rc
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


class ParseRoleTest(unittest.TestCase):
    def test_a_chip_is_a_baseline_a_signal_the_bulk_chip_or_something_else(self) -> None:
        self.assertEqual(rc.parse_role("B1"), ("B", 1))
        self.assertEqual(rc.parse_role("S2"), ("S", 2))
        self.assertEqual(rc.parse_role("B12"), ("B", 12))
        self.assertEqual(rc.parse_role("Sb"), ("Sb", 0))  # not "S" + "b": the S_bulk chip is its own kind
        for other in ("", "X1", "S", "Bx", "b1"):
            self.assertEqual(rc.parse_role(other), ("", 0), other)


class PairColourTest(unittest.TestCase):
    def test_the_palette_has_six_distinct_colours_and_starts_again_after_the_sixth(self) -> None:
        self.assertEqual(len(rc.PAIR_PALETTE), 6)
        self.assertEqual(len(set(rc.PAIR_PALETTE)), 6)
        self.assertEqual([rc.pair_color(n) for n in (1, 2, 3)], list(rc.PAIR_PALETTE[:3]))
        self.assertEqual(rc.pair_color(7), rc.pair_color(1))  # "I do not expect more": a seventh pair repeats rather than failing
        self.assertEqual(rc.pair_color(0), rc.pair_color(1))  # never an index error

    def test_a_pair_is_one_colour_and_its_baseline_is_the_same_hue_darker_and_a_little_more_transparent(self) -> None:
        for number in range(1, 7):
            signal, signal_alpha = rc.role_fill(f"S{number}")
            baseline, baseline_alpha = rc.role_fill(f"B{number}")
            self.assertEqual(signal, rc.pair_color(number))  # the signal chip is the pair's colour itself
            base, dark = _rgb(signal), _rgb(baseline)
            self.assertTrue(all(d < s for d, s in zip(dark, base) if s > 8), f"pair {number}: every channel is darker")
            ratios = [d / s for d, s in zip(dark, base) if s > 60]  # the same hue: the channels keep their proportions
            self.assertLess(max(ratios) - min(ratios), 0.03)
            self.assertLess(baseline_alpha, signal_alpha)

    def test_different_pairs_have_different_colours_for_both_chips(self) -> None:
        signals = {rc.role_fill(f"S{n}")[0] for n in range(1, 7)}
        baselines = {rc.role_fill(f"B{n}")[0] for n in range(1, 7)}
        self.assertEqual((len(signals), len(baselines)), (6, 6))

    def test_the_bulk_chip_and_unknown_chips_have_no_pair_colour(self) -> None:
        self.assertIsNone(rc.role_fill("Sb"))  # its grey depends on the step
        self.assertIsNone(rc.role_fill("X"))

    def test_darkening_keeps_a_valid_colour(self) -> None:
        self.assertEqual(rc.darken("#ffffff", 0.5), "#808080")
        self.assertEqual(rc.darken("#000000", 0.5), "#000000")
        self.assertEqual(rc.darken("#ff8040", 1.0), "#ff8040")
        self.assertEqual(rc.darken("#ffffff", 2.0), "#ffffff")  # clipped, never past ff


class BulkGrayTest(unittest.TestCase):
    def test_the_lowest_level_is_dark_the_highest_is_white_and_the_others_in_proportion(self) -> None:
        grays = rc.bulk_grays({4: 600.0, 5: 601.0, 6: 603.0})
        self.assertEqual(grays[4], f"#{rc.GRAY_DARK:02x}{rc.GRAY_DARK:02x}{rc.GRAY_DARK:02x}")
        self.assertEqual(grays[6], "#ffffff")
        level = _rgb(grays[5])[0]
        self.assertEqual(_rgb(grays[5]), (level, level, level))  # a grey: equal channels
        expected = rc.GRAY_DARK + (1.0 / 3.0) * (rc.GRAY_WHITE - rc.GRAY_DARK)  # a third of the way: linear in the level, not in the rank (rank would put it in the middle)
        self.assertAlmostEqual(level, expected, delta=1.0)

    def test_the_order_of_the_greys_is_the_order_of_the_levels_whatever_the_order_of_the_steps(self) -> None:
        grays = rc.bulk_grays({9: 5.0, 2: -1.0, 7: 2.0, 3: 9.0})
        by_level = sorted(grays, key=lambda i: {9: 5.0, 2: -1.0, 7: 2.0, 3: 9.0}[i])
        values = [_rgb(grays[i])[0] for i in by_level]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), 4)

    def test_no_spread_no_level_or_a_single_step_gives_the_middle_grey(self) -> None:
        middle = f"#{rc.GRAY_MIDDLE:02x}{rc.GRAY_MIDDLE:02x}{rc.GRAY_MIDDLE:02x}"
        self.assertEqual(rc.bulk_grays({1: 3.0}), {1: middle})  # one step: nothing to order
        self.assertEqual(rc.bulk_grays({1: 3.0, 2: 3.0}), {1: middle, 2: middle})  # all equal
        grays = rc.bulk_grays({1: 3.0, 2: float("nan"), 3: None, 4: 5.0})
        self.assertEqual((grays[2], grays[3]), (middle, middle))  # no data in the step: the middle, and the others are scaled without it
        self.assertEqual(grays[4], "#ffffff")
        self.assertEqual(rc.bulk_grays({}), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
