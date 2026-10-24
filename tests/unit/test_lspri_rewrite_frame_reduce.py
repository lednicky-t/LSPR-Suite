"""Pure-logic tests for analysis step 1 of the LSPRimaging Evaluation rewrite: processed pixels -> reduced numbers.

Covers `lspr_imaging_app.analysis.frame_reduce` (finding every ROI's pixels in one vectorised pass, the exclusions, the
counts, the values and the standard deviation) and the new optional `roi_exclusion` argument of
`apply_preprocessing` / `flatten_background`.

The reference here is `tasks.compute_cell`'s per-ROI rules written out plainly with the existing rasteriser
(`roi.rasterize`): the vectorised code has to give the same pixels, counts and values. Design record:
`apps/LSPRi/eva/docs/analysis_redesign_plan_2026-10-19.md`, sections 10-11.

Like the other rewrite tests this only runs when the `apps/LSPRi/eva` submodule is on its `rewrite` branch. No Qt, no
files, small synthetic images.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.frame_reduce import (
        REFERENCE,
        SAMPLE,
        LayoutCache,
        build_layout,
        build_roi_set,
        locate_pixels,
        reduce_frame,
        segment_mean_std,
        segment_std,
    )
    from lspr_imaging_app.analysis.provenance import REFERENCE_EXCLUSION_MODES
    from lspr_imaging_app.analysis.reduction import REDUCTION_METHODS, reduce_sample_and_reference
    from lspr_imaging_app.image_tools.background import estimate as bg_estimate
    from lspr_imaging_app.image_tools.background.estimate import BackgroundWeightsCache, roi_exclusion_mask
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.preprocess import apply_preprocessing, apply_preprocessing_at
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.roi.model import AreaRoi, RoiMask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

HEIGHT, WIDTH = 90, 130
INNER, OUTER = 20.0, 30.0  # the shared default reference-ring diameters
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
# Stretch of 0.4 %, a shear and a shift of a few pixels: the size of a real chromatic correction.
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])


def _rois() -> list[AreaRoi]:
    return [
        AreaRoi(1, 20.3, 18.7, 14.0, reference_inner_diameter_px=20.0, reference_outer_diameter_px=28.0),
        AreaRoi(2, 45.5, 40.1, 12.0, reference_inner_diameter_px=18.0, reference_outer_diameter_px=26.0),
        AreaRoi(3, 52.0, 41.0, 10.0, reference_inner_diameter_px=16.0, reference_outer_diameter_px=24.0),  # overlaps ROI 2
        AreaRoi(4, 100.2, 30.8, 14.0),  # ring from the shared defaults; the NaN block of the image sits on it
        AreaRoi(5, 3.0, 4.0, 10.0, reference_inner_diameter_px=14.0, reference_outer_diameter_px=22.0),  # window leaves the image
        AreaRoi(6, 126.5, 86.0, 12.0, reference_inner_diameter_px=16.0, reference_outer_diameter_px=24.0),  # bottom-right corner
        AreaRoi(7, 75.0, 60.0, 10.0, reference_geometry_type="none"),  # no ring at all
        AreaRoi(8, 90.0, 70.0, 0.0),  # empty sample aperture
        AreaRoi(9, 30.0, 65.0, 9.0, sample_geometry_type="mask", sample_mask=RoiMask(26, 61, np.ones((8, 9), dtype=bool))),
    ]


def _image() -> np.ndarray:
    rng = np.random.default_rng(7)
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH]
    image = (3000.0 + 20.0 * x + 10.0 * y + rng.normal(0.0, 30.0, (HEIGHT, WIDTH))).astype(np.float32)
    image[30:50, 80:110] = np.nan  # pixels without a measurement (what a rotation leaves at the corners)
    return image


def _ignore() -> np.ndarray:
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    mask[10:25, 10:30] = True
    return mask


def _same(a, b) -> bool:
    return bool(np.array_equal(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64), equal_nan=True))


def _reference_cells(rois, processed, ignore, affine, mode, methods, trimmed=0.10):
    """compute_cell's per-ROI rules, one full-frame mask at a time."""
    shape = processed.shape
    valid = np.isfinite(processed)
    union = None
    if mode == "exclude_all_sample_rois":
        union = np.zeros(shape, dtype=bool)
        for roi in rois:
            union |= rz.rasterize_sample(roi, shape, affine)
    out = {"nominal": [], "valid": [], "std": [], "mean64": [], "values": {m: [] for m in methods}}
    for roi in rois:
        sample = rz.rasterize_sample(roi, shape, affine)
        ring = rz.rasterize_reference(roi, shape, affine, default_inner_diameter_px=INNER, default_outer_diameter_px=OUTER)
        if union is not None:
            ring = ring & ~union
        if ignore is not None:
            sample = sample & ~ignore
            ring = ring & ~ignore
        out["nominal"].append((int(sample.sum()), int(ring.sum())))
        sample_px, ring_px = processed[sample & valid], processed[ring & valid]
        yy, xx = np.nonzero(ring & valid)
        out["valid"].append((sample_px.size, ring_px.size))
        out["std"].append(tuple(p.astype(np.float64).std(ddof=1) if p.size > 1 else np.nan for p in (sample_px, ring_px)))
        out["mean64"].append(
            (float(np.mean(sample_px.astype(np.float64))), float(np.mean(ring_px.astype(np.float64))))
            if sample_px.size and ring_px.size else (np.nan, np.nan)
        )
        for m in methods:
            if sample_px.size == 0 or ring_px.size == 0:
                pair = (np.nan, np.nan)
            else:
                pair = reduce_sample_and_reference(
                    sample_px, ring_px, m, trimmed_mean_fraction=trimmed, reference_xx=xx.astype(np.float64),
                    reference_yy=yy.astype(np.float64), sample_x=float(roi.center_x), sample_y=float(roi.center_y),
                )
                if not (np.isfinite(pair[0]) and np.isfinite(pair[1])):
                    pair = (np.nan, np.nan)
            out["values"][m].append(pair)
    return out


class LocatePixelsTests(unittest.TestCase):
    def test_pixels_are_exactly_the_rasterisers_masks(self) -> None:
        rois = _rois()
        rs = build_roi_set(rois, INNER, OUTER)
        for name, affine in (("identity", IDENTITY), ("chromatic", CHROMATIC)):
            for block in (1, 4, 16, 64):
                with self.subTest(affine=name, block=block):
                    s_roi, s_flat, r_roi, r_flat = locate_pixels(rs, affine, (HEIGHT, WIDTH), block)
                    for i, roi in enumerate(rois):
                        sample = rz.rasterize_sample(roi, (HEIGHT, WIDTH), affine)
                        ring = rz.rasterize_reference(roi, (HEIGHT, WIDTH), affine, default_inner_diameter_px=INNER, default_outer_diameter_px=OUTER)
                        self.assertTrue(np.array_equal(s_flat[s_roi == i], np.flatnonzero(sample)), f"sample of ROI {roi.area_roi_id}")
                        self.assertTrue(np.array_equal(r_flat[r_roi == i], np.flatnonzero(ring)), f"ring of ROI {roi.area_roi_id}")
                    # Sorted by ROI: the blocks of a ROI's pixels are contiguous.
                    self.assertTrue(np.all(np.diff(s_roi) >= 0) and np.all(np.diff(r_roi) >= 0))

    def test_a_free_form_mask_roi_is_not_vectorised_but_still_correct(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        self.assertEqual(list(np.flatnonzero(~rs.vectorised)), [8])

    def test_the_default_method_is_the_mean(self) -> None:
        """The first stored value is the mean (maintainer, 2026-10-19); the others are optional extra layers."""
        rs = build_roi_set(_rois(), INNER, OUTER)
        result = reduce_frame(rs, _image(), None, IDENTITY)
        self.assertEqual(result.methods, ("mean",))
        self.assertEqual(list(result.values), ["mean"])

    def test_no_rois_gives_empty_arrays(self) -> None:
        rs = build_roi_set([], INNER, OUTER)
        result = reduce_frame(rs, _image(), None, IDENTITY, ("mean",))
        self.assertEqual(result.values["mean"].shape, (0, 2))
        self.assertEqual(result.n_valid.shape, (0, 2))


class ReduceFrameTests(unittest.TestCase):
    def test_matches_the_per_roi_rules_for_every_mode_mask_and_method(self) -> None:
        rois = _rois()
        rs = build_roi_set(rois, INNER, OUTER)
        image = _image()
        for mode in REFERENCE_EXCLUSION_MODES:
            for ignore in (None, _ignore()):
                for name, affine in (("identity", IDENTITY), ("chromatic", CHROMATIC)):
                    with self.subTest(mode=mode, ignore=ignore is not None, affine=name):
                        expected = _reference_cells(rois, image, ignore, affine, mode, REDUCTION_METHODS)
                        got = reduce_frame(rs, image, ignore, affine, REDUCTION_METHODS, exclusion_mode=mode)
                        self.assertTrue(_same(got.n_nominal, expected["nominal"]))
                        self.assertTrue(_same(got.n_valid, expected["valid"]))
                        for method in REDUCTION_METHODS:
                            if method == "mean":
                                # Accumulated in float64 (maintainer, 2026-10-19): equal to a float64 reference, and within
                                # 1e-6 relative of the old float32 mean (in practice ~1e-7).
                                np.testing.assert_allclose(got.values["mean"], np.array(expected["mean64"]), rtol=1e-12, atol=0.0)
                                np.testing.assert_allclose(got.values["mean"], np.array(expected["values"]["mean"]), rtol=1e-6, atol=0.0)
                            else:
                                self.assertTrue(_same(got.values[method], expected["values"][method]), method)
                        np.testing.assert_allclose(got.std, np.array(expected["std"]), rtol=1e-12, atol=0.0)

    def test_the_fixture_really_exercises_the_awkward_cases(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        image, ignore = _image(), _ignore()
        with_exclusion = reduce_frame(rs, image, None, IDENTITY, ("mean",), exclusion_mode="exclude_all_sample_rois")
        without = reduce_frame(rs, image, None, IDENTITY, ("mean",), exclusion_mode="none")
        self.assertLess(with_exclusion.n_nominal[1, REFERENCE], without.n_nominal[1, REFERENCE])  # ROI 2's ring loses ROI 3's sample
        self.assertLess(with_exclusion.n_valid[3, SAMPLE], with_exclusion.n_nominal[3, SAMPLE])  # NaN block on ROI 4
        masked = reduce_frame(rs, image, ignore, IDENTITY, ("mean",))
        self.assertLess(masked.n_nominal[0, SAMPLE], with_exclusion.n_nominal[0, SAMPLE])  # ignore mask on ROI 1
        self.assertGreater(with_exclusion.n_nominal[4, SAMPLE], 0)  # corner ROI still has pixels
        self.assertLess(with_exclusion.n_nominal[4, SAMPLE], np.pi * 5.0**2)  # ... but only the part inside the image

    def test_n_masked_is_what_the_ignore_mask_removed_from_each_aperture(self) -> None:
        rois = _rois()
        rs = build_roi_set(rois, INNER, OUTER)
        image, ignore = _image(), _ignore()
        free = reduce_frame(rs, image, None, IDENTITY, ("mean",))
        masked = reduce_frame(rs, image, ignore, IDENTITY, ("mean",))
        self.assertEqual(masked.n_masked.dtype, np.int32)
        self.assertFalse(free.n_masked.any())  # no mask: nothing removed
        self.assertTrue(np.array_equal(masked.n_masked, free.n_nominal - masked.n_nominal))
        self.assertGreater(masked.n_masked[0, SAMPLE], 0)  # ROI 1 sits under the mask
        self.assertEqual(masked.n_masked[6, SAMPLE], 0)  # ROI 7 is far from it
        shape = image.shape
        union = np.zeros(shape, dtype=bool)
        for roi in rois:
            union |= rz.rasterize_sample(roi, shape, IDENTITY)
        for index, roi in enumerate(rois):  # independently: the part of each aperture that lies inside the ignore mask
            sample = rz.rasterize_sample(roi, shape, IDENTITY)
            ring = rz.rasterize_reference(roi, shape, IDENTITY, default_inner_diameter_px=INNER, default_outer_diameter_px=OUTER) & ~union
            self.assertEqual(int(masked.n_masked[index, SAMPLE]), int((sample & ignore).sum()), f"sample of ROI {roi.area_roi_id}")
            self.assertEqual(int(masked.n_masked[index, REFERENCE]), int((ring & ignore).sum()), f"ring of ROI {roi.area_roi_id}")

    def test_a_roi_without_a_ring_or_sample_gets_nan_values_and_counts_of_zero(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        result = reduce_frame(rs, _image(), None, IDENTITY, ("mean",))
        for index in (6, 7):  # no ring; empty sample
            self.assertTrue(np.all(np.isnan(result.values["mean"][index])))
        self.assertEqual(result.n_valid[6, REFERENCE], 0)
        self.assertEqual(result.n_valid[7, SAMPLE], 0)
        self.assertTrue(np.isnan(result.std[6, REFERENCE]))

    def test_the_standard_deviation_is_that_of_the_valid_pixels(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        image = _image()
        result = reduce_frame(rs, image, None, IDENTITY, ("mean",), exclusion_mode="none")
        sample = rz.rasterize_sample(rs.rois[0], image.shape, IDENTITY) & np.isfinite(image)
        self.assertAlmostEqual(result.std[0, SAMPLE], float(np.std(image[sample].astype(np.float64), ddof=1)), places=9)

    def test_unknown_method_or_mode_is_an_error_not_a_silent_mean(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        with self.assertRaises(ValueError):
            reduce_frame(rs, _image(), None, IDENTITY, ("average",))
        with self.assertRaises(ValueError):
            reduce_frame(rs, _image(), None, IDENTITY, ("mean",), exclusion_mode="some")

    def test_a_mask_of_another_shape_is_ignored_like_compute_cell_does(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        wrong = np.ones((5, 5), dtype=bool)
        a = reduce_frame(rs, _image(), wrong, IDENTITY, ("mean",))
        b = reduce_frame(rs, _image(), None, IDENTITY, ("mean",))
        self.assertTrue(_same(a.values["mean"], b.values["mean"]))


class PixelLayoutTests(unittest.TestCase):
    """A cached layout must change nothing; the cache must only return it for exactly the same inputs."""

    def test_reusing_a_layout_gives_bit_identical_results(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        image = _image()
        for mode in REFERENCE_EXCLUSION_MODES:
            for ignore in (None, _ignore()):
                for name, affine in (("identity", IDENTITY), ("chromatic", CHROMATIC)):
                    with self.subTest(mode=mode, ignore=ignore is not None, affine=name):
                        layout = build_layout(rs, affine, image.shape, mode)
                        a = reduce_frame(rs, image, ignore, affine, REDUCTION_METHODS, exclusion_mode=mode)
                        b = reduce_frame(rs, image, ignore, affine, REDUCTION_METHODS, exclusion_mode=mode, layout=layout)
                        for method in REDUCTION_METHODS:
                            self.assertTrue(_same(a.values[method], b.values[method]), method)
                        self.assertTrue(_same(a.std, b.std) and _same(a.n_valid, b.n_valid) and _same(a.n_nominal, b.n_nominal))

    def test_cache_hits_only_for_the_same_inputs(self) -> None:
        cache = LayoutCache(max_entries=8)
        rois = _rois()
        rs = build_roi_set(rois, INNER, OUTER)
        first = cache.get(rs, CHROMATIC, (HEIGHT, WIDTH))
        self.assertIs(cache.get(build_roi_set(rois, INNER, OUTER), CHROMATIC.copy(), (HEIGHT, WIDTH)), first)  # equal geometry, new objects
        self.assertIsNot(cache.get(rs, IDENTITY, (HEIGHT, WIDTH)), first)
        self.assertIsNot(cache.get(rs, CHROMATIC, (HEIGHT, WIDTH), "none"), first)
        moved = [replace(rois[0], center_x=rois[0].center_x + 0.01)] + rois[1:]
        self.assertNotEqual(build_roi_set(moved, INNER, OUTER).key, rs.key)
        self.assertNotEqual(build_roi_set(rois, INNER, OUTER + 1.0).key, rs.key)  # the shared ring default is part of the key
        mask_changed = rois[:8] + [replace(rois[8], sample_mask=RoiMask(26, 61, np.ones((8, 8), dtype=bool)))]
        self.assertNotEqual(build_roi_set(mask_changed, INNER, OUTER).key, rs.key)  # a free-form mask's pixels are part of it
        self.assertEqual((cache.hits, cache.misses), (1, 3))

    def test_cache_keeps_at_most_max_entries(self) -> None:
        cache = LayoutCache(max_entries=2)
        rs = build_roi_set(_rois(), INNER, OUTER)
        shifted = [np.array([[1.0, 0.0, dx], [0.0, 1.0, 0.0]]) for dx in (0.1, 0.2, 0.3)]
        for affine in shifted:
            cache.get(rs, affine, (HEIGHT, WIDTH))
        cache.get(rs, shifted[0], (HEIGHT, WIDTH))  # evicted, so built again
        self.assertEqual(cache.misses, 4)

    def test_a_layout_for_other_inputs_is_refused(self) -> None:
        rs = build_roi_set(_rois(), INNER, OUTER)
        layout = build_layout(rs, IDENTITY, (HEIGHT, WIDTH), "none")
        with self.assertRaises(ValueError):
            reduce_frame(rs, _image(), None, IDENTITY, layout=layout)  # mode differs from the layout's
        with self.assertRaises(ValueError):
            reduce_frame(rs, _image()[:-1], None, IDENTITY, exclusion_mode="none", layout=layout)  # shape differs


class SegmentStdTests(unittest.TestCase):
    def test_blocks_of_different_sizes_including_empty_and_single(self) -> None:
        rng = np.random.default_rng(3)
        counts = np.array([5, 0, 1, 12, 0, 2, 0])
        offsets = np.concatenate(([0], np.cumsum(counts)))
        values = rng.normal(40000.0, 900.0, int(counts.sum())).astype(np.float32)
        got = segment_std(values, offsets, counts)
        for i, n in enumerate(counts):
            block = values[offsets[i]:offsets[i + 1]].astype(np.float64)
            if n > 1:
                self.assertAlmostEqual(got[i], float(np.std(block, ddof=1)), places=9)
            else:
                self.assertTrue(np.isnan(got[i]))

    def test_mean_is_a_float64_mean_and_nan_only_for_empty_blocks(self) -> None:
        rng = np.random.default_rng(5)
        counts = np.array([5, 0, 1, 900, 0, 2])
        offsets = np.concatenate(([0], np.cumsum(counts)))
        values = rng.normal(40000.0, 900.0, int(counts.sum())).astype(np.float32)
        mean, std = segment_mean_std(values, offsets, counts)
        for i, n in enumerate(counts):
            block = values[offsets[i]:offsets[i + 1]].astype(np.float64)
            if n:
                self.assertAlmostEqual(mean[i] / float(np.mean(block)), 1.0, places=13)
            else:
                self.assertTrue(np.isnan(mean[i]) and np.isnan(std[i]))
        self.assertTrue(_same(std, segment_std(values, offsets, counts)))

    def test_all_blocks_too_small(self) -> None:
        got = segment_std(np.zeros(0, dtype=np.float32), np.zeros(4, dtype=np.int64), np.zeros(3, dtype=np.int64))
        self.assertTrue(np.all(np.isnan(got)))


class CachedRoiExclusionTests(unittest.TestCase):
    """`roi_exclusion` must give exactly the result of passing the ROIs, only without rebuilding the mask each frame."""

    def _frame(self):
        rng = np.random.default_rng(11)
        y, x = np.mgrid[0:HEIGHT, 0:WIDTH]
        return (4000.0 + 5.0 * x + 8.0 * y + rng.normal(0.0, 25.0, (HEIGHT, WIDTH))).astype(np.float32)

    def test_same_image_as_passing_the_rois(self) -> None:
        rois = _rois()[:8]
        geometry = GeometrySettings()
        for exclude_rois in (True, False):
            with self.subTest(exclude_rois=exclude_rois):
                background = BackgroundSettings(
                    flatten_background_enabled=True, flatten_background_sigma_px=12.0, flatten_background_binning=2,
                    flatten_background_exclude_area_rois=exclude_rois,
                )
                frame = self._frame()
                expected = apply_preprocessing(frame, geometry, background, rois=rois)
                cached = roi_exclusion_mask((HEIGHT, WIDTH), rois)
                got = apply_preprocessing(frame, geometry, background, roi_exclusion=cached)
                self.assertTrue(np.array_equal(expected, got, equal_nan=True))
                self.assertTrue(np.array_equal(cached, roi_exclusion_mask((HEIGHT, WIDTH), rois)))  # the caller's array is untouched

    def test_a_mask_of_the_wrong_shape_fails_loudly(self) -> None:
        background = BackgroundSettings(flatten_background_enabled=True, flatten_background_binning=2)
        with self.assertRaises(ValueError):
            apply_preprocessing(self._frame(), GeometrySettings(), background, roi_exclusion=np.zeros((5, 5), dtype=bool))


class BackgroundCacheAndPixelsTests(unittest.TestCase):
    """The weights cache and the at-pixels divide must give exactly what the plain full-frame path gives."""

    def _frame(self):
        rng = np.random.default_rng(13)
        y, x = np.mgrid[0:HEIGHT, 0:WIDTH]
        return (4000.0 + 6.0 * x - 4.0 * y + rng.normal(0.0, 30.0, (HEIGHT, WIDTH))).astype(np.float32)

    def test_weights_cache_gives_identical_estimates(self) -> None:
        image = self._frame()
        valid = ~roi_exclusion_mask((HEIGHT, WIDTH), _rois()[:8])
        no_support = valid.copy()
        no_support[:, 60:] = False  # a large invalid region: part of the estimate needs the median fallback
        for name, mask in (("ROIs excluded", valid), ("needs the fallback", no_support)):
            for binning, sigma in ((1, 6.0), (2, 12.0), (4, 12.0)):
                with self.subTest(mask=name, binning=binning):
                    cache = BackgroundWeightsCache()
                    plain = bg_estimate._background_and_baseline(image, mask, sigma, binning)
                    first = bg_estimate._background_and_baseline(image, mask, sigma, binning, weights_cache=cache)
                    again = bg_estimate._background_and_baseline(image * 1.01, mask.copy(), sigma, binning, weights_cache=cache)
                    self.assertTrue(np.array_equal(plain[0], first[0], equal_nan=True) and plain[1] == first[1])
                    reference = bg_estimate._background_and_baseline(image * 1.01, mask, sigma, binning)
                    self.assertTrue(np.array_equal(reference[0], again[0], equal_nan=True) and reference[1] == again[1])
                    self.assertEqual((cache.misses, cache.hits), (1, 1))  # same mask content, new array: reused

    def test_weights_cache_misses_when_one_pixel_differs(self) -> None:
        cache = BackgroundWeightsCache()
        valid = np.ones((HEIGHT, WIDTH), dtype=bool)
        cache.part(valid, 12.0, 2)
        changed = valid.copy()
        changed[40, 40] = False
        cache.part(changed, 12.0, 2)
        cache.part(valid, 12.0, 4)  # other binning
        self.assertEqual((cache.misses, cache.hits), (3, 0))

    def test_preprocessing_at_pixels_equals_the_full_frame(self) -> None:
        rois = _rois()[:8]
        frame = self._frame()
        index = np.flatnonzero(np.random.default_rng(2).random(HEIGHT * WIDTH) < 0.3)
        ignore = _ignore()
        for flatten in (True, False):
            for rotation in (0.0, 1.4):
                with self.subTest(flatten=flatten, rotation=rotation):
                    geometry = GeometrySettings(rotation_angle_deg=rotation)
                    background = BackgroundSettings(flatten_background_enabled=flatten, flatten_background_sigma_px=12.0,
                                                    flatten_background_binning=2, flatten_background_exclude_mask=True)
                    full = apply_preprocessing(frame, geometry, background, rois=rois, external_mask=ignore,
                                               mask_settings=_detection_settings())
                    shape = full.shape[:2]
                    idx = index[index < shape[0] * shape[1]]
                    cache = BackgroundWeightsCache()
                    for _ in range(2):  # second pass uses the cache
                        part = apply_preprocessing_at(frame, idx, geometry, background, mask_settings=_detection_settings(),
                                                      external_mask=ignore if ignore.shape == shape else None,
                                                      roi_exclusion=roi_exclusion_mask(shape, rois), weights_cache=cache)
                        if ignore.shape != shape:
                            full_here = apply_preprocessing(frame, geometry, background, rois=rois, mask_settings=_detection_settings())
                        else:
                            full_here = full
                        self.assertTrue(np.array_equal(part, full_here.reshape(-1)[idx], equal_nan=True))


def _detection_settings():
    from lspr_imaging_app.roi.model import AreaRoiDetectionSettings

    return AreaRoiDetectionSettings(ignore_marked_pixels=True)


if __name__ == "__main__":
    unittest.main()
