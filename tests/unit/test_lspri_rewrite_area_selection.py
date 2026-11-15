"""Logic tests for the LSPRi rewrite's area selection (`AreaSelectionModule`).

Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch (see
`test_lspri_rewrite_analysis_core.py`). No Qt event loop, no files.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.selection import AreaSelectionMode, AreaSelectionModule
    from lspr_imaging_app.selection.area_selection_module import ADD, REPLACE, SUBTRACT, _Snapshot, rasterize_polygon
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

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


def _blob(row: int, col: int, size: int = 4) -> np.ndarray:
    region = np.zeros(SHAPE, dtype=bool)
    region[row : row + size, col : col + size] = True
    return region


class AreaSelectionUndoTest(unittest.TestCase):
    """Every change of the selection is one undo step (2026-11-15); `reset` (a new pixel grid) is none and voids older steps."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.sel = AreaSelectionModule()
        self.skipped: list[str] = []
        self.sel.undo_skipped.connect(self.skipped.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _mask(self) -> np.ndarray | None:
        mask = self.sel.mask(SHAPE)
        return None if mask is None else mask.copy()

    def test_every_kind_of_change_is_a_step_with_its_own_label(self) -> None:
        steps = [
            ("Rectangle selection", lambda: self.sel.set_rectangle(2.0, 4.0, 9.0, 9.0)),
            ("Lasso selection", lambda: self.sel.set_polygon([(1.0, 1.0), (12.0, 1.0), (1.0, 12.0)])),
            ("Select with the wand", lambda: self.sel.combine_region(_blob(2, 2), REPLACE)),
            ("Add to selection", lambda: self.sel.combine_region(_blob(10, 10), ADD)),
            ("Take away from selection", lambda: self.sel.combine_region(_blob(2, 2), SUBTRACT)),
            ("Invert selection", self.sel.invert),
            ("Deselect", self.sel.clear),
        ]
        for label, action in steps:
            action()
            self.assertEqual(undo_manager.undo_label, label)

    def test_undo_and_redo_walk_back_and_forth_through_the_exact_states(self) -> None:
        states = [None]
        self.sel.set_rectangle(2.0, 4.0, 9.0, 9.0)
        states.append(self._mask())
        self.sel.combine_region(_blob(12, 15), ADD)
        states.append(self._mask())
        self.sel.invert()
        states.append(self._mask())
        self.sel.clear()
        states.append(None)
        for expected in reversed(states[:-1]):
            undo_manager.undo()
            got = self._mask()
            if expected is None:
                self.assertIsNone(got)
            else:
                np.testing.assert_array_equal(got, expected)
        self.assertFalse(undo_manager.can_undo)
        for expected in states[1:]:
            undo_manager.redo()
            got = self._mask()
            if expected is None:
                self.assertIsNone(got)
            else:
                np.testing.assert_array_equal(got, expected)

    def test_undoing_an_add_brings_back_the_shape_not_only_its_pixels(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 9.0, 9.0)
        outline = self.sel.vertices()
        self.sel.combine_region(_blob(12, 15), ADD)
        self.assertIsNone(self.sel.vertices(), "an add turns the shape into pixels")
        undo_manager.undo()
        np.testing.assert_array_equal(self.sel.vertices(), outline)
        self.assertIsNone(self.sel.region())

    def test_undoing_an_invert_restores_the_flag(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 9.0, 9.0)
        self.sel.invert()
        self.assertTrue(self.sel.is_inverted())
        undo_manager.undo()
        self.assertFalse(self.sel.is_inverted())
        undo_manager.redo()
        self.assertTrue(self.sel.is_inverted())

    def test_a_change_that_changes_nothing_is_not_a_step(self) -> None:
        self.sel.clear()
        self.sel.invert()
        self.sel.combine_region(_blob(2, 2), SUBTRACT)  # nothing to take away from
        self.sel.combine_region(np.zeros(SHAPE, dtype=bool), REPLACE)  # an empty result with nothing selected
        self.assertFalse(undo_manager.can_undo)
        self.sel.combine_region(_blob(2, 2), REPLACE)
        undo_manager.clear()
        self.sel.combine_region(_blob(2, 2), REPLACE)  # the same region again
        self.assertFalse(undo_manager.can_undo)

    def test_a_subtract_that_empties_the_selection_is_one_step_that_undo_restores(self) -> None:
        self.sel.combine_region(_blob(2, 2), REPLACE)
        self.sel.combine_region(_blob(2, 2), SUBTRACT)
        self.assertFalse(self.sel.has_selection())
        self.assertEqual(undo_manager.undo_label, "Take away from selection")
        undo_manager.undo()
        np.testing.assert_array_equal(self._mask(), _blob(2, 2))

    def test_reset_is_not_a_step_and_voids_the_older_steps(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 9.0, 9.0)
        self.sel.reset()  # the pixel grid changed
        self.assertFalse(self.sel.has_selection())
        self.assertEqual(undo_manager.undo_label, "Rectangle selection", "reset pushed no step of its own")
        undo_manager.undo()
        self.assertFalse(self.sel.has_selection(), "the old rectangle is not brought back onto a new grid")
        self.assertEqual(len(self.skipped), 1)
        self.assertIn("Rectangle selection", self.skipped[0])
        undo_manager.redo()
        self.assertFalse(self.sel.has_selection())
        self.assertEqual(len(self.skipped), 2)

    def test_reset_with_no_selection_still_voids_the_steps_made_before_it(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 9.0, 9.0)
        self.sel.clear()  # undoing this would bring the rectangle (of the old grid) back
        self.sel.reset()
        undo_manager.undo()
        self.assertFalse(self.sel.has_selection())
        self.assertEqual(len(self.skipped), 1)

    def test_a_step_made_after_a_reset_undoes_normally(self) -> None:
        self.sel.set_rectangle(2.0, 4.0, 9.0, 9.0)
        self.sel.reset()
        self.sel.combine_region(_blob(5, 5), REPLACE)
        undo_manager.undo()
        self.assertFalse(self.sel.has_selection())
        self.assertEqual(self.skipped, [])
        undo_manager.redo()
        np.testing.assert_array_equal(self._mask(), _blob(5, 5))

    def test_a_snapshot_round_trips_any_pixel_map_exactly_and_is_small_for_a_particle(self) -> None:
        rng = np.random.default_rng(11)
        noisy = rng.random((37, 53)) > 0.5  # odd sizes: the bit packing pads the last byte
        snapshot = _Snapshot.of(None, noisy, False)
        np.testing.assert_array_equal(snapshot.region(), noisy)
        self.assertFalse(snapshot.region().flags.writeable)
        particle = np.zeros((1000, 1200), dtype=bool)
        particle[400:430, 600:630] = True
        self.assertLess(len(_Snapshot.of(None, particle, False).region_packed), 2000, "a few KB at most for a small region")


if __name__ == "__main__":
    unittest.main()
