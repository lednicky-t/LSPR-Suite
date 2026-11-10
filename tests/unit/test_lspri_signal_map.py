"""The signal map's pure part (2026-11-10, `analysis/signal_map.py`): the range a value maps to, the colours, the out-of-date fade, the hover text and the
export table. No Qt, no files."""

from __future__ import annotations

import math
import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()
APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.analysis import signal_map as sm  # noqa: E402


class ValueRangeTest(unittest.TestCase):
    def test_auto_is_symmetric_around_zero_when_the_values_have_both_signs(self) -> None:
        low, high = sm.value_range(np.array([-0.5, 0.1, 1.0]), sm.RANGE_AUTO)
        self.assertAlmostEqual(low, -high)
        self.assertGreater(high, 0.9)

    def test_auto_is_the_percentile_range_when_all_values_have_one_sign(self) -> None:
        values = np.linspace(600.0, 700.0, 101)
        low, high = sm.value_range(values, sm.RANGE_AUTO)
        self.assertAlmostEqual(low, 602.0, places=6)
        self.assertAlmostEqual(high, 698.0, places=6)

    def test_one_outlier_does_not_set_the_range(self) -> None:
        values = np.concatenate([np.linspace(0.0, 1.0, 100), [50.0]])
        _low, high = sm.value_range(values, sm.RANGE_DATA)
        self.assertLess(high, 2.0)

    def test_fixed_is_taken_as_given_and_sorted(self) -> None:
        self.assertEqual(sm.value_range(np.array([5.0]), sm.RANGE_FIXED, (2.0, -1.0)), (-1.0, 2.0))

    def test_no_finite_value_gives_no_range_and_equal_values_get_a_narrow_one(self) -> None:
        self.assertTrue(all(math.isnan(v) for v in sm.value_range(np.array([np.nan]))))
        low, high = sm.value_range(np.array([3.0, 3.0]), sm.RANGE_DATA)
        self.assertLess(low, 3.0)
        self.assertGreater(high, 3.0)


class ColoursTest(unittest.TestCase):
    def test_the_ends_and_the_middle_of_a_diverging_scheme(self) -> None:
        colours = sm.colours_for(np.array([-1.0, 0.0, 1.0, 9.0]), -1.0, 1.0, "rdbu")
        self.assertEqual(colours[0], "#2166ac")  # the low end
        self.assertEqual(colours[1], "#f7f7f7")  # zero is white
        self.assertEqual(colours[2], "#b2182b")  # the high end
        self.assertEqual(colours[3], colours[2])  # beyond the range: clipped to the end colour

    def test_a_roi_with_no_value_is_grey(self) -> None:
        self.assertEqual(sm.colours_for(np.array([np.nan]), 0.0, 1.0, "viridis"), [sm.NO_VALUE_HEX])

    def test_out_of_date_colours_are_faded_towards_grey(self) -> None:
        fresh = sm.colours_for(np.array([1.0]), 0.0, 1.0, "rdbu")[0]
        faded = sm.colours_for(np.array([1.0]), 0.0, 1.0, "rdbu", stale=True)[0]
        self.assertNotEqual(fresh, faded)
        self.assertEqual(faded, sm.to_hex(sm.fade(np.array([[178, 24, 43]])))[0])

    def test_every_scheme_runs_from_its_first_to_its_last_stop_and_an_unknown_key_falls_back(self) -> None:
        for s in sm.SCHEMES:
            rgb = sm.scheme_rgb(np.array([0.0, 1.0]), s.key)
            self.assertEqual(tuple(rgb[0]), s.stops[0][1])
            self.assertEqual(tuple(rgb[1]), s.stops[-1][1])
        self.assertEqual(sm.scheme("no-such").key, sm.DEFAULT_SCHEME)


class ViewTest(unittest.TestCase):
    def _view(self, stale: bool = False) -> sm.SignalMapView:
        return sm.SignalMapView(
            1, {1: "#ffffff", 2: sm.NO_VALUE_HEX}, {1: 0.25, 2: math.nan}, {1: 0.01, 2: math.nan}, {1: "ROI 1", 2: "group A"},
            "Shift S1 − B1", "nm", "rdbu", -1.0, 1.0, stale=stale, missing=1,
        )

    def test_the_hover_text_says_the_value_its_sd_and_where_it_came_from(self) -> None:
        view = self._view()
        self.assertEqual(view.describe(1), "ROI 1\nShift S1 − B1: 0.250 nm (SD 0.010 nm)")
        self.assertIn("ROI 2 (group A)", view.describe(2))
        self.assertIn("no value yet", view.describe(2))
        self.assertEqual(view.describe(99), "")
        self.assertIn("Out of date", self._view(stale=True).describe(1))

    def test_the_export_table_has_one_row_per_roi_and_marks_out_of_date_numbers(self) -> None:
        table = sm.signal_map_table(self._view(stale=True), [1, 2])
        self.assertEqual(table.header[2], "Shift S1 − B1 (nm)")
        self.assertEqual([row[0] for row in table.rows], [1, 2])
        self.assertTrue(all(row[-1] for row in table.rows))
        self.assertTrue(any("out of date" in note for note in table.notes))


if __name__ == "__main__":
    unittest.main()
