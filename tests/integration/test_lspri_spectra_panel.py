"""Spectra panel and the scan-results module behind it (2026-10-23): the real scan on a small real dataset, the real wiring
(`app_rewrite._wire_analysis_scan`), the real panel. Driven through signals and widget methods (`QPushButton.click()`, combo and check
boxes), never by screen coordinate. The window is not shown.

Pinned: the panel draws the selected ROIs' spectra of the current cube from `scan.h5`; the work is done on a worker thread; a hidden panel
does nothing and catches up when shown; the formula, fit, order, metric and fit window are edited through their modules; results that went
out of date are drawn dimmed with a note, and a formula change does *not* make them out of date.
Design: apps/LSPRi/eva/docs/analysis_redesign_plan_2026-10-19.md, section 19.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.panels.image.mark_colors import wavelength_color
    from lspr_imaging_app.analysis import scan_store
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.spectra import panel as spectra_module
    from lspr_imaging_app.panels.spectra.panel import SpectraPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 64, 80
WAVELENGTHS = (500.0, 550.0, 600.0)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(3)
    base = rng.uniform(900.0, 1100.0, size=(H, W)).astype(np.float32)
    for cube in (0, 1, 2):
        for wavelength in WAVELENGTHS:
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), base + cube * 40.0 + (wavelength - 500.0) * 0.5)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _pump(until, seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return bool(until())


class SpectraPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.workflow = AnalysisTab()  # the Image panel's Analysis tab: Scan button, wired like the app does
        self.ui_state = UiStateStore({})
        app_rewrite._wire_analysis_scan(self.m, self.workflow, self.ui_state)
        self.m.scan_results._timer.setInterval(30)  # the debounce is 1.5 s in the app
        self.panel = SpectraPanel(self.m.scan_results, self.m.roi_toolbox, self.m.selection, self.m.analysis_settings)
        self.panel.restore_ui_state(self.ui_state)
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))
        self.compute_threads: list[threading.Thread] = []
        self._real_compute = spectra_module.compute_spectra

        def spy(stored, request):
            self.compute_threads.append(threading.current_thread())
            if getattr(self, "slow", 0.0):
                time.sleep(self.slow)
            return self._real_compute(stored, request)

        spectra_module.compute_spectra = spy

    def tearDown(self) -> None:
        spectra_module.compute_spectra = self._real_compute
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()

    # -- helpers -------------------------------------------------------------------------------------------------------------

    def _open_and_scan(self) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        self.m.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=6.0)
        self.m.roi_toolbox.add_roi(55.0, 34.0, sample_diameter_px=6.0)
        self.finished.clear()
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertEqual(self.finished[0][1], "completed")
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.assertTrue(_pump(lambda: self.m.scan_results.stale_cubes() is not None))  # the first check has run

    def _wait_drawn(self, n_roi: int) -> None:
        self.assertTrue(_pump(lambda: self.panel._last is not None and len(self.panel._last[1]) == n_roi and not self.panel._busy),
                        "the panel never drew")

    def _items(self) -> int:
        return len(self.panel._plot._items)

    # -- states without data -----------------------------------------------------------------------------------------------------

    def test_without_a_scan_it_says_so(self) -> None:
        _pump(lambda: False, 0.4)
        self.assertIn("No scan yet", self.panel._status.text())
        self.assertEqual(self._items(), 0)

    def test_with_a_scan_but_no_selection_it_asks_for_rois(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection(set())
        _pump(lambda: False, 0.4)
        self.assertIn("Select one or more ROIs", self.panel._status.text())

    # -- drawing ------------------------------------------------------------------------------------------------------------------

    def test_selected_rois_are_drawn_from_the_file_on_a_worker_thread(self) -> None:
        self._open_and_scan()
        self.compute_threads.clear()
        self.m.selection.set_cube(1)
        self.m.selection.set_roi_selection({1, 2})
        self._wait_drawn(2)
        self.assertTrue(self.compute_threads and all(t is not threading.main_thread() for t in self.compute_threads))
        self.assertEqual(self._items(), 2 * 3)  # per ROI: points, fit curve, metric marker
        spectra = self.panel._last[1]
        self.assertEqual([s.roi_id for s in spectra], [1, 2])
        stored = self.m.scan_results.stored()
        row = stored.roi_ids.index(1)
        expected = np.log10(stored.values["mean"][1, row, :, 1] / stored.values["mean"][1, row, :, 0])
        np.testing.assert_allclose(spectra[0].values, expected, rtol=1e-9)  # the formula of the stored pair, nothing else
        self.assertIn("Cube 1: 2 ROIs", self.panel._status.text())
        self.assertNotIn("OUT OF DATE", self.panel._status.text())

    def test_following_the_cube_slider_redraws_with_that_cubes_numbers(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self.m.selection.set_cube(0)
        self._wait_drawn(1)
        first = self.panel._last[1][0].values.copy()
        self.m.selection.set_cube(2)
        self.assertTrue(_pump(lambda: self.panel._last[0].request.cube == 2 and not self.panel._busy))
        self.assertFalse(np.array_equal(first, self.panel._last[1][0].values))

    def test_the_cursor_follows_the_wavelength_slider_and_its_circle_sets_the_wavelength(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self.m.selection.set_cube(1)
        self._wait_drawn(1)
        cursor = self.panel._plot._axis_cursor
        self.m.selection.set_wavelength(550.0)
        self.assertTrue(cursor.is_shown())
        self.assertEqual(cursor.line.value(), 550.0)
        self.assertEqual(cursor.x(), 550.0)
        self.assertEqual(cursor.handle._color.name(), QColor(wavelength_color()).name())  # the Wavelength slider's colour
        calls = len(self.compute_threads)
        self.m.selection.set_wavelength(600.0)
        _pump(lambda: False, 0.4)
        self.assertEqual(cursor.line.value(), 600.0)
        self.assertEqual(len(self.compute_threads), calls)  # moving the wavelength recomputes nothing
        cursor.dragged.emit(503.0)  # dragged to somewhere between ticks: the nearest scanned wavelength
        self.assertEqual(self.m.selection.current_wavelength(), 500.0)
        self.assertEqual(cursor.line.value(), 500.0)
        class _Click:  # a plain left click on the plot area, as pyqtgraph hands it to `sigMouseClicked`
            def button(self): return Qt.MouseButton.LeftButton
            def double(self): return False
            def isAccepted(self): return False
            def scenePos(self): return scene_pos

        scene_pos = cursor.handle._view_box.mapViewToScene(QPointF(548.0, 0.0))
        self.panel._plot._on_scene_clicked(_Click())  # a click elsewhere places the cursor (at the nearest scanned wavelength)
        self.assertEqual(self.m.selection.current_wavelength(), 550.0)
        self.assertEqual(cursor.x(), 550.0)
        cursor.line.sigDragged.emit(cursor.line)  # dragging the line itself reports its value too
        self.assertEqual(self.m.selection.current_wavelength(), 550.0)
        cursor.line.setValue(512.0)  # let go away from where the owner put it: it goes back
        cursor.line.sigPositionChangeFinished.emit(cursor.line)
        self.assertEqual(cursor.line.value(), 550.0)
        self.m.selection.set_wavelength(0.0)  # the dark frame is not on the spectral axis
        self.assertFalse(cursor.is_shown())

    def test_a_slow_recompute_is_reported_to_the_status_bar_and_a_quick_one_is_not(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        progress, finished = [], []
        self.panel.task.task_progress.connect(lambda *a: progress.append(a))
        self.panel.task.task_finished.connect(lambda *a: finished.append(a))
        self.panel.task._timer.setInterval(100)
        self.m.selection.set_cube(2)  # quick: no trace in the status bar
        self.assertTrue(_pump(lambda: self.panel._last[0].request.cube == 2 and not self.panel._busy))
        self.assertEqual((progress, finished), ([], []))
        self.slow = 0.5
        self.m.selection.set_cube(1)
        self.assertTrue(_pump(lambda: bool(finished)))
        self.assertEqual(finished[0][:2], ("spectra.compute", "completed"))
        self.assertEqual(progress[0][:3], ("spectra.compute", "Spectra", -1.0))
        self.assertIn("1 ROI on cube 1", progress[0][3])

    def test_cancel_clears_the_plot_and_drops_the_late_result(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        finished = []
        self.panel.task.task_finished.connect(lambda *a: finished.append(a))
        self.panel.task._timer.setInterval(50)
        self.slow = 0.6
        self.m.selection.set_cube(2)
        self.assertTrue(_pump(lambda: self.panel.task.is_shown()))
        self.panel.cancel()
        self.assertEqual(finished[0][:2], ("spectra.compute", "cancelled"))
        self.assertEqual(self._items(), 0)
        self.assertTrue(_pump(lambda: not self.panel._busy))  # the worker ends; its result belongs to an older generation
        self.assertEqual(self._items(), 0)
        self.assertIsNone(self.panel._last)

    def test_error_bars_are_a_remembered_toggle_and_add_items(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1, 2})
        self._wait_drawn(2)
        before = self._items()
        self.panel._error_box.click()
        self.assertEqual(self._items(), before + 2)
        self.assertEqual(self.ui_state.get("spectra/error_bars"), True)

    # -- the control row is a ribbon (2026-10-24) ----------------------------------------------------------------------------------

    def test_the_control_row_is_a_ribbon_that_does_not_set_the_panels_width(self) -> None:
        self.assertEqual(
            [label.text() for label in self.panel._ribbon._captions], ["Formula", "Fit", "Metric", "Fit window", "Error bars"]
        )
        self.assertLess(self.panel.minimumSizeHint().width(), 250)  # it was ~850 px before the row could scroll

    def test_the_ribbon_rolls_up_and_the_choice_is_remembered(self) -> None:
        drawer = self.panel._ribbon_drawer
        self.panel.restore_ui_state(self.ui_state)
        self.assertTrue(drawer.is_expanded())
        drawer.set_expanded(False, animate=False)
        self.assertIs(self.ui_state.get("spectra/ribbon_open"), False)
        drawer.set_expanded(True, animate=False)
        self.ui_state.set("spectra/ribbon_open", False)
        self.panel.restore_ui_state(self.ui_state)
        self.assertFalse(drawer.is_expanded())

    # -- controls edit the modules -----------------------------------------------------------------------------------------------

    def test_formula_fit_order_and_metric_are_edited_through_their_modules(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        self.panel._formula.setCurrentIndex(self.panel._formula.findData("ratio"))
        self.assertEqual(self.m.roi_toolbox.detection_settings().formula_key, "ratio")
        self.panel._fit.setCurrentIndex(self.panel._fit.findData("none"))
        self.panel._metric.setCurrentIndex(self.panel._metric.findData("centroid"))
        metric = self.m.analysis_settings.metric_settings()
        self.assertEqual((metric.fit_method, metric.metric_key), ("none", "centroid"))
        self.assertTrue(_pump(lambda: self.panel._last[0].request.formula_key == "ratio" and self.panel._last[0].request.metric.fit_method == "none"))
        self.assertFalse(self.panel._window_box.isEnabled())  # no fit: no fit window
        self.assertFalse(self.panel._order.isEnabled())
        self.panel._fit.setCurrentIndex(self.panel._fit.findData("polynomial"))
        self.panel._order.setValue(2)
        self.assertEqual(self.m.analysis_settings.metric_settings().poly_order, 2)

    def test_the_controls_follow_a_change_made_elsewhere(self) -> None:
        from dataclasses import replace

        self.m.analysis_settings.set_metric_settings(replace(self.m.analysis_settings.metric_settings(), fit_method="gaussian", metric_key="centroid"))
        self.assertEqual((self.panel._fit.currentData(), self.panel._metric.currentData()), ("gaussian", "centroid"))

    def test_the_fit_window_is_set_dragged_and_cleared_through_the_settings(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        self.panel._window_box.click()
        metric = self.m.analysis_settings.metric_settings()
        self.assertAlmostEqual(metric.fit_wl_min, 510.0)  # the middle 80 % of 500..600
        self.assertAlmostEqual(metric.fit_wl_max, 590.0)
        self.assertTrue(_pump(lambda: self.panel._plot.fit_window() is not None))
        self.panel._plot.fit_window_changed.emit(520.0, 580.0)  # what releasing the dragged band emits
        metric = self.m.analysis_settings.metric_settings()
        self.assertEqual((metric.fit_wl_min, metric.fit_wl_max), (520.0, 580.0))
        self.panel._window_box.click()
        metric = self.m.analysis_settings.metric_settings()
        self.assertEqual((metric.fit_wl_min, metric.fit_wl_max), (None, None))
        self.assertTrue(_pump(lambda: self.panel._plot.fit_window() is None))

    # -- a hidden panel does nothing ---------------------------------------------------------------------------------------------

    def test_a_hidden_panel_computes_nothing_and_catches_up_once_when_shown(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        self.panel.set_panel_visible(False)
        self.compute_threads.clear()
        for cube in (0, 1, 2, 1):
            self.m.selection.set_cube(cube)
        _pump(lambda: False, 0.4)
        self.assertEqual(self.compute_threads, [])
        self.panel.set_panel_visible(True)
        self.assertTrue(_pump(lambda: len(self.compute_threads) >= 1 and not self.panel._busy))
        _pump(lambda: False, 0.3)
        self.assertEqual(len(self.compute_threads), 1)
        self.assertEqual(self.panel._last[0].request.cube, 1)

    # -- out of date ---------------------------------------------------------------------------------------------------------------

    def test_a_moved_roi_makes_the_drawing_dimmed_and_says_so_but_a_formula_change_does_not(self) -> None:
        self._open_and_scan()
        self.assertEqual(self.m.scan_results.stale_cubes(), frozenset())
        self.m.selection.set_cube(1)
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        self.assertNotIn("OUT OF DATE", self.panel._status.text())

        self.panel._formula.setCurrentIndex(self.panel._formula.findData("ratio"))  # never stales a scan
        _pump(lambda: False, 0.5)
        self.assertEqual(self.m.scan_results.stale_cubes(), frozenset())

        self.m.roi_toolbox.move_roi(1, 41.0, 30.0)  # may change a number: the scan is out of date
        self.assertTrue(_pump(lambda: self.m.scan_results.is_cube_current(1) is False))
        self.assertTrue(_pump(lambda: "OUT OF DATE" in self.panel._status.text()))
        self.assertGreater(len(self.panel._plot._items), 0)  # dimmed, not hidden

    def test_adding_a_roi_after_the_scan_is_explained_not_drawn(self) -> None:
        self._open_and_scan()
        new_id = self.m.roi_toolbox.add_roi(20.0, 20.0, sample_diameter_px=6.0)
        self.m.selection.set_roi_selection({new_id})
        self._wait_drawn(1)
        self.assertIn("not in the scan file", self.panel._status.text())
        self.assertEqual(self._items(), 0)
        self.assertTrue(_pump(lambda: self.m.scan_results.is_cube_current(1) is False))  # the ROI list changed: everything is out of date
        self.assertTrue(_pump(lambda: "ROIs changed" in self.m.scan_results.stale_reason()))

    def test_after_a_new_scan_the_panel_shows_the_new_numbers_again_as_current(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self.m.roi_toolbox.move_roi(1, 41.0, 30.0)
        self.assertTrue(_pump(lambda: self.m.scan_results.is_cube_current(1) is False))
        self.finished.clear()
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.is_cube_current(1) is True))
        self.assertTrue(_pump(lambda: self.panel._last is not None and "OUT OF DATE" not in self.panel._status.text()))


class ResultsModuleTests(unittest.TestCase):
    def test_a_file_that_is_not_a_scan_file_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            bad = Path(tmp) / "scan.h5"
            bad.write_bytes(b"not hdf5")
            m = app_rewrite._build_modules()
            m.scan_results.set_scan_file(bad)
            self.assertTrue(_pump(lambda: bool(m.scan_results.message())))
            self.assertIsNone(m.scan_results.stored())
            self.assertIn("cannot read", m.scan_results.message().lower())
            m.scan_results.shutdown()
            self.assertEqual(scan_store.describe_file(bad).count("cannot be read"), 1)


if __name__ == "__main__":
    unittest.main()
