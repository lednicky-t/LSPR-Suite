"""Logic tests for the LSPRi rewrite's area selection (`AreaSelectionModule`).

Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch (see
`test_lspri_rewrite_analysis_core.py`). No Qt event loop, no files.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.selection import AreaSelectionMode, AreaSelectionModule
    from lspr_imaging_app.selection.area_selection_module import rasterize_polygon
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

import numpy as np  # noqa: E402

SHAPE = (20, 30)


class AreaSelectionModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sel = AreaSelectionModule()
        self.changes = 0
        self.sel.selection_changed.connect(self._count)

    def _count(self) -> None:
        self.changes += 1

    def test_no_selection_means_unrestricted(self) -> None:
        self.assertIsNone(self.sel.mask(SHAPE))
        self.assertTrue(self.sel.contains(3.0, 3.0, SHAPE))

    def test_rectangle_covers_exactly_the_pixels_whose_centers_are_inside(self) -> None:
        # View coords: pixel (row, col) spans [col, col+1) x [row, row+1).
        self.sel.set_rectangle(2.0, 4.0, 7.0, 9.0)  # cols 2..6, rows 4..8
        mask = self.sel.mask(SHAPE)
        expected = np.zeros(SHAPE, dtype=bool)
        expected[4:9, 2:7] = True
        np.testing.assert_array_equal(mask, expected)

    def test_rectangle_corners_can_be_given_in_any_order(self) -> None:
        self.sel.set_rectangle(7.0, 9.0, 2.0, 4.0)
        self.assertEqual(int(self.sel.mask(SHAPE).sum()), 25)

    def test_a_click_without_a_drag_is_not_a_selection(self) -> None:
        self.sel.set_rectangle(5.0, 5.0, 5.0, 5.0)
        self.assertFalse(self.sel.has_selection())
        self.sel.set_polygon([(1.0, 1.0), (2.0, 2.0)])
        self.assertFalse(self.sel.has_selection())

    def test_invert_flips_the_mask_and_back(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 7.0, 9.0)
        before = self.sel.mask(SHAPE).copy()
        self.sel.invert()
        np.testing.assert_array_equal(self.sel.mask(SHAPE), ~before)
        self.sel.invert()
        np.testing.assert_array_equal(self.sel.mask(SHAPE), before)

    def test_a_new_shape_does_not_inherit_inversion(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 7.0, 9.0)
        self.sel.invert()
        self.sel.set_rectangle(1.0, 1.0, 3.0, 3.0)
        self.assertFalse(self.sel.is_inverted())

    def test_clear_restores_unrestricted_and_emits_once(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 7.0, 9.0)
        self.changes = 0
        self.sel.clear()
        self.sel.clear()  # already clear: no second signal
        self.assertIsNone(self.sel.mask(SHAPE))
        self.assertEqual(self.changes, 1)

    def test_picking_all_clears_the_selection(self) -> None:
        self.sel.set_mode(AreaSelectionMode.RECTANGLE)
        self.sel.set_rectangle(2.0, 4.0, 7.0, 9.0)
        self.sel.set_mode(AreaSelectionMode.ALL)
        self.assertFalse(self.sel.has_selection())

    def test_contains_is_false_off_the_image_once_a_selection_exists(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 7.0, 9.0)
        self.assertTrue(self.sel.contains(3.5, 5.5, SHAPE))
        self.assertFalse(self.sel.contains(10.5, 5.5, SHAPE))
        self.assertFalse(self.sel.contains(-1.0, 5.5, SHAPE))
        self.sel.invert()
        self.assertTrue(self.sel.contains(10.5, 5.5, SHAPE))

    def test_mask_is_read_only_and_cached_per_shape(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 7.0, 9.0)
        mask = self.sel.mask(SHAPE)
        self.assertIs(mask, self.sel.mask(SHAPE))
        with self.assertRaises(ValueError):
            mask[0, 0] = True

    def test_triangle_polygon_area_is_close_to_geometric_area(self) -> None:
        verts = np.array([[2.0, 2.0], [22.0, 2.0], [2.0, 18.0]])
        mask = rasterize_polygon(verts, SHAPE)
        # Geometric area 0.5 * 20 * 16 = 160; pixel-center sampling is within a few percent.
        self.assertLess(abs(int(mask.sum()) - 160), 12)
        self.assertTrue(mask[3, 3])
        self.assertFalse(mask[17, 20])


if __name__ == "__main__":
    unittest.main()
