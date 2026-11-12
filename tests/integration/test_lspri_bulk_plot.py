"""The floating S_bulk plot (`panels/sensorgram/bulk_plot.py`, 2026-11-12): lines, filled points for the S_bulk set, hollow points for the other steps with an RI, the fitted
line with its legend, the notes, the export. A real `QApplication` in-process (offscreen); no dataset files."""

from __future__ import annotations

import math
import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np
    import pyqtgraph as pg

    from lspr_imaging_app.analysis.step_table import RowNumbers
    from lspr_imaging_app.panels.sensorgram.bulk_plot import LINES_GROUPS, BulkLine, BulkPlotWindow, PlotStep, lines_from_series
    from lspr_imaging_app.panels.ui_state import UiStateStore
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

RI = (1.33300, 1.34531, 1.35762, 1.33500)
REFS = tuple(("m", i + 1, 1000 * i, f"s{i}") for i in range(4))
NAMES = ("H2O", "10perEG", "20perEG", "Protein")


def _steps(in_set=(True, True, True, False)):
    return [PlotStep(REFS[i], i + 1, NAMES[i], RI[i], math.nan, in_set[i]) for i in range(4)]


def _numbers(extra=0.8):
    means = [600.0 + 200.0 * (r - RI[0]) for r in RI]
    means[3] += extra  # the protein step shifts more than the bulk RI explains
    return RowNumbers(refs=REFS, n=np.full(4, 25), mean=np.array(means), sd=np.full(4, 0.05))


class BulkPlotWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = BulkPlotWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def _items(self, kind):
        return [i for i in self.window._plot.getPlotItem().items if isinstance(i, kind)]

    def test_points_line_band_and_legend(self) -> None:
        w = self.window
        w.set_steps(_steps(), 589.3)
        w.set_lines([BulkLine("ROI 1", "#4e79a7", _numbers())], y_label="Peak position (nm)")
        w.show()
        scatters = self._items(pg.ScatterPlotItem)
        self.assertEqual(sorted(len(s.data) for s in scatters), [1, 3])  # 3 filled (in the set), 1 hollow (the protein)
        lines = [i for i in self._items(pg.PlotDataItem) if i.name()]
        self.assertEqual(len(lines), 1)
        self.assertIn("S_bulk = 200.0", lines[0].name())
        self.assertIn("nm/RIU", lines[0].name())
        self.assertEqual(len(self._items(pg.FillBetweenItem)), 1)  # the line's uncertainty band
        self.assertIn("589.3 nm", w._plot.getPlotItem().getAxis("bottom").labelText)
        self.assertTrue(w._plot.getPlotItem().getAxis("bottom").labelText.startswith("Refractive index"))
        self.assertEqual(w.windowTitle(), "Bulk sensitivity")
        x_lo, x_hi = w._plot.getPlotItem().vb.viewRange()[0]  # the x axis spans the lowest to the highest RI (plus a little room), not more
        self.assertLess(x_lo, min(RI))
        self.assertGreater(x_lo, min(RI) - 0.1 * (max(RI) - min(RI)))
        self.assertGreater(x_hi, max(RI))
        self.assertLess(x_hi, max(RI) + 0.1 * (max(RI) - min(RI)))
        self.assertEqual(w.status.text(), "")

    def test_too_few_steps_in_the_set_says_so_and_draws_no_line(self) -> None:
        w = self.window
        w.set_steps(_steps((True, False, False, False)), None)
        w.set_lines([BulkLine("ROI 1", "#4e79a7", _numbers())], y_label="Peak position (nm)")
        w.show()
        self.assertIn("at least two steps", w.status.text())
        self.assertFalse([i for i in self._items(pg.PlotDataItem) if i.name()])

    def test_export_has_every_point_with_its_excess_and_the_fit(self) -> None:
        w = self.window
        w.set_steps(_steps(), 589.3)
        w.set_lines([BulkLine("ROI 1", "#4e79a7", _numbers(extra=0.8))], y_label="Peak position (nm)")
        w.show()
        data = w.export_data()
        points = data.tables["s_bulk_points"]
        protein = next(row for row in points.rows if row[2] == "Protein")
        excess = protein[points.header.index("measured_minus_predicted")]
        self.assertAlmostEqual(excess, 0.8, places=6)
        fits = data.tables["s_bulk_fits"]
        self.assertAlmostEqual(fits.rows[0][fits.header.index("s_bulk")], 200.0, places=6)

    def test_groups_mode_asks_for_a_calculation_and_the_choice_is_remembered(self) -> None:
        store = UiStateStore({})
        w = self.window
        w.restore_ui_state(store)
        asked = []
        w.calculate_requested.connect(lambda: asked.append(1))
        w.lines_pick.setCurrentIndex(w.lines_pick.findData(LINES_GROUPS))
        self.assertEqual(store.get("sensorgram/bulk_plot_lines"), LINES_GROUPS)
        w.set_steps(_steps(), None)
        w.set_lines([], y_label="Shift (nm)", note="2 groups not calculated yet.", need_calculation=True)
        w.show()
        self.assertFalse(w.calculate_button.isHidden())
        w.calculate_button.click()
        self.assertEqual(asked, [1])
        self.assertIn("not calculated", w.status.text())

    def test_closing_the_window_says_so(self) -> None:
        closed = []
        self.window.closed.connect(lambda: closed.append(1))
        self.window.show()
        self.window.close()
        self.assertEqual(closed, [1])


class LinesFromSeriesTest(unittest.TestCase):
    def test_a_line_without_step_numbers_is_left_out(self) -> None:
        from types import SimpleNamespace

        from lspr_imaging_app.analysis.step_stats import LineStepValues

        values = LineStepValues(n=np.full(4, 10), mean=np.arange(4.0), sd=np.full(4, 0.1), between_rois=False)
        series = [SimpleNamespace(label="ROI 1", color="#ff0000", step_values=values), SimpleNamespace(label="ROI 2", color="#00ff00", step_values=None)]
        lines = lines_from_series(series, REFS)
        self.assertEqual([line.label for line in lines], ["ROI 1"])
        self.assertEqual(lines[0].numbers.refs, REFS)
        self.assertEqual(lines_from_series(series, REFS[:2]), [])  # the steps changed since the line was drawn: not matched by position


if __name__ == "__main__":
    unittest.main()
