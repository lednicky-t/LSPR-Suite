"""LSPRi selection tools, 2026-11-16: (1) the three outlines of a drag (past = the selection as it stands, present = the shape being dragged, result =
what Subtract / Intersect would leave) and which of them each combine mode shows, (2) the + / - / x badge beside the pointer and the mode button that
follows the held Shift / Alt keys.

Real `QApplication` and widgets, driven by direct method calls (no screen coordinates); the keyboard is faked by patching
`QGuiApplication.queryKeyboardModifiers` where the badge reads it.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        ImageTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.combine_badge import CROSS, MINUS, PLUS, _BadgeItem, symbol_for
    from lspr_imaging_app.panels.image.combine_mode_icons import combine_mode_icon
    from lspr_imaging_app.panels.image.selection_outlines import closed_path, polygon_path
    from lspr_imaging_app.panels.image.selection_palette import (
        COLORS,
        OUTLINES_FOR_MODE,
        PAST,
        PRESENT,
        RESULT,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
    from lspr_imaging_app.selection.area_selection_module import ADD, INTERSECT, REPLACE, SUBTRACT
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_SHAPE = (64, 80)
_NONE = Qt.KeyboardModifier.NoModifier
_SHIFT = Qt.KeyboardModifier.ShiftModifier
_ALT = Qt.KeyboardModifier.AltModifier


def _pump_until(condition, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    frame = np.full(_SHAPE, 150.0, dtype=np.float32)
    frame[10:20, 10:20] = 3000.0
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), frame)
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


def _render(path: QPainterPath) -> np.ndarray:
    """The pixels a path fills (its own, even-odd, fill rule; no anti-aliasing), as a boolean image of the test's frame."""
    image = QImage(_SHAPE[1], _SHAPE[0], QImage.Format.Format_Grayscale8)
    image.fill(0)
    painter = QPainter(image)
    painter.fillPath(path, QColor(255, 255, 255))
    painter.end()
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bits, dtype=np.uint8).reshape(_SHAPE[0], image.bytesPerLine())
    return rows[:, : _SHAPE[1]] > 127


class PaletteTableTest(unittest.TestCase):
    def test_what_each_mode_shows_while_dragging(self) -> None:
        """Maintainer, 2026-11-16: Subtract shows only how the old selection is cut; Add shows the old and the new one."""
        self.assertEqual(OUTLINES_FOR_MODE[SUBTRACT], (RESULT,))
        self.assertEqual(OUTLINES_FOR_MODE[ADD], (PAST, PRESENT))
        self.assertEqual(OUTLINES_FOR_MODE[REPLACE], (PAST, PRESENT))
        self.assertEqual(OUTLINES_FOR_MODE[INTERSECT], (PAST, PRESENT, RESULT))

    def test_two_looks_two_colours_and_the_badge_keeps_its_blue_and_orange(self) -> None:
        """The image has two colours (pink, green): the result outline wears the pink. The blue and the orange are the badge's rings only."""
        from lspr_imaging_app.panels.image.selection_palette import REMOVED_COLOR, RESULT_COLOR

        self.assertEqual(set(COLORS), {PAST, PRESENT})
        self.assertEqual(len(set(COLORS.values())), 2)
        self.assertEqual(RESULT_COLOR, "#38bdf8")
        self.assertEqual(REMOVED_COLOR, "#f97316")


class SymbolTableTest(unittest.TestCase):
    def test_each_combine_mode_has_its_symbol_and_replace_has_none(self) -> None:
        """The same for every tool, the brush included (it was special until 2026-11-16: no "+", Intersect and Replace shown as Add)."""
        self.assertEqual(symbol_for(ADD), PLUS)
        self.assertEqual(symbol_for(SUBTRACT), MINUS)
        self.assertEqual(symbol_for(INTERSECT), CROSS)
        self.assertIsNone(symbol_for(REPLACE))


class BadgeItemTest(unittest.TestCase):
    def _paint(self, symbol: str) -> QImage:
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        image.fill(0)
        item = _BadgeItem()
        item.set_symbol(symbol)
        painter = QPainter(image)
        item.paint(painter)
        painter.end()
        return image

    def test_the_badge_is_an_open_ring_with_no_fill(self) -> None:
        """"In a transparent circle" (maintainer): the inside of the ring away from the symbol shows the image."""
        image = self._paint(MINUS)
        self.assertGreater(image.pixelColor(18, 14).alpha(), 150, "the ring is drawn (ring centre (14, 14), radius 4)")
        for x, y in ((15, 15), (12, 12), (15, 12), (12, 15)):  # in the ring, off the horizontal / vertical strokes
            self.assertLess(image.pixelColor(x, y).alpha(), 60, (x, y))

    def test_the_badge_is_small(self) -> None:
        self.assertLessEqual(_BadgeItem().boundingRect().width(), 15.0, "half of the first version (a 15 px disc)")

    def test_the_colour_follows_the_mode(self) -> None:
        def has(image: QImage, test) -> bool:
            return any(test(image.pixelColor(x, y)) for x in range(32) for y in range(32) if image.pixelColor(x, y).alpha() > 200)

        self.assertTrue(has(self._paint(MINUS), lambda c: c.red() > 200 and c.green() < 180 and c.blue() < 100), "taken away is orange")
        self.assertTrue(has(self._paint(PLUS), lambda c: c.blue() > 200 and c.red() < 120), "add is blue")
        self.assertTrue(has(self._paint(CROSS), lambda c: c.blue() > 200 and c.red() < 120), "intersect is blue")


def _luminance(color: QColor) -> float:
    channels = []
    for value in (color.red(), color.green(), color.blue()):
        c = value / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class VennIconTest(unittest.TestCase):
    """The mode icons (maintainer, 2026-11-16): the circles keep their colours (pink = the selection now, green = the new region), the fill that
    was blue for "ends up selected" is grey (then a step lighter), and what is taken away has no fill (it was orange): the plain background."""

    def _picture(self, how: str) -> QImage:
        return combine_mode_icon(how).pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage()

    def test_the_grey_fill_contrasts_with_both_circle_lines_and_with_the_toolbars(self) -> None:
        from lspr_imaging_app.panels.image.selection_palette import ICON_SELECTED_FILL, PAST_COLOR, PRESENT_COLOR

        fill = QColor(ICON_SELECTED_FILL)
        self.assertGreaterEqual(_contrast(fill, QColor(PAST_COLOR)), 2.0, "against the pink line (the blue fill had 1.2)")
        self.assertGreaterEqual(_contrast(fill, QColor(PRESENT_COLOR)), 3.0, "against the green line (the blue fill had 1.2)")
        self.assertGreaterEqual(_contrast(fill, QColor("#14161a")), 3.0, "against the near-black toolbar (the first grey #525b69 had 2.6)")
        self.assertGreaterEqual(_contrast(fill, QColor("#ffffff")), 4.5, "against a white one")
        self.assertGreater(_luminance(fill), _luminance(QColor("#525b69")), "lighter than the first grey (maintainer: a bit lighter)")
        self.assertLess(max(abs(fill.red() - fill.green()), abs(fill.green() - fill.blue())), 30, "it is a grey")

    def test_each_part_of_the_picture_has_the_fill_its_mode_gives_it(self) -> None:
        from lspr_imaging_app.panels.image.selection_palette import ICON_SELECTED_FILL

        a_only, both, b_only = (13, 24), (24, 24), (35, 24)  # pixel of a part of the 48 x 48 picture (left circle, overlap, right circle)
        grey = QColor(ICON_SELECTED_FILL)

        def opaque(image: QImage, at: tuple[int, int]) -> QColor:
            color = image.pixelColor(*at)
            self.assertGreater(color.alpha(), 250, at)
            return color

        def empty(image: QImage, at: tuple[int, int]) -> None:
            self.assertEqual(image.pixelColor(*at).alpha(), 0, f"a part that is not selected, or taken away, is the plain background {at}")

        replace, add, subtract, intersect = (self._picture(how) for how in (REPLACE, ADD, SUBTRACT, INTERSECT))
        empty(replace, a_only)
        self.assertEqual(opaque(replace, both).name(), grey.name())
        self.assertEqual(opaque(replace, b_only).name(), grey.name())
        for at in (a_only, both, b_only):
            self.assertEqual(opaque(add, at).name(), grey.name())
        self.assertEqual(opaque(subtract, a_only).name(), grey.name())
        empty(subtract, both)  # what is taken away: no fill any more (it was orange)
        empty(subtract, b_only)
        empty(intersect, a_only)
        self.assertEqual(opaque(intersect, both).name(), grey.name())
        empty(intersect, b_only)

    def test_the_circles_keep_their_colours_pink_left_and_green_right(self) -> None:
        """"The border colors disappear from the venn icons" (maintainer, 2026-11-16): the left circle's rim is pink, the right one's green."""
        from lspr_imaging_app.panels.image.selection_palette import PAST_COLOR, PRESENT_COLOR

        picture = self._picture(SUBTRACT)

        def rim(columns: range) -> QColor:  # the most colourful pixel of the picture's middle row over a stretch holding one circle's rim (the grey
            # fill next to it has little colour)
            return max((picture.pixelColor(x, 24) for x in columns), key=lambda color: color.saturation() * color.alpha())

        left, right = rim(range(3, 8)), rim(range(40, 45))  # the outer edges of the left and the right circle (x = 2.6 and 21.4 of 24)
        pink, green = QColor(PAST_COLOR), QColor(PRESENT_COLOR)
        self.assertLess(abs(left.red() - pink.red()) + abs(left.green() - pink.green()) + abs(left.blue() - pink.blue()), 120, left.name())
        self.assertLess(abs(right.red() - green.red()) + abs(right.green() - green.green()) + abs(right.blue() - green.blue()), 120, right.name())
        self.assertGreater(left.red(), left.green(), "pink: red above green")
        self.assertGreater(right.green(), right.red(), "green: green above red")


class SelectionOutlineAndBadgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.active_tool = ActiveToolModule()
        self.area = AreaSelectionModule()
        self.image_panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.image_panel.resize(900, 600)
        self.image_panel.show()
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.image_panel._area_tool._image_shape is not None)
        _pump_until(lambda: False, timeout=0.5)  # let the view settle on the picture (the existing selection tests pause too)
        self.tool = self.image_panel._area_tool
        self.outlines = self.tool._outlines
        self.badge = self.image_panel._selection_tools.combine_badge
        self.toolbar = self.image_panel._selection_tools.toolbar

    def tearDown(self) -> None:
        self.badge.hide()
        self.image_panel._renderer.stop()
        self._tmp.cleanup()

    def _pick(self, mode: AreaSelectionMode) -> None:
        if self.area.mode() is not mode or self.active_tool.active() is not ImageTool.SELECT_AREA:
            self.toolbar.tool_button(mode).click()

    def _scene(self, x: float, y: float) -> QPointF:
        return self.image_panel._plot.vb.mapViewToScene(QPointF(x, y))

    def _shown(self) -> set[str]:
        items = {PAST: self.outlines.past_items(), PRESENT: self.outlines.present_items(), RESULT: self.outlines.result_items()}
        return {role for role, (_base, ants) in items.items() if ants.isVisible()}

    def _settle(self) -> None:
        """Wait until the result preview has caught up with the pointer. After a result the preview pauses for twice the time it took (so it never
        takes more than a third of the GUI thread) and one trailing update follows: on a busy machine that pause outlasts a test's back-to-back
        updates, which made the tests that read the result flaky."""
        _pump_until(lambda: not self.outlines._catch_up.isActive(), timeout=5.0)

    def _lasso_over_the_right_part(self, how: str) -> None:
        """A lasso from x = 12 to 40, y = 0 to 30, dragged over a selection that is the rectangle (5, 5)-(20, 20)."""
        self._pick(AreaSelectionMode.LASSO)
        self.assertTrue(self.tool.begin_gesture(12.0, 0.0, how))
        for point in ((40.0, 0.0), (40.0, 30.0), (12.0, 30.0)):
            self.tool.update_gesture(*point)
        self._settle()

    # -- which outlines each mode shows --------------------------------------------------------------------

    def test_with_no_drag_the_selection_is_drawn_in_the_past_colour(self) -> None:
        self.assertEqual(self._shown(), set())
        self.area.set_rectangle(5, 5, 20, 20)
        self.assertEqual(self._shown(), {PAST})

    def test_every_mode_shows_the_outlines_of_its_table_row(self) -> None:
        for how in (REPLACE, ADD, SUBTRACT, INTERSECT):
            with self.subTest(how=how):
                self.area.clear()
                self.area.set_rectangle(5, 5, 20, 20)
                if self.tool.gesture_how() is not None:
                    self.tool.cancel_gesture()
                self._lasso_over_the_right_part(how)
                self.assertEqual(self._shown(), set(OUTLINES_FOR_MODE[how]))
                self.tool.cancel_gesture()
                self.assertEqual(self._shown(), {PAST}, "cancelling leaves the old outline, nothing else")

    def test_subtract_shows_the_old_selection_with_the_shape_cut_out_and_not_the_shape(self) -> None:
        self.area.set_rectangle(5, 5, 20, 20)
        self._lasso_over_the_right_part(SUBTRACT)
        rect = self.outlines.result_items()[1].path().boundingRect()
        self.assertAlmostEqual(rect.left(), 5.0, places=3)
        self.assertAlmostEqual(rect.right(), 12.0, places=3, msg="what is left of the rectangle once x >= 12 is cut out")
        self.assertAlmostEqual(rect.height(), 15.0, places=3)
        self.tool.end_gesture()
        self.assertEqual(self._shown(), {PAST}, "after the drag the new selection is the past outline again")
        self.assertFalse(self.area.mask(_SHAPE)[10, 15], "and the pixels were really taken away")

    def test_add_keeps_the_old_selection_and_the_new_shape_both_visible(self) -> None:
        self.area.set_rectangle(5, 5, 20, 20)
        self._lasso_over_the_right_part(ADD)
        self.assertEqual(self._shown(), {PAST, PRESENT})
        self.assertAlmostEqual(self.outlines.past_items()[1].path().boundingRect().width(), 15.0, places=3)
        self.assertGreater(self.outlines.present_items()[1].path().boundingRect().width(), 25.0)

    def test_intersect_also_shows_the_overlap(self) -> None:
        self.area.set_rectangle(5, 5, 20, 20)
        self._lasso_over_the_right_part(INTERSECT)
        rect = self.outlines.result_items()[1].path().boundingRect()
        self.assertAlmostEqual(rect.left(), 12.0, places=3)
        self.assertAlmostEqual(rect.right(), 20.0, places=3)

    def test_subtract_with_nothing_selected_draws_nothing_and_does_not_fail(self) -> None:
        self._lasso_over_the_right_part(SUBTRACT)
        self.assertEqual(self._shown(), set())
        self.tool.end_gesture()
        self.assertFalse(self.area.has_selection())

    def test_at_the_press_of_a_subtract_drag_the_selection_does_not_blink_out(self) -> None:
        """The shape has no area yet (one point): the result is the whole old selection (drawn in the selection's colour)."""
        self.area.set_rectangle(5, 5, 20, 20)
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertTrue(self.tool.begin_gesture(30.0, 30.0, SUBTRACT))
        self.assertEqual(self._shown(), {RESULT})
        rect = self.outlines.result_items()[1].path().boundingRect()
        self.assertAlmostEqual(rect.width(), 15.0, places=3)

    def test_an_erasing_brush_stroke_shows_what_is_left(self) -> None:
        self.area.set_rectangle(5, 5, 20, 20)
        self.tool._brush_diameter = lambda: 6.0
        self._pick(AreaSelectionMode.BRUSH)
        self.assertTrue(self.tool.begin_gesture(0.0, 12.0, SUBTRACT))
        self.tool.update_gesture(30.0, 12.0)
        self._settle()
        self.assertEqual(self._shown(), {RESULT})
        pieces = self.outlines.result_items()[1].path().toSubpathPolygons()
        self.assertEqual(len(pieces), 2, "a band 6 px wide cut across the rectangle leaves a strip above and a strip below")

    def test_the_committed_outline_stays_visible_while_a_brush_stroke_is_added(self) -> None:
        """The first report: Draw + Shift made the previous selection's ants disappear until the button was released."""
        self.area.set_rectangle(5, 5, 20, 20)
        self._pick(AreaSelectionMode.BRUSH)
        self.assertTrue(self.tool.begin_gesture(30.0, 30.0, ADD))
        self.tool.update_gesture(45.0, 30.0)
        self.assertEqual(self._shown(), {PAST, PRESENT})
        old = self.tool._ants_item.path().boundingRect()  # still the rectangle (15 x 15 px at 5..20), not the stroke
        self.assertAlmostEqual(old.width(), 15.0)
        self.assertAlmostEqual(old.height(), 15.0)
        self.tool.end_gesture()
        mask = self.area.mask(_SHAPE)
        self.assertTrue(mask[10, 10], "the old selection is still there")
        self.assertTrue(mask[30, 35], "and the stroke was added")

    def test_dragging_does_not_rebuild_the_committed_outline(self) -> None:
        """A wand region's outline takes up to hundreds of ms to build; it is made when the selection changes, not per mouse move."""
        self.area.set_rectangle(5, 5, 20, 20)
        self._pick(AreaSelectionMode.LASSO)
        with patch.object(self.outlines, "_committed_path", wraps=self.outlines._committed_path) as built:
            self.tool.begin_gesture(30.0, 30.0, ADD)
            for step in range(8):
                self.tool.update_gesture(31.0 + 4 * step, 30.0 + 3 * step)
            self.assertEqual(built.call_count, 0)
            self.tool.end_gesture()
            self.assertGreaterEqual(built.call_count, 1, "a committed change does rebuild it")

    def test_ants_animate_for_a_drag_with_no_selection_and_stop_afterwards(self) -> None:
        self._pick(AreaSelectionMode.LASSO)
        self.assertFalse(self.tool.is_animating())
        self.tool.begin_gesture(30.0, 30.0)
        self.tool.update_gesture(40.0, 30.0)
        self.assertTrue(self.tool.is_animating())
        self.tool.cancel_gesture()
        self.assertFalse(self.tool.is_animating())

    # -- the result equals the full Qt operation ------------------------------------------------------------

    def test_the_result_made_from_pieces_equals_the_operation_on_the_whole_outline(self) -> None:
        """Only the pieces of the outline the shape's box meets go through the operation. The pixels must be the same as when the whole
        outline does: a ring (an outline with a hole), a separate blob, a speck, with shapes that touch the ring's edge, its hole, the blob."""
        region = np.zeros(_SHAPE, dtype=bool)
        region[5:30, 5:30] = True
        region[12:22, 12:22] = False  # the ring's hole
        region[40:58, 50:72] = True  # a separate blob
        region[5:9, 60:70] = True  # a speck
        self.area.combine_region(region)
        self.outlines.update(None, None)
        committed = self.outlines._committed
        self.assertIsNotNone(committed)
        shapes = {
            "ring edge only": [(2, 2), (14, 2), (14, 9), (2, 9)],
            "into the hole": [(10, 10), (26, 10), (26, 26), (10, 26)],
            "ring and blob": [(20, 20), (60, 20), (60, 50), (20, 50)],
            "nothing": [(35, 33), (45, 33), (45, 38), (35, 38)],
            "all of it": [(0, 0), (80, 0), (80, 64), (0, 64)],
        }
        for name, points in shapes.items():
            present = polygon_path(points)
            for how, full in ((SUBTRACT, committed.subtracted(present)), (INTERSECT, committed.intersected(present))):
                with self.subTest(shape=name, how=how):
                    self.outlines._ready_at = 0.0
                    self.outlines.update(present, how)
                    made = self.outlines.result_items()[1].path()
                    np.testing.assert_array_equal(_render(made), _render(closed_path(full)))

    # -- the pause between results ---------------------------------------------------------------------------

    def test_a_result_is_not_made_on_every_mouse_move_but_the_picture_catches_up(self) -> None:
        self.area.set_rectangle(5, 5, 20, 20)
        self._pick(AreaSelectionMode.LASSO)
        self.tool.begin_gesture(12.0, 0.0, SUBTRACT)
        self.tool.update_gesture(40.0, 0.0)
        before = QPainterPath(self.outlines.result_items()[1].path())
        self.outlines._ready_at = time.monotonic() + 0.15  # as if the last result had been slow
        self.tool.update_gesture(40.0, 30.0)
        self.assertTrue(self.outlines._catch_up.isActive(), "one more update is waiting for the pause to end")
        self.assertEqual(self.outlines.result_items()[1].path(), before, "meanwhile the last picture stays")
        _pump_until(lambda: not self.outlines._catch_up.isActive(), timeout=3.0)
        self.assertNotEqual(self.outlines.result_items()[1].path(), before, "then it shows the pointer's latest shape")

    def test_ending_the_drag_stops_a_waiting_update(self) -> None:
        self.area.set_rectangle(5, 5, 20, 20)
        self._pick(AreaSelectionMode.LASSO)
        self.tool.begin_gesture(12.0, 0.0, SUBTRACT)
        self.tool.update_gesture(40.0, 0.0)
        self.outlines._ready_at = time.monotonic() + 0.5
        self.tool.update_gesture(40.0, 30.0)
        self.tool.cancel_gesture()
        self.assertFalse(self.outlines._catch_up.isActive())
        self.assertEqual(self._shown(), {PAST})

    # -- the badge ----------------------------------------------------------------

    def _follow(self, modifiers, x: float = 30.0, y: float = 30.0):
        with patch("lspr_imaging_app.panels.image.combine_badge.QGuiApplication") as gui:
            gui.queryKeyboardModifiers.return_value = modifiers
            self.badge.follow(self._scene(x, y))
        return self.badge.symbol()

    def test_shift_alt_and_both_show_plus_minus_and_cross_and_no_key_shows_nothing(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertIsNone(self._follow(_NONE))
        self.assertEqual(self._follow(_SHIFT), PLUS)
        self.assertEqual(self._follow(_ALT), MINUS)
        self.assertEqual(self._follow(_SHIFT | _ALT), CROSS)

    def test_the_picked_mode_shows_when_no_key_is_held_and_a_key_overrides_it(self) -> None:
        self._pick(AreaSelectionMode.WAND)
        self.area.set_combine_mode(ADD)
        self.assertEqual(self._follow(_NONE), PLUS)
        self.assertEqual(self._follow(_ALT), MINUS)
        self.area.set_combine_mode(INTERSECT)
        self.assertEqual(self._follow(_NONE), CROSS)

    def test_the_brush_shows_plus_with_shift_and_minus_with_alt(self) -> None:
        self._pick(AreaSelectionMode.BRUSH)
        self.assertIsNone(self._follow(_NONE))
        self.assertEqual(self._follow(_SHIFT), PLUS, "Draw + Shift: adding to the selection")
        self.assertEqual(self._follow(_ALT), MINUS)
        self.assertEqual(self._follow(_SHIFT | _ALT), CROSS, "Shift+Alt: keep only what the stroke covers")
        self.area.set_combine_mode(INTERSECT)
        self.assertEqual(self._follow(_NONE), CROSS, "the picked Intersect stays Intersect for the brush")

    def test_a_brush_stroke_begun_with_shift_keeps_the_plus(self) -> None:
        self._pick(AreaSelectionMode.BRUSH)
        self.area.set_rectangle(5, 5, 20, 20)
        self.tool.begin_gesture(30.0, 30.0, ADD)
        self.assertEqual(self._follow(_NONE), PLUS)
        self.tool.cancel_gesture()

    def test_a_drag_in_progress_keeps_the_symbol_it_began_with(self) -> None:
        self._pick(AreaSelectionMode.LASSO)
        self.tool.begin_gesture(30.0, 30.0, SUBTRACT)
        self.assertEqual(self._follow(_NONE), MINUS, "keys let go after the press do not change what the drag does")
        self.tool.cancel_gesture()
        self.assertIsNone(self._follow(_NONE))

    def test_the_badge_is_hidden_with_no_tool_armed_and_off_the_image(self) -> None:
        self.assertIsNone(self._follow(_SHIFT), "no selection tool is armed")
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertEqual(self._follow(_SHIFT), PLUS)
        self.assertTrue(self.badge._timer.isActive())
        self.assertIsNone(self._follow(_SHIFT, x=-500.0, y=-500.0), "the pointer is not over the image")
        self.assertFalse(self.badge._timer.isActive(), "an idle badge costs nothing")
        self.toolbar.tool_button(AreaSelectionMode.RECTANGLE).click()  # clicking the armed tool again disarms it
        self.assertIsNone(self.active_tool.active())
        self.assertIsNone(self._follow(_SHIFT))

    def test_the_timer_follows_the_keys_with_the_pointer_standing_still_and_stops_when_the_pointer_leaves(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertIsNone(self._follow(_NONE))
        viewport = self.image_panel._view.viewport()
        inside = viewport.mapToGlobal(QPoint(20, 20))
        with patch("lspr_imaging_app.panels.image.combine_badge.QGuiApplication") as gui, patch(
            "lspr_imaging_app.panels.image.combine_badge.QCursor"
        ) as cursor:
            cursor.pos.return_value = inside
            gui.queryKeyboardModifiers.return_value = _ALT
            self.badge._tick()
            self.assertEqual(self.badge.symbol(), MINUS, "Alt pressed with the mouse still: the symbol appears")
            gui.queryKeyboardModifiers.return_value = _NONE
            self.badge._tick()
            self.assertIsNone(self.badge.symbol())
            gui.queryKeyboardModifiers.return_value = _SHIFT
            self.badge._tick()
            self.assertEqual(self.badge.symbol(), PLUS)
            cursor.pos.return_value = QPoint(-9000, -9000)
            self.badge._tick()
            self.assertIsNone(self.badge.symbol(), "the pointer left the image")
            self.assertFalse(self.badge._timer.isActive())

    def test_a_pointer_move_over_the_image_drives_the_badge(self) -> None:
        """The panel's scene-moved slot is what the canvas calls; the badge needs no other wiring."""
        self._pick(AreaSelectionMode.WAND)
        with patch("lspr_imaging_app.panels.image.combine_badge.QGuiApplication") as gui:
            gui.queryKeyboardModifiers.return_value = _ALT
            self.image_panel._on_scene_moved(self._scene(30.0, 30.0))
        self.assertEqual(self.badge.symbol(), MINUS)
        self.assertIs(self.active_tool.active(), ImageTool.SELECT_AREA)

    # -- the mode button follows the keys ---------------------------------------------------------------------

    def _button_shows(self, how: str) -> bool:
        return self.toolbar.mode_button.icon().cacheKey() == combine_mode_icon(how).cacheKey()

    def test_the_mode_button_shows_the_mode_a_held_key_gives_and_goes_back(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertTrue(self._button_shows(REPLACE))
        self._follow(_ALT)
        self.assertEqual(self.badge.held_mode(), SUBTRACT)
        self.assertTrue(self._button_shows(SUBTRACT), "Alt: the button shows Subtract")
        self._follow(_SHIFT)
        self.assertTrue(self._button_shows(ADD), "Shift: Add")
        self._follow(_SHIFT | _ALT)
        self.assertTrue(self._button_shows(INTERSECT), "Shift+Alt: Intersect")
        self._follow(_NONE)
        self.assertIsNone(self.badge.held_mode())
        self.assertTrue(self._button_shows(REPLACE), "no key: the picked mode again")
        self.assertEqual(self.area.combine_mode(), REPLACE, "the picked (sticky) mode was never changed")

    def test_the_mode_button_goes_back_when_the_pointer_leaves_the_image(self) -> None:
        self._pick(AreaSelectionMode.WAND)
        self._follow(_ALT)
        self.assertTrue(self._button_shows(SUBTRACT))
        self._follow(_ALT, x=-500.0, y=-500.0)
        self.assertTrue(self._button_shows(REPLACE))

    def test_a_picked_mode_with_no_key_is_not_a_held_mode(self) -> None:
        self._pick(AreaSelectionMode.WAND)
        self.area.set_combine_mode(ADD)
        self._follow(_NONE)
        self.assertIsNone(self.badge.held_mode())
        self.assertTrue(self._button_shows(ADD))
        self._follow(_ALT)
        self.assertTrue(self._button_shows(SUBTRACT), "a key overrides the picked mode while it is down")

    def test_a_drag_keeps_the_mode_it_began_with_on_the_button(self) -> None:
        self._pick(AreaSelectionMode.LASSO)
        self.tool.begin_gesture(30.0, 30.0, SUBTRACT)
        self._follow(_NONE)
        self.assertTrue(self._button_shows(SUBTRACT))
        self.tool.cancel_gesture()
        self._follow(_NONE)
        self.assertTrue(self._button_shows(REPLACE))

    def test_the_button_shows_intersect_for_the_brush_and_never_jumps_to_add(self) -> None:
        """Maintainer, 2026-11-16: "when I select intersection it will always jump to adding mode" (Draw armed)."""
        self._pick(AreaSelectionMode.BRUSH)
        self._follow(_SHIFT | _ALT)
        self.assertTrue(self._button_shows(INTERSECT), "Shift+Alt is Intersect for the brush as for every tool")
        self._follow(_ALT)
        self.assertTrue(self._button_shows(SUBTRACT))
        self.area.set_combine_mode(INTERSECT)
        self._follow(_NONE)
        self.assertTrue(self._button_shows(INTERSECT), "no key: the picked mode, still Intersect")
        self.tool.begin_gesture(30.0, 30.0, INTERSECT)  # a stroke begun in the picked mode
        self._follow(_NONE)
        self.assertTrue(self._button_shows(INTERSECT), "and during the stroke")
        self.assertIsNone(self.badge.held_mode())
        self.tool.cancel_gesture()

    def test_a_brush_stroke_in_the_picked_replace_mode_does_not_switch_the_button_to_add(self) -> None:
        """Maintainer, 2026-11-16: "I select replace, and when I start drawing it switches to add mode (without any key, only LMB)"."""
        self._pick(AreaSelectionMode.BRUSH)
        self.assertEqual(self.area.combine_mode(), REPLACE)
        self.assertTrue(self._button_shows(REPLACE))
        self.assertTrue(self.tool.begin_gesture(30.0, 30.0, REPLACE), "the canvas hands the brush the picked mode, not Add")
        self.assertEqual(self.tool.gesture_how(), REPLACE)
        self.assertIsNone(self._follow(_NONE), "no symbol: nothing was asked for")
        self.assertIsNone(self.badge.held_mode())
        self.assertTrue(self._button_shows(REPLACE), "and the button stays on Replace during the stroke")
        self.tool.cancel_gesture()

    def test_the_canvas_gives_the_brush_the_picked_mode_for_every_mode(self) -> None:
        self._pick(AreaSelectionMode.BRUSH)
        for how in (REPLACE, ADD, SUBTRACT, INTERSECT):
            self.area.set_combine_mode(how)
            self.assertEqual(self.image_panel._interaction.how_for(_NONE), how)

    def test_the_mode_names_are_short(self) -> None:
        """Maintainer, 2026-11-16: the names of the four Venn modes are "replace, add, subtract, intersect"."""
        self.assertEqual([action.text() for action in self.toolbar.mode_menu.actions()], ["Replace", "Add", "Subtract", "Intersect"])
        self.area.set_combine_mode(SUBTRACT)
        self.assertTrue(self.toolbar.mode_button.toolTip().startswith("Selection mode: Subtract"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
