"""`region_outline` (2026-11-16): the outline of a pixel selection is now traced along the pixel edges. The old way (one rectangle per run of
pixels, merged by `QPainterPath.simplified`) took 200-900 ms on the GUI thread at every release of a selection stroke, 89 % of it in
`simplified`; the old way stays here as the oracle: both must fill exactly the same pixels, which are the pixels of the mask.

Also pins the ants' pen: width 0 (Qt's cosmetic hairline). With any other width `QGraphicsPathItem` runs a stroker over the whole path to measure
its bounding rect every time the pen changes, 6.5 ms per ants tick on a 300-particle outline.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.selection_outlines import SelectionOutlines, region_outline
    from lspr_imaging_app.panels.image.selection_palette import COLORS
    from lspr_imaging_app.selection import AreaSelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import pyqtgraph as pg  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath  # noqa: E402


def _old_outline(editable: np.ndarray) -> QPainterPath:
    """The implementation this replaced: a rectangle per run of selected pixels in a row, merged by `simplified`."""
    path = QPainterPath()
    rows = np.flatnonzero(editable.any(axis=1))
    if rows.size == 0:
        return path
    for row in range(int(rows[0]), int(rows[-1]) + 1):
        line = editable[row].astype(np.int8)
        edges = np.flatnonzero(np.diff(np.concatenate(([0], line, [0]))))
        for start, stop in zip(edges[0::2], edges[1::2]):
            path.addRect(float(start), float(row), float(stop - start), 1.0)
    return path.simplified()


def _fill(path: QPainterPath, shape: tuple[int, int]) -> np.ndarray:
    """The pixels a path fills (its own, even-odd, fill rule; no anti-aliasing)."""
    image = QImage(shape[1], shape[0], QImage.Format.Format_Grayscale8)
    image.fill(0)
    painter = QPainter(image)
    painter.fillPath(path, QColor(255, 255, 255))
    painter.end()
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    return np.frombuffer(bits, dtype=np.uint8).reshape(shape[0], image.bytesPerLine())[:, : shape[1]] > 127


def _signed_area(polygon) -> float:
    xs = np.array([polygon.at(i).x() for i in range(polygon.count())])
    ys = np.array([polygon.at(i).y() for i in range(polygon.count())])
    return 0.5 * float(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1)))


def _cases() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(7)
    cases: dict[str, np.ndarray] = {
        "one pixel": np.pad(np.ones((1, 1), dtype=bool), 5),
        "a bar": np.pad(np.ones((1, 9), dtype=bool), 4),
        "full image": np.ones((20, 30), dtype=bool),
        "checkerboard": (np.indices((12, 14)).sum(axis=0) % 2).astype(bool),
        "two pixels meeting at a corner": np.pad(np.eye(2, dtype=bool), 3),
        "a ring": np.pad(np.pad(np.ones((3, 3), dtype=bool), 2) & ~np.pad(np.ones((1, 1), dtype=bool), 3), 3),
        "a ring touching the border": np.pad(np.ones((5, 5), dtype=bool) & ~np.pad(np.ones((1, 1), dtype=bool), 2), 0),
    }
    for density in (0.2, 0.5, 0.8):
        for k in range(3):
            cases[f"random, {density:.0%} selected, #{k}"] = rng.random((40, 55)) < density
    blobs = np.zeros((60, 80), dtype=bool)
    rows, cols = np.mgrid[:60, :80]
    for _ in range(25):
        cy, cx, radius = rng.integers(0, 60), rng.integers(0, 80), rng.integers(2, 9)
        blobs |= (rows - cy) ** 2 + (cols - cx) ** 2 <= radius * radius
    cases["overlapping blobs, some touching the border"] = blobs
    return cases


class RegionOutlineTest(unittest.TestCase):
    def test_the_traced_outline_fills_the_pixels_of_the_mask_and_the_old_one_did_too(self) -> None:
        for name, mask in _cases().items():
            with self.subTest(case=name):
                traced = _fill(region_outline(mask), mask.shape)
                np.testing.assert_array_equal(traced, mask)
                np.testing.assert_array_equal(traced, _fill(_old_outline(mask), mask.shape))

    def test_an_empty_mask_has_an_empty_outline(self) -> None:
        self.assertTrue(region_outline(np.zeros((20, 30), dtype=bool)).isEmpty())

    def test_the_full_image_is_one_rectangle_along_the_border(self) -> None:
        path = region_outline(np.ones((20, 30), dtype=bool))
        self.assertEqual(len(path.toSubpathPolygons()), 1)
        rect = path.boundingRect()
        self.assertEqual((rect.left(), rect.top(), rect.width(), rect.height()), (0.0, 0.0, 30.0, 20.0))

    def test_a_ring_is_two_polygons_and_the_hole_runs_the_other_way_round(self) -> None:
        ring = np.pad(np.pad(np.ones((3, 3), dtype=bool), 2) & ~np.pad(np.ones((1, 1), dtype=bool), 3), 3)
        polygons = region_outline(ring).toSubpathPolygons()
        self.assertEqual(len(polygons), 2)
        areas = sorted(_signed_area(polygon) for polygon in polygons)
        self.assertLess(areas[0], 0.0)
        self.assertGreater(areas[1], 0.0)
        self.assertAlmostEqual(areas[0] + areas[1], float(ring.sum()), places=6)  # the area between them is the ring's pixels

    def test_pixels_that_meet_only_at_a_corner_are_separate_polygons(self) -> None:
        self.assertEqual(len(region_outline(np.pad(np.eye(2, dtype=bool), 3)).toSubpathPolygons()), 2)

    def test_the_corners_are_the_only_vertices(self) -> None:
        """Runs are merged: a w x h block of pixels is a rectangle with four corners, not an edge per pixel."""
        block = np.pad(np.ones((7, 11), dtype=bool), 4)
        (polygon,) = region_outline(block).toSubpathPolygons()
        self.assertLessEqual(polygon.count(), 5)  # 4 corners (a closed subpath may repeat the first)
        rect = polygon.boundingRect()
        self.assertEqual((rect.left(), rect.top(), rect.width(), rect.height()), (4.0, 4.0, 11.0, 7.0))

    def test_every_subpath_is_closed(self) -> None:
        """A stroke skips the closing edge of a subpath that is not explicitly closed."""
        mask = _cases()["random, 50% selected, #0"]
        path = region_outline(mask)
        starts = [i for i in range(path.elementCount()) if path.elementAt(i).isMoveTo()]
        polygons = path.toSubpathPolygons()
        self.assertEqual(len(starts), len(polygons))
        for polygon in polygons:
            self.assertEqual(polygon.first(), polygon.last(), "toSubpathPolygons repeats the first point of a closed subpath")


class AntsPenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = pg.PlotItem()  # owns the outline items: it must outlive them
        self.outlines = SelectionOutlines(self.plot, AreaSelectionModule())

    def test_every_outline_uses_qts_hairline_pen(self) -> None:
        outlines = self.outlines
        for role in COLORS:
            for item in (outlines._items[role][0], outlines._items[role][1]):
                with self.subTest(role=role):
                    self.assertEqual(item.pen().width(), 0, "width 0: see the module docstring; any other width costs a stroker pass per pen change")
                    self.assertTrue(item.pen().isCosmetic())

    def test_an_ants_tick_leaves_a_hidden_outline_alone(self) -> None:
        outlines = self.outlines
        _solid, ants = outlines.past_items()
        self.assertFalse(ants.isVisible())
        offset = ants.pen().dashOffset()
        outlines._advance_ants()
        self.assertEqual(ants.pen().dashOffset(), offset)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
