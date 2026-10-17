"""Compressed masks and the Auto mask batch: `MaskChange` and `MaskModule.set_mask_changes` (2026-10-17).

Pure module logic, no widgets. What is worth pinning: a stored mask comes back pixel for pixel but takes a few KB instead of 1.2 MB,
the caller's array is never shared, and a batch of masks is ONE undoable step with ONE signal that is skipped, with a message, when
the timeline changed after it (the same version-token guard as the Mask rows' commands).
Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md.
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
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.image_tools.mask.module import MaskModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

SHAPE = (900, 1300)


def _blob(x: int, y: int = 400, size: int = 12) -> np.ndarray:
    plane = np.zeros(SHAPE, dtype=bool)
    plane[y : y + size, x : x + size] = True
    return plane


class MaskChangeTests(unittest.TestCase):
    def test_the_pixels_come_back_exactly(self) -> None:
        rng = np.random.default_rng(1)
        for plane in (_blob(100), rng.random(SHAPE) > 0.5, np.zeros(SHAPE, dtype=bool), np.ones((7, 5), dtype=bool)):
            change = MaskChange(frame=(3, 550.0), scope="individual", mask=plane)
            np.testing.assert_array_equal(change.mask, plane)
            self.assertEqual(change.mask.dtype, bool)

    def test_a_sparse_mask_takes_kilobytes_not_megabytes(self) -> None:
        change = MaskChange(frame=(0, 500.0), scope="persistent", mask=_blob(100))
        self.assertLess(change.stored_bytes, 3_000)  # a full plane is 1,170,000 bytes
        wide = np.zeros(SHAPE, dtype=bool)
        wide[100:700, 200:1000] = True  # a big hand-drawn region compresses too
        self.assertLess(MaskChange(frame=(0, 500.0), scope="persistent", mask=wide).stored_bytes, 20_000)

    def test_the_decoded_array_is_read_only_and_the_callers_array_is_never_shared(self) -> None:
        plane = _blob(100)
        change = MaskChange(frame=(0, 500.0), scope="persistent", mask=plane)
        plane[:] = True  # the caller keeps working on its own array
        self.assertEqual(int(change.mask.sum()), 144)
        with self.assertRaises(ValueError):
            change.mask[0, 0] = True

    def test_equal_pixels_at_the_same_frame_and_scope_compare_equal(self) -> None:
        a = MaskChange((1, 500.0), "individual", _blob(100))
        self.assertEqual(a, MaskChange((1, 500.0), "individual", _blob(100)))
        self.assertNotEqual(a, MaskChange((1, 500.0), "individual", _blob(101)))
        self.assertNotEqual(a, MaskChange((1, 510.0), "individual", _blob(100)))
        self.assertNotEqual(a, MaskChange((1, 500.0), "persistent", _blob(100)))
        self.assertNotEqual(a, None)
        self.assertEqual(hash(a), hash(MaskChange((1, 500.0), "individual", _blob(100))))

    def test_moving_a_change_keeps_its_pixels_without_decoding_them(self) -> None:
        a = MaskChange((1, 500.0), "persistent", _blob(100))
        moved = a.moved((1, 500.0), "individual")
        self.assertEqual((moved.scope, moved.frame), ("individual", (1, 500.0)))
        np.testing.assert_array_equal(moved.mask, a.mask)
        self.assertIs(moved._packed, a._packed)

    def test_a_mask_must_be_two_dimensional(self) -> None:
        with self.assertRaises(ValueError):
            MaskChange((0, 500.0), "individual", np.zeros(5, dtype=bool))


class _Recorder:
    def __init__(self, module: MaskModule) -> None:
        self.changes: list = []
        self.skipped: list[str] = []
        module.mask_changed.connect(self.changes.append)
        module.undo_skipped.connect(self.skipped.append)


class BatchTests(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.mask = MaskModule()
        self.events = _Recorder(self.mask)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _items(self):
        return [((3, 500.0 + 10 * i), "individual", _blob(100 * i)) for i in range(4)]

    def test_many_masks_are_one_step_one_signal_and_one_undo(self) -> None:
        self.assertEqual(self.mask.set_mask_changes(self._items(), "Auto mask"), 4)
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0, 510.0, 520.0, 530.0)})
        self.assertEqual(len(self.events.changes), 1)
        change = self.events.changes[0]
        self.assertEqual((change.reason, change.frame, change.scope), ("mask_batch", None, None))
        self.assertEqual(change.frames, ((3, 500.0), (3, 510.0), (3, 520.0), (3, 530.0)))
        self.assertEqual(undo_manager.undo_label, "Auto mask")
        np.testing.assert_array_equal(self.mask.resolve_mask_source((3, 520.0))[1], _blob(200))

        undo_manager.undo()
        self.assertFalse(self.mask.has_mask_changes())
        self.assertEqual(len(self.events.changes), 2)  # the undo is one signal too
        self.assertFalse(undo_manager.can_undo)
        undo_manager.redo()
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0, 510.0, 520.0, 530.0)})
        np.testing.assert_array_equal(self.mask.resolve_mask_source((3, 530.0))[1], _blob(300))

    def test_what_is_stored_already_is_skipped_and_nothing_at_all_is_not_a_step(self) -> None:
        self.mask.set_mask_changes(self._items())
        undo_manager.clear()
        self.events.changes.clear()
        self.assertEqual(self.mask.set_mask_changes(self._items()), 0)
        self.assertEqual((self.events.changes, undo_manager.can_undo), ([], False))
        changed = self._items()
        changed[1] = ((3, 510.0), "individual", _blob(555))
        self.assertEqual(self.mask.set_mask_changes(changed), 1)
        undo_manager.undo()
        np.testing.assert_array_equal(self.mask.resolve_mask_source((3, 510.0))[1], _blob(100))  # the old mask is back

    def test_a_frame_given_twice_keeps_the_last_mask_and_undo_removes_it(self) -> None:
        self.mask.set_mask_changes([((1, 500.0), "individual", _blob(10)), ((1, 500.0), "individual", _blob(20))])
        np.testing.assert_array_equal(self.mask.resolve_mask_source((1, 500.0))[1], _blob(20))
        undo_manager.undo()
        self.assertIsNone(self.mask.resolve_mask_source((1, 500.0)))

    def test_persistent_masks_are_keyed_by_cube(self) -> None:
        self.mask.set_mask_changes([((2, 500.0), "persistent", _blob(10)), ((2, 600.0), "persistent", _blob(20))])
        self.assertEqual(self.mask.persistent_points(), {2: 600.0})  # one persistent mask per cube: the last wins

    def test_an_unknown_scope_is_refused_and_nothing_is_written(self) -> None:
        with self.assertRaises(ValueError):
            self.mask.set_mask_changes([((1, 500.0), "individual", _blob(10)), ((1, 510.0), "forever", _blob(10))])
        self.assertFalse(self.mask.has_mask_changes())
        self.assertFalse(undo_manager.can_undo)

    def test_undo_is_skipped_with_a_message_when_the_mask_was_changed_after_the_batch(self) -> None:
        """Painting is not undoable: an undo of the batch must never overwrite a stroke made after it."""
        self.mask.set_mask_changes(self._items())
        self.mask.set_mask_change((3, 500.0), "individual", _blob(777))  # a stroke after the batch
        undo_manager.undo()
        self.assertEqual(len(self.events.skipped), 1)
        self.assertIn("was not undone", self.events.skipped[0])
        np.testing.assert_array_equal(self.mask.resolve_mask_source((3, 500.0))[1], _blob(777))
        self.assertEqual(len(self.mask.individual_cubes()[3]), 4)

    def test_the_batch_costs_kilobytes_in_memory(self) -> None:
        items = [((3, 500.0 + i), "individual", _blob(i * 3)) for i in range(300)]
        self.mask.set_mask_changes(items)
        stored = sum(change.stored_bytes for change in self.mask.mask_changes())
        self.assertLess(stored, 300 * 3_000)  # 300 full planes would be ~350 MB


if __name__ == "__main__":
    unittest.main()
