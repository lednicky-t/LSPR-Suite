"""Tests for `SampleCirclesItem`: all ROI sample circles as one painted item.

Checks what is really painted (pixels of a grabbed plot), not only stored state, plus a
speed guard on the reason the item exists (hundreds of colours must not cost hundreds of
graphics items).
"""

from __future__ import annotations

import sys
import time
import unittest

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg

    from lspr_imaging_app.panels.image.sample_circles_item import SampleCirclesItem
    from lspr_imaging_app.roi.overlay_geometry import circle_outlines, circle_points
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

POINTS = 48


def _circles(centers, diameter=20.0):
    centers = np.asarray(centers, dtype=float)
    return circle_points(centers, np.full(len(centers), diameter), np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), n_points=POINTS)


class _Canvas:
    """A plot widget with a fixed 1 data unit = 1 pixel view, to read pixels back."""

    def __init__(self) -> None:
        self.widget = pg.PlotWidget()
        self.widget.resize(220, 120)
        self.widget.hideAxis("left")
        self.widget.hideAxis("bottom")
        self.widget.setBackground("k")
        self.plot = self.widget.getPlotItem()
        self.plot.vb.setAspectLocked(False)
        self.plot.vb.setMouseEnabled(False, False)
        self.plot.vb.setRange(xRange=(0, 220), yRange=(0, 120), padding=0.0)
        self.widget.show()
        _APP.processEvents()

    def pixel(self, x: float, y: float):
        """The colour at data position (x, y)."""
        _APP.processEvents()
        image = self.widget.grab().toImage()
        point = self.plot.vb.mapViewToScene(pg.Point(x, y))
        view_point = self.widget.mapFromScene(point)
        ratio = image.devicePixelRatio()  # the grab is scaled on a high-DPI screen
        return image.pixelColor(round(view_point.x() * ratio), round(view_point.y() * ratio))

    def close(self) -> None:
        self.widget.close()


class SampleCirclesItemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas = _Canvas()
        self.item = SampleCirclesItem(1.5)
        self.canvas.plot.addItem(self.item)

    def tearDown(self) -> None:
        self.canvas.close()

    def test_each_circle_is_painted_in_its_own_colour_outline_and_fill(self) -> None:
        xs, ys = _circles([(60.0, 60.0), (150.0, 60.0)])
        self.item.set_circles(xs, ys, ["#ff0000", "#0000ff"])
        self.item.set_style(1.0, 0.5)
        left_fill, right_fill = self.canvas.pixel(60, 60), self.canvas.pixel(150, 60)
        self.assertGreater(left_fill.red(), left_fill.blue(), "left circle is filled red")
        self.assertGreater(right_fill.blue(), right_fill.red(), "right circle is filled blue")
        self.assertGreater(left_fill.red(), 100)  # half-transparent red on black
        self.assertLess(left_fill.red(), 160)
        outline = self.canvas.pixel(70, 60)  # on the left circle's outline (radius 10)
        self.assertGreater(outline.red(), 230, "the outline is opaque red")
        self.assertEqual(self.canvas.pixel(105, 60).name(), "#000000", "nothing between the circles")

    def test_transparency_and_visibility_are_applied(self) -> None:
        xs, ys = _circles([(60.0, 60.0)])
        self.item.set_circles(xs, ys, ["#00ff00"])
        self.item.set_style(0.4, 0.0)  # no fill at all
        self.assertEqual(self.canvas.pixel(60, 60).name(), "#000000", "fill alpha 0: the inside stays empty")
        self.assertAlmostEqual(self.item.outline_color("#00ff00").alphaF(), 0.4, delta=0.01)
        self.item.set_style(1.0, 1.0)
        self.assertGreater(self.canvas.pixel(60, 60).green(), 240)
        self.item.setVisible(False)
        self.assertEqual(self.canvas.pixel(60, 60).name(), "#000000")

    def test_the_queries_describe_what_was_set(self) -> None:
        xs, ys = _circles([(20.0, 20.0), (60.0, 20.0), (100.0, 20.0)])
        self.item.set_circles(xs, ys, ["#112233", "#445566", "#112233"])
        self.assertEqual(self.item.colors(), ["#112233", "#445566"])
        self.assertEqual((self.item.circle_count(), self.item.circle_count("#112233"), self.item.circle_count("#999999")), (3, 2, 0))
        flat_x, _ = self.item.outline_xy("#112233")
        self.assertEqual(len(flat_x), 2 * POINTS + 1)  # two circles, one NaN between them
        self.assertEqual(int(np.isfinite(flat_x).sum()), 2 * POINTS)
        self.assertEqual(len(self.item.outline_xy()[0]), 3 * POINTS + 2)
        self.item.clear()
        self.assertEqual((self.item.circle_count(), self.item.colors()), (0, []))
        self.assertEqual(len(self.item.outline_xy()[0]), 0)

    def test_circle_points_are_the_rows_of_circle_outlines(self) -> None:
        centers = np.array([[10.0, 10.0], [50.0, 30.0]])
        diameters = np.array([8.0, 12.0])
        affine = np.array([[1.1, 0.05, 0.0], [-0.05, 0.9, 0.0]])
        flat_x, flat_y = circle_outlines(centers, diameters, affine, n_points=POINTS)
        rows_x, rows_y = circle_points(centers, diameters, affine, n_points=POINTS)
        self.assertEqual(rows_x.shape, (2, POINTS))
        expected_x = np.append(rows_x, np.full((2, 1), np.nan), axis=1).ravel()[:-1]
        np.testing.assert_allclose(flat_x, expected_x)
        np.testing.assert_allclose(flat_y, np.append(rows_y, np.full((2, 1), np.nan), axis=1).ravel()[:-1])

    def test_it_does_not_take_mouse_clicks_from_the_items_below(self) -> None:
        self.assertEqual(self.item.acceptedMouseButtons(), Qt.MouseButton.NoButton)

    def test_the_outline_width_can_change_and_a_thinner_line_paints_fewer_pixels(self) -> None:
        xs, ys = _circles([(60.0, 60.0)], diameter=60.0)
        self.item.set_circles(xs, ys, ["#ffffff"])
        self.item.set_style(1.0, 0.0)  # outline only

        def lit_pixels() -> int:
            _APP.processEvents()
            image = self.canvas.widget.grab().toImage()
            return sum(1 for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).red() > 30)

        thick = lit_pixels()
        self.item.set_width(1.0)
        self.assertEqual(self.item.width, 1.0)
        self.assertLess(lit_pixels(), thick)
        self.item.set_width(1.5)
        self.assertEqual(lit_pixels(), thick)

    def test_an_empty_item_paints_nothing_and_has_no_extent(self) -> None:
        self.assertTrue(self.item.boundingRect().isEmpty())
        self.assertEqual(self.canvas.pixel(60, 60).name(), "#000000")

    def test_hundreds_of_colours_cost_one_item_and_milliseconds(self) -> None:
        count = 400
        centers = np.column_stack((np.linspace(10, 210, count), np.linspace(10, 110, count)))
        xs, ys = _circles(centers, diameter=6.0)
        colors = [f"#{(k * 37) % 256:02x}{(k * 91) % 256:02x}{(k * 53) % 256:02x}" for k in range(count)]
        items_before = len(self.canvas.plot.items)
        started = time.perf_counter()
        for _ in range(10):
            self.item.set_circles(xs, ys, colors)
        per_update_ms = (time.perf_counter() - started) / 10 * 1000
        self.assertEqual(len(self.canvas.plot.items), items_before)  # no item per colour
        self.assertLess(per_update_ms, 10.0, f"{per_update_ms:.2f} ms to update 400 circles of ~400 colours")  # ~0.3 ms typical


if __name__ == "__main__":
    unittest.main()
