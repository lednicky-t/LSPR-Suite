"""The ignore-mask part of the cell hash is local (2026-10-28): a cell is hashed with the mask pixels inside its ROI's window, not with the whole mask.

The safety claim is "a cell whose hash did not change after a mask edit has the same numbers" (a hash that changes for no reason only costs a rescan; one
that stays when the numbers change would show a wrong number as current). It is tested on the numbers themselves: random ROI layouts, chromatic shifts and
random mask edits, `reduce_frame` before and after. Also: the cases that must stay whole-mask, the fallbacks, and the recognition of cells stamped with the
previous formula. No Qt; the plans come from a small fake source.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import scan_fingerprint
    from lspr_imaging_app.analysis.frame_reduce import reduce_frame
    from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes, stamped_by_previous_formula
    from lspr_imaging_app.analysis.scan_jobs import MaskRef, ScanSource, build_scan_plan
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings, RoiMask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

H, W = 70, 110
WAVELENGTHS = (0.0, 500.0, 510.0, 520.0)
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
AFFINES = {
    500.0: IDENTITY,
    510.0: np.array([[1.002, 0.001, 2.6], [-0.001, 0.998, -1.9]]),
    520.0: np.array([[0.997, -0.004, -3.1], [0.004, 1.003, 2.2]]),
}


class _World:
    """A tiny app: three cubes, a few ROIs, masks authored at chosen frames (each used only at its own frame, so no warp)."""

    def __init__(self, rois, masks=None, background=None):
        self.rois = rois
        self.masks: dict[tuple[int, float], np.ndarray] = dict(masks or {})
        self.background = background or BackgroundSettings()

    def source(self) -> ScanSource:
        return ScanSource(
            cube_indices=lambda: (0, 1, 2),
            wavelengths_for_cube=lambda c: WAVELENGTHS,
            is_spectral=lambda c, w: w != 0.0,
            record_path=lambda c, w: f"c{c}_w{int(w)}.tif",
            rois=lambda: self.rois, rois_at=lambda c: self.rois, has_timeline=lambda: False,
            chromatic_affine=lambda c, w: AFFINES.get(w, IDENTITY),
            chromatic_affine_between=lambda a, b: IDENTITY,
            resolve_mask=lambda c, w: MaskRef((c, w), "individual", lambda a=self.masks[(c, w)]: a) if (c, w) in self.masks else None,
            geometry=lambda: GeometrySettings(), background=lambda: self.background, detection=lambda: AreaRoiDetectionSettings(),
            default_reference_diameters=lambda: (11.0, 16.0),
        )

    def light(self):
        return build_scan_plan(self.source(), resolve_masks=False)

    def full(self):
        return build_scan_plan(self.source())


def _rois(rng, n=12):
    out = []
    for i in range(n):
        out.append(AreaRoi(
            i + 1, float(rng.uniform(-4, W + 4)), float(rng.uniform(-4, H + 4)), float(rng.uniform(5, 14)),
            reference_inner_diameter_px=float(rng.uniform(10, 18)), reference_outer_diameter_px=float(rng.uniform(18, 30)),
            reference_geometry_type="none" if rng.random() < 0.1 else "annulus",
        ))
    return out


def _blobs(rng, count) -> np.ndarray:
    yy, xx = np.mgrid[:H, :W]
    mask = np.zeros((H, W), dtype=bool)
    for _ in range(count):
        cy, cx, radius = rng.uniform(0, H), rng.uniform(0, W), rng.uniform(2, 9)
        mask |= (yy - cy) ** 2 + (xx - cx) ** 2 <= radius**2
    return mask


def _numbers(plan):
    """The scan's numbers for every cell of a *resolved* plan: reduce_frame per frame, on a fixed random image per frame. {(cube pos, roi pos): tuple}."""
    out = {}
    for p, job in enumerate(plan.jobs):
        per_frame = []
        for column, frame in zip(plan.axis_positions[p], job.frames):
            image = np.random.default_rng(100 * p + column).normal(3000.0, 40.0, (H, W)).astype(np.float32)
            image[5:9, 20:60] = np.nan
            per_frame.append(reduce_frame(job.rois, image, frame.ignore, frame.affine, ("mean",)))
        for r in range(len(plan.roi_ids)):
            out[(p, r)] = tuple(
                np.concatenate([np.ravel(getattr(f, name)[r] if name != "mean" else f.values["mean"][r]) for f in per_frame])
                for name in ("mean", "std", "n_valid", "n_nominal", "n_masked")
            )
    return out


def _same(a, b) -> bool:
    return all(np.array_equal(x, y, equal_nan=True) for x, y in zip(a, b))


class SafetyPropertyTests(unittest.TestCase):
    def test_a_cell_whose_hash_did_not_change_has_the_same_numbers(self) -> None:
        rng = np.random.default_rng(2028)
        unchanged_with_a_changed_mask = changed_hash = numbers_changed = 0
        for trial in range(30):
            world = _World(_rois(rng), {(1, 500.0): _blobs(rng, 3), (1, 510.0): _blobs(rng, 2), (2, 520.0): _blobs(rng, 4)})
            base_hash, base_numbers = cell_hashes(world.light()), _numbers(world.full())
            frame = [(1, 500.0), (1, 510.0), (2, 520.0)][trial % 3]
            before = world.masks[frame]
            if trial % 2:
                world.masks[frame] = before | _blobs(rng, 1)  # grow the mask somewhere
            else:
                world.masks[frame] = before & ~_blobs(rng, 1)  # or cut a piece out
            if np.array_equal(world.masks[frame], before):
                continue
            new_hash, new_numbers = cell_hashes(world.light()), _numbers(world.full())
            for p in range(3):
                for r in range(len(world.rois)):
                    if new_hash[p, r] == base_hash[p, r]:
                        unchanged_with_a_changed_mask += 1 if p == frame[0] else 0
                        self.assertTrue(_same(base_numbers[(p, r)], new_numbers[(p, r)]), f"trial {trial}: cell ({p}, {r}) kept its hash but its numbers changed")
                    else:
                        changed_hash += 1
                        numbers_changed += 0 if _same(base_numbers[(p, r)], new_numbers[(p, r)]) else 1
        # not vacuous: the edits left many cells of the edited cube alone, changed some, and some of the changes were visible in the numbers
        self.assertGreater(unchanged_with_a_changed_mask, 100)
        self.assertGreater(changed_hash, 10)
        self.assertGreater(numbers_changed, 5)


class LocalityTests(unittest.TestCase):
    def setUp(self) -> None:
        # three ROIs in a row, far apart; windows are about 14 px (sample 5, ring to 11 at most) plus margin
        self.rois = [AreaRoi(i + 1, x, 35.0, 10.0, reference_inner_diameter_px=14.0, reference_outer_diameter_px=22.0) for i, x in enumerate((15.0, 55.0, 95.0))]

    def _hash(self, mask) -> np.ndarray:
        return cell_hashes(_World(self.rois, {(1, 500.0): mask}).light())

    def test_only_the_roi_that_can_see_the_edit_changes(self) -> None:
        empty = np.zeros((H, W), dtype=bool)
        far = empty.copy()
        far[3:6, 3:6] = True  # nobody's window
        near_second = empty.copy()
        near_second[34:37, 55:58] = True
        base = self._hash(empty)
        self.assertTrue(np.array_equal(self._hash(far)[1], base[1]))  # a mask nobody can see counts for nothing
        edited = self._hash(near_second)
        self.assertNotEqual(edited[1][1], base[1][1])
        self.assertEqual((edited[1][0], edited[1][2]), (base[1][0], base[1][2]))
        self.assertTrue(np.array_equal(edited[[0, 2]], base[[0, 2]]))  # the cubes without the mask

    def test_an_empty_mask_hashes_like_no_mask(self) -> None:
        no_mask = cell_hashes(_World(self.rois).light())
        self.assertTrue(np.array_equal(self._hash(np.zeros((H, W), dtype=bool)), no_mask))

    def test_unresolved_and_resolved_plans_give_the_same_hashes(self) -> None:
        mask = _blobs(np.random.default_rng(5), 4)
        world = _World(self.rois, {(1, 500.0): mask, (2, 510.0): mask.copy()})
        self.assertTrue(np.array_equal(cell_hashes(world.light()), cell_hashes(world.full())))

    def test_the_cache_does_not_mix_up_masks_or_geometries(self) -> None:
        a, b = _blobs(np.random.default_rng(1), 3), _blobs(np.random.default_rng(2), 3)
        first = self._hash(a)
        self._hash(b)
        self.assertTrue(np.array_equal(self._hash(a), first))  # asking again, with the other mask cached in between

    def test_a_free_form_roi_sees_the_whole_mask(self) -> None:
        rois = list(self.rois) + [AreaRoi(4, 30.0, 10.0, 8.0, sample_geometry_type="mask", sample_mask=RoiMask(26, 6, np.ones((8, 9), dtype=bool)))]
        empty, far = np.zeros((H, W), dtype=bool), np.zeros((H, W), dtype=bool)
        far[60:64, 100:104] = True
        base = cell_hashes(_World(rois, {(1, 500.0): empty}).light())
        seen = cell_hashes(_World(rois, {(1, 500.0): far}).light())
        self.assertNotEqual(seen[1][3], base[1][3])  # no radius to bound it: any mask pixel counts
        self.assertTrue(np.array_equal(seen[1][:3], base[1][:3]))

    def test_background_that_cuts_the_masked_pixels_out_keeps_the_whole_mask_in_every_cell(self) -> None:
        background = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_mask=True, flatten_background_exclude_area_rois=False)
        empty, far = np.zeros((H, W), dtype=bool), np.zeros((H, W), dtype=bool)
        far[3:6, 3:6] = True
        base = cell_hashes(_World(self.rois, {(1, 500.0): empty}, background).light())
        seen = cell_hashes(_World(self.rois, {(1, 500.0): far}, background).light())
        self.assertTrue(np.all(seen[1] != base[1]))  # the background of the whole frame changes with any masked pixel
        background_off_mask = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_mask=False, flatten_background_exclude_area_rois=False)
        kept = cell_hashes(_World(self.rois, {(1, 500.0): empty}, background_off_mask).light())
        moved = cell_hashes(_World(self.rois, {(1, 500.0): far}, background_off_mask).light())
        self.assertTrue(np.array_equal(kept[1], moved[1]))  # background on but not looking at the mask: local again

    def test_a_plan_that_cannot_make_its_masks_falls_back_to_the_whole_mask(self) -> None:
        world = _World(self.rois, {(1, 500.0): np.zeros((H, W), dtype=bool)})
        plan = world.light()
        from dataclasses import replace

        blind = replace(plan, mask_resolver=None)
        self.assertTrue(np.array_equal(cell_hashes(blind), cell_hashes(plan, local_masks=False)))

    def test_a_mask_that_cannot_be_looked_at_counts_for_every_roi_of_its_cube(self) -> None:
        world = _World(self.rois, {(1, 500.0): _blobs(np.random.default_rng(8), 2)})
        plan = world.light()
        from dataclasses import replace

        def broken(key):
            raise OSError("cannot decode")

        with self.assertLogs(scan_fingerprint.logger, level="WARNING"):
            hashes = cell_hashes(replace(plan, mask_resolver=broken))
        healthy = cell_hashes(plan)
        self.assertTrue(np.all(hashes[1] != healthy[1]))  # stale rather than wrong, for all of the cube's ROIs
        self.assertTrue(np.array_equal(hashes[[0, 2]], healthy[[0, 2]]))


class PreviousFormulaTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.default_rng(31)
        self.world = _World(_rois(rng, 6), {(1, 500.0): _blobs(rng, 3), (2, 510.0): _blobs(rng, 3)})
        self.plan = self.world.light()

    def test_cells_stamped_with_the_whole_mask_formula_are_recognised_and_only_those(self) -> None:
        current = cell_hashes(self.plan)
        legacy = cell_hashes(self.plan, local_masks=False)
        self.assertFalse(np.array_equal(current, legacy))  # cubes with masks changed their stamp
        self.assertTrue(np.array_equal(current[0], legacy[0]))  # the cube without masks did not
        recognised = stamped_by_previous_formula(legacy, current, self.plan)
        self.assertTrue(np.array_equal(recognised, (legacy != current)))
        self.assertFalse(recognised[0].any())

    def test_a_cell_that_no_formula_explains_is_not_recognised_and_never_computed_is_not_either(self) -> None:
        current = cell_hashes(self.plan)
        legacy = cell_hashes(self.plan, local_masks=False)
        stored = legacy.copy()
        stored[1, 2] ^= np.uint64(1)  # an old stamp of other inputs
        stored[2, 0] = 0  # never computed
        recognised = stamped_by_previous_formula(stored, current, self.plan)
        self.assertFalse(recognised[1, 2])
        self.assertFalse(recognised[2, 0])
        self.assertTrue(recognised[1].sum() == (legacy[1] != current[1]).sum() - (1 if legacy[1, 2] != current[1, 2] else 0))

    def test_nothing_is_hashed_again_when_nothing_differs(self) -> None:
        current = cell_hashes(self.plan)
        with mock.patch.object(scan_fingerprint, "cell_hashes", side_effect=AssertionError("hashed again")):
            self.assertFalse(stamped_by_previous_formula(current, current, self.plan).any())


if __name__ == "__main__":
    unittest.main()
