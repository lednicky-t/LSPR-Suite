"""The ribbon of the LbL and S_bulk plot windows (2026-11-22): the Points & lines, Uncertainty and Legend menus.

What is pinned:
- **one definition, separate values** (maintainer, 2026-11-22): the Sensorgram's View tab and both windows build their menus from `PlotLookControls`, so the same rows are in every
  plot's menu (a new option is added once); but each plot has its own values and its own remembered keys, so a change in one plot changes no other;
- what each option changes in what the two windows draw (points, lines, joined, bars / band, SD / SEM, legend), and that the numbers themselves are not touched;
- the remembered state; the SD arithmetic of the LbL points; the new pictures draw something.
A real `QApplication` in-process (offscreen); no dataset files.
"""

from __future__ import annotations

import math
import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np
    import pyqtgraph as pg

    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.lbl_series import lbl_points
    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
    from lspr_imaging_app.panels.sensorgram.lbl_plot import SHIFT_CYCLE, LblPlotWindow, LblStep
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.plot_look import PlotLookControls
    from lspr_imaging_app.panels.sensorgram.view_settings import (
        LEGEND_HIDE,
        LINE_SPLINE,
        UNCERTAINTY_BAND,
        UNCERTAINTY_SD,
        SensorgramViewSettings,
        UncertaintyLook,
    )
    from lspr_imaging_app.panels.sensorgram.window_look_icons import accumulated_picture, band_picture, bars_picture, cycle_picture, error_bars_icon
    from lspr_imaging_app.panels.ui_state import UiStateStore
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
RI = (1.33300, 1.34531, 1.35762, 1.33500)
NAMES = ("H2O", "10perEG", "20perEG", "Protein")
N, SD = 25, 0.5  # 25 cubes with an SD of 0.5: the SEM is 0.1


def _numbers() -> RowNumbers:
    return RowNumbers(refs=REFS, n=np.full(4, N), mean=np.array([100.0, 110.0, 125.0, 131.0]), sd=np.full(4, SD))


def _lbl_window() -> LblPlotWindow:
    window = LblPlotWindow()
    window.set_steps([LblStep(REFS[i], i + 1, NAMES[i]) for i in range(4)])
    window.set_lines([BulkLine("Group A (n=12)", "#38bdf8", _numbers(), short_label="Group A")], y_label="Δλ (nm)")
    window.show()
    return window


def _bulk_window() -> BulkPlotWindow:
    window = BulkPlotWindow()
    window.set_steps([PlotStep(REFS[i], i + 1, NAMES[i], RI[i], math.nan, i < 3) for i in range(4)], 589.3)
    window.set_lines([BulkLine("Group A (n=12)", "#4e79a7", _numbers(), short_label="Group A")], y_label="Peak position (nm)")
    window.show()
    return window


def _items(window, kind):
    return [i for i in window._plot.getPlotItem().items if isinstance(i, kind)]


def _bar_heights(window) -> list[float]:
    return sorted({round(float(v), 6) for bar in _items(window, pg.ErrorBarItem) for v in np.atleast_1d(bar.opts["height"])})


def _captions(menu) -> list[str]:
    """The row captions of a `GroupMenuButton`'s panel, in order."""
    return [label.text() for label in menu._captions.values()]


class OneDefinitionSeparateValuesTest(unittest.TestCase):
    def setUp(self) -> None:
        m = app_rewrite._build_modules()
        m.session_autosave._suspended = True
        self.m = m
        self.panel = SensorgramPanel(
            m.scan_results, m.roi_toolbox, m.dataset, m.selection, m.analysis_settings, m.cube_display, trace_in_process=False, mask=m.mask, experiment_plan=m.experiment_plan,
        )
        self.view = self.panel._view_controls
        self.bulk, self.lbl = _bulk_window(), _lbl_window()

    def tearDown(self) -> None:
        for window in (self.bulk, self.lbl):
            window.close()
            window.deleteLater()
        self.panel.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()

    def test_every_plot_builds_its_menus_from_the_one_definition(self) -> None:
        for owner in (self.view.look, self.lbl.look, self.bulk.look):
            self.assertIsInstance(owner, PlotLookControls)
        sensorgram_lines, sensorgram_legend = _captions(self.view.lines_menu), _captions(self.view.legend_menu)
        for window in (self.lbl, self.bulk):
            for menu, sensorgram in ((window.look.lines_menu, sensorgram_lines), (window.look.legend_menu, sensorgram_legend)):
                self.assertTrue(set(_captions(menu)) <= set(sensorgram))  # nothing a window has that the Sensorgram's menu has not
        # what a window leaves out is only what has no meaning there (and is said): the Dot limit, the legend's place at the plan bar, the Joined row of a plot that joins nothing
        self.assertEqual(set(sensorgram_lines) - set(_captions(self.lbl.look.lines_menu)), {"Dot limit"})
        self.assertEqual(set(sensorgram_lines) - set(_captions(self.bulk.look.lines_menu)), {"Dot limit", "Joined"})
        self.assertEqual(set(sensorgram_legend) - set(_captions(self.lbl.look.legend_menu)), {"Position"})

    def test_a_change_in_one_plot_changes_no_other(self) -> None:
        self.lbl.look.point_size.setValue(12)
        self.lbl.look.legend_text.setValue(14)
        self.assertEqual(self.bulk.look.point_size.value(), 9)  # each window starts with its own default
        self.assertEqual(self.view.point_size.value(), SensorgramViewSettings().point_size)
        self.assertEqual(self.view.legend_text.value(), SensorgramViewSettings().legend_text_pt)
        self.bulk.look.point_size.setValue(6)
        self.view.point_size.setValue(8)
        self.assertEqual((self.lbl.look.point_size.value(), self.bulk.look.point_size.value(), self.view.point_size.value()), (12, 6, 8))
        self.assertEqual(self.panel._plot.view().point_size, 8)  # the Sensorgram's plot follows its own tab only

    def test_each_plot_keeps_its_own_remembered_values(self) -> None:
        store = UiStateStore({})
        self.view.restore_ui_state(store)
        self.lbl.restore_ui_state(store)
        self.bulk.restore_ui_state(store)
        self.lbl.look.point_size.setValue(12)
        self.view.point_size.setValue(8)
        self.bulk.look._mode_buttons[LEGEND_HIDE].click()
        self.assertEqual(store.get("sensorgram/view_point_size"), 8)  # the Sensorgram's keys are the ones it always had
        self.assertEqual(store.get("sensorgram/lbl_plot_look_point_size"), 12)
        self.assertIsNone(store.get("sensorgram/bulk_plot_look_point_size"))
        self.assertEqual(store.get("sensorgram/bulk_plot_look_legend_mode"), LEGEND_HIDE)
        self.assertNotEqual(store.get("sensorgram/lbl_plot_look_legend_mode"), LEGEND_HIDE)
        values = dict(store._values)
        later = _lbl_window()
        other_view = _bulk_window()
        try:
            later.restore_ui_state(UiStateStore(dict(values)))
            other_view.restore_ui_state(UiStateStore(dict(values)))
            self.assertEqual(later.look.point_size.value(), 12)
            self.assertEqual(other_view.look.point_size.value(), 9)  # the bulk window never changed its size
            self.assertEqual(other_view.look.legend_mode(), LEGEND_HIDE)
        finally:
            for window in (later, other_view):
                window.close()
                window.deleteLater()

    def test_the_sensorgram_view_keeps_what_only_it_has(self) -> None:
        self.view.dots_limit.setValue(50)
        self.view.legend_over_bar.setChecked(True)
        self.assertEqual(self.view.settings().dots_max_lines, 50)
        self.assertTrue(self.view.settings().legend_over_bar)
        self.assertEqual(self.lbl.look.settings().dots_max_lines, SensorgramViewSettings().dots_max_lines)  # a window has no limit of its own to change
        self.assertFalse(self.lbl.look.settings().legend_over_bar)

    def test_a_reset_puts_back_the_plots_own_defaults_in_one_change(self) -> None:
        self.lbl.look.point_size.setValue(12)
        self.lbl.look.show_lines.setChecked(False)
        reported = []
        self.lbl.look.settings_changed.connect(lambda: reported.append(1))
        self.lbl.look.reset_lines()
        self.assertEqual(len(reported), 1)
        self.assertEqual(self.lbl.look.point_size.value(), 9)  # the window's default, not the Sensorgram's 4
        self.assertTrue(self.lbl.look.show_lines.isChecked())


class LblDrawTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = _lbl_window()
        self.look, self.unc = self.window.look, self.window.uncertainty

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_the_bars_are_the_sem_or_the_sd_of_the_steps(self) -> None:
        w = self.window
        sem = SD / math.sqrt(N)
        self.assertEqual(_bar_heights(w), sorted({0.0, round(2 * math.hypot(sem, sem), 6)}))  # (cycle 0 has no uncertainty of its own)
        self.unc._kind_buttons[UNCERTAINTY_SD].click()
        self.assertEqual(_bar_heights(w), sorted({0.0, round(2 * math.hypot(SD, SD), 6)}))
        self.assertIn("(SD)", [tip for s in _items(w, pg.ScatterPlotItem) for tip in s.data["data"]][1])

    def test_the_band_replaces_the_bars_and_hiding_removes_both(self) -> None:
        w = self.window
        self.unc._shape_buttons[UNCERTAINTY_BAND].click()
        self.assertEqual(_items(w, pg.ErrorBarItem), [])
        self.assertEqual(len(_items(w, pg.FillBetweenItem)), 1)
        self.unc.show_toggle.setChecked(False)
        self.assertEqual(_items(w, pg.ErrorBarItem) + _items(w, pg.FillBetweenItem), [])
        self.assertEqual(len(_items(w, pg.ScatterPlotItem)), 1)  # the points stay

    def test_points_lines_and_joining(self) -> None:
        w = self.window
        self.assertEqual(len(_items(w, pg.PlotCurveItem)), 1)
        straight = len(_items(w, pg.PlotCurveItem)[0].xData)
        self.look._style_buttons[LINE_SPLINE].click()
        self.assertGreater(len(_items(w, pg.PlotCurveItem)[0].xData), straight)  # a smooth curve has more drawn points than the measured ones
        self.look.show_lines.setChecked(False)
        self.assertEqual(_items(w, pg.PlotCurveItem), [])
        self.assertEqual(len(w._legend.items), 1)  # the legend still has the colour of a hidden line
        self.look.show_lines.setChecked(True)
        self.look.show_points.setChecked(False)
        self.assertEqual(_items(w, pg.ScatterPlotItem), [])
        self.look.show_points.setChecked(True)
        self.look.line_width.setValue(3.0)
        self.assertEqual(float(_items(w, pg.PlotCurveItem)[0].opts["pen"].widthF()), 3.0)

    def test_the_legend_follows_the_options(self) -> None:
        w = self.window
        self.assertEqual(w._legend.items[0][1].text, "Group A (n=12)")
        self.look.legend_counts.setChecked(False)
        self.look.legend_spacing.setValue(5)
        self.look.legend_text.setValue(14)
        self.assertEqual(w._legend.items[0][1].text, "Group A")
        self.assertEqual(w._legend.layout.verticalSpacing(), 5.0)
        self.assertEqual(w._legend.items[0][1].opts["size"], "14pt")  # (new entries take the text size)
        self.look._mode_buttons[LEGEND_HIDE].click()
        self.assertFalse(w._legend.isVisible())

    def test_no_number_changes_with_the_look(self) -> None:
        w = self.window
        before = w.export_data().tables["lbl_points"].rows
        self.look.show_lines.setChecked(False)
        self.look.point_size.setValue(12)
        self.look._style_buttons[LINE_SPLINE].click()
        self.unc.show_toggle.setChecked(False)
        self.unc._shape_buttons[UNCERTAINTY_BAND].click()
        self.assertEqual(w.export_data().tables["lbl_points"].rows, before)

    def test_the_shift_and_lines_buttons(self) -> None:
        w = self.window
        heard = []
        w.lines_mode_changed.connect(heard.append)
        w.set_lines_mode("groups")
        self.assertEqual(heard, ["groups"])
        self.assertEqual(w.lines_mode(), "groups")
        w.set_shift_mode(SHIFT_CYCLE)
        self.assertEqual(w.shift_mode(), SHIFT_CYCLE)
        self.assertEqual(len(_items(w, pg.ScatterPlotItem)[0].data), 3)  # per cycle: no value at cycle 0


class PickMenusAndPlotAreaButtonTest(unittest.TestCase):
    """Lines and Shift are pick-up menus like the Sensorgram's Plot tab (the picked picture is the button, no words, no frame); the recalculate button is in the plot area."""

    def setUp(self) -> None:
        self.lbl, self.bulk = _lbl_window(), _bulk_window()

    def tearDown(self) -> None:
        for window in (self.lbl, self.bulk):
            window.close()
            window.deleteLater()

    def test_lines_and_shift_are_pick_up_menus_without_captions(self) -> None:
        from lspr_imaging_app.panels.spectra.pick_menu import PickMenu

        for window in (self.lbl, self.bulk):
            self.assertIsInstance(window._lines_pick, PickMenu)
            self.assertEqual([label.text() for label in window.findChildren(QtWidgets.QLabel) if label.parentWidget() is window and label.text() in ("Lines", "Shift")], [])  # (a menu row caption is not in the row)
        self.assertIsInstance(self.lbl._shift_pick, PickMenu)
        self.assertEqual(self.lbl._shift_pick.text(), "")  # the picture says it: no name on the face
        before = self.lbl._shift_pick.icon().pixmap(20, 20).toImage()
        self.lbl.set_shift_mode(SHIFT_CYCLE)
        self.assertNotEqual(self.lbl._shift_pick.icon().pixmap(20, 20).toImage(), before)  # the face shows the picked choice

    def test_the_recalculate_button_is_in_the_plot_area_not_the_row(self) -> None:
        for window in (self.lbl, self.bulk):
            button = window.calculate_button
            self.assertIs(button.parentWidget(), window._plot)
            window.set_lines([], y_label="Shift (nm)", note="2 groups not calculated yet.", need_calculation=True)
            self.assertFalse(button.isHidden())
            area = window._plot.rect()
            self.assertTrue(abs(button.geometry().center().x() - area.center().x()) <= 2)  # nothing is drawn: in the middle of the plot
            window.set_lines([BulkLine("G", "#4e79a7", _numbers())], y_label="Shift (nm)", need_calculation=True)
            self.assertGreater(button.geometry().center().x(), area.center().x())  # something is drawn: out of the way, at the top right
            window.set_lines([BulkLine("G", "#4e79a7", _numbers())], y_label="Shift (nm)", need_calculation=False)
            self.assertTrue(button.isHidden())


class BulkDrawTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = _bulk_window()
        self.look, self.unc = self.window.look, self.window.uncertainty

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_the_vertical_bars_are_the_sem_or_the_sd(self) -> None:
        w = self.window
        self.assertEqual(_bar_heights(w), [round(2 * SD / math.sqrt(N), 6)])
        self.unc._kind_buttons[UNCERTAINTY_SD].click()
        self.assertEqual(_bar_heights(w), [round(2 * SD, 6)])

    def test_the_fit_does_not_change_with_what_is_shown(self) -> None:
        w = self.window
        before = [(line.label, values.fit_sem.slope) for line, values in w._fits]
        self.unc._kind_buttons[UNCERTAINTY_SD].click()
        self.unc.show_toggle.setChecked(False)
        self.assertEqual([(line.label, values.fit_sem.slope) for line, values in w._fits], before)

    def test_band_style_and_hiding(self) -> None:
        w = self.window
        fit_band = len(_items(w, pg.FillBetweenItem))  # the fit's own band
        self.unc._shape_buttons[UNCERTAINTY_BAND].click()
        self.assertEqual(_items(w, pg.ErrorBarItem), [])  # (no RI Std here: no horizontal bars either)
        self.assertEqual(len(_items(w, pg.FillBetweenItem)), fit_band + 1)  # a band along the points
        self.unc.show_toggle.setChecked(False)
        self.assertEqual(_items(w, pg.ErrorBarItem) + _items(w, pg.FillBetweenItem), [])

    def test_lines_toggle_hides_the_fitted_line_and_keeps_its_legend_entry(self) -> None:
        w = self.window
        self.assertEqual(len(_items(w, pg.PlotCurveItem)), 1)
        self.look.show_lines.setChecked(False)
        self.assertEqual(_items(w, pg.PlotCurveItem), [])
        names = [label.text for _s, label in w._legend.items]
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("Group A (n=12): S_bulk = "))
        self.look.legend_counts.setChecked(False)
        self.assertTrue([label.text for _s, label in w._legend.items][0].startswith("Group A: S_bulk = "))


class RememberedTest(unittest.TestCase):
    def test_the_uncertainty_look_is_saved_per_window_and_comes_back(self) -> None:
        store = UiStateStore({})
        window = _lbl_window()
        window.restore_ui_state(store)
        window.uncertainty._kind_buttons[UNCERTAINTY_SD].click()
        window.uncertainty._shape_buttons[UNCERTAINTY_BAND].click()
        window.uncertainty.show_toggle.setChecked(False)
        window.close()
        window.deleteLater()
        values = dict(store._values)
        lbl, bulk = LblPlotWindow(), BulkPlotWindow()
        try:
            lbl.restore_ui_state(UiStateStore(dict(values)))
            bulk.restore_ui_state(UiStateStore(dict(values)))
            self.assertEqual(lbl.uncertainty.settings(), UncertaintyLook(show=False, kind=UNCERTAINTY_SD, style=UNCERTAINTY_BAND))
            self.assertEqual(bulk.uncertainty.settings(), UncertaintyLook())  # the other window never heard of it
        finally:
            lbl.deleteLater()
            bulk.deleteLater()

    def test_a_damaged_value_falls_back_to_the_default(self) -> None:
        window = LblPlotWindow()
        try:
            window.restore_ui_state(
                UiStateStore({"sensorgram/lbl_plot_look_uncertainty_kind": 7, "sensorgram/lbl_plot_look_uncertainty_style": "zigzag", "sensorgram/lbl_plot_look_line_style": "wavy"})
            )
            self.assertEqual(window.uncertainty.settings(), UncertaintyLook())
            self.assertEqual(window.look.line_style(), "straight")
        finally:
            window.deleteLater()

    def test_the_lines_and_shift_choices_are_remembered(self) -> None:
        store = UiStateStore({})
        window = _lbl_window()
        window.restore_ui_state(store)
        window.set_lines_mode("groups")
        window.set_shift_mode(SHIFT_CYCLE)
        window.close()
        window.deleteLater()
        other = LblPlotWindow()
        try:
            other.restore_ui_state(UiStateStore(dict(store._values)))
            self.assertEqual((other.lines_mode(), other.shift_mode()), ("groups", SHIFT_CYCLE))
        finally:
            other.deleteLater()


class LblSpreadTest(unittest.TestCase):
    def test_sd_is_combined_in_quadrature_like_the_sem(self) -> None:
        points = lbl_points(_numbers(), REFS[:3], "sd")
        self.assertAlmostEqual(points[2].accumulated_u, math.hypot(SD, SD))
        self.assertAlmostEqual(points[2].cycle_shift_u, math.hypot(SD, SD))
        self.assertAlmostEqual(points[2].sem, SD / math.sqrt(N))  # the step's own SEM and SD are always kept
        self.assertAlmostEqual(points[2].sd, SD)
        sem_points = lbl_points(_numbers(), REFS[:3])
        self.assertAlmostEqual(sem_points[2].accumulated_u, math.hypot(SD / 5, SD / 5))
        self.assertEqual(points[2].accumulated, sem_points[2].accumulated)  # the shift itself does not depend on the spread shown


class PicturesTest(unittest.TestCase):
    def test_every_new_picture_draws_something(self) -> None:
        for picture in (bars_picture(), band_picture(), accumulated_picture(), cycle_picture()):
            image = picture("#dddddd").toImage()
            self.assertTrue(any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(image.height())))
        self.assertFalse(error_bars_icon(True, "#888888", "#38bdf8").isNull())
        self.assertFalse(error_bars_icon(False, "#888888", "#38bdf8").isNull())
        self.assertNotEqual(accumulated_picture()("#dddddd").toImage(), cycle_picture()("#dddddd").toImage())  # the two shift pictures differ

    def test_the_band_picture_is_a_wide_band(self) -> None:
        def covered(picture) -> float:
            image = picture("#dddddd").toImage()
            total = image.width() * image.height()
            return sum(1 for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() > 40) / total

        self.assertGreater(covered(band_picture()), 0.28)  # about a third of the picture, not a narrow line
        self.assertGreater(covered(band_picture()), 1.5 * covered(bars_picture()))


if __name__ == "__main__":
    unittest.main()
