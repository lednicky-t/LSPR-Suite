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
    from lspr_imaging_app.panels.sensorgram.edit_group import PlanEditControls
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.sensorgram.plan_group import PlanBarControls
    from lspr_imaging_app.panels.sensorgram.plan_icons import (
        add_step_after_icon, add_step_at_cursor_icon, add_step_before_icon, merge_steps_icon, remove_step_icon, restore_steps_icon,
        split_step_icon,
    )
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan import bar_model as bm
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE, MODE_TIME
    from lspr_imaging_app.undo import undo_manager
    from lspr_ui import get_active_theme
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
        for name in ("visible", "position", "height_stop", "alpha", "text_mode", "separators", "text_align", "text_stop", "show_ri"):
            self.assertEqual(getattr(settings, name), getattr(defaults, name), name)
        self.assertFalse(settings.show_ri)  # the refractive index after the text is opt-in
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
        c._show_ri.setChecked(True)
        self.assertEqual(self.changes, 9)
        s = c.settings()
        self.assertEqual((s.visible, s.position, s.height_stop, s.alpha, s.text_mode, s.separators, s.text_stop, s.text_pt, s.show_ri),
                         (False, bm.POSITION_BOTTOM, 7, 0.2, "solution", ("_x_", "_y_"), 5, 12, True))

    def test_top_bottom_is_hidden_while_the_height_is_full_and_keeps_its_choice(self) -> None:
        c = self.controls
        c._position.setCurrentIndex(c._position.findData(bm.POSITION_BOTTOM))
        self.assertFalse(c._position.isHidden())
        c._height.setValue(bm.HEIGHT_FULL)
        self.assertTrue(c._position.isHidden())
        self.assertEqual(c.settings().position, bm.POSITION_BOTTOM)
        c._height.setValue(4)
        self.assertFalse(c._position.isHidden())

    def test_every_control_lives_in_the_one_steps_menu(self) -> None:
        """2026-11-13: the plan bar's fields and its "Step labels" pop-up became one menu, like the Legend menu."""
        c = self.controls
        self.assertEqual(c.widgets(), (c.menu,))  # the ribbon shows one icon
        inside = c.menu.findChildren(QtWidgets.QWidget)  # recursive, and through the menu's own window (isAncestorOf stops at a window)
        for control in (c._show, c._height, c._position, c._alpha, c._content, c._show_ri, c._align, c._stop, c._size):
            self.assertIn(control, inside)
        self.assertFalse(c.menu.icon().isNull())
        captions = [label.text() for label in c.menu.findChildren(QtWidgets.QLabel) if label.text()]
        self.assertEqual(captions[:5], ["Show", "Height", "Edge", "Transparency", "Step labels"])  # the labels are a heading of the same panel

    def test_the_edge_row_goes_with_its_caption_while_the_bar_is_full(self) -> None:
        c = self.controls
        caption = next(label for label in c.menu.findChildren(QtWidgets.QLabel) if label.text() == "Edge")
        self.assertFalse(caption.isHidden())
        c._height.setValue(bm.HEIGHT_FULL)
        self.assertTrue(caption.isHidden())  # not a caption without its control
        c._height.setValue(4)
        self.assertFalse(caption.isHidden())

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
        c._show_ri.setChecked(True)
        saved = c.settings()
        self.assertTrue(saved.show_ri)

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


class PlanEditControlsTest(unittest.TestCase):
    """The Edit group (2026-11-10) on its own: which button is on for which state, what it says when it is off, and what it asks for when clicked."""

    def setUp(self) -> None:
        self.host = QtWidgets.QWidget()
        self.controls = PlanEditControls(self.host)
        self.asked: list[tuple] = []
        self.controls.add_requested.connect(lambda where: self.asked.append(("add", where)))
        self.controls.remove_requested.connect(lambda: self.asked.append(("remove",)))
        self.controls.merge_requested.connect(lambda: self.asked.append(("merge",)))
        self.controls.split_requested.connect(lambda: self.asked.append(("split",)))
        self.controls.restore_requested.connect(lambda: self.asked.append(("restore",)))

    def tearDown(self) -> None:
        self.host.deleteLater()

    def _enabled(self) -> tuple[bool, ...]:
        """(add at cursor, add in front, add behind, remove, merge); restore is asked for by name."""
        c = self.controls
        return tuple(b.isEnabled() for b in (c.add_cursor_button(), c.add_before_button(), c.add_after_button(), c.remove_button(), c.merge_button()))

    def test_the_group_is_greyed_out_until_there_are_steps_and_says_why(self) -> None:
        self.assertFalse(self.controls.is_available())
        self.assertEqual(self._enabled(), (False,) * 5)
        self.assertIn("no experiment steps", self.controls.remove_button().toolTip())
        self.assertIn("no experiment steps", self.controls.add_cursor_button().toolTip())
        self.controls.set_state(False, 2, True, True, False, True, True)  # a selection and a cursor mean nothing without steps
        self.assertEqual(self._enabled(), (False,) * 5)
        self.assertFalse(self.controls.split_button().isEnabled())
        self.assertIn("no experiment steps", self.controls.split_button().toolTip())

    def test_each_button_is_on_when_its_command_could_act(self) -> None:
        c = self.controls
        for state, expected in (  # (available, selected, can_add, can_merge, can_restore, cursor_on_plot) -> (at cursor, in front, behind, remove, merge)
            ((True, 0, True, False, False, False), (False, False, False, False, False)),  # nothing selected, no cursor on the plot
            ((True, 0, True, False, False, True), (True, False, False, False, False)),  # the cursor alone is enough for a step at the cursor
            ((True, 1, True, False, False, True), (True, True, True, True, False)),  # one step: both sides and remove (a single step cannot be merged)
            ((True, 1, False, False, False, False), (False, False, False, True, False)),  # no traces on the plot: a new boundary has no grid to snap to
            ((True, 2, True, True, False, True), (True, False, False, True, True)),  # two neighbours: remove and merge; the sides need exactly one
            ((True, 2, True, False, False, True), (True, False, False, True, False)),  # two steps with another between them cannot be merged
        ):
            c.set_state(*state)
            self.assertEqual(self._enabled(), expected, state)

    def test_a_button_that_is_off_says_what_is_missing(self) -> None:
        c = self.controls
        c.set_state(True, 0, True, False)
        self.assertIn("cursor is not on the plot", c.add_cursor_button().toolTip())
        self.assertIn("in front of the selected step: select one step first", c.add_before_button().toolTip())
        self.assertIn("behind the selected step: select one step first", c.add_after_button().toolTip())
        self.assertIn("Click a step on the plan bar", c.remove_button().toolTip())
        self.assertIn("next to each other", c.merge_button().toolTip())
        c.set_state(True, 2, True, False)
        self.assertIn("Select just one", c.add_before_button().toolTip())
        c.set_state(True, 1, False, False)
        self.assertIn("needs traces", c.add_after_button().toolTip())
        c.set_state(True, 1, True, False, False, True)
        self.assertIn("In front of this step", c.add_before_button().toolTip())  # on again: the tooltips describe the commands
        self.assertIn("Behind this step", c.add_after_button().toolTip())
        self.assertIn("no step selected", c.add_cursor_button().toolTip())

    def test_the_buttons_ask_for_their_command(self) -> None:
        c = self.controls
        c.set_state(True, 1, True, False, False, True)
        c.add_cursor_button().click()
        c.add_before_button().click()
        c.add_after_button().click()
        c.remove_button().click()
        c.merge_button().click()  # off: a click on a button that cannot act does nothing
        self.assertEqual(self.asked, [("add", "cursor"), ("add", "before"), ("add", "after"), ("remove",)])
        c.set_state(True, 2, True, True)
        c.merge_button().click()
        self.assertEqual(self.asked[-1], ("merge",))
        c.set_state(True, 0, True, False, False, True, True)
        c.split_button().click()
        self.assertEqual(self.asked[-1], ("split",))

    def test_split_needs_the_cursor_inside_a_step_and_no_selection(self) -> None:
        c = self.controls
        split = c.split_button()
        for state, expected in (  # (available, selected, can_add, can_merge, can_restore, cursor_on_plot, cursor_inside_a_step)
            ((True, 0, True, False, False, True, True), True),  # no selection needed
            ((True, 2, True, True, False, True, True), True),  # and whatever is selected
            ((True, 0, True, False, False, True, False), False),  # the cursor is on a start of a step or between steps
            ((True, 0, True, False, False, False, False), False),  # the cursor is not on the plot
        ):
            c.set_state(*state)
            self.assertEqual(split.isEnabled(), expected, state)
        c.set_state(True, 0, True, False, False, True, False)
        self.assertIn("inside a step", split.toolTip())
        c.set_state(True, 0, True, False, False, False, False)
        self.assertIn("not on the plot", split.toolTip())
        c.set_state(True, 0, True, False, False, True, True)
        self.assertIn("No step has to be selected", split.toolTip())

    def test_restore_needs_no_selection_only_something_removed(self) -> None:
        c = self.controls
        restore = c.restore_button()
        c.set_state(True, 0, True, False)  # nothing removed
        self.assertFalse(restore.isEnabled())
        self.assertIn("nothing has been removed", restore.toolTip())
        c.set_state(True, 0, True, False, True)  # something removed, nothing selected
        self.assertTrue(restore.isEnabled())
        restore.click()
        self.assertEqual(self.asked, [("restore",)])
        c.set_state(True, 2, True, True, True)  # whatever is selected
        self.assertTrue(restore.isEnabled())

    def test_when_every_step_was_removed_restore_is_the_one_button_left_and_says_so(self) -> None:
        c = self.controls
        c.set_state(False, 0, False, False, True, False)  # no steps left, some removed
        self.assertFalse(c.is_available())
        self.assertEqual(self._enabled(), (False,) * 5)
        self.assertTrue(c.restore_button().isEnabled())
        self.assertFalse(c.split_button().isEnabled())
        self.assertIn("every step was removed", c.remove_button().toolTip())
        c.set_state(False, 0, False, False, False)  # a dataset that never had steps: all off, and the tooltip says why
        self.assertFalse(c.restore_button().isEnabled())
        self.assertIn("no experiment steps", c.restore_button().toolTip())

    def _drawn(self, icon) -> tuple:
        """(the image, the pixels drawn as (x, y, colour)) of an icon rendered at 36 px; faint anti-aliasing pixels are left out of the colour checks."""
        image = icon.pixmap(36, 36).toImage()
        drawn = [(x, y, image.pixelColor(x, y)) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha() > 80]
        return image, drawn

    @staticmethod
    def _is_ink(c) -> bool:  # the ink is red in these tests
        return c.red() > 200 and c.blue() < 60

    @staticmethod
    def _is_accent(c) -> bool:  # and the accent is blue
        return c.blue() > 200 and c.red() < 60

    def test_the_concerned_step_is_in_the_accent_colour_and_the_other_in_ink(self) -> None:
        """Add (behind / in front), remove and restore: one box is the ordinary ink, the dashed box and its symbol are the accent: two colours, nothing else. The dashed box is on
        the right except for "in front of", where the new step comes first in time."""
        for make, ghost_on_right in (
            (add_step_after_icon, True), (add_step_before_icon, False), (remove_step_icon, True), (restore_steps_icon, True),
        ):
            _image, drawn = self._drawn(make("#ff0000", "#0000ff"))
            self.assertGreater(len(drawn), 50, make.__name__)
            left = [c for x, _y, c in drawn if x < 18]
            right = [c for x, _y, c in drawn if x >= 18]
            self.assertTrue(left and right, make.__name__)
            ink_side, accent_side = (left, right) if ghost_on_right else (right, left)
            self.assertTrue(all(self._is_ink(c) for c in ink_side), f"{make.__name__}: the selected step is ink")
            self.assertTrue(all(self._is_accent(c) for c in accent_side), f"{make.__name__}: the dashed step and its symbol are the accent")

    def test_add_at_the_cursor_is_an_ink_cursor_line_and_an_accent_step_starting_at_it(self) -> None:
        _image, drawn = self._drawn(add_step_at_cursor_icon("#ff0000", "#0000ff"))
        self.assertGreater(len(drawn), 50)
        line = [(x, y) for x, y, c in drawn if self._is_ink(c)]
        self.assertGreater(len(line), 20)  # the cursor: a tall thin line near the left, taller than the step beside it
        self.assertTrue(all(3 <= x <= 11 for x, _y in line))
        self.assertGreater(max(y for _x, y in line) - min(y for _x, y in line), 24)
        accent = [(x, y) for x, y, c in drawn if self._is_accent(c)]
        self.assertGreater(max(x for x, _y in accent), 30)  # the new step runs to the right, to the end
        self.assertTrue(all(not self._is_ink(c) for x, _y, c in drawn if x >= 12))  # and nothing right of the cursor is ink: no other step is drawn

    def test_split_is_two_ink_halves_and_a_tall_accent_cut_between_them(self) -> None:
        _image, drawn = self._drawn(split_step_icon("#ff0000", "#0000ff"))
        self.assertGreater(len(drawn), 50)
        self.assertTrue(all(self._is_ink(c) for x, _y, c in drawn if x <= 15 or x >= 21))  # the two halves, one each side
        self.assertTrue(any(x <= 15 for x, _y, _c in drawn) and any(x >= 21 for x, _y, _c in drawn))
        cut = [(x, y) for x, y, c in drawn if 17 <= x <= 19 and self._is_accent(c)]
        self.assertGreater(max(y for _x, y in cut) - min(y for _x, y in cut), 24)  # the cut is taller than the halves
        halves_top = min(y for x, y, c in drawn if (x <= 15 or x >= 21) and self._is_ink(c))
        self.assertLess(min(y for _x, y in cut), halves_top - 3)

    def test_merge_has_no_concerned_step_and_stays_in_ink(self) -> None:
        _image, drawn = self._drawn(merge_steps_icon("#ff0000"))
        self.assertGreater(len(drawn), 50)
        self.assertTrue(all(self._is_ink(c) for _x, _y, c in drawn))

    def test_the_seven_icons_differ(self) -> None:
        images = [
            add_step_at_cursor_icon("#000000", "#00ff00").pixmap(36, 36).toImage(), add_step_before_icon("#000000", "#00ff00").pixmap(36, 36).toImage(),
            add_step_after_icon("#000000", "#00ff00").pixmap(36, 36).toImage(), remove_step_icon("#000000", "#00ff00").pixmap(36, 36).toImage(),
            merge_steps_icon("#000000").pixmap(36, 36).toImage(), restore_steps_icon("#000000", "#00ff00").pixmap(36, 36).toImage(),
            split_step_icon("#000000", "#00ff00").pixmap(36, 36).toImage(),
        ]
        for i, first in enumerate(images):
            for second in images[i + 1:]:
                self.assertFalse(first == second)

    def test_the_buttons_carry_the_theme_colours(self) -> None:
        """The icons the group sets are the ones made from the theme's ink and its green / red / blue (checked on the pixels of the buttons' own icons)."""
        theme = get_active_theme()
        ink = theme.text_primary
        c = self.controls
        expected = (
            (c.add_cursor_button(), add_step_at_cursor_icon(ink, theme.accent_green)), (c.add_before_button(), add_step_before_icon(ink, theme.accent_green)),
            (c.add_after_button(), add_step_after_icon(ink, theme.accent_green)), (c.remove_button(), remove_step_icon(ink, theme.accent_red)),
            (c.merge_button(), merge_steps_icon(ink)), (c.restore_button(), restore_steps_icon(ink, theme.accent_blue)),
            (c.split_button(), split_step_icon(ink, theme.accent_gold)),
        )
        for index, (button, icon) in enumerate(expected):
            self.assertTrue(button.icon().pixmap(36, 36).toImage() == icon.pixmap(36, 36).toImage(), index)


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

    def test_during_play_the_cursor_follows_the_frame_on_screen_not_the_slider(self) -> None:
        """2026-11-11: while Play runs the cursor moves when the Image panel is about to show a frame (`show_play_frame`), so it reaches
        the screen in the same window repaint as the image; the cube slider is one step ahead of it."""
        self._open_scan_and_draw()
        cubes = list(self.m.scan_results.stored().cube_indices)
        self.assertGreaterEqual(len(cubes), 3)
        cursor = self.panel._plot._axis_cursor
        self.m.selection.set_cube(cubes[0])
        at_first = cursor.x()
        self.panel.follow_play(True)
        self.m.selection.set_cube(cubes[2])  # the slider moved on: the frame on screen is still the first
        self.assertEqual(cursor.x(), at_first)
        self.panel.show_play_frame(cubes[1])
        at_second = cursor.x()
        self.assertNotEqual(at_second, at_first)
        self.panel.follow_play(False)  # Play stopped: back to the slider
        self.assertNotEqual(cursor.x(), at_second)
        self.m.selection.set_cube(cubes[1])
        self.assertEqual(cursor.x(), at_second)
        self.panel.show_play_frame(cubes[0])  # not playing: ignored
        self.assertEqual(cursor.x(), at_second)

    def test_the_steps_are_drawn_on_the_cube_axis(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        steps = self._bar().steps()
        self.assertEqual([(s.text, round(s.x_start, 6), round(s.x_end, 6)) for s in steps], [("H2O_--_ch1F", 0.0, 3.0), ("10perEG_-_ch1F", 3.0, round(6.0 + 100 / CUBE_MS, 6))])
        self.assertTrue(self._bar().is_shown())
        self.assertTrue(self.panel._steps.plan_controls.is_available())
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
        controls = self.panel._steps.plan_controls
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

    # -- the Experimental plan tab, the Evaluation pickers and the structure tools (2026-10-29) ------------------------------------------

    def test_the_control_row_has_a_plot_tab_a_view_tab_and_an_experimental_plan_tab(self) -> None:
        tabs = self.panel._ribbon_tabs
        self.assertEqual(tabs._category_names, ["Plot", "View", "Experimental plan"])  # the View tab: 2026-11-13
        self.assertEqual(self.panel.current_ribbon_tab(), "Plot")  # the first one is shown at the start
        self.assertEqual([c.text() for c in self.panel._plan_ribbon._captions], ["Edit", "Evaluation", "Checks"])  # the two View groups moved to the View tab (2026-11-13)
        self.assertEqual([c.text() for c in self.panel._view_ribbon._captions], ["Show", "Points & lines", "Legend", "Steps", "Evaluation", "Stats"])  # "View steps" / "View stats" until 2026-11-13; Evaluation the same day

    def test_the_structure_tools_are_live_only_while_the_experimental_plan_tab_is_shown(self) -> None:
        self._open_scan_and_draw()
        actions = self.panel._steps.actions
        self.assertFalse(actions.structure_tools_active())
        self.assertNotIn("Remove step", [a.text() for a in actions.build_menu(0).actions()])
        self.panel._ribbon_tabs.set_category("Experimental plan")
        self.assertTrue(actions.structure_tools_active())
        texts = [a.text() for a in actions.build_menu(0).actions()]
        self.assertIn("Remove step", texts)
        self.assertIn("Use as", texts)
        self.panel._ribbon_tabs.set_category("Plot")
        self.assertFalse(actions.structure_tools_active())
        self.assertIn("Edit text...", [a.text() for a in actions.build_menu(0).actions()])  # the old items stay on every tab

    def test_the_shown_tab_is_remembered_and_brings_its_tools_back(self) -> None:
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        self.panel._ribbon_tabs.set_category("Experimental plan")
        saved = dict(store._values)
        self.assertEqual(saved["sensorgram/ribbon_tab"], "Experimental plan")
        other = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask, experiment_plan=self.m.experiment_plan,
        )
        other.restore_ui_state(UiStateStore(saved))
        self.assertEqual(other.current_ribbon_tab(), "Experimental plan")
        self.assertTrue(other._steps.actions.structure_tools_active())
        other.shutdown()
        other.deleteLater()

    def test_an_unknown_remembered_tab_is_ignored(self) -> None:
        self.panel.restore_ui_state(UiStateStore({"sensorgram/ribbon_tab": "Gone"}))
        self.assertEqual(self.panel.current_ribbon_tab(), "Plot")

    def test_the_evaluation_pickers_list_the_steps_and_write_the_roles(self) -> None:
        self._open_scan_and_draw()
        evaluation = self.panel._steps.evaluation
        picker = evaluation._picker(1, "baseline")
        self.assertEqual([picker.itemText(i) for i in range(picker.count())], ["not set", "1 · H2O_--_ch1F", "2 · 10perEG_-_ch1F"])
        picker.setCurrentIndex(2)
        picker.activated.emit(2)  # the user picked the second step
        self.assertEqual(self.m.experiment_plan.baseline_step().order, 2)
        self.assertEqual([s.roles for s in self._bar().steps()], [(), ("B1",)])  # the chip is on the bar
        self.assertEqual(picker.currentIndex(), 2)
        self.m.experiment_plan.set_role("baseline", None)  # cleared elsewhere (the menu, an undo): the picker follows without sending anything
        self.assertEqual(picker.currentIndex(), 0)
        self.assertIsNone(self.m.experiment_plan.baseline_step())

    def test_the_evaluation_pickers_follow_a_role_set_in_the_menu_and_an_undo(self) -> None:
        self._open_scan_and_draw()
        self.m.experiment_plan.set_role("signal", 0)
        self.assertEqual(self.panel._steps.evaluation._picker(1, "signal").currentIndex(), 1)
        undo_manager.undo()
        self.assertEqual(self.panel._steps.evaluation._picker(1, "signal").currentIndex(), 0)

    def test_a_second_pair_picked_in_the_popup_reaches_the_plan_and_the_bar(self) -> None:
        self._open_scan_and_draw()
        evaluation = self.panel._steps.evaluation
        evaluation._add.click()  # B2 + S2
        self.assertEqual(evaluation.pair_count(), 2)
        picker = evaluation._picker(2, "signal")
        picker.setCurrentIndex(1)
        picker.activated.emit(1)  # the user picked the first step for S2
        self.assertEqual(self.m.experiment_plan.signal_step(2).order, 1)
        self.assertIsNone(self.m.experiment_plan.signal_step(1))
        self.assertEqual([s.roles for s in self._bar().steps()], [("S2",), ()])  # the chip says which pair
        evaluation._remove.click()  # taking pair 2 away clears its roles in the plan, in one undo step
        self.assertEqual(evaluation.pair_count(), 1)
        self.assertIsNone(self.m.experiment_plan.signal_step(2))
        self.assertEqual([s.roles for s in self._bar().steps()], [(), ()])
        undo_manager.undo()
        self.assertEqual(self.m.experiment_plan.signal_step(2).order, 1)
        self.assertEqual(evaluation.pair_count(), 2)  # a pair that is set in the plan is shown again
        self.assertEqual(evaluation._picker(2, "signal").currentIndex(), 1)

    def test_a_structure_edit_clears_the_selection_redraws_the_bar_and_keeps_roles_on_their_steps(self) -> None:
        self._open_scan_and_draw()
        self.m.experiment_plan.set_role("signal", 1)
        self._bar().select_many((0, 1), anchor=0)
        self.m.experiment_plan.remove_steps([0])
        self.assertEqual(self._bar().selected_indices(), ())
        self.assertEqual([(s.text, s.roles) for s in self._bar().steps()], [("10perEG_-_ch1F", ("S1",))])  # the signal step is now number 1
        undo_manager.undo()
        self.assertEqual(len(self._bar().steps()), 2)

    def test_a_step_the_user_added_is_drawn_and_marked_as_added(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        plan = self.m.experiment_plan
        ref = plan.add_step(0, "after", plan.snap_grid_unix_ms([0, 1, 2, 3, 4, 5]))
        self.assertIsNotNone(ref)
        steps = self._bar().steps()
        self.assertEqual([s.added for s in steps], [False, True, False])
        self.assertIn("Added by you", steps[1].tooltip)
        self.assertEqual(round(steps[0].x_end, 6), round(steps[1].x_start, 6))  # the new step starts where the one it was cut from now ends

    # -- the Edit group: add / remove / merge / restore steps from the ribbon (2026-11-10) ----------------------------------------------

    def _edit(self) -> PlanEditControls:
        return self.panel._steps.edit_controls

    def _edit_enabled(self) -> tuple[bool, ...]:
        """(add at cursor, add in front, add behind, remove, merge)"""
        c = self._edit()
        return tuple(b.isEnabled() for b in (c.add_cursor_button(), c.add_before_button(), c.add_after_button(), c.remove_button(), c.merge_button()))

    def test_the_edit_buttons_follow_the_selection_on_the_bar(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        self.assertTrue(self._edit().is_available())
        self.assertEqual(self._edit_enabled(), (True, False, False, False, False))  # nothing selected: only a step at the cursor, which needs none
        self._bar().select(0)
        self.assertEqual(self._edit_enabled(), (True, True, True, True, False))
        self._bar().select_many((0, 1), anchor=0)
        self.assertEqual(self._edit_enabled(), (True, False, False, True, True))
        self._bar().clear_selection()
        self.assertEqual(self._edit_enabled(), (True, False, False, False, False))

    def test_add_remove_and_merge_edit_the_plan_each_as_one_undo_step(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        plan, edit = self.m.experiment_plan, self._edit()

        self._bar().select(0)
        edit.add_after_button().click()  # a new step behind the first
        self.assertEqual([s.added for s in self._bar().steps()], [False, True, False])
        self.assertEqual(self._bar().selected_indices(), (1,))  # the new step is selected, ready to be dragged into place
        undo_manager.undo()
        self.assertEqual(len(plan.steps()), 2)
        self.assertEqual(self._edit_enabled(), (True, False, False, False, False))  # a structure change clears the selection, and the buttons follow

        self._bar().select(1)
        edit.add_before_button().click()  # a new step in front of the second
        self.assertEqual([s.added for s in self._bar().steps()], [False, True, False])
        undo_manager.undo()

        self._bar().select(1)
        edit.remove_button().click()
        self.assertEqual(len(plan.steps()), 1)
        self.assertTrue(plan.has_removed())
        undo_manager.undo()
        self.assertEqual(len(plan.steps()), 2)

        self._bar().select_many((0, 1), anchor=0)
        edit.merge_button().click()
        self.assertEqual(len(plan.steps()), 1)
        self.assertEqual(self._bar().selected_indices(), (0,))  # the step they became
        undo_manager.undo()
        self.assertEqual(len(plan.steps()), 2)

    def test_add_at_the_cursor_needs_no_selected_step_and_is_one_undo_step(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        plan, edit = self.m.experiment_plan, self._edit()
        self.m.selection.set_cube(1)  # inside step 1 (cubes 0 - 3)
        self._bar().clear_selection()
        self.assertTrue(edit.add_cursor_button().isEnabled())
        edit.add_cursor_button().click()
        steps = plan.steps()
        cube1 = self.m.dataset.cube_start_unix_ms(1)
        self.assertEqual([s.is_added for s in steps], [False, True, False])
        self.assertEqual((steps[0].end_unix_ms, steps[1].start_unix_ms), (cube1, cube1))  # step 1 is cut where the cursor is, the new step starts there
        self.assertEqual(self._bar().selected_indices(), (1,))  # and is selected, ready to be dragged into place
        undo_manager.undo()
        self.assertEqual(len(plan.steps()), 2)

    def test_add_at_the_cursor_on_the_start_of_a_step_says_so_and_changes_nothing(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        notices: list[str] = []
        self.m.experiment_plan.notice.connect(notices.append)
        history = undo_manager.undo_label
        self.m.selection.set_cube(0)  # cube 0 starts step 1: nothing of it is left to give
        self._edit().add_cursor_button().click()
        self.assertEqual(len(self.m.experiment_plan.steps()), 2)
        self.assertEqual(undo_manager.undo_label, history)
        self.assertEqual(len(notices), 1)
        self.assertIn("cursor", notices[0])

    def test_the_cursor_button_follows_whether_the_cursor_is_on_the_plot_without_rebuilding_the_axis(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        steps, edit = self.panel._steps, self._edit()
        self.assertTrue(edit.add_cursor_button().isEnabled())
        steps._plan_axis = lambda: (_ for _ in ()).throw(AssertionError("the axis is rebuilt at every cursor move"))  # type: ignore[method-assign]
        steps._current_cube = lambda: 999  # a cube that is not one of the plotted cubes
        steps.cursor_moved()
        self.assertFalse(edit.add_cursor_button().isEnabled())
        self.assertIn("not on the plot", edit.add_cursor_button().toolTip())
        self.assertFalse(edit.split_button().isEnabled())
        steps._current_cube = lambda: 1
        steps.cursor_moved()
        self.assertTrue(edit.add_cursor_button().isEnabled())
        self.assertTrue(edit.split_button().isEnabled())  # inside step 1
        steps._current_cube = lambda: 0
        steps.cursor_moved()
        self.assertTrue(edit.add_cursor_button().isEnabled())
        self.assertFalse(edit.split_button().isEnabled())  # on the first cube of step 1: nothing of it to give

    def test_split_follows_the_cube_slider(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        split = self._edit().split_button()
        self.m.selection.set_cube(0)  # the first cube of step 1
        self.assertFalse(split.isEnabled())
        self.assertIn("inside a step", split.toolTip())
        self.m.selection.set_cube(1)  # inside it
        self.assertTrue(split.isEnabled())
        self.m.selection.set_cube(3)  # the first cube of step 2: a boundary, not inside either step
        self.assertFalse(split.isEnabled())
        self.m.selection.set_cube(4)  # inside step 2
        self.assertTrue(split.isEnabled())

    def test_split_cuts_the_step_at_the_cursor_with_no_selection_and_is_one_undo_step(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        plan, edit = self.m.experiment_plan, self._edit()
        self.m.selection.set_cube(1)
        self._bar().clear_selection()
        edit.split_button().click()
        steps = plan.steps()
        cube1 = self.m.dataset.cube_start_unix_ms(1)
        self.assertEqual([s.is_added for s in steps], [False, True, False])
        self.assertEqual((steps[0].end_unix_ms, steps[1].start_unix_ms), (cube1, cube1))
        self.assertEqual(steps[0].label, steps[1].label)  # the same solution goes on
        self.assertEqual(steps[0].label, "H2O_--_ch1F")
        self.assertEqual(self._bar().selected_indices(), (1,))  # the new part is selected, ready to be dragged
        self.assertEqual(self._bar().steps()[1].text, "H2O_--_ch1F")  # and drawn with the note
        undo_manager.undo()
        self.assertEqual(len(plan.steps()), 2)
        self.assertEqual(plan.steps()[0].end_unix_ms - plan.steps()[0].start_unix_ms, 3 * CUBE_MS)

    def test_split_on_a_first_cube_says_so_and_changes_nothing(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        notices: list[str] = []
        self.m.experiment_plan.notice.connect(notices.append)
        history = undo_manager.undo_label
        self.m.selection.set_cube(0)
        self.panel._steps.actions.split_at_cursor()  # the button is off here; the command refuses by itself as well
        self.assertEqual(len(self.m.experiment_plan.steps()), 2)
        self.assertEqual(undo_manager.undo_label, history)
        self.assertEqual(len(notices), 1)
        self.assertIn("split", notices[0])

    def test_the_slider_moves_the_cursor_and_the_new_step_with_it(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        plan = self.m.experiment_plan
        self.m.selection.set_cube(2)
        self._edit().add_cursor_button().click()
        self.assertEqual(plan.steps()[1].start_unix_ms, self.m.dataset.cube_start_unix_ms(2))

    def test_restore_brings_back_removed_steps_even_when_every_step_was_removed(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        plan, edit = self.m.experiment_plan, self._edit()
        self.assertFalse(edit.restore_button().isEnabled())  # nothing removed yet
        self._bar().select_many((0, 1), anchor=0)
        edit.remove_button().click()  # both steps: nothing is left on the bar to select
        self.assertEqual(len(plan.steps()), 0)
        self.assertFalse(edit.is_available())
        self.assertEqual(self._edit_enabled(), (False,) * 5)
        self.assertTrue(edit.restore_button().isEnabled())  # the way back is still there
        edit.restore_button().click()
        self.assertEqual([s.order for s in plan.steps()], [1, 2])
        self.assertTrue(edit.is_available())
        self.assertFalse(edit.restore_button().isEnabled())  # nothing removed any more
        undo_manager.undo()  # one undo step: the restore is undone, the steps are removed again
        self.assertEqual(len(plan.steps()), 0)
        self.assertTrue(edit.restore_button().isEnabled())

    def test_the_edit_buttons_do_nothing_for_a_selection_they_cannot_use(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        actions = self.panel._steps.actions
        history = undo_manager.undo_label  # the ROIs the test added are the last entries; nothing below may add one
        self._bar().clear_selection()
        actions.add_at_selection("after")
        actions.remove_selected()
        actions.merge_selected()
        actions.restore_removed()  # nothing was removed
        self._bar().select(0)
        actions.merge_selected()  # one step cannot be merged
        self._bar().select_many((0, 1), anchor=0)
        actions.add_at_selection("after")  # add next to a step needs exactly one
        self.assertEqual(len(self.m.experiment_plan.steps()), 2)
        self.assertEqual(undo_manager.undo_label, history)

    def test_when_the_traces_go_the_bar_the_selection_and_the_cursor_go_and_the_buttons_follow(self) -> None:
        self.m.cube_display.set_mode(MODE_CUBE)
        self._open_scan_and_draw()
        self._bar().select(0)
        self.assertEqual(self._edit_enabled(), (True, True, True, True, False))
        self.m.selection.set_roi_selection(set())  # nothing drawn: the bar goes, a step that is not on the bar is not selected, and the cursor is not on the plot
        self.assertTrue(_pump(lambda: not self._bar().is_shown()))
        self.assertEqual(self._bar().selected_indices(), ())
        self.assertEqual(self._edit_enabled(), (False,) * 5)
        self.assertFalse(self._edit().split_button().isEnabled())  # no cursor on the plot either
        self.assertTrue(self._edit().is_available())  # the dataset still has steps

    def test_a_dataset_without_steps_greys_the_edit_group_out(self) -> None:
        self._open_scan_and_draw()
        self._bar().select(0)
        self.assertTrue(self._edit().is_available())
        dataset = self.m.dataset._dataset
        dataset.acquisition_metadata = dataset.acquisition_metadata.model_copy(update={"plan": None})
        self.m.experiment_plan.steps_changed.emit("dataset")
        self.assertFalse(self._edit().is_available())
        self.assertEqual(self._edit_enabled(), (False,) * 5)

    def test_without_the_plan_module_the_edit_group_is_greyed_out(self) -> None:
        panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask,
        )
        self.assertFalse(panel._steps.edit_controls.is_available())
        panel.shutdown()
        panel.deleteLater()

    def test_without_the_plan_module_the_group_is_greyed_out(self) -> None:
        panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask,
        )
        self.assertFalse(panel._steps.plan_controls.is_available())
        self.assertFalse(panel._plot.plan_bar().is_shown())
        panel.shutdown()
        panel.deleteLater()

    def test_a_dataset_without_steps_greys_the_group_out_and_clearing_hides_the_bar(self) -> None:
        self._open_scan_and_draw()
        self.assertTrue(self.panel._steps.plan_controls.is_available())
        self.m.selection.set_roi_selection(set())  # nothing drawn: no axis to put the steps on
        self.assertTrue(_pump(lambda: not self._bar().is_shown()))
        dataset = self.m.dataset._dataset
        dataset.acquisition_metadata = dataset.acquisition_metadata.model_copy(update={"plan": None})
        self.m.experiment_plan.steps_changed.emit("dataset")
        self.assertFalse(self.panel._steps.plan_controls.is_available())


if __name__ == "__main__":
    unittest.main()
