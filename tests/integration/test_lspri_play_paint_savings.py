"""Play paint savings of 2026-11-11 (`docs/play_diagnosis_2026-11-11.md` section 9): what each one must keep showing.

- the Image view's plain (processor-drawn) viewport is opaque, so the widgets behind it are not repainted for nothing;
- the grey "no data" checker is worked out on the render thread (`FrameDisplay.no_data`) and equals the GUI thread's own;
- the greyed Display range fields are rewritten at most ten times a second during Play, never later than the frame Play stops on;
- the timeline strip's line and marks are a cached picture that follows every change of the marks and of the governing mark;
- a cached ROI overlay picture drawn as only its used tiles gives the same pixels as drawing it whole.
A real `QApplication` built in-process (offscreen), never `.exec()`.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image import gpu_view
    from lspr_imaging_app.panels.image.display_picture import prepare_frame_display
    from lspr_imaging_app.panels.image.display_range_controls import DisplayRangeControls
    from lspr_imaging_app.panels.image.no_data import no_data_overlay_rgba
    from lspr_imaging_app.panels.image.overlay_cache import _Entry
    from lspr_imaging_app.panels.image.timeline_strip import SCOPE_PERSISTENT, StripMark, TimelineStrip
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class OpaqueViewportTest(unittest.TestCase):
    def test_a_plain_viewport_installed_by_the_switch_is_opaque(self) -> None:
        view = QtWidgets.QGraphicsView()
        gpu_view.apply_gpu_drawing(view, False)  # already plain: nothing replaced, so mark it the way the panel does
        gpu_view.mark_opaque(view.viewport())
        self.assertTrue(view.viewport().testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))

    def test_the_image_panel_marks_its_viewport_opaque(self) -> None:
        from lspr_imaging_app.app_rewrite import build_main_window
        from lspr_imaging_app.panels.image import ImagePanel
        from lspr_imaging_app.storage.app_settings import AppSettings

        window = build_main_window(AppSettings(), lambda _settings: None)
        self.addCleanup(window.close)
        panel = window.findChild(ImagePanel)
        self.assertFalse(panel.gpu_drawing, "offscreen: processor drawing")
        self.assertTrue(panel._view.viewport().testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))


class NoDataOnRenderThreadTest(unittest.TestCase):
    def test_a_frame_without_missing_pixels_needs_no_checker(self) -> None:
        display = prepare_frame_display(np.arange(12, dtype=np.float32).reshape(3, 4))
        self.assertTrue(display.no_data_known)
        self.assertIsNone(display.no_data)

    def test_a_frame_with_missing_pixels_gets_the_same_checker_as_the_gui_thread(self) -> None:
        image = np.arange(400, dtype=np.float32).reshape(20, 20)
        image[:3, :5] = np.nan
        for fast in (False, True):
            display = prepare_frame_display(image, fast=fast)
            self.assertTrue(display.no_data_known)
            np.testing.assert_array_equal(display.no_data, no_data_overlay_rgba(image))

    def test_infinite_pixels_leave_it_to_the_panel(self) -> None:
        image = np.ones((4, 4), dtype=np.float32)
        image[0, 0] = np.inf
        self.assertFalse(prepare_frame_display(image).no_data_known)


class RangeFieldsDuringPlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controls = DisplayRangeControls()

    def _fields(self) -> tuple[str, str]:
        return self.controls._low_edit.text(), self.controls._high_edit.text()

    def test_play_frames_close_together_keep_the_shown_numbers(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0), playing=True)
        self.assertEqual(self._fields(), ("100", "4000"))
        self.controls.show_frame_levels((120.0, 4100.0), playing=True)  # well within 0.1 s
        self.assertEqual(self._fields(), ("100", "4000"))

    def test_the_frame_play_stops_on_is_shown_at_once(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0), playing=True)
        self.controls.show_frame_levels((120.0, 4100.0), playing=True)
        self.controls.show_frame_levels((120.0, 4100.0))  # Play stopped: the frame is drawn again, not playing
        self.assertEqual(self._fields(), ("120", "4100"))

    def test_the_fit_target_is_always_the_newest_frame(self) -> None:
        self.controls.show_frame_levels((100.0, 4000.0), playing=True)
        self.controls.show_frame_levels((120.0, 4100.0), playing=True)
        self.assertEqual(self.controls._frame_levels, (120.0, 4100.0))


class StripPictureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strip = TimelineStrip()
        self.strip.resize(400, self.strip.height())
        self.strip.set_axis_count(20)

    def _image(self):
        return self.strip.grab().toImage()

    def test_new_marks_are_drawn(self) -> None:
        self.strip.set_marks([StripMark(3, SCOPE_PERSISTENT, "")])
        before = self._image()
        self.strip.set_marks([StripMark(12, SCOPE_PERSISTENT, "")])
        self.assertNotEqual(before, self._image())

    def test_the_governing_mark_follows_the_cursor(self) -> None:
        self.strip.set_marks([StripMark(3, SCOPE_PERSISTENT, ""), StripMark(12, SCOPE_PERSISTENT, "")])
        self.strip.set_cursor(5)  # the mark at 3 governs
        self._image()
        first_key = self.strip._static_key
        self.strip.set_cursor(14)  # now the mark at 12
        self._image()
        self.assertNotEqual(first_key, self.strip._static_key, "a new governing mark makes a new picture")

    def test_moving_the_cursor_between_marks_reuses_the_picture(self) -> None:
        self.strip.set_marks([StripMark(3, SCOPE_PERSISTENT, "")])
        self.strip.set_cursor(5)
        self._image()
        picture = self.strip._static
        self.strip.set_cursor(6)
        moved = self._image()
        self.assertIs(picture, self.strip._static)
        self.strip.set_cursor(5)
        self.assertNotEqual(moved, self._image(), "the cursor line itself still moves")


class OverlayTilesTest(unittest.TestCase):
    """`_Entry`: the device-pixel draw (whole or in tiles) against Qt's plain `drawPixmap(offset, picture)`, at a 1.5 pixel ratio."""

    RATIO = 1.5

    def _picture(self, dense: bool) -> QPixmap:
        picture = QPixmap(300, 160)
        picture.setDevicePixelRatio(self.RATIO)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if dense:
            painter.fillRect(QRectF(0, 0, 200, 100), QColor(20, 200, 90, 120))
        painter.setPen(QPen(QColor(255, 60, 40, 220), 1.7))
        for x in (12.3, 61.0, 140.6):
            painter.drawEllipse(QRectF(x, 15.4, 33.2, 27.9))
        painter.end()
        return picture

    def _target(self) -> QImage:
        target = QImage(420, 260, QImage.Format.Format_ARGB32_Premultiplied)
        target.setDevicePixelRatio(self.RATIO)
        target.fill(QColor(30, 40, 50))
        return target

    def _compare(self, dense: bool) -> _Entry:
        picture, offset = self._picture(dense), QPoint(7, 5)
        expected = self._target()
        painter = QPainter(expected)
        painter.drawPixmap(offset, picture)
        painter.end()
        entry = _Entry(picture, offset, 0)
        for tiled in (False, True):
            if tiled:
                entry.tile()
            got = self._target()
            painter = QPainter(got)
            entry.draw(painter)
            painter.end()
            self.assertEqual(expected, got, f"tiled={tiled}")
        return entry

    def test_a_sparse_picture_is_drawn_in_pieces_with_the_same_pixels(self) -> None:
        entry = self._compare(dense=False)
        self.assertTrue(entry.fragments, "drawn as pieces")

    def test_a_mostly_full_picture_is_drawn_whole_with_the_same_pixels(self) -> None:
        entry = self._compare(dense=True)
        self.assertIsNone(entry.fragments)


if __name__ == "__main__":
    unittest.main()
