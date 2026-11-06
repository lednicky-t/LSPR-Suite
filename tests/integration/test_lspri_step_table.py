"""The ROI / Groups table's step columns (2026-11-05): the module that calculates and remembers the numbers (`StepTableModule`), the trace store and axis it shares with the
Sensorgram, and the table panel that shows them. Real modules and a real scan on the small two-step dataset of `test_lspri_plan_bar` (6 cubes, steps = cubes 0-2 and 3-5);
driven through methods and signals, never by screen coordinate; the window is not shown.

Pinned: the numbers are the Sensorgram's (ROI lines and average-by-group lines); the fitted traces are shared both ways (no ROI is fitted twice); a changed input (a moved
boundary, Subtract) leaves the numbers in place but out of date, and a calculation makes them current; a display setting of the Sensorgram that changes no number does not dim
the table; columns follow the steps and the plan bar's title style; the panel shows, sorts, hides, remembers and exports.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from unittest import mock
from pathlib import Path

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis import scan_trace
    from lspr_imaging_app.analysis import step_stats
    from lspr_imaging_app.analysis.scan_trace import DISPLAY_BY_GROUP
    from lspr_imaging_app.analysis.step_table import UNGROUPED, GroupMembers
    from lspr_imaging_app.selection.step_table_module import StepTableModule
    from lspr_imaging_app.analysis.trace_store import TraceStore
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.model import COLUMN_KIND_ROLE, KIND_ROLE, STEP_STALE_ROLE
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_COUNT, COLUMN_ID
    from lspr_imaging_app.panels.roi_table.step_columns import DISPLAY_BOTH, DISPLAY_MEAN, DISPLAY_MEAN_SEM, DISPLAY_SEM, DISPLAY_STD
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.label_rule import MODE_SOLUTION
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.startup_tracker import StartupStep, StartupTracker
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_plan_bar import CENTERS, CUBE_MS, T0, _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()


class StepTableBase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.m.session_autosave._suspended = True
        self.workflow = AnalysisTab()
        app_rewrite._wire_analysis_scan(self.m, self.workflow, UiStateStore({}))
        self.m.scan_results._timer.setInterval(30)
        self.m.cube_display.set_mode(MODE_CUBE)
        # The fits run in this process so a test can count them; the app's own store (with its helper process) is never started.
        self.store = TraceStore(self.m.scan_results, in_process=False)
        self.axis = self.m.sensorgram_axis
        self.table = StepTableModule(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.analysis_settings, self.m.experiment_plan, self.store, self.axis
        )
        self.panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            mask=self.m.mask, experiment_plan=self.m.experiment_plan, trace_store=self.store, axis=self.axis,
        )
        self.fits: list[int] = []
        self._real_metric_trace = scan_trace.metric_trace

        def spy(stored, roi_id, formula_key, metric):
            self.fits.append(roi_id)
            return self._real_metric_trace(stored, roi_id, formula_key, metric)

        scan_trace.metric_trace = spy
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        undo_manager.clear()
        self.done: list[tuple] = []
        self.table.finished.connect(lambda outcome, message: self.done.append((outcome, message)))
        self.notices: list[str] = []
        self.table.notice.connect(self.notices.append)

    def tearDown(self) -> None:
        scan_trace.metric_trace = self._real_metric_trace
        self.table.shutdown()
        self.panel.shutdown()
        self.m.step_table.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _calculate(self, rois=(1, 2), groups=()) -> None:
        before = len(self.done)
        self.assertTrue(self.table.recalculate(list(rois), list(groups)))
        self.assertTrue(_pump(lambda: len(self.done) > before and not self.table.is_running()), "the calculation never finished")
        self.assertEqual(self.done[-1][0], "completed", self.done[-1])

    def _draw(self) -> None:
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None and not self.panel._redraw_timer.isActive()), "the Sensorgram never drew")

    def _refs(self):
        return [c.ref for c in self.table.columns()]


class StepTableModuleTest(StepTableBase):
    def test_the_columns_are_the_steps_titled_as_the_plan_bar_titles_them(self) -> None:
        columns = self.table.columns()
        self.assertEqual([c.title for c in columns], ["H2O_--_ch1F", "10perEG_-_ch1F"])
        self.assertEqual([c.order for c in columns], [1, 2])
        self.assertIn("Step 1", columns[0].tooltip)

    def test_there_is_only_one_axis_per_settings_module_so_a_unit_switch_moves_the_baseline_window_once(self) -> None:
        from lspr_imaging_app.selection.sensorgram_axis import SensorgramAxis

        again = SensorgramAxis.of(self.m.scan_results, self.m.dataset, self.m.cube_display, self.m.analysis_settings)
        self.assertIs(again, self.axis)
        lonely = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display, trace_in_process=False
        )
        self.assertIs(lonely._axis, self.axis)  # a panel given no axis takes this one, not a second that would move the window again
        lonely.shutdown()
        lonely.deleteLater()

    def test_the_title_style_of_the_plan_bar_changes_the_titles(self) -> None:
        seen: list[int] = []
        self.table.columns_changed.connect(lambda: seen.append(1))
        self.table.set_title_style(MODE_SOLUTION, ("_-_", "_--_"))
        self.assertEqual([c.title for c in self.table.columns()], ["H2O", "10perEG"])
        self.assertTrue(seen)

    def test_the_numbers_of_a_roi_are_the_numbers_of_its_sensorgram_line(self) -> None:
        self.m.selection.set_roi_selection({1, 2})
        self._draw()
        lines = {line.key: line for line in self.panel._last[1]}
        self._calculate(rois=(1, 2))
        for roi in (1, 2):
            line = lines[f"roi:{roi}"]
            for i, ref in enumerate(self._refs()):
                cell = self.table.roi_cell(roi, ref)
                self.assertIsNotNone(cell)
                self.assertAlmostEqual(cell.mean, float(line.step_values.mean[i]), places=9)
                self.assertAlmostEqual(cell.sd, float(line.step_values.sd[i]), places=9)
                self.assertEqual(cell.n, int(line.step_values.n[i]))
                self.assertTrue(cell.current)

    def test_the_numbers_of_a_group_are_the_numbers_of_the_sensorgram_average_by_group_line(self) -> None:
        group_id = self.m.roi_toolbox.create_group("Pair")
        self.m.roi_toolbox.add_rois_to_group([1, 2], group_id)
        stats = self.m.analysis_settings.statistics_settings()
        self.m.analysis_settings.set_statistics_settings(replace(stats, sensorgram_display_mode=DISPLAY_BY_GROUP))
        self.m.selection.set_roi_selection({1, 2})
        self._draw()
        (line,) = [ln for ln in self.panel._last[1] if ln.key == "group:Pair"]
        self._calculate(rois=(), groups=(GroupMembers(group_id, (1, 2)),))
        for i, ref in enumerate(self._refs()):
            cell = self.table.group_cell(group_id, (2, 1), ref)  # members in any order
            self.assertAlmostEqual(cell.mean, float(line.step_values.mean[i]), places=9)
            self.assertAlmostEqual(cell.sd, float(line.step_values.sd[i]), places=9)

    def test_a_collapsed_group_is_still_calculated_from_all_its_rois(self) -> None:
        self._calculate(rois=(), groups=(GroupMembers(UNGROUPED, (1, 2)),))
        self.assertEqual(sorted(set(self.fits)), [1, 2])  # both members were fitted though no ROI row was asked for
        self.assertIsNone(self.table.roi_cell(1, self._refs()[0]))  # and no ROI got a row of its own
        self.assertIsNotNone(self.table.group_cell(UNGROUPED, (1, 2), self._refs()[0]))

    def test_the_fitted_traces_are_shared_in_both_directions(self) -> None:
        self._calculate(rois=(1, 2))
        fitted = len(self.fits)
        self.assertEqual(fitted, 2)
        self.m.selection.set_roi_selection({1, 2})
        self._draw()
        self.assertEqual(len(self.fits), fitted, "the Sensorgram fitted ROIs the table had already fitted")
        # and the other way: a third ROI fitted by the Sensorgram is not fitted again by the table
        self.m.roi_toolbox.add_roi(20.0, 20.0, sample_diameter_px=6.0)
        third = self.m.roi_toolbox.rois()[-1].area_roi_id
        self.m.selection.set_roi_selection({third})
        self._draw()
        count = len(self.fits)
        self._calculate(rois=(third,))
        self.assertEqual(len(self.fits), count)

    def test_a_calculation_is_the_same_whatever_the_order_of_the_rows(self) -> None:
        self._calculate(rois=(1, 2))
        forward = [self.table.roi_cell(r, ref).mean for r in (1, 2) for ref in self._refs()]
        self._calculate(rois=(2, 1))
        backward = [self.table.roi_cell(r, ref).mean for r in (1, 2) for ref in self._refs()]
        np.testing.assert_array_equal(forward, backward)

    def test_a_moved_step_boundary_leaves_the_numbers_out_of_date_and_a_calculation_makes_them_current(self) -> None:
        self._calculate(rois=(1, 2))
        self.assertEqual(self.table.count_not_current([1, 2], []), 0)
        before = self.table.roi_cell(1, self._refs()[0])
        plan = self.m.experiment_plan
        plan.move_edge(0, "end", T0 + 2 * CUBE_MS, plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5]), together=True)
        self.assertEqual(self.table.count_not_current([1, 2], []), 2)
        stale = self.table.roi_cell(1, self._refs()[0])
        self.assertFalse(stale.current)
        self.assertEqual(stale.mean, before.mean)  # the numbers stay, they are only marked
        self._calculate(rois=(1, 2))
        self.assertEqual(self.table.count_not_current([1, 2], []), 0)
        self.assertEqual(self.table.roi_cell(1, self._refs()[0]).n, 2)  # step 1 is now two cubes

    def test_a_note_or_a_colour_edit_does_not_make_the_numbers_out_of_date(self) -> None:
        self._calculate(rois=(1, 2))
        self.m.experiment_plan.set_label(0, "Buffer")
        self.m.experiment_plan.set_color(0, "#ff0000")
        self.assertEqual(self.table.count_not_current([1, 2], []), 0)
        self.assertEqual(self.table.columns()[0].title, "Buffer")  # but the column is retitled

    def test_subtract_is_the_sensorgrams_switch_and_makes_the_numbers_out_of_date(self) -> None:
        self._calculate(rois=(1, 2))
        absolute = self.table.roi_cell(1, self._refs()[1]).mean
        self.table.set_baseline_enabled(True)
        self.assertTrue(self.m.analysis_settings.statistics_settings().baseline_enabled)
        self.assertTrue(self.panel._baseline.isChecked())  # the Sensorgram's own button follows
        self.assertEqual(self.table.count_not_current([1, 2], []), 2)
        self.assertEqual(len(self.fits), 2)
        self._calculate(rois=(1, 2))
        self.assertEqual(len(self.fits), 2, "a baseline change fitted again")
        shifted = self.table.roi_cell(1, self._refs()[1]).mean
        self.assertLess(abs(shifted), abs(absolute))  # a shift from the baseline, not a position near 500 nm

    def test_a_sensorgram_display_mode_that_changes_no_number_does_not_dim_the_table(self) -> None:
        self._calculate(rois=(1, 2))
        stats = self.m.analysis_settings.statistics_settings()
        self.m.analysis_settings.set_statistics_settings(replace(stats, sensorgram_display_mode=DISPLAY_BY_GROUP, sensorgram_band="sem"))
        self.assertEqual(self.table.count_not_current([1, 2], []), 0)

    def test_a_new_scan_keeps_the_numbers_dimmed_and_another_dataset_drops_them(self) -> None:
        self._calculate(rois=(1, 2))
        self.m.scan_results.results_changed.emit()
        self.assertTrue(self.table.has_numbers())
        self.assertEqual(self.table.count_not_current([1, 2], []), 2)
        self.table.clear()
        self.assertFalse(self.table.has_numbers())

    def test_a_roi_renumbering_makes_every_row_out_of_date(self) -> None:
        self._calculate(rois=(1, 2))
        self.m.roi_toolbox.roi_ids_renumbered.emit({1: 2, 2: 1})
        self.assertEqual(self.table.count_not_current([1, 2], []), 2)

    def test_without_a_scan_there_is_nothing_to_calculate_and_the_user_is_told(self) -> None:
        self.m.scan_results.set_scan_file(None)
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is None))
        self.assertFalse(self.table.recalculate([1], []))
        self.assertTrue(self.notices and "scan" in self.notices[-1].lower())

    def test_a_steps_structure_change_changes_the_columns(self) -> None:
        seen: list[int] = []
        self.table.columns_changed.connect(lambda: seen.append(1))
        self.m.experiment_plan.remove_steps([1])
        self.assertEqual(len(self.table.columns()), 1)
        self.assertTrue(seen)

    # -- comparison columns (step 2) ------------------------------------------------------------------------------------------------

    def _set_pair(self, pair: int = 1, baseline: int = 0, signal: int = 1) -> None:
        self.m.experiment_plan.set_role("baseline", baseline, pair)
        self.m.experiment_plan.set_role("signal", signal, pair)

    def _compare_ref(self, index: int = -1):
        return [c for c in self.table.columns() if c.kind == "compare"][index].ref

    def test_a_comparison_column_is_the_signal_step_minus_the_baseline_step_with_the_sds_added_in_quadrature(self) -> None:
        self._set_pair()
        spec = self.table.add_compare()
        self.assertEqual((spec.pair, spec.op), (1, "difference"))
        self._calculate(rois=(1, 2))
        step_refs = self._refs()[:2]
        for roi in (1, 2):
            base, signal = self.table.roi_cell(roi, step_refs[0]), self.table.roi_cell(roi, step_refs[1])
            cell = self.table.roi_cell(roi, self._compare_ref())
            self.assertAlmostEqual(cell.mean, signal.mean - base.mean, places=9)
            self.assertAlmostEqual(cell.sd, (signal.sd**2 + base.sd**2) ** 0.5, places=9)
            self.assertEqual(cell.n, min(base.n, signal.n))
            self.assertTrue(cell.current)

    def test_a_group_has_a_comparison_value_too(self) -> None:
        self._set_pair()
        self.table.add_compare()
        self._calculate(rois=(), groups=(GroupMembers(UNGROUPED, (1, 2)),))
        base, signal = (self.table.group_cell(UNGROUPED, (1, 2), ref) for ref in self._refs()[:2])
        cell = self.table.group_cell(UNGROUPED, (1, 2), self._compare_ref())
        self.assertAlmostEqual(cell.mean, signal.mean - base.mean, places=9)

    def test_the_signal_to_noise_divides_by_the_noise_of_the_baseline_step(self) -> None:
        self._set_pair()
        spec = self.table.add_compare()
        self.table.set_compare_op(spec.uid, "snr")
        self._calculate(rois=(1,))
        stored = self.m.scan_results.stored()
        membership = step_stats.assign_cubes(self.m.experiment_plan.steps(), *self.m.dataset.cube_spans_unix_ms(stored.cube_indices))
        trace = self.store.cached()[1][None, :]
        expected = step_stats.step_statistics([1], trace, membership)
        cell = self.table.roi_cell(1, self._compare_ref())
        want = (expected.mean[0, 1] - expected.mean[0, 0]) / expected.noise[0, 0]
        self.assertAlmostEqual(cell.mean, float(want), places=9)
        self.assertTrue(np.isnan(cell.sd))

    def test_a_comparison_column_is_empty_until_the_pair_is_set_and_follows_a_change_of_the_pair_without_a_new_calculation(self) -> None:
        self.table.add_compare()
        self._calculate(rois=(1, 2))
        fits = len(self.fits)
        self.assertIsNone(self.table.roi_cell(1, self._compare_ref()))  # nothing set
        self._set_pair()
        first = self.table.roi_cell(1, self._compare_ref())
        self.assertIsNotNone(first)
        self.m.experiment_plan.set_role("signal", None, 1)
        self.assertIsNone(self.table.roi_cell(1, self._compare_ref()))
        self.assertEqual(len(self.fits), fits)  # nothing was fitted or calculated again
        self.assertFalse(self.table.is_running())

    def test_a_comparison_cell_is_current_exactly_when_its_step_numbers_are(self) -> None:
        self._set_pair()
        self.table.add_compare()
        self._calculate(rois=(1, 2))
        self.assertTrue(self.table.roi_cell(1, self._compare_ref()).current)
        plan = self.m.experiment_plan
        plan.move_edge(0, "end", T0 + 2 * CUBE_MS, plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5]), together=True)
        self.assertFalse(self.table.roi_cell(1, self._compare_ref()).current)
        self.assertEqual(self.table.count_not_current([1, 2], []), 2)  # the comparison columns add no rows of their own to calculate

    def test_adding_comparison_columns_takes_the_next_pair_and_operation_not_yet_shown(self) -> None:
        self._set_pair(1)
        self._set_pair(2)
        got = [(c.pair, c.op) for c in (self.table.add_compare() for _ in range(5))]
        self.assertEqual(got, [(1, "difference"), (1, "snr"), (2, "difference"), (2, "snr"), (1, "difference")])  # all four combinations, then it starts again
        self.assertEqual(len([c for c in self.table.columns() if c.kind == "compare"]), 5)

    def test_with_no_pair_set_a_new_column_is_pair_1_and_its_tooltip_says_what_to_do(self) -> None:
        spec = self.table.add_compare()
        self.assertEqual(spec.pair, 1)
        self.assertNotIn(1, self.table.defined_pairs())
        column = [c for c in self.table.columns() if c.kind == "compare"][0]
        self.assertIn("Experimental plan", column.tooltip)

    def test_removing_changing_and_restoring_comparison_columns_tell_the_table(self) -> None:
        seen: list[int] = []
        self.table.columns_changed.connect(lambda: seen.append(1))
        a = self.table.add_compare()
        self.table.set_compare_op(a.uid, "snr")
        self.assertEqual(self.table.compare_specs()[0].op, "snr")
        self.table.remove_compare(a.uid)
        self.assertEqual(self.table.compare_specs(), ())
        self.assertEqual(len(seen), 3)
        self.table.restore_compare([(1, "snr"), (2, "difference"), (3, "bogus"), (0, "difference")])  # unknown operations and pair 0 are left out
        self.assertEqual([(c.pair, c.op) for c in self.table.compare_specs()], [(1, "snr"), (2, "difference")])
        self.table.restore_compare([(1, "snr"), (2, "difference")])
        self.assertEqual(len(seen), 4)  # the same list again changes nothing

    def test_a_comparison_column_title_names_the_pair_and_the_operation(self) -> None:
        a = self.table.add_compare()
        self.assertEqual(self.table.columns()[-1].title, "μ_S1 − μ_B1")
        self.table.set_compare_op(a.uid, "snr")
        self.assertEqual(self.table.columns()[-1].title, "(μ_S1 − μ_B1) / σ_B1")
        self.assertEqual(self.table.columns()[-1].export_label, "(S1 - B1) / noise of B1")


class StepTablePanelTest(StepTableBase):
    def setUp(self) -> None:
        super().setUp()
        self.ui = UiStateStore({})
        self.view = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        self.view.restore_ui_state(self.ui)
        self.group_id = self.m.roi_toolbox.create_group("Pair")
        self.m.roi_toolbox.add_rois_to_group([1], self.group_id)
        self.view.refresh_now()

    def tearDown(self) -> None:
        self.view.deleteLater()
        super().tearDown()

    def _show_steps(self) -> None:
        self.view._step_controls.columns_button.click()
        self.assertTrue(_pump(lambda: not self.table.is_running()))

    def _texts(self, column: int, view=None) -> list[str]:
        model = (view or self.view)._model
        out = []
        for header in model.group_headers():
            out.append(str(header.siblingAtColumn(column).data(Qt.ItemDataRole.DisplayRole)))
            for row in range(model.rowCount(header)):
                out.append(str(model.index(row, column, header).data(Qt.ItemDataRole.DisplayRole)))
        return out

    def test_there_are_no_step_columns_and_no_step_controls_until_they_are_switched_on(self) -> None:
        self.assertEqual(self.view._model.columnCount(), COLUMN_COUNT)
        self.assertFalse(self.view._step_controls.columns_button.isHidden())
        for widget in (self.view._step_controls.display_pick, self.view._step_controls.subtract, self.view._step_controls.refresh_button, self.view._step_controls.export_button):
            self.assertTrue(widget.isHidden())
        with self.assertRaises(ValueError) as caught:  # the export button shows this sentence
            self.view._step_controls.export_data()
        self.assertIn("calculate", str(caught.exception))

    def test_a_table_without_a_step_module_has_no_step_controls_at_all(self) -> None:
        plain = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry)
        self.assertTrue(plain._step_controls.columns_button.isHidden())
        self.assertEqual(plain._model.columnCount(), COLUMN_COUNT)
        plain.deleteLater()

    def test_switching_them_on_adds_one_column_per_step_calculates_the_rows_on_screen_and_fills_groups_and_rois(self) -> None:
        self._show_steps()
        self.assertEqual(self.view._model.columnCount(), COLUMN_COUNT + 2)
        self.assertEqual(self.view._model.headerData(COLUMN_COUNT, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole), "H2O_--_ch1F")
        for widget in (self.view._step_controls.display_pick, self.view._step_controls.subtract, self.view._step_controls.refresh_button, self.view._step_controls.export_button):
            self.assertFalse(widget.isHidden())
        texts = self._texts(COLUMN_COUNT)
        self.assertTrue(all(t and t != "None" for t in texts), texts)  # the group header, its ROI, and the Ungrouped header with ROI 2
        self.assertIn("±", texts[0])

    def test_the_display_picker_rows_show_only_the_symbols(self) -> None:
        pick = self.view._step_controls.display_pick
        self.assertEqual(pick.count(), 5)
        self.assertEqual([row.text().strip() for row in pick._rows], [""] * 5, "a row says it twice: the symbol and then the word")
        self.assertTrue(all(row.icon() and not row.icon().isNull() for row in pick._rows))
        self.assertTrue(all(row.toolTip() for row in pick._rows), "the words went into the tips, which must stay")

    def test_the_display_picker_chooses_mean_std_or_both(self) -> None:
        self._show_steps()
        for mode in (DISPLAY_MEAN, DISPLAY_STD, DISPLAY_BOTH):
            self.view._step_controls.display_pick.setCurrentIndex(self.view._step_controls.display_pick.findData(mode))
            texts = self._texts(COLUMN_COUNT)
            self.assertEqual("±" in texts[0], mode == DISPLAY_BOTH)
        mean_only = self.view._step_controls.display_pick.findData(DISPLAY_MEAN)
        self.view._step_controls.display_pick.setCurrentIndex(mean_only)
        self.assertEqual(self.ui.get("roi_table/step_display"), DISPLAY_MEAN)

    def test_an_out_of_date_number_is_dimmed_and_the_button_says_so(self) -> None:
        self._show_steps()
        model = self.view._model
        header = model.group_headers()[0]
        self.assertFalse(header.siblingAtColumn(COLUMN_COUNT).data(STEP_STALE_ROLE))
        plan = self.m.experiment_plan
        plan.move_edge(0, "end", T0 + 2 * CUBE_MS, plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5]), together=True)
        _pump(lambda: False, 0.2)
        self.assertTrue(header.siblingAtColumn(COLUMN_COUNT).data(STEP_STALE_ROLE))
        self.assertIn("out of date", self.view._step_controls.refresh_button.toolTip())
        self.view._step_controls.refresh_button.click()
        self.assertTrue(_pump(lambda: not self.table.is_running()))
        self.assertFalse(model.group_headers()[0].siblingAtColumn(COLUMN_COUNT).data(STEP_STALE_ROLE))
        _pump(lambda: False, 0.2)
        self.assertIn("up to date", self.view._step_controls.refresh_button.toolTip())

    def test_the_button_lights_when_the_rows_arrive_after_the_columns_were_switched_on_at_start_up(self) -> None:
        # At launch the table is blank, the saved "step columns on" is restored, and only then does the session bring the ROIs and groups (the table rebuilds ~100 ms after
        # them). The button was checked once, against the blank table ("up to date"), and never again: the rows had no numbers and it stayed grey.
        saved = UiStateStore({})
        saved.set("roi_table/steps_shown", True)
        late = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        late._rebuild(lambda: late._model.set_content([], [], self.m.roi_toolbox.detection_settings(), late._unit(), None))  # blank, as at launch
        late.restore_ui_state(saved)
        self.assertFalse(late._step_controls.refresh_button.isHidden(), "the columns are on")
        _pump(lambda: False, 0.2)
        self.assertEqual(late._model.rows(), [])
        late.refresh_now()  # the session's ROIs and groups arrive
        _pump(lambda: False, 0.2)
        self.assertTrue(late._model.rows())
        self.assertIn("no numbers", late._step_controls.refresh_button.toolTip())
        self.assertNotIn("up to date", late._step_controls.refresh_button.toolTip())
        late.deleteLater()

    def _launched_panel(self, *, steps_on: bool = True) -> RoiTablePanel:
        """A table as at launch: the saved state restored (step columns on or off), no numbers yet."""
        saved = UiStateStore({})
        saved.set("roi_table/steps_shown", steps_on)
        panel = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        panel.restore_ui_state(saved)
        self.addCleanup(panel.deleteLater)
        return panel

    def test_after_launch_the_rows_on_screen_are_calculated_once_without_a_click(self) -> None:
        late = self._launched_panel()
        _pump(lambda: False, 0.2)
        self.assertEqual(self.done, [], "restoring the saved state calculates nothing by itself")
        self.assertIn("no numbers", late._step_controls.refresh_button.toolTip())
        late.calculate_at_startup()
        self.assertTrue(_pump(lambda: bool(self.done) and not self.table.is_running()), "the start-up calculation never finished")
        self.assertEqual(self.done[-1][0], "completed")
        texts = self._texts(COLUMN_COUNT, late)
        self.assertTrue(all(t and t != "None" for t in texts), texts)  # the group header, its ROI, and the Ungrouped header with ROI 2
        _pump(lambda: False, 0.2)
        self.assertIn("up to date", late._step_controls.refresh_button.toolTip())
        runs = len(self.done)
        late.calculate_at_startup()  # everything is current: no second run
        _pump(lambda: False, 0.2)
        self.assertEqual(len(self.done), runs)

    def test_the_start_up_calculation_is_the_same_as_the_refresh_buttons(self) -> None:
        late = self._launched_panel()
        late.calculate_at_startup()
        self.assertTrue(_pump(lambda: bool(self.done) and not self.table.is_running()))
        by_start_up = self._texts(COLUMN_COUNT, late)
        self.table.clear()
        late._step_controls.refresh_button.click()
        self.assertTrue(_pump(lambda: len(self.done) > 1 and not self.table.is_running()))
        self.assertEqual(self._texts(COLUMN_COUNT, late), by_start_up)

    def test_the_start_up_calculation_stays_quiet_when_nobody_asked_for_numbers(self) -> None:
        off = self._launched_panel(steps_on=False)
        off.calculate_at_startup()
        hidden = self._launched_panel()
        hidden.set_panel_visible(False)
        hidden.calculate_at_startup()
        self.m.scan_results.set_scan_file(None)
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is None))
        noscan = self._launched_panel()
        noscan.calculate_at_startup()
        _pump(lambda: False, 0.2)
        self.assertFalse(self.table.is_running())
        self.assertEqual(self.done, [], "step columns off, table hidden, no scan: nothing is calculated")
        self.assertEqual(self.notices, [], "and the user is not told off for something they did not ask for")

    def test_collapsed_groups_do_not_get_roi_rows_calculated(self) -> None:
        self.view.set_group_only(True)
        self._show_steps()
        rois, groups = self.view._step_controls.visible_rows()
        self.assertEqual(rois, [])
        self.assertEqual(len(groups), 2)  # Pair and Ungrouped
        self.assertIsNone(self.table.roi_cell(1, self._refs()[0]))
        self.assertIsNotNone(self.table.group_cell(self.group_id, (1,), self._refs()[0]))

    def test_clicking_a_step_header_sorts_the_groups_and_the_rois_by_the_value_and_a_number_less_row_comes_last(self) -> None:
        group_b = self.m.roi_toolbox.create_group("Other")
        self.m.roi_toolbox.add_rois_to_group([2], group_b)
        self.view.refresh_now()
        self._show_steps()
        column = COLUMN_COUNT + 1
        self.view._on_header_clicked(column)  # ascending
        order_up = [self.view._model.index(i, 0).data(Qt.ItemDataRole.DisplayRole) for i in range(self.view._model.rowCount())]
        self.view._on_header_clicked(column)  # descending
        order_down = [self.view._model.index(i, 0).data(Qt.ItemDataRole.DisplayRole) for i in range(self.view._model.rowCount())]
        self.assertEqual(sorted(order_up), sorted(order_down))
        self.assertEqual(order_up, list(reversed(order_down)))
        self.assertTrue(self.view._model.sorts_by_step())
        self.assertIsNone(self.ui.get("roi_table/sort"))  # a step sort is not remembered (its numbers are calculated on request)
        # the manual moves of groups are off while the groups are listed by a value
        self.assertTrue(self.view._groups_sorted_by_label())

    def test_the_step_columns_follow_the_steps_and_a_sort_on_a_vanished_step_goes_back_to_the_number(self) -> None:
        self._show_steps()
        self.view._on_header_clicked(COLUMN_COUNT + 1)
        self.m.experiment_plan.remove_steps([1])  # the second step goes
        self.assertEqual(self.view._model.columnCount(), COLUMN_COUNT + 1)
        self.assertEqual(self.view._model.sort_state(), (COLUMN_ID, False))

    def test_the_state_is_remembered_and_restored(self) -> None:
        self._show_steps()
        self.view._step_controls.display_pick.setCurrentIndex(self.view._step_controls.display_pick.findData(DISPLAY_STD))
        self.view._on_section_resized(COLUMN_COUNT, 100, 140)
        self.assertEqual(self.ui.get("roi_table/steps_shown"), True)
        self.assertEqual(self.ui.get("roi_table/step_display"), DISPLAY_STD)
        self.assertEqual(self.ui.get("roi_table/step_width_manual"), 140)  # (a width the user gave them; fitted widths are never saved, see test_lspri_step_column_width.py)
        again = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        again.restore_ui_state(self.ui)
        self.assertEqual(again._model.columnCount(), COLUMN_COUNT + 2)
        self.assertEqual(again._model.step_display(), DISPLAY_STD)
        self.assertEqual(again._tree.columnWidth(COLUMN_COUNT), 140)
        again.deleteLater()

    def test_all_step_columns_share_one_width(self) -> None:
        self._show_steps()
        self.view._tree.setColumnWidth(COLUMN_COUNT, 150)
        self.assertEqual(self.view._tree.columnWidth(COLUMN_COUNT + 1), 150)

    def test_the_export_writes_every_row_with_the_chosen_columns_and_marks_out_of_date_rows(self) -> None:
        self._show_steps()
        data = self.view._step_controls.export_data()
        self.assertIsNotNone(data)
        table = data.tables["roi_table_steps"]
        self.assertEqual(table.header[:3], ("row", "group", "roi_id"))
        self.assertTrue(any("step 1 (H2O_--_ch1F) mean (nm)" == h for h in table.header))
        self.assertTrue(any(h.endswith("sd (nm)") for h in table.header))
        self.assertEqual(table.header[-1], "up_to_date")
        kinds = [row[0] for row in table.rows]
        self.assertEqual(kinds, ["group", "roi", "group", "roi"])  # Pair, ROI 1, Ungrouped, ROI 2
        self.assertTrue(all(row[-1] == "yes" for row in table.rows))
        plan = self.m.experiment_plan
        plan.move_edge(0, "end", T0 + 2 * CUBE_MS, plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5]), together=True)
        stale = self.view._step_controls.export_data().tables["roi_table_steps"]
        self.assertTrue(all(row[-1] == "no" for row in stale.rows))
        self.assertTrue(stale.notes)
        self.view._step_controls.display_pick.setCurrentIndex(self.view._step_controls.display_pick.findData(DISPLAY_MEAN))
        mean_only = self.view._step_controls.export_data().tables["roi_table_steps"]
        self.assertFalse(any(h.endswith("sd (nm)") for h in mean_only.header))
        self.assertEqual(self.view._step_controls.export_data().info["display"], DISPLAY_MEAN)

    # -- SEM (2026-11-06) ----------------------------------------------------------------------------------------------------------

    def _pick(self, mode: str) -> None:
        pick = self.view._step_controls.display_pick
        pick.setCurrentIndex(pick.findData(mode))

    def _shown(self, roi_id: int, column: int) -> str:
        return self.view._model.roi_index(roi_id, column).data(Qt.ItemDataRole.DisplayRole)

    def test_the_sem_is_the_sd_over_the_root_of_the_cubes_and_the_two_sem_displays_show_it(self) -> None:
        self._show_steps()
        cell = self.table.roi_cell(1, self._refs()[0])
        self.assertGreater(cell.n, 1)
        self.assertAlmostEqual(cell.sem, cell.sd / np.sqrt(cell.n), places=12)
        self._pick(DISPLAY_SEM)
        digits = self.view._model._decimals  # the table's decimals follow the SEM now, so read them after the switch
        self.assertEqual(self._shown(1, COLUMN_COUNT), f"{cell.sem:.{digits}f}")
        self._pick(DISPLAY_MEAN_SEM)
        digits = self.view._model._decimals
        self.assertEqual(self._shown(1, COLUMN_COUNT), f"{cell.mean:.{digits}f} ± {cell.sem:.{digits}f}")
        header = self.view._model.group_headers()[0]
        self.assertIn("±", str(header.siblingAtColumn(COLUMN_COUNT).data(Qt.ItemDataRole.DisplayRole)))  # a group's header cell follows the display too

    def test_the_tooltip_of_a_cell_gives_the_sem_whatever_the_display(self) -> None:
        self._show_steps()
        tip = self.view._model.roi_index(1, COLUMN_COUNT).data(Qt.ItemDataRole.ToolTipRole)
        self.assertIn("SEM", tip)
        self.assertIn("sqrt(", tip)

    def test_the_sem_display_is_remembered_and_restored(self) -> None:
        self._show_steps()
        self._pick(DISPLAY_MEAN_SEM)
        self.assertEqual(self.ui.get("roi_table/step_display"), DISPLAY_MEAN_SEM)
        again = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        again.restore_ui_state(self.ui)
        self.assertEqual(again._model.step_display(), DISPLAY_MEAN_SEM)
        self.assertEqual(again._step_controls.display_pick.currentData(), DISPLAY_MEAN_SEM)
        again.deleteLater()

    def test_the_export_writes_the_sem_where_the_display_shows_it(self) -> None:
        self._show_steps()
        cell = self.table.roi_cell(1, self._refs()[0])
        self._pick(DISPLAY_SEM)
        only = self._controls().export_data().tables["roi_table_steps"]
        self.assertTrue(any(h.endswith("sem (nm)") for h in only.header))
        self.assertFalse(any(h.endswith("sd (nm)") or h.endswith("mean (nm)") for h in only.header))
        self._pick(DISPLAY_MEAN_SEM)
        both = self._controls().export_data().tables["roi_table_steps"]
        first = both.header.index("step 1 (H2O_--_ch1F) sem (nm)")
        roi_row = next(row for row in both.rows if row[0] == "roi" and row[2] == 1)
        self.assertAlmostEqual(roi_row[first], cell.sem, places=12)
        self.assertAlmostEqual(roi_row[first - 1], cell.mean, places=12)
        self.assertEqual(self._controls().export_data().info["display"], DISPLAY_MEAN_SEM)

    def test_a_difference_column_has_the_two_sems_added_in_quadrature_and_a_ratio_keeps_its_value_in_every_display(self) -> None:
        self._show_steps()
        self.m.experiment_plan.set_role("baseline", 0, 1)
        self.m.experiment_plan.set_role("signal", 1, 1)
        self._controls().add_compare_button.click()
        column = COLUMN_COUNT + 2
        baseline, signal = self.table.roi_cell(1, self._refs()[0]), self.table.roi_cell(1, self._refs()[1])
        diff = self.table.roi_cell(1, self.view._model.step_columns()[-1].ref)
        self.assertAlmostEqual(diff.sem, float(np.hypot(baseline.sem, signal.sem)), places=12)
        self._pick(DISPLAY_SEM)
        digits = self.view._model._decimals
        self.assertEqual(self._shown(1, column), f"{diff.sem:.{digits}f}")
        uid = self.view._model.step_columns()[-1].ref[1]
        self._controls().set_compare_op(uid, "snr")
        for mode in (DISPLAY_MEAN, DISPLAY_STD, DISPLAY_SEM, DISPLAY_BOTH, DISPLAY_MEAN_SEM):
            self._pick(mode)
            self.assertTrue(self._shown(1, column), f"the signal-to-noise cell is empty in {mode}")
            self.assertNotIn("±", self._shown(1, column))

    # -- comparison columns (step 2) ------------------------------------------------------------------------------------------------

    def _controls(self):
        return self.view._step_controls

    def _press_header(self, section: int, dx: int) -> QMouseEvent:
        header = self.view._tree.header()
        point = QPointF(header.sectionViewportPosition(section) + dx, 6.0)
        press = QMouseEvent(QEvent.Type.MouseButtonPress, point, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, point, point, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        header.mousePressEvent(press)
        header.mouseReleaseEvent(release)
        return press

    def test_the_add_button_makes_a_comparison_column_that_shows_the_difference_and_its_sd(self) -> None:
        self._show_steps()
        self.m.experiment_plan.set_role("baseline", 0, 1)
        self.m.experiment_plan.set_role("signal", 1, 1)
        self._controls().add_compare_button.click()
        model = self.view._model
        self.assertEqual(model.columnCount(), COLUMN_COUNT + 3)
        self.assertEqual(model.headerData(COLUMN_COUNT + 2, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole), "μ_S1 − μ_B1")
        self.assertEqual(model.headerData(COLUMN_COUNT + 2, Qt.Orientation.Horizontal, COLUMN_KIND_ROLE), "compare")
        self.assertEqual(model.headerData(COLUMN_COUNT, Qt.Orientation.Horizontal, COLUMN_KIND_ROLE), "step")
        texts = self._texts(COLUMN_COUNT + 2)
        self.assertTrue(all("±" in t for t in texts), texts)
        self.assertTrue(all(widget.isHidden() is False for widget in (self._controls().add_compare_button,)))

    def test_the_new_column_says_in_the_footer_when_its_pair_is_not_set(self) -> None:
        self._show_steps()
        self._controls().add_compare_button.click()
        self.assertIn("Choose the baseline and signal step", self.view._footer.text())
        self.assertTrue(all(t == "" for t in self._texts(COLUMN_COUNT + 2)))

    def test_the_arrow_in_the_header_opens_the_menu_and_a_click_there_is_not_a_sort(self) -> None:
        self._show_steps()
        self._controls().add_compare_button.click()
        header = self.view._tree.header()
        asked: list[int] = []
        header.compare_menu_requested.connect(lambda section, _pos: asked.append(section))
        menus: list[QtWidgets.QMenu] = []

        def capture(menu: QtWidgets.QMenu, *_args: object, **_kwargs: object) -> None:  # the real menu would wait for a click
            menus.append(menu)

        with mock.patch.object(QtWidgets.QMenu, "exec", capture):
            self._press_header(COLUMN_COUNT + 2, 6)  # on the arrow
            self.assertEqual(asked, [COLUMN_COUNT + 2])
            self.assertFalse(self.view._model.sorts_by_step())  # the click on the arrow is not a sort click
            self.assertEqual(len(menus), 1)
            pick = menus[0].parent()  # the menu is a `PickMenu`: each row is the formula drawn with subscripts (a picture) and a short name
            self.assertEqual([row.text().strip() for row in pick._rows], ["Shift", "Signal to noise"])
            self.assertTrue(all(not row.icon().isNull() for row in pick._rows))
            self.assertEqual([row.isChecked() for row in pick._rows], [True, False])
            self.assertTrue(all(row.toolTip() for row in pick._rows), "the explanation moved into the tips")
            self.assertEqual([b.text() for b in menus[0].findChildren(QtWidgets.QToolButton) if b not in pick._rows], ["Remove this column"])
            self._press_header(COLUMN_COUNT + 0, 6)  # the same place on a step column: a plain header, no menu
            self.assertEqual(asked, [COLUMN_COUNT + 2])
            self.assertEqual(len(menus), 1)

    def test_the_menu_rows_choose_the_calculation_and_its_footer_removes_the_column(self) -> None:
        self._show_steps()
        self._controls().add_compare_button.click()
        header = self.view._tree.header()
        section = self.view._model.columnCount() - 1
        menus: list[QtWidgets.QMenu] = []

        def capture(menu: QtWidgets.QMenu, *_args: object, **_kwargs: object) -> None:
            menus.append(menu)

        with mock.patch.object(QtWidgets.QMenu, "exec", capture):
            self._controls()._show_compare_menu(section, header.mapToGlobal(QPoint(0, 0)))
            pick = menus[0].parent()
            pick._rows[1].click()  # "Signal to noise"
            self.assertEqual(self.table.compare_specs()[0].op, "snr")
            self.assertEqual(self.view._model.headerData(section, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole), "(μ_S1 − μ_B1) / σ_B1")
            self._controls()._show_compare_menu(section, header.mapToGlobal(QPoint(0, 0)))
            reopened = menus[1].parent()
            self.assertEqual([row.isChecked() for row in reopened._rows], [False, True], "the menu does not show the column's calculation")
            remove = next(b for b in menus[1].findChildren(QtWidgets.QToolButton) if b.text() == "Remove this column")
            remove.click()
        self.assertEqual(self.table.compare_specs(), ())
        self.assertEqual(self.view._model.columnCount(), section)

    def test_choosing_an_operation_changes_the_column_and_remove_takes_it_away(self) -> None:
        self._show_steps()
        self.m.experiment_plan.set_role("baseline", 0, 1)
        self.m.experiment_plan.set_role("signal", 1, 1)
        self._controls().add_compare_button.click()
        uid = self.view._model.step_columns()[-1].ref[1]
        self._controls().set_compare_op(uid, "snr")
        model = self.view._model
        self.assertEqual(model.headerData(COLUMN_COUNT + 2, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole), "(μ_S1 − μ_B1) / σ_B1")
        self.assertTrue(all(t and "±" not in t for t in self._texts(COLUMN_COUNT + 2)))  # a ratio has no SD
        self._controls().remove_compare(uid)
        self.assertEqual(model.columnCount(), COLUMN_COUNT + 2)

    def test_a_comparison_column_sorts_the_groups_and_the_rois_by_its_value(self) -> None:
        group_b = self.m.roi_toolbox.create_group("Other")
        self.m.roi_toolbox.add_rois_to_group([2], group_b)
        self.view.refresh_now()
        self._show_steps()
        self.m.experiment_plan.set_role("baseline", 0, 1)
        self.m.experiment_plan.set_role("signal", 1, 1)
        self._controls().add_compare_button.click()
        column = COLUMN_COUNT + 2
        ref = self.view._model.step_columns()[-1].ref
        value_pair = self.table.group_cell(self.group_id, (1,), ref).mean
        value_other = self.table.group_cell(group_b, (2,), ref).mean
        self.view._on_header_clicked(column)  # ascending
        names = [self.view._model.index(i, 0).data(Qt.ItemDataRole.DisplayRole) for i in range(self.view._model.rowCount())]
        self.assertEqual(names, ["Pair", "Other"] if value_pair < value_other else ["Other", "Pair"])
        self.assertTrue(self.view._model.sorts_by_step())

    def test_the_comparison_columns_are_remembered_and_come_back_with_the_step_columns(self) -> None:
        self._show_steps()
        self._controls().add_compare_button.click()
        self._controls().add_compare_button.click()
        self.assertEqual(self.ui.get("roi_table/compare_columns"), [[1, "difference"], [1, "snr"]])
        saved = {"roi_table/steps_shown": True, "roi_table/compare_columns": [[1, "difference"], [1, "snr"]]}  # what a new launch reads
        self.table.restore_compare([])  # and nothing is held yet
        again = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        again.restore_ui_state(UiStateStore(saved))
        self.assertEqual(again._model.columnCount(), COLUMN_COUNT + 4)
        self.assertEqual([(c.pair, c.op) for c in self.table.compare_specs()], [(1, "difference"), (1, "snr")])
        again.deleteLater()

    def test_the_export_has_the_comparison_columns(self) -> None:
        self._show_steps()
        self.m.experiment_plan.set_role("baseline", 0, 1)
        self.m.experiment_plan.set_role("signal", 1, 1)
        self._controls().add_compare_button.click()
        self._controls().add_compare_button.click()
        table = self._controls().export_data().tables["roi_table_steps"]
        self.assertIn("S1 - B1 difference (nm)", table.header)
        self.assertIn("S1 - B1 sd (nm)", table.header)
        self.assertIn("(S1 - B1) / noise of B1 signal-to-noise", table.header)
        self.assertNotIn("(S1 - B1) / noise of B1 sd", table.header)
        self.assertEqual(len(set(table.header)), len(table.header))
        info = self._controls().export_data().info
        self.assertEqual([c["operation"] for c in info["comparisons"]], ["difference", "snr"])

    def test_the_task_indicator_hears_of_a_long_calculation_and_can_cancel_it(self) -> None:
        self.view.task.begin("fitting")
        self.assertTrue(self.view.task.is_active())
        self.view.task.end("cancelled")
        self.view.cancel()  # nothing running: harmless

    def test_the_kind_of_a_group_header_is_unchanged(self) -> None:
        self.assertEqual(self.view._model.group_headers()[0].data(KIND_ROLE), "group")


class StartUpCalculationWiringTest(unittest.TestCase):
    """The ROI table hears of the end of the start-up from the tracker the status bar shows, and only of a start-up that really completed."""

    class _Panel:
        def __init__(self) -> None:
            self.calls = 0

        def calculate_at_startup(self) -> None:
            self.calls += 1

    def _tracker(self) -> StartupTracker:
        return StartupTracker([StartupStep("all", "loading", lambda: True)], poll_ms=10, settle_polls=1)

    def test_a_completed_start_up_calculates_the_table_once(self) -> None:
        panel, tracker = self._Panel(), self._tracker()
        app_rewrite._wire_startup_calculation(tracker, panel)
        tracker.start()
        self.assertEqual(panel.calls, 1)
        tracker.cancel()  # (already over: still one)
        self.assertEqual(panel.calls, 1)

    def test_a_failed_cancelled_or_not_started_start_up_calculates_nothing(self) -> None:
        for end in ("fail", "cancel"):
            panel = self._Panel()
            tracker = StartupTracker([StartupStep("all", "loading", lambda: False)], poll_ms=10, settle_polls=1)
            app_rewrite._wire_startup_calculation(tracker, panel)
            tracker.start()
            getattr(tracker, end)(*(("could not open",) if end == "fail" else ()))
            self.assertEqual(panel.calls, 0, end)
        idle = self._Panel()
        app_rewrite._wire_startup_calculation(self._tracker(), idle)  # never started: no dataset was reopened at launch
        self.assertEqual(idle.calls, 0)


if __name__ == "__main__":
    unittest.main()
