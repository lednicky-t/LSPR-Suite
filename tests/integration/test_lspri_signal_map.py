"""The signal map (2026-11-10): `SignalMapModule`, its Analysis-tab group and its drawing on the Image plot. Real modules and a real scan on the small two-step
dataset of `test_lspri_plan_bar` (the same set-up as `test_lspri_step_table`); driven through methods and signals, the window is not shown.

Pinned: the map's values are the step table's comparison numbers (no second calculation; a ROI fitted for the table is not fitted again); switching it on
calculates, other changes only mark it out of date; by group gives every member its group's value; the ROI overlay draws the map's colours and gets its own back
when the map is off; the legend and the hover text follow their switches; the export writes the values."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.integration.test_lspri_step_table import StepTableBase, _pump

from lspr_imaging_app.analysis.signal_map import NO_VALUE_HEX, PER_GROUP, SHOW_CUBE, SHOW_SNR, SHOW_STEP, full_range
from lspr_imaging_app.analysis.step_table import OP_DIFFERENCE, compare_values
from lspr_imaging_app.panels.image.roi_overlay import RoiOverlay
from lspr_imaging_app.panels.image.signal_map_display import SignalMapDisplay
from lspr_imaging_app.panels.image.signal_map_group import SignalMapGroup
from lspr_imaging_app.selection.signal_map_module import SignalMapModule

import pyqtgraph as pg


class SignalMapTest(StepTableBase):
    def setUp(self) -> None:
        super().setUp()
        self.map = SignalMapModule(self.table, self.m.roi_toolbox, self.m.experiment_plan)
        self.views: list[object] = []
        self.map.view_changed.connect(self.views.append)

    def _set_pair(self) -> None:
        self.m.experiment_plan.set_role("baseline", 0, 1)
        self.m.experiment_plan.set_role("signal", 1, 1)

    def _settle(self) -> None:
        self.assertTrue(_pump(lambda: not self.table.is_running() and not self.map._rebuild_timer.isActive()), "the map never settled")

    def test_without_a_pair_the_map_is_grey_and_says_what_to_choose(self) -> None:
        self.map.set_enabled(True)
        view = self.map.view()
        self.assertIsNotNone(view)
        self.assertTrue(all(c == NO_VALUE_HEX for c in view.colors.values()))
        self.assertIn("B1", self.map.status())
        self.assertFalse(self.table.is_running())  # nothing to calculate yet

    def test_switching_on_calculates_and_the_values_are_the_step_tables_shift(self) -> None:
        self._set_pair()
        self.map.set_enabled(True)
        self._settle()
        view = self.map.view()
        base, signal = (s.ref for s in self.m.experiment_plan.steps()[:2])
        for roi in (1, 2):
            b, _ = self.table.roi_step_value(roi, base)
            s, _ = self.table.roi_step_value(roi, signal)
            self.assertAlmostEqual(view.values[roi], compare_values(OP_DIFFERENCE, b, s)[1], places=12)
        self.assertFalse(view.stale)
        self.assertEqual(view.missing, 0)
        self.assertFalse(self.map.needs_refresh())
        fitted = len(self.fits)
        self.map.set_show(SHOW_SNR)  # another quantity of the same numbers: nothing is fitted again
        self._settle()
        self.assertEqual(len(self.fits), fitted)
        self.assertEqual(self.map.view().unit, "")

    def test_a_changed_input_marks_the_map_out_of_date_without_calculating(self) -> None:
        self._set_pair()
        self.map.set_enabled(True)
        self._settle()
        runs = len(self.done)
        self.axis.set_baseline_enabled(not self.table.baseline_enabled())  # the Subtract switch: the numbers are no longer current
        self._settle()
        self.assertEqual(len(self.done), runs)
        self.assertTrue(self.map.view().stale)
        self.assertTrue(self.map.needs_refresh())
        self.assertTrue(self.map.calculate())
        self._settle()
        self.assertFalse(self.map.view().stale)

    def test_by_group_every_member_gets_the_groups_value(self) -> None:
        self._set_pair()
        self.m.roi_toolbox.group_rois([1, 2], "A")
        self.map.set_per(PER_GROUP)
        self.map.set_enabled(True)
        self._settle()
        view = self.map.view()
        self.assertTrue(math.isfinite(view.values[1]))
        self.assertEqual(view.values[1], view.values[2])
        self.assertEqual(view.colors[1], view.colors[2])
        self.assertIn("group", view.source[1])

    def test_one_steps_mean_needs_a_step(self) -> None:
        self.map.set_show(SHOW_STEP)
        self.map.set_enabled(True)
        self.assertIn("choose the step", self.map.status())
        self.map.set_step(self.m.experiment_plan.steps()[1].ref)
        self.assertTrue(self.map.calculate())
        self._settle()
        self.assertTrue(all(math.isfinite(v) for v in self.map.view().values.values()))

    def test_the_export_writes_one_row_per_roi(self) -> None:
        from lspr_imaging_app.analysis.signal_map import signal_map_table
        from lspr_imaging_app.storage.plot_export import write_plot_export

        self._set_pair()
        self.map.set_enabled(True)
        self._settle()
        table = signal_map_table(self.map.view(), self.map.roi_ids())
        self.assertEqual([row[0] for row in table.rows], [1, 2])
        with tempfile.TemporaryDirectory() as folder:
            written = write_plot_export(Path(folder) / "map.csv", {"signal_map": table}, self.map.export_info())
            self.assertTrue(written[0].is_file())

    def test_current_cube_reads_the_line_at_the_cube_with_a_whole_dataset_range(self) -> None:
        stored = self.m.scan_results.stored()
        cubes = list(stored.cube_indices)
        self.map.set_show(SHOW_CUBE)
        self.map.set_cube(cubes[0])
        self.map.set_enabled(True)
        self._settle()
        lines = {r: self.table.roi_line(r)[0] for r in (1, 2)}
        for roi in (1, 2):
            self.assertAlmostEqual(self.map.view().values[roi], float(lines[roi][0]), places=12)
        everything = np.concatenate(list(lines.values()))
        low, high = full_range(everything)  # Auto: the lines' own min..max, not symmetric around zero
        first = self.map.view()
        self.assertAlmostEqual(first.low, low, places=12)
        self.assertAlmostEqual(first.high, high, places=12)
        runs, fitted = len(self.done), len(self.fits)
        self.map.set_cube(cubes[-1])  # the slider moves: a lookup, no calculation, the same colour range
        self._settle()
        self.assertEqual((len(self.done), len(self.fits)), (runs, fitted))
        view = self.map.view()
        self.assertAlmostEqual(view.values[1], float(lines[1][-1]), places=12)
        self.assertEqual((view.low, view.high), (first.low, first.high))
        self.assertIn(f"Cube {cubes[-1]}", view.title)

    def test_subtract_baseline_from_the_map_switches_the_setting_and_recalculates(self) -> None:
        self.map.set_show(SHOW_CUBE)
        self.map.set_enabled(True)
        self._settle()
        before = self.map.baseline_enabled()
        runs = len(self.done)
        self.map.set_baseline_enabled(not before)
        self._settle()
        self.assertEqual(self.table.baseline_enabled(), not before)  # the one switch, the Sensorgram's too
        self.assertGreater(len(self.done), runs)  # a request from the map: recalculated
        self.assertFalse(self.map.view().stale)
        self.assertIn("position" if before else "shift from baseline", self.map.view().title)

    def test_off_hands_out_no_view(self) -> None:
        self._set_pair()
        self.map.set_enabled(True)
        self._settle()
        self.map.set_enabled(False)
        self.assertIsNone(self.map.view())
        self.assertIsNone(self.views[-1])


class SignalMapDrawingTest(unittest.TestCase):
    """The drawing, on a bare plot: no module, no dataset."""

    def setUp(self) -> None:
        from lspr_imaging_app.analysis.signal_map import SignalMapView

        self.widget = pg.PlotWidget()
        self.plot = self.widget.getPlotItem()
        self.redraws: list[int] = []
        self.display = SignalMapDisplay(self.plot, self.widget, lambda: self.redraws.append(1), lambda x, y: 1 if x < 5 else None)
        self.view = SignalMapView(3, {1: "#123456"}, {1: 0.5}, {1: 0.1}, {1: "ROI 1"}, "Shift S1 − B1", "nm", "viridis", 0.0, 1.0)

    def tearDown(self) -> None:
        self.widget.deleteLater()

    def test_a_view_redraws_the_rois_and_shows_the_legend_with_its_range(self) -> None:
        self.display.set_view(self.view)
        self.assertEqual(self.redraws, [1])
        self.assertEqual(self.display.colors(), {1: "#123456"})
        self.assertEqual(self.display.revision(), 3)
        self.assertTrue(self.display.legend.isVisible())
        self.assertEqual(self.display.legend.labels(), ("Shift S1 − B1", "0.000", "0.500", "1.000 nm"))
        self.display.set_legend_visible(False)
        self.assertFalse(self.display.legend.isVisible())
        self.display.set_view(None)
        self.assertIsNone(self.display.colors())

    def test_the_overlay_draws_the_override_colours_and_keeps_its_own_for_the_rest(self) -> None:
        from lspr_imaging_app.domain.roi_model import AreaRoi

        overlay = RoiOverlay(self.plot)
        rois = [AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_diameter_px=6.0, sample_color_hex="#ff0000"),
                AreaRoi(area_roi_id=2, center_x=30.0, center_y=10.0, sample_diameter_px=6.0, sample_color_hex="#00ff00")]
        centers = np.array([[10.0, 10.0], [30.0, 10.0]])
        seen: list[list[str]] = []
        real = overlay.sample_circles.set_circles
        overlay.sample_circles.set_circles = lambda xs, ys, colors: (seen.append(list(colors)), real(xs, ys, colors))  # type: ignore[method-assign]
        overlay.draw(rois, centers, set(), np.eye(2, 3), 8.0, 12.0, color_override={1: "#123456"})
        self.assertEqual(seen[-1], ["#123456", "#00ff00"])
        overlay.draw(rois, centers, set(), np.eye(2, 3), 8.0, 12.0)
        self.assertEqual(seen[-1], ["#ff0000", "#00ff00"])

    def test_the_group_greys_the_controls_that_do_not_apply(self) -> None:
        group = SignalMapGroup()
        self.assertFalse(group.step_picker.isEnabled())
        self.assertFalse(group.low_spin.isEnabled())
        group.show_combo.setCurrentIndex(group.show_combo.findData(SHOW_STEP))
        self.assertTrue(group.step_picker.isEnabled())
        self.assertFalse(group.pair_combo.isEnabled())
        group.set_state("Signal map: something", True)
        self.assertFalse(group.refresh_button.isEnabled())  # off: nothing to refresh
        group.map_button.setChecked(True)
        self.assertTrue(group.refresh_button.isEnabled())
        group.deleteLater()

    def test_the_legend_says_which_part_was_clicked(self) -> None:
        self.widget.resize(600, 400)
        self.display.set_view(self.view)
        legend = self.display.legend
        layout = legend._layout()
        self.assertEqual(legend.boundingRect(), layout["chip"])  # only the chip takes clicks, not the whole image
        self.assertEqual(legend.part_at(layout["bar"].center()), "scheme")
        self.assertEqual(legend.part_at(layout["low"].center()), "low")
        self.assertEqual(legend.part_at(layout["high"].center()), "high")
        self.assertIsNone(legend.part_at(layout["chip"].bottomLeft() + pg.QtCore.QPointF(-5.0, 5.0)))

    def test_editing_a_legend_number_makes_the_range_fixed_and_keeps_the_other_end(self) -> None:
        group = SignalMapGroup()
        seen: list[tuple[str, float, float]] = []
        group.range_changed.connect(lambda mode, low, high: seen.append((mode, low, high)))
        _menu, spin = group.bound_editor("high", -0.2, 0.8)
        self.assertAlmostEqual(spin.value(), 0.8)
        spin.setValue(1.5)
        spin.lineEdit().returnPressed.emit()
        self.assertEqual(seen, [("fixed", -0.2, 1.5)])  # one announcement, not one per control
        self.assertEqual(group.range_mode(), "fixed")
        group.deleteLater()

    def test_the_legend_menus_change_the_ribbon_controls(self) -> None:
        group = SignalMapGroup()
        schemes: list[str] = []
        group.scheme_changed.connect(schemes.append)
        menu = group.scheme_menu()
        buttons = [a.defaultWidget() for a in menu.actions()]
        self.assertEqual(len(buttons), group.scheme_combo.count())
        buttons[2].click()
        self.assertEqual(schemes, [group.scheme_combo.itemData(2)])
        group.set_fixed_bound("low", -1.0, 0.0, 1.0)
        context = group.context_menu()
        auto = next(a for a in context.actions() if a.text() == "Auto scale")
        auto.trigger()
        self.assertEqual(group.range_mode(), "auto")
        pair_menu = next(a.menu() for a in context.actions() if a.menu() is not None and a.text() == "Pair")
        self.assertTrue(pair_menu.isEnabled())
        show_menu = next(a.menu() for a in context.actions() if a.menu() is not None and a.text() == "Show")
        next(a for a in show_menu.actions() if a.text() == group.show_combo.itemText(group.show_combo.findData(SHOW_STEP))).trigger()
        self.assertEqual(group.show_key(), SHOW_STEP)
        legend_action = next(a for a in group.context_menu().actions() if a.text() == "Legend")
        legend_action.trigger()
        self.assertFalse(group.legend_button.isChecked())
        toggled: list[bool] = []
        group.baseline_toggled.connect(toggled.append)
        next(a for a in group.context_menu().actions() if a.text().startswith("Subtract baseline")).trigger()
        self.assertEqual(toggled, [True])
        self.assertTrue(group.baseline_check.isChecked())
        group.show_combo.setCurrentIndex(group.show_combo.findData(SHOW_CUBE))
        self.assertFalse(group.pair_combo.isEnabled())  # current cube needs no pair
        group.deleteLater()


if __name__ == "__main__":
    unittest.main()
