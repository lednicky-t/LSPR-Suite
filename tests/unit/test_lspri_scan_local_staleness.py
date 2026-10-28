"""Pure-logic tests for "a moved ROI makes only its own cells stale" (analysis step 1, 2026-10-27).

Covers `frame_reduce.RoiSet.subset`, `PixelLayout.restrict` / `LayoutCache.get(subset=)` (a subset of ROIs gives exactly the numbers the full
set gives for those ROIs, bit for bit, including the reference-ring exclusion by ROIs that are not in the subset), `frame_reduce.local_roi_keys`
(which ROIs a ROI's pixels can depend on), `scan_fingerprint.cell_hashes` (which cells change when a ROI moves), `scan_arrays.place` and
`ScanStore.write_cells` for a subset. The task around it (`ScanTask` writing a subset, `Fast scan`) is in
`tests/integration/test_lspri_analysis_scan_task.py`. Design record: `apps/LSPRi/eva/docs/roi_nudge_partial_scan_proposal_2026-10-26.md`.
No Qt; the only files are a scan file in a temporary folder.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import frame_reduce, scan_store
    from lspr_imaging_app.analysis.frame_reduce import (
        LayoutCache,
        build_layout,
        build_roi_set,
        build_subset_layout,
        local_roi_keys,
        reduce_frame,
        ring_reach_matrix,
    )
    from lspr_imaging_app.analysis.scan import CubeResult
    from lspr_imaging_app.analysis.scan_arrays import new_arrays, place
    from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes
    from lspr_imaging_app.analysis.scan_jobs import ScanSource, build_scan_plan
    from lspr_imaging_app.analysis.scan_store import ScanWriter
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings, RoiMask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

H, W = 90, 130
INNER, OUTER = 20.0, 30.0
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])


def _image() -> np.ndarray:
    rng = np.random.default_rng(11)
    y, x = np.mgrid[0:H, 0:W]
    image = (3000.0 + 20.0 * x + 10.0 * y + rng.normal(0.0, 30.0, (H, W))).astype(np.float32)
    image[30:50, 80:110] = np.nan
    return image


def _ignore() -> np.ndarray:
    mask = np.zeros((H, W), dtype=bool)
    mask[10:25, 10:30] = True
    return mask


def _crowded_rois() -> list[AreaRoi]:
    """Overlapping ROIs 2 and 3 (their rings take each other's sample pixels), edge ROIs, a ROI without a ring, and a free-form ROI."""
    return [
        AreaRoi(1, 20.3, 18.7, 14.0, reference_inner_diameter_px=20.0, reference_outer_diameter_px=28.0),
        AreaRoi(2, 45.5, 40.1, 12.0, reference_inner_diameter_px=18.0, reference_outer_diameter_px=26.0),
        AreaRoi(3, 52.0, 41.0, 10.0, reference_inner_diameter_px=16.0, reference_outer_diameter_px=24.0),
        AreaRoi(4, 100.2, 30.8, 14.0),
        AreaRoi(5, 3.0, 4.0, 10.0, reference_inner_diameter_px=14.0, reference_outer_diameter_px=22.0),
        AreaRoi(6, 75.0, 60.0, 10.0, reference_geometry_type="none"),
        AreaRoi(7, 30.0, 65.0, 9.0, sample_geometry_type="mask", sample_mask=RoiMask(26, 61, np.ones((8, 9), dtype=bool))),
    ]


class SubsetEqualsFullTests(unittest.TestCase):
    """The numbers of some ROIs do not depend on which other ROIs are computed with them."""

    def _check(self, indices, affine, ignore, methods) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        image = _image()
        full = reduce_frame(rs, image, ignore, affine, methods)
        layout = build_layout(rs, affine, (H, W)).restrict(indices)
        part = reduce_frame(rs.subset(indices), image, ignore, affine, methods, layout=layout)
        idx = list(indices)
        for method in methods:
            self.assertTrue(np.array_equal(part.values[method], full.values[method][idx], equal_nan=True), method)
        for name in ("std", "n_valid", "n_nominal", "n_masked"):
            self.assertTrue(np.array_equal(getattr(part, name), getattr(full, name)[idx], equal_nan=True), name)

    def test_every_method_with_an_ignore_mask_and_a_chromatic_affine(self) -> None:
        for affine in (IDENTITY, CHROMATIC):
            with self.subTest(affine="identity" if affine is IDENTITY else "chromatic"):
                self._check([1], affine, _ignore(), ("mean", "median", "trimmed_mean", "plane_fit"))

    def test_the_ring_still_leaves_out_the_sample_of_a_roi_that_is_not_in_the_subset(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        full = build_layout(rs, IDENTITY, (H, W))
        only_roi_2 = full.restrict([1])
        # ROI 3 (index 2) overlaps ROI 2's ring: the ring of the restricted layout is the full layout's ring, not a bigger one
        self.assertEqual(int(only_roi_2.ring_count[0]), int(full.ring_count[1]))
        alone = build_layout(rs.subset([1]), IDENTITY, (H, W))  # what you would get by forgetting the other ROIs
        self.assertGreater(int(alone.ring_count[0]), int(only_roi_2.ring_count[0]))

    def test_several_rois_in_any_subset_order(self) -> None:
        self._check([0, 2, 4, 5], CHROMATIC, _ignore(), ("mean", "median"))
        self._check([6, 1], IDENTITY, None, ("mean",))  # the free-form ROI and an ordinary one, order as given

    def test_restricted_layout_reads_only_what_its_rois_read(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        full = build_layout(rs, IDENTITY, (H, W))
        one = full.restrict([3])
        self.assertLess(one.needed_flat.size, full.needed_flat.size)
        self.assertEqual(int(one.n_rois), 1)
        self.assertTrue(np.array_equal(one.needed_flat[one.sample_pos], one.sample_flat))
        with self.assertRaises(ValueError):
            full.restrict([1, 1])
        with self.assertRaises(ValueError):
            full.restrict([len(_crowded_rois())])

    def test_the_cache_hands_out_one_restricted_layout_per_subset(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        cache = LayoutCache()
        a = cache.get(rs, IDENTITY, (H, W), subset=(1, 3))
        self.assertIs(cache.get(rs, IDENTITY, (H, W), subset=(1, 3)), a)
        self.assertIsNot(cache.get(rs, IDENTITY, (H, W), subset=(1,)), a)
        self.assertTrue(np.array_equal(a.sample_flat, build_layout(rs, IDENTITY, (H, W)).restrict([1, 3]).sample_flat))


_LAYOUT_FIELDS = ("sample_roi", "sample_flat", "ring_roi", "ring_flat", "sample_count", "ring_count", "needed_flat", "sample_pos", "ring_pos")
ROTATED = np.array([[0.9998, -0.0251, 6.1], [0.0251, 0.9998, -3.4]])  # a small rotation, as a chromatic affine can be


class DirectSubsetLayoutTests(unittest.TestCase):
    """`build_subset_layout` (the layout of some ROIs, built without locating the others) is exactly `build_layout(...).restrict(...)`."""

    def assertSameLayout(self, direct, cut, label="") -> None:
        self.assertEqual((direct.shape, direct.n_rois, direct.exclusion_mode), (cut.shape, cut.n_rois, cut.exclusion_mode), label)
        for name in _LAYOUT_FIELDS:
            a, b = getattr(direct, name), getattr(cut, name)
            self.assertEqual(a.dtype, b.dtype, f"{label} {name} dtype")
            self.assertTrue(np.array_equal(a, b), f"{label} {name}")

    def _compare(self, rs, subset, affine, shape, mode="exclude_all_sample_rois", label="") -> None:
        cut = build_layout(rs, affine, shape, mode).restrict(subset)
        self.assertSameLayout(build_subset_layout(rs, affine, shape, mode, subset), cut, f"{label} subset {list(subset)}")

    def test_every_single_roi_and_several_subsets_of_the_crowded_set(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        subsets = [(i,) for i in range(rs.n)] + [(0, 2, 4, 5), (6, 1), (1, 2), (2, 1), tuple(range(rs.n)), (3, 6)]
        for affine in (IDENTITY, CHROMATIC, ROTATED):
            for subset in subsets:
                self._compare(rs, subset, affine, (H, W))

    def test_without_the_exclusion_and_with_an_image_smaller_than_the_rois(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        for subset in ((0,), (1, 2), (3,), (6,), (0, 1, 2, 3, 4, 5, 6)):
            self._compare(rs, subset, CHROMATIC, (H, W), mode="none", label="no exclusion")
            self._compare(rs, subset, CHROMATIC, (50, 70), label="small image")  # ROIs 3 and 4 run over the edge
            self._compare(rs, subset, IDENTITY, (20, 20), label="tiny image")

    def test_a_ring_still_loses_the_pixels_a_neighbour_that_is_not_in_the_subset_takes(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        full = build_layout(rs, IDENTITY, (H, W))
        direct = build_subset_layout(rs, IDENTITY, (H, W), subset=(1,))  # ROI 3 (index 2) overlaps its ring
        self.assertEqual(int(direct.ring_count[0]), int(full.ring_count[1]))
        self.assertGreater(int(build_layout(rs.subset([1]), IDENTITY, (H, W)).ring_count[0]), int(direct.ring_count[0]))

    def test_empty_subset(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        self._compare(rs, (), IDENTITY, (H, W))
        self.assertEqual(build_subset_layout(rs, IDENTITY, (H, W), subset=()).n_rois, 0)

    def test_a_single_roi_set(self) -> None:
        rs = build_roi_set(_crowded_rois()[:1], INNER, OUTER)
        self._compare(rs, (0,), CHROMATIC, (H, W))

    def test_random_dense_layouts(self) -> None:
        rng = np.random.default_rng(2026)
        for trial in range(6):
            rois = [
                AreaRoi(
                    i + 1, float(rng.uniform(-5, W + 5)), float(rng.uniform(-5, H + 5)), float(rng.uniform(6, 18)),
                    reference_inner_diameter_px=float(rng.uniform(8, 24)), reference_outer_diameter_px=float(rng.uniform(24, 40)),
                    reference_geometry_type="none" if rng.random() < 0.1 else "annulus",
                )
                for i in range(30)
            ]
            rs = build_roi_set(rois, INNER, OUTER)
            affine = (IDENTITY, CHROMATIC, ROTATED)[trial % 3]
            for _ in range(4):
                size = int(rng.integers(1, 9))
                subset = tuple(int(i) for i in rng.choice(rs.n, size, replace=False))  # any order
                self._compare(rs, subset, affine, (H, W), label=f"trial {trial}")

    def test_the_neighbours_of_a_ring_are_the_rois_the_cell_hash_looks_at(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        near = ring_reach_matrix(rs, [1, 3])
        self.assertEqual(near.shape, (2, rs.n))
        self.assertTrue(bool(near[0, 2]) and bool(near[0, 6]))  # ROI 3 overlaps ROI 2's ring; the free-form ROI reaches everything
        self.assertFalse(bool(near[1, 0]))  # ROI 1 is far from ROI 4
        self.assertFalse(bool(ring_reach_matrix(rs, [5]).any()))  # a ROI without a ring is reached by nobody

    def test_bad_subsets_and_modes_are_refused(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        with self.assertRaises(ValueError):
            build_subset_layout(rs, IDENTITY, (H, W), subset=(1, 1))
        with self.assertRaises(ValueError):
            build_subset_layout(rs, IDENTITY, (H, W), subset=(rs.n,))
        with self.assertRaises(ValueError):
            build_subset_layout(rs, IDENTITY, (H, W), subset=(-1,))
        with self.assertRaises(ValueError):
            build_subset_layout(rs, IDENTITY, (H, W), "nonsense", subset=(0,))

    def test_the_cache_does_not_build_the_full_layout_for_a_subset(self) -> None:
        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        cache = LayoutCache()
        with mock.patch.object(frame_reduce, "build_layout", side_effect=AssertionError("the full layout was built")):
            layout = cache.get(rs, CHROMATIC, (H, W), subset=(1, 3))
        self.assertSameLayout(layout, build_layout(rs, CHROMATIC, (H, W)).restrict([1, 3]))


class WindowBoxTests(unittest.TestCase):
    """`roi_window_boxes`: the box of a ROI must hold every pixel the ROI can read, in every frame geometry (the mask part of the cell hash relies on it)."""

    def test_every_pixel_a_roi_reads_is_inside_its_box(self) -> None:
        from lspr_imaging_app.analysis.frame_reduce import locate_pixels, roi_window_boxes

        rng = np.random.default_rng(77)
        affines = [IDENTITY, CHROMATIC, ROTATED, np.array([[1.08, -0.04, 5.0], [0.03, 0.93, -4.0]]), np.array([[0.9, 0.0, -9.5], [0.0, 1.12, 7.25]])]
        for trial in range(6):
            rois = [
                AreaRoi(
                    i + 1, float(rng.uniform(-8, W + 8)), float(rng.uniform(-8, H + 8)), float(rng.uniform(4, 20)),
                    reference_inner_diameter_px=float(rng.uniform(8, 24)), reference_outer_diameter_px=float(rng.uniform(24, 44)),
                    reference_geometry_type="none" if rng.random() < 0.15 else "annulus",
                )
                for i in range(25)
            ]
            rs = build_roi_set(rois, INNER, OUTER)
            for affine in affines:
                y0, y1, x0, x1, bounded = roi_window_boxes(rs, affine, (H, W))
                self.assertTrue(bounded.all())
                s_roi, s_flat, r_roi, r_flat = locate_pixels(rs, affine, (H, W))  # before the exclusion: a superset of what is read
                for roi_of, flat in ((s_roi, s_flat), (r_roi, r_flat)):
                    rows, cols = np.divmod(flat, W)
                    self.assertTrue(np.all(rows >= y0[roi_of]) and np.all(rows < y1[roi_of]))
                    self.assertTrue(np.all(cols >= x0[roi_of]) and np.all(cols < x1[roi_of]))

    def test_a_free_form_roi_has_no_bound_and_gets_the_whole_image(self) -> None:
        from lspr_imaging_app.analysis.frame_reduce import roi_window_boxes

        rs = build_roi_set(_crowded_rois(), INNER, OUTER)
        y0, y1, x0, x1, bounded = roi_window_boxes(rs, IDENTITY, (H, W))
        free = int(np.flatnonzero(~bounded)[0])
        self.assertEqual((y0[free], y1[free], x0[free], x1[free]), (0, H, 0, W))
        self.assertTrue(bounded[:free].all() and bounded.sum() == rs.n - 1)
        self.assertTrue(np.all(y1 <= H) and np.all(x1 <= W) and np.all(y0 >= 0) and np.all(x0 >= 0))

    def test_the_box_follows_the_chromatic_shift(self) -> None:
        from lspr_imaging_app.analysis.frame_reduce import roi_window_boxes

        rs = build_roi_set([AreaRoi(1, 60.0, 45.0, 10.0, reference_inner_diameter_px=14.0, reference_outer_diameter_px=20.0)], INNER, OUTER)
        plain = roi_window_boxes(rs, IDENTITY, (H, W))
        moved = roi_window_boxes(rs, np.array([[1.0, 0.0, 12.0], [0.0, 1.0, -7.0]]), (H, W))
        self.assertEqual((int(moved[2][0] - plain[2][0]), int(moved[0][0] - plain[0][0])), (12, -7))


class LocalKeyTests(unittest.TestCase):
    @staticmethod
    def _row(centres, **kwargs) -> list[AreaRoi]:
        return [AreaRoi(i + 1, x, y, 10.0, reference_inner_diameter_px=14.0, reference_outer_diameter_px=20.0, **kwargs)
                for i, (x, y) in enumerate(centres)]

    def _keys(self, rois, mode="exclude_all_sample_rois"):
        return local_roi_keys(build_roi_set(rois, 14.0, 20.0), mode)

    def test_moving_a_roi_far_from_every_ring_changes_only_its_own_key(self) -> None:
        base = self._keys(self._row([(20, 20), (70, 20), (20, 70), (70, 70)]))
        moved = self._keys(self._row([(21.5, 20), (70, 20), (20, 70), (70, 70)]))
        self.assertEqual([a != b for a, b in zip(base, moved)], [True, False, False, False])

    def test_moving_a_roi_into_reach_of_a_neighbours_ring_changes_the_neighbours_key_too(self) -> None:
        base = self._keys(self._row([(20, 20), (45, 20), (20, 70), (70, 70)]))
        # sample radius 5 + ring outer radius 10 = 15 (+ the margin): at 13 px the discs overlap
        moved = self._keys(self._row([(32, 20), (45, 20), (20, 70), (70, 70)]))
        self.assertEqual([a != b for a, b in zip(base, moved)], [True, True, False, False])
        # and moving away again beyond the reach changes the neighbour once more
        away = self._keys(self._row([(8, 20), (45, 20), (20, 70), (70, 70)]))
        self.assertNotEqual(away[1], moved[1])
        self.assertEqual(away[1], base[1])  # back where it was: the neighbour's key is the one it had

    def test_without_the_exclusion_no_neighbour_matters(self) -> None:
        base = self._keys(self._row([(20, 20), (45, 20)]), "none")
        moved = self._keys(self._row([(32, 20), (45, 20)]), "none")
        self.assertEqual([a != b for a, b in zip(base, moved)], [True, False])

    def test_a_roi_without_a_ring_is_not_affected_by_a_neighbour(self) -> None:
        def build(first_x):
            rois = self._row([(first_x, 20), (45, 20)])
            rois[1].reference_geometry_type = "none"
            return rois

        base, moved = self._keys(build(20)), self._keys(build(32))  # ROI 1's sample disk comes within reach of ROI 2, which has no ring
        self.assertEqual([a != b for a, b in zip(base, moved)], [True, False])

    def test_a_roi_without_a_sample_aperture_excludes_nothing(self) -> None:
        def build(first_x):
            rois = self._row([(first_x, 20), (32, 20)])
            rois[0].sample_diameter_px = 0.0
            return rois

        near, far = self._keys(build(20)), self._keys(build(80))  # ROI 1 has no sample: ROI 2's ring is the same wherever it is
        self.assertNotEqual(near[0], far[0])
        self.assertEqual(near[1], far[1])

    def test_a_free_form_roi_counts_as_reaching_everything(self) -> None:
        def build(free_x):
            rois = self._row([(20, 20), (70, 20), (20, 70)])
            rois.append(AreaRoi(4, free_x, 90.0, 9.0, sample_geometry_type="mask", sample_mask=RoiMask(int(free_x), 86, np.ones((8, 9), dtype=bool))))
            return rois

        base, moved = self._keys(build(100.0)), self._keys(build(104.0))
        self.assertTrue(all(a != b for a, b in zip(base, moved)))  # every ROI's key knows where the free-form one is


class _Fake:
    """A small app for `build_scan_plan`: the plan is only hashed, so the frame paths are labels."""

    def __init__(self, rois, background=None, cubes=3):
        self.rois = rois
        self.background = background if background is not None else BackgroundSettings()
        self.cubes = cubes

    def source(self) -> ScanSource:
        return ScanSource(
            cube_indices=lambda: tuple(range(self.cubes)),
            wavelengths_for_cube=lambda c: (500.0, 510.0),
            is_spectral=lambda c, w: True,
            record_path=lambda c, w: f"c{c}_w{w}.tif",
            rois=lambda: self.rois,
            rois_at=lambda c: self.rois,
            has_timeline=lambda: False,
            chromatic_affine=lambda c, w: IDENTITY,
            chromatic_affine_between=lambda a, b: IDENTITY,
            resolve_mask=lambda c, w: None,
            geometry=lambda: GeometrySettings(),
            background=lambda: self.background,
            detection=lambda: AreaRoiDetectionSettings(),
            default_reference_diameters=lambda: (14.0, 20.0),
        )

    def hashes(self):
        return cell_hashes(build_scan_plan(self.source(), resolve_masks=False))


class CellHashLocalityTests(unittest.TestCase):
    def test_background_off_moving_one_roi_changes_that_roi_in_every_cube_and_no_other(self) -> None:
        base = _Fake(LocalKeyTests._row([(20, 20), (70, 20), (20, 70), (70, 70)])).hashes()
        moved = _Fake(LocalKeyTests._row([(20, 20), (70, 20), (21.5, 70), (70, 70)])).hashes()
        changed = base != moved
        self.assertEqual(changed.shape, (3, 4))
        self.assertTrue(changed[:, 2].all())
        self.assertFalse(changed[:, [0, 1, 3]].any())

    def test_background_off_a_roi_that_comes_within_reach_changes_the_neighbour_as_well(self) -> None:
        base = _Fake(LocalKeyTests._row([(20, 20), (45, 20), (20, 70)])).hashes()
        moved = _Fake(LocalKeyTests._row([(32, 20), (45, 20), (20, 70)])).hashes()
        self.assertEqual(list((base != moved).all(axis=0)), [True, True, False])

    def test_background_that_cuts_the_rois_out_of_its_estimate_makes_every_cell_depend_on_every_roi(self) -> None:
        on = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=True)
        base = _Fake(LocalKeyTests._row([(20, 20), (70, 20), (20, 70)]), on).hashes()
        moved = _Fake(LocalKeyTests._row([(21.5, 20), (70, 20), (20, 70)]), on).hashes()
        self.assertTrue((base != moved).all())

    def test_background_that_does_not_look_at_the_rois_stays_local(self) -> None:
        on = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=False)
        base = _Fake(LocalKeyTests._row([(20, 20), (70, 20), (20, 70)]), on).hashes()
        moved = _Fake(LocalKeyTests._row([(21.5, 20), (70, 20), (20, 70)]), on).hashes()
        self.assertEqual(list((base != moved).all(axis=0)), [True, False, False])

    def test_the_same_inputs_give_the_same_hashes(self) -> None:
        rois = LocalKeyTests._row([(20, 20), (70, 20)])
        self.assertTrue(np.array_equal(_Fake(rois).hashes(), _Fake(LocalKeyTests._row([(20, 20), (70, 20)])).hashes()))


class StaleMatrixTests(unittest.TestCase):
    def test_a_cell_is_stale_when_its_hash_differs_or_was_never_computed(self) -> None:
        current = np.array([[5, 6, 7], [8, 9, 10]], dtype=np.uint64)
        stored = np.array([[5, 0, 7], [8, 99, 10]], dtype=np.uint64)
        self.assertEqual(scan_store.stale_cell_matrix(stored, current).tolist(), [[False, True, False], [False, True, False]])
        self.assertEqual(scan_store.stale_cube_positions(stored, current), [0, 1])

    def test_a_different_shape_makes_everything_stale(self) -> None:
        current = np.ones((2, 3), dtype=np.uint64)
        self.assertTrue(scan_store.stale_cell_matrix(np.ones((1, 3), dtype=np.uint64), current).all())


class SubsetPlacementAndWritingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.plan = build_scan_plan(_Fake(LocalKeyTests._row([(20, 20), (70, 20), (20, 70), (70, 70)])).source(), resolve_masks=False)
        self.hashes = cell_hashes(self.plan)
        self.path = Path(self._tmp.name) / "analysis" / "scan.h5"

    def _result(self, cube_pos, subset, fill):
        shape = (len(subset) if subset is not None else 4, 2, 2)
        return CubeResult(
            cube_pos, {"mean": np.full(shape, fill)}, np.full(shape, fill + 0.5), np.full(shape, 7, dtype=np.int32),
            np.full(shape, 9, dtype=np.int32), np.full(shape, 1, dtype=np.int32), roi_subset=subset,
        )

    def test_place_fills_only_the_rows_of_the_subset(self) -> None:
        arrays = new_arrays(self.plan)
        place(arrays, self.plan, self._result(1, (1, 3), 4.0))
        mean = arrays.values["mean"][1]
        self.assertTrue((mean[[1, 3]] == 4.0).all())
        self.assertTrue(np.isnan(mean[[0, 2]]).all())
        self.assertEqual(int(arrays.n_valid[1][[1, 3]].min()), 7)
        self.assertEqual(int(arrays.n_valid[1][[0, 2]].max()), 0)
        self.assertTrue(arrays.done[1])

    def _store_with_a_full_scan(self):
        store = scan_store.ScanStore(self.path)
        store.prepare(self.plan)
        arrays = new_arrays(self.plan)
        for pos in range(len(self.plan.jobs)):
            place(arrays, self.plan, self._result(pos, None, 1.0))
            store.write_cube(self.plan, arrays, pos, self.hashes[pos])
        return store

    def test_write_cells_changes_the_given_rows_of_one_cube_and_nothing_else(self) -> None:
        store = self._store_with_a_full_scan()
        before = scan_store.read_scan(self.path)
        arrays = new_arrays(self.plan)
        place(arrays, self.plan, self._result(1, (2,), 5.0))
        new_hashes = self.hashes[1] ^ np.uint64(0xFFFF)  # different from the stored ones, and never 0
        store.write_cells(self.plan, arrays, 1, (2,), new_hashes)
        after = scan_store.read_scan(self.path)
        row = self.plan.cube_indices[1]
        mean_before, mean_after = before.values["mean"], after.values["mean"]
        self.assertTrue((mean_after[row, 2] == 5.0).all())
        mask = np.ones(mean_after.shape[:2], dtype=bool)
        mask[row, 2] = False
        self.assertTrue(np.array_equal(mean_after[mask], mean_before[mask]))  # every other cell of the file is untouched
        self.assertEqual(int(after.n_valid[row, 2].min()), 7)
        expected_hash = before.cell_hash.copy()
        expected_hash[row, 2] = new_hashes[2]
        self.assertTrue(np.array_equal(after.cell_hash, expected_hash))

    def test_a_failure_while_writing_cells_leaves_those_cells_marked_not_computed(self) -> None:
        store = self._store_with_a_full_scan()
        before = scan_store.read_scan(self.path)
        arrays = new_arrays(self.plan)
        place(arrays, self.plan, self._result(0, (1, 2), 5.0))

        class _Broken:
            def __getitem__(self, key):
                raise OSError("disk full")

        arrays.n_masked = _Broken()  # the last array written before the hashes
        with self.assertRaises(OSError):
            store.write_cells(self.plan, arrays, 0, (1, 2), self.hashes[0])
        after = scan_store.read_scan(self.path)
        row = self.plan.cube_indices[0]
        self.assertEqual(after.cell_hash[row].tolist()[1:3], [0, 0])  # never a current hash on half-written values
        self.assertEqual(int(after.cell_hash[row, 0]), int(before.cell_hash[row, 0]))  # the other cells keep theirs
        self.assertTrue(np.array_equal(after.cell_hash[1:], before.cell_hash[1:]))


class WriteBatchAndWriterTests(unittest.TestCase):
    """`ScanStore.write_batch` and the `ScanWriter` thread (2026-10-27): the same file as writing the cubes one by one, off the collector's thread."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.plan = build_scan_plan(_Fake(LocalKeyTests._row([(20, 20), (70, 20), (20, 70), (70, 70)]), cubes=5).source(), resolve_masks=False)
        self.hashes = cell_hashes(self.plan)
        self.arrays = new_arrays(self.plan)
        rng = np.random.default_rng(3)
        for pos in range(len(self.plan.jobs)):
            shape = (4, 2, 2)
            place(self.arrays, self.plan, CubeResult(
                pos, {"mean": rng.normal(3000, 50, shape)}, np.abs(rng.normal(60, 5, shape)), rng.integers(1, 900, shape).astype(np.int32),
                np.full(shape, 900, np.int32), rng.integers(0, 3, shape).astype(np.int32),
            ))

    def _store(self, name: str) -> scan_store.ScanStore:
        store = scan_store.ScanStore(Path(self._tmp.name) / name / "scan.h5")
        store.prepare(self.plan)
        return store

    def _same_file(self, a: scan_store.ScanStore, b: scan_store.ScanStore) -> None:
        x, y = scan_store.read_scan(a.path), scan_store.read_scan(b.path)
        self.assertTrue(np.array_equal(x.values["mean"], y.values["mean"], equal_nan=True))
        for name in ("std", "n_valid", "n_nominal", "n_masked", "cell_hash", "recipe_id", "layout_id", "acquired_at_unix_ms"):
            self.assertTrue(np.array_equal(getattr(x, name), getattr(y, name), equal_nan=True), name)

    def test_a_mixed_unsorted_batch_with_gaps_changes_exactly_what_it_names(self) -> None:
        """Checked against numpy arithmetic on the file's earlier content, not against another run of the writer: runs of neighbouring cubes are
        written as slabs (2026-10-28), and the cubes between the runs must not be touched."""
        store = self._store("a")
        for pos in range(5):
            store.write_cube(self.plan, self.arrays, pos, self.hashes[pos])
        before = scan_store.read_scan(store.path)
        fresh = new_arrays(self.plan)
        rng = np.random.default_rng(9)
        for pos in range(5):
            place(fresh, self.plan, CubeResult(
                pos, {"mean": rng.normal(100, 5, (4, 2, 2))}, np.abs(rng.normal(9, 1, (4, 2, 2))), rng.integers(1, 90, (4, 2, 2)).astype(np.int32),
                np.full((4, 2, 2), 90, np.int32), rng.integers(0, 3, (4, 2, 2)).astype(np.int32),
            ))
        new_hash = self.hashes ^ np.uint64(5)
        items = [(3, None, new_hash[3]), (0, (1,), new_hash[0]), (1, (0, 2), new_hash[1])]  # not in file order; cubes 2 and 4 are gaps
        store.write_batch(self.plan, fresh, items)
        after = scan_store.read_scan(store.path)

        def expect(old, new):
            out = old.copy()
            for pos, idx, _ in items:
                row = self.plan.cube_indices[pos]
                if idx is None:
                    out[row] = new[pos]
                else:
                    out[row, list(idx)] = new[pos][list(idx)]
            return out

        self.assertTrue(np.array_equal(after.values["mean"], expect(before.values["mean"], fresh.values["mean"]), equal_nan=True))
        for name in ("std", "n_valid", "n_nominal", "n_masked"):
            self.assertTrue(np.array_equal(getattr(after, name), expect(getattr(before, name), getattr(fresh, name)), equal_nan=True), name)
        expected_hash = before.cell_hash.copy()
        expected_hash[3] = new_hash[3]
        expected_hash[0, 1] = new_hash[0][1]
        expected_hash[1, [0, 2]] = new_hash[1][[0, 2]]
        self.assertTrue(np.array_equal(after.cell_hash, expected_hash))
        expected_recipe = before.recipe_id.copy()
        expected_recipe[3] = store._recipe_id
        expected_recipe[0, 1] = store._recipe_id
        expected_recipe[1, [0, 2]] = store._recipe_id
        self.assertTrue(np.array_equal(after.recipe_id, expected_recipe))
        for gap in (2, 4):  # untouched, bit for bit
            for name in ("std", "n_valid", "n_nominal", "n_masked", "cell_hash", "recipe_id"):
                self.assertTrue(np.array_equal(getattr(after, name)[gap], getattr(before, name)[gap], equal_nan=True), (gap, name))
            self.assertTrue(np.array_equal(after.values["mean"][gap], before.values["mean"][gap], equal_nan=True))

    def test_a_batch_never_writes_a_row_that_is_not_in_it(self) -> None:
        """The cubes between two runs are current; even rewriting them with their own values could tear them if the process died at that moment."""
        import h5py

        store = self._store("a")
        for pos in range(5):
            store.write_cube(self.plan, self.arrays, pos, self.hashes[pos])
        written: list[tuple[str, object]] = []
        original = h5py.Dataset.__setitem__

        def spy(dataset, key, value):
            written.append((dataset.name, key))
            return original(dataset, key, value)

        with mock.patch.object(h5py.Dataset, "__setitem__", spy):
            store.write_batch(self.plan, self.arrays, [(0, None, self.hashes[0]), (1, (1,), self.hashes[1]), (3, None, self.hashes[3])])
        rows_written = set()
        for name, key in written:
            if not name.startswith(("/processed/scan", "/state", "/axes/acquired")):
                continue
            first = key[0] if isinstance(key, tuple) else key
            self.assertIsInstance(first, slice, (name, key))  # slabs of rows, never a single scalar write that could hide a gap
            rows_written |= set(range(first.start, first.stop))
        self.assertEqual(rows_written, {0, 1, 3})

    def test_a_batch_writes_the_same_file_as_the_cubes_one_by_one_whole_and_partial(self) -> None:
        one_by_one, batched = self._store("a"), self._store("b")
        for pos in (0, 1, 2, 3):
            one_by_one.write_cube(self.plan, self.arrays, pos, self.hashes[pos])
            batched.write_cube(self.plan, self.arrays, pos, self.hashes[pos])
        fresh = self.hashes ^ np.uint64(0xABCD)  # a later refresh of some ROIs of cubes 1 and 3, and a whole cube 4
        one_by_one.write_cells(self.plan, self.arrays, 1, (0, 2), fresh[1])
        one_by_one.write_cells(self.plan, self.arrays, 3, (3,), fresh[3])
        one_by_one.write_cube(self.plan, self.arrays, 4, fresh[4])
        batched.write_batch(self.plan, self.arrays, [(1, (0, 2), fresh[1]), (3, (3,), fresh[3]), (4, None, fresh[4])])
        self._same_file(one_by_one, batched)

    def test_a_cube_twice_in_one_batch_is_refused(self) -> None:
        store = self._store("a")
        with self.assertRaises(ValueError):
            store.write_batch(self.plan, self.arrays, [(1, None, self.hashes[1]), (1, (0,), self.hashes[1])])
        store.write_batch(self.plan, self.arrays, [])  # nothing to do is fine

    def test_a_failure_half_way_through_a_batch_leaves_every_cube_of_it_marked_not_computed(self) -> None:
        store = self._store("a")
        for pos in range(5):
            store.write_cube(self.plan, self.arrays, pos, self.hashes[pos])

        class _Broken:
            def __getitem__(self, key):
                raise OSError("disk full")

        saved = self.arrays.n_masked
        self.arrays.n_masked = _Broken()
        try:
            with self.assertRaises(OSError):
                store.write_batch(self.plan, self.arrays, [(0, None, self.hashes[0] ^ np.uint64(1)), (2, (1,), self.hashes[2] ^ np.uint64(1))])
        finally:
            self.arrays.n_masked = saved
        stored = scan_store.read_scan(store.path)
        self.assertTrue((stored.cell_hash[0] == 0).all())  # the whole cube 0 and only ROI 1 of cube 2 are marked "never computed"
        self.assertEqual(stored.cell_hash[2].tolist()[1], 0)
        self.assertTrue(np.array_equal(np.delete(stored.cell_hash[2], 1), np.delete(self.hashes[2], 1)))
        self.assertTrue(np.array_equal(stored.cell_hash[[1, 3, 4]], self.hashes[[1, 3, 4]]))

    def test_the_writer_writes_everything_submitted_and_the_file_equals_a_direct_write(self) -> None:
        direct, threaded = self._store("direct"), self._store("threaded")
        for pos in range(5):
            direct.write_cube(self.plan, self.arrays, pos, self.hashes[pos])
        writer = ScanWriter(threaded, self.plan, self.arrays)
        for pos in range(5):
            writer.submit(pos, None, self.hashes[pos])
        writer.finish()
        self.assertEqual((writer.cubes, writer.error), (5, None))
        self.assertGreaterEqual(writer.batches, 1)
        self.assertGreater(writer.busy_seconds, 0.0)
        self._same_file(direct, threaded)

    def test_cubes_that_arrive_while_a_batch_is_being_written_go_together_in_the_next_batch(self) -> None:
        store = self._store("a")
        real = store.write_batch
        sizes: list[int] = []
        entered, release = threading.Event(), threading.Event()

        def slow_first(plan, arrays, items):
            sizes.append(len(items))
            if len(sizes) == 1:
                entered.set()
                release.wait(30)
            real(plan, arrays, items)

        with mock.patch.object(store, "write_batch", slow_first):
            writer = ScanWriter(store, self.plan, self.arrays)
            writer.submit(0, None, self.hashes[0])
            self.assertTrue(entered.wait(30))  # the first cube is being written; these three queue up behind it
            for pos in (1, 2, 3):
                writer.submit(pos, None, self.hashes[pos])
            release.set()
            writer.finish()
        self.assertEqual(sizes, [1, 3])
        self.assertEqual(writer.cubes, 4)
        self.assertEqual(int(scan_store.read_scan(store.path).computed().any(axis=1).sum()), 4)

    def test_a_writer_error_is_raised_to_the_collector_by_the_next_submit_and_by_finish(self) -> None:
        store = self._store("a")
        with mock.patch.object(store, "write_batch", side_effect=OSError("disk full")):
            writer = ScanWriter(store, self.plan, self.arrays)
            writer.submit(0, None, self.hashes[0])
            writer._thread.join(30)  # the writer has failed and stopped
            self.assertIsInstance(writer.error, OSError)
            with self.assertRaises(OSError):
                writer.submit(1, None, self.hashes[1])
            with self.assertRaises(OSError):
                writer.finish()

    def test_finish_with_nothing_submitted_just_stops_the_thread(self) -> None:
        writer = ScanWriter(self._store("a"), self.plan, self.arrays)
        writer.finish()
        self.assertEqual((writer.batches, writer.cubes), (0, 0))


if __name__ == "__main__":
    unittest.main()
