"""The scan side of reference ROIs (phase 1.2a of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

A reference ROI (`AreaRoi.role == "reference"`) is read as one region: its pixels go to the SAMPLE column, it has no ring, its region loses
the pixels of every *sample* ROI, and its disc takes nothing from anyone. A set with no reference ROI must give what it always gave (that
part is pinned by the existing frame_reduce tests and by a before / after fingerprint made when this was written).

No Qt, no files, small synthetic images.
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
    from lspr_imaging_app.analysis import frame_reduce as fr
    from lspr_imaging_app.analysis.scan_store import layout_json
    from lspr_imaging_app.domain.roi_model import AreaRoi, RoiMask
    from lspr_imaging_app.domain.roi_shapes import Border, Circle, MaskShape
    from lspr_imaging_app.roi import rasterize as rz
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

HEIGHT, WIDTH = 100, 140
INNER, OUTER = 14.0, 24.0
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])


def _roi(roi_id: int, x: float, y: float, d: float = 10.0, role: str = "sample", **kw) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=x, center_y=y, shape=Circle(d), role=role, **kw)


def _image(seed: int = 3) -> np.ndarray:
    return np.random.default_rng(seed).normal(1000.0, 40.0, (HEIGHT, WIDTH)).astype(np.float32)


def _pixels_of(layout: fr.PixelLayout, which: str, roi: int) -> np.ndarray:
    roi_arr, flat = (layout.sample_roi, layout.sample_flat) if which == "sample" else (layout.ring_roi, layout.ring_flat)
    return flat[roi_arr == roi]


class ReferenceRoiPixelsTests(unittest.TestCase):
    def setUp(self) -> None:
        # 0: a sample; 1: a reference whose disc overlaps the sample's disc; 2: a sample far away that has a ring; 3: a reference far from all
        self.rois = [_roi(1, 40.0, 40.0), _roi(2, 46.0, 40.0, role="reference"), _roi(3, 100.0, 60.0), _roi(4, 20.0, 80.0, role="reference")]
        self.rs = fr.build_roi_set(self.rois, Border.from_ring_diameters(10.0, INNER, OUTER))

    def test_reference_roi_has_no_ring_and_a_sample_does(self) -> None:
        self.assertEqual(list(self.rs.is_ref), [False, True, False, True])
        self.assertEqual(list(self.rs.ring_on), [True, False, True, False])
        for affine in (IDENTITY, CHROMATIC):
            layout = fr.build_layout(self.rs, affine, (HEIGHT, WIDTH))
            self.assertEqual(layout.ring_count[1], 0)
            self.assertEqual(layout.ring_count[3], 0)
            self.assertGreater(layout.ring_count[0], 0)
            self.assertGreater(layout.ring_count[2], 0)

    def test_reference_region_loses_every_sample_pixel_and_the_sample_loses_nothing(self) -> None:
        layout = fr.build_layout(self.rs, IDENTITY, (HEIGHT, WIDTH))
        sample_disc = np.flatnonzero(rz.rasterize_sample(self.rois[0], (HEIGHT, WIDTH), IDENTITY))
        reference_disc = np.flatnonzero(rz.rasterize_sample(self.rois[1], (HEIGHT, WIDTH), IDENTITY))
        np.testing.assert_array_equal(_pixels_of(layout, "sample", 0), sample_disc)  # the sample's aperture is never cut
        expected = np.setdiff1d(reference_disc, sample_disc)
        self.assertLess(expected.size, reference_disc.size)  # the discs really overlap
        np.testing.assert_array_equal(_pixels_of(layout, "sample", 1), expected)
        np.testing.assert_array_equal(_pixels_of(layout, "sample", 3), np.flatnonzero(rz.rasterize_sample(self.rois[3], (HEIGHT, WIDTH), IDENTITY)))

    def test_a_reference_disc_takes_no_pixels_from_a_sample_ring(self) -> None:
        """Next to a reference ROI, a sample's ring has the pixels it would have with no neighbour: only sample discs are cut out of a ring."""
        rois = [_roi(1, 40.0, 40.0), _roi(2, 52.0, 40.0, role="reference")]
        with_reference = fr.build_layout(fr.build_roi_set(rois, Border.from_ring_diameters(10.0, INNER, OUTER)), IDENTITY, (HEIGHT, WIDTH))
        alone = fr.build_layout(fr.build_roi_set(rois[:1], Border.from_ring_diameters(10.0, INNER, OUTER)), IDENTITY, (HEIGHT, WIDTH))
        np.testing.assert_array_equal(_pixels_of(with_reference, "ring", 0), _pixels_of(alone, "ring", 0))
        as_sample = fr.build_layout(fr.build_roi_set([rois[0], replace(rois[1], role="sample")], Border.from_ring_diameters(10.0, INNER, OUTER)), IDENTITY, (HEIGHT, WIDTH))
        self.assertLess(_pixels_of(as_sample, "ring", 0).size, _pixels_of(alone, "ring", 0).size)  # as a sample it would have cut the ring

    def test_exclusion_mode_none_cuts_nothing(self) -> None:
        layout = fr.build_layout(self.rs, IDENTITY, (HEIGHT, WIDTH), "none")
        full = np.flatnonzero(rz.rasterize_sample(self.rois[1], (HEIGHT, WIDTH), IDENTITY))
        np.testing.assert_array_equal(_pixels_of(layout, "sample", 1), full)

    def test_subset_layout_is_the_full_layout_cut_down_with_reference_rois(self) -> None:
        for affine in (IDENTITY, CHROMATIC):
            full = fr.build_layout(self.rs, affine, (HEIGHT, WIDTH))
            for subset in ((1,), (3,), (1, 2), (0, 1, 3), (3, 1)):
                got = fr.build_subset_layout(self.rs, affine, (HEIGHT, WIDTH), subset=subset)
                want = full.restrict(subset)
                for name in ("sample_roi", "sample_flat", "ring_roi", "ring_flat", "sample_count", "ring_count"):
                    np.testing.assert_array_equal(getattr(got, name), getattr(want, name), err_msg=f"{name} for subset {subset}")


class ReferenceRoiNumbersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rois = [_roi(1, 40.0, 40.0), _roi(2, 70.0, 40.0, role="reference"), _roi(3, 100.0, 60.0)]
        self.rs = fr.build_roi_set(self.rois, Border.from_ring_diameters(10.0, INNER, OUTER))
        self.image = _image()

    def test_a_reference_roi_stores_the_mean_std_and_count_of_its_own_region(self) -> None:
        for affine in (IDENTITY, CHROMATIC):
            red = fr.reduce_frame(self.rs, self.image, None, affine, ("mean",))
            region = self.image.reshape(-1)[_pixels_of(fr.build_layout(self.rs, affine, (HEIGHT, WIDTH)), "sample", 1)].astype(np.float64)
            self.assertAlmostEqual(red.values["mean"][1, fr.SAMPLE], region.mean(), places=9)
            self.assertAlmostEqual(red.std[1, fr.SAMPLE], region.std(ddof=1), places=9)
            self.assertEqual(red.n_valid[1, fr.SAMPLE], region.size)
            self.assertTrue(np.isnan(red.values["mean"][1, fr.REFERENCE]))
            self.assertEqual(red.n_valid[1, fr.REFERENCE], 0)
            self.assertEqual(red.n_nominal[1, fr.REFERENCE], 0)

    def test_samples_are_unchanged_by_a_reference_roi_in_the_set(self) -> None:
        """A sample's numbers with a far-away reference ROI in the set are those of the set without it."""
        with_ref = fr.reduce_frame(self.rs, self.image, None, CHROMATIC, ("mean",))
        without = fr.reduce_frame(fr.build_roi_set([self.rois[0], self.rois[2]], Border.from_ring_diameters(10.0, INNER, OUTER)), self.image, None, CHROMATIC, ("mean",))
        np.testing.assert_array_equal(with_ref.values["mean"][[0, 2]], without.values["mean"])
        np.testing.assert_array_equal(with_ref.std[[0, 2]], without.std)
        np.testing.assert_array_equal(with_ref.n_valid[[0, 2]], without.n_valid)

    def test_other_reduction_methods_read_the_region_alone(self) -> None:
        red = fr.reduce_frame(self.rs, self.image, None, IDENTITY, ("mean", "median", "trimmed_mean", "plane_fit"))
        region = self.image.reshape(-1)[_pixels_of(fr.build_layout(self.rs, IDENTITY, (HEIGHT, WIDTH)), "sample", 1)]
        self.assertAlmostEqual(red.values["median"][1, fr.SAMPLE], float(np.median(region)), places=3)
        self.assertTrue(np.isfinite(red.values["trimmed_mean"][1, fr.SAMPLE]))
        self.assertAlmostEqual(red.values["plane_fit"][1, fr.SAMPLE], float(region.astype(np.float64).mean()), places=3)  # plane_fit's sample side is a mean
        self.assertTrue(np.isnan(red.values["median"][1, fr.REFERENCE]))

    def test_the_ignore_mask_and_nan_pixels_leave_a_reference_region_like_a_sample(self) -> None:
        ignore = np.zeros((HEIGHT, WIDTH), dtype=bool)
        ignore[38:42, 66:74] = True
        image = self.image.copy()
        image[40, 70] = np.nan
        layout = fr.build_layout(self.rs, IDENTITY, (HEIGHT, WIDTH))
        pixels = _pixels_of(layout, "sample", 1)
        red = fr.reduce_frame(self.rs, image, ignore, IDENTITY, ("mean",))
        keep = ~ignore.reshape(-1)[pixels] & np.isfinite(image.reshape(-1)[pixels])
        self.assertEqual(red.n_valid[1, fr.SAMPLE], int(keep.sum()))
        self.assertEqual(red.n_masked[1, fr.SAMPLE], int(ignore.reshape(-1)[pixels].sum()))
        self.assertAlmostEqual(red.values["mean"][1, fr.SAMPLE], float(image.reshape(-1)[pixels][keep].astype(np.float64).mean()), places=9)

    def test_a_reference_roi_made_of_a_free_form_mask_has_only_its_region(self) -> None:
        mask = np.zeros((9, 9), dtype=bool)
        mask[2:7, 1:8] = True
        free = AreaRoi(area_roi_id=9, center_x=20.0, center_y=80.0, shape=MaskShape(RoiMask(x0=16, y0=76, mask=mask)), role="reference")
        rs = fr.build_roi_set([self.rois[0], free], Border.from_ring_diameters(10.0, INNER, OUTER))
        layout = fr.build_layout(rs, CHROMATIC, (HEIGHT, WIDTH))
        self.assertEqual(layout.ring_count[1], 0)
        self.assertGreater(layout.sample_count[1], 0)
        subset = fr.build_subset_layout(rs, CHROMATIC, (HEIGHT, WIDTH), subset=(1,))
        np.testing.assert_array_equal(subset.sample_flat, _pixels_of(layout, "sample", 1))


class ReferenceRoiKeysTests(unittest.TestCase):
    def test_the_role_is_part_of_what_decides_a_rois_pixels(self) -> None:
        as_sample = fr.build_roi_set([_roi(1, 40.0, 40.0)], Border.from_ring_diameters(10.0, INNER, OUTER))
        as_reference = fr.build_roi_set([_roi(1, 40.0, 40.0, role="reference")], Border.from_ring_diameters(10.0, INNER, OUTER))
        self.assertNotEqual(fr.roi_row_digests(as_sample), fr.roi_row_digests(as_reference))
        self.assertNotEqual(as_sample.key, as_reference.key)
        self.assertNotEqual(fr.local_roi_keys(as_sample), fr.local_roi_keys(as_reference))

    def test_an_all_sample_set_has_the_same_keys_as_before_reference_rois_existed(self) -> None:
        """Pinned (re-pinned at phase 2.1, see PINNED_ROW_DIGESTS): ROIs at (40, 40) d 10, (52, 40) d 12, (100, 60) d 8, default ring 14 / 24 around a 10 px sample."""
        rs = fr.build_roi_set([_roi(1, 40.0, 40.0), _roi(2, 52.0, 40.0, 12.0), _roi(3, 100.0, 60.0, 8.0)], Border.from_ring_diameters(10.0, INNER, OUTER))
        self.assertEqual([d.hex() for d in fr.roi_row_digests(rs)], PINNED_ROW_DIGESTS)
        self.assertEqual([k.hex() for k in fr.local_roi_keys(rs)], PINNED_LOCAL_KEYS)

    def test_moving_a_reference_roi_changes_no_samples_key_but_its_own(self) -> None:
        a = [_roi(1, 40.0, 40.0), _roi(2, 50.0, 40.0, role="reference"), _roi(3, 100.0, 60.0)]
        b = [a[0], replace(a[1], center_x=51.0), a[2]]
        ka = fr.local_roi_keys(fr.build_roi_set(a, Border.from_ring_diameters(10.0, INNER, OUTER)))
        kb = fr.local_roi_keys(fr.build_roi_set(b, Border.from_ring_diameters(10.0, INNER, OUTER)))
        self.assertEqual(ka[0], kb[0])  # its disc takes nothing from the sample's ring
        self.assertEqual(ka[2], kb[2])
        self.assertNotEqual(ka[1], kb[1])

    def test_moving_a_sample_next_to_a_reference_roi_changes_the_reference_roi_key(self) -> None:
        a = [_roi(1, 40.0, 40.0), _roi(2, 50.0, 40.0, role="reference")]
        b = [replace(a[0], center_x=41.0), a[1]]
        ka = fr.local_roi_keys(fr.build_roi_set(a, Border.from_ring_diameters(10.0, INNER, OUTER)))
        kb = fr.local_roi_keys(fr.build_roi_set(b, Border.from_ring_diameters(10.0, INNER, OUTER)))
        self.assertNotEqual(ka[1], kb[1])  # a sample disc cuts the reference region
        far = [_roi(1, 40.0, 40.0), _roi(2, 120.0, 90.0, role="reference")]
        far_moved = [replace(far[0], center_x=41.0), far[1]]
        self.assertEqual(
            fr.local_roi_keys(fr.build_roi_set(far, Border.from_ring_diameters(10.0, INNER, OUTER)))[1], fr.local_roi_keys(fr.build_roi_set(far_moved, Border.from_ring_diameters(10.0, INNER, OUTER)))[1]
        )

    def test_the_scan_files_layout_record_names_reference_rois_only(self) -> None:
        text = layout_json(fr.build_roi_set([_roi(1, 40.0, 40.0), _roi(2, 70.0, 40.0, role="reference")], Border.from_ring_diameters(10.0, INNER, OUTER)))
        self.assertEqual(text.count('"role": "reference"'), 1)
        self.assertNotIn('"role"', layout_json(fr.build_roi_set([_roi(1, 40.0, 40.0)], Border.from_ring_diameters(10.0, INNER, OUTER))))


# Made by the code as committed before phase 1.2a (git stash of frame_reduce.py), for the ROI set of the test named above.
# Re-pinned at phase 2.1 (2026-11-18): a circle's digest no longer holds the shared default diameters, and the shared ring is now a border around each sample.
PINNED_ROW_DIGESTS = ["140be928127116890eed5b9524205570", "0a4e7991d167b715f53ede2902c7698d", "1bcf5a85dad374ec9de9ff941c3b28fb"]
PINNED_LOCAL_KEYS = ["0a861939ef919f53ecd112bfca7dc76c", "879d52fd0053f440f4f6db40ec28771e", "958b1bdc4c6cc9747ca807759779f7dd"]


if __name__ == "__main__":
    unittest.main()
