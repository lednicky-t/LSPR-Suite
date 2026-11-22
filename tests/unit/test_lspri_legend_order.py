"""The order of a plot's legend rows (2026-11-22, `panels/sensorgram/legend_order.py`): pure rules, no Qt.

Pinned: Group = the lines' own order (and its reverse); Name reads numbers as numbers and ignores case; Value puts a line without a number last in both directions; equal keys keep the
default order in both directions; a Value order with nothing to read is the default order; an unknown setting is sanitized.
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

from lspr_imaging_app.panels.sensorgram.legend_order import (  # noqa: E402
    SORT_GROUP,
    SORT_NAME,
    SORT_VALUE,
    VALUE_MEAN,
    VALUE_STEP,
    LegendOrder,
    natural_key,
    order_rows,
)

NAMES = ["ROI 10", "ROI 2", "roi 1", "Group B"]


class GroupOrderTest(unittest.TestCase):
    def test_group_is_the_default_order_and_its_reverse(self) -> None:
        self.assertEqual(order_rows(NAMES, None, LegendOrder(SORT_GROUP)), [0, 1, 2, 3])
        self.assertEqual(order_rows(NAMES, None, LegendOrder(SORT_GROUP, descending=True)), [3, 2, 1, 0])

    def test_nothing_to_order(self) -> None:
        self.assertEqual(order_rows([], None, LegendOrder(SORT_NAME)), [])


class NameOrderTest(unittest.TestCase):
    def test_numbers_are_read_as_numbers_and_case_is_ignored(self) -> None:
        self.assertEqual(order_rows(NAMES, None, LegendOrder(SORT_NAME)), [3, 2, 1, 0])  # Group B, roi 1, ROI 2, ROI 10
        self.assertEqual(order_rows(NAMES, None, LegendOrder(SORT_NAME, descending=True)), [0, 1, 2, 3])

    def test_equal_names_keep_the_default_order_in_both_directions(self) -> None:
        same = ["A", "a", "A"]
        self.assertEqual(order_rows(same, None, LegendOrder(SORT_NAME)), [0, 1, 2])
        self.assertEqual(order_rows(same, None, LegendOrder(SORT_NAME, descending=True)), [0, 1, 2])

    def test_natural_key_mixes_digits_and_text_without_error(self) -> None:
        self.assertLess(natural_key("2 nm"), natural_key("10 nm"))
        self.assertLess(natural_key("12"), natural_key("A"))  # a number before text: comparable, no TypeError


class ValueOrderTest(unittest.TestCase):
    def test_ascending_and_descending(self) -> None:
        values = [3.0, 1.0, 2.0, 5.0]
        self.assertEqual(order_rows(NAMES, values, LegendOrder(SORT_VALUE)), [1, 2, 0, 3])
        self.assertEqual(order_rows(NAMES, values, LegendOrder(SORT_VALUE, descending=True)), [3, 0, 2, 1])

    def test_a_missing_number_is_last_whichever_the_direction(self) -> None:
        values = [3.0, math.nan, 2.0, None]
        self.assertEqual(order_rows(NAMES, values, LegendOrder(SORT_VALUE)), [2, 0, 1, 3])
        self.assertEqual(order_rows(NAMES, values, LegendOrder(SORT_VALUE, descending=True)), [0, 2, 1, 3])

    def test_equal_values_keep_the_default_order_in_both_directions(self) -> None:
        values = [1.0, 2.0, 1.0, 2.0]
        self.assertEqual(order_rows(NAMES, values, LegendOrder(SORT_VALUE)), [0, 2, 1, 3])
        self.assertEqual(order_rows(NAMES, values, LegendOrder(SORT_VALUE, descending=True)), [1, 3, 0, 2])

    def test_no_values_is_the_default_order(self) -> None:
        self.assertEqual(order_rows(NAMES, None, LegendOrder(SORT_VALUE, descending=True)), [0, 1, 2, 3])
        self.assertEqual(order_rows(NAMES, [1.0], LegendOrder(SORT_VALUE)), [0, 1, 2, 3])  # a wrong length is not trusted


class OrderSettingsTest(unittest.TestCase):
    def test_unknown_values_are_sanitized(self) -> None:
        clean = LegendOrder("nonsense", True, "nonsense").sanitized()
        self.assertEqual((clean.key, clean.descending, clean.value), (SORT_GROUP, True, VALUE_MEAN))

    def test_needs_step_only_for_value_at_step(self) -> None:
        self.assertTrue(LegendOrder(SORT_VALUE, value=VALUE_STEP).needs_step())
        self.assertFalse(LegendOrder(SORT_VALUE, value=VALUE_MEAN).needs_step())
        self.assertFalse(LegendOrder(SORT_NAME, value=VALUE_STEP).needs_step())


if __name__ == "__main__":
    unittest.main()
