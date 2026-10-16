"""`MaskModule`'s queries and undoable commands for the Mask rows under the sliders (2026-10-16).

Pure module logic, no widgets. The part worth pinning is the version-token guard: painting and loading are not undoable, so an
undo entry of a row command must never overwrite a mask that was changed after it.
Design: apps/LSPRi/eva/docs/mask_rows_2026-10-16.md.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask.module import MaskModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


def _mask(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.random((8, 8)) > 0.5


class _Recorder:
    def __init__(self, module: MaskModule) -> None:
        self.changes: list[tuple[tuple[int, float] | None, str | None]] = []
        self.skipped: list[str] = []
        module.mask_changed.connect(lambda change: self.changes.append((change.frame, change.scope)))
        module.undo_skipped.connect(self.skipped.append)


class MaskRowQueriesTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.mask = MaskModule()
        self.mask.set_mask_change((2, 500.0), "persistent", _mask(1))
        self.mask.set_mask_change((5, 600.0), "persistent", _mask(2))
        self.mask.set_mask_change((4, 550.0), "individual", _mask(3))
        self.mask.set_mask_change((4, 500.0), "individual", _mask(4))
        self.mask.set_mask_change((2, 550.0), "individual", _mask(5))

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_persistent_points_are_the_cubes_with_the_wavelength_they_were_drawn_at(self) -> None:
        self.assertEqual(self.mask.persistent_points(), {2: 500.0, 5: 600.0})

    def test_individual_masks_are_grouped_by_cube_with_ascending_wavelengths(self) -> None:
        self.assertEqual(self.mask.individual_cubes(), {2: (550.0,), 4: (500.0, 550.0)})
        self.assertEqual(self.mask.individual_wavelengths(4), (500.0, 550.0))
        self.assertEqual(self.mask.individual_wavelengths(3), ())

    def test_the_per_cube_index_follows_every_kind_of_write(self) -> None:
        self.assertEqual(self.mask.individual_wavelengths(3), ())
        self.mask.set_mask_change((3, 520.0), "individual", _mask(6))
        self.assertEqual(self.mask.individual_wavelengths(3), (520.0,))
        self.mask.set_mask_change((3, 520.0), "individual", None)
        self.assertEqual(self.mask.individual_wavelengths(3), ())
        self.mask.clear_all_masks()
        self.assertEqual((self.mask.individual_cubes(), self.mask.persistent_points(), self.mask.has_mask_changes()), ({}, {}, False))

    def test_queries_do_not_push_undo_entries(self) -> None:
        self.assertFalse(undo_manager.can_undo)  # painting and loading stay non-undoable


class MaskRowCommandsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.mask = MaskModule()
        self.persistent_2 = _mask(1)
        self.persistent_5 = _mask(2)
        self.individual_a = _mask(3)
        self.individual_b = _mask(4)
        self.mask.set_mask_change((2, 550.0), "persistent", self.persistent_2)
        self.mask.set_mask_change((5, 600.0), "persistent", self.persistent_5)
        self.mask.set_mask_change((4, 550.0), "individual", self.individual_a)
        self.mask.set_mask_change((4, 500.0), "individual", self.individual_b)
        self.events = _Recorder(self.mask)

    def tearDown(self) -> None:
        undo_manager.clear()

    def resolved(self, frame: tuple[int, float]):
        return self.mask.resolve_mask_source(frame)

    def test_delete_persistent_mask_removes_it_and_undo_redo_restore_the_very_same_mask(self) -> None:
        self.mask.delete_persistent_mask(2)
        self.assertEqual(list(self.mask.persistent_points()), [5])
        self.assertIsNone(self.resolved((3, 550.0)))  # nothing before cube 5 applies any more
        self.assertEqual(self.events.changes[-1], ((2, 550.0), "persistent"))
        undo_manager.undo()
        np.testing.assert_array_equal(self.resolved((3, 550.0))[1], self.persistent_2)
        undo_manager.redo()
        self.assertEqual(list(self.mask.persistent_points()), [5])

    def test_deleting_what_is_not_there_does_nothing_and_pushes_nothing(self) -> None:
        self.mask.delete_persistent_mask(3)
        self.mask.delete_individual_masks([(4, 999.0), (9, 500.0)])
        self.assertFalse(undo_manager.can_undo)
        self.assertEqual(self.events.changes, [])

    def test_delete_individual_masks_of_several_frames_is_one_undo_step(self) -> None:
        self.mask.delete_individual_masks([(4, 500.0), (4, 550.0), (4, 123.0)])
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertEqual(sorted(frame for frame, _scope in self.events.changes), [(4, 500.0), (4, 550.0)])
        self.assertEqual(undo_manager.undo_label, "Delete masks")
        undo_manager.undo()
        self.assertEqual(self.mask.individual_cubes(), {4: (500.0, 550.0)})
        self.assertFalse(undo_manager.can_undo)

    def test_a_deleted_individual_mask_falls_back_to_the_persistent_one(self) -> None:
        source, _mask_array, scope = self.resolved((4, 550.0))
        self.assertEqual((source, scope), ((4, 550.0), "individual"))
        self.mask.delete_individual_masks([(4, 550.0)])
        source, mask_array, scope = self.resolved((4, 550.0))
        self.assertEqual((source, scope), ((2, 550.0), "persistent"))
        np.testing.assert_array_equal(mask_array, self.persistent_2)

    def test_make_mask_individual_keeps_the_frame_it_was_drawn_at_and_sends_later_cubes_back(self) -> None:
        self.mask.make_mask_individual(5)
        self.assertEqual(list(self.mask.persistent_points()), [2])
        self.assertEqual(self.mask.individual_wavelengths(5), (600.0,))
        source, mask_array, scope = self.resolved((5, 600.0))
        self.assertEqual((source, scope), ((5, 600.0), "individual"))
        np.testing.assert_array_equal(mask_array, self.persistent_5)
        source, _array, scope = self.resolved((6, 600.0))  # later cubes: the earlier persistent mask again
        self.assertEqual((source, scope), ((2, 550.0), "persistent"))
        undo_manager.undo()
        self.assertEqual(list(self.mask.persistent_points()), [2, 5])
        self.assertEqual(self.mask.individual_wavelengths(5), ())

    def test_make_mask_individual_refuses_to_overwrite_an_individual_mask_of_that_frame(self) -> None:
        self.mask.set_mask_change((5, 600.0), "individual", _mask(9))
        undo_manager.clear()
        with self.assertRaisesRegex(ValueError, "already has an individual mask"):
            self.mask.make_mask_individual(5)
        self.assertFalse(undo_manager.can_undo)

    def test_make_mask_persistent_applies_to_the_whole_cube_and_the_cubes_after_it(self) -> None:
        self.mask.make_mask_persistent((4, 550.0))
        self.assertEqual(list(self.mask.persistent_points()), [2, 4, 5])
        self.assertEqual(self.mask.individual_wavelengths(4), (500.0,))  # the other frame's own mask stays
        source, mask_array, scope = self.resolved((4, 600.0))
        self.assertEqual((source, scope), ((4, 550.0), "persistent"))
        np.testing.assert_array_equal(mask_array, self.individual_a)
        undo_manager.undo()
        self.assertEqual(list(self.mask.persistent_points()), [2, 5])
        self.assertEqual(self.mask.individual_wavelengths(4), (500.0, 550.0))

    def test_make_mask_persistent_refuses_to_overwrite_the_persistent_mask_of_that_cube(self) -> None:
        self.mask.set_mask_change((5, 500.0), "individual", _mask(9))
        undo_manager.clear()
        with self.assertRaisesRegex(ValueError, "already has its own persistent mask"):
            self.mask.make_mask_persistent((5, 500.0))
        self.assertFalse(undo_manager.can_undo)

    def test_converting_a_frame_without_an_individual_mask_does_nothing(self) -> None:
        self.mask.make_mask_persistent((3, 500.0))
        self.mask.make_mask_individual(3)
        self.assertFalse(undo_manager.can_undo)


class StaleUndoGuardTest(unittest.TestCase):
    """Painting is not undoable, so an undo entry must not overwrite what was drawn after it."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.mask = MaskModule()
        self.original = _mask(1)
        self.mask.set_mask_change((2, 500.0), "persistent", self.original)
        self.mask.set_mask_change((4, 500.0), "persistent", _mask(2))
        self.events = _Recorder(self.mask)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_undo_after_a_newer_stroke_is_skipped_with_a_reason_and_the_stroke_survives(self) -> None:
        self.mask.delete_persistent_mask(2)
        newer = _mask(7)
        self.mask.set_mask_change((2, 500.0), "persistent", newer)  # redrawn after the delete, not undoable
        undo_manager.undo()
        np.testing.assert_array_equal(self.mask.resolve_mask_source((2, 500.0))[1], newer)  # not overwritten
        self.assertEqual(len(self.events.skipped), 1)
        self.assertIn("not undone", self.events.skipped[0])
        undo_manager.redo()  # the entry is on the redo stack now; redoing it must not delete the newer mask either
        np.testing.assert_array_equal(self.mask.resolve_mask_source((2, 500.0))[1], newer)
        self.assertEqual(len(self.events.skipped), 2)
        self.assertIn("not redone", self.events.skipped[1])

    def test_row_commands_undo_and_redo_in_order(self) -> None:
        self.mask.delete_persistent_mask(2)
        self.mask.delete_persistent_mask(4)
        undo_manager.undo()
        undo_manager.undo()
        self.assertEqual(list(self.mask.persistent_points()), [2, 4])
        undo_manager.redo()
        undo_manager.redo()
        self.assertEqual(self.mask.persistent_points(), {})
        self.assertEqual(self.events.skipped, [])

    def test_a_stroke_between_two_commands_protects_only_the_older_one(self) -> None:
        self.mask.delete_persistent_mask(2)  # A
        self.mask.set_mask_change((9, 500.0), "individual", _mask(8))  # a stroke somewhere else, not undoable
        self.mask.delete_persistent_mask(4)  # B
        undo_manager.undo()  # B: the timeline is as B left it
        self.assertEqual(list(self.mask.persistent_points()), [4])
        self.assertEqual(self.events.skipped, [])
        undo_manager.undo()  # A: a stroke happened after it, so it is skipped
        self.assertEqual(list(self.mask.persistent_points()), [4])
        self.assertEqual(len(self.events.skipped), 1)

    def test_loading_a_session_or_clearing_also_protects_older_entries(self) -> None:
        self.mask.delete_persistent_mask(2)
        self.mask.clear_all_masks()
        undo_manager.undo()
        self.assertEqual(self.mask.persistent_points(), {})
        self.assertEqual(len(self.events.skipped), 1)


if __name__ == "__main__":
    unittest.main()
