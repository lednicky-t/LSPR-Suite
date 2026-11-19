"""The ROI table's Type column (plan 2026-11-19, "Rim pairs, linked pairs, the Type column"; was the Shape column).

One pictogram of the ROI's shape, with a ring round it while the ROI is measured against its own rim, filled in the colour of its kind of pair: green (a sample with a
rim or with links), amber (a sample with neither), blue (a reference some sample uses), gray (a reference nobody uses). The colours are for the Type icon only: the dot of the
first column keeps the ROI's own colour.

The pictograms are painted into an image and read back pixel by pixel; no window is shown.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_ui import get_active_theme

    from lspr_imaging_app.domain.roi_model import AreaRoi, AreaRoiDetectionSettings, RoiMask
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Circle, MaskShape, Rectangle
    from lspr_imaging_app.panels.roi_table.model import COLOR_ROLE, TYPE_ROLE, RoiTreeModel
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_ID,
        COLUMN_SHAPE,
        COLUMN_TITLES,
        ICON_CIRCLE,
        ICON_CIRCLE_RIM,
        ICON_MAP,
        ICON_MAP_RIM,
        ICON_RECTANGLE,
        ICON_RECTANGLE_RIM,
        KIND_BARE,
        KIND_LINKED,
        KIND_REFERENCE_FREE,
        KIND_REFERENCE_USED,
        KIND_RIM,
        PIXELS,
        build_roi_rows,
        sort_rows,
    )
    from lspr_imaging_app.panels.roi_table.type_icons import kind_color, paint_type_icon
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def _mask() -> MaskShape:
    return MaskShape(RoiMask(10, 10, np.ones((4, 4), dtype=bool)))


def _rois() -> list[AreaRoi]:
    return [
        AreaRoi(1, 10.0, 10.0, Circle(8.0)),  # a sample with the default rim
        AreaRoi(2, 20.0, 10.0, Circle(8.0), border=NO_BORDER),  # no rim, no links
        AreaRoi(3, 30.0, 10.0, Circle(8.0), reference_roi_ids=(5,)),  # linked (its own rim is dormant)
        AreaRoi(4, 40.0, 10.0, _mask(), role="reference"),  # nobody uses it
        AreaRoi(5, 50.0, 10.0, _mask(), role="reference"),  # used by 3
        AreaRoi(6, 60.0, 10.0, Rectangle(12.0, 6.0, 0.0), border=None),  # a rectangle with the default rim
        AreaRoi(7, 70.0, 10.0, _mask(), border=None),  # a pixel map that is a sample, with the default rim
    ]


class KindsAndIconsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = {row.roi_id: row for row in build_roi_rows(_rois(), [], AreaRoiDetectionSettings())}

    def test_the_kind_of_each_roi(self) -> None:
        self.assertEqual({i: r.kind for i, r in self.rows.items()}, {
            1: KIND_RIM, 2: KIND_BARE, 3: KIND_LINKED, 4: KIND_REFERENCE_FREE, 5: KIND_REFERENCE_USED, 6: KIND_RIM, 7: KIND_RIM,
        })

    def test_a_ring_is_drawn_only_while_the_roi_is_measured_against_its_own_rim(self) -> None:
        self.assertEqual(self.rows[1].type_icon, ICON_CIRCLE_RIM)
        self.assertEqual(self.rows[6].type_icon, ICON_RECTANGLE_RIM)
        self.assertEqual(self.rows[7].type_icon, ICON_MAP_RIM)
        self.assertEqual(self.rows[2].type_icon, ICON_CIRCLE, "no rim")
        self.assertEqual(self.rows[3].type_icon, ICON_CIRCLE, "linked: its rim is dormant, the link is what counts")
        self.assertEqual(self.rows[4].type_icon, ICON_MAP, "a reference has no rim")
        self.assertEqual(self.rows[5].type_icon, ICON_MAP)

    def test_a_rectangle_reference_shows_the_plain_rectangle(self) -> None:
        rows = build_roi_rows([AreaRoi(1, 1.0, 1.0, Rectangle(10.0, 6.0, 0.0), role="reference")], [], AreaRoiDetectionSettings())
        self.assertEqual((rows[0].type_icon, rows[0].kind), (ICON_RECTANGLE, KIND_REFERENCE_FREE))

    def test_a_link_to_something_that_is_not_a_reference_does_not_make_a_sample_linked(self) -> None:
        rows = build_roi_rows(
            [AreaRoi(1, 1.0, 1.0, Circle(8.0), border=NO_BORDER, reference_roi_ids=(2,)), AreaRoi(2, 9.0, 1.0, Circle(8.0))], [], AreaRoiDetectionSettings()
        )
        self.assertEqual(rows[0].kind, KIND_BARE)

    def test_the_column_is_called_type(self) -> None:
        self.assertEqual(COLUMN_TITLES[COLUMN_SHAPE], "Type")

    def test_the_type_column_sorts_the_kinds_together(self) -> None:
        order = [r.roi_id for r in sort_rows(list(self.rows.values()), COLUMN_SHAPE, False)]
        kinds = [self.rows[i].kind for i in order]
        self.assertEqual(kinds, [KIND_RIM, KIND_RIM, KIND_RIM, KIND_LINKED, KIND_BARE, KIND_REFERENCE_USED, KIND_REFERENCE_FREE])


class ModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = RoiTreeModel()
        self.model.set_content(_rois(), [], AreaRoiDetectionSettings(), PIXELS)

    def _index(self, roi_id: int, column: int):
        return self.model.index(roi_id - 1, column)

    def test_the_cell_says_the_type_and_the_tooltip_adds_what_it_is_measured_against(self) -> None:
        self.assertEqual(self._index(1, COLUMN_SHAPE).data(Qt.ItemDataRole.DisplayRole), "Circle with rim")
        self.assertEqual(self._index(6, COLUMN_SHAPE).data(Qt.ItemDataRole.DisplayRole), "Rectangle with rim")
        self.assertEqual(self._index(7, COLUMN_SHAPE).data(Qt.ItemDataRole.DisplayRole), "Pixel map with rim")
        self.assertEqual(self._index(4, COLUMN_SHAPE).data(Qt.ItemDataRole.DisplayRole), "Pixel map")
        self.assertEqual(self._index(1, COLUMN_SHAPE).data(Qt.ItemDataRole.ToolTipRole), "Circle with rim. Measured against its own rim.")
        self.assertEqual(self._index(2, COLUMN_SHAPE).data(Qt.ItemDataRole.ToolTipRole), "Circle. No reference: its results are missing.")
        self.assertIn("Measured against reference R5", self._index(3, COLUMN_SHAPE).data(Qt.ItemDataRole.ToolTipRole))
        self.assertTrue(self._index(5, COLUMN_SHAPE).data(Qt.ItemDataRole.ToolTipRole).startswith("Pixel map. Reference ROI, used by sample 3."))

    def test_the_type_role_carries_the_pictogram_and_the_kind(self) -> None:
        self.assertEqual(self._index(2, COLUMN_SHAPE).data(TYPE_ROLE), (ICON_CIRCLE, KIND_BARE))
        self.assertEqual(self._index(5, COLUMN_SHAPE).data(TYPE_ROLE), (ICON_MAP, KIND_REFERENCE_USED))

    def test_the_dot_of_the_first_column_keeps_the_rois_own_colour(self) -> None:
        rois = _rois()
        rois[1].sample_color_hex = "#123456"  # an amber (bare) sample with a colour of its own
        model = RoiTreeModel()
        model.set_content(rois, [], AreaRoiDetectionSettings(), PIXELS)
        self.assertEqual(model.index(1, COLUMN_ID).data(COLOR_ROLE), "#123456")
        self.assertEqual(model.index(1, COLUMN_SHAPE).data(TYPE_ROLE), (ICON_CIRCLE, KIND_BARE))  # the Type icon is amber whatever the dot is


class PaintingTest(unittest.TestCase):
    """Each pictogram painted into a 40 x 40 image: the pixel at the middle of a filled shape wears the kind's colour; a rim is the reference blue."""

    SIZE = 40
    CENTRE = QPointF(20.0, 20.0)

    def _paint(self, icon: str, kind: str) -> QImage:
        image = QImage(self.SIZE, self.SIZE, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_type_icon(painter, self.CENTRE, icon, kind, get_active_theme())
        painter.end()
        return image

    def test_a_filled_shape_wears_the_colour_of_its_kind(self) -> None:
        theme = get_active_theme()
        for kind in (KIND_RIM, KIND_LINKED, KIND_BARE, KIND_REFERENCE_USED, KIND_REFERENCE_FREE):
            image = self._paint(ICON_CIRCLE, kind)
            self.assertEqual(QColor(image.pixel(20, 20)).name(), QColor(kind_color(kind, theme)).name(), kind)

    def test_the_four_colours_are_green_amber_blue_gray(self) -> None:
        theme = get_active_theme()
        self.assertEqual(kind_color(KIND_RIM, theme), theme.accent_green)
        self.assertEqual(kind_color(KIND_LINKED, theme), theme.accent_green)
        self.assertEqual(kind_color(KIND_BARE, theme), theme.accent_gold)
        self.assertEqual(kind_color(KIND_REFERENCE_USED, theme), theme.accent_blue)
        self.assertEqual(kind_color(KIND_REFERENCE_FREE, theme), theme.text_dim)
        self.assertEqual(len({theme.accent_green, theme.accent_gold, theme.accent_blue, theme.text_dim}), 4)

    def test_a_rim_icon_has_a_blue_ring_round_a_filled_shape(self) -> None:
        theme = get_active_theme()
        for icon, ring_pixel in ((ICON_CIRCLE_RIM, (27, 20)), (ICON_RECTANGLE_RIM, (28, 20)), (ICON_MAP_RIM, None)):
            image = self._paint(icon, KIND_RIM)
            self.assertEqual(QColor(image.pixel(20, 20)).name(), QColor(theme.accent_green).name(), icon)  # the shape is green
            blue = QColor(theme.accent_blue)
            found = any(
                abs(QColor(image.pixel(x, y)).red() - blue.red()) < 40 and abs(QColor(image.pixel(x, y)).blue() - blue.blue()) < 40 and QColor(image.pixel(x, y)).alpha() > 200
                for x in range(self.SIZE)
                for y in range(self.SIZE)
            )
            self.assertTrue(found, f"{icon}: no blue ring")
            if ring_pixel is not None:
                self.assertGreater(QColor(image.pixel(*ring_pixel)).alpha(), 100, f"{icon}: nothing where the ring is")

    def test_a_plain_icon_has_no_blue(self) -> None:
        theme = get_active_theme()
        blue = QColor(theme.accent_blue)
        for icon in (ICON_CIRCLE, ICON_RECTANGLE, ICON_MAP):
            image = self._paint(icon, KIND_BARE)
            for x in range(self.SIZE):
                for y in range(self.SIZE):
                    pixel = QColor(image.pixel(x, y))
                    self.assertFalse(pixel.alpha() > 200 and abs(pixel.blue() - blue.blue()) < 12 and abs(pixel.red() - blue.red()) < 12 and abs(pixel.green() - blue.green()) < 12, icon)


if __name__ == "__main__":
    unittest.main()
