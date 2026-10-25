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
        self.panel = SpectraPanel(self.m.scan_results, self.m.roi_toolbox, self.m.selection, self.m.analysis_settings, live=self.m.analysis_live)
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

    # -- the polynomial order menu (2026-10-25) --------------------------------------------------------------------------------------

    def _wheel(self, widget, delta: int) -> None:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QWheelEvent

        event = QWheelEvent(QPointF(5.0, 5.0), QPointF(5.0, 5.0), QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(widget, event)

    def test_the_order_is_a_pickup_list_of_ordinals_to_the_13th_and_the_wheel_steps_it(self) -> None:
        from dataclasses import replace

        from lspr_imaging_app.panels.spectra.pick_menu import ordinal_suffix

        picker, settings = self.panel._order, self.m.analysis_settings
        self.assertEqual((picker.count(), picker._choices[0].key, picker._choices[-1].key), (13, 1, 13))
        self.assertEqual([ordinal_suffix(k) for k in range(1, 14)], ["st", "nd", "rd"] + ["th"] * 10)  # 11th, 12th, 13th are not 11st, 12nd, 13rd
        self.assertEqual(picker.value(), 3)  # the default
        self.assertFalse(picker.icon().isNull())
        self._wheel(picker, 120)
        self.assertEqual(picker.value(), 4)  # wheel up: a higher order, like a spin box
        self.assertEqual(settings.metric_settings().poly_order, 4)
        self._wheel(picker, -120)
        self._wheel(picker, -240)
        self.assertEqual(picker.value(), 1)  # the lower limit holds
        self._wheel(picker, -120)
        self.assertEqual(picker.value(), 1)
        for _ in range(20):
            self._wheel(picker, 120)
        self.assertEqual((picker.value(), settings.metric_settings().poly_order), (13, 13))  # the upper limit: 13
        picker.setValue(99)
        self.assertEqual(picker.value(), 13)
        picker.setValue(0)
        self.assertEqual(picker.value(), 1)
        picker.setValue(5)
        self._wheel(picker, 60)  # a touch pad's small steps add up to one notch
        self.assertEqual(picker.value(), 5)
        self._wheel(picker, 60)
        self.assertEqual(picker.value(), 6)
        picker.setEnabled(False)
        self._wheel(picker, 120)
        self.assertEqual(picker.value(), 6)  # a disabled control ignores the wheel
        picker.setEnabled(True)
        self.m.analysis_settings.set_metric_settings(replace(settings.metric_settings(), poly_order=11))
        self.assertEqual(picker.value(), 11)  # a change made elsewhere shows

    def test_the_order_menu_is_judged_on_a_worker_thread_and_a_cube_change_only_recolours_it(self) -> None:
        from dataclasses import replace

        calls: list[threading.Thread] = []
        real = spectra_module.evaluate_order_quality

        def spy(inputs, formula_key, metric, **kwargs):
            calls.append(threading.current_thread())
            return real(inputs, formula_key, metric, **kwargs)

        spectra_module.evaluate_order_quality = spy
        try:
            self._open_and_scan()
            picker = self.panel._order
            self.m.selection.set_roi_selection({1, 2})
            self._wait_drawn(2)
            self.assertTrue(_pump(lambda: self.panel._quality is not None and not self.panel._quality_busy))
            _pump(lambda: False, 0.5)  # the scan's own notifications may ask for the check more than once while it settles
            settled = len(calls)
            self.assertGreaterEqual(settled, 1)
            self.assertTrue(all(t is not threading.main_thread() for t in calls))  # never on the GUI thread
            states = {k: picker._shown[k][0] for k in range(1, 14)}
            self.assertIn(states[1], ("good", "some"))  # three wavelengths allow one term (half the points): the first order is judged,
            self.assertTrue(all(states[k] == "na" for k in range(2, 14)))  # and the others cannot be fitted
            self.assertEqual(picker._rows[4].text().strip(), "needs more points")
            self.assertIn("Widen the fit window", picker._rows[4].toolTip())
            self.m.selection.set_cube(2)
            _pump(lambda: False, 0.4)
            self.assertEqual(len(calls), settled, "a cube change must not start a new check")
            self.m.roi_toolbox.add_roi(20.0, 20.0, sample_diameter_px=6.0)  # a ROI added later is not selected: nothing changes
            self.m.selection.set_roi_selection({2})
            self._wait_drawn(1)
            self.assertTrue(_pump(lambda: len(calls) == settled + 1 and not self.panel._quality_busy))  # a new selection: one new check
            current = self.m.analysis_settings.metric_settings()
            self.m.analysis_settings.set_metric_settings(replace(current, fit_method="gaussian"))
            _pump(lambda: False, 0.5)
            self.assertFalse(picker.isEnabled())
            self.assertTrue(all(picker._shown[k][0] == "unknown" for k in range(1, 14)))  # another fit has no order: nothing is judged
            self.assertEqual(len(calls), settled + 1)
            self.m.analysis_settings.set_metric_settings(replace(current, fit_method="polynomial"))
            self.assertTrue(_pump(lambda: picker.isEnabled()))
        finally:
            spectra_module.evaluate_order_quality = real

    def test_the_order_menu_colours_each_order_by_the_verdict_for_the_present_cube(self) -> None:
        from lspr_imaging_app.analysis import order_quality as oq
        from lspr_imaging_app.panels.spectra.order_picker import STATE_COLORS, STATE_NAMES

        g, o, u, na = oq.GOOD, oq.OVER, oq.UNDER, oq.UNAVAILABLE
        verdict = np.full((oq.MAX_ORDER, 4), na, dtype=np.int8)
        verdict[0], verdict[1], verdict[2], verdict[3] = (u, u, u, u), (g, g, g, g), (g, o, g, g), (g, g, u, g)
        quality = oq.OrderQuality(np.arange(4), np.full(verdict.shape, np.nan), verdict, np.zeros(4, dtype=int), 1)
        picker = self.panel._order
        picker.apply_quality(quality, 1)
        self.assertEqual([picker._shown[k][0] for k in (1, 2, 3, 4, 13)], ["under", "good", "over", "some", "na"])
        for order, state in ((1, "under"), (2, "good"), (3, "over"), (4, "some")):  # the dot is in the colour of its state
            image = picker._choices[order - 1].picture("#ffffff").toImage()
            dot = image.pixelColor(9, image.height() // 2).name()
            self.assertEqual(dot, STATE_COLORS[state], msg=f"order {order}")
            self.assertEqual(picker._rows[order - 1].text().strip(), STATE_NAMES[state])
        self.assertIn("over-fitted at this cube", picker._rows[2].toolTip())
        self.assertIn("under-fitted at this cube", picker._rows[0].toolTip())
        picker.apply_quality(quality, 2)  # another cube: only the colours that depend on it change, from the stored verdicts
        self.assertEqual([picker._shown[k][0] for k in (1, 2, 3, 4, 13)], ["under", "good", "some", "under", "na"])
        picker.apply_quality(None, None)
        self.assertTrue(all(picker._shown[k][0] == "unknown" for k in range(1, 14)))
        self.assertEqual(picker._rows[0].text().strip(), STATE_NAMES["unknown"])

    # -- the View menu (2026-10-25) --------------------------------------------------------------------------------------------------

    def test_the_view_group_is_one_pickup_menu_and_the_error_bars_are_a_pictogram_inside_it(self) -> None:
        captions = [label.text() for label in self.panel._ribbon._captions]
        self.assertIn("View", captions)
        self.assertNotIn("Error bars", captions)
        menu = self.panel._view_menu
        self.assertEqual(menu.popupMode(), QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.assertFalse(menu.icon().isNull())
        for control in (self.panel._show_points, self.panel._point_size, self.panel._show_lines, self.panel._line_style, self.panel._error_box):
            self.assertIs(menu.menu().actions()[0].defaultWidget().isAncestorOf(control), True)  # all of them sit in the one panel
        for toggle in (self.panel._show_points, self.panel._show_lines, self.panel._error_box):
            self.assertTrue(toggle.isCheckable())
            self.assertFalse(toggle.icon().isNull())
        self.assertEqual((self.panel._show_points.isChecked(), self.panel._point_size.value(), self.panel._show_lines.isChecked(),
                          self.panel._line_style.style(), self.panel._error_box.isChecked()), (True, 6, True, "solid", False))

    def test_the_view_menu_changes_how_the_points_are_drawn_without_computing_anything(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        panel = self.panel
        data = lambda: panel._plot._items[0]  # the measured points and the line that joins them: the first item of the ROI  # noqa: E731
        fit = lambda: panel._plot._items[1]  # noqa: E731
        self.assertEqual((data().opts["symbol"], data().opts["symbolSize"]), ("o", 6))
        self.assertEqual(data().opts["pen"].style(), Qt.PenStyle.SolidLine)
        computed = len(self.compute_threads)
        panel._point_size.setValue(11)
        self.assertEqual(data().opts["symbolSize"], 11)
        panel._line_style._buttons["dash"].click()
        self.assertEqual(panel._line_style.style(), "dash")
        self.assertEqual(data().opts["pen"].style(), Qt.PenStyle.DashLine)
        for key, style in (("dot", Qt.PenStyle.DotLine), ("dashdot", Qt.PenStyle.DashDotLine), ("solid", Qt.PenStyle.SolidLine)):
            panel._line_style._buttons[key].click()
            self.assertEqual(data().opts["pen"].style(), style, msg=key)
        panel._show_lines.click()  # lines off: the dots stay
        self.assertEqual(data().opts["pen"].style(), Qt.PenStyle.NoPen)
        self.assertEqual(data().opts["symbol"], "o")
        panel._show_points.click()  # points off as well: no data marks at all, but the fitted curve and the marker stay
        self.assertIsNone(data().opts["symbol"])
        self.assertEqual(self._items(), 3)  # points item (empty of marks), fit curve, metric marker
        self.assertIsNone(data().name())
        self.assertEqual(fit().name(), "ROI 1")  # the legend entry moves to the fitted curve, so the ROI is still named
        panel._show_lines.click()  # lines back on, points still off: the line alone
        self.assertIsNone(data().opts["symbol"])
        self.assertEqual(data().opts["pen"].style(), Qt.PenStyle.SolidLine)
        self.assertEqual(data().name(), "ROI 1")
        panel._error_box.click()
        self.assertEqual(self._items(), 4)  # the error bars are drawn whatever the points do
        self.assertEqual(len(self.compute_threads), computed, "a display choice must not start a computation")

    def test_the_view_choices_are_remembered_and_come_back(self) -> None:
        self.panel._point_size.setValue(9)
        self.panel._show_points.click()
        self.panel._line_style._buttons["dot"].click()
        self.panel._show_lines.click()
        self.panel._error_box.click()
        self.assertEqual(
            (self.ui_state.get("spectra/point_size"), self.ui_state.get("spectra/show_points"), self.ui_state.get("spectra/line_style"),
             self.ui_state.get("spectra/show_lines"), self.ui_state.get("spectra/error_bars")),
            (9, False, "dot", False, True),
        )
        saved = {key: self.ui_state.get(key) for key in ("spectra/point_size", "spectra/show_points", "spectra/line_style", "spectra/show_lines", "spectra/error_bars")}
        again = SpectraPanel(self.m.scan_results, self.m.roi_toolbox, self.m.selection, self.m.analysis_settings, live=self.m.analysis_live)
        try:
            again.restore_ui_state(UiStateStore(dict(saved)))
            self.assertEqual((again._point_size.value(), again._show_points.isChecked(), again._line_style.style(), again._show_lines.isChecked(), again._error_box.isChecked()),
                             (9, False, "dot", False, True))
            broken = SpectraPanel(self.m.scan_results, self.m.roi_toolbox, self.m.selection, self.m.analysis_settings, live=self.m.analysis_live)
            try:  # a saved value that no longer fits is ignored: the defaults stay
                broken.restore_ui_state(UiStateStore({"spectra/line_style": "wavy", "spectra/point_size": 500, "spectra/show_points": "yes"}))
                self.assertEqual((broken._line_style.style(), broken._point_size.value(), broken._show_points.isChecked()), ("solid", 6, True))
            finally:
                broken.shutdown()
                broken.deleteLater()
        finally:
            again.shutdown()
            again.deleteLater()

    # -- the control row is a ribbon (2026-10-24) ----------------------------------------------------------------------------------

    def test_the_control_row_is_a_ribbon_that_does_not_set_the_panels_width(self) -> None:
        self.assertEqual(
            [label.text() for label in self.panel._ribbon._captions], ["Live", "Formula", "Fit", "Metric", "View"]
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
        self.assertFalse(self.panel._window_mode.isEnabled())  # no fit: no fit window
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
        self._pick_window("manual")
        metric = self.m.analysis_settings.metric_settings()
        self.assertAlmostEqual(metric.fit_wl_min, 510.0)  # the middle 80 % of 500..600
        self.assertAlmostEqual(metric.fit_wl_max, 590.0)
        self.assertTrue(_pump(lambda: self.panel._plot.fit_window() is not None))
        self.panel._plot.fit_window_changed.emit(520.0, 580.0)  # what releasing the dragged band emits
        metric = self.m.analysis_settings.metric_settings()
        self.assertEqual((metric.fit_wl_min, metric.fit_wl_max), (520.0, 580.0))
        self._pick_window("off")
        metric = self.m.analysis_settings.metric_settings()
        self.assertEqual((metric.fit_wl_min, metric.fit_wl_max), (None, None))
        self.assertTrue(_pump(lambda: self.panel._plot.fit_window() is None))

    def _pick_window(self, key: str) -> None:
        self.panel._window_mode.setCurrentIndex(self.panel._window_mode.findData(key))

    def test_the_fit_window_modes_are_chosen_in_the_menu_and_follow_the_settings(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        panel, settings = self.panel, self.m.analysis_settings
        self.assertEqual(panel._window_mode.currentData(), "off")  # a new session fits every point
        self.assertFalse(panel._same_points.isEnabled())
        self._pick_window("around_max")
        metric = settings.metric_settings()
        self.assertEqual((metric.window_mode, metric.relative_window), ("around_max", True))
        self.assertEqual(metric.manual_bounds(), (None, None))
        self.assertTrue(panel._same_points.isEnabled())  # the footer applies to the per-spectrum modes
        self.assertIsNone(panel._plot.fit_window())  # a per-spectrum window is not drawn
        panel._half_width.setValue(30.0)
        panel._same_points.setChecked(True)
        self.assertTrue(settings.metric_settings().relative_window)  # editing a width never turns the window on or off
        self._pick_window("fwhm")
        panel._fwhm_factor.setValue(0.6)
        metric = settings.metric_settings()
        self.assertEqual((metric.window_mode, metric.window_half_width_nm, metric.window_fwhm_factor, metric.window_same_points),
                         ("fwhm", 30.0, 0.6, True))
        self.assertTrue(_pump(lambda: self.panel._last[0].request.metric.window_mode == "fwhm"))
        self._pick_window("manual")  # a manual range is set (the middle 80 %), the per-spectrum window is replaced
        metric = settings.metric_settings()
        self.assertIsNotNone(metric.fit_wl_min)
        self.assertFalse(metric.relative_window)
        self.assertTrue(_pump(lambda: self.panel._plot.fit_window() is not None))
        self._pick_window("off")
        metric = settings.metric_settings()
        self.assertEqual((metric.manual_bounds(), metric.relative_window), ((None, None), False))
        self.assertEqual(panel._window_mode.currentData(), "off")
        self.assertEqual(metric.window_fwhm_factor, 0.6)  # the widths are remembered while the window is off

    def test_the_metric_range_is_chosen_in_the_menu_and_only_for_the_centroid(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        panel, settings = self.panel, self.m.analysis_settings
        self.assertEqual(panel._metric_range.currentData(), "off")
        self.assertFalse(panel._metric_range.isEnabled())  # the maximum has no range
        panel._metric.setCurrentIndex(panel._metric.findData("centroid"))
        self.assertTrue(panel._metric_range.isEnabled())
        panel._metric_range.setCurrentIndex(panel._metric_range.findData("top_fraction"))
        panel._top_fraction.setValue(40.0)
        metric = settings.metric_settings()
        self.assertEqual((metric.metric_range, metric.metric_top_fraction), ("top_fraction", 0.4))
        self.assertTrue(metric.ranged_centroid)
        self.assertTrue(_pump(lambda: self.panel._last[0].request.metric.metric_top_fraction == 0.4))
        panel._metric_range.setCurrentIndex(panel._metric_range.findData("manual"))
        metric = settings.metric_settings()
        self.assertAlmostEqual(metric.metric_range_min, 510.0)  # the middle 80 % of 500..600, as a new manual fit range
        self.assertAlmostEqual(metric.metric_range_max, 590.0)
        panel._range_max.setValue(570.0)
        panel._range_min.setValue(580.0)  # the ends are put in order
        metric = settings.metric_settings()
        self.assertEqual((metric.metric_range_min, metric.metric_range_max), (570.0, 580.0))
        panel._metric.setCurrentIndex(panel._metric.findData("maximum"))
        self.assertFalse(panel._metric_range.isEnabled())
        self.assertEqual(settings.metric_settings().metric_range, "manual")  # remembered while it does not apply
        panel._fit.setCurrentIndex(panel._fit.findData("none"))
        panel._metric.setCurrentIndex(panel._metric.findData("centroid"))
        self.assertFalse(panel._metric_range.isEnabled())  # no fit, no curve

    def test_the_template_shift_is_chosen_in_the_fit_menu_with_its_reference_cubes(self) -> None:
        """The fourth fit choice (2026-10-25, `analysis/template_shift.py`): the controls that mean nothing for it are off, the reference cubes are two fields in its row, typed in either
        order, and a scan with fewer than four wavelengths (this one has three) is explained instead of drawn."""
        from dataclasses import replace

        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        panel, settings = self.panel, self.m.analysis_settings
        self.assertGreaterEqual(panel._fit.findData("template"), 0)
        settings.set_metric_settings(replace(settings.metric_settings(), fit_wl_min=520.0, fit_wl_max=580.0))  # a manual window that the template shift must not draw
        panel._fit.setCurrentIndex(panel._fit.findData("template"))
        metric = settings.metric_settings()
        self.assertEqual((metric.fit_method, metric.uses_template), ("template", True))
        self.assertEqual((panel._ref_first.value(), panel._ref_last.value()), (0, 9))  # the first ten cubes
        for control in (panel._order, panel._window_mode, panel._metric, panel._metric_range):
            self.assertFalse(control.isEnabled())
        panel._ref_first.setValue(2)
        panel._ref_last.setValue(1)  # typed the wrong way round: read as 1 to 2
        metric = settings.metric_settings()
        self.assertEqual(metric.reference_cube_range(), (1, 2))
        self.assertEqual((metric.template_first_cube, metric.template_last_cube), (1, 2))
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._last[0].request.metric.reference_cube_range() == (1, 2) and not self.panel._busy))
        spectrum = self.panel._last[1][0]
        self.assertIn("at least 4 scanned wavelengths", spectrum.note)  # three wavelengths cannot carry a shift against a reference
        self.assertIsNone(spectrum.fit_values)
        self.assertGreater(spectrum.values.size, 0)
        self.assertIsNone(panel._plot.fit_window())  # no window band for a method that uses every point
        panel._fit.setCurrentIndex(panel._fit.findData("polynomial"))  # the others come back
        for control in (panel._order, panel._window_mode, panel._metric):
            self.assertTrue(control.isEnabled())
        self.assertEqual(settings.metric_settings().reference_cube_range(), (1, 2))  # remembered while it does not apply

    def test_the_reference_cube_fields_follow_a_change_made_elsewhere(self) -> None:
        from dataclasses import replace

        current = self.m.analysis_settings.metric_settings()
        self.m.analysis_settings.set_metric_settings(replace(current, fit_method="template", template_first_cube=4, template_last_cube=12))
        self.assertEqual(self.panel._fit.currentData(), "template")
        self.assertEqual((self.panel._ref_first.value(), self.panel._ref_last.value()), (4, 12))

    def test_the_window_controls_follow_a_change_made_elsewhere(self) -> None:
        from dataclasses import replace

        current = self.m.analysis_settings.metric_settings()
        self.m.analysis_settings.set_metric_settings(
            replace(current, window_mode="around_max", window_on=True, window_half_width_nm=70.0, window_same_points=True)
        )
        self.assertEqual(self.panel._window_mode.currentData(), "around_max")
        self.assertEqual(self.panel._half_width.value(), 70.0)
        self.assertTrue(self.panel._same_points.isChecked())

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

    # -- live / paused ---------------------------------------------------------------------------------------------------------------

    def test_a_paused_panel_computes_nothing_and_catches_up_once_when_live_again(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        self.m.analysis_live.set_live(False)
        self.compute_threads.clear()
        for cube in (0, 1, 2, 1):
            self.m.selection.set_cube(cube)
        self.panel._formula.setCurrentIndex(self.panel._formula.findData("ratio"))
        _pump(lambda: False, 0.4)
        self.assertEqual(self.compute_threads, [])
        self.m.analysis_live.set_live(True)
        self.assertTrue(_pump(lambda: len(self.compute_threads) >= 1 and not self.panel._busy))
        _pump(lambda: False, 0.3)
        self.assertEqual(len(self.compute_threads), 1)
        self.assertEqual(self.panel._last[0].request.cube, 1)
        self.assertEqual(self.panel._last[0].request.formula_key, "ratio")

    def test_pausing_holds_back_the_out_of_date_check_and_going_live_runs_it_once(self) -> None:
        self._open_and_scan()
        self.assertEqual(self.m.scan_results.stale_cubes(), frozenset())
        self.m.analysis_live.set_live(False)
        self.m.roi_toolbox.move_roi(1, 41.0, 30.0)
        _pump(lambda: False, 0.5)
        self.assertEqual(self.m.scan_results.stale_cubes(), frozenset())  # no check ran
        self.assertFalse(self.m.scan_results.is_checking())
        self.m.analysis_live.set_live(True)
        self.assertTrue(_pump(lambda: self.m.scan_results.is_cube_current(0) is False))

    def test_the_toggles_on_the_analysis_tab_and_in_the_ribbon_share_one_flag(self) -> None:
        self.assertTrue(self.workflow.live_toggle.is_live() and self.panel._live_toggle.is_live())
        self.workflow.live_toggle.click()
        self.assertFalse(self.m.analysis_live.is_live())
        self.assertFalse(self.panel._live_toggle.is_live())
        self.panel._live_toggle.click()
        self.assertTrue(self.m.analysis_live.is_live())
        self.assertTrue(self.workflow.live_toggle.is_live())

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
