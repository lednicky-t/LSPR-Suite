"""The legend's corner (2026-11-22): the pure rule behind the Legend menu's Corner row. No Qt.

pyqtgraph anchors a legend by the signs of its offset (positive = from the left / top, negative = from the right / bottom), so the four corners are four sign pairs, and an offset of
exactly 0 must never reach it (it reads as "from the right / bottom").
"""

from __future__ import annotations

import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

import sys

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.sensorgram.legend_corner import (
        BOTTOM_LEFT,
        BOTTOM_RIGHT,
        CORNERS,
        DEFAULT_CORNER,
        TOP_LEFT,
        TOP_RIGHT,
        corner_offset,
        sanitize_corner,
    )
    from lspr_imaging_app.panels.sensorgram.view_settings import SensorgramViewSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class CornerRuleTest(unittest.TestCase):
    def test_the_default_is_where_the_legend_always_was(self) -> None:
        self.assertEqual(DEFAULT_CORNER, TOP_LEFT)
        self.assertEqual(SensorgramViewSettings().legend_corner, TOP_LEFT)

    def test_each_corner_is_its_own_pair_of_signs(self) -> None:
        self.assertEqual(corner_offset(TOP_LEFT, 8, 6), (8.0, 6.0))
        self.assertEqual(corner_offset(TOP_RIGHT, 8, 6), (-8.0, 6.0))
        self.assertEqual(corner_offset(BOTTOM_LEFT, 8, 6), (8.0, -6.0))
        self.assertEqual(corner_offset(BOTTOM_RIGHT, 8, 6), (-8.0, -6.0))
        self.assertEqual(len(set(CORNERS)), 4)

    def test_a_zero_gap_never_reaches_pyqtgraph_as_zero(self) -> None:
        for corner in CORNERS:
            across, down = corner_offset(corner, 0, 0)
            self.assertNotEqual(across, 0.0)
            self.assertNotEqual(down, 0.0)
        self.assertGreater(corner_offset(TOP_LEFT, 0, 0)[0], 0.0)  # a left / top legend stays on the left / top

    def test_a_damaged_value_is_the_default(self) -> None:
        for bad in ("middle", "", None, 3, ["top-left"]):
            self.assertEqual(sanitize_corner(bad), DEFAULT_CORNER)
            self.assertEqual(SensorgramViewSettings(legend_corner=bad).sanitized().legend_corner, DEFAULT_CORNER)  # type: ignore[arg-type]
        self.assertEqual(sanitize_corner(BOTTOM_RIGHT), BOTTOM_RIGHT)


if __name__ == "__main__":
    unittest.main()
