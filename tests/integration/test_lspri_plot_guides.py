"""Guides of the Plot group (2026-11-27): horizontal reference lines at a y value, with a label, a dash style and a colour.

What is pinned:
- **the rules**: a guide with no finite value is dropped, a colour must be '#rrggbb', an unknown style falls back, at most `MAX_GUIDES`, saved with the y side's options and read back leniently;
- **the controls**: a typed value makes a guide, an empty value removes it, text that is no number is refused with a reason and the row goes back, the guides belong to the y quantity shown,
  a colour a session file brought stays;
- **what the plots draw**: a line behind the traces at each value, never part of the autoscale, a guide edit leaves the user's zoom alone, the windows draw them again after a redraw;
- **the figure**: every guide is a part of the "Guides" layer of the layered SVG;
- **no number changes**: the lines given to the plot are not modified.
A real `QApplication` in-process (offscreen); no dataset files.
"""

from __future__ import annotations

import math
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
    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.domain.guide_lines import MAX_GUIDES, Guide, normalized
    from lspr_imaging_app.domain.plot_axes import AxisOptions, PlotAxes
    from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
    from lspr_imaging_app.panels.sensorgram.guide_controls import GuideControls
    from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.svg_layers import write_layered_svg
    from lspr_imaging_app.selection.plot_axes_module import PlotAxesModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

X = np.arange(100.0)
Y_LABEL = "Peak position (nm)"
REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
RI = (1.33300, 1.34531, 1.35762, 1.33500)


def _series() -> Series:
    return Series("roi:1", "ROI 1", "#ffffff", X.copy(), 600.0 + 0.1 * X)


def _numbers() -> RowNumbers:
    return RowNumbers(refs=REFS, n=np.full(4, 25), mean=np.array([100.0, 110.0, 125.0, 131.0]), sd=np.full(4, 0.5))


class GuideRulesTest(unittest.TestCase):
    def test_a_guide_without_a_finite_value_is_dropped_and_the_other_fields_are_made_valid(self) -> None:
        self.assertIsNone(Guide(float("nan")).sanitized())
        self.assertIsNone(Guide(float("inf")).sanitized())
        self.assertIsNone(Guide("soon").sanitized())  # type: ignore[arg-type]
        guide = Guide(650.0, "  Base   line  ", "red", "wavy").sanitized()
        self.assertEqual((guide.value, guide.label, guide.color, guide.style), (650.0, "Base line", None, "dash"))
        self.assertEqual(Guide(1.0, "", "#FF00AA", "dot").sanitized().color, "#ff00aa")
        self.assertEqual(len(Guide(1.0, "x" * 100).sanitized().label), 40)

    def test_at_most_max_guides_in_the_given_order(self) -> None:
        many = [Guide(float(i)) for i in range(MAX_GUIDES + 3)]
        self.assertEqual([g.value for g in normalized(many)], [float(i) for i in range(MAX_GUIDES)])
        self.assertEqual(normalized([Guide(1.0), "junk", Guide(float("nan")), Guide(2.0)]), (Guide(1.0), Guide(2.0)))
        self.assertEqual(normalized(None), ())

    def test_they_are_saved_with_the_axis_options_only_when_set_and_read_back_leniently(self) -> None:
        self.assertEqual(AxisOptions().to_payload(), {})
        options = AxisOptions(guides=(Guide(650.0, "Base", "#d62728", "solid"), Guide(660.0)))
        payload = options.to_payload()
        self.assertEqual(payload["guides"], [{"value": 650.0, "label": "Base", "color": "#d62728", "style": "solid"}, {"value": 660.0}])
        self.assertEqual(AxisOptions.from_payload(payload), options)
        damaged = AxisOptions.from_payload({"guides": [{"value": "x"}, 5, {"value": 3, "style": "zigzag"}, {"label": "no value"}]})
        self.assertEqual(damaged.guides, (Guide(3.0),))
        self.assertEqual(AxisOptions.from_payload({"guides": "junk"}).guides, ())
        self.assertFalse(options.is_default())

    def test_only_guides_differ(self) -> None:
        base = PlotAxes(x=AxisOptions(major=10.0))
        with_guide = PlotAxes(x=AxisOptions(major=10.0), y=AxisOptions(guides=(Guide(650.0),)))
        self.assertTrue(base.only_guides_differ(with_guide))
        self.assertTrue(base.only_guides_differ(base))
        self.assertFalse(base.only_guides_differ(PlotAxes(x=AxisOptions(major=20.0))))
        self.assertFalse(with_guide.only_guides_differ(PlotAxes(y=AxisOptions(low=1.0, guides=(Guide(650.0),)))))


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


class ControlsTest(_Case):
    def _controls(self, module=None, labels=("Cube (#)", Y_LABEL)) -> GuideControls:
        parent = self._own(QtWidgets.QWidget())
        return GuideControls(parent, module or PlotAxesModule(), "sensorgram", lambda: labels, object_prefix="g")

    @staticmethod
    def _type(box: QtWidgets.QLineEdit, text: str) -> None:
        box.setText(text)
        box.editingFinished.emit()

    def test_a_typed_value_makes_a_guide_and_an_empty_value_removes_it(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        told: list[str] = []
        module.changed.connect(told.append)
        slot = controls._slots[0]
        self._type(slot.value, "650,5")
        self.assertEqual(controls.guides(), (Guide(650.5),))
        self.assertEqual(told, ["sensorgram"])
        self._type(slot.label, "Baseline")
        slot.style.setCurrentIndex(slot.style.findData("dot"))
        slot.style.activated.emit(slot.style.currentIndex())
        slot.color.setCurrentIndex(slot.color.findData("#2ca02c"))
        slot.color.activated.emit(slot.color.currentIndex())
        self.assertEqual(controls.guides(), (Guide(650.5, "Baseline", "#2ca02c", "dot"),))
        self.assertEqual(module.state()["sensorgram"]["axes"][0]["guides"][0]["label"], "Baseline")
        self._type(slot.value, "")
        self.assertEqual(controls.guides(), ())
        self.assertEqual(slot.label.text(), "")  # the row is empty again
        self.assertFalse(controls.clear_button.isEnabled())

    def test_text_that_is_no_number_is_refused_and_the_row_goes_back(self) -> None:
        controls = self._controls()
        self._type(controls._slots[0].value, "650")
        self._type(controls._slots[0].value, "high")
        self.assertEqual(controls.guides(), (Guide(650.0),))
        self.assertEqual(controls._slots[0].value.text(), "650")
        self.assertIn("Guide 1", controls.note.text())
        self.assertIn("not a number", controls.note.text())

    def test_empty_rows_in_the_middle_close_up(self) -> None:
        controls = self._controls()
        self._type(controls._slots[0].value, "600")
        self._type(controls._slots[2].value, "700")
        self.assertEqual([g.value for g in controls.guides()], [600.0, 700.0])
        self.assertEqual(controls._slots[1].value.text(), "700")

    def test_the_guides_belong_to_the_y_quantity_shown(self) -> None:
        module = PlotAxesModule()
        shown = [Y_LABEL]
        parent = self._own(QtWidgets.QWidget())
        controls = GuideControls(parent, module, "sensorgram", lambda: ("Cube (#)", shown[0]), object_prefix="g")
        self._type(controls._slots[0].value, "650")
        shown[0] = "Peak shift (nm)"
        controls.labels_changed()
        self.assertEqual(controls.guides(), ())
        self.assertEqual(controls._slots[0].value.text(), "")
        shown[0] = Y_LABEL
        controls.labels_changed()
        self.assertEqual(controls._slots[0].value.text(), "650")

    def test_a_colour_the_list_does_not_have_is_shown_as_custom_and_kept(self) -> None:
        module = PlotAxesModule()
        module.set_axes("sensorgram", "Cube (#)", Y_LABEL, PlotAxes(y=AxisOptions(guides=(Guide(650.0, "", "#123456"),))))
        controls = self._controls(module)
        slot = controls._slots[0]
        self.assertEqual(slot.color.currentText(), "Custom")
        self._type(slot.label, "x")
        self.assertEqual(controls.guides()[0].color, "#123456")

    def test_clear_all_removes_the_guides_of_this_quantity_only(self) -> None:
        module = PlotAxesModule()
        controls = self._controls(module)
        self._type(controls._slots[0].value, "650")
        module.set_axes("sensorgram", "Cube (#)", "Other (nm)", PlotAxes(y=AxisOptions(guides=(Guide(1.0),))))
        controls.clear_button.click()
        self.assertEqual(controls.guides(), ())
        self.assertEqual(module.axes("sensorgram", "Cube (#)", "Other (nm)").y.guides, (Guide(1.0),))


class SensorgramPlotTest(_Case):
    def _plot(self, guides=()) -> SensorgramPlot:
        plot = self._own(SensorgramPlot())
        plot.resize(900, 600)
        plot.show()
        _APP.processEvents()
        plot.set_axes(PlotAxes(y=AxisOptions(guides=tuple(guides))))
        plot.show_series([_series()])
        return plot

    def test_a_line_stands_at_each_value_behind_the_traces_with_its_label(self) -> None:
        plot = self._plot([Guide(605.0, "Baseline", "#d62728", "solid"), Guide(608.0)])
        lines = plot._guides.lines()
        self.assertEqual([line.value() for line in lines], [605.0, 608.0])
        self.assertTrue(all(line.zValue() < 0.0 for line in lines))
        self.assertTrue(all(line.movable for line in lines))  # dragged with the mouse (2026-11-27)
        self.assertEqual(lines[0].label.textItem.toPlainText(), "Baseline")
        self.assertIsNone(getattr(lines[1], "label", None))  # no text: no label item

    def test_a_guide_far_from_the_data_does_not_stretch_the_view(self) -> None:
        plot = self._plot([Guide(5000.0)])
        _x, (y_low, y_high) = plot.plot_item().getViewBox().viewRange()
        self.assertLess(y_high, 700.0)
        plot.reset_view()
        _x, (_low, y_high) = plot.plot_item().getViewBox().viewRange()
        self.assertLess(y_high, 700.0)

    def test_a_label_with_braces_is_written_as_typed(self) -> None:
        plot = self._plot([Guide(605.0, "{value} {x}")])
        self.assertEqual(plot._guides.lines()[0].label.textItem.toPlainText(), "{value} {x}")

    def test_editing_a_guide_leaves_the_users_zoom_and_changing_the_range_does_not(self) -> None:
        plot = self._plot()
        view_box = plot.plot_item().getViewBox()
        view_box.setXRange(20.0, 40.0, padding=0.0)
        plot.set_axes(PlotAxes(y=AxisOptions(guides=(Guide(605.0),))))
        self.assertAlmostEqual(view_box.viewRange()[0][0], 20.0)  # the guide only appeared
        self.assertAlmostEqual(view_box.viewRange()[0][1], 40.0)
        self.assertEqual(len(plot._guides.lines()), 1)
        plot.set_axes(PlotAxes(y=AxisOptions(low=590.0, high=620.0, guides=(Guide(605.0),))))
        self.assertAlmostEqual(view_box.viewRange()[1][0], 590.0)  # a range is a request for that view
        plot.set_axes(PlotAxes())
        self.assertEqual(plot._guides.lines(), [])

    def test_the_lines_given_to_the_plot_are_not_modified(self) -> None:
        series = _series()
        before = (series.x.copy(), series.y.copy())
        plot = self._own(SensorgramPlot())
        plot.resize(900, 600)
        plot.show()
        plot.set_axes(PlotAxes(y=AxisOptions(guides=(Guide(605.0),))))
        plot.show_series([series])
        np.testing.assert_array_equal(series.x, before[0])
        np.testing.assert_array_equal(series.y, before[1])

    def test_every_guide_is_a_part_of_the_guides_layer_of_the_figure(self) -> None:
        plot = self._plot([Guide(605.0, "Baseline"), Guide(608.0)])
        parts = [p for p in plot.export_parts() if p.layer == "Guides"]
        self.assertEqual([p.name for p in parts], ["605 Baseline", "608"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "f.svg"
            write_layered_svg(plot.plot_item(), path, plot.export_parts(), "t")
            root = ET.fromstring(path.read_text(encoding="utf-8"))
        ink = "{http://www.inkscape.org/namespaces/inkscape}"
        labels = [el.get(f"{ink}label") for el in root.iter() if el.get(f"{ink}groupmode") == "layer"]
        self.assertIn("Guides", labels, labels)
        guides = next(el for el in root.iter() if el.get(f"{ink}groupmode") == "layer" and el.get(f"{ink}label") == "Guides")
        texts = ["".join(t.itertext()).strip() for t in guides.iter() if t.tag.endswith("text")]
        self.assertIn("Baseline", texts)
        self.assertTrue(any(el.tag.endswith(("path", "polyline", "line")) for el in guides.iter()))


class WindowTest(_Case):
    def _bulk(self, module: PlotAxesModule | None = None) -> BulkPlotWindow:
        window = self._own(BulkPlotWindow(plot_axes=module))
        window.set_steps([PlotStep(REFS[i], i + 1, f"s{i}", RI[i], math.nan, i < 3) for i in range(4)], 589.3)
        window.set_lines([BulkLine("Group A (n=12)", "#4e79a7", _numbers(), short_label="Group A")], y_label=Y_LABEL)
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

    def test_both_windows_have_the_guides_menu_in_their_ribbon_and_draw_them(self) -> None:
        for window in (self._bulk(), self._lbl()):
            name = type(window).__name__
            self.assertTrue(window.isAncestorOf(window.guides.menu), name)
            box = window.guides._slots[0].value
            box.setText("120")
            box.editingFinished.emit()
            self.assertEqual([line.value() for line in window._guide_lines.lines()], [120.0], name)
            window.redraw()  # `clear()` took the line out: it is drawn again
            self.assertEqual([line.value() for line in window._guide_lines.lines()], [120.0], name)
            self.assertIn(window._guide_lines.lines()[0], window._plot.getPlotItem().items, name)

    def test_a_guide_edit_does_not_draw_the_window_again(self) -> None:
        for window in (self._bulk(), self._lbl()):
            before = window._state
            box = window.guides._slots[0].value
            box.setText("120")
            box.editingFinished.emit()
            self.assertIs(window._state, before, type(window).__name__)  # the lines were not redrawn

    def test_a_guide_does_not_change_the_range_of_a_window(self) -> None:
        for window in (self._bulk(), self._lbl()):
            view_box = window._plot.getPlotItem().getViewBox()
            before = view_box.viewRange()[1]
            box = window.guides._slots[0].value
            box.setText("90000")
            box.editingFinished.emit()
            window.redraw()
            self.assertEqual(view_box.viewRange()[1], before, type(window).__name__)

    def test_the_guides_are_the_sessions_per_window_and_quantity(self) -> None:
        module = PlotAxesModule()
        window = self._bulk(module)
        box = window.guides._slots[0].value
        box.setText("120")
        box.editingFinished.emit()
        block = module.state()
        entry = block["bulk"]["axes"][0]
        self.assertEqual((entry["side"], entry["label"]), ("y", Y_LABEL))
        self.assertEqual(entry["guides"], [{"value": 120.0}])
        self.assertNotIn("lbl", block)


if __name__ == "__main__":
    unittest.main()
