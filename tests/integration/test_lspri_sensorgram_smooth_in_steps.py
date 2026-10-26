"""The Sensorgram's "smooth inside steps" switch (2026-10-26): the toggle in the Smoothing group, the pieces the panel hands to the smoother, and the redraw when a
step boundary moves. Real modules and a real scan on the small two-step dataset of `test_lspri_plan_bar` (6 cubes, steps = cubes 0-2 and 3-5); driven through
methods and signals, never by screen coordinate."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.statistics import apply_statistics
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.sensorgram.panel import NO_STEPS_NOTE, SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.stat_icons import within_steps_icon
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_plan_bar import CENTERS, CUBE_MS, T0, _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()


class SmoothInsideStepsPanelTest(unittest.TestCase):
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
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.m.selection.set_roi_selection({1, 2})
        self._settle()
        self.panel._steps.stats_controls._show.setChecked(False)  # the step statistics table also asks for a redraw when a boundary moves; these tests are about the smoothing alone
        self._settle()
        undo_manager.clear()

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _settle(self) -> None:
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None and not self.panel._redraw_timer.isActive()), "the panel never drew")

    def _smooth(self, method: str = "savgol") -> None:
        self.panel._smoothing.setCurrentIndex(self.panel._smoothing.findData(method))
        self._settle()

    def _segments(self):
        return self.panel._last[0].request.segments

    # -- the toggle ---------------------------------------------------------------------------------------------------------------

    def test_the_toggle_sits_in_the_smoothing_group_and_is_there_only_while_a_method_is_chosen(self) -> None:
        toggle = self.panel._within_steps
        self.assertIs(toggle.parentWidget(), self.panel._window.parentWidget())  # with the window, in the Smoothing group
        self.assertTrue(toggle.isCheckable())
        self.assertFalse(toggle.icon().isNull())
        self.assertTrue(toggle.isHidden())  # no smoothing by default
        self._smooth("savgol")
        self.assertFalse(toggle.isHidden())
        self.assertTrue(toggle.isChecked())  # on by default
        self.assertTrue(toggle.isEnabled())  # the dataset has steps
        self._smooth("none")
        self.assertTrue(toggle.isHidden())

    def test_the_icon_looks_different_on_and_off(self) -> None:
        on, off = within_steps_icon(True, "#888888", "#3399ff"), within_steps_icon(False, "#888888", "#3399ff")
        self.assertFalse(on.isNull() or off.isNull())
        self.assertNotEqual(on.pixmap(32, 32).toImage(), off.pixmap(32, 32).toImage())

    def test_clicking_it_changes_the_statistics_settings_and_the_curve(self) -> None:
        self._smooth("savgol")
        stats = self.m.analysis_settings.statistics_settings
        self.assertTrue(stats().smooth_within_steps)
        inside = self.panel._last[1][0].y.copy()
        self.panel._within_steps.click()
        self.assertFalse(stats().smooth_within_steps)
        self._settle()
        whole = self.panel._last[1][0].y
        self.assertIsNone(self._segments())
        self.assertFalse(np.allclose(inside, whole))  # smoothed across the step boundary, the curve differs
        self.panel._within_steps.click()
        self.assertTrue(stats().smooth_within_steps)
        self._settle()
        np.testing.assert_allclose(self.panel._last[1][0].y, inside)

    def test_the_toggle_follows_a_settings_change_made_elsewhere(self) -> None:
        """A session restore sets the settings, not the button: the button follows."""
        from dataclasses import replace

        self._smooth("savgol")
        self.m.analysis_settings.set_statistics_settings(replace(self.m.analysis_settings.statistics_settings(), smooth_within_steps=False))
        self.assertFalse(self.panel._within_steps.isChecked())

    # -- what the smoother is given -----------------------------------------------------------------------------------------------

    def test_no_smoothing_means_no_pieces(self) -> None:
        self.assertIsNone(self._segments())

    def test_with_smoothing_each_step_is_a_piece(self) -> None:
        self._smooth("savgol")
        np.testing.assert_array_equal(self._segments(), [0, 0, 0, 1, 1, 1])  # steps = cubes 0-2 and 3-5

    def test_the_curve_drawn_is_smoothed_piece_by_piece(self) -> None:
        self._smooth("moving_average")
        request = self.panel._last[0].request
        traces = self.panel._cache
        drawn = self.panel._last[1][0]
        expected = apply_statistics(request.x, traces[drawn.member_ids[0]], request.statistics, np.array([0, 0, 0, 1, 1, 1]))[0]
        np.testing.assert_array_equal(drawn.y, expected)

    def test_a_dragged_boundary_changes_the_pieces(self) -> None:
        self._smooth("savgol")
        plan = self.m.experiment_plan
        plan.move_edge(0, "end", T0 + 4 * CUBE_MS, plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5]), together=True)
        self.assertTrue(_pump(lambda: self._segments() is not None and self._segments().tolist() == [0, 0, 0, 0, 1, 1]), "the curve did not follow the boundary")

    def test_a_note_or_colour_edit_does_not_redraw_the_curve(self) -> None:
        self._smooth("savgol")
        drawn = self.panel._last
        self.m.experiment_plan.set_label(0, "Water")
        self.m.experiment_plan.set_color(1, "#00ff00")
        self.assertFalse(self.panel._redraw_timer.isActive())  # cosmetic: nothing to recompute
        self.assertIs(self.panel._last, drawn)

    def test_a_boundary_edit_with_the_switch_off_or_no_smoothing_does_not_redraw_the_curve(self) -> None:
        plan = self.m.experiment_plan
        grid = plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5])
        plan.move_edge(0, "end", T0 + 4 * CUBE_MS, grid, together=True)  # no smoothing
        self.assertFalse(self.panel._redraw_timer.isActive())
        self._smooth("savgol")
        self.panel._within_steps.click()  # switch off
        self._settle()
        plan.move_edge(0, "end", T0 + 5 * CUBE_MS, grid, together=True)
        self.assertFalse(self.panel._redraw_timer.isActive())

    # -- a dataset without steps --------------------------------------------------------------------------------------------------

    def test_without_steps_the_toggle_is_greyed_out_and_the_whole_trace_is_smoothed(self) -> None:
        self._smooth("savgol")
        dataset = self.m.dataset._dataset
        dataset.acquisition_metadata = dataset.acquisition_metadata.model_copy(update={"plan": None})
        self.m.experiment_plan.steps_changed.emit("dataset")
        self.assertFalse(self.panel._within_steps.isEnabled())
        self.assertIn(NO_STEPS_NOTE.strip(), self.panel._within_steps.toolTip())
        self.assertTrue(self.panel._within_steps.isChecked())  # the choice itself is kept for a dataset that has steps
        self.assertIsNone(self.panel._steps.step_inputs(self.m.scan_results.stored())[0])  # no pieces to smooth on their own
        self.assertFalse(self.panel._steps.smoothing_follows_steps())


if __name__ == "__main__":
    unittest.main()
