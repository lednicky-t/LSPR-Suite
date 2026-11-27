"""Axis breaks on the Sensorgram plot (2026-11-27): the x axis with a small gap where a stretch is left out.

What is pinned:
- **what is drawn**: a point inside a break is not drawn, every other x is at its drawn place, and every per-point array (the band, the out-of-date flags, the mask marks) loses the same points;
  the plan bar, the step table, the cursor and the baseline window are placed through the same map; with the switch off or while editing the axis is continuous again, with the breaks kept;
- **what is reported**: a click, the cube circle, the baseline window and the view are always in data x (the map is exact);
- **the axis**: ticks only where data are drawn, labelled with the data's values, the `//` gap marks, handles under the gaps and none in a figure;
- **the gestures**: a handle moved is laid out on release and kept inside the data and between its neighbours; the edit mode adds, edits and removes breaks and refuses to start during the anchor edit;
- **the session**: breaks are saved with the options of the x quantity shown, survive a typed tick step, and are cleared by Reset;
- **no number changes**: the lines given to the plot are not modified.
A real `QApplication` in-process (offscreen); no dataset files.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.domain.axis_breaks import AxisBreak
    from lspr_imaging_app.domain.plot_axes import AxisOptions, PlotAxes
    from lspr_imaging_app.panels.sensorgram.axis_controls import AxisControls
    from lspr_imaging_app.panels.sensorgram.broken_axis import BrokenAxisItem
    from lspr_imaging_app.panels.svg_layers import write_layered_svg
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.plan.bar_model import PlanBarSettings, PlanBarStep
    from lspr_imaging_app.plan.stats_table import StatsColumn, StatsTable, StatsTableSettings
    from lspr_imaging_app.selection.plot_axes_module import PlotAxesModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

X = np.arange(100.0)
BREAK = AxisBreak(30.0, 60.0)  # cubes 31..59 are inside it; 30 and 60 are its ends and stay drawn


def _series() -> Series:
    y = 600.0 + 0.1 * X
    return Series("roi:1", "ROI 1", "#ffffff", X.copy(), y, band_low=y - 1.0, band_high=y + 1.0, baseline_curve=y - 0.5)


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._widgets: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for widget in self._widgets:
            widget.close()
            widget.deleteLater()
        _APP.processEvents()

    def _plot(self, breaks=(BREAK,), series=None, **show) -> SensorgramPlot:
        plot = SensorgramPlot()
        self._widgets.append(plot)
        plot.resize(900, 600)
        plot.show()
        _APP.processEvents()
        plot.set_axes(PlotAxes(x=AxisOptions(breaks=tuple(breaks))))
        plot.show_series([series or _series()], **show)
        return plot

    @staticmethod
    def _line(plot: SensorgramPlot):
        part = next(p for p in plot._svg_parts if p.layer == "Lines")
        return part.item.getData()


class DrawnLayoutTest(_Case):
    def test_points_inside_a_break_are_not_drawn_and_the_rest_are_at_their_drawn_place(self) -> None:
        plot = self._plot()
        bm = plot._breaks
        self.assertTrue(bm.active)
        x, y = self._line(plot)
        self.assertEqual(len(x), 100 - 29)  # 31..59 left out; the two ends stay
        data_x = bm.inverse(np.asarray(x))
        np.testing.assert_allclose(data_x, X[~bm.inside(X)], atol=1e-9)
        np.testing.assert_allclose(y, (600.0 + 0.1 * X)[~bm.inside(X)])  # y travels with its x: nothing is shifted or resampled
        self.assertTrue(np.all(np.diff(x) > 0))
        self.assertAlmostEqual(float(x[list(data_x).index(60.0)] - x[list(data_x).index(30.0)]), bm.gap)  # the two ends of the break are a gap apart

    def test_the_lines_given_are_not_modified(self) -> None:
        series = _series()
        before = (series.x.copy(), series.y.copy(), series.band_low.copy())
        self._plot(series=series)
        np.testing.assert_array_equal(series.x, before[0])
        np.testing.assert_array_equal(series.y, before[1])
        np.testing.assert_array_equal(series.band_low, before[2])

    def test_every_per_point_array_loses_the_same_points(self) -> None:
        stale = np.zeros(100, dtype=bool)
        stale[[10, 40, 80]] = True  # 40 is inside the break
        persistent = np.zeros(100, dtype=bool)
        persistent[[20, 45, 90]] = True
        plot = self._plot(stale=stale, mask_marks={"roi:1": (persistent, np.zeros(100, dtype=bool))}, baseline_curves=True)
        parts = {p.layer: p for p in plot._svg_parts}
        stale_x = parts["Out-of-date marks"].item.getData()[0]
        mark_x = parts["Mask marks"].item.getData()[0]
        self.assertEqual(sorted(plot._breaks.inverse(np.asarray(stale_x)).round().tolist()), [10, 80])
        self.assertEqual(sorted(plot._breaks.inverse(np.asarray(mark_x)).round().tolist()), [20, 90])
        self.assertEqual(len(parts["Baseline curves"].item.getData()[0]), 71)
        band = [p for p in plot._svg_parts if p.layer == "Bands" and "edges" in p.name][0].item.getData()
        self.assertEqual(len(band[0]), 71)

    def test_the_switch_and_the_edit_mode_draw_the_axis_continuous_and_keep_the_breaks(self) -> None:
        plot = self._plot()
        plot.set_breaks_visible(False)
        self.assertFalse(plot.breaks_active())
        self.assertEqual(len(self._line(plot)[0]), 100)
        self.assertEqual(plot.breaks(), (BREAK,))  # kept
        plot.set_breaks_visible(True)
        self.assertEqual(len(self._line(plot)[0]), 71)
        self.assertTrue(plot.set_break_edit(True))
        self.assertFalse(plot.breaks_active())
        self.assertEqual(len(self._line(plot)[0]), 100)
        self.assertEqual(len(plot._regions.regions()), 1)  # the break as a shaded span
        plot.set_break_edit(False)
        self.assertTrue(plot.breaks_active())
        self.assertEqual(len(plot._regions.regions()), 0)

    def test_the_plan_bar_the_table_the_cursor_and_the_window_follow_the_map(self) -> None:
        plot = self._plot()
        bm = plot._breaks
        step = PlanBarStep(0, 10.0, 70.0, "S1", "#4e79a7", "", None)
        plot.set_plan([step], PlanBarSettings())
        drawn = plot.plan_bar().steps()[0]
        self.assertAlmostEqual(drawn.x_start, bm.forward(10.0))
        self.assertAlmostEqual(drawn.x_end, bm.forward(70.0))
        self.assertLess(drawn.x_end - drawn.x_start, 60.0)  # the step spans the break: shorter on screen
        table = StatsTable(columns=(StatsColumn(0, 10.0, 70.0, "S1"),), lines=(), decimals=1, unit="nm")
        plot.set_stats_table(table, StatsTableSettings())
        self.assertAlmostEqual(plot.stats_item().table().columns[0].x_end, bm.forward(70.0))
        plot.set_current_x(80.0)
        self.assertAlmostEqual(plot._axis_cursor.x(), bm.forward(80.0))
        plot.set_current_x(45.0)  # a cube inside the break has no place on the axis
        self.assertIsNone(plot._axis_cursor.x())
        plot.set_baseline_window((10.0, 80.0))
        low, high = plot._window.getRegion()
        self.assertAlmostEqual(low, bm.forward(10.0))
        self.assertAlmostEqual(high, bm.forward(80.0))
        got = plot.baseline_window()
        self.assertAlmostEqual(got[0], 10.0)
        self.assertAlmostEqual(got[1], 80.0)
        plot.set_breaks_visible(False)  # everything back at its data place
        self.assertAlmostEqual(plot.plan_bar().steps()[0].x_end, 70.0)
        self.assertAlmostEqual(plot._axis_cursor.x(), 45.0)
        self.assertEqual(plot._window.getRegion(), (10.0, 80.0))


class ReportedInDataUnitsTest(_Case):
    def test_clicks_the_circle_and_the_view_are_in_data_x(self) -> None:
        plot = self._plot()
        bm = plot._breaks
        told: list[float] = []
        plot.x_clicked.connect(told.append)
        plot._axis_cursor.dragged.emit(bm.forward(80.0))
        plot._axis_cursor.dragged.emit(bm.forward(10.0))
        self.assertAlmostEqual(told[0], 80.0)
        self.assertAlmostEqual(told[1], 10.0)
        (x_low, x_high), _y = plot.visible_range()
        self.assertLessEqual(x_low, 0.0 + 1e-6)
        self.assertGreaterEqual(x_high, 99.0 - 1e-6)  # the whole data, in data x (the drawn range is shorter)
        self.assertLess(plot.plot_item().getViewBox().viewRange()[0][1], 99.0)

    def test_a_pinned_x_range_is_in_data_units(self) -> None:
        plot = self._plot()
        plot.set_axes(PlotAxes(x=AxisOptions(low=10.0, high=80.0, breaks=(BREAK,))))
        (x_low, x_high), _y = plot.visible_range()
        self.assertAlmostEqual(x_low, 10.0)
        self.assertAlmostEqual(x_high, 80.0)


class AxisItemTest(_Case):
    def test_ticks_stand_only_on_drawn_data_and_are_labelled_with_data_values(self) -> None:
        plot = self._plot()
        axis = plot.plot_item().getAxis("bottom")
        self.assertIsInstance(axis, BrokenAxisItem)
        bm = plot._breaks
        low, high = plot.plot_item().getViewBox().viewRange()[0]
        levels = axis.tickValues(low, high, 800.0)
        self.assertTrue(levels)
        drawn = [v for _spacing, values in levels for v in values]
        data = bm.inverse(np.asarray(drawn))
        self.assertFalse(np.any(bm.inside(data)))  # no tick inside the removed stretch
        self.assertTrue(np.any(data < 30.0) and np.any(data > 60.0))
        strings = axis.tickStrings(drawn[:3], 1.0, levels[0][0])
        self.assertEqual(strings, axis.tickStrings(list(bm.forward(bm.inverse(np.asarray(drawn[:3])))), 1.0, levels[0][0]))
        self.assertEqual([float(t) for t in strings], [round(float(v), 6) for v in bm.inverse(np.asarray(drawn[:3]))])  # data values, not drawn numbers

    def test_a_tick_step_of_the_plot_menu_holds_in_every_stretch(self) -> None:
        plot = self._plot()
        plot.set_axes(PlotAxes(x=AxisOptions(major=10.0, breaks=(BREAK,))))
        axis = plot.plot_item().getAxis("bottom")
        low, high = plot.plot_item().getViewBox().viewRange()[0]
        values = [v for _s, vs in axis.tickValues(low, high, 800.0) for v in vs]
        data = sorted(round(float(v), 6) for v in plot._breaks.inverse(np.asarray(values)))
        self.assertEqual(data, [0.0, 10.0, 20.0, 30.0, 60.0, 70.0, 80.0, 90.0])  # 30 and 60 are the ends of the break: both drawn, at the two sides of the gap

    def test_no_break_leaves_the_axis_as_pyqtgraph_makes_it(self) -> None:
        plot = self._plot(breaks=())
        axis = plot.plot_item().getAxis("bottom")
        low, high = plot.plot_item().getViewBox().viewRange()[0]
        self.assertEqual(axis.tickValues(low, high, 800.0), pg_ticks(axis, low, high))
        self.assertEqual(axis.handles(), [])

    def test_a_grab_area_covers_each_gap_draws_nothing_at_rest_and_never_draws_in_a_figure(self) -> None:
        plot = self._plot(breaks=(BREAK, AxisBreak(70.0, 80.0)))
        axis = plot.plot_item().getAxis("bottom")
        handles = axis.handles()
        self.assertEqual(len(handles), 2)
        for handle, span in zip(handles, plot._breaks.gaps()):
            self.assertEqual(handle._span, span)  # it covers the gap: the break mark itself is what is grabbed
            self.assertGreaterEqual(handle.boundingRect().width(), 16.0)  # also a narrow gap is easy to catch
        painted: list[int] = []

        class _Painter:
            def __getattr__(self, name):
                painted.append(1)
                return lambda *a, **k: None

        handles[0].paint(_Painter(), None)
        self.assertEqual(painted, [], "nothing is drawn at rest: the handle is gone, only the break mark of the axis shows")
        handles[0]._hover = True
        handles[0].paint(_Painter(), None)
        self.assertTrue(painted)  # under the pointer the marks show in the accent colour
        painted.clear()
        handles[0]._exportOpts = {"painter": None}  # what pyqtgraph sets on every item while a figure is written
        handles[0].paint(_Painter(), None)
        self.assertEqual(painted, [])
        plot.set_breaks_visible(False)
        self.assertEqual(axis.handles(), [])


def pg_ticks(axis, low, high):
    import pyqtgraph as pg

    return pg.AxisItem.tickValues(axis, low, high, 800.0)


class GestureTest(_Case):
    def test_a_moved_handle_is_laid_out_on_release_and_kept_between_its_neighbours(self) -> None:
        plot = self._plot(breaks=(BREAK, AxisBreak(70.0, 80.0)))
        told: list[tuple] = []
        plot.breaks_changed.connect(told.append)
        plot._on_break_dragged(0, 5.0)
        self.assertEqual(told, [])  # nothing is laid out while the mouse is down
        plot._on_break_drag_finished(0)
        self.assertEqual(told[-1], (AxisBreak(35.0, 65.0), AxisBreak(70.0, 80.0)))  # moved by what was dragged, its length kept
        plot._on_break_dragged(0, 12.0)  # further than the room to the neighbour
        plot._on_break_drag_finished(0)
        first, second = told[-1]
        self.assertAlmostEqual(first.end - first.start, 30.0)
        self.assertLess(first.end, second.start)  # stops short of the neighbour: no merge, no crossing
        self.assertGreater(first.start, 35.0)
        plot._on_break_dragged(1, 500.0)
        plot._on_break_drag_finished(1)
        last = told[-1][-1]
        self.assertLess(last.end, 99.0)  # kept inside the data
        self.assertAlmostEqual(last.end - last.start, 10.0)
        before = len(told)
        plot._on_break_dragged(0, 0.0)  # a click on the handle without a move: nothing to report
        plot._on_break_drag_finished(0)
        self.assertEqual(len(told), before)

    def test_the_edit_mode_adds_edits_and_removes_breaks(self) -> None:
        plot = self._plot(breaks=())
        told: list[tuple] = []
        plot.breaks_changed.connect(told.append)
        plot.set_break_edit(True)
        plot._add_break_at(50.0)  # what a click on the plot does in the edit mode
        self.assertEqual(len(told[-1]), 1)
        added = told[-1][0]
        self.assertAlmostEqual((added.start + added.end) / 2.0, 50.0)
        self.assertAlmostEqual(added.end - added.start, 0.05 * 99.0)
        plot.set_axes(PlotAxes(x=AxisOptions(breaks=told[-1])))  # the owner keeps it and sets it back
        self.assertEqual(len(plot._regions.regions()), 1)
        region = plot._regions.regions()[0]
        region.setRegion((40.0, 55.0))
        region.sigRegionChangeFinished.emit(region)
        self.assertEqual(told[-1], (AxisBreak(40.0, 55.0),))
        plot._regions._remove(region)
        self.assertEqual(told[-1], ())
        plot._add_break_at(2.0)  # near the start: kept inside the data, never reaching it
        self.assertGreater(told[-1][0].start, 0.0)

    def test_the_edit_mode_and_the_anchor_edit_never_run_together(self) -> None:
        plot = self._plot()
        plot.set_anchor_mode(True)
        self.assertFalse(plot.set_break_edit(True))  # refused: one edit mode at a time
        self.assertFalse(plot.break_edit())
        plot.set_anchor_mode(False)
        self.assertTrue(plot.set_break_edit(True))
        ended: list[bool] = []
        plot.break_edit_changed.connect(ended.append)
        plot.set_anchor_mode(True)  # starting the anchor edit ends the break edit and tells the toggle
        self.assertFalse(plot.break_edit())
        self.assertEqual(ended, [False])
        self.assertTrue(plot.plan_bar().is_locked())

    def test_a_click_in_the_edit_mode_adds_a_break_and_does_not_move_the_cube(self) -> None:
        plot = self._plot(breaks=())
        moved, added = [], []
        plot.x_clicked.connect(moved.append)
        plot.breaks_changed.connect(added.append)
        plot.set_break_edit(True)
        view_box = plot.plot_item().getViewBox()
        scene_point = view_box.mapViewToScene(__import__("PyQt6.QtCore", fromlist=["QPointF"]).QPointF(50.0, 600.0))

        class _Event:
            def button(self):
                from PyQt6.QtCore import Qt

                return Qt.MouseButton.LeftButton

            def double(self):
                return False

            def isAccepted(self):
                return False

            def scenePos(self):
                return scene_point

        plot._on_scene_clicked(_Event())
        self.assertEqual(moved, [])
        self.assertEqual(len(added), 1)


class FigureTest(_Case):
    def test_a_layered_svg_is_written_with_the_broken_axis_and_without_the_editing_aids(self) -> None:
        for edit in (False, True):
            plot = self._plot(breaks=(BREAK, AxisBreak(70.0, 80.0)))
            plot.set_break_edit(edit)  # the spans of the edit mode are not part of a figure either
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                path = Path(tmp) / "figure.svg"
                write_layered_svg(plot.plot_item(), path, plot.export_parts(), "t")
                root = ET.fromstring(path.read_text(encoding="utf-8"))
            names = {el.get("{http://www.inkscape.org/namespaces/inkscape}label") for el in root.iter() if el.get("{http://www.inkscape.org/namespaces/inkscape}groupmode") == "layer"}
            self.assertIn("Axes", names, names)  # the axis (with its // marks) is a layer of its own
            self.assertIn("Lines", names, names)
            plot.set_break_edit(False)


class SessionTest(_Case):
    def _controls(self, module):
        parent = QtWidgets.QWidget()
        self._widgets.append(parent)
        labels = ("Cube (#)", "Peak position (nm)")
        return AxisControls(parent, module, "sensorgram", lambda: labels, lambda: ((0.0, 1.0), (0.0, 1.0)), object_prefix="t", breaks=True)

    def test_breaks_are_saved_per_quantity_survive_a_typed_step_and_reset_clears_them(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        told: list[int] = []
        controls.axes_changed.connect(lambda: told.append(1))
        controls.set_x_breaks((BREAK,))
        self.assertEqual(controls.axes().x.breaks, (BREAK,))
        self.assertEqual(told, [1])
        self.assertEqual(controls.break_count.text(), "1 break")
        box = controls._sides["x"].major
        box.setText("10")
        box.editingFinished.emit()
        self.assertEqual(controls.axes().x, AxisOptions(major=10.0, breaks=(BREAK,)))  # a typed step keeps the breaks
        block = module.state()
        self.assertEqual(block["sensorgram"]["axes"][0]["breaks"], [{"start": 30.0, "end": 60.0}])
        other = PlotAxesModule()
        other.restore_state(block)
        self.assertEqual(other.axes("sensorgram", "Cube (#)", "Peak position (nm)").x.breaks, (BREAK,))
        self.assertEqual(other.axes("sensorgram", "Time (min)", "Peak position (nm)").x.breaks, ())  # time has its own
        controls.clear_breaks.click()
        self.assertEqual(controls.axes().x, AxisOptions(major=10.0))
        controls.set_x_breaks((BREAK,))
        controls.reset()
        self.assertTrue(controls.axes().is_default())

    def test_the_edit_toggle_reports_only_the_users_clicks(self) -> None:
        controls = self._controls(PlotAxesModule())
        asked: list[bool] = []
        controls.break_edit_requested.connect(asked.append)
        controls.edit_breaks.setChecked(True)
        self.assertEqual(asked, [True])
        controls.set_break_edit_state(False)  # the plot ended it: shown, not reported
        self.assertFalse(controls.edit_breaks.isChecked())
        self.assertEqual(asked, [True])


if __name__ == "__main__":
    unittest.main()
