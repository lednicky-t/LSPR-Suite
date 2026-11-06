"""The pure parts of the ROI / Groups table's step columns: sorting rows and groups by a step value (`rows.py`), the cell text and sort value (`step_columns.py`), the CSV table
(`export_tables.roi_step_table`) and the shared trace store (`trace_store.py`). No widgets."""

from __future__ import annotations

import sys
import unittest
from math import sqrt as math_sqrt

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np
    from PyQt6.QtCore import QObject, pyqtSignal

    from lspr_imaging_app.analysis.export_tables import roi_step_table
    from lspr_imaging_app.analysis.step_table import OP_DIFFERENCE, OP_SNR, RowNumbers, StepValue, compare_values
    from lspr_imaging_app.selection.step_table_module import CellValue, StepColumn
    from lspr_imaging_app.analysis.trace_store import TraceStore
    from lspr_imaging_app.domain.roi_model import AreaRoiGroup
    from lspr_imaging_app.panels.roi_table import rows as rw
    from lspr_imaging_app.panels.roi_table import step_columns as sc
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _row(roi_id: int, group_id: str | None) -> rw.RoiRow:
    return rw.RoiRow(
        roi_id=roi_id, label="", group_id=group_id, color_hex="#fff", has_own_color=False, x=0.0, y=0.0, sample=6.0, inner=8.0, outer=12.0,
        inner_inherited=True, outer_inherited=True, is_mask=False, nudge_count=0,
    )


class SortByStepValueTest(unittest.TestCase):
    def test_rows_are_ordered_by_value_and_a_row_without_one_comes_last_in_either_direction(self) -> None:
        rows = [_row(i, None) for i in (1, 2, 3, 4)]
        sort = rw.StepSort({1: 5.0, 2: 1.0, 3: float("nan")}, {})  # ROI 3 has NaN, ROI 4 has nothing
        self.assertEqual([r.roi_id for r in rw.sort_rows(rows, 0, False, sort)], [2, 1, 3, 4])
        self.assertEqual([r.roi_id for r in rw.sort_rows(rows, 0, True, sort)], [1, 2, 3, 4])

    def test_equal_values_keep_the_id_order_whatever_the_direction(self) -> None:
        rows = [_row(i, None) for i in (3, 1, 2)]
        sort = rw.StepSort({1: 2.0, 2: 2.0, 3: 2.0}, {})
        self.assertEqual([r.roi_id for r in rw.sort_rows(rows, 0, False, sort)], [1, 2, 3])
        self.assertEqual([r.roi_id for r in rw.sort_rows(rows, 0, True, sort)], [1, 2, 3])

    def test_the_groups_are_ordered_by_their_value_ungrouped_stays_last_and_the_rois_by_their_own(self) -> None:
        groups = [AreaRoiGroup("a", "A", area_roi_ids=[1, 2]), AreaRoiGroup("b", "B", area_roi_ids=[3]), AreaRoiGroup("c", "C", area_roi_ids=[])]
        rows = [_row(1, "a"), _row(2, "a"), _row(3, "b"), _row(4, None)]
        sort = rw.StepSort({1: 9.0, 2: 3.0, 3: 5.0, 4: 1.0}, {"a": 6.0, "b": 5.0})  # group c has no value
        colors = {"a": "#1", "b": "#2", "c": "#3"}
        up = rw.build_sections(rows, groups, colors, sort_column=rw.COLUMN_COUNT, descending=False, step_sort=sort)
        self.assertEqual([s.group.name for s in up], ["B", "A", "C", "Ungrouped"])
        self.assertEqual([r.roi_id for r in up[1].rows], [2, 1])  # inside A: by the ROIs' own values
        down = rw.build_sections(rows, groups, colors, sort_column=rw.COLUMN_COUNT, descending=True, step_sort=sort)
        self.assertEqual([s.group.name for s in down], ["A", "B", "C", "Ungrouped"])  # C has no value: after the others, before Ungrouped
        self.assertEqual([r.roi_id for r in down[0].rows], [1, 2])

    def test_a_fixed_column_sort_is_unchanged(self) -> None:
        rows = [_row(2, None), _row(1, None)]
        self.assertEqual([r.roi_id for r in rw.sort_rows(rows, rw.COLUMN_ID, False)], [1, 2])


class CellTextTest(unittest.TestCase):
    cell = CellValue(n=30, mean=595.77312, sd=0.0871, current=True, sem=0.0871 / math_sqrt(30))  # SEM 0.0159

    def test_each_display_writes_its_numbers_with_the_same_decimals(self) -> None:
        self.assertEqual(sc.format_cell(self.cell, sc.DISPLAY_MEAN, 3), "595.773")
        self.assertEqual(sc.format_cell(self.cell, sc.DISPLAY_STD, 3), "0.087")
        self.assertEqual(sc.format_cell(self.cell, sc.DISPLAY_BOTH, 3), "595.773 ± 0.087")
        self.assertEqual(sc.format_cell(self.cell, sc.DISPLAY_SEM, 3), "0.016")
        self.assertEqual(sc.format_cell(self.cell, sc.DISPLAY_MEAN_SEM, 3), "595.773 ± 0.016")

    def test_the_sem_displays_fall_back_like_the_sd_ones_where_there_is_no_sem(self) -> None:
        one = CellValue(n=1, mean=600.0, sd=float("nan"), current=True)  # one cube: no SD, no SEM
        self.assertEqual(sc.format_cell(one, sc.DISPLAY_MEAN_SEM, 2), "600.00")
        self.assertEqual(sc.format_cell(one, sc.DISPLAY_SEM, 2), "")
        self.assertIsNone(sc.sort_value(one, sc.DISPLAY_SEM))

    def test_the_sem_decimals_follow_the_sem_not_the_sd(self) -> None:
        # SD 0.087 gives 3 decimals; the SEM of 100 cubes is ten times smaller, so it needs one more to keep two significant digits.
        cells = [CellValue(100, 600.0, 0.087, True, sem=0.0087), CellValue(100, 601.0, 0.091, True, sem=0.0091)]
        self.assertEqual(sc.table_decimals(cells, sc.DISPLAY_BOTH), 3)
        self.assertEqual(sc.table_decimals(cells, sc.DISPLAY_MEAN_SEM), 4)
        self.assertEqual(sc.table_decimals(cells, sc.DISPLAY_SEM), 4)
        self.assertEqual(sc.table_decimals(cells, sc.DISPLAY_MEAN), 3)  # the mean alone is rounded by the SD, as before

    def test_the_sort_value_follows_what_is_shown(self) -> None:
        self.assertEqual(sc.sort_value(self.cell, sc.DISPLAY_SEM), self.cell.sem)
        self.assertEqual(sc.sort_value(self.cell, sc.DISPLAY_MEAN_SEM), self.cell.mean)

    def test_the_tooltip_gives_the_sem_with_its_formula(self) -> None:
        column = StepColumn(("m", 1), 1, "H2O", "", (), None)
        tip = sc.cell_tooltip("ROI 3", column, self.cell, is_group=False)
        self.assertIn("SEM 0.0159", tip)
        self.assertIn("sqrt(30)", tip)

    def test_a_cell_without_an_sd_falls_back_to_the_mean_and_a_missing_one_is_empty(self) -> None:
        one = CellValue(n=1, mean=600.0, sd=float("nan"), current=True)
        self.assertEqual(sc.format_cell(one, sc.DISPLAY_BOTH, 2), "600.00")
        self.assertEqual(sc.format_cell(one, sc.DISPLAY_STD, 2), "")
        self.assertEqual(sc.format_cell(None, sc.DISPLAY_BOTH, 2), "")
        self.assertEqual(sc.format_cell(CellValue(0, float("nan"), float("nan"), True), sc.DISPLAY_MEAN, 2), "")

    def test_the_decimals_follow_the_typical_sd_of_the_table(self) -> None:
        cells = [CellValue(5, 600.0, 0.087, True), CellValue(5, 601.0, 0.091, True), None]
        self.assertEqual(sc.table_decimals(cells), 3)
        self.assertEqual(sc.table_decimals([None, None]), sc.DEFAULT_DECIMALS)

    def test_the_sort_value_is_the_mean_except_for_the_std_display(self) -> None:
        self.assertEqual(sc.sort_value(self.cell, sc.DISPLAY_MEAN), self.cell.mean)
        self.assertEqual(sc.sort_value(self.cell, sc.DISPLAY_BOTH), self.cell.mean)
        self.assertEqual(sc.sort_value(self.cell, sc.DISPLAY_STD), self.cell.sd)
        self.assertIsNone(sc.sort_value(None, sc.DISPLAY_MEAN))
        self.assertIsNone(sc.sort_value(CellValue(1, 600.0, float("nan"), True), sc.DISPLAY_STD))

    def test_the_tooltip_says_what_the_sd_is_and_when_the_number_is_out_of_date(self) -> None:
        column = StepColumn(("m", 1), 1, "H2O", "", (), None)
        tip = sc.cell_tooltip("Pair", column, self.cell, is_group=True)
        self.assertIn("averaged line", tip)
        self.assertIn("30 cubes", tip)
        self.assertNotIn("Out of date", tip)
        self.assertIn("Out of date", sc.cell_tooltip("ROI 3", column, CellValue(30, 595.0, 0.1, False), is_group=False))
        self.assertIn("Not calculated", sc.cell_tooltip("ROI 3", column, None, is_group=False))

    def test_the_tooltip_of_a_comparison_cell_names_the_value_and_the_unit(self) -> None:
        diff = StepColumn(("cmp", 1), 0, "S1 − B1", "", (), None, "compare", "nm", None, "difference", True, "S1 - B1", OP_DIFFERENCE, 1)
        tip = sc.cell_tooltip("ROI 3", diff, CellValue(3, 1.5, 0.22, True), is_group=False)
        self.assertIn("difference 1.50", tip)
        self.assertIn("added in quadrature", tip)
        snr = StepColumn(("cmp", 2), 0, "(S1 − B1) / noise", "", (), None, "compare", "", 1, "signal-to-noise", False, "x", OP_SNR, 1)
        tip = sc.cell_tooltip("ROI 3", snr, CellValue(3, 25.31, float("nan"), True), is_group=False)
        self.assertIn("signal-to-noise 25.31", tip)
        self.assertNotIn("SD", tip)
        self.assertIn("baseline or signal step", sc.cell_tooltip("ROI 3", snr, None, is_group=False))


class WidestTextTest(unittest.TestCase):
    """The widest text of a column, measured on the longest few only (2026-11-06)."""

    @staticmethod
    def advance(text: str) -> int:
        return 7 * len(text) + 3 * text.count("±")  # a stand-in for the font: every character 7 px, the plus-minus sign 3 px wider

    def test_nothing_is_zero(self) -> None:
        self.assertEqual(sc.widest_text([], self.advance), 0)

    def test_it_is_the_widest_text(self) -> None:
        self.assertEqual(sc.widest_text(["1.5", "595.77 ± 0.09", "12.3"], self.advance), self.advance("595.77 ± 0.09"))

    def test_hundreds_of_rows_cost_a_bounded_number_of_measurements_and_still_find_the_widest(self) -> None:
        texts = [f"{500 + i / 10:.1f} ± 0.{i % 10}" for i in range(400)] + ["1002.55 ± 12.34"]  # one longer than all the others, last in the list
        calls: list[str] = []

        def counting(text: str) -> int:
            calls.append(text)
            return self.advance(text)

        self.assertEqual(sc.widest_text(texts, counting), self.advance("1002.55 ± 12.34"))
        self.assertLessEqual(len(calls), 64)

    def test_the_same_text_over_and_over_is_measured_once(self) -> None:
        calls: list[str] = []
        sc.widest_text(["595.7 ± 0.1"] * 300, lambda t: calls.append(t) or 1)
        self.assertEqual(len(calls), 1)


class ExportTableTest(unittest.TestCase):
    steps = [("step 1 (H2O)", "mean", "nm", True), ("step 2 (10perEG)", "mean", "nm", True)]
    rows = [
        ("group", "Pair", None, [(30, 595.5, 0.1, 0.02), (28, 597.0, 0.2, 0.04)], True),
        ("roi", "Pair", 1, [(30, 595.4, 0.11, 0.021), None], False),
        ("roi", "", 2, [None, None], None),
    ]

    def test_both_gives_mean_sd_and_n_per_step_and_a_currency_column(self) -> None:
        table = roi_step_table(self.steps, self.rows, "both")
        self.assertEqual(table.header[:3], ("row", "group", "roi_id"))
        self.assertEqual(
            table.header[3:9],
            ("step 1 (H2O) mean (nm)", "step 1 (H2O) sd (nm)", "step 1 (H2O) n", "step 2 (10perEG) mean (nm)", "step 2 (10perEG) sd (nm)", "step 2 (10perEG) n"),
        )
        self.assertEqual(table.header[-1], "up_to_date")
        self.assertEqual(table.rows[0], ("group", "Pair", None, 595.5, 0.1, 30, 597.0, 0.2, 28, "yes"))
        self.assertEqual(table.rows[1][-1], "no")
        self.assertEqual(table.rows[2], ("roi", "", 2, None, None, None, None, None, None, ""))  # never calculated: empty cells, empty flag
        self.assertEqual(len(table.notes), 1)  # one row is out of date

    def test_mean_only_and_std_only_leave_the_other_out(self) -> None:
        mean = roi_step_table(self.steps, self.rows, "mean")
        self.assertFalse(any("sd (nm)" in h for h in mean.header))
        std = roi_step_table(self.steps, self.rows, "std")
        self.assertFalse(any("mean (nm)" in h for h in std.header))
        self.assertEqual(std.rows[0][3:6], (0.1, 30, 0.2))

    def test_sem_and_mean_with_sem_write_the_sem_where_the_sd_would_be(self) -> None:
        sem = roi_step_table(self.steps, self.rows, "sem")
        self.assertEqual(sem.header[3:6], ("step 1 (H2O) sem (nm)", "step 1 (H2O) n", "step 2 (10perEG) sem (nm)"))
        self.assertFalse(any("mean (nm)" in h or "sd (nm)" in h for h in sem.header))
        self.assertEqual(sem.rows[0][3:6], (0.02, 30, 0.04))
        both = roi_step_table(self.steps, self.rows, "mean_sem")
        self.assertEqual(both.header[3:6], ("step 1 (H2O) mean (nm)", "step 1 (H2O) sem (nm)", "step 1 (H2O) n"))
        self.assertEqual(both.rows[0], ("group", "Pair", None, 595.5, 0.02, 30, 597.0, 0.04, 28, "yes"))
        self.assertEqual(both.rows[2], ("roi", "", 2, None, None, None, None, None, None, ""))

    def test_titles_that_repeat_still_give_distinct_columns(self) -> None:
        table = roi_step_table([("step 1 (H2O)", "mean", "nm", True), ("step 2 (10perEG)", "mean", "nm", True), ("step 3 (H2O)", "mean", "nm", True)], [], "mean")
        self.assertEqual(len(set(table.header)), len(table.header))

    def test_a_comparison_column_is_written_with_its_own_word_and_a_ratio_has_no_sd_column(self) -> None:
        columns = [("step 1 (H2O)", "mean", "nm", True), ("S1 - B1", "difference", "nm", True), ("(S1 - B1) / noise of B1", "signal-to-noise", "", False)]
        rows = [("roi", "", 1, [(3, 600.0, 0.1, 0.06), (3, 1.5, 0.2, 0.12), (3, 25.0, float("nan"), float("nan"))], True)]
        both = roi_step_table(columns, rows, "both")
        self.assertEqual(
            both.header[3:],
            (
                "step 1 (H2O) mean (nm)", "step 1 (H2O) sd (nm)", "step 1 (H2O) n", "S1 - B1 difference (nm)", "S1 - B1 sd (nm)", "S1 - B1 n",
                "(S1 - B1) / noise of B1 signal-to-noise", "(S1 - B1) / noise of B1 n", "up_to_date",
            ),
        )
        self.assertEqual(both.rows[0][3:], (600.0, 0.1, 3, 1.5, 0.2, 3, 25.0, 3, "yes"))
        std = roi_step_table(columns, rows, "std")  # the ratio has no SD: it keeps its value column so the table still says something
        self.assertIn("(S1 - B1) / noise of B1 signal-to-noise", std.header)
        self.assertNotIn("step 1 (H2O) mean (nm)", std.header)
        sem = roi_step_table(columns, rows, "sem")  # the same for the SEM: the ratio keeps its value column, the others show only their SEM
        self.assertIn("(S1 - B1) / noise of B1 signal-to-noise", sem.header)
        self.assertIn("S1 - B1 sem (nm)", sem.header)
        self.assertNotIn("S1 - B1 difference (nm)", sem.header)


class CompareValuesTest(unittest.TestCase):
    baseline = StepValue(n=30, mean=600.0, sd=0.10, noise=0.05)
    signal = StepValue(n=28, mean=601.5, sd=0.20, noise=0.08)

    def test_the_difference_is_signal_minus_baseline_with_the_sds_added_in_quadrature(self) -> None:
        n, value, sd, _sem = compare_values(OP_DIFFERENCE, self.baseline, self.signal)
        self.assertEqual(n, 28)  # the smaller step
        self.assertAlmostEqual(value, 1.5, places=12)
        self.assertAlmostEqual(sd, math_sqrt(0.10**2 + 0.20**2), places=12)

    def test_the_sem_of_a_difference_adds_the_two_sems_in_quadrature_and_shrinks_with_more_cubes(self) -> None:
        _, _, _, sem = compare_values(OP_DIFFERENCE, self.baseline, self.signal)
        self.assertAlmostEqual(sem, math_sqrt(0.10**2 / 30 + 0.20**2 / 28), places=12)
        longer = StepValue(n=120, mean=601.5, sd=0.20, noise=0.08)
        self.assertLess(compare_values(OP_DIFFERENCE, self.baseline, longer)[3], sem)  # more cubes in the step: the difference of the means is better known
        self.assertEqual(compare_values(OP_DIFFERENCE, self.baseline, longer)[2], compare_values(OP_DIFFERENCE, self.baseline, self.signal)[2])  # the SD does not change

    def test_the_signal_to_noise_divides_by_the_noise_of_the_baseline_only(self) -> None:
        n, value, sd, sem = compare_values(OP_SNR, self.baseline, self.signal)
        self.assertAlmostEqual(value, 1.5 / 0.05, places=9)
        self.assertTrue(np.isnan(sd) and np.isnan(sem))

    def test_a_missing_input_gives_nothing_not_a_wrong_number(self) -> None:
        nan = float("nan")
        no_baseline = StepValue(0, nan, nan, nan)
        for op in (OP_DIFFERENCE, OP_SNR):
            n, value, sd, sem = compare_values(op, no_baseline, self.signal)
            self.assertTrue(np.isnan(value) and np.isnan(sd) and np.isnan(sem))
        _, value, _, _ = compare_values(OP_SNR, StepValue(2, 600.0, 0.1, nan), self.signal)  # fewer than three cubes: no noise
        self.assertTrue(np.isnan(value))
        _, value, _, _ = compare_values(OP_SNR, StepValue(5, 600.0, 0.0, 0.0), self.signal)  # a baseline with no scatter at all: no ratio
        self.assertTrue(np.isnan(value))
        _, value, sd, sem = compare_values(OP_DIFFERENCE, StepValue(1, 600.0, nan, nan), self.signal)  # one cube has no SD, the difference still exists
        self.assertAlmostEqual(value, 1.5)
        self.assertTrue(np.isnan(sd) and np.isnan(sem))

    def test_the_sem_of_a_step_is_the_sd_over_the_root_of_the_cubes_and_needs_two_cubes(self) -> None:
        self.assertAlmostEqual(StepValue(25, 600.0, 0.5, 0.1).sem, 0.1, places=12)
        self.assertTrue(np.isnan(StepValue(1, 600.0, 0.5, np.nan).sem))
        self.assertTrue(np.isnan(StepValue(5, 600.0, np.nan, np.nan).sem))

    def test_a_row_answers_with_everything_it_has_in_a_step_and_nan_for_a_step_it_lacks(self) -> None:
        row = RowNumbers(("a", "b"), np.array([3, 4]), np.array([1.0, 2.0]), np.array([0.1, 0.2]), np.array([0.05, np.nan]))
        self.assertEqual(row.value_at("a"), StepValue(3, 1.0, 0.1, 0.05))
        self.assertTrue(np.isnan(row.value_at("b").noise))
        self.assertTrue(np.isnan(row.value_at("zz").mean))
        no_noise = RowNumbers(("a",), np.array([3]), np.array([1.0]), np.array([0.1]))
        self.assertTrue(np.isnan(no_noise.value_at("a").noise))


class _FakeScan(QObject):
    results_changed = pyqtSignal()


class TraceStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scan = _FakeScan()
        self.store = TraceStore(self.scan, in_process=False)
        self.trace = np.arange(3.0)

    def test_traces_are_kept_for_their_key_and_handed_out_as_a_copy(self) -> None:
        key = ("scan", "poly")
        self.assertEqual(self.store.known(key), {})
        self.store.add(key, {1: self.trace})
        got = self.store.known(key)
        self.assertIn(1, got)
        got.pop(1)
        self.assertIn(1, self.store.known(key))  # the copy was the caller's own

    def test_another_key_drops_the_old_traces_and_a_late_result_for_the_old_key_is_ignored(self) -> None:
        self.store.known(("a",))
        self.store.add(("a",), {1: self.trace})
        self.assertEqual(self.store.known(("b",)), {})  # the fit settings changed
        self.store.add(("a",), {2: self.trace})  # a worker that started before the change finishes now
        self.assertEqual(self.store.known(("b",)), {})

    def test_a_new_scan_empties_it(self) -> None:
        self.store.known(("a",))
        self.store.add(("a",), {1: self.trace})
        self.scan.results_changed.emit()
        self.assertEqual(self.store.known(("a",)), {})

    def test_without_a_helper_process_the_fits_run_in_the_calling_thread(self) -> None:
        self.assertIsNone(self.store.pool())
        self.assertIsNone(self.store.runner())
        self.store.warm_up()  # nothing to start
        self.store.shutdown()


if __name__ == "__main__":
    unittest.main()
