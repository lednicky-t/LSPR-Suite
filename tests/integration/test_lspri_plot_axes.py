"""The Plot group of the Sensorgram's View tab and of the LbL / S_bulk windows: tick spacing, display range, grid (2026-11-27).

What is pinned:
- the rules of `AxisOptions` (what is refused, the tick-count guard, the pinned range) and of the session module (per plot and per quantity an axis shows, reset, restore, damaged blocks);
- the session file (schema 1.17): written only when something is set, read back, an older file opens with every plot automatic;
- the controls: typing, an empty field = auto, a refused value goes back and says why, "Use view", reset, another quantity shows its own options;
- what the three plots do with it: the range replaces the autoscale (also after "A" / View All and a redraw), an unpinned end stays automatic, a tick step reaches the axis, LbL's
  one-tick-per-cycle labels give way to a step, and no number of a plot changes.
A real `QApplication` in-process (offscreen); no dataset files.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
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
    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.domain.plot_axes import MAX_TICKS, AxisOptions, PlotAxes, pinned_range
    from lspr_imaging_app.frame_naming import FrameNamingScheme
    from lspr_imaging_app.panels.sensorgram.axis_controls import AxisControls
    from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
    from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.selection.plot_axes_module import PlotAxesModule
    from lspr_imaging_app.storage.session import SESSION_SCHEMA_VERSION, SessionState, load_session, save_session, session_path
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
RI = (1.33300, 1.34531, 1.35762, 1.33500)
X_LABEL, Y_LABEL = "Cube (#)", "Peak position (nm)"


def _numbers() -> RowNumbers:
    return RowNumbers(refs=REFS, n=np.full(4, 25), mean=np.array([100.0, 110.0, 125.0, 131.0]), sd=np.full(4, 0.5))


class AxisOptionsTest(unittest.TestCase):
    def test_the_defaults_are_automatic_and_a_default_has_an_empty_payload(self) -> None:
        self.assertTrue(AxisOptions().is_default())
        self.assertEqual(AxisOptions().to_payload(), {})
        self.assertTrue(PlotAxes().is_default())

    def test_sanitized_drops_what_makes_no_sense(self) -> None:
        self.assertEqual(AxisOptions(major=-5.0, minor=1.0).sanitized(), AxisOptions())  # a negative step is no step, and a minor step needs a major one
        self.assertIsNone(AxisOptions(major=10.0, minor=10.0).sanitized().minor)  # not below the major step
        self.assertEqual(AxisOptions(low=5.0, high=5.0).sanitized(), AxisOptions())  # no range: both ends are dropped
        self.assertEqual(AxisOptions(low=1.0).sanitized().low, 1.0)  # one end alone is fine
        self.assertIsNone(AxisOptions(major=float("nan")).sanitized().major)

    def test_a_step_that_would_draw_too_many_ticks_is_ignored(self) -> None:
        options = AxisOptions(major=0.001, minor=0.0001)
        self.assertIsNone(options.spacing_for(500.0, 700.0))
        self.assertEqual(AxisOptions(major=10.0).spacing_for(0.0, 100.0), (10.0, None))
        self.assertEqual(AxisOptions(major=10.0, minor=2.0).spacing_for(0.0, 100.0), (10.0, 2.0))
        wide = AxisOptions(major=1.0, minor=1.0 / (MAX_TICKS * 2))  # the minor step alone is too fine: it is left out, the major step stays
        self.assertEqual(wide.sanitized().spacing_for(0.0, 10.0), (1.0, None))

    def test_the_payload_round_trips_and_is_read_leniently(self) -> None:
        options = AxisOptions(major=50.0, minor=10.0, low=0.0, high=400.0, grid=False)
        self.assertEqual(AxisOptions.from_payload(options.to_payload()), options)
        self.assertEqual(AxisOptions.from_payload({"major": "junk", "low": 3, "grid": True}), AxisOptions(low=3.0))
        self.assertEqual(AxisOptions.from_payload("junk"), AxisOptions())

    def test_a_pinned_end_replaces_the_autoscales_and_an_empty_one_stays_automatic(self) -> None:
        self.assertIsNone(pinned_range(AxisOptions(), 0.0, 1.0))
        self.assertEqual(pinned_range(AxisOptions(low=2.0, high=3.0), 0.0, 10.0), (2.0, 3.0))
        self.assertEqual(pinned_range(AxisOptions(high=3.0), -1.0, 10.0), (-1.0, 3.0))
        self.assertEqual(pinned_range(AxisOptions(low=-4.0), -1.0, 10.0), (-4.0, 10.0))
        low, high = pinned_range(AxisOptions(low=20.0), 0.0, 10.0)  # the data end is below the pin: a window above the pin, never an empty range
        self.assertEqual(low, 20.0)
        self.assertGreater(high, low)


class PlotAxesModuleTest(unittest.TestCase):
    def test_options_belong_to_the_plot_and_to_the_quantity_the_axis_shows(self) -> None:
        module = PlotAxesModule()
        module.set_axes("sensorgram", "Cube (#)", Y_LABEL, PlotAxes(x=AxisOptions(major=50.0), y=AxisOptions(low=600.0, high=700.0)))
        self.assertEqual(module.axes("sensorgram", "Cube (#)", Y_LABEL).x.major, 50.0)
        self.assertEqual(module.axes("sensorgram", "Time (min)", Y_LABEL).x, AxisOptions())  # another quantity on x: its own (automatic) options
        self.assertEqual(module.axes("sensorgram", "Cube (#)", "Shift (nm)").y, AxisOptions())
        self.assertEqual(module.axes("lbl", "Cube (#)", Y_LABEL), PlotAxes())  # another plot

    def test_it_emits_only_on_a_change_and_reset_puts_the_quantities_shown_back(self) -> None:
        module = PlotAxesModule()
        seen: list[str] = []
        module.changed.connect(seen.append)
        module.set_axes("bulk", "A", "B", PlotAxes())
        self.assertEqual(seen, [])
        module.set_axes("bulk", "A", "B", PlotAxes(x=AxisOptions(major=1.0), grid_alpha=0.5))
        module.set_axes("bulk", "A", "B", PlotAxes(x=AxisOptions(major=1.0), grid_alpha=0.5))
        self.assertEqual(seen, ["bulk"])
        module.set_axes("bulk", "C", "B", PlotAxes(x=AxisOptions(major=7.0), grid_alpha=0.5))
        module.reset("bulk", "A", "B")
        self.assertEqual(module.axes("bulk", "A", "B"), PlotAxes())
        self.assertEqual(module.axes("bulk", "C", "B").x.major, 7.0)  # the quantity not shown keeps its options
        with self.assertRaises(ValueError):
            module.set_axes("nope", "A", "B", PlotAxes())

    def test_state_and_restore_round_trip_and_a_damaged_block_loads_what_it_can(self) -> None:
        module = PlotAxesModule()
        self.assertIsNone(module.state())
        module.set_axes("sensorgram", X_LABEL, Y_LABEL, PlotAxes(x=AxisOptions(major=50.0, minor=10.0), y=AxisOptions(low=600.0), grid_alpha=0.4))
        block = module.state()
        other = PlotAxesModule()
        seen: list[str] = []
        other.changed.connect(seen.append)
        other.restore_state(json.loads(json.dumps(block)))
        self.assertEqual(other.axes("sensorgram", X_LABEL, Y_LABEL), module.axes("sensorgram", X_LABEL, Y_LABEL))
        self.assertEqual(seen, ["sensorgram"])
        other.restore_state(None)  # a session without the block: every plot automatic again, and told
        self.assertEqual(other.axes("sensorgram", X_LABEL, Y_LABEL), PlotAxes())
        self.assertEqual(seen, ["sensorgram", "sensorgram"])
        other.restore_state({"lbl": "junk", "bulk": {"axes": [{"side": "x", "label": "L", "major": 2}, {"side": "z"}, 5]}})
        self.assertEqual(other.axes("bulk", "L", "").x.major, 2.0)
        self.assertEqual(other.axes("lbl", "L", ""), PlotAxes())
        other.restore_state(["not", "a", "dict"])
        self.assertIsNone(other.state())


class SessionFileTest(unittest.TestCase):
    def test_the_block_is_written_only_when_something_is_set_and_an_older_file_opens_without_it(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root, naming = Path(tmp), FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])
            self.assertEqual(SESSION_SCHEMA_VERSION, "1.17")
            path = save_session(root, SessionState(), naming)
            self.assertNotIn("plot_axes", json.loads(path.read_text(encoding="utf-8")))  # written exactly as before
            self.assertIsNone(load_session(root).plot_axes)
            block = {"sensorgram": {"axes": [{"side": "x", "label": X_LABEL, "major": 50.0}], "grid_alpha": 0.3}}
            save_session(root, SessionState(plot_axes=block), naming)
            self.assertEqual(json.loads(session_path(root).read_text(encoding="utf-8"))["plot_axes"], block)
            self.assertEqual(load_session(root).plot_axes, block)
            payload = json.loads(session_path(root).read_text(encoding="utf-8"))
            payload["schema_version"] = "1.16"
            del payload["plot_axes"]
            session_path(root).write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(load_session(root).plot_axes)


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


class AxisControlsTest(_Case):
    def _controls(self, module: PlotAxesModule | None = None, labels=(X_LABEL, Y_LABEL), view=((0.0, 10.0), (600.0, 700.0))):
        parent = self._own(QtWidgets.QWidget())
        state = {"labels": labels}
        controls = AxisControls(parent, module, "sensorgram", lambda: state["labels"], lambda: view, object_prefix="t_axes")
        controls.state = state  # type: ignore[attr-defined]
        return controls

    @staticmethod
    def _type(box: QtWidgets.QLineEdit, text: str) -> None:
        box.setText(text)
        box.editingFinished.emit()

    def test_typing_reaches_the_module_and_an_empty_field_is_auto(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        seen: list[PlotAxes] = []
        controls.axes_changed.connect(lambda: seen.append(controls.axes()))
        x = controls._sides["x"]
        self._type(x.major, "50")
        self.assertEqual(module.axes("sensorgram", X_LABEL, Y_LABEL).x.major, 50.0)
        self.assertTrue(x.minor.isEnabled())
        self._type(x.minor, "10")
        self._type(controls._sides["y"].low, "600,5")  # a decimal comma is fine
        self.assertEqual(controls.axes().y.low, 600.5)
        self._type(x.major, "")
        self.assertIsNone(controls.axes().x.major)
        self.assertIsNone(controls.axes().x.minor)  # without a major step the minor one goes with it
        self.assertEqual(len(seen), 4)

    def test_a_refused_value_goes_back_and_the_note_says_why(self) -> None:
        controls = self._controls(PlotAxesModule())
        y = controls._sides["y"]
        self._type(y.low, "700")
        self._type(y.high, "600")  # below the minimum
        self.assertIn("minimum must be below", controls.note.text())
        self.assertEqual(y.high.text(), "")
        self.assertEqual(controls.axes().y, AxisOptions(low=700.0))
        self._type(y.major, "abc")
        self.assertIn("not a number", controls.note.text())
        self._type(y.major, "-3")
        self.assertIn("above zero", controls.note.text())
        self.assertIsNone(controls.axes().y.major)
        self._type(controls._sides["x"].major, "5")
        self._type(controls._sides["x"].minor, "9")  # not below the major step
        self.assertIn("below the major step", controls.note.text())
        self.assertIsNone(controls.axes().x.minor)

    def test_use_view_copies_what_is_on_screen_and_reset_puts_automatic_back(self) -> None:
        controls = self._controls(PlotAxesModule(), view=((1.0, 9.5), (610.25, 690.75)))
        controls._sides["y"].use_view.click()
        self.assertEqual((controls.axes().y.low, controls.axes().y.high), (610.25, 690.75))
        self._type(controls._sides["x"].major, "2")
        controls.alpha.setValue(40)
        self.assertEqual(controls.axes().grid_alpha, 0.4)
        controls.reset()
        self.assertTrue(controls.axes().is_default())
        self.assertEqual(controls._sides["y"].low.text(), "")
        self.assertEqual(controls.alpha.value(), 15)

    def test_another_quantity_shows_its_own_options_and_tells_the_plot(self) -> None:
        controls = self._controls(PlotAxesModule())
        self._type(controls._sides["x"].major, "50")
        told: list[int] = []
        controls.axes_changed.connect(lambda: told.append(1))
        controls.state["labels"] = ("Time (min)", Y_LABEL)
        controls.labels_changed()
        self.assertIsNone(controls.axes().x.major)  # time has no step of its own yet
        self.assertEqual(controls._sides["x"].major.text(), "")
        self.assertEqual(told, [1])
        controls.labels_changed()  # nothing new: nothing told
        self.assertEqual(told, [1])
        controls.state["labels"] = (X_LABEL, Y_LABEL)
        controls.labels_changed()
        self.assertEqual(controls._sides["x"].major.text(), "50")  # and back: the cube axis kept its step

    def test_a_restored_session_updates_the_fields_and_the_plot(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        told: list[int] = []
        controls.axes_changed.connect(lambda: told.append(1))
        module.restore_state({"sensorgram": {"axes": [{"side": "y", "label": Y_LABEL, "low": 610.0, "high": 650.0}]}})
        self.assertEqual((controls._sides["y"].low.text(), controls._sides["y"].high.text()), ("610", "650"))
        self.assertEqual(told, [1])


class SensorgramPlotAxesTest(_Case):
    def _plot(self) -> SensorgramPlot:
        plot = self._own(SensorgramPlot())
        plot.resize(900, 600)
        plot.show()
        _APP.processEvents()
        x = np.arange(20.0)
        plot.show_series([Series("roi:1", "ROI 1", "#ffffff", x, 630.0 + 0.5 * x)])
        return plot

    def test_the_range_replaces_the_autoscale_and_comes_back_after_a_full_view(self) -> None:
        plot = self._plot()
        auto_x = plot.visible_range()[0]
        plot.set_axes(PlotAxes(x=AxisOptions(low=2.0, high=8.0), y=AxisOptions(high=700.0)))
        (x_low, x_high), (y_low, y_high) = plot.visible_range()
        self.assertEqual((x_low, x_high, y_high), (2.0, 8.0, 700.0))
        self.assertLess(y_low, 640.0)  # the y minimum is still the autoscale's (an unpinned end stays automatic)
        plot.reset_view()  # the "A" button / View All: the pinned range again, not the full data
        self.assertEqual(plot.visible_range()[0], (2.0, 8.0))
        x = np.arange(20.0)
        plot.show_series([Series("roi:1", "ROI 1", "#ffffff", x, 630.0 + 0.5 * x)])  # a redraw
        self.assertEqual(plot.visible_range()[0], (2.0, 8.0))
        plot.set_axes(PlotAxes())  # un-pinned: the autoscale again
        self.assertEqual(plot.visible_range()[0], auto_x)

    def test_setting_a_range_ends_the_users_own_zoom(self) -> None:
        plot = self._plot()
        plot.plot_item().getViewBox().setXRange(5.0, 6.0, padding=0.0)
        plot._on_view_changed_by_user()
        self.assertTrue(plot.view_is_kept())
        plot.set_axes(PlotAxes(x=AxisOptions(low=0.0, high=10.0)))
        self.assertFalse(plot.view_is_kept())
        self.assertEqual(plot.visible_range()[0], (0.0, 10.0))

    def test_a_tick_step_reaches_the_axis_and_the_guard_ignores_one_that_draws_too_many(self) -> None:
        plot = self._plot()
        axis = plot.plot_item().getAxis("bottom")
        plot.set_axes(PlotAxes(x=AxisOptions(major=5.0, minor=1.0)))
        self.assertEqual(axis._tickSpacing, [(5.0, 0.0), (1.0, 0.0)])
        plot.set_axes(PlotAxes(x=AxisOptions(major=0.0001)))  # 20 / 0.0001 ticks: ignored, automatic ticks
        self.assertIsNone(axis._tickSpacing)
        plot.set_axes(PlotAxes())
        self.assertIsNone(axis._tickSpacing)

    def test_the_grid_follows_the_options_and_no_number_is_touched(self) -> None:
        plot = self._plot()
        before = [(np.array(item.getData()[0]), np.array(item.getData()[1])) for item in plot._items if hasattr(item, "getData") and item.getData()[0] is not None]
        plot.set_axes(PlotAxes(x=AxisOptions(grid=False), grid_alpha=0.5))
        self.assertFalse(plot.plot_item().ctrl.xGridCheck.isChecked())
        self.assertTrue(plot.plot_item().ctrl.yGridCheck.isChecked())
        after = [(np.array(item.getData()[0]), np.array(item.getData()[1])) for item in plot._items if hasattr(item, "getData") and item.getData()[0] is not None]
        self.assertEqual(len(before), len(after))
        for (bx, by), (ax, ay) in zip(before, after):
            np.testing.assert_array_equal(bx, ax)
            np.testing.assert_array_equal(by, ay)

    def test_a_new_quantity_on_an_axis_is_announced_once(self) -> None:
        plot = self._own(SensorgramPlot())
        told: list[int] = []
        plot.axis_labels_changed.connect(lambda: told.append(1))
        plot.set_axis_labels("Time (min)", "Peak position (nm)")
        plot.set_axis_labels("Time (min)", "Peak position (nm)")
        self.assertEqual(told, [1])
        self.assertEqual(plot.axis_labels(), ("Time (min)", "Peak position (nm)"))


class WindowAxesTest(_Case):
    def _bulk(self, module: PlotAxesModule | None = None) -> BulkPlotWindow:
        window = self._own(BulkPlotWindow(plot_axes=module))
        window.set_steps([PlotStep(REFS[i], i + 1, f"s{i}", RI[i], math.nan, i < 3) for i in range(4)], 589.3)
        window.set_lines([BulkLine("Group A (n=12)", "#4e79a7", _numbers(), short_label="Group A")], y_label="Peak position (nm)")
        window.show()
        _APP.processEvents()
        return window

    def _lbl(self, module: PlotAxesModule | None = None) -> LblPlotWindow:
        window = self._own(LblPlotWindow(plot_axes=module))
        window.set_steps([LblStep(REFS[i], i + 1, f"s{i}") for i in range(4)])
        window.set_lines([BulkLine("Group A (n=12)", "#38bdf8", _numbers(), short_label="Group A")], y_label="Δλ (nm)")
        window.show()
        _APP.processEvents()
        return window

    def test_both_windows_have_the_plot_menu_and_it_is_in_their_ribbon(self) -> None:
        for window in (self._bulk(), self._lbl()):
            self.assertTrue(window.isAncestorOf(window.axes.menu))
            self.assertEqual(window.axes.menu.toolTip()[:5], "Plot:")

    def test_the_range_is_shown_after_a_redraw_and_given_up_when_cleared(self) -> None:
        for window in (self._bulk(), self._lbl()):
            view_box = window._plot.getPlotItem().getViewBox()
            window.axes._sides["y"].low.setText("0")
            window.axes._sides["y"].high.setText("500")
            window.axes._sides["y"].high.editingFinished.emit()
            self.assertEqual(tuple(round(v, 6) for v in view_box.viewRange()[1]), (0.0, 500.0), type(window).__name__)
            window.redraw()
            self.assertEqual(tuple(round(v, 6) for v in view_box.viewRange()[1]), (0.0, 500.0), type(window).__name__)
            window.axes.reset()
            _APP.processEvents()
            self.assertLess(view_box.viewRange()[1][1], 400.0, type(window).__name__)  # back to the data's own range
            self.assertTrue(view_box.autoRangeEnabled()[1], type(window).__name__)

    def test_lbl_cycle_labels_give_way_to_a_tick_step_and_come_back(self) -> None:
        window = self._lbl()
        axis = window._plot.getPlotItem().getAxis("bottom")
        self.assertIsNotNone(axis._tickLevels)  # one tick per cycle
        window.axes._sides["x"].major.setText("2")
        window.axes._sides["x"].major.editingFinished.emit()
        window.redraw()
        self.assertIsNone(axis._tickLevels)
        self.assertEqual(axis._tickSpacing, [(2.0, 0.0)])
        window.axes.reset()
        window.redraw()
        self.assertIsNotNone(axis._tickLevels)
        self.assertIsNone(axis._tickSpacing)

    def test_the_options_follow_the_quantity_each_window_shows_and_are_the_sessions(self) -> None:
        module = PlotAxesModule()
        window = self._bulk(module)
        window.axes._sides["y"].major.setText("5")
        window.axes._sides["y"].major.editingFinished.emit()
        block = module.state()
        self.assertEqual(block["bulk"]["axes"][0]["label"], "Peak position (nm)")
        self.assertEqual(block["bulk"]["axes"][0]["side"], "y")
        self.assertNotIn("lbl", block)


if __name__ == "__main__":
    unittest.main()
