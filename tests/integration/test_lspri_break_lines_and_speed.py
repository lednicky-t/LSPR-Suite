"""Axis breaks, 2026-11-27 (second round): lines that end at a break, the grab-the-mark drag, and the faster redraw.

What is pinned:
- **the option** (`AxisOptions.join_lines`): saved only when "Break" is chosen, read back leniently, a row of the Plot menu that keeps the other options (and the guides) when a step is typed;
- **what a plot draws**: with "Continue" (the default) a line runs across the gap; with "Break" it ends at the last point before the break and starts at the first after it, and so do the
  baseline curve and the band; the smoothed line is smoothed piece by piece; a break between two points with none inside it cuts too; switching the option leaves the zoom alone;
- **the drag**: the mark has no handle of its own, a ghost shows the new place while it is dragged and nothing is left after it;
- **the speed-ups change no picture**: the work shared between lines of one x gives the arrays the line-by-line way gave, a tick step or the grid no longer ends the user's zoom, and a
  change of the breaks ranges the view once.
A real `QApplication` in-process (offscreen); no dataset files.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np
from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from dataclasses import replace

    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.domain.axis_breaks import AxisBreak
    from lspr_imaging_app.domain.guide_lines import Guide
    from lspr_imaging_app.domain.plot_axes import AxisOptions, PlotAxes
    from lspr_imaging_app.panels.sensorgram.axis_controls import AxisControls
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot, _finite_runs, _split_runs
    from lspr_imaging_app.panels.sensorgram.view_settings import LINE_SPLINE, SensorgramViewSettings
    from lspr_imaging_app.selection.plot_axes_module import PlotAxesModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

X = np.arange(100.0)
BREAK = AxisBreak(30.0, 60.0)


def _series(x=X, **extra) -> Series:
    y = 600.0 + 0.1 * x
    return Series("roi:1", "ROI 1", "#ffffff", x.copy(), y, **extra)


class OptionTest(unittest.TestCase):
    def test_it_is_saved_only_when_lines_are_broken_and_read_back(self) -> None:
        self.assertEqual(AxisOptions().to_payload(), {})
        options = AxisOptions(join_lines=False, breaks=(BREAK,))
        self.assertEqual(options.to_payload()["join"], False)
        self.assertEqual(AxisOptions.from_payload(options.to_payload()), options)
        self.assertTrue(AxisOptions.from_payload({"join": "no"}).join_lines)  # only an explicit false counts
        self.assertFalse(AxisOptions(join_lines=False).is_default())

    def test_what_needs_a_new_range_or_a_redraw(self) -> None:
        base = PlotAxes(x=AxisOptions(major=10.0))
        self.assertFalse(base.needs_rerange(PlotAxes(x=AxisOptions(major=20.0, minor=5.0, grid=False))))  # ticks and grid do not move the view
        self.assertFalse(base.needs_rerange(PlotAxes(x=AxisOptions(major=10.0, join_lines=False, breaks=(BREAK,)))))
        self.assertTrue(base.needs_rerange(PlotAxes(x=AxisOptions(major=10.0, low=5.0))))
        self.assertTrue(base.needs_rerange(PlotAxes(x=AxisOptions(major=10.0), y=AxisOptions(high=700.0))))
        self.assertFalse(base.needs_rerange(PlotAxes(x=AxisOptions(major=10.0, zero=3.0))))  # a zero matters only to a typed x range
        ranged = PlotAxes(x=AxisOptions(low=0.0, high=50.0))
        self.assertTrue(ranged.needs_rerange(PlotAxes(x=AxisOptions(low=0.0, high=50.0, zero=3.0))))
        self.assertFalse(base.needs_redraw(PlotAxes(x=AxisOptions(major=20.0), grid_alpha=0.5)))  # a window applies a new step value at once
        self.assertTrue(base.needs_redraw(PlotAxes(x=AxisOptions(major=None))))  # LbL's cycle labels come back: a redraw
        self.assertTrue(base.needs_redraw(PlotAxes(x=AxisOptions(major=10.0, low=1.0))))


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._widgets: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for widget in self._widgets:
            widget.close()
            widget.deleteLater()
        _APP.processEvents()

    def _own(self, widget):
        self._widgets.append(widget)
        return widget

    def _plot(self, *, join=True, breaks=(BREAK,), series=None, spline=False, **show) -> SensorgramPlot:
        plot = self._own(SensorgramPlot())
        plot.resize(900, 600)
        plot.show()
        _APP.processEvents()
        if spline:
            plot.set_view(replace(SensorgramViewSettings(), line_style=LINE_SPLINE))
        plot.set_axes(PlotAxes(x=AxisOptions(join_lines=join, breaks=tuple(breaks))))
        plot.show_series(series or [_series()], **show)
        return plot

    @staticmethod
    def _parts(plot: SensorgramPlot, layer: str):
        return [p for p in plot._svg_parts if p.layer == layer]


class LinesAtBreaksTest(_Case):
    def test_continue_runs_across_the_gap_and_break_ends_the_line_before_it(self) -> None:
        x, y = self._parts(self._plot(join=True), "Lines")[0].item.getData()
        self.assertFalse(np.isnan(y).any())
        plot = self._plot(join=False)
        x, y = self._parts(plot, "Lines")[0].item.getData()
        gap = np.flatnonzero(np.isnan(y))
        self.assertEqual(len(gap), 1)  # one cut for one break
        before, after = x[gap[0] - 1], x[gap[0] + 1]
        bm = plot._breaks
        self.assertAlmostEqual(float(bm.inverse(before)), 30.0)  # the line ends at the last point before the break ...
        self.assertAlmostEqual(float(bm.inverse(after)), 60.0)  # ... and starts at the first after it
        self.assertEqual(int(np.isfinite(y).sum()), 100 - 29)  # no point was lost, only the 29 inside the break

    def test_every_break_cuts_once_and_a_break_between_two_points_cuts_too(self) -> None:
        plot = self._plot(join=False, breaks=(BREAK, AxisBreak(70.0, 80.0), AxisBreak(90.2, 90.8)))
        _x, y = self._parts(plot, "Lines")[0].item.getData()
        self.assertEqual(int(np.isnan(y).sum()), 3)  # the third lies between the points 90 and 91: nothing inside it, the line still ends

    def test_a_line_that_does_not_reach_the_break_is_not_cut(self) -> None:
        short = _series(np.arange(0.0, 20.0))
        plot = self._plot(join=False, series=[short])
        _x, y = self._parts(plot, "Lines")[0].item.getData()
        self.assertFalse(np.isnan(y).any())

    def test_the_baseline_curve_and_the_band_end_too(self) -> None:
        extras = dict(band_low=600.0 + 0.1 * X - 1.0, band_high=600.0 + 0.1 * X + 1.0, baseline_curve=600.0 + 0.1 * X - 0.5)
        joined = self._plot(join=True, series=[_series(**extras)], baseline_curves=True)
        broken = self._plot(join=False, series=[_series(**extras)], baseline_curves=True)
        _bx, by = self._parts(broken, "Baseline curves")[0].item.getData()
        self.assertEqual(int(np.isnan(by).sum()), 1)
        self.assertEqual(len([p for p in joined._svg_parts if p.layer == "Bands" and "(edges)" not in p.name]), 1)
        self.assertEqual(len([p for p in broken._svg_parts if p.layer == "Bands" and "(edges)" not in p.name]), 2)  # one band each side of the break

    def test_a_smoothed_line_is_smoothed_piece_by_piece(self) -> None:
        plot = self._plot(join=False, spline=True)
        x, y = self._parts(plot, "Lines")[0].item.getData()
        self.assertEqual(int(np.isnan(y).sum()), 1)
        gap = int(np.flatnonzero(np.isnan(y))[0])
        bm = plot._breaks
        self.assertAlmostEqual(float(bm.inverse(x[gap - 1])), 30.0)
        self.assertAlmostEqual(float(bm.inverse(x[gap + 1])), 60.0)
        self.assertGreater(len(x), 2 * 100)  # and still denser than the measured points

    def test_switching_the_option_leaves_the_zoom_and_the_ranges_alone(self) -> None:
        plot = self._plot(join=True)
        view_box = plot.plot_item().getViewBox()
        view_box.setXRange(5.0, 20.0, padding=0.0)
        plot._view_kept = True
        ranged = plot._autoscales
        plot.set_axes(PlotAxes(x=AxisOptions(join_lines=False, breaks=(BREAK,))))
        self.assertEqual(plot._autoscales, ranged)
        self.assertAlmostEqual(view_box.viewRange()[0][0], 5.0)
        self.assertTrue(plot.lines_join() is False)
        _x, y = self._parts(plot, "Lines")[0].item.getData()
        self.assertEqual(int(np.isnan(y).sum()), 1)

    def test_without_breaks_the_option_changes_nothing(self) -> None:
        plot = self._plot(join=False, breaks=())
        _x, y = self._parts(plot, "Lines")[0].item.getData()
        self.assertFalse(np.isnan(y).any())


class DragTest(_Case):
    def test_the_mark_is_the_grab_area_and_a_drag_shows_a_ghost_until_it_ends(self) -> None:
        plot = self._plot()
        handle = plot.plot_item().getAxis("bottom").handles()[0]
        rest = handle.boundingRect().height()
        plot.plot_item().getAxis("bottom").handles()[0]._dragging = True
        handle._rise = 300.0  # what the start of a drag sets: the dashed line up through the plot
        self.assertGreater(handle.boundingRect().height(), rest + 250.0)
        handle._dragging, handle._rise = False, 0.0
        self.assertAlmostEqual(handle.boundingRect().height(), rest)  # nothing is left after it


class SpeedupsChangeNoPictureTest(_Case):
    def test_the_work_shared_between_lines_of_one_x_equals_the_line_by_line_way(self) -> None:
        plot = self._plot()
        bm = plot._breaks
        x_nan = X.copy()
        x_nan[10] = np.nan
        lines = [_series(), _series(), _series(x_nan), _series(X[:50])]
        stale = np.zeros(100, dtype=bool)
        stale[40], stale[5] = True, True
        drawn, stale_out, _marks, cuts = plot._through_breaks(lines, stale, None)
        self.assertIsNone(cuts)  # lines joined: nothing is cut
        for s, d in zip(lines, drawn):
            keep = ~np.asarray(bm.inside(s.x), dtype=bool)
            np.testing.assert_array_equal(d.x, bm.forward(s.x[keep]))
            np.testing.assert_array_equal(d.y, s.y[keep])
        self.assertEqual(int(stale_out[0].sum()), 1)  # 40 is inside the break, 5 is not
        self.assertEqual(len(stale_out[3]), 100)  # an array of another length is left as it is

    def test_the_vectorised_runs_equal_the_loop_and_split_at_cuts(self) -> None:
        a = np.array([1.0, 2.0, np.nan, 3.0, 4.0, 5.0, np.nan, 6.0])
        self.assertEqual(_finite_runs(a, a), [(0, 2), (3, 6)])
        self.assertEqual(_finite_runs(np.array([]), np.array([])), [])
        self.assertEqual(_split_runs([(0, 8)], np.array([3])), [(0, 4), (4, 8)])
        self.assertEqual(_split_runs([(0, 3), (4, 8)], np.array([1, 5])), [(0, 2), (4, 6), (6, 8)])  # the first piece left with one point is no band
        self.assertEqual(_split_runs([(0, 4)], np.array([0])), [(1, 4)])  # a piece of one point is no band

    def test_a_tick_step_or_the_grid_keeps_the_users_zoom_and_does_not_range_again(self) -> None:
        plot = self._plot(breaks=())
        view_box = plot.plot_item().getViewBox()
        view_box.setXRange(20.0, 40.0, padding=0.0)
        plot._view_kept = True
        ranged = plot._autoscales
        plot.set_axes(PlotAxes(x=AxisOptions(major=5.0), y=AxisOptions(grid=False)))
        self.assertEqual(plot._autoscales, ranged)
        self.assertAlmostEqual(view_box.viewRange()[0][0], 20.0)
        self.assertAlmostEqual(view_box.viewRange()[0][1], 40.0)
        plot.set_axes(PlotAxes(x=AxisOptions(major=5.0, low=10.0, high=50.0)))  # a typed range is a request for that view
        self.assertAlmostEqual(view_box.viewRange()[0][0], 10.0)
        self.assertGreater(plot._autoscales, ranged)

    def test_a_change_of_the_breaks_ranges_the_view_once(self) -> None:
        plot = self._plot(breaks=())
        ranged = plot._autoscales
        plot.set_axes(PlotAxes(x=AxisOptions(breaks=(BREAK,))))
        self.assertEqual(plot._autoscales - ranged, 1)
        ranged = plot._autoscales
        plot.set_axes(PlotAxes(x=AxisOptions(breaks=(BREAK, AxisBreak(70.0, 80.0)), low=0.0, high=50.0)))  # a range typed at the same time: still once
        self.assertEqual(plot._autoscales - ranged, 1)
        plot.set_breaks_visible(False)
        self.assertGreaterEqual(plot._autoscales, ranged + 2)


class TickDensityTest(_Case):
    """Screenshot of 2026-11-27: with a break every stretch chose its own spacing from its own few pixels, so a narrow stretch had labels running into each other."""

    def _major(self, plot: SensorgramPlot):
        axis = plot.plot_item().getAxis("bottom")
        low, high = plot.plot_item().getViewBox().viewRange()[0]
        levels = axis.tickValues(low, high, 800.0)
        return levels, low, high

    def test_a_break_does_not_change_the_tick_density_and_every_stretch_has_the_same_spacing(self) -> None:
        x = np.arange(0.0, 300.0)
        wide = self._plot(breaks=(), series=[_series(x)])
        narrow = self._plot(breaks=(AxisBreak(40.0, 150.0),), series=[_series(x)])  # a narrow stretch (0-40) and a wide one (150-299)
        plain_levels, _l, _h = self._major(wide)
        levels, low, high = self._major(narrow)
        kept = 40.0 + 149.0
        # as many ticks as a continuous axis of the kept data would have (+ the ones standing at the two ends of the break)
        axis = narrow.plot_item().getAxis("bottom")
        import pyqtgraph as pg

        reference = pg.AxisItem.tickValues(axis, 0.0, kept, 800.0 * (kept / (high - low)))
        for level, (spacing, ticks) in enumerate(levels):
            self.assertEqual(spacing, reference[level][0])  # one spacing for the whole axis
            self.assertLessEqual(len(ticks), len(reference[level][1]) + 3)
        data = sorted(round(float(narrow._breaks.inverse(v)), 6) for v in levels[0][1])
        self.assertTrue(all(v % levels[0][0] == 0.0 for v in data))  # on multiples of that spacing, in the narrow stretch too
        self.assertTrue(any(v < 40.0 for v in data) and any(v > 150.0 for v in data))
        self.assertFalse([v for v in data if 40.0 < v < 150.0])
        self.assertTrue(plain_levels)

    def test_a_zero_keeps_the_density_and_a_tick_on_each_side_of_the_gap(self) -> None:
        x = np.arange(0.0, 300.0)
        plot = self._plot(breaks=(AxisBreak(40.0, 150.0),), series=[_series(x)])
        plot.set_axes(PlotAxes(x=AxisOptions(zero=150.0, breaks=(AxisBreak(40.0, 150.0),))))
        levels, _low, _high = self._major(plot)
        data = sorted(round(float(plot._breaks.inverse(v)), 6) for v in levels[0][1])
        self.assertIn(150.0, data)  # the zero itself is a tick
        self.assertTrue(all((v - 150.0) % levels[0][0] == 0.0 for v in data))


class ControlsTest(_Case):
    def _controls(self, module=None, breaks=True) -> AxisControls:
        parent = self._own(QtWidgets.QWidget())
        labels = ("Cube (#)", "Peak position (nm)")
        return AxisControls(parent, module or PlotAxesModule(), "sensorgram", lambda: labels, lambda: ((0.0, 1.0), (0.0, 1.0)), object_prefix="t", breaks=breaks)

    def test_the_lines_row_sets_the_option_and_shows_it_back(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        self.assertTrue(controls.join_buttons["continue"].isChecked())
        controls.join_buttons["break"].click()
        self.assertFalse(controls.axes().x.join_lines)
        self.assertEqual(module.state()["sensorgram"]["axes"][0]["join"], False)
        other = self._controls(module)
        self.assertTrue(other.join_buttons["break"].isChecked())  # the session's choice is what the row shows
        controls.join_buttons["continue"].click()
        self.assertTrue(controls.axes().x.join_lines)
        self.assertNotIn("join", module.state() or {})

    def test_a_typed_step_keeps_the_choice_the_breaks_and_the_guides(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        controls.set_x_breaks((BREAK,))
        controls.join_buttons["break"].click()
        module.set_axes("sensorgram", "Cube (#)", "Peak position (nm)", replace(controls.axes(), y=AxisOptions(guides=(Guide(650.0, "Base"),))))
        box = controls._sides["x"].major
        box.setText("10")
        box.editingFinished.emit()
        axes = controls.axes()
        self.assertEqual((axes.x.major, axes.x.join_lines, axes.x.breaks), (10.0, False, (BREAK,)))
        self.assertEqual(axes.y.guides, (Guide(650.0, "Base"),))  # a typed step used to lose the guides
        controls.reset()
        self.assertEqual(controls.axes().y.guides, (Guide(650.0, "Base"),))
        self.assertTrue(controls.axes().x.join_lines)  # Reset gives the option back its default, like the breaks

    def test_a_window_has_no_lines_row(self) -> None:
        controls = self._controls(breaks=False)
        self.assertTrue(controls.join_row.isHidden())


class WindowRedrawTest(_Case):
    def _windows(self):
        import math

        from lspr_imaging_app.analysis.step_table import RowNumbers
        from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
        from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep

        refs = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
        numbers = RowNumbers(refs=refs, n=np.full(4, 25), mean=np.array([100.0, 110.0, 125.0, 131.0]), sd=np.full(4, 0.5))
        bulk = self._own(BulkPlotWindow(plot_axes=None))
        bulk.set_steps([PlotStep(refs[i], i + 1, f"s{i}", (1.333, 1.3453, 1.3576, 1.335)[i], math.nan, i < 3) for i in range(4)], 589.3)
        bulk.set_lines([BulkLine("A", "#4e79a7", numbers, short_label="A")], y_label="Peak position (nm)")
        lbl = self._own(LblPlotWindow(plot_axes=None))
        lbl.set_steps([LblStep(refs[i], i + 1, f"s{i}") for i in range(4)])
        lbl.set_lines([BulkLine("A", "#38bdf8", numbers, short_label="A")], y_label="Shift (nm)")
        for window in (bulk, lbl):
            window.show()
        _APP.processEvents()
        return bulk, lbl

    def test_a_tick_value_or_the_grid_does_not_draw_a_window_again_but_a_range_does(self) -> None:
        for window in self._windows():
            name = type(window).__name__
            window.axes._sides["y"].major.setText("20")
            window.axes._sides["y"].major.editingFinished.emit()  # a step set: LbL's labels / the autoscale need a redraw
            window.redraw()
            state = window._state
            window.axes._sides["y"].major.setText("25")
            window.axes._sides["y"].major.editingFinished.emit()  # another value of the step: applied at once
            self.assertIs(window._state, state, name)
            window.axes._sides["y"].grid.setChecked(False)
            self.assertIs(window._state, state, name)
            self.assertEqual(window._plot.getPlotItem().getAxis("left")._tickSpacing, [(25.0, 0.0)], name)
            window.axes._sides["y"].low.setText("0")
            window.axes._sides["y"].low.editingFinished.emit()  # a range: shown after a redraw
            self.assertIsNot(window._state, state, name)


if __name__ == "__main__":
    unittest.main()
