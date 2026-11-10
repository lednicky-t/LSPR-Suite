"""The Sensorgram plot keeps the user's own zoom over redraws (2026-11-10).

Before, every redraw (a recompute, another selection, a new baseline) put the view back to the full range. Now a zoom or pan made with the mouse
stays until the user asks for the full view (the corner "A" button, the view menu's "View All"), an axis shows another quantity, or a dataset is
loaded. Bare `SensorgramPlot`s, driven through methods and signals; the one wheel turn is sent to the middle of the view.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.panels.sensorgram.plot import SensorgramPlot
    from lspr_imaging_app.plan.stats_table import StatsLine, StatsTableSettings, build_stats_table
    from tests.integration.test_lspri_sensorgram_step_stats import _bar, _destroy
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _lines(level: float, count: int = 2) -> list[Series]:
    """`count` lines of 6 cubes that rise by 1 nm per cube from `level` (+ 1 nm per line)."""
    x = np.arange(6.0)
    return [Series(f"roi:{i}", f"ROI {i}", "#ffffff", x, level + i + x) for i in range(count)]


class KeptViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = SensorgramPlot()
        self.plot.resize(900, 500)
        self.plot.show()
        _APP.processEvents()
        self.addCleanup(_destroy, self.plot)
        self.view = self.plot.plot_item().getViewBox()

    def _range(self) -> np.ndarray:
        return np.array(self.view.viewRange(), dtype=float)

    def _wheel(self, delta: int = 240) -> None:
        """One turn of the mouse wheel over the middle of the plot (zooms in for a positive `delta`): the real gesture, not a call to the plot."""
        viewport = self.plot._widget.viewport()
        where = QPointF(self.plot._widget.mapFromScene(self.view.sceneBoundingRect().center()))
        event = QWheelEvent(
            where, QPointF(viewport.mapToGlobal(where.toPoint())), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(viewport, event)

    def test_without_a_zoom_every_redraw_shows_the_full_view(self) -> None:
        self.plot.show_series(_lines(600.0))
        first = self._range()
        self.assertFalse(self.plot.view_is_kept())
        self.plot.show_series(_lines(650.0))
        second = self._range()
        np.testing.assert_allclose(second[0], first[0])
        np.testing.assert_allclose(second[1], first[1] + 50.0)  # the y range went with the data

    def test_a_wheel_zoom_is_kept_over_redraws(self) -> None:
        self.plot.show_series(_lines(600.0))
        full = self._range()
        self._wheel()
        zoomed = self._range()
        self.assertTrue(self.plot.view_is_kept())
        self.assertLess(zoomed[1][1] - zoomed[1][0], full[1][1] - full[1][0])
        self.plot.show_series(_lines(600.5))  # a recompute: slightly other numbers
        np.testing.assert_allclose(self._range(), zoomed)
        self.plot.show_series(_lines(600.5, count=4))  # another selection
        np.testing.assert_allclose(self._range(), zoomed)
        self.plot.clear()  # nothing selected for a moment
        self.plot.show_series(_lines(600.5))
        np.testing.assert_allclose(self._range(), zoomed)

    def test_the_a_button_and_view_all_bring_the_full_view_back(self) -> None:
        self.plot.show_series(_lines(600.0))
        full = self._range()
        button = self.plot.plot_item().autoBtn
        self._wheel()
        button.clicked.emit(button)
        self.assertFalse(self.plot.view_is_kept())
        np.testing.assert_allclose(self._range(), full)
        self._wheel()
        self.assertTrue(self.plot.view_is_kept())
        self.view.menu.viewAll.trigger()
        self.assertFalse(self.plot.view_is_kept())
        np.testing.assert_allclose(self._range(), full)
        self.plot.show_series(_lines(650.0))  # and redraws follow the data again
        np.testing.assert_allclose(self._range()[1], full[1] + 50.0)

    def test_an_axis_with_another_quantity_ends_the_kept_zoom(self) -> None:
        self.plot.set_axis_labels("Cube (#)", "Peak position (nm)")
        self.plot.show_series(_lines(600.0))
        self._wheel()
        self.plot.set_axis_labels("Cube (#)", "Peak position (nm)")  # the same names at every redraw: nothing ends
        self.assertTrue(self.plot.view_is_kept())
        self.plot.set_axis_labels("Cube (#)", "Peak shift (nm)")  # Subtract: the traces are near 0 now, a zoom near 600 nm would show nothing
        self.assertFalse(self.plot.view_is_kept())
        self.plot.show_series(_lines(0.0))
        low, high = self._range()[1]
        self.assertLess(low, 0.0)
        self.assertGreater(high, 6.0)

    def test_a_new_dataset_ends_the_kept_zoom(self) -> None:
        self.plot.show_series(_lines(600.0))
        self._wheel()
        self.plot.forget_view()
        self.plot.show_series(_lines(650.0))
        low, high = self._range()[1]
        self.assertLess(low, 650.0)
        self.assertGreater(high, 656.0)

    def test_a_wheel_turn_over_an_empty_plot_is_no_zoom(self) -> None:
        self._wheel()
        self.assertFalse(self.plot.view_is_kept())
        self.plot.show_series(_lines(600.0))
        low, high = self._range()[1]
        self.assertLess(low, 600.0)
        self.assertGreater(high, 606.0)

    def test_the_room_for_the_step_table_is_made_only_when_the_full_view_comes_back(self) -> None:
        self.plot.show_series(_lines(600.0))
        self._wheel()
        zoomed = self._range()
        bars = [_bar(0, 0.0, 3.0), _bar(1, 3.0, 6.0)]
        rows = [StatsLine(f"ROI {i}", "#ffffff", (600.0 + i, 603.0 + i), (0.1, 0.1), (3, 3), False) for i in range(2)]
        self.plot.set_stats_table(build_stats_table(bars, rows), StatsTableSettings())
        np.testing.assert_allclose(self._range(), zoomed)  # showing the table does not move a kept zoom
        self.plot.reset_view()
        low, high = self._range()[1]
        self.assertGreater((high - low), 6.0 * 1.2)  # more than the data's 6 nm: the table has its room beyond the traces


if __name__ == "__main__":
    unittest.main()
