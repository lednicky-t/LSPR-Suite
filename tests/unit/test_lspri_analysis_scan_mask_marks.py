"""Pure-logic tests for `analysis/scan_mask_marks.py`: which cubes had a persistent / an individual mask on the ROIs of a Sensorgram line."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan_mask_marks import mask_marks
    from lspr_imaging_app.analysis.scan_store import StoredScan
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

N_CUBE, ROI_IDS, WL = 5, (10, 11, 12), (500.0, 510.0, 520.0)


def _stored(masked: dict[tuple[int, int, int, int], int]) -> StoredScan:
    """A scan of 5 cubes x 3 ROIs x 3 wavelengths x 2 sides where only the given [cube, roi row, wavelength, side] cells lost pixels."""
    shape = (N_CUBE, len(ROI_IDS), len(WL), 2)
    n_masked = np.zeros(shape, dtype=np.uint32)
    for cell, count in masked.items():
        n_masked[cell] = count
    return StoredScan(
        path=Path("memory.h5"), attrs={}, cube_indices=tuple(range(N_CUBE)), roi_ids=ROI_IDS, wavelength_nm=WL,
        acquired_at_unix_ms=np.zeros((N_CUBE, len(WL)), dtype=np.int64), values={"mean": np.ones(shape)}, std=np.ones(shape),
        n_valid=np.full(shape, 100, dtype=np.uint32), n_nominal=np.full(shape, 110, dtype=np.uint32), n_masked=n_masked,
        cell_hash=np.ones((N_CUBE, len(ROI_IDS)), dtype=np.uint64), recipe_id=np.ones((N_CUBE, len(ROI_IDS)), dtype=np.int32),
        layout_id=np.ones(N_CUBE, dtype=np.int32),
    )


class MaskMarksTests(unittest.TestCase):
    def test_nothing_is_marked_when_no_mask_removed_pixels(self) -> None:
        persistent, individual = mask_marks(_stored({}), ROI_IDS, {2: (510.0,)})
        self.assertFalse(persistent.any() or individual.any())

    def test_a_mask_that_touched_no_selected_roi_gets_no_mark(self) -> None:
        stored = _stored({(1, 2, 0, 0): 7})  # cube 1, ROI 12
        self.assertFalse(mask_marks(stored, (10, 11), {})[0].any())
        self.assertEqual(list(mask_marks(stored, (12,), {})[0]), [False, True, False, False, False])

    def test_the_sample_disk_and_the_reference_ring_both_count(self) -> None:
        for side in (0, 1):
            persistent, _ = mask_marks(_stored({(3, 0, 1, side): 1}), (10,), {})
            self.assertEqual(list(persistent), [False, False, False, True, False], side)

    def test_an_individual_mask_at_exactly_that_frame_gives_a_circle_otherwise_a_triangle(self) -> None:
        stored = _stored({(1, 0, 1, 0): 4, (2, 0, 0, 0): 4})  # cube 1 at 510 nm, cube 2 at 500 nm
        persistent, individual = mask_marks(stored, (10,), {1: (510.0,), 2: (520.0,)})  # cube 2's individual mask is at another wavelength
        self.assertEqual(list(individual), [False, True, False, False, False])
        self.assertEqual(list(persistent), [False, False, True, False, False])

    def test_one_cube_can_carry_both_kinds(self) -> None:
        stored = _stored({(4, 1, 0, 0): 2, (4, 1, 2, 1): 2})
        persistent, individual = mask_marks(stored, (11,), {4: (520.0,)})
        self.assertTrue(persistent[4] and individual[4])

    def test_a_line_made_of_several_rois_is_marked_when_any_member_was_touched(self) -> None:
        stored = _stored({(0, 0, 0, 0): 1, (2, 2, 0, 0): 1})
        self.assertEqual(list(mask_marks(stored, ROI_IDS, {})[0]), [True, False, True, False, False])

    def test_unknown_rois_and_cubes_are_skipped_and_wavelengths_match_to_a_hundredth_of_a_nanometre(self) -> None:
        stored = _stored({(1, 0, 1, 0): 3})
        persistent, _ = mask_marks(stored, (99,), {})
        self.assertFalse(persistent.any())
        _, individual = mask_marks(stored, (10,), {1: (510.004,), 77: (510.0,)})
        self.assertEqual(list(individual), [False, True, False, False, False])


if __name__ == "__main__":
    unittest.main()
