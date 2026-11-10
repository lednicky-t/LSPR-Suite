"""The Sensorgram's drift baseline in the panel (2026-11-09, anchor points only): the Baseline group's controls, the dashed curve while Subtract is off,
adding and removing points in the edit mode, the Anchors menu, per-ROI points following a ROI renumber, the anchor marks and the plan bar lock.

Real modules and a real scan on the small two-step dataset of `test_lspri_plan_bar` (6 cubes; steps = cubes 0-2 "H2O_--_ch1F" and 3-5 "10perEG_-_ch1F").
Driven through methods and signals, never by screen coordinate."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PyQt6 import QtWidgets
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QLabel, QMenu, QSpinBox, QToolButton

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.baseline_drift import METHOD_ANCHORS, METHOD_WINDOW
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.undo import undo_manager
    from tests.integration.test_lspri_plan_bar import CENTERS, _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise



class DriftBaselinePanelTest(unittest.TestCase):
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
        self.panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask, experiment_plan=self.m.experiment_plan,
        )
        self.panel.resize(1300, 480)
        self.panel.show()
        finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: finished.append(args))
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.m.selection.set_roi_selection({1, 2})
        self._settle()
        undo_manager.clear()
        self.drift = self.panel._drift

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _settle(self) -> None:
        _APP.processEvents()
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None and not self.panel._redraw_timer.isActive()), "the panel never drew")
        _APP.processEvents()

    def _set(self, **changes) -> None:
        self.m.analysis_settings.set_statistics_settings(replace(self.m.analysis_settings.statistics_settings(), **changes))
        self._settle()

    def _lines(self):
        return self.panel._last[1]

    def _menu_texts(self) -> list[str]:
        menu = QMenu()
        self.drift.fill_anchor_menu(menu)
        return [a.text() for a in menu.actions() if not a.isSeparator()] + [label.text() for label in menu.findChildren(QLabel)]

    def test_the_controls_sit_in_the_baseline_group_and_follow_the_method(self) -> None:
        self.assertEqual(self.drift.method.currentData(), METHOD_WINDOW)  # a new session keeps the method it always had
        for widget in (self.drift.shape, self.drift.anchors, self.drift.edit):
            self.assertTrue(widget.isHidden())
        self.assertFalse(self.panel._band_menu.isHidden())
        self.drift.method.setCurrentIndex(self.drift.method.findData(METHOD_ANCHORS))
        self._settle()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_method, METHOD_ANCHORS)
        for widget in (self.drift.shape, self.drift.anchors, self.drift.edit):
            self.assertFalse(widget.isHidden())
        self.assertTrue(self.panel._band_menu.isHidden())
        self.assertFalse(hasattr(self.drift, "settle"))  # no step anchors any more: no settled-tail box
        self.assertEqual(self.drift.anchors.text(), "0")  # beside the anchor icon
        self.assertFalse(self.drift.anchors.icon().isNull())

    def test_the_window_method_has_a_pencil_that_shows_the_baseline_band(self) -> None:
        """2026-11-09: the window baseline got an edit pencil like the anchors; it is the band's show / hide, so the band menu has no toggle of its own."""
        pencil = self.panel._band_edit
        self.assertFalse(pencil.isHidden())
        self.assertTrue(pencil.isChecked())
        self.assertTrue(self.panel._band_controls._toggle_button.isHidden())
        pencil.click()
        self.assertFalse(self.panel._band_visible)
        self.panel._band_controls.set_visible(True)  # any other route keeps the pencil in step
        self.assertTrue(pencil.isChecked())
        self._set(baseline_method=METHOD_ANCHORS)
        self.assertTrue(pencil.isHidden())
        self.assertFalse(self.drift.edit.isHidden())  # the anchors' own pencil instead

    def test_no_point_or_too_few_leave_the_lines_uncorrected_and_say_why(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS, baseline_shape="linear", baseline_enabled=True)
        for line in self._lines():
            self.assertIn("no anchors", line.baseline_note)
        self.drift.add_point_at(self._x_of(1))
        self._settle()
        for line in self._lines():
            self.assertIn("linear needs 2", line.baseline_note)
        self.assertTrue(any("linear needs 2" in t for t in self._menu_texts()))
        self.assertEqual(self.drift.anchors.text(), "1")

    def test_constant_subtracts_and_the_dashed_curve_shows_while_subtract_is_off(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS, baseline_shape="constant", baseline_point_halfwidth=1)
        self.drift.add_point_at(self._x_of(1))  # with +- 1 cube: the whole water step (cubes 0-2)
        self._settle()
        parts = [p.layer for p in self.panel._plot._svg_parts]
        self.assertEqual(parts.count("Baseline curves"), 2)  # one per line, while the traces are absolute
        self._set(baseline_enabled=True)
        self.assertNotIn("Baseline curves", [p.layer for p in self.panel._plot._svg_parts])
        for line in self._lines():
            self.assertAlmostEqual(float(np.nanmean(line.y[:3])), 0.0, places=9)  # the water step is the zero
            self.assertEqual(line.baseline_note, "")

    # -- anchor points (left click adds, right click removes, in the edit mode) ------------------------------------------------

    def _x_of(self, cube_position: int) -> float:
        _label, x = self.panel._x_axis()
        return float(x[cube_position])

    def test_the_edit_toggle_sets_the_plot_mode_and_only_with_the_anchors_method(self) -> None:
        self.assertTrue(self.drift.edit.isHidden())
        self._set(baseline_method=METHOD_ANCHORS)
        self.assertFalse(self.drift.edit.isHidden())
        self.drift.edit.setChecked(True)
        self.assertTrue(self.panel._plot.anchor_mode())
        self._set(baseline_method=METHOD_WINDOW)  # nothing to edit with the window method: the mode ends
        self.assertFalse(self.drift.edit.isChecked())
        self.assertFalse(self.panel._plot.anchor_mode())

    def test_a_click_adds_a_shared_point_at_the_nearest_cube_and_the_cursor_stays(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS, baseline_shape="constant", baseline_point_halfwidth=0)
        self.drift.edit.setChecked(True)
        cube_before = self.m.selection.current_cube() if hasattr(self.m.selection, "current_cube") else None
        stored = self.m.scan_results.stored()
        self.panel._plot.anchor_point_added.emit(self._x_of(1) + 0.2)
        self._settle()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ((0, int(stored.cube_indices[1])),))
        self.assertEqual(len(self._dots()), 2)  # a hollow square on each line, no other mark
        if cube_before is not None:
            self.assertEqual(self.m.selection.current_cube(), cube_before)
        self._set(baseline_enabled=True)
        for line in self._lines():  # one point, constant: each line is zero at that cube
            self.assertAlmostEqual(float(line.y[1]), 0.0, places=9)

    def test_a_click_on_a_trace_dot_still_adds_a_point(self) -> None:
        """The maintainer's report: most clicks placed nothing. A click on a line's per-cube dot is accepted by the dot item (pyqtgraph), which
        made the plot ignore it; the dots do nothing with a click, so it must count."""
        self._set(baseline_method=METHOD_ANCHORS, baseline_point_halfwidth=0)
        self.drift.edit.setChecked(True)
        plot = self.panel._plot
        view_box = plot._item.getViewBox()
        dot_item = next(p.item for p in plot._svg_parts if isinstance(p.item, pg.ScatterPlotItem))
        scene = view_box.mapViewToScene(QPointF(self._x_of(2), view_box.viewRect().center().y()))

        class _Click:  # what pyqtgraph hands `sigMouseClicked` after the dot item took the click
            currentItem = dot_item

            def button(self):
                return Qt.MouseButton.LeftButton

            def double(self):
                return False

            def isAccepted(self):
                return True

            def scenePos(self):
                return scene

        plot._on_scene_clicked(_Click())
        stored = self.m.scan_results.stored()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ((0, int(stored.cube_indices[2])),))

    def test_every_point_is_drawn_and_the_cursor_counts_the_points_still_needed(self) -> None:
        """Maintainer, 2026-11-09: one point of a linear baseline was not drawn (it looked as if not added); show it, and show 2 -> 1 -> nothing."""
        self._set(baseline_method=METHOD_ANCHORS, baseline_shape="linear", baseline_point_halfwidth=0)
        self.drift.edit.setChecked(True)
        plot = self.panel._plot
        self.assertEqual(plot.anchors_needed(), 2)
        self.drift.add_point_at(self._x_of(0))
        self._settle()
        self.assertEqual(plot.anchors_needed(), 1)
        dots = self._dots()
        self.assertEqual(len(dots), 2)  # one square per line although the line cannot be fitted yet
        line = self._lines()[0]
        np.testing.assert_allclose(dots[0].getData()[1], [line.y[0]])  # on the (uncorrected) trace
        self.drift.add_point_at(self._x_of(5))
        self._settle()
        self.assertEqual(plot.anchors_needed(), 0)
        self._set(baseline_shape="constant")  # another shape, another count (the polynomials all need 2 since the fit goes through every cube)
        self.assertEqual(plot.anchors_needed(), 0)
        self.drift.remove_point_at(self._x_of(5))
        self.drift.remove_point_at(self._x_of(0))
        self._settle()
        self.assertEqual(plot.anchors_needed(), 1)

    def test_the_edit_mode_makes_a_slow_click_a_click_and_restores_the_drag_limits(self) -> None:
        scene = self.panel._plot._item.scene()
        before = (scene.minDragTime, scene._moveDistance)
        self._set(baseline_method=METHOD_ANCHORS)
        self.drift.edit.setChecked(True)
        self.assertGreater(scene.minDragTime, 10.0)
        self.assertGreater(scene._moveDistance, before[1])
        self.drift.edit.setChecked(False)
        self.assertEqual((scene.minDragTime, scene._moveDistance), before)

    def test_the_lines_shown_scope_gives_the_point_to_those_rois_only(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS)
        self.m.selection.set_roi_selection({2})
        self._settle()
        self.drift.set_point_scope("shown")
        cube = self.drift.add_point_at(self._x_of(4))
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ((2, cube),))
        self.m.roi_toolbox.delete_rois([1])  # ROI 2 becomes 1, its own point follows
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ((1, cube),))

    def test_a_right_click_on_a_square_removes_it_and_elsewhere_asks_for_the_menu(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS, baseline_point_halfwidth=0)
        first = self.drift.add_point_at(self._x_of(0))
        self.drift.add_point_at(self._x_of(5))
        self._settle()
        self.drift.edit.setChecked(True)
        plot = self.panel._plot
        view_box = plot._item.getViewBox()
        menus: list[tuple[float, object]] = []
        plot.anchor_menu_requested.disconnect()  # the panel would pop a real menu up
        plot.anchor_menu_requested.connect(lambda x, pos: menus.append((x, pos)))
        top = view_box.viewRect().top() if view_box.viewRect().top() > view_box.viewRect().bottom() else view_box.viewRect().bottom()
        far = view_box.mapViewToScene(QPointF(self._x_of(5), top))  # above cube 5 but far from its squares
        self.assertTrue(plot._on_anchor_right_click(far))  # taken by the edit mode: the view's own menu does not open
        self.assertEqual(len(menus), 1)
        self.assertEqual(len(self.m.analysis_settings.statistics_settings().baseline_anchor_points), 2)  # nothing removed
        square = view_box.mapViewToScene(QPointF(self._x_of(5), float(self._lines()[0].y[5])))
        self.assertTrue(plot._on_anchor_right_click(square + QPointF(4.0, -3.0)))  # 5 px off the square: close enough
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ((0, first),))
        self.assertEqual(len(menus), 1)
        self.drift.edit.setChecked(False)
        self.assertFalse(plot._on_anchor_right_click(square))

    def test_the_anchor_context_menu_adds_clears_and_stops(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS)
        self.drift.edit.setChecked(True)

        def act(text: str) -> None:
            menu = QMenu()
            self.drift.fill_context_menu(menu, self._x_of(3))
            next(a for a in menu.actions() if a.text() == text).trigger()

        act("Add anchor point here")
        stored = self.m.scan_results.stored()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ((0, int(stored.cube_indices[3])),))
        act("New points apply to the lines shown only")
        self.assertEqual(self.drift.point_scope, "shown")
        act("Clear all anchor points")
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ())
        act("Stop editing anchor points")
        self.assertFalse(self.drift.edit.isChecked())

    def test_the_menu_is_one_row_of_icons_for_scope_width_and_clear(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS)
        self.drift.add_point_at(self._x_of(2))
        menu = QMenu()
        self.drift.fill_anchor_menu(menu)
        self.assertEqual(len(menu.actions()), 1)  # one compact row, no text entries
        scope_all = menu.findChild(QToolButton, "anchor_scope_all")
        scope_shown = menu.findChild(QToolButton, "anchor_scope_shown")
        self.assertTrue(scope_all.isChecked() and not scope_shown.isChecked())
        self.assertFalse(scope_all.icon().isNull())
        scope_shown.click()
        self.assertEqual(self.drift.point_scope, "shown")
        self.assertTrue(scope_shown.isChecked() and not scope_all.isChecked())
        menu.findChild(QSpinBox, "anchor_halfwidth").setValue(4)
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_point_halfwidth, 4)
        menu.findChild(QToolButton, "anchor_clear").click()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_points, ())


    # -- anchor marks and the plan bar during the edit mode ---------------------------------------------------------------------

    def _dots(self):
        return [p.item for p in self.panel._plot._svg_parts if p.layer == "Baseline anchors"]

    def test_anchors_are_hollow_squares_at_the_trace_level_then_at_zero_after_subtracting(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS, baseline_shape="linear", baseline_point_halfwidth=0)
        self.drift.add_point_at(self._x_of(0))
        self.drift.add_point_at(self._x_of(5))
        self._settle()
        dots = self._dots()
        self.assertEqual(len(dots), 2)  # one item per line, two anchors each
        line = self._lines()[0]
        xs, ys = dots[0].getData()
        np.testing.assert_allclose(ys, [line.y[0], line.y[5]])  # absolute: on the trace (one cube each)
        self._set(baseline_enabled=True)
        xs, ys = self._dots()[0].getData()
        np.testing.assert_allclose(ys, 0.0, atol=1e-9)  # two anchors, a line through both: nothing left

    def test_the_plan_bar_takes_no_click_while_anchors_are_edited(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS)
        bar = self.panel._plot.plan_bar()
        bar.select(0)
        self.drift.edit.setChecked(True)
        self.assertTrue(bar.is_locked())
        self.assertEqual(bar.selected_indices(), ())  # the selection goes, so nothing acts on a step
        rect = bar.device_rect_of(0)
        self.assertIsNotNone(rect)
        scene = self.panel._plot._widget.mapToScene(rect.center().toPoint())
        self.assertFalse(bar.claims_scene_pos(scene))  # a right-click on the bar is the anchor mode's, not the bar's menu
        self.drift.edit.setChecked(False)
        self.assertTrue(bar.claims_scene_pos(scene))  # and the bar's own again once the mode is off
        self.assertFalse(bar.is_locked())

    def test_opening_the_plan_tab_ends_the_anchor_edit(self) -> None:
        self._set(baseline_method=METHOD_ANCHORS)
        self.drift.edit.setChecked(True)
        self.panel._on_ribbon_tab("Experimental plan")
        self.assertFalse(self.drift.edit.isChecked())
        self.assertFalse(self.panel._plot.plan_bar().is_locked())

    # -- the Range method's anchor steps (right-click a plan step with the Range pencil on) ------------------------------------

    def _step_menu(self, index: int) -> list:
        menu = self.panel._steps.actions.build_menu(index)
        return [a for a in menu.actions() if not a.isSeparator()]

    def test_a_right_click_on_a_step_marks_it_as_a_range_anchor(self) -> None:
        self._set(baseline_method=METHOD_WINDOW, baseline_shape="constant", baseline_step_trim=0)
        self.assertTrue(self.panel._band_edit.isChecked())  # the Range pencil (on by default: the band shows)
        self.assertTrue(self.drift.trim.isHidden())  # no step marked yet: the window's mean, no offset box
        actions = self._step_menu(0)
        make = next(a for a in actions if a.text() == "Make this step a baseline anchor")
        self.assertTrue(any(a.text() == "Edit text..." for a in actions))  # the step's own items stay below the baseline's
        make.trigger()
        self._settle()
        steps = self.m.experiment_plan.steps()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_steps, (tuple(steps[0].ref),))
        self.assertEqual(self.panel._plot.plan_bar().baseline_anchors(), frozenset({0}))  # the small anchor in its corner
        self.assertFalse(self.drift.trim.isHidden())
        self.assertFalse(self.drift.shape.isHidden())
        self._set(baseline_enabled=True)
        for line in self._lines():  # the marked water step (cubes 0-2) is the zero now
            self.assertAlmostEqual(float(np.nanmean(line.y[:3])), 0.0, places=9)
            self.assertEqual(line.baseline_note, "")
        remove = next(a for a in self._step_menu(0) if a.text() == "Remove this step from the baseline anchors")
        remove.trigger()
        self._settle()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_anchor_steps, ())
        self.assertEqual(self.panel._plot.plan_bar().baseline_anchors(), frozenset())
        self.assertTrue(self.drift.trim.isHidden())

    def test_too_few_marked_steps_for_the_shape_are_said_at_the_pointer_in_the_menu_and_in_the_shape_tip(self) -> None:
        """2026-11-10: with the default Linear shape the first marked step corrects nothing (two are needed, and no simpler shape is swapped in);
        the status row is hidden, so the reason has to show where the user is looking."""
        said: list[str] = []
        self.drift.say = said.append
        self._set(baseline_method=METHOD_WINDOW, baseline_shape="linear", baseline_step_trim=0, baseline_enabled=True)
        self.assertEqual(said, [])  # nothing marked: the plain range mean, nothing to say
        self.assertEqual(self.drift.steps_note(), "")
        next(a for a in self._step_menu(0) if a.text() == "Make this step a baseline anchor").trigger()
        self._settle()
        self.assertEqual(said, [
            "Linear needs 2 anchor steps, 1 marked: mark 1 more, or choose the Constant shape. Until then the lines are not corrected."
        ])
        first = self._step_menu(1)[1]  # the greyed line under the title of any step's menu
        self.assertEqual(first.text(), "Linear needs 2 anchor steps: 1 marked")
        self.assertFalse(first.isEnabled())
        self.assertFalse(first.icon().isNull())
        self.assertTrue(all("linear needs 2" in line.baseline_note for line in self._lines()))  # no silent fallback to a constant
        self.assertIn("Not corrected now: linear needs 2", self.drift.shape.toolTip())
        next(a for a in self._step_menu(1) if a.text() == "Make this step a baseline anchor").trigger()
        self._settle()
        self.assertEqual(len(said), 1)  # enough steps now: no second note
        self.assertEqual(self._step_menu(0)[1].text(), "Make this step a baseline anchor")
        self.assertTrue(all(line.baseline_note == "" for line in self._lines()))
        self.assertNotIn("Not corrected now", self.drift.shape.toolTip())

    def test_choosing_a_shape_that_needs_more_steps_than_are_marked_says_so(self) -> None:
        said: list[str] = []
        self.drift.say = said.append
        steps = self.m.experiment_plan.steps()
        self._set(baseline_method=METHOD_WINDOW, baseline_anchor_steps=(tuple(steps[0].ref),), baseline_shape="constant", baseline_step_trim=0)
        self.assertEqual(self.drift.steps_note(), "")  # Constant needs one step
        self.drift.shape.setCurrentIndex(self.drift.shape.findData("quadratic"))
        self._settle()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_shape, "quadratic")  # the choice is kept, never swapped
        self.assertEqual(len(said), 1)
        self.assertIn("Quadratic needs 2 anchor steps, 1 marked: mark 1 more, or choose the Constant shape.", said[0])

    def test_a_marked_step_emptied_by_the_offset_is_explained_in_the_step_menu(self) -> None:
        steps = self.m.experiment_plan.steps()
        self._set(baseline_method=METHOD_WINDOW, baseline_anchor_steps=(tuple(steps[0].ref),), baseline_shape="constant", baseline_step_trim=2)
        first = self._step_menu(0)[1]  # under the title; enough steps for the shape, yet nothing is left of the step: what the fit said
        self.assertTrue(first.text().startswith("Not corrected: "), first.text())
        self.assertIn("no anchors", first.text())
        self.assertFalse(first.isEnabled())

    def test_the_border_offset_is_a_ribbon_control(self) -> None:
        steps = self.m.experiment_plan.steps()
        self._set(baseline_method=METHOD_WINDOW, baseline_anchor_steps=(tuple(steps[0].ref),), baseline_shape="constant")
        self.drift.trim.setValue(1)
        self._settle()
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_step_trim, 1)
        self.drift.trim.setValue(2)  # 3 cubes - 2 x 2: nothing left in the step
        self._settle()
        self.assertTrue(all("no anchors" in line.baseline_note for line in self._lines()))

    def test_the_baseline_items_show_only_with_the_range_pencil_on_and_off_the_plan_tab(self) -> None:
        def has_baseline_items() -> bool:
            return any(a.text() == "Make this step a baseline anchor" for a in self._step_menu(0))

        self._set(baseline_method=METHOD_WINDOW)
        self.assertTrue(has_baseline_items())
        self.panel._band_edit.setChecked(False)
        self.assertFalse(has_baseline_items())
        self.panel._band_edit.setChecked(True)
        self.assertTrue(self.panel._ribbon_tabs.set_category("Experimental plan"))  # where steps are edited: the plan's items only
        self.assertFalse(has_baseline_items())
        self.assertTrue(self.panel._ribbon_tabs.set_category("Plot"))
        self._set(baseline_method=METHOD_ANCHORS)  # not the Range method
        self.assertFalse(has_baseline_items())


if __name__ == "__main__":
    unittest.main()
