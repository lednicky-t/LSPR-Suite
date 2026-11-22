"""Which line lies over which in the three plots (2026-11-22): the Draw order (Auto / Legend / Manual), the Line order table, and the session's manual list.

Pinned, driven through methods and signals (never a screen coordinate):
- the Points & lines menu has a Draw order row in every plot (Legend at first: the first legend row in front), its value is the plot's own and is remembered, and the table button works
  only in Manual;
- the stacking really changes (the z of a line's items) in the Sensorgram, the LbL and the S_bulk plots, for each mode; a line's own items stay together; the legend, the lines' own order
  and the numbers are not touched;
- Auto puts the roughest line at the back; Legend follows the Legend menu's order, also with a Value-at-step order (the Sensorgram redraws when the step changes);
- the Line order table: rows in front-first order with the new lines in front, To top / Up / Down / To bottom, a drop, Own order, only what can be done is enabled; every change is the
  session's list (`PlotLineOrderModule`) and the plot follows at once;
- the module and the session file (schema 1.16): written only when a list exists, read back, an older file opens without one, a damaged block is skipped.
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
from PyQt6.QtCore import QCoreApplication, QEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg

    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.analysis.step_stats import LineStepValues
    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.frame_naming import FrameNamingScheme
    from lspr_imaging_app.panels.sensorgram.bulk_plot import BulkLine, BulkPlotWindow, PlotStep
    from lspr_imaging_app.panels.sensorgram.draw_order import DRAW_AUTO, DRAW_LEGEND, DRAW_MANUAL
    from lspr_imaging_app.panels.sensorgram.lbl_plot import LblPlotWindow, LblStep
    from lspr_imaging_app.panels.sensorgram.legend_order import SORT_NAME, SORT_VALUE, VALUE_STEP
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.panels.sensorgram.view_settings import SensorgramViewSettings
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.selection.plot_line_order_module import PlotLineOrderModule
    from lspr_imaging_app.storage.session import SESSION_SCHEMA_VERSION, SessionState, load_session, save_session, session_path
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
RI = (1.33300, 1.34531, 1.35762, 1.33500)
NAMES = ("H2O", "10perEG", "20perEG", "Protein")
# Group A is smooth (accumulated shift 0, 10, 25, 31) and B is rough (0, 40, 25, 20).
MEAN = {"A": [100.0, 110.0, 125.0, 131.0], "B": [100.0, 140.0, 125.0, 120.0]}
COLOR = {"A": "#38bdf8", "B": "#f59e0b"}


def _lines(*names: str) -> list[BulkLine]:
    return [
        BulkLine(f"Group {n}", COLOR[n], RowNumbers(refs=REFS, n=np.full(4, 25), mean=np.array(MEAN[n]), sd=np.full(4, 0.5)), short_label=f"Group {n}") for n in names
    ]


def _lbl(lines, module=None) -> LblPlotWindow:
    window = LblPlotWindow(None, line_order=module)
    window.set_steps([LblStep(REFS[i], i + 1, NAMES[i]) for i in range(4)])
    window.set_lines(lines, y_label="Δλ (nm)")
    window.show()
    return window


def _bulk(lines, module=None) -> BulkPlotWindow:
    window = BulkPlotWindow(None, line_order=module)
    window.set_steps([PlotStep(REFS[i], i + 1, NAMES[i], RI[i], math.nan, i < 3) for i in range(4)], 589.3)
    window.set_lines(lines, y_label="Peak position (nm)")
    window.show()
    return window


def _front_to_back(window) -> list[str]:
    """The names ("A", "B") of the lines of a plot window, the one in front first, read from the z of their curves."""
    curves = [i for i in window._plot.getPlotItem().items if isinstance(i, pg.PlotCurveItem) and i.opts.get("pen") is not None and i.opts["pen"].color().name() in COLOR.values()]
    by_color = {c: z for c, z in ((i.opts["pen"].color().name(), i.zValue()) for i in curves)}
    names = {v: k for k, v in COLOR.items()}
    return [names[c] for c, _z in sorted(by_color.items(), key=lambda kv: -kv[1])]


def _pick(look, mode: str) -> None:
    look._draw_buttons[mode].click()


class _Case(unittest.TestCase):
    def _own(self, widget):
        self.addCleanup(lambda: (widget.close(), widget.deleteLater(), QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)))
        return widget


class WindowStackingTest(_Case):
    def test_legend_is_the_start_and_the_first_legend_row_is_in_front(self) -> None:
        window = self._own(_lbl(_lines("B", "A")))
        self.assertEqual(window.look.settings().draw_mode, DRAW_LEGEND)
        self.assertEqual(_front_to_back(window), ["B", "A"])  # the legend's first row is B
        window.look._sort_buttons[SORT_NAME].click()  # the legend now reads A, B: the stacking follows it
        self.assertEqual(_front_to_back(window), ["A", "B"])

    def test_auto_puts_the_roughest_line_at_the_back(self) -> None:
        window = self._own(_lbl(_lines("B", "A")))
        _pick(window.look, DRAW_AUTO)
        self.assertEqual(_front_to_back(window), ["A", "B"])  # A is smooth, B jumps about
        window.set_lines(_lines("A", "B"), y_label="Δλ (nm)")
        self.assertEqual(_front_to_back(window), ["A", "B"])  # whatever the order the lines come in

    def test_the_legend_and_the_lines_own_order_are_not_touched_by_the_draw_order(self) -> None:
        window = self._own(_lbl(_lines("B", "A")))
        before = [label.text for _s, label in window._legend.items]
        _pick(window.look, DRAW_AUTO)
        self.assertEqual([label.text for _s, label in window._legend.items], before)
        self.assertEqual([line.label for line, _points in window._drawn], ["Group B", "Group A"])

    def test_value_at_step_in_the_legend_restacks_the_lines_when_the_cursor_moves(self) -> None:
        window = self._own(_lbl(_lines("A", "B")))
        window.look._sort_buttons[SORT_VALUE].click()
        window.look._value_buttons[VALUE_STEP].click()  # the cursor starts at the last cycle: A 31, B 20 -> ascending: B first row, in front
        self.assertEqual(_front_to_back(window), ["B", "A"])
        window._cursor._dragged(1.0)  # cycle 1: A 10, B 40
        self.assertEqual(_front_to_back(window), ["A", "B"])

    def test_the_cursor_is_above_every_line(self) -> None:
        window = self._own(_lbl(_lines("A", "B")))
        window.look._sort_buttons[SORT_VALUE].click()
        window.look._value_buttons[VALUE_STEP].click()
        line_z = max(i.zValue() for i in window._plot.getPlotItem().items if i is not window._cursor._cursor.line)
        self.assertGreater(window._cursor._cursor.line.zValue(), line_z)

    def test_s_bulk_stacks_the_same_way(self) -> None:
        window = self._own(_bulk(_lines("B", "A")))
        self.assertEqual(_front_to_back(window), ["B", "A"])
        _pick(window.look, DRAW_AUTO)  # roughness = the scatter of the step means about the line's own fit
        front = _front_to_back(window)
        self.assertEqual(set(front), {"A", "B"})
        rough = {e.key: e.rough for e in window._last_entries}
        self.assertEqual(front[0], min(rough, key=lambda k: rough[k]).split()[-1])  # the smoother line in front


class ManualOrderTest(_Case):
    def test_manual_follows_the_sessions_list_and_a_new_line_is_in_front(self) -> None:
        module = PlotLineOrderModule()
        window = self._own(_lbl(_lines("A", "B"), module))
        _pick(window.look, DRAW_MANUAL)
        self.assertEqual(_front_to_back(window), ["A", "B"])  # no list yet: the first line in front
        module.set_order("lbl", ["Group B", "Group A"])
        self.assertEqual(_front_to_back(window), ["B", "A"])  # the plot followed the change
        module.set_order("lbl", ["Group B"])  # A is not in the list: in front of the listed
        self.assertEqual(_front_to_back(window), ["A", "B"])

    def test_the_list_of_one_plot_is_not_another_plots(self) -> None:
        module = PlotLineOrderModule()
        lbl, bulk = self._own(_lbl(_lines("A", "B"), module)), self._own(_bulk(_lines("A", "B"), module))
        _pick(lbl.look, DRAW_MANUAL)
        _pick(bulk.look, DRAW_MANUAL)
        module.set_order("lbl", ["Group B", "Group A"])
        self.assertEqual(_front_to_back(lbl), ["B", "A"])
        self.assertEqual(_front_to_back(bulk), ["A", "B"])
        self.assertEqual(module.order("bulk"), ())

    def test_the_table_button_works_only_in_manual_and_with_a_session_list(self) -> None:
        window = self._own(_lbl(_lines("A", "B"), PlotLineOrderModule()))
        self.assertFalse(window.look.edit_order_button.isEnabled())
        _pick(window.look, DRAW_MANUAL)
        self.assertTrue(window.look.edit_order_button.isEnabled())
        bare = self._own(_lbl(_lines("A", "B")))  # no session: nothing to keep a list in
        _pick(bare.look, DRAW_MANUAL)
        self.assertFalse(bare.look.edit_order_button.isEnabled())

    def test_the_draw_order_is_remembered_per_plot_and_reset_puts_it_back(self) -> None:
        store = UiStateStore({})
        window = self._own(_lbl(_lines("A", "B")))
        window.restore_ui_state(store)
        _pick(window.look, DRAW_AUTO)
        again = self._own(LblPlotWindow())
        again.restore_ui_state(UiStateStore(dict(store._values)))
        self.assertEqual(again.look.settings().draw_mode, DRAW_AUTO)
        self.assertEqual(self._own(BulkPlotWindow()).look.settings().draw_mode, DRAW_LEGEND)  # the other plot never heard of it
        window.look.reset_lines()
        self.assertEqual(window.look.settings().draw_mode, DRAW_LEGEND)
        damaged = self._own(LblPlotWindow())
        damaged.restore_ui_state(UiStateStore({"sensorgram/lbl_plot_look_draw_mode": "sideways"}))
        self.assertEqual(damaged.look.settings().draw_mode, DRAW_LEGEND)


class LineOrderTableTest(_Case):
    def _table(self, *names: str):
        module = PlotLineOrderModule()
        window = self._own(_lbl(_lines(*names), module))
        _pick(window.look, DRAW_MANUAL)
        window.look.open_line_order()
        dialog = window.look._order_dialog
        self.addCleanup(dialog.close)
        return module, window, dialog

    def test_rows_are_front_first_with_their_colour_and_the_new_lines_in_front(self) -> None:
        module, window, dialog = self._table("A", "B")
        self.assertEqual(dialog.keys(), ["Group A", "Group B"])
        module.set_order("lbl", ["Group B"])  # A is not in the list
        dialog.refresh()
        self.assertEqual(dialog.keys(), ["Group A", "Group B"])
        self.assertFalse(dialog._list.item(0).icon().isNull())

    def test_the_move_buttons_change_the_plot_and_the_sessions_list(self) -> None:
        module, window, dialog = self._table("A", "B")
        dialog._list.item(1).setSelected(True)  # B
        dialog._buttons["top"].click()
        self.assertEqual(module.order("lbl"), ("Group B", "Group A"))
        self.assertEqual(dialog.keys(), ["Group B", "Group A"])
        self.assertEqual(_front_to_back(window), ["B", "A"])
        dialog._list.clearSelection()
        dialog._list.item(0).setSelected(True)  # B again, now first
        self.assertFalse(dialog._buttons["top"].isEnabled())  # nothing to do: greyed
        self.assertFalse(dialog._buttons["up"].isEnabled())
        dialog._buttons["down"].click()
        self.assertEqual(module.order("lbl"), ("Group A", "Group B"))
        dialog._buttons["bottom"].click()  # already at the bottom: nothing happens
        self.assertEqual(module.order("lbl"), ("Group A", "Group B"))

    def test_a_drop_is_the_new_order_and_own_order_forgets_it(self) -> None:
        module, window, dialog = self._table("A", "B")
        item = dialog._list.takeItem(1)  # what a drag and drop leaves behind
        dialog._list.insertItem(0, item)
        dialog._dropped()
        self.assertEqual(module.order("lbl"), ("Group B", "Group A"))
        self.assertEqual(_front_to_back(window), ["B", "A"])
        dialog.reset_order()
        self.assertEqual(module.order("lbl"), ())
        self.assertEqual(dialog.keys(), ["Group A", "Group B"])
        self.assertFalse(dialog._reset.isEnabled())

    def test_the_table_follows_the_plot_when_the_lines_change_and_says_when_it_is_not_used(self) -> None:
        module, window, dialog = self._table("A", "B")
        window.set_lines(_lines("A"), y_label="Δλ (nm)")
        self.assertEqual(dialog.keys(), ["Group A"])
        _pick(window.look, DRAW_AUTO)
        dialog.refresh()
        self.assertIn("auto", dialog._hint.text().lower())


class SensorgramStackingTest(_Case):
    def _plot(self, view: SensorgramViewSettings, manual=()) -> SensorgramPlot:
        plot = SensorgramPlot()
        plot.resize(900, 700)
        plot.show()
        _APP.processEvents()
        self._own(plot)
        plot.set_view(view)
        plot.set_manual_order(manual)
        return plot

    @staticmethod
    def _series(roughness=(0.0, 5.0, 0.0)) -> list[Series]:
        x = np.arange(8.0)
        out = []
        for i, r in enumerate(roughness):
            y = 600.0 + i + x + r * np.where(np.arange(8) % 2 == 0, 1.0, -1.0)  # a ramp plus jitter of size r
            values = LineStepValues(np.array([3, 3]), np.array([600.0 + i, 610.0 - 2 * i]), np.array([0.1, 0.1]), False)
            out.append(Series(f"roi:{i}", f"ROI {i}", "#ffffff", x, y, step_values=values))
        return out

    @staticmethod
    def _front_to_back(plot: SensorgramPlot) -> list[int]:
        curves = [i for i in plot._items if isinstance(i, pg.PlotCurveItem)]
        order = sorted(curves, key=lambda c: -c.zValue())
        return [int(round(float(np.mean(c.yData)) - 603.5)) for c in order]  # line i: y = 600 + i + x (mean x 3.5) plus jitter that sums to zero

    def test_legend_mode_has_the_first_legend_row_in_front(self) -> None:
        plot = self._plot(SensorgramViewSettings())
        plot.show_series(self._series())
        self.assertEqual(self._front_to_back(plot)[0], 0)  # ROI 0 is the first legend row
        plot.set_view(SensorgramViewSettings(legend_descending=True))
        plot.show_series(self._series())
        self.assertEqual(self._front_to_back(plot)[0], 2)

    def test_auto_puts_the_rough_line_at_the_back(self) -> None:
        plot = self._plot(SensorgramViewSettings(draw_mode=DRAW_AUTO))
        plot.show_series(self._series())
        self.assertEqual(self._front_to_back(plot), [2, 0, 1])  # ROI 1 is the noisy one: at the back; the equally smooth 0 and 2 keep the later one in front

    def test_manual_follows_the_list_and_a_line_not_in_it_is_in_front(self) -> None:
        plot = self._plot(SensorgramViewSettings(draw_mode=DRAW_MANUAL), ["roi:1", "roi:2", "roi:0"])
        plot.show_series(self._series())
        self.assertEqual(self._front_to_back(plot), [1, 2, 0])
        plot.set_manual_order(["roi:1"])
        plot.show_series(self._series())
        self.assertEqual(self._front_to_back(plot), [0, 2, 1])  # 0 and 2 are new: in front, in their own order (0 first)

    def test_a_redraw_with_other_data_keeps_the_stacking_and_the_items_are_reused(self) -> None:
        plot = self._plot(SensorgramViewSettings(draw_mode=DRAW_MANUAL), ["roi:2", "roi:1", "roi:0"])
        plot.show_series(self._series())
        first = {id(i) for i in plot._items}
        plot.set_manual_order(["roi:0", "roi:1", "roi:2"])
        plot.show_series(self._series())
        self.assertEqual(self._front_to_back(plot), [0, 1, 2])
        self.assertEqual({id(i) for i in plot._items}, first)  # no new items: the pool re-stacked the ones it had

    def test_a_new_selected_step_restacks_only_when_the_stacking_follows_a_value_at_step_legend(self) -> None:
        plot = self._plot(SensorgramViewSettings(legend_sort=SORT_VALUE, legend_value=VALUE_STEP))
        plot.show_series(self._series())
        self.assertTrue(plot.set_order_step(1))  # Draw order = Legend: the panel must redraw
        plot.set_view(SensorgramViewSettings(legend_sort=SORT_VALUE, legend_value=VALUE_STEP, draw_mode=DRAW_AUTO))
        self.assertFalse(plot.set_order_step(0))  # Auto does not read the step
        self.assertFalse(plot.set_order_step(0))  # and nothing changed


class ModuleAndSessionTest(unittest.TestCase):
    def test_the_module_emits_only_on_a_change_and_cleans_its_input(self) -> None:
        module = PlotLineOrderModule()
        seen: list[str] = []
        module.changed.connect(seen.append)
        module.set_order("lbl", ["a", "b", "a"])
        self.assertEqual(module.order("lbl"), ("a", "b"))
        module.set_order("lbl", ["a", "b"])
        self.assertEqual(seen, ["lbl"])  # the same list again says nothing
        with self.assertRaises(ValueError):
            module.set_order("nope", ["a"])

    def test_state_is_none_without_a_list_and_restores_with_one(self) -> None:
        module = PlotLineOrderModule()
        self.assertIsNone(module.state())
        module.set_order("bulk", ["x"])
        state = module.state()
        self.assertEqual(state, {"bulk": ["x"]})
        other, seen = PlotLineOrderModule(), []
        other.changed.connect(seen.append)
        other.restore_state(state)
        self.assertEqual(other.order("bulk"), ("x",))
        self.assertEqual(seen, ["bulk"])
        other.restore_state(None)  # a session without the block: every plot back to its own order, and the plots are told
        self.assertEqual(other.order("bulk"), ())
        self.assertEqual(seen, ["bulk", "bulk"])

    def test_a_damaged_block_loads_what_it_can(self) -> None:
        module = PlotLineOrderModule()
        module.restore_state({"lbl": ["a", 3], "bulk": ["b"], "sensorgram": "junk"})
        self.assertEqual((module.order("lbl"), module.order("bulk"), module.order("sensorgram")), ((), ("b",), ()))
        module.restore_state(["not", "a", "dict"])
        self.assertEqual(module.order("bulk"), ())

    def test_the_session_file_holds_the_list_only_when_there_is_one(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root, naming = Path(tmp), FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])
            self.assertEqual(SESSION_SCHEMA_VERSION, "1.16")
            path = save_session(root, SessionState(), naming)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("plot_line_order", payload)  # written exactly as before
            self.assertIsNone(load_session(root).plot_line_order)
            save_session(root, SessionState(plot_line_order={"sensorgram": ["roi:3", "group:A"], "lbl": ["Group A"]}), naming)
            payload = json.loads(session_path(root).read_text(encoding="utf-8"))
            self.assertEqual(payload["plot_line_order"], {"sensorgram": ["roi:3", "group:A"], "lbl": ["Group A"]})
            self.assertEqual(load_session(root).plot_line_order, {"sensorgram": ["roi:3", "group:A"], "lbl": ["Group A"]})

    def test_an_older_file_and_a_damaged_block_open_without_the_list(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root, naming = Path(tmp), FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])
            path = save_session(root, SessionState(plot_line_order={"lbl": ["a"]}), naming)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = "1.15"
            del payload["plot_line_order"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIsNone(load_session(root).plot_line_order)
            payload["plot_line_order"] = {"lbl": "junk", "bulk": ["ok"]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_session(root).plot_line_order, {"bulk": ["ok"]})


if __name__ == "__main__":
    unittest.main()
