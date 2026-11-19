"""The drawn two-headed resize arrow (`panels/image/resize_cursor.py`, 2026-11-19): the angle it is drawn at, and that one angle step is one shared cursor.

Needs a running Qt application (a cursor is built from a pixmap) but no window. The arrow's picture is read back and its long axis measured, so the test would fail if the
picture were drawn mirrored or turned the wrong way, not only if the code rounded wrongly.
"""

from __future__ import annotations

import math
import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image import resize_cursor as module
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _long_axis_deg(angle_deg: float) -> float:
    """The direction (clockwise on the screen, modulo 180) of the long axis of the arrow drawn for ``angle_deg``, from the picture's own pixels."""
    image = module._picture(angle_deg).toImage()
    points = [(x, y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha() > 128]
    n = len(points)
    mean_x, mean_y = sum(p[0] for p in points) / n, sum(p[1] for p in points) / n
    cxx = sum((p[0] - mean_x) ** 2 for p in points) / n
    cyy = sum((p[1] - mean_y) ** 2 for p in points) / n
    cxy = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points) / n
    return math.degrees(0.5 * math.atan2(2.0 * cxy, cxx - cyy)) % 180.0


class ResizeCursorTest(unittest.TestCase):
    def test_a_direction_becomes_its_angle_on_the_screen_modulo_a_half_turn(self) -> None:
        self.assertAlmostEqual(module.direction_angle_deg(1.0, 0.0), 0.0)
        self.assertAlmostEqual(module.direction_angle_deg(0.0, 1.0), 90.0)  # y points down: clockwise
        self.assertAlmostEqual(module.direction_angle_deg(1.0, 1.0), 45.0)
        self.assertAlmostEqual(module.direction_angle_deg(-1.0, 1.0), 135.0)
        self.assertAlmostEqual(module.direction_angle_deg(-1.0, -1.0), 45.0, msg="two heads: the line, not the sense")
        self.assertEqual(module.direction_angle_deg(0.0, 0.0), 0.0)

    def test_the_arrow_is_drawn_along_the_angle_it_is_given(self) -> None:
        for angle in (0.0, 21.0, 45.0, 90.0, 120.0, 135.0, 171.0):
            measured = _long_axis_deg(angle)
            off = abs((measured - angle + 90.0) % 180.0 - 90.0)  # the smaller difference between two lines
            self.assertLess(off, 1.5, f"arrow asked at {angle} degrees was drawn at {measured:.1f}")

    def test_the_arrow_grows_with_the_screen_scale_like_windows_own_cursors(self) -> None:
        """23 pixels long at 100 %, and 1.5 times that at 150 % (a fixed size came out a third too small beside the move cursor, 2026-11-19); the picture is odd-sized."""

        def length(ratio: float) -> int:
            image = module._picture(0.0, ratio).toImage()
            columns = [x for x in range(image.width()) if any(image.pixelColor(x, y).alpha() > 128 for y in range(image.height()))]
            return max(columns) - min(columns) + 1

        self.assertEqual(length(1.0), 23)
        self.assertLessEqual(abs(length(1.5) - 34.5), 1.5)
        self.assertLessEqual(abs(length(2.0) - 46), 1.5)
        for ratio in (1.0, 1.25, 1.5, 2.0):
            self.assertEqual(module._picture(0.0, ratio).width() % 2, 1, f"odd side at {ratio}")
            self.assertEqual(module._picture(0.0, ratio).devicePixelRatio(), ratio)

    def test_one_step_is_one_shared_cursor_and_a_half_turn_is_the_same_line(self) -> None:
        self.assertIs(module.resize_cursor(0.0), module.resize_cursor(0.0))
        self.assertIs(module.resize_cursor(0.0), module.resize_cursor(180.0))
        self.assertIs(module.resize_cursor(0.0), module.resize_cursor(179.0), "179 rounds to the 180-degree step, which is the 0 step")
        self.assertIs(module.resize_cursor(0.0), module.resize_cursor(1.0))
        self.assertIsNot(module.resize_cursor(0.0), module.resize_cursor(2.0))
        self.assertIsNot(module.resize_cursor(30.0), module.resize_cursor(45.0))

    def test_it_is_a_picture_cursor_with_its_hot_spot_in_the_middle(self) -> None:
        cursor = module.resize_cursor(33.0)
        self.assertEqual(cursor.shape(), Qt.CursorShape.BitmapCursor)
        side = module._picture(33.0).width()
        hot = cursor.hotSpot()
        self.assertLessEqual(abs(hot.x() - side // 2), 1)
        self.assertLessEqual(abs(hot.y() - side // 2), 1)


if __name__ == "__main__":
    unittest.main()
