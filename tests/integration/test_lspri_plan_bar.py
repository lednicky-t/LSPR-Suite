"""The Sensorgram's plan bar (2026-10-26): the bar item on the plot, the ribbon group that controls it, and the panel wiring that hands the experiment steps to the plot.

Three layers, driven through methods and signals, never by screen coordinate (the item's layout is asked for directly; one test renders the plot offscreen and looks at a pixel):
- `PlanBarItem` / `SensorgramPlot.set_plan`: geometry, hit area, tooltip, the room made for the bar, one rendered pixel;
- `PlanBarControls`: the settings the group makes, the per-mode text position, the remembered state, the greyed-out state;
- `SensorgramPanel` with the real modules and a real scan on a small dataset that carries timings and two steps.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QTransform

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming, ImagingPlan, ImagingPlanStep
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.plan_group import PlanBarControls
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan import bar_model as bm
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE, MODE_TIME
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()


def _pump(until, seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return bool(until())


def _step(index, x0, x1, text="H2O", color="#ff0000", levels=None) -> bm.PlanBarStep:
    return bm.PlanBarStep(index=index, x_start=x0, x_end=x1, text=text, color=color, tooltip=f"tip {index}", levels=levels)


def _series(n=11) -> Series:
    x = np.arange(float(n))
    return Series(key="a", label="ROI 1", color="#ffffff", x=x, y=600.0 + 2.0 * x)


# -- the item on the plot --------------------------------------------------------------------------------------------------------


class PlanBarItemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = SensorgramPlot()
        self.plot.resize(640, 320)
        self.plot.show()
        _APP.processEvents()
        self.plot.show_series([_series()])
        _APP.processEvents()
        self.bar = self.plot.plan_bar()

    def tearDown(self) -> None:
        self.plot.close()
        self.plot.deleteLater()

    def _show(self, steps, **changes):
        self.plot.set_plan(steps, bm.PlanBarSettings(**changes))
        _APP.processEvents()

    def test_nothing_is_shown_without_steps_or_while_switched_off(self) -> None:
        self.assertFalse(self.bar.is_shown())
        self._show([_step(0, 2, 6)], visible=False)
        self.assertFalse(self.bar.is_shown())
        self.assertTrue(self.bar.shape().isEmpty())
        self._show([_step(0, 2, 6)], visible=True)
        self.assertTrue(self.bar.is_shown())

    def test_the_hit_area_is_the_strip_of_the_bar_and_the_tooltip_is_the_steps(self) -> None:
        self._show([_step(0, 2, 6), _step(1, 6, 9)], position=bm.POSITION_TOP, height_stop=10)
        view = self.bar.view_rect()
        top_y = view.bottom() - 0.05 * view.height()  # inside the top ten percent
        low_y = view.top() + 0.5 * view.height()
        self.assertTrue(self.bar.shape().contains(QPointF(4.0, top_y)))
        self.assertFalse(self.bar.shape().contains(QPointF(4.0, low_y)))  # the rest of the plot is not the bar's
        self.assertFalse(self.bar.shape().contains(QPointF(0.5, top_y)))  # before the first step
        self.assertEqual((self.bar.tooltip_at(4.0), self.bar.tooltip_at(6.0), self.bar.tooltip_at(9.5)), ("tip 0", "tip 1", ""))
        self._show([_step(0, 2, 6)], position=bm.POSITION_BOTTOM, height_stop=10)
        view = self.bar.view_rect()
        self.assertTrue(self.bar.shape().contains(QPointF(4.0, view.top() + 0.05 * view.height())))
        self.assertFalse(self.bar.shape().contains(QPointF(4.0, view.bottom() - 0.05 * view.height())))

    def test_a_full_bar_covers_the_plot(self) -> None:
        self._show([_step(0, 2, 6)], height_stop=bm.HEIGHT_FULL)
        view = self.bar.view_rect()
        self.assertTrue(self.bar.shape().contains(QPointF(4.0, view.top() + 0.5 * view.height())))

    def test_the_bar_is_drawn_behind_everything(self) -> None:
        self.assertLess(self.bar.zValue(), -10)  # below the filled bands of an average (-10) and the baseline selector (-5)

    def test_the_layout_follows_the_transform_and_clips_to_the_plot(self) -> None:
        self._show([_step(0, -3, 4, text="H2O"), _step(1, 4, 40, text="10perEG")], position=bm.POSITION_TOP, height_stop=10)
        view = QRectF(0.0, 0.0, 10.0, 100.0)  # visible data: x 0 - 10, y 0 - 100
        to_device = QTransform(60.0, 0.0, 0.0, -2.0, 20.0, 220.0)  # x: 60 px per unit from 20; y: up, 2 px per unit, 100 -> 20
        drawn = self.bar.layout(to_device, view)
        self.assertEqual(len(drawn), 2)
        area = to_device.mapRect(view)
        self.assertEqual((area.left(), area.right(), area.top(), area.bottom()), (20.0, 620.0, 20.0, 220.0))
        first, second = drawn
        self.assertEqual((first.rect.left(), first.rect.right()), (20.0, 260.0))  # clipped at the left edge of the plot; ends at x = 4
        self.assertEqual((second.rect.left(), second.rect.right()), (260.0, 620.0))  # clipped at the right edge
        self.assertEqual((first.rect.top(), first.rect.height()), (20.0, 20.0))  # ten percent of 200 px, along the top
        self.assertIsNone(first.left_edge)  # the step starts outside the plot: no boundary line there
        self.assertEqual(first.right_edge, 260.0)
        self.assertIsNone(second.right_edge)

    def test_text_is_dropped_when_the_bar_is_too_narrow_or_too_low(self) -> None:
        self._show([_step(0, 0, 0.3)], height_stop=10)
        to_device = QTransform(60.0, 0.0, 0.0, -2.0, 20.0, 220.0)
        drawn = self.bar.layout(to_device, QRectF(0.0, 0.0, 10.0, 100.0))
        self.assertEqual((drawn[0].text, drawn[0].text_rect), ("", None))  # 18 px wide: under the 40 px limit
        self._show([_step(0, 0, 8)], height_stop=1, text_pt=12)  # a 2 px strip cannot hold a line of text in the Bar mode
        drawn = self.bar.layout(to_device, QRectF(0.0, 0.0, 10.0, 100.0))
        self.assertIsNone(drawn[0].text_rect)

    def test_data_mode_puts_the_label_next_to_the_traces_of_the_step(self) -> None:
        levels = bm.SectionLevels(low=20.0, median=30.0, high=40.0)
        self._show([_step(0, 0, 8, levels=levels)], text_align=bm.ALIGN_DATA, text_stop=4, position=bm.POSITION_BOTTOM, height_stop=5)
        to_device = QTransform(60.0, 0.0, 0.0, -2.0, 20.0, 220.0)
        drawn = self.bar.layout(to_device, QRectF(0.0, 0.0, 10.0, 100.0))[0]
        high_y = 220.0 - 2.0 * 40.0  # device y of the top level
        self.assertIsNotNone(drawn.text_rect)
        self.assertLess(drawn.text_rect.bottom(), high_y)  # just above the data
        self.assertGreater(drawn.text_rect.bottom(), high_y - 3 * drawn.text_rect.height())

    def test_text_colour_contrasts_with_the_fill(self) -> None:
        self._show([_step(0, 0, 8, color="#ffffff")], alpha=1.0, height_stop=10)
        to_device = QTransform(60.0, 0.0, 0.0, -2.0, 20.0, 220.0)
        drawn = self.bar.layout(to_device, QRectF(0.0, 0.0, 10.0, 100.0))[0]
        self.assertEqual(drawn.text_color, "#0f1720")  # dark text on a light fill

    def test_the_bar_is_painted_in_its_colour_along_the_top_and_nowhere_else(self) -> None:
        self._show([_step(0, 2, 6, color="#ff0000")], alpha=1.0, position=bm.POSITION_TOP, height_stop=10)
        drawn = self.bar.layout(self.bar.deviceTransform(self.plot._widget.viewportTransform()), self.bar.view_rect())[0]
        image = self.plot.grab().toImage()
        x, y = int(drawn.rect.left()) + 3, int(drawn.rect.top()) + 1
        colour = image.pixelColor(x, y)
        self.assertGreater(colour.red(), 200)
        self.assertLess(colour.green(), 60)
        self.assertLess(colour.blue(), 60)
        below = image.pixelColor(x, int(drawn.rect.bottom()) + 30)
        self.assertFalse(below.red() > 200 and below.green() < 60)

    def test_making_room_widens_the_y_range_on_the_bars_side_only_and_hiding_gives_it_back(self) -> None:
        def y_range():
            return self.plot._item.getViewBox().viewRange()[1]

        self.plot.set_plan([], bm.PlanBarSettings())
        plain = y_range()
        data_low, data_high = 600.0, 620.0
        self._show([_step(0, 2, 6)], position=bm.POSITION_TOP, height_stop=10)
        top = y_range()
        self.assertAlmostEqual(top[0], plain[0], delta=0.01 * (plain[1] - plain[0]))  # the bottom did not move
        self.assertGreater(top[1] - data_high, plain[1] - data_high)  # more free space above the data
        self.assertAlmostEqual((top[1] - plain[1]) / (top[1] - top[0]), 0.10, delta=0.01)  # the bar's ten percent
        self._show([_step(0, 2, 6)], position=bm.POSITION_BOTTOM, height_stop=10)
        bottom = y_range()
        self.assertLess(bottom[0], plain[0])
        self._show([_step(0, 2, 6)], height_stop=bm.HEIGHT_FULL)  # a full bar sits behind the traces and takes no room
        full = y_range()
        self.assertAlmostEqual(full[1] - full[0], plain[1] - plain[0], delta=0.01 * (plain[1] - plain[0]))
        self.plot.set_plan([], bm.PlanBarSettings())
        again = y_range()
        self.assertAlmostEqual(again[0], plain[0], delta=0.01 * (plain[1] - plain[0]))
        self.assertAlmostEqual(again[1], plain[1], delta=0.01 * (plain[1] - plain[0]))
        self.assertGreater(data_high, data_low)

    def test_a_change_that_needs_no_room_does_not_range_the_view_again(self) -> None:
        self._show([_step(0, 2, 6)], height_stop=5)
        view_box = self.plot._item.getViewBox()
        view_box.setXRange(1.0, 3.0, padding=0.0)  # the user zoomed in
        self._show([_step(0, 2, 6)], height_stop=5, alpha=0.2)  # only the transparency changed
        self.assertEqual(tuple(round(v, 6) for v in view_box.viewRange()[0]), (1.0, 3.0))


# -- the ribbon group --------------------------------------------------------------------------------------------------------------


class PlanBarControlsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.host = QtWidgets.QWidget()
        self.controls = PlanBarControls(self.host)
        self.changes = 0
        self.controls.settings_changed.connect(self._count)

    def tearDown(self) -> None:
        self.host.deleteLater()

    def _count(self) -> None:
        self.changes += 1

    def test_the_defaults(self) -> None:
        settings = self.controls.settings()
        defaults = bm.PlanBarSettings()
        for name in ("visible", "position", "height_stop", "alpha", "text_mode", "separators", "text_align", "text_stop"):
            self.assertEqual(getattr(settings, name), getattr(defaults, name), name)
        self.assertGreaterEqual(settings.text_pt, bm.FONT_RANGE[0])

    def test_every_control_reports_one_change_and_shows_in_the_settings(self) -> None:
        c = self.controls
        c._show.setChecked(False)
        c._position.setCurrentIndex(c._position.findData(bm.POSITION_BOTTOM))
        c._height.setValue(7)
        c._alpha.setValue(20)
        c._content.setCurrentIndex(c._content.findData("solution"))
        c._separators.setText("_x_ ; _y_")
        c._separators.editingFinished.emit()
        c._stop.setValue(5)
        c._size.setValue(12)
        self.assertEqual(self.changes, 8)
        s = c.settings()
        self.assertEqual((s.visible, s.position, s.height_stop, s.alpha, s.text_mode, s.separators, s.text_stop, s.text_pt),
                         (False, bm.POSITION_BOTTOM, 7, 0.2, "solution", ("_x_", "_y_"), 5, 12))

    def test_the_height_box_has_one_more_stop_that_reads_full(self) -> None:
        box = self.controls._height
        box.setValue(10)
        self.assertEqual(box.text(), "10 %")
        box.setValue(box.value() + 1)
        self.assertEqual((box.value(), box.text()), (bm.HEIGHT_FULL, "Full"))
        self.assertEqual(box.value(), self.controls.settings().height_stop)
        box.setValue(box.value() + 1)
        self.assertEqual(box.value(), bm.HEIGHT_FULL)  # nothing beyond Full
        self.assertEqual(box.valueFromText("full"), bm.HEIGHT_FULL)
        self.assertEqual(box.valueFromText("3 %"), 3)
        self.assertEqual(box.valueFromText("99"), bm.HEIGHT_FULL)

    def test_each_text_mode_remembers_its_own_position(self) -> None:
        c = self.controls
        self.assertEqual(c._stop.value(), 3)  # the bar's middle
        c._stop.setValue(5)
        c._align.setCurrentIndex(c._align.findData(bm.ALIGN_DATA))
        self.assertEqual(c._stop.value(), 4)  # the data mode starts just above the data
        c._stop.setValue(1)
        c._align.setCurrentIndex(c._align.findData(bm.ALIGN_BAR))
        self.assertEqual(c._stop.value(), 5)
        self.assertEqual(c.settings().text_stop, 5)
        c._align.setCurrentIndex(c._align.findData(bm.ALIGN_DATA))
        self.assertEqual(c._stop.value(), 1)

    def test_switching_the_mode_is_one_change_and_the_tip_names_the_stop(self) -> None:
        c = self.controls
        before = self.changes
        c._align.setCurrentIndex(c._align.findData(bm.ALIGN_DATA))
        self.assertEqual(self.changes, before + 1)
        self.assertIn("Just above the data", c._stop.toolTip())
        c._align.setCurrentIndex(c._align.findData(bm.ALIGN_BAR))
        self.assertIn("Middle of the bar", c._stop.toolTip())

    def test_without_steps_the_group_is_greyed_out_and_says_why(self) -> None:
        self.controls.set_available(False)
        self.assertTrue(all(not w.isEnabled() for w in self.controls.widgets()))
        self.assertIn("no experiment steps", self.controls._show.toolTip())
        self.controls.set_available(True)
        self.assertTrue(all(w.isEnabled() for w in self.controls.widgets()))
        self.assertNotIn("no experiment steps", self.controls._show.toolTip())

    def test_every_control_is_remembered_and_comes_back(self) -> None:
        store = UiStateStore({})
        self.controls.restore_ui_state(store)
        c = self.controls
        c._show.setChecked(False)
        c._position.setCurrentIndex(c._position.findData(bm.POSITION_BOTTOM))
        c._height.setValue(bm.HEIGHT_FULL)
        c._alpha.setValue(33)
        c._content.setCurrentIndex(c._content.findData("solution"))
        c._separators.setText("_x_")
        c._separators.editingFinished.emit()
        c._stop.setValue(2)
        c._align.setCurrentIndex(c._align.findData(bm.ALIGN_DATA))
        c._stop.setValue(5)
        c._size.setValue(15)
        saved = c.settings()

        other_host = QtWidgets.QWidget()
        other = PlanBarControls(other_host)
        other.restore_ui_state(UiStateStore(dict(store._values)))
        self.assertEqual(other.settings(), saved)
        other._align.setCurrentIndex(other._align.findData(bm.ALIGN_BAR))
        self.assertEqual(other._stop.value(), 2)  # the bar mode's own stop came back too
        other_host.deleteLater()

    def test_values_that_no_longer_fit_are_ignored(self) -> None:
        store = UiStateStore({
            "sensorgram/plan_position": "sideways", "sensorgram/plan_text_mode": 5, "sensorgram/plan_height": 400,
            "sensorgram/plan_text_stop_bar": 99, "sensorgram/plan_alpha": "dark",
        })
        self.controls.restore_ui_state(store)
        s = self.controls.settings()
        defaults = bm.PlanBarSettings()
        self.assertEqual((s.position, s.text_mode, s.alpha), (defaults.position, defaults.text_mode, defaults.alpha))
        self.assertEqual(s.height_stop, defaults.height_stop)
        self.assertEqual(s.text_stop, bm.STOP_MAX)  # clamped to the slider


# -- the panel ---------------------------------------------------------------------------------------------------------------------

H, W = 64, 90
WAVELENGTHS = (480.0, 510.0, 540.0, 570.0, 600.0, 630.0)
N_CUBE = 6
T0 = 1_700_000_000_000  # unix ms of the first image
CUBE_MS = 3_000
CENTERS = {1: (40.0, 30.0, 515.0), 2: (60.0, 34.0, 520.0)}


def _metadata() -> ImagingAcquisitionMetadata:
    timings = [
        ImagingCubeTiming(spectral_cube_index=cube, wavelength_nm=wl, acquired_at_unix_ms=T0 + CUBE_MS * cube + 100 * k)
        for cube in range(N_CUBE) for k, wl in enumerate(WAVELENGTHS)
    ]
    steps = [
        ImagingPlanStep(step=1, label="H2O_--_ch1F", start_unix_ms=T0, end_unix_ms=T0 + 3 * CUBE_MS),  # cubes 0 - 2
        ImagingPlanStep(step=2, label="10perEG_-_ch1F", start_unix_ms=T0 + 3 * CUBE_MS, end_unix_ms=T0 + 6 * CUBE_MS + 100),  # cubes 3 - 5
    ]
    return ImagingAcquisitionMetadata(source_format="test", image_timings=timings, plan=ImagingPlan(steps=steps))


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
    return ImageDataset(folder=root, records=records, source_format="image_stack", acquisition_metadata=_metadata())


class PlanBarPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.m.session_autosave._suspended = True
        self.workflow = AnalysisTab()
        app_rewrite._wire_analysis_scan(self.m, self.workflow, UiStateStore({}))
        self.m.scan_results._timer.setInterval(30)
        self.panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask, experiment_plan=self.m.experiment_plan,
        )
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _open_scan_and_draw(self) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.m.selection.set_roi_selection({1, 2})
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None), "the panel never drew")

    def _bar(self):
        return self.panel._plot.plan_bar()

    def test_the_steps_are_drawn_on_the_cube_axis(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        steps = self._bar().steps()
        self.assertEqual([(s.text, round(s.x_start, 6), round(s.x_end, 6)) for s in steps], [("H2O_--_ch1F", 0.0, 3.0), ("10perEG_-_ch1F", 3.0, round(6.0 + 100 / CUBE_MS, 6))])
        self.assertTrue(self._bar().is_shown())
        self.assertTrue(self.panel._plan_controls.is_available())
        self.assertIsNotNone(steps[0].levels)  # the drawn traces give the step its levels
        self.assertIn("Step 1", steps[0].tooltip)

    def test_the_steps_move_with_the_time_axis_in_seconds(self) -> None:
        self.m.cube_display.set_mode(MODE_TIME)
        self._open_scan_and_draw()
        steps = self._bar().steps()
        self.assertEqual([(round(s.x_start, 6), round(s.x_end, 6)) for s in steps], [(0.0, 9.0), (9.0, 18.1)])
        self.m.cube_display.set_mode(MODE_CUBE)
        self.assertTrue(_pump(lambda: round(self._bar().steps()[0].x_end, 3) == 3.0))

    def test_the_settings_reach_the_bar_and_the_text_follows_the_rule(self) -> None:
        self._open_scan_and_draw()
        controls = self.panel._plan_controls
        controls._content.setCurrentIndex(controls._content.findData("solution"))
        self.assertEqual([s.text for s in self._bar().steps()], ["H2O", "10perEG"])
        controls._show.setChecked(False)
        self.assertFalse(self._bar().is_shown())
        controls._show.setChecked(True)
        controls._height.setValue(bm.HEIGHT_FULL)
        self.assertTrue(self._bar().settings().is_full)

    def test_an_edit_in_the_module_redraws_the_bar_and_undo_takes_it_back(self) -> None:
        self._open_scan_and_draw()
        self.m.experiment_plan.set_label(0, "Water")
        self.assertEqual(self._bar().steps()[0].text, "Water")
        self.assertTrue(self._bar().steps()[0].edited)
        undo_manager.undo()
        self.assertEqual(self._bar().steps()[0].text, "H2O_--_ch1F")

    def test_an_edited_boundary_moves_the_bar_segments(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        plan = self.m.experiment_plan
        grid = plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5])
        plan.move_edge(0, "end", T0 + 4 * CUBE_MS, grid, together=True)
        steps = self._bar().steps()
        self.assertEqual((round(steps[0].x_end, 6), round(steps[1].x_start, 6)), (4.0, 4.0))

    def test_without_the_plan_module_the_group_is_greyed_out(self) -> None:
        panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask,
        )
        self.assertFalse(panel._plan_controls.is_available())
        self.assertFalse(panel._plot.plan_bar().is_shown())
        panel.shutdown()
        panel.deleteLater()

    def test_a_dataset_without_steps_greys_the_group_out_and_clearing_hides_the_bar(self) -> None:
        self._open_scan_and_draw()
        self.assertTrue(self.panel._plan_controls.is_available())
        self.m.selection.set_roi_selection(set())  # nothing drawn: no axis to put the steps on
        self.assertTrue(_pump(lambda: not self._bar().is_shown()))
        dataset = self.m.dataset._dataset
        dataset.acquisition_metadata = dataset.acquisition_metadata.model_copy(update={"plan": None})
        self.m.experiment_plan.steps_changed.emit("dataset")
        self.assertFalse(self.panel._plan_controls.is_available())


if __name__ == "__main__":
    unittest.main()
