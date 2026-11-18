"""Which reference goes with which sample (phase 1.2b of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

Covers `analysis/reference_pairing.py`: the paired view of a stored scan (one row per sample ROI, the reference side = linked references, else
the built-in ring, else nothing), the pooling of several references by pixel, and the end-to-end proof that a separate reference ROI with
exactly the pixels of a ring gives the ring's numbers. No Qt, no files.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import frame_reduce as fr
    from lspr_imaging_app.analysis.query import formula_spectrum
    from lspr_imaging_app.analysis.reference_pairing import REFERENCE, SAMPLE, pair_scan
    from lspr_imaging_app.analysis.scan_store import StoredScan
    from lspr_imaging_app.analysis.scan_view import SpectraRequest, SpectrumFitJob, prepare_spectra
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.domain.roi_model import AreaRoi, RoiMask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

N_CUBE, N_WL = 3, 4
WL = (500.0, 510.0, 520.0, 530.0)


def _roi(roi_id: int, role: str = "sample", links: tuple[int, ...] = (), **kw) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=10.0 * roi_id, center_y=20.0, sample_diameter_px=6.0, role=role, reference_roi_ids=links, **kw)


def _stored(roi_ids: tuple[int, ...], seed: int = 1, methods: tuple[str, ...] = ("mean",)) -> StoredScan:
    """A scan with random but plausible numbers: every (cube, roi, wavelength, side) has 50..400 pixels, a mean near 1000 and an SD near 30."""
    rng = np.random.default_rng(seed)
    shape = (N_CUBE, len(roi_ids), N_WL, 2)
    n = rng.integers(50, 400, shape).astype(np.uint32)
    return StoredScan(
        path=Path("memory.h5"), attrs={}, cube_indices=tuple(range(N_CUBE)), roi_ids=roi_ids, wavelength_nm=WL,
        acquired_at_unix_ms=np.zeros((N_CUBE, N_WL), dtype=np.int64),
        values={m: rng.normal(1000.0, 80.0, shape) for m in methods}, std=rng.normal(30.0, 3.0, shape), n_valid=n,
        n_nominal=n + 5, n_masked=rng.integers(0, 4, shape).astype(np.uint32),
        cell_hash=rng.integers(1, 2**60, (N_CUBE, len(roi_ids))).astype(np.uint64),
        recipe_id=np.ones((N_CUBE, len(roi_ids)), dtype=np.int32), layout_id=np.ones(N_CUBE, dtype=np.int32),
    )


def _copies(stored: StoredScan) -> list[np.ndarray]:
    return [a.copy() for a in (*stored.values.values(), stored.std, stored.n_valid, stored.n_nominal, stored.n_masked, stored.cell_hash)]


class PairingStructureTests(unittest.TestCase):
    def test_a_session_without_references_or_links_is_returned_as_it_is(self) -> None:
        stored = _stored((1, 2, 3))
        paired = pair_scan(stored, [_roi(1), _roi(2), _roi(3, reference_geometry_type="none")])
        self.assertIs(paired.stored, stored)  # nothing copied
        self.assertEqual(paired.sources, {1: "builtin", 2: "builtin", 3: "none"})
        self.assertIn("No reference", paired.problems[3])
        self.assertNotIn(1, paired.problems)

    def test_a_reference_roi_has_no_row_and_the_samples_keep_the_files_row_order(self) -> None:
        stored = _stored((1, 2, 3, 4))
        rois = [_roi(4), _roi(1, links=(2,)), _roi(2, role="reference"), _roi(3)]  # given in another order than the file's
        paired = pair_scan(stored, rois)
        self.assertEqual(paired.stored.roi_ids, (1, 3, 4))
        self.assertEqual(paired.stored.values["mean"].shape[1], 3)
        self.assertEqual(paired.stored.cell_hash.shape, (N_CUBE, 3))
        self.assertEqual(paired.sources, {1: "linked", 3: "builtin", 4: "builtin"})

    def test_a_sample_without_links_keeps_its_ring_numbers_and_the_raw_scan_is_not_changed(self) -> None:
        stored = _stored((1, 2, 3))
        before = _copies(stored)
        paired = pair_scan(stored, [_roi(1, links=(2,)), _roi(2, role="reference"), _roi(3)])
        np.testing.assert_array_equal(paired.stored.values["mean"][:, 1], stored.values["mean"][:, 2])  # ROI 3, whole row
        for a, b in zip(before, _copies(stored)):
            np.testing.assert_array_equal(a, b)

    def test_a_roi_added_after_the_scan_is_left_out(self) -> None:
        paired = pair_scan(_stored((1, 2)), [_roi(1, links=(2,)), _roi(2, role="reference"), _roi(9)])
        self.assertEqual(paired.stored.roi_ids, (1,))
        self.assertNotIn(9, paired.sources)


class SingleLinkedReferenceTests(unittest.TestCase):
    def test_one_linked_reference_replaces_the_ring_with_its_stored_region_exactly(self) -> None:
        stored = _stored((1, 2, 3), methods=("mean", "median"))
        paired = pair_scan(stored, [_roi(1, links=(2,)), _roi(2, role="reference"), _roi(3, links=(2,))]).stored  # one reference serves two samples
        for row in (0, 1):  # samples 1 and 3
            for m in ("mean", "median"):
                np.testing.assert_array_equal(paired.values[m][:, row, :, REFERENCE], stored.values[m][:, 1, :, SAMPLE])
            for name in ("std", "n_valid", "n_nominal", "n_masked"):
                np.testing.assert_array_equal(getattr(paired, name)[:, row, :, REFERENCE], getattr(stored, name)[:, 1, :, SAMPLE])
        np.testing.assert_array_equal(paired.values["mean"][:, 0, :, SAMPLE], stored.values["mean"][:, 0, :, SAMPLE])  # the sample side is its own
        np.testing.assert_array_equal(paired.std[:, 1, :, SAMPLE], stored.std[:, 2, :, SAMPLE])

    def test_a_cube_where_the_reference_was_never_scanned_is_not_complete(self) -> None:
        stored = _stored((1, 2))
        stored.cell_hash[1, 1] = 0  # ROI 2 (the reference) was not scanned on cube 1
        paired = pair_scan(stored, [_roi(1, links=(2,)), _roi(2, role="reference")]).stored
        self.assertEqual(list(paired.cell_hash[:, 0] != 0), [True, False, True])

    def test_a_link_to_a_roi_that_is_not_in_the_file_leaves_the_sample_without_a_reference(self) -> None:
        stored = _stored((1,))
        paired = pair_scan(stored, [_roi(1, links=(7,)), _roi(7, role="reference")])
        self.assertTrue(np.isnan(paired.stored.values["mean"][:, 0, :, REFERENCE]).all())
        self.assertEqual(int(paired.stored.n_valid[:, 0, :, REFERENCE].sum()), 0)
        self.assertFalse((paired.stored.cell_hash != 0).any())
        self.assertIn("7", paired.problems[1])
        self.assertIn("scan again", paired.problems[1])


class PoolingTests(unittest.TestCase):
    """Several linked references are the pixels of one region: the numbers must be the ones of the concatenated pixels."""

    def _pixels_and_stored(self, sizes_per_region: list[list[int]], seed: int = 5) -> tuple[StoredScan, list[list[np.ndarray]]]:
        """``len(sizes_per_region)`` reference regions (file rows 1..), each with, per (cube, wavelength), a block of pixels of the given sizes."""
        rng = np.random.default_rng(seed)
        n_regions = len(sizes_per_region)
        pixels = [[rng.normal(1000.0 + 25.0 * k, 20.0 + 5.0 * k, size=sizes[c * N_WL + w]) for c in range(N_CUBE) for w in range(N_WL)]
                  for k, sizes in enumerate(sizes_per_region)]
        stored = _stored(tuple(range(1, n_regions + 2)))
        for k in range(n_regions):
            for c in range(N_CUBE):
                for w in range(N_WL):
                    p = pixels[k][c * N_WL + w]
                    stored.values["mean"][c, 1 + k, w, SAMPLE] = p.mean() if p.size else np.nan
                    stored.std[c, 1 + k, w, SAMPLE] = p.std(ddof=1) if p.size > 1 else np.nan
                    stored.n_valid[c, 1 + k, w, SAMPLE] = p.size
        return stored, pixels

    def test_two_and_three_pooled_regions_match_the_concatenated_pixels(self) -> None:
        rng = np.random.default_rng(2)
        for n_regions in (2, 3):
            sizes = [list(rng.integers(5, 300, N_CUBE * N_WL)) for _ in range(n_regions)]
            stored, pixels = self._pixels_and_stored(sizes)
            rois = [_roi(1, links=tuple(range(2, 2 + n_regions)))] + [_roi(2 + k, role="reference") for k in range(n_regions)]
            paired = pair_scan(stored, rois).stored
            for c in range(N_CUBE):
                for w in range(N_WL):
                    whole = np.concatenate([pixels[k][c * N_WL + w] for k in range(n_regions)])
                    self.assertAlmostEqual(paired.values["mean"][c, 0, w, REFERENCE] / whole.mean(), 1.0, places=12)
                    self.assertAlmostEqual(paired.std[c, 0, w, REFERENCE] / whole.std(ddof=1), 1.0, places=12)
                    self.assertEqual(int(paired.n_valid[c, 0, w, REFERENCE]), whole.size)

    def test_the_order_of_the_links_does_not_matter(self) -> None:
        stored, _ = self._pixels_and_stored([[40] * 12, [70] * 12])
        a = pair_scan(stored, [_roi(1, links=(2, 3)), _roi(2, role="reference"), _roi(3, role="reference")]).stored
        b = pair_scan(stored, [_roi(1, links=(3, 2)), _roi(2, role="reference"), _roi(3, role="reference")]).stored
        np.testing.assert_allclose(a.values["mean"], b.values["mean"], rtol=1e-14)
        np.testing.assert_allclose(a.std, b.std, rtol=1e-14)

    def test_an_empty_region_adds_nothing_and_a_one_pixel_region_adds_no_spread_of_its_own(self) -> None:
        sizes = [[0] * (N_CUBE * N_WL), [1] * (N_CUBE * N_WL), [30] * (N_CUBE * N_WL)]
        stored, pixels = self._pixels_and_stored(sizes)
        rois = [_roi(1, links=(2, 3, 4))] + [_roi(i, role="reference") for i in (2, 3, 4)]
        paired = pair_scan(stored, rois).stored
        whole = np.concatenate([pixels[1][0], pixels[2][0]])
        self.assertEqual(int(paired.n_valid[0, 0, 0, REFERENCE]), 31)
        self.assertAlmostEqual(paired.values["mean"][0, 0, 0, REFERENCE] / whole.mean(), 1.0, places=12)
        self.assertAlmostEqual(paired.std[0, 0, 0, REFERENCE] / whole.std(ddof=1), 1.0, places=12)

    def test_regions_without_any_pixel_give_no_value_and_one_pixel_gives_no_spread(self) -> None:
        stored, _ = self._pixels_and_stored([[0] * (N_CUBE * N_WL), [0] * (N_CUBE * N_WL)])
        paired = pair_scan(stored, [_roi(1, links=(2, 3)), _roi(2, role="reference"), _roi(3, role="reference")]).stored
        self.assertTrue(np.isnan(paired.values["mean"][:, 0, :, REFERENCE]).all())
        self.assertEqual(int(paired.n_valid[:, 0, :, REFERENCE].sum()), 0)
        stored, _ = self._pixels_and_stored([[1] * (N_CUBE * N_WL), [0] * (N_CUBE * N_WL)])
        paired = pair_scan(stored, [_roi(1, links=(2, 3)), _roi(2, role="reference"), _roi(3, role="reference")]).stored
        self.assertTrue(np.isfinite(paired.values["mean"][:, 0, :, REFERENCE]).all())
        self.assertTrue(np.isnan(paired.std[:, 0, :, REFERENCE]).all())

    def test_counts_masked_and_nominal_add_up_and_a_median_of_pooled_regions_is_left_empty(self) -> None:
        stored = _stored((1, 2, 3), methods=("mean", "median"))
        paired = pair_scan(stored, [_roi(1, links=(2, 3)), _roi(2, role="reference"), _roi(3, role="reference")]).stored
        for name in ("n_nominal", "n_masked"):
            np.testing.assert_array_equal(
                getattr(paired, name)[:, 0, :, REFERENCE], getattr(stored, name)[:, 1, :, SAMPLE] + getattr(stored, name)[:, 2, :, SAMPLE]
            )
        self.assertTrue(np.isnan(paired.values["median"][:, 0, :, REFERENCE]).all())  # not poolable from stored medians
        self.assertTrue(np.isfinite(paired.values["mean"][:, 0, :, REFERENCE]).all())

    def test_the_pooling_is_the_same_for_every_sample_that_shares_the_references(self) -> None:
        stored = _stored((1, 2, 3, 4))
        rois = [_roi(1, links=(3, 4)), _roi(2, links=(3, 4)), _roi(3, role="reference"), _roi(4, role="reference")]
        paired = pair_scan(stored, rois).stored
        np.testing.assert_array_equal(paired.values["mean"][:, 0, :, REFERENCE], paired.values["mean"][:, 1, :, REFERENCE])
        np.testing.assert_array_equal(paired.std[:, 0, :, REFERENCE], paired.std[:, 1, :, REFERENCE])


class ProofFromPixelsTests(unittest.TestCase):
    """The plan's proof test, end to end through the real reduction: a sample linked to a *separate* reference ROI that has exactly the pixels of its
    own ring gives the numbers of the built-in ring (and two halves of that ring, pooled, give the same to rounding)."""

    H, W = 90, 120

    def setUp(self) -> None:
        self.image = np.random.default_rng(11).normal(1000.0, 40.0, (self.H, self.W)).astype(np.float32)
        self.sample = AreaRoi(area_roi_id=1, center_x=60.0, center_y=45.0, sample_diameter_px=10.0)
        ring_only = fr.build_roi_set([self.sample], 20.0, 34.0)
        self.layout = fr.build_layout(ring_only, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), (self.H, self.W))
        ring = np.zeros(self.H * self.W, dtype=bool)
        ring[self.layout.ring_flat] = True
        self.ring = ring.reshape(self.H, self.W)

    def _mask_roi(self, roi_id: int, mask: np.ndarray) -> AreaRoi:
        return AreaRoi(
            area_roi_id=roi_id, center_x=60.0, center_y=45.0, sample_diameter_px=10.0, role="reference", sample_geometry_type="mask",
            sample_mask=RoiMask(x0=0, y0=0, mask=mask),
        )

    def _stored_from(self, rois: list[AreaRoi]) -> tuple[StoredScan, fr.FrameReduction]:
        red = fr.reduce_frame(fr.build_roi_set(rois, 20.0, 34.0), self.image, None, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), ("mean",))
        n = len(rois)
        shaped = lambda a: np.asarray(a)[None, :, None, :]  # noqa: E731  one cube, one wavelength
        stored = StoredScan(
            path=Path("memory.h5"), attrs={}, cube_indices=(0,), roi_ids=tuple(r.area_roi_id for r in rois), wavelength_nm=(500.0,),
            acquired_at_unix_ms=np.zeros((1, 1), dtype=np.int64), values={"mean": shaped(red.values["mean"])}, std=shaped(red.std),
            n_valid=shaped(red.n_valid).astype(np.uint32), n_nominal=shaped(red.n_nominal).astype(np.uint32),
            n_masked=shaped(red.n_masked).astype(np.uint32), cell_hash=np.ones((1, n), dtype=np.uint64),
            recipe_id=np.ones((1, n), dtype=np.int32), layout_id=np.ones(1, dtype=np.int32),
        )
        return stored, red

    def test_a_reference_roi_with_the_pixels_of_the_ring_gives_the_ring_numbers(self) -> None:
        builtin = replace(self.sample, reference_roi_ids=())
        reference = self._mask_roi(2, self.ring)
        stored, _ = self._stored_from([builtin, reference])
        want_mean = stored.values["mean"][0, 0, 0, REFERENCE]
        self.assertTrue(np.isfinite(want_mean))
        paired = pair_scan(stored, [replace(builtin, reference_roi_ids=(2,)), reference]).stored
        for name, got in (("mean", paired.values["mean"]), ("std", paired.std), ("n_valid", paired.n_valid), ("n_nominal", paired.n_nominal)):
            source = stored.values["mean"] if name == "mean" else getattr(stored, name)
            self.assertEqual(got[0, 0, 0, REFERENCE], source[0, 0, 0, REFERENCE], name)  # exactly, not to a tolerance

    def test_two_halves_of_the_ring_pooled_give_the_whole_ring_to_rounding(self) -> None:
        upper = self.ring.copy()
        upper[46:, :] = False
        lower = self.ring.copy()
        lower[:46, :] = False
        a, b = self._mask_roi(2, upper), self._mask_roi(3, lower)
        stored, _ = self._stored_from([self.sample, a, b])
        paired = pair_scan(stored, [replace(self.sample, reference_roi_ids=(2, 3)), a, b]).stored
        whole = stored.values["mean"][0, 0, 0, REFERENCE]
        self.assertAlmostEqual(paired.values["mean"][0, 0, 0, REFERENCE] / whole, 1.0, places=12)
        self.assertAlmostEqual(paired.std[0, 0, 0, REFERENCE] / stored.std[0, 0, 0, REFERENCE], 1.0, places=12)
        self.assertEqual(paired.n_valid[0, 0, 0, REFERENCE], stored.n_valid[0, 0, 0, REFERENCE])


class ReadersSeeThePairedScanTests(unittest.TestCase):
    def test_the_spectrum_of_a_linked_sample_is_made_from_its_references_numbers(self) -> None:
        stored = _stored((1, 2))
        stored.n_valid[:] = 100
        paired = pair_scan(stored, [_roi(1, links=(2,)), _roi(2, role="reference")]).stored
        request = SpectraRequest(roi_ids=(1,), cube=1, formula_key="absorbance", metric=MetricSettings())
        job = prepare_spectra(paired, request)[0]
        self.assertIsInstance(job, SpectrumFitJob)
        want = formula_spectrum(
            np.asarray(WL), stored.values["mean"][1, 0, :, SAMPLE], stored.values["mean"][1, 1, :, SAMPLE], "absorbance"
        ).values
        np.testing.assert_array_equal(job.spectrum.values, want)  # sample 1's own numbers over reference ROI 2's, not over the ring


if __name__ == "__main__":
    unittest.main()
