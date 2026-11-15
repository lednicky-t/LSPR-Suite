"""Logic tests for the selection lock and the Intersect mode of `AreaSelectionModule` (2026-11-15).

The lock is the fence (where tools may act, one level), the selection is the product (always inside the fence). The tests pin the
rules the maintainer decided: a locked selection is not a selection any more (a lock alone selects nothing), everything is
clipped to the lock, a new lock replaces the old one, Unlock keeps what was selected, every step is undoable, a new pixel grid
drops the lock. No Qt event loop, no files.
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
    from lspr_imaging_app.selection import AreaSelectionModule
    from lspr_imaging_app.selection.area_selection_module import ADD, INTERSECT, REPLACE, SUBTRACT, _Snapshot
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

SHAPE = (20, 30)


def _box(top: int, bottom: int, left: int, right: int) -> np.ndarray:
    pixels = np.zeros(SHAPE, dtype=bool)
    pixels[top:bottom, left:right] = True
    return pixels


class SelectionLockTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.sel = AreaSelectionModule()
        self.skipped: list[str] = []
        self.sel.undo_skipped.connect(self.skipped.append)
        self.lock_signals = 0
        self.sel.lock_changed.connect(self._count_lock)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _count_lock(self) -> None:
        self.lock_signals += 1

    def _lock_box(self, top: int = 4, bottom: int = 14, left: int = 5, right: int = 20) -> np.ndarray:
        """Select a box and lock it; returns the box."""
        box = _box(top, bottom, left, right)
        self.sel.combine_region(box, REPLACE)
        self.assertTrue(self.sel.lock_selection(SHAPE))
        return box

    # -- the lock itself --------------------------------------------------------

    def test_locking_turns_the_selection_into_the_fence_and_clears_it(self) -> None:
        box = self._lock_box()
        self.assertTrue(self.sel.has_lock())
        self.assertFalse(self.sel.has_selection(), "a locked selection is not a selection any more")
        np.testing.assert_array_equal(self.sel.lock_mask(SHAPE), box)
        self.assertEqual(self.lock_signals, 1)

    def test_a_lock_alone_selects_nothing_but_limits_editing_to_itself(self) -> None:
        box = self._lock_box()
        self.assertIsNone(self.sel.selected_mask(SHAPE), "Add selection to the mask must find nothing to add")
        np.testing.assert_array_equal(self.sel.mask(SHAPE), box)
        self.assertTrue(self.sel.contains(6.5, 5.5, SHAPE))
        self.assertFalse(self.sel.contains(1.5, 1.5, SHAPE))

    def test_nothing_selected_cannot_be_locked(self) -> None:
        self.assertFalse(self.sel.lock_selection(SHAPE))
        self.assertFalse(self.sel.has_lock())
        self.assertFalse(undo_manager.can_undo)

    def test_unlocking_with_no_lock_does_nothing(self) -> None:
        self.assertFalse(self.sel.unlock())
        self.assertFalse(undo_manager.can_undo)

    def test_a_new_lock_replaces_the_old_one_and_can_only_be_smaller(self) -> None:
        first = self._lock_box(2, 18, 2, 28)
        inner = _box(5, 9, 8, 12)
        self.sel.combine_region(inner, REPLACE)
        self.assertTrue(self.sel.lock_selection(SHAPE))
        np.testing.assert_array_equal(self.sel.lock_mask(SHAPE), inner)
        self.assertTrue((self.sel.lock_mask(SHAPE) & ~first).sum() == 0)
        self.sel.unlock()
        self.assertFalse(self.sel.has_lock(), "one level only: Unlock drops the lock, there is no outer one to fall back to")

    # -- everything is clipped to the lock -----------------------------------------

    def test_every_new_selection_is_clipped_to_the_lock(self) -> None:
        box = self._lock_box()
        self.sel.combine_region(_box(0, 20, 0, 30), REPLACE)  # the whole image asked for
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), box)
        self.sel.set_rectangle(0.0, 0.0, 30.0, 20.0)  # a rectangle bigger than the lock
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), box)
        self.assertIsNone(self.sel.vertices(), "under a lock a shape becomes the pixels it covers, clipped")
        self.sel.set_polygon([(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0)])
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), box)

    def test_a_result_inside_the_lock_equals_the_unlocked_result_and_the_lock(self) -> None:
        """Compute-then-cut: whatever a tool finds, the lock only cuts it."""
        rng = np.random.default_rng(3)
        found = rng.random(SHAPE) > 0.6
        free = AreaSelectionModule()
        free.combine_region(found, REPLACE)
        box = self._lock_box()
        self.sel.combine_region(found, REPLACE)
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), free.selected_mask(SHAPE) & box)

    def test_a_region_wholly_outside_the_lock_selects_nothing(self) -> None:
        self._lock_box(4, 14, 5, 20)
        self.assertFalse(self.sel.combine_region(_box(16, 20, 22, 30), REPLACE))
        self.assertFalse(self.sel.has_selection())

    def test_invert_inside_a_lock_is_the_lock_minus_the_selection(self) -> None:
        box = self._lock_box()
        part = _box(6, 10, 8, 12)
        self.sel.combine_region(part, REPLACE)
        self.sel.invert()
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), box & ~part)

    def test_add_with_only_a_lock_does_not_add_the_whole_lock(self) -> None:
        self._lock_box()
        part = _box(6, 8, 8, 10)
        self.sel.combine_region(part, ADD)
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), part)

    def test_subtract_with_only_a_lock_has_nothing_to_take_away_from(self) -> None:
        self._lock_box()
        self.assertFalse(self.sel.combine_region(_box(6, 8, 8, 10), SUBTRACT))

    # -- Intersect ----------------------------------------------------------------

    def test_intersect_keeps_what_both_share(self) -> None:
        self.sel.combine_region(_box(2, 12, 2, 12), REPLACE)
        self.sel.combine_region(_box(8, 18, 8, 18), INTERSECT)
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), _box(8, 12, 8, 12))

    def test_intersect_with_nothing_selected_is_the_region_alone(self) -> None:
        self.sel.combine_region(_box(8, 18, 8, 18), INTERSECT)
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), _box(8, 18, 8, 18))

    def test_intersect_of_regions_that_do_not_touch_clears_the_selection_as_one_step(self) -> None:
        self.sel.combine_region(_box(0, 4, 0, 4), REPLACE)
        self.sel.combine_region(_box(10, 14, 10, 14), INTERSECT)
        self.assertFalse(self.sel.has_selection())
        self.assertEqual(undo_manager.undo_label, "Intersect with selection")

    def test_a_rectangle_in_add_mode_joins_the_selection_as_pixels(self) -> None:
        self.sel.combine_region(_box(0, 4, 0, 4), REPLACE)
        self.sel.set_rectangle(10.0, 10.0, 14.0, 14.0, ADD, SHAPE)
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), _box(0, 4, 0, 4) | _box(10, 14, 10, 14))

    def test_a_rectangle_in_intersect_mode_cuts_the_selection(self) -> None:
        self.sel.combine_region(_box(0, 12, 0, 12), REPLACE)
        self.sel.set_rectangle(6.0, 6.0, 20.0, 20.0, INTERSECT, SHAPE)
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), _box(6, 12, 6, 12))

    def test_a_combined_shape_needs_the_image_size(self) -> None:
        with self.assertRaises(ValueError):
            self.sel.set_rectangle(1.0, 1.0, 4.0, 4.0, ADD)

    def test_the_sticky_combine_mode_is_remembered_and_announced(self) -> None:
        seen: list[str] = []
        self.sel.combine_mode_changed.connect(seen.append)
        self.assertEqual(self.sel.combine_mode(), REPLACE)
        self.sel.set_combine_mode(INTERSECT)
        self.sel.set_combine_mode(INTERSECT)
        self.assertEqual(self.sel.combine_mode(), INTERSECT)
        self.assertEqual(seen, [INTERSECT])
        with self.assertRaises(ValueError):
            self.sel.set_combine_mode("xor")
        self.assertFalse(undo_manager.can_undo, "a tool setting is not an undo step")

    # -- unlock ---------------------------------------------------------------------

    def test_unlock_keeps_the_selection(self) -> None:
        self._lock_box()
        part = _box(6, 10, 8, 12)
        self.sel.combine_region(part, REPLACE)
        self.assertTrue(self.sel.unlock())
        self.assertFalse(self.sel.has_lock())
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), part)

    def test_unlocking_an_inverted_selection_keeps_only_what_it_showed_inside_the_lock(self) -> None:
        box = self._lock_box()
        part = _box(6, 10, 8, 12)
        self.sel.combine_region(part, REPLACE)
        self.sel.invert()
        self.sel.unlock()
        self.assertFalse(self.sel.is_inverted())
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), box & ~part)

    def test_deselect_keeps_the_lock(self) -> None:
        box = self._lock_box()
        self.sel.combine_region(_box(6, 10, 8, 12), REPLACE)
        self.sel.clear()
        self.assertTrue(self.sel.has_lock())
        np.testing.assert_array_equal(self.sel.mask(SHAPE), box)

    # -- undo -----------------------------------------------------------------------

    def test_lock_and_unlock_are_undoable_steps_that_restore_the_selection_too(self) -> None:
        box = _box(4, 14, 5, 20)
        self.sel.combine_region(box, REPLACE)
        self.sel.lock_selection(SHAPE)
        self.assertEqual(undo_manager.undo_label, "Lock selection")
        undo_manager.undo()
        self.assertFalse(self.sel.has_lock())
        np.testing.assert_array_equal(self.sel.selected_mask(SHAPE), box)
        undo_manager.redo()
        self.assertTrue(self.sel.has_lock())
        self.assertFalse(self.sel.has_selection())
        self.sel.unlock()
        self.assertEqual(undo_manager.undo_label, "Unlock")
        undo_manager.undo()
        np.testing.assert_array_equal(self.sel.lock_mask(SHAPE), box)

    def test_ordinary_selection_steps_inside_a_lock_undo_inside_the_lock(self) -> None:
        box = self._lock_box()
        self.sel.combine_region(_box(6, 10, 8, 12), REPLACE)
        undo_manager.undo()
        self.assertTrue(self.sel.has_lock())
        self.assertFalse(self.sel.has_selection())
        np.testing.assert_array_equal(self.sel.mask(SHAPE), box)

    def test_a_snapshot_round_trips_the_lock_exactly(self) -> None:
        rng = np.random.default_rng(5)
        noisy = rng.random((37, 53)) > 0.5
        snapshot = _Snapshot.of(None, None, False, noisy)
        np.testing.assert_array_equal(snapshot.lock(), noisy)
        self.assertFalse(snapshot.lock().flags.writeable)
        self.assertIsNone(_Snapshot.of(None, None, False).lock())

    # -- a new pixel grid ---------------------------------------------------------------

    def test_reset_drops_the_lock_and_voids_the_steps_that_hold_it(self) -> None:
        self._lock_box()
        self.sel.reset()
        self.assertFalse(self.sel.has_lock())
        self.assertIsNone(self.sel.mask(SHAPE))
        self.assertEqual(self.lock_signals, 2, "made, then dropped")
        undo_manager.undo()  # undoes "Lock selection" of the old grid
        self.assertFalse(self.sel.has_lock(), "a lock of the old grid is not brought back onto the new one")
        self.assertTrue(self.skipped)

    def test_a_lock_made_for_another_image_size_restricts_to_nothing_never_to_everything(self) -> None:
        self._lock_box()
        other = (10, 10)
        self.assertFalse(self.sel.mask(other).any())
        self.assertFalse(self.sel.contains(2.5, 2.5, other))


if __name__ == "__main__":
    unittest.main()
