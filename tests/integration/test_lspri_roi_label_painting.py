"""Tests for the ROI label painter (`panels/image/roi_label_overlay.py`): labels are cached bitmaps, placed where
the circle's right edge is, only on screen, and cheap. Pixels are read back from a grabbed plot widget."""

from __future__ import annotations

import sys
import time
import unittest

import numpy as np
from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg

    from lspr_imaging_app.panels.image.roi_label_overlay import RoiLabelItem
    from tests._qt_fonts import load_system_fonts
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

load_system_fonts()  # offscreen Qt on Windows has no fonts; where the text ends depends on its measured width


class RoiLabelPaintingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = pg.PlotWidget()
        self.widget.resize(420, 300)
        self.widget.hideAxis("left")
        self.widget.hideAxis("bottom")
        self.widget.setBackground("k")
        self.vb = self.widget.getPlotItem().vb
        self.vb.setMouseEnabled(False, False)
        self.vb.setRange(xRange=(0, 420), yRange=(0, 300), padding=0.0)
        self.widget.show()
        _APP.processEvents()
        self.item = RoiLabelItem(self.vb)

    def tearDown(self) -> None:
        self.widget.close()

    def _lit_pixels(self) -> tuple[np.ndarray, float]:
        """Boolean image of bright pixels in the grabbed widget, and its device pixel ratio."""
        _APP.processEvents()
        image = self.widget.grab().toImage().convertToFormat(self.widget.grab().toImage().Format.Format_ARGB32)
        pointer = image.constBits()
        pointer.setsize(image.sizeInBytes())
        data = np.frombuffer(pointer, dtype=np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)[:, : image.width()]
        return data[..., :3].max(axis=2) > 140, image.devicePixelRatio()

    def _anchor_in_widget(self, x: float, y: float) -> tuple[float, float]:
        point = self.widget.mapFromScene(self.vb.mapViewToScene(pg.Point(x, y)))
        return float(point.x()), float(point.y())

    def test_a_label_sits_just_right_of_its_circles_edge_and_is_vertically_centred(self) -> None:
        self.item.set_labels([(100.0, 120.0, 10.0, "42")])
        lit, ratio = self._lit_pixels()
        rows, cols = np.nonzero(lit)
        self.assertGreater(len(rows), 15, "the text is drawn")
        anchor_x, anchor_y = self._anchor_in_widget(110.0, 120.0)  # right edge of the circle
        left, top, bottom = cols.min() / ratio, rows.min() / ratio, rows.max() / ratio
        self.assertAlmostEqual(left, anchor_x + 4.0, delta=3.0)  # the 4 px gap
        self.assertAlmostEqual((top + bottom) / 2.0, anchor_y, delta=4.0)  # centred on the circle's height

    def _text_box_around_circle(self, placement: str, radius: float = 20.0) -> tuple[float, float, float, float, float, float, float]:
        """Paint one label at ``placement`` of a circle of ``radius`` and return (left, top, right, bottom) of the lit
        text, then the circle's centre x, y and radius, all in logical widget pixels."""
        self.item.set_placement(placement)
        self.item.set_labels([(200.0, 150.0, radius, "42")])
        lit, ratio = self._lit_pixels()
        rows, cols = np.nonzero(lit)
        self.assertGreater(len(rows), 15, f"{placement}: the text is drawn")
        centre_x, centre_y = self._anchor_in_widget(200.0, 150.0)
        edge_x, _ = self._anchor_in_widget(200.0 + radius, 150.0)
        return (
            cols.min() / ratio, rows.min() / ratio, (cols.max() + 1) / ratio, (rows.max() + 1) / ratio,
            centre_x, centre_y, abs(edge_x - centre_x),
        )

    def test_each_side_puts_the_text_just_outside_the_circle(self) -> None:
        gap = 4.0
        left, top, right, bottom, cx, cy, r = self._text_box_around_circle("left")
        self.assertAlmostEqual(right, cx - r - gap, delta=3.0)
        self.assertAlmostEqual((top + bottom) / 2.0, cy, delta=4.0)
        left, top, right, bottom, cx, cy, r = self._text_box_around_circle("top")
        self.assertAlmostEqual(bottom, cy - r - gap, delta=4.0)
        self.assertAlmostEqual((left + right) / 2.0, cx, delta=3.0)
        left, top, right, bottom, cx, cy, r = self._text_box_around_circle("bottom")
        self.assertAlmostEqual(top, cy + r + gap, delta=4.0)
        self.assertAlmostEqual((left + right) / 2.0, cx, delta=3.0)
        left, top, right, bottom, cx, cy, r = self._text_box_around_circle("right")
        self.assertAlmostEqual(left, cx + r + gap, delta=3.0)

    def test_the_middle_cell_centres_the_text_on_the_circle(self) -> None:
        left, top, right, bottom, cx, cy, _r = self._text_box_around_circle("center")
        self.assertAlmostEqual((left + right) / 2.0, cx, delta=3.0)
        self.assertAlmostEqual((top + bottom) / 2.0, cy, delta=4.0)

    def test_a_corner_cell_puts_the_text_off_that_corner_clear_of_the_circle(self) -> None:
        reach = (20.0 + 4.0) / 2.0**0.5
        left, top, right, bottom, cx, cy, r = self._text_box_around_circle("top-left")
        self.assertAlmostEqual(r, 20.0, delta=0.5, msg="one view unit is one pixel in this setup")
        self.assertAlmostEqual(right, cx - reach, delta=3.0)
        self.assertAlmostEqual(bottom, cy - reach, delta=4.0)
        left, top, right, bottom, cx, cy, r = self._text_box_around_circle("bottom-right")
        self.assertAlmostEqual(left, cx + reach, delta=3.0)
        self.assertAlmostEqual(top, cy + reach, delta=4.0)

    def test_the_default_place_is_right_and_a_bad_place_is_refused(self) -> None:
        self.assertEqual(self.item.placement(), "right")
        with self.assertRaises(ValueError):
            self.item.set_placement("diagonal")
        self.assertEqual(self.item.placement(), "right")

    def test_labels_outside_the_view_are_not_painted(self) -> None:
        self.item.set_labels([(-500.0, 100.0, 5.0, "7"), (100.0, 5000.0, 5.0, "8"), (900.0, 100.0, 5.0, "9")])
        lit, _ratio = self._lit_pixels()
        self.assertEqual(int(lit.sum()), 0)

    def test_one_bitmap_per_distinct_text_however_often_it_is_painted(self) -> None:
        self.item.set_labels([(40.0 + 30 * k, 100.0, 5.0, "5" if k % 2 else "6") for k in range(10)])
        self._lit_pixels()
        self._lit_pixels()
        self.assertEqual(len(self.item._bitmaps), 2)

    def test_labels_follow_zoom_and_pan_but_not_the_text_size(self) -> None:
        self.item.set_labels([(100.0, 120.0, 10.0, "42")])
        before, _ratio = self._lit_pixels()
        self.vb.setRange(xRange=(0, 210), yRange=(0, 150), padding=0.0)  # zoom in 2x: the label moves, text stays the same size
        after, _ratio = self._lit_pixels()
        rows_b, cols_b = np.nonzero(before)
        rows_a, cols_a = np.nonzero(after)
        self.assertAlmostEqual(cols_a.max() - cols_a.min(), cols_b.max() - cols_b.min(), delta=2)
        self.assertAlmostEqual(rows_a.max() - rows_a.min(), rows_b.max() - rows_b.min(), delta=2)
        self.assertGreater(cols_a.min(), cols_b.min() + 50)  # but its place followed the 2x zoom

    def test_no_labels_means_nothing_painted_and_the_item_is_hidden(self) -> None:
        self.item.set_labels([])
        self.assertFalse(self.item.isVisible())
        lit, _ratio = self._lit_pixels()
        self.assertEqual(int(lit.sum()), 0)

    def test_a_few_hundred_labels_paint_in_milliseconds(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage, QPainter

        self.item.set_labels([(10.0 + (k % 20) * 20.0, 10.0 + (k // 20) * 14.0, 5.0, str(k + 1)) for k in range(300)])
        image = QImage(420, 300, QImage.Format.Format_ARGB32_Premultiplied)
        timings = []
        for _ in range(8):
            image.fill(Qt.GlobalColor.black)
            painter = QPainter(image)
            started = time.perf_counter()
            self.item.paint(painter)
            timings.append((time.perf_counter() - started) * 1000.0)
            painter.end()
        self.assertLess(sorted(timings)[len(timings) // 2], 15.0, f"{sorted(timings)} ms")  # ~3 ms typical; the direct drawing took ~30


if __name__ == "__main__":
    unittest.main()
