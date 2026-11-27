"""The x offset / time zero of the Plot menu (2026-11-27): the numbers printed on the Sensorgram's x axis count from a chosen value.

What is pinned:
- **the rules**: `AxisOptions.zero` is saved only when set, a value that is no finite number is dropped, `shown()` counts from it;
- **the axis**: the ticks stand on multiples of the step *from the zero* and are labelled with the number counted from it (0 at the zero, negative before it), also across axis breaks;
- **the range**: a typed x range is in the printed numbers (the view in data x is shifted by the zero), "Use view" gives printed numbers back;
- **the controls**: the "Zero at" field and the step list (filled each time it opens) set it; text that is no number is refused; the LbL / S_bulk windows have no such row;
- **no number changes**: the lines, the cursor and the view reported by the plot stay in the data's own x; the exported figure carries the shifted labels.
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
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.svg_layers import write_layered_svg
    from lspr_imaging_app.selection.plot_axes_module import PlotAxesModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

X = np.arange(100.0)


def _series() -> Series:
    return Series("roi:1", "ROI 1", "#ffffff", X.copy(), 600.0 + 0.1 * X)


class AxisOptionsZeroTest(unittest.TestCase):
    def test_zero_is_saved_only_when_set_and_a_bad_value_is_dropped(self) -> None:
        self.assertEqual(AxisOptions().to_payload(), {})
        options = AxisOptions(zero=25.0)
        self.assertEqual(options.to_payload(), {"zero": 25.0})
        self.assertEqual(AxisOptions.from_payload(options.to_payload()), options)
        self.assertIsNone(AxisOptions.from_payload({"zero": "soon"}).zero)
        self.assertIsNone(AxisOptions.from_payload({"zero": float("inf")}).zero)
        self.assertFalse(AxisOptions(zero=0.0).is_default())  # a typed 0 is a value, even if it prints like the data
        self.assertTrue(AxisOptions(zero=None).is_default())

    def test_shown_counts_from_the_zero(self) -> None:
        self.assertEqual(AxisOptions().shown(12.0), 12.0)
        self.assertEqual(AxisOptions(zero=10.0).shown(12.0), 2.0)
        self.assertEqual(AxisOptions(zero=10.0).shown(4.0), -6.0)


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._widgets: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for widget in self._widgets:
            widget.close()
            widget.deleteLater()
        _APP.processEvents()

    def _plot(self, **x_options) -> SensorgramPlot:
        plot = SensorgramPlot()
        self._widgets.append(plot)
        plot.resize(900, 600)
        plot.show()
        _APP.processEvents()
        plot.set_axes(PlotAxes(x=AxisOptions(**x_options)))
        plot.show_series([_series()])
        return plot

    @staticmethod
    def _labels(plot: SensorgramPlot) -> dict[float, str]:
        """{data x of each tick: its printed label}."""
        axis = plot.plot_item().getAxis("bottom")
        low, high = plot.plot_item().getViewBox().viewRange()[0]
        out: dict[float, str] = {}
        for spacing, drawn in axis.tickValues(low, high, 800.0):
            for value, text in zip(drawn, axis.tickStrings(drawn, 1.0, spacing)):
                out[round(float(plot._data_x(value)), 6)] = text
        return out


class AxisTest(_Case):
    def test_ticks_stand_on_multiples_of_the_step_from_the_zero_and_are_labelled_from_it(self) -> None:
        plot = self._plot(major=10.0, zero=25.0)
        labels = self._labels(plot)
        self.assertEqual(labels[25.0], "0")
        self.assertEqual(labels[35.0], "10")
        self.assertEqual(labels[5.0], "-20")
        self.assertNotIn(30.0, labels)  # 30 is not a multiple of 10 from 25
        self.assertTrue(all((x - 25.0) % 10.0 == 0.0 for x in labels))

    def test_no_zero_leaves_the_numbers_the_data_has(self) -> None:
        plot = self._plot(major=10.0)
        labels = self._labels(plot)
        self.assertEqual(labels[30.0], "30")
        self.assertEqual(labels[0.0], "0")

    def test_automatic_ticks_also_count_from_the_zero(self) -> None:
        plot = self._plot(zero=33.0)
        labels = self._labels(plot)
        self.assertIn("0", labels.values())
        zero_at = [x for x, text in labels.items() if text == "0"]
        self.assertEqual(zero_at, [33.0])

    def test_across_a_break_the_labels_count_from_the_zero_and_both_ends_keep_their_tick(self) -> None:
        plot = self._plot(major=10.0, zero=30.0, breaks=(AxisBreak(30.0, 60.0),))
        labels = self._labels(plot)
        self.assertEqual(labels[30.0], "0")
        self.assertEqual(labels[60.0], "30")  # the far end of the break: a tick on both sides of the gap
        self.assertEqual(labels[70.0], "40")
        self.assertEqual(labels[20.0], "-10")
        self.assertFalse([x for x in labels if 30.0 < x < 60.0])

    def test_a_zero_that_is_a_float_of_noise_prints_without_a_minus_sign(self) -> None:
        plot = self._plot(major=0.1, zero=0.3, low=0.0, high=1.0)
        labels = self._labels(plot)
        self.assertNotIn("-0", labels.values())

    def test_a_typed_range_is_in_the_printed_numbers(self) -> None:
        plot = self._plot(low=0.0, high=50.0, zero=10.0)
        x_low, x_high = plot.plot_item().getViewBox().viewRange()[0]
        self.assertAlmostEqual(x_low, 10.0)  # printed 0 is data 10
        self.assertAlmostEqual(x_high, 60.0)
        (low, high), _y = plot.visible_range()
        self.assertAlmostEqual(low, 10.0)  # what the plot reports is data x
        self.assertAlmostEqual(high, 60.0)

    def test_a_typed_range_with_a_zero_and_a_break(self) -> None:
        plot = self._plot(low=-10.0, high=80.0, zero=20.0, breaks=(AxisBreak(30.0, 60.0),))
        (low, high), _y = plot.visible_range()
        self.assertAlmostEqual(low, 10.0)
        self.assertAlmostEqual(high, 100.0)


class NoNumberChangesTest(_Case):
    def test_the_line_and_the_reported_view_stay_in_data_x(self) -> None:
        plot = self._plot(zero=40.0)
        part = next(p for p in plot._svg_parts if p.layer == "Lines")
        x, y = part.item.getData()
        np.testing.assert_array_equal(x, X)
        np.testing.assert_allclose(y, 600.0 + 0.1 * X)
        plot.set_current_x(50.0)
        self.assertAlmostEqual(plot._data_x(plot._drawn_x(50.0)), 50.0)

    def test_the_exported_figure_carries_the_shifted_labels(self) -> None:
        plot = self._plot(major=10.0, zero=20.0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "f.svg"
            write_layered_svg(plot.plot_item(), path, plot.export_parts(), "t")
            root = ET.fromstring(path.read_text(encoding="utf-8"))
        ink = "{http://www.inkscape.org/namespaces/inkscape}"
        axes = next(el for el in root.iter() if el.get(f"{ink}groupmode") == "layer" and el.get(f"{ink}label") == "Axes")
        texts = {"".join(t.itertext()).strip() for t in axes.iter() if t.tag.endswith("text")}
        self.assertIn("0", texts)
        self.assertIn("-20", texts)  # data 0 is printed -20
        self.assertIn("70", texts)  # data 90


class ControlsTest(_Case):
    def _controls(self, module=None, *, steps=None, labels=("Time (s)", "Peak position (nm)"), view=((0.0, 100.0), (600.0, 700.0))):
        parent = QtWidgets.QWidget()
        self._widgets.append(parent)
        return AxisControls(parent, module or PlotAxesModule(), "sensorgram", lambda: labels, lambda: view, object_prefix="t", breaks=True, zero_steps=steps)

    @staticmethod
    def _type(box: QtWidgets.QLineEdit, text: str) -> None:
        box.setText(text)
        box.editingFinished.emit()

    def test_typing_a_zero_sets_it_for_the_quantity_shown_and_empty_clears_it(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module, steps=lambda: [])
        told: list[int] = []
        controls.axes_changed.connect(lambda: told.append(1))
        self._type(controls.zero, "25,5")  # a decimal comma is fine
        self.assertEqual(controls.axes().x.zero, 25.5)
        self.assertEqual(told, [1])
        self.assertEqual(module.state()["sensorgram"]["axes"][0]["zero"], 25.5)
        self._type(controls.zero, "")
        self.assertIsNone(controls.axes().x.zero)

    def test_a_zero_belongs_to_its_quantity(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module, steps=lambda: [])
        self._type(controls.zero, "25")
        other = self._controls(module, steps=lambda: [], labels=("Cube (#)", "Peak position (nm)"))
        self.assertIsNone(other.axes().x.zero)
        self.assertEqual(other.zero.text(), "")

    def test_text_that_is_no_number_is_refused_with_a_reason(self) -> None:
        controls = self._controls(steps=lambda: [])
        self._type(controls.zero, "25")
        self._type(controls.zero, "soon")
        self.assertEqual(controls.axes().x.zero, 25.0)
        self.assertEqual(controls.zero.text(), "25")  # back to what the session has
        self.assertIn("zero", controls.note.text())

    def test_the_step_list_is_asked_each_time_it_opens_and_a_step_sets_the_zero_at_its_start(self) -> None:
        plan = [[("1 · buffer", 0.0), ("2 · BSA", 120.0)]]
        controls = self._controls(steps=lambda: plan[0])
        combo = controls.zero_step
        combo.showPopup()
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ["Step...", "1 · buffer", "2 · BSA"])
        combo.hidePopup()
        combo.activated.emit(2)
        self.assertEqual(controls.axes().x.zero, 120.0)
        self.assertEqual(controls.zero.text(), "120")
        self.assertEqual(combo.currentIndex(), 0)  # it is an action: the caption again
        plan[0] = [("1 · other", 5.0)]
        combo.showPopup()
        self.assertEqual(combo.count(), 2)
        combo.hidePopup()

    def test_use_view_gives_the_numbers_the_axis_prints(self) -> None:
        controls = self._controls(view=((30.0, 90.0), (600.0, 700.0)), steps=lambda: [])
        self._type(controls.zero, "20")
        controls._sides["x"].use_view.click()
        self.assertEqual(controls.axes().x.low, 10.0)
        self.assertEqual(controls.axes().x.high, 70.0)
        controls._sides["y"].use_view.click()
        self.assertEqual(controls.axes().y.low, 600.0)  # y has no zero

    def test_a_window_without_the_row_keeps_its_zero_and_hides_the_widgets(self) -> None:
        module = PlotAxesModule()
        module.set_axes("lbl", "Cycle", "Shift", PlotAxes(x=AxisOptions(zero=3.0)))
        parent = QtWidgets.QWidget()
        self._widgets.append(parent)
        controls = AxisControls(parent, module, "lbl", lambda: ("Cycle", "Shift"), lambda: ((0.0, 1.0), (0.0, 1.0)), object_prefix="w")
        self.assertTrue(controls.zero.isHidden())
        self.assertTrue(controls.zero_step.isHidden())
        self._type(controls._sides["x"].major, "2")
        self.assertEqual(controls.axes().x, AxisOptions(major=2.0, zero=3.0))  # a typed step does not lose a saved zero

    def test_reset_clears_the_zero(self) -> None:
        controls = self._controls(steps=lambda: [])
        self._type(controls.zero, "25")
        controls.reset()
        self.assertIsNone(controls.axes().x.zero)
        self.assertEqual(controls.zero.text(), "")


if __name__ == "__main__":
    unittest.main()
