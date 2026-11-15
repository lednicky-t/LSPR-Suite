"""`CachedLineItem` (2026-11-16): the selection outline's lines drawn from pictures of their 4 dash phases when that is cheaper than stroking them.

The one thing that must hold: **a picture is pixel-identical to drawing the path** (also on a scaled screen, device pixel ratio 1.5, run in a
subprocess because the scale is fixed when Qt starts). The rest pins when pictures are made (only for a path with many elements, never at the first
paint of a view), when they are thrown away (new path, colour, zoom, window size) and that nothing is kept beyond the cap.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import lspr_imaging_app.panels.image.cached_line_item as cached_module
    from lspr_imaging_app.panels.image.cached_line_item import CachedLineItem
    from lspr_imaging_app.panels.image.selection_outlines import SelectionOutlines, region_outline
    from lspr_imaging_app.selection import AreaSelectionModule
    from lspr_imaging_app.selection.area_selection_module import REPLACE
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import pyqtgraph as pg  # noqa: E402

_H, _W = 350, 500


def _particles(count: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    mask = np.zeros((_H, _W), bool)
    yy, xx = np.mgrid[0:_H, 0:_W]
    for _ in range(count):
        cy, cx, r = rng.integers(10, _H - 10), rng.integers(10, _W - 10), rng.integers(3, 7)
        mask |= (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
    return mask


def _ants_pen(offset: float, color: str = "#f472b6") -> QPen:
    pen = QPen(QColor(color))
    pen.setWidth(0)
    pen.setCosmetic(True)
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern([4.0, 4.0])
    pen.setDashOffset(offset)
    return pen


def _view(path: QPainterPath, phases: int = 4) -> tuple[pg.PlotWidget, CachedLineItem]:
    widget = pg.PlotWidget()
    widget.resize(500, 350)
    widget.show()
    plot = widget.getPlotItem()
    plot.hideAxis("left")
    plot.hideAxis("bottom")
    plot.getViewBox().setRange(xRange=(0, _W), yRange=(0, _H), padding=0)
    item = CachedLineItem(phases=phases)
    item.setPen(_ants_pen(0.0))
    item.setPath(path)
    plot.addItem(item, ignoreBounds=True)
    _APP.processEvents()
    return widget, item


def _bytes(image: QImage) -> bytes:
    return bytes(image.constBits().asstring(image.sizeInBytes()))


class CachedLineItemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dense = region_outline(_particles(220))
        self.widgets: list[QtWidgets.QWidget] = []

    def tearDown(self) -> None:
        for widget in self.widgets:
            widget.close()

    def _dense_view(self) -> tuple[pg.PlotWidget, CachedLineItem]:
        widget, item = _view(self.dense)
        self.widgets.append(widget)
        return widget, item

    def _paint_cycle(self, widget: pg.PlotWidget, item: CachedLineItem, repeats: int = 2) -> None:
        for offset in (0.0, 2.0, 4.0, 6.0):
            item.setPen(_ants_pen(offset))
            for _ in range(repeats):
                widget.grab()

    # -- identity ------------------------------------------------------------------------------------------------

    def test_a_dense_outline_is_drawn_from_four_pictures_equal_to_the_direct_drawing(self) -> None:
        widget, item = self._dense_view()
        self.assertGreater(item.path().elementCount() * cached_module.PIXELS_PER_ELEMENT, 500 * 350, "this path qualifies")
        self._paint_cycle(widget, item)
        self.assertEqual(item.pictures(), 4)
        self.assertEqual(item.pictures_built, 4)
        for offset in (0.0, 2.0, 4.0, 6.0):
            item.setPen(_ants_pen(offset))
            item.caching = True
            before = item.picture_paints
            cached = _bytes(widget.grab().toImage())
            self.assertGreater(item.picture_paints, before, f"offset {offset} was copied from its picture")
            item.caching = False
            direct = _bytes(widget.grab().toImage())
            self.assertEqual(cached, direct, f"offset {offset}: the picture differs from the path drawn directly")
        item.caching = True

    def test_the_picture_is_identical_on_a_scaled_screen(self) -> None:
        """The scale is fixed when Qt starts, so this runs in a process of its own with QT_SCALE_FACTOR=1.5 (a Windows screen at 150 %)."""
        code = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(APP_SRC)!r})
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.integration import test_lspri_cached_line_item as t
            assert t._APP.primaryScreen().devicePixelRatio() == 1.5, t._APP.primaryScreen().devicePixelRatio()
            widget, item = t._view(t.region_outline(t._particles(700)))
            for offset in (0.0, 2.0, 4.0, 6.0):
                item.setPen(t._ants_pen(offset))
                for _ in range(2):
                    widget.grab()
            assert item.pictures() == 4, item.pictures()
            for offset in (0.0, 2.0, 4.0, 6.0):
                item.setPen(t._ants_pen(offset))
                item.caching = True
                cached = t._bytes(widget.grab().toImage())
                item.caching = False
                assert cached == t._bytes(widget.grab().toImage()), offset
            print("scaled screen: identical")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=240,
            env={**__import__("os").environ, "QT_SCALE_FACTOR": "1.5", "QT_QPA_PLATFORM": "offscreen"},
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("scaled screen: identical", result.stdout)

    # -- when pictures are made --------------------------------------------------------------------------------------

    def test_a_path_with_few_elements_is_never_drawn_from_pictures(self) -> None:
        rectangle = QPainterPath()
        rectangle.addRect(50.0, 40.0, 300.0, 200.0)
        widget, item = _view(rectangle)
        self.widgets.append(widget)
        self._paint_cycle(widget, item, repeats=3)
        self.assertEqual((item.pictures(), item.pictures_built, item.picture_paints), (0, 0, 0), "stroking 4 lines is cheaper than copying a picture")
        self.assertGreater(item.direct_paints, 0)

    def test_the_first_paint_of_a_view_is_direct_and_a_changed_view_throws_the_pictures_away(self) -> None:
        widget, item = self._dense_view()  # showing it painted the view once
        self.assertEqual((item.direct_paints, item.pictures(), item.pictures_built), (1, 0, 0), "the first paint of a view builds nothing")
        widget.grab()
        self.assertEqual(item.pictures(), 1, "the next paint of the same view makes the picture")
        widget.getPlotItem().getViewBox().setRange(xRange=(100, 300), yRange=(80, 200), padding=0)  # a zoom
        widget.grab()
        self.assertEqual(item.pictures(), 0, "another view: the pictures are stale")
        widget.grab()
        self.assertEqual(item.pictures(), 1, "and are made again once the view holds still")

    def test_a_new_path_or_colour_throws_the_pictures_away(self) -> None:
        widget, item = self._dense_view()
        self._paint_cycle(widget, item)
        self.assertEqual(item.pictures(), 4)
        item.setPen(_ants_pen(0.0, "#00ff00"))
        self.assertEqual(item.pictures(), 4, "dropped at the next paint, which sees another colour")
        widget.grab()
        self.assertEqual(item.pictures(), 0)
        self._paint_cycle(widget, item)
        self.assertEqual(item.pictures(), 4)
        item.setPath(region_outline(_particles(220, seed=5)))
        self.assertEqual(item.pictures(), 0, "a new path forgets at once")

    def test_the_picture_follows_the_new_colour(self) -> None:
        widget, item = self._dense_view()
        self._paint_cycle(widget, item)
        item.setPen(_ants_pen(0.0, "#00ff00"))
        for _ in range(3):
            green = widget.grab().toImage()
        item.caching = False
        self.assertEqual(_bytes(green), _bytes(widget.grab().toImage()))
        item.caching = True

    def test_nothing_is_kept_beyond_the_cap_and_the_drawing_stays_right(self) -> None:
        widget, item = self._dense_view()
        with patch.object(cached_module, "MAX_PICTURE_BYTES", 1000):
            self._paint_cycle(widget, item)
            self.assertEqual((item.pictures(), item.pictures_built), (0, 0))
            cached_off = _bytes(widget.grab().toImage())
        item.caching = False
        self.assertEqual(cached_off, _bytes(widget.grab().toImage()))

    def test_an_item_that_cycles_through_more_offsets_than_phases_does_not_grow(self) -> None:
        widget, item = self._dense_view()
        for offset in np.arange(0.0, 16.0, 0.5):
            item.setPen(_ants_pen(float(offset)))
            widget.grab()
            widget.grab()
        self.assertLessEqual(item.pictures(), 4)

    def test_outside_a_window_the_path_is_drawn_directly(self) -> None:
        """`scene.render` (exports) passes no widget."""
        widget, item = self._dense_view()
        self._paint_cycle(widget, item)
        before = (item.picture_paints, item.pictures_built)
        image = QImage(500, 350, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        widget.getPlotItem().scene().render(painter)
        painter.end()
        self.assertEqual((item.picture_paints, item.pictures_built), before)

    # -- the outlines use it -------------------------------------------------------------------------------------------

    def test_the_selection_outlines_lines_are_cached_items_with_the_right_number_of_phases(self) -> None:
        widget = pg.PlotWidget()
        self.widgets.append(widget)
        outlines = SelectionOutlines(widget.getPlotItem(), AreaSelectionModule())
        solid, dashed = outlines.past_items()
        self.assertIsInstance(solid, CachedLineItem)
        self.assertIsInstance(dashed, CachedLineItem)
        self.assertEqual(solid._phases, 1, "the solid line never changes")
        self.assertEqual(dashed._phases, 4, "the dashes repeat every 8 px and crawl 2 px a tick")

    def test_a_crawling_dense_selection_fills_its_pictures_and_keeps_drawing_what_it_drew(self) -> None:
        widget = pg.PlotWidget()
        widget.resize(500, 350)
        widget.show()
        self.widgets.append(widget)
        plot = widget.getPlotItem()
        plot.hideAxis("left")
        plot.hideAxis("bottom")
        plot.getViewBox().setRange(xRange=(0, _W), yRange=(0, _H), padding=0)
        area = AreaSelectionModule()
        outlines = SelectionOutlines(plot, area)
        outlines.set_image_shape((_H, _W))
        area.combine_region(_particles(220), REPLACE)
        outlines.update()
        outlines._ants.stop()
        solid, dashed = outlines.past_items()
        for _ in range(12):
            outlines._advance_ants()
            widget.grab()
        self.assertEqual(dashed.pictures(), 4)
        self.assertEqual(solid.pictures(), 1)
        cached = _bytes(widget.grab().toImage())
        for item in (solid, dashed):
            item.caching = False
        self.assertEqual(cached, _bytes(widget.grab().toImage()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
