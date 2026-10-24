"""Sensorgram panel (2026-10-23): the real scan on a small real dataset whose ROIs carry an absorption band that drifts to longer wavelengths from
cube to cube, the real wiring (`app_rewrite._wire_analysis_scan`), the real panel. Driven through signals and widget methods, never by screen
coordinate; the window is not shown.

Pinned: the metric of the selected ROIs is plotted against the cube and follows the drift; the work is done on a worker thread; statistics changes and
other selections reuse the fitted traces while a changed fit does not; averages combine the processed per-ROI traces; a long computation is
cancelled between ROIs by a new request and keeps what it finished; clicking sets the cube, the cursor follows the slider; out-of-date cubes are ringed;
a hidden panel does nothing.
Design: apps/LSPRi/eva/docs/analysis_redesign_plan_2026-10-19.md, section 20.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis import scan_trace
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.panels.workflow.panel import WorkflowPanel
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 64, 90
WAVELENGTHS = (480.0, 510.0, 540.0, 570.0, 600.0, 630.0)
N_CUBE = 6
CENTERS = {1: (40.0, 30.0, 515.0), 2: (60.0, 34.0, 520.0)}  # x, y, band centre at cube 0 (nm); +6 nm per cube


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    y, x = np.mgrid[0:H, 0:W]
    for cube in range(N_CUBE):
        for wavelength in WAVELENGTHS:
            frame = np.full((H, W), 1000.0, dtype=np.float64)
            for cx, cy, c0 in CENTERS.values():
                absorbance = 0.4 * np.exp(-0.5 * ((wavelength - (c0 + 6.0 * cube)) / 40.0) ** 2)
                frame[(x - cx) ** 2 + (y - cy) ** 2 <= 5.0**2] *= 10.0 ** (-absorbance)
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), frame.astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _pump(until, seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return bool(until())


class SensorgramPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.m.session_autosave._suspended = True
        self.workflow = WorkflowPanel(
            self.m.dataset, self.m.selection, self.m.reference_frame, self.m.session_coordinator,
            cube_display=self.m.cube_display, frame_roles=self.m.frame_roles,
        )
        app_rewrite._wire_analysis_scan(self.m, self.workflow, UiStateStore({}))
        self.m.scan_results._timer.setInterval(30)
        self.panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
        )
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))
        self.metric_calls: list[int] = []
        self.compute_threads: list[threading.Thread] = []
        self._real_metric_trace = scan_trace.metric_trace

        def spy(stored, roi_id, formula_key, metric):
            self.metric_calls.append(roi_id)
            self.compute_threads.append(threading.current_thread())
            if getattr(self, "slow", 0.0):
                time.sleep(self.slow)
            return self._real_metric_trace(stored, roi_id, formula_key, metric)

        scan_trace.metric_trace = spy

    def tearDown(self) -> None:
        scan_trace.metric_trace = self._real_metric_trace
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()

    # -- helpers -------------------------------------------------------------------------------------------------------------

    def _open_and_scan(self, extra_rois: int = 0) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        for k in range(extra_rois):
            self.m.roi_toolbox.add_roi(10.0 + 8.0 * k, 55.0, sample_diameter_px=4.0)
        self.finished.clear()
        self.workflow.analysis_scan_row._run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertEqual(self.finished[0][1], "completed")
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.assertTrue(_pump(lambda: self.m.scan_results.stale_cubes() is not None))

    def _wait_drawn(self, n_series: int) -> None:
        self.assertTrue(_pump(lambda: self.panel._last is not None and len(self.panel._last[1]) == n_series and self.panel._job is None),
                        "the panel never drew")

    def _series(self):
        return self.panel._last[1]

    # -- states without data -----------------------------------------------------------------------------------------------------

    def test_without_a_scan_and_without_a_selection_it_says_what_to_do(self) -> None:
        _pump(lambda: False, 0.4)
        self.assertIn("No scan yet", self.panel._status.text())
        self._open_and_scan()
        self.m.selection.set_roi_selection(set())
        _pump(lambda: False, 0.4)
        self.assertIn("Select one or more ROIs", self.panel._status.text())

    # -- drawing ------------------------------------------------------------------------------------------------------------------

    def test_the_metric_of_selected_rois_follows_the_drift_on_a_worker_thread(self) -> None:
        self._open_and_scan()
        self.compute_threads.clear()
        self.m.selection.set_roi_selection({1, 2})
        self._wait_drawn(2)
        self.assertTrue(self.compute_threads and all(t is not threading.main_thread() for t in self.compute_threads))
        s1, s2 = self._series()
        self.assertEqual((s1.label, s2.label), ("ROI 1", "ROI 2"))
        np.testing.assert_array_equal(s1.x, np.arange(N_CUBE))  # the cube number
        self.assertTrue(np.all(np.isfinite(s1.y)))
        self.assertTrue(np.all(np.diff(s1.y) > 0))  # the band moves to longer wavelengths
        self.assertGreater(s1.y[-1] - s1.y[0], 10.0)  # by roughly 5 x 6 nm; a coarse 6-point fit is allowed to be rough
        self.assertLess(s1.y[-1] - s1.y[0], 50.0)
        self.assertIn("2 ROIs", self.panel._status.text())
        self.assertNotIn("OUT OF DATE", self.panel._status.text())

    def test_clicking_the_plot_moves_the_cube_and_the_cursor_follows_the_slider(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        calls_before = len(self.metric_calls)
        self.panel._plot.x_clicked.emit(3.2)
        self.assertEqual(self.m.selection.current_cube(), 3)
        self.m.selection.set_cube(4)
        self.assertEqual(self.panel._plot._cursor.value(), 4.0)
        _pump(lambda: False, 0.4)
        self.assertEqual(len(self.metric_calls), calls_before)  # moving the cube recomputes nothing

    def test_the_time_axis_uses_the_cube_start_seconds(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        with mock.patch.object(self.m.cube_display, "effective_mode", return_value="time"), \
                mock.patch.object(self.m.dataset, "cube_start_seconds", side_effect=lambda c: 10.0 * c):
            self.panel._x_cache = None
            label, x = self.panel._x_axis()
        self.assertEqual(label, "Time (s)")
        np.testing.assert_allclose(x, 10.0 * np.arange(N_CUBE))

    # -- display modes and statistics ----------------------------------------------------------------------------------------------

    def test_average_all_combines_the_per_roi_traces_and_shows_a_band(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1, 2})
        self._wait_drawn(2)
        singles = [s.y.copy() for s in self._series()]
        self.panel._display.setCurrentIndex(self.panel._display.findData("average_all"))
        self.assertTrue(_pump(lambda: self.panel._last is not None and len(self.panel._last[1]) == 1 and self.panel._job is None))
        avg = self._series()[0]
        np.testing.assert_allclose(avg.y, np.mean(singles, axis=0))
        self.assertIsNotNone(avg.band_low)
        self.assertEqual(avg.n_members, 2)
        self.assertTrue(self.panel._center.isEnabled() and self.panel._band.isEnabled())
        self.panel._band.setCurrentIndex(self.panel._band.findData("sem"))
        self.assertEqual(self.m.analysis_settings.statistics_settings().sensorgram_band, "sem")

    def test_average_by_group_uses_the_groups_of_the_selected_rois(self) -> None:
        self._open_and_scan(extra_rois=1)
        self.m.roi_toolbox.group_rois({1, 2}, "Pair")
        self.m.selection.set_roi_selection({1, 2, 3})
        self.panel._display.setCurrentIndex(self.panel._display.findData("average_by_group"))
        self.assertTrue(_pump(lambda: self.panel._last is not None and [s.label for s in self.panel._last[1]] == ["Pair (n=2)", "Ungrouped (n=1)"]))

    def test_statistics_controls_edit_the_settings_and_reuse_the_fitted_traces(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1, 2})
        self._wait_drawn(2)
        fitted = len(self.metric_calls)
        self.assertEqual(fitted, 2)
        self.panel._smoothing.setCurrentIndex(self.panel._smoothing.findData("moving_average"))
        self.panel._window.setValue(3)
        self.panel._spikes.click()
        stats = self.m.analysis_settings.statistics_settings()
        self.assertEqual((stats.smoothing_method, stats.smoothing_window, stats.spike_rejection_enabled), ("moving_average", 3, True))
        _pump(lambda: False, 0.5)
        self.assertEqual(len(self.metric_calls), fitted)  # statistics are applied to the cached traces
        self.m.selection.set_roi_selection({2})
        self._wait_drawn(1)
        self.assertEqual(len(self.metric_calls), fitted)  # another selection of already fitted ROIs too

    def test_a_changed_fit_or_formula_computes_the_traces_again(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        before = len(self.metric_calls)
        from dataclasses import replace

        self.m.analysis_settings.set_metric_settings(replace(self.m.analysis_settings.metric_settings(), metric_key="centroid"))
        self.assertTrue(_pump(lambda: len(self.metric_calls) > before and self.panel._job is None))
        self.assertIn("Centroid", self.panel._plot._item.getAxis("left").labelText)

    def test_the_baseline_band_is_set_dragged_and_makes_the_trace_a_shift(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        self.panel._baseline.click()
        stats = self.m.analysis_settings.statistics_settings()
        self.assertTrue(stats.baseline_enabled)
        self.assertAlmostEqual(stats.baseline_window_start, 0.0)
        self.assertAlmostEqual(stats.baseline_window_end, 0.5)  # the first tenth of cubes 0..5
        self.assertTrue(_pump(lambda: self.panel._plot.baseline_window() is not None))
        self.panel._plot.baseline_window_changed.emit(0.0, 1.0)  # what releasing the dragged band emits
        self.assertEqual(self.m.analysis_settings.statistics_settings().baseline_window_end, 1.0)
        self.assertTrue(_pump(lambda: abs(float(np.mean(self._series()[0].y[:2]))) < 1e-9))
        self.assertIn("shift", self.panel._plot._item.getAxis("left").labelText)
        self.panel._baseline.click()
        self.assertFalse(self.m.analysis_settings.statistics_settings().baseline_enabled)
        self.assertTrue(_pump(lambda: self.panel._plot.baseline_window() is None))

    def test_the_control_row_is_a_ribbon_that_does_not_set_the_panels_width(self) -> None:
        self.assertEqual(
            [label.text() for label in self.panel._ribbon._captions], ["Display", "Average", "Smoothing", "Spikes", "Baseline"]
        )
        self.assertLess(self.panel.minimumSizeHint().width(), 250)  # it was ~960 px before the row could scroll

    def test_the_ribbon_rolls_up_and_the_choice_is_remembered(self) -> None:
        from lspr_imaging_app.panels.ui_state import UiStateStore

        store = UiStateStore()
        drawer = self.panel._ribbon_drawer
        self.panel.restore_ui_state(store)
        self.assertTrue(drawer.is_expanded())
        drawer.set_expanded(False, animate=False)
        self.assertIs(store.get("sensorgram/ribbon_open"), False)
        drawer.set_expanded(True, animate=False)
        store.set("sensorgram/ribbon_open", False)
        self.panel.restore_ui_state(store)
        self.assertFalse(drawer.is_expanded())

    def test_the_controls_follow_a_change_made_elsewhere(self) -> None:
        from dataclasses import replace

        self.m.analysis_settings.set_statistics_settings(replace(self.m.analysis_settings.statistics_settings(), sensorgram_display_mode="average_all", smoothing_method="savgol"))
        self.assertEqual((self.panel._display.currentData(), self.panel._smoothing.currentData()), ("average_all", "savgol"))
        self.assertTrue(self.panel._window.isEnabled())

    # -- long computations, hidden panel, out of date --------------------------------------------------------------------------------

    def test_a_new_request_cancels_a_long_one_between_rois_and_keeps_what_it_finished(self) -> None:
        self._open_and_scan(extra_rois=4)
        self.slow = 0.25
        self.metric_calls.clear()
        self.m.selection.set_roi_selection({1, 2, 3, 4, 5, 6})
        self.assertTrue(_pump(lambda: len(self.metric_calls) >= 1, 10))  # the long job is running
        self.m.selection.set_roi_selection({1})
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._last[0].request.roi_ids == (1,) and self.panel._job is None, 30))
        self.assertLess(len(set(self.metric_calls)), 6)  # the six were not all fitted: the first job stopped
        self.assertIn(1, self.panel._cache)  # what it finished is kept
        self.slow = 0.0

    def test_a_hidden_panel_does_nothing_and_catches_up_once(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        self.panel.set_panel_visible(False)
        self.metric_calls.clear()
        self.m.selection.set_roi_selection({2})
        self.m.selection.set_roi_selection({1, 2})
        _pump(lambda: False, 0.4)
        self.assertEqual(self.metric_calls, [])
        self.panel.set_panel_visible(True)
        self._wait_drawn(2)
        self.assertEqual(self.metric_calls, [2])  # ROI 1 was cached; one catch-up

    def test_a_moved_roi_rings_the_out_of_date_cubes(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        items_current = len(self.panel._plot._items)
        self.m.roi_toolbox.move_roi(2, 61.0, 34.0)
        self.assertTrue(_pump(lambda: self.m.scan_results.stale_cubes() == frozenset(range(N_CUBE))))
        self.assertTrue(_pump(lambda: "OUT OF DATE" in self.panel._status.text()))
        self.assertEqual(len(self.panel._plot._items), items_current + 1)  # a ring marker set over the (still drawn) line
        self.assertIn("6 of 6 cubes OUT OF DATE", self.panel._status.text())


if __name__ == "__main__":
    unittest.main()
