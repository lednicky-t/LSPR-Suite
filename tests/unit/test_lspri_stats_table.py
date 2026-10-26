"""The pure model behind the Sensorgram's step statistics table (`lspri_imaging_app.plan.stats_table`): number format, what fits a narrow step, the table and its tooltips. No Qt."""

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
    from lspr_imaging_app.plan import stats_table as st
    from lspr_imaging_app.plan.bar_model import PlanBarStep
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

NAN = float("nan")


def _bar(index: int, x0: float, x1: float, text: str = "") -> PlanBarStep:
    return PlanBarStep(index=index, x_start=x0, x_end=x1, text=text, color="#ff0000", tooltip="", levels=None)


def _line(label: str, mean, sd, n=None, between=False) -> st.StatsLine:
    return st.StatsLine(label, "#00ff00", tuple(mean), tuple(sd), tuple(n if n is not None else [27] * len(mean)), between)


class DecimalsTest(unittest.TestCase):
    def test_two_significant_digits_of_the_typical_sd(self) -> None:
        self.assertEqual(st.choose_decimals([0.087, 0.09, 0.1]), 3)
        self.assertEqual(st.choose_decimals([0.5]), 2)
        self.assertEqual(st.choose_decimals([5.0]), 1)
        self.assertEqual(st.choose_decimals([0.004]), 4)

    def test_kept_between_one_and_four_and_two_without_any_sd(self) -> None:
        self.assertEqual(st.choose_decimals([500.0]), 1)
        self.assertEqual(st.choose_decimals([1e-7]), 4)
        self.assertEqual(st.choose_decimals([]), 2)
        self.assertEqual(st.choose_decimals([NAN, 0.0, -1.0]), 2)  # NaN, zero and negative are not an SD

    def test_a_few_odd_values_do_not_move_the_median(self) -> None:
        self.assertEqual(st.choose_decimals([0.09, 0.09, 0.09, 40.0]), 3)


class VariantsTest(unittest.TestCase):
    def test_longest_first_then_one_decimal_less_then_the_mean_alone(self) -> None:
        self.assertEqual(st.cell_variants(595.7734, 0.0871, 3), ("595.773 ± 0.087", "595.77 ± 0.09", "595.77"))

    def test_without_an_sd_only_the_mean(self) -> None:
        self.assertEqual(st.cell_variants(595.7734, NAN, 3), ("595.77",))

    def test_without_a_mean_nothing(self) -> None:
        self.assertEqual(st.cell_variants(NAN, 0.1, 3), ())

    def test_one_decimal_has_nothing_to_drop(self) -> None:
        self.assertEqual(st.cell_variants(13.19, 0.99, 1), ("13.2 ± 1.0", "13.2"))

    def test_a_shortened_sd_never_reads_zero(self) -> None:
        self.assertEqual(st.cell_variants(2.007, 0.03, 2), ("2.01 ± 0.03", "2.0"))  # one decimal less would say "2.0 ± 0.0"
        self.assertEqual(st.cell_variants(2.007, 0.004, 3), ("2.007 ± 0.004", "2.01"))
        self.assertEqual(st.cell_variants(2.0, 0.0, 2), ("2.00 ± 0.00", "2.0 ± 0.0", "2.0"))  # a spread of exactly zero is a spread of zero

    def test_the_first_variant_that_fits_is_taken(self) -> None:
        variants = st.cell_variants(595.7734, 0.0871, 3)
        measure = lambda text: 7.0 * len(text)  # noqa: E731 - a stand-in for the font metrics
        self.assertEqual(st.fit_variant(variants, 120.0, measure), "595.773 ± 0.087")
        self.assertEqual(st.fit_variant(variants, 100.0, measure), "595.77 ± 0.09")
        self.assertEqual(st.fit_variant(variants, 45.0, measure), "595.77")
        self.assertEqual(st.fit_variant(variants, 10.0, measure), "")
        self.assertEqual(st.fit_variant((), 500.0, measure), "")


class TableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = [_bar(0, 0.0, 10.0, "H2O"), _bar(1, 10.0, 20.0, ""), _bar(2, 20.0, 30.0, "EG")]
        self.rows = [
            _line("ROI 1", [591.4, 593.5, NAN], [0.14, 0.09, NAN], [32, 27, 0]),
            _line("Average of 2 ROIs", [590.8, 592.8, 595.1], [5.27, 5.28, 5.29], [2, 2, 2], between=True),
        ]

    def test_columns_follow_the_bars_and_the_decimals_the_typical_sd(self) -> None:
        table = st.build_stats_table(self.bars, self.rows)
        self.assertEqual([(c.index, c.x_start, c.x_end) for c in table.columns], [(0, 0.0, 10.0), (1, 10.0, 20.0), (2, 20.0, 30.0)])
        self.assertEqual([c.title for c in table.columns], ["H2O", "Step 2", "EG"])  # a step without a note is named by its number
        self.assertEqual(table.decimals, 1)  # the median SD is 0.3...5.3: the between-ROI spreads are large
        self.assertEqual(table.unit, "nm")

    def test_value_by_line_and_column(self) -> None:
        table = st.build_stats_table(self.bars, self.rows)
        self.assertEqual(table.value(0, table.columns[0]), (591.4, 0.14, 32))
        mean, sd, n = table.value(0, table.columns[2])
        self.assertTrue(math.isnan(mean) and math.isnan(sd))
        self.assertEqual(n, 0)

    def test_a_step_past_the_end_of_a_lines_numbers_has_none(self) -> None:
        short = [_line("ROI 1", [591.4], [0.14], [32])]
        table = st.build_stats_table(self.bars, short)
        self.assertTrue(math.isnan(table.value(0, table.columns[1])[0]))

    def test_nothing_to_write_is_no_table(self) -> None:
        self.assertIsNone(st.build_stats_table([], self.rows))
        self.assertIsNone(st.build_stats_table(self.bars, []))
        self.assertIsNone(st.build_stats_table(self.bars, [_line("ROI 1", [NAN, NAN, NAN], [NAN, NAN, NAN])]))


class TooltipTest(unittest.TestCase):
    def setUp(self) -> None:
        bars = [_bar(0, 0.0, 10.0, "H2O"), _bar(1, 10.0, 20.0, "EG")]
        rows = [
            _line("ROI 1", [591.4123, NAN], [0.1412, NAN], [32, 0]),
            _line("Group A", [590.8, 592.8], [5.27, NAN], [160, 1], between=True),
        ]
        self.table = st.build_stats_table(bars, rows)

    def test_a_roi_line_says_the_sd_is_of_its_cubes(self) -> None:
        text = st.cell_tooltip(self.table, 0, self.table.columns[0])
        self.assertIn("ROI 1 - H2O", text)
        self.assertIn("mean 591.412 nm", text)  # by its own SD (0.14): two significant digits, plus one more
        self.assertIn("SD of the 32 cubes in the step 0.141 nm", text)

    def test_a_group_line_says_the_sd_is_between_its_rois(self) -> None:
        text = st.cell_tooltip(self.table, 1, self.table.columns[0])
        self.assertIn("SD between the 160 ROIs of this line", text)
        self.assertIn("own mean in the step", text)

    def test_a_single_roi_group_has_no_spread(self) -> None:
        text = st.cell_tooltip(self.table, 1, self.table.columns[1])
        self.assertIn("1 ROI: no spread between ROIs", text)

    def test_a_cell_without_a_value(self) -> None:
        self.assertIn("no value in this step", st.cell_tooltip(self.table, 0, self.table.columns[1]))


if __name__ == "__main__":
    unittest.main()
