"""`panels/roi_table/number_format.py` (2026-11-26): decimals and notation of a table column's numbers. Pure, no Qt."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.roi_table.number_format import (
        DEFAULT_DISPLAY,
        MAX_DECIMALS,
        MIN_DECIMALS,
        NORMAL,
        SCIENTIFIC,
        ColumnDisplay,
        automatic_mantissa_decimals,
        clean_decimals,
        clean_display,
        format_number,
        format_value,
        shown_decimals,
        stepped,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class FormatNumberTest(unittest.TestCase):
    def test_normal_is_the_plain_fixed_point_text(self) -> None:
        for value, decimals in ((595.773, 3), (0.087, 1), (-12.0, 0), (1e-7, 4), (123456.789, 2)):
            self.assertEqual(format_number(value, decimals, NORMAL), f"{value:.{decimals}f}")
        self.assertEqual(format_number(2.5, 1, NORMAL, signed=True), "+2.5")

    def test_scientific_has_one_digit_before_the_point(self) -> None:
        self.assertEqual(format_number(595.773, 3, SCIENTIFIC), "5.958e+02")
        self.assertEqual(format_number(0.0125, 2, SCIENTIFIC), "1.25e-02")
        self.assertEqual(format_number(-595.773, 1, SCIENTIFIC), "-6.0e+02")
        self.assertEqual(format_number(5.0, 0, SCIENTIFIC, signed=True), "+5e+00")

    def test_zero_has_exponent_zero(self) -> None:
        self.assertEqual(format_number(0.0, 3, SCIENTIFIC), "0.000e+00")

    def test_rounding_up_into_the_next_power_moves_the_exponent(self) -> None:
        self.assertEqual(format_number(9.9996, 3, SCIENTIFIC), "1.000e+01")
        self.assertEqual(format_number(0.99999996, 3, SCIENTIFIC), "1.000e+00")
        self.assertEqual(format_number(-9.9996, 3, SCIENTIFIC), "-1.000e+01")

    def test_what_is_written_reads_back_as_the_number_to_the_digits_written(self) -> None:
        for value in (595.773, 0.000123456, 123456.0, -42.5, 7.0e-9, 3.3e12):
            for notation in (SCIENTIFIC,):
                text = format_number(value, 6, notation)
                self.assertAlmostEqual(float(text) / value, 1.0, places=5, msg=(value, notation, text))


class AutomaticAndDisplayTest(unittest.TestCase):
    def test_no_setting_is_the_plain_text_with_the_columns_own_decimals(self) -> None:
        for display in (None, DEFAULT_DISPLAY, ColumnDisplay()):
            self.assertEqual(format_value(10.0, 1, display), "10.0")
            self.assertEqual(format_value(595.7734, 3, display), "595.773")
            self.assertEqual(format_value(2.0, 2, display, signed=True), "+2.00")

    def test_explicit_decimals_in_normal_notation(self) -> None:
        self.assertEqual(format_value(10.0, 1, ColumnDisplay(3, NORMAL)), "10.000")
        self.assertEqual(format_value(10.06, 1, ColumnDisplay(0, NORMAL)), "10")

    def test_automatic_scientific_keeps_the_resolution_the_plain_number_had(self) -> None:
        self.assertEqual(format_value(595.773, 3, ColumnDisplay(None, SCIENTIFIC)), "5.95773e+02")
        self.assertEqual(format_value(0.087, 3, ColumnDisplay(None, SCIENTIFIC)), "8.7e-02")
        self.assertEqual(format_value(10.0, 1, ColumnDisplay(None, SCIENTIFIC)), "1.00e+01")
        self.assertEqual(format_value(12345.6, 1, ColumnDisplay(None, SCIENTIFIC)), "1.23456e+04")

    def test_explicit_decimals_in_scientific_are_the_digits_of_the_mantissa(self) -> None:
        self.assertEqual(format_value(595.773, 3, ColumnDisplay(2, SCIENTIFIC)), "5.96e+02")
        self.assertEqual(format_value(595.773, 3, ColumnDisplay(0, SCIENTIFIC)), "6e+02")

    def test_the_automatic_mantissa_digits_are_never_negative(self) -> None:
        self.assertEqual(automatic_mantissa_decimals(0.0004, 1), 0)
        self.assertEqual(automatic_mantissa_decimals(0.0, 3), 0)
        self.assertEqual(automatic_mantissa_decimals(float("nan"), 3), 0)

    def test_shown_decimals_is_what_format_value_writes(self) -> None:
        for display in (None, ColumnDisplay(), ColumnDisplay(4, NORMAL), ColumnDisplay(None, SCIENTIFIC), ColumnDisplay(2, SCIENTIFIC)):
            for value in (595.773, 0.087, 12345.6):
                text = format_value(value, 3, display)
                digits = len(text.split("e")[0].split(".")[1]) if "." in text.split("e")[0] else 0
                self.assertEqual(shown_decimals(value, 3, display), digits, (display, value, text))


class SteppingAndCleaningTest(unittest.TestCase):
    def test_a_step_makes_the_digits_explicit_and_keeps_the_notation(self) -> None:
        self.assertEqual(stepped(None, 1, +1), ColumnDisplay(2, NORMAL))
        self.assertEqual(stepped(ColumnDisplay(None, SCIENTIFIC), 5, -1), ColumnDisplay(4, SCIENTIFIC))

    def test_a_step_stays_inside_the_range(self) -> None:
        self.assertEqual(stepped(None, MIN_DECIMALS, -1).decimals, MIN_DECIMALS)
        self.assertEqual(stepped(None, MAX_DECIMALS, +1).decimals, MAX_DECIMALS)

    def test_default_means_no_setting(self) -> None:
        self.assertTrue(ColumnDisplay().is_default)
        self.assertFalse(ColumnDisplay(2).is_default)
        self.assertFalse(ColumnDisplay(None, SCIENTIFIC).is_default)

    def test_saved_junk_is_cleaned(self) -> None:
        self.assertEqual(clean_decimals(3), 3)
        self.assertEqual(clean_decimals(99), MAX_DECIMALS)
        self.assertEqual(clean_decimals(-4), MIN_DECIMALS)
        for junk in (None, True, "3", 2.5, [1]):
            self.assertIsNone(clean_decimals(junk))
        self.assertEqual(clean_display(2, "scientific"), ColumnDisplay(2, SCIENTIFIC))
        self.assertEqual(clean_display("x", "hex"), DEFAULT_DISPLAY)
        self.assertEqual(clean_display(2, "engineering"), ColumnDisplay(2, NORMAL))  # (a notation that was built and taken out: a saved one reads as normal)


if __name__ == "__main__":
    unittest.main()
