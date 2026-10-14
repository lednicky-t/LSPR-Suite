"""The nine label places around a circle (`panels/image/label_placement.py`): pure geometry, no Qt."""

from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.label_placement import (
        DEFAULT_GROUP_PLACEMENT,
        DEFAULT_ROI_PLACEMENT,
        PLACEMENTS,
        placement_origin,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

ANCHOR = (100.0, 100.0)
RADIUS, GAP, WIDTH, HEIGHT = 10.0, 3.0, 40.0, 12.0


def box(placement: str) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) of the label."""
    left, top = placement_origin(*ANCHOR, RADIUS, placement, WIDTH, HEIGHT, GAP)
    return left, top, left + WIDTH, top + HEIGHT


class PlacementTest(unittest.TestCase):
    def test_nine_distinct_places_in_grid_reading_order(self) -> None:
        self.assertEqual(len(PLACEMENTS), 9)
        self.assertEqual(len(set(PLACEMENTS)), 9)
        self.assertEqual(PLACEMENTS[0], "top-left")
        self.assertEqual(PLACEMENTS[4], "center")
        self.assertEqual(PLACEMENTS[8], "bottom-right")

    def test_the_defaults_keep_the_old_looks(self) -> None:
        self.assertEqual(DEFAULT_ROI_PLACEMENT, "right")  # ROI labels were always drawn right of the circle
        self.assertEqual(DEFAULT_GROUP_PLACEMENT, "top")  # group labels defaulted to the top side

    def test_the_four_sides_sit_gap_outside_the_circle_and_centred_across(self) -> None:
        left, top, right, bottom = box("top")
        self.assertEqual((bottom, (left + right) / 2.0), (100.0 - RADIUS - GAP, 100.0))
        left, top, right, bottom = box("bottom")
        self.assertEqual((top, (left + right) / 2.0), (100.0 + RADIUS + GAP, 100.0))
        left, top, right, bottom = box("left")
        self.assertEqual((right, (top + bottom) / 2.0), (100.0 - RADIUS - GAP, 100.0))
        left, top, right, bottom = box("right")
        self.assertEqual((left, (top + bottom) / 2.0), (100.0 + RADIUS + GAP, 100.0))

    def test_the_middle_cell_centres_the_label_on_the_circle(self) -> None:
        left, top, right, bottom = box("center")
        self.assertEqual(((left + right) / 2.0, (top + bottom) / 2.0), ANCHOR)

    def test_each_corner_puts_its_nearest_corner_gap_beyond_the_circle_on_the_diagonal(self) -> None:
        reach = (RADIUS + GAP) / math.sqrt(2.0)
        left, top, right, bottom = box("top-left")
        self.assertAlmostEqual(right, 100.0 - reach)
        self.assertAlmostEqual(bottom, 100.0 - reach)
        left, top, right, bottom = box("top-right")
        self.assertAlmostEqual(left, 100.0 + reach)
        self.assertAlmostEqual(bottom, 100.0 - reach)
        left, top, right, bottom = box("bottom-left")
        self.assertAlmostEqual(right, 100.0 - reach)
        self.assertAlmostEqual(top, 100.0 + reach)
        left, top, right, bottom = box("bottom-right")
        self.assertAlmostEqual(left, 100.0 + reach)
        self.assertAlmostEqual(top, 100.0 + reach)

    def test_no_outside_place_overlaps_the_circle(self) -> None:
        """The closest point of the label to the circle's centre is at least the radius away, whatever the label's size."""
        for placement in PLACEMENTS:
            if placement == "center":
                continue
            left, top, right, bottom = box(placement)
            nearest_x = min(max(ANCHOR[0], left), right)
            nearest_y = min(max(ANCHOR[1], top), bottom)
            self.assertGreaterEqual(math.hypot(nearest_x - ANCHOR[0], nearest_y - ANCHOR[1]), RADIUS + GAP - 1e-9, placement)

    def test_an_unknown_place_is_refused(self) -> None:
        for bad in ("diagonal", "", "TOP", "up"):
            with self.assertRaises(ValueError):
                placement_origin(0.0, 0.0, 5.0, bad, 10.0, 10.0, 2.0)


if __name__ == "__main__":
    unittest.main()
