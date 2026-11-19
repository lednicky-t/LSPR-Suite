"""The rim of a pixel map: every blob has a rim of its own (apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md, "Rim pairs, linked pairs, the Type column").

The rim of a map is the union of the bands of its blobs, each band measured from that blob's own edge; a pixel of another blob is never in a blob's rim. A map
of one blob keeps exactly the rim it always had. The brute-force reference is written here, with `cdist`, independently of the code under test.

No Qt, no files, small synthetic images.
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
    from lspr_imaging_app.analysis import frame_reduce as fr
    from lspr_imaging_app.domain.roi_model import AreaRoi
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, MaskShape
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.roi.mask_regions import blob_count
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402
from scipy.spatial.distance import cdist  # noqa: E402

HEIGHT, WIDTH = 60, 90
SHAPE = (HEIGHT, WIDTH)
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
XX, YY = np.meshgrid(np.arange(WIDTH, dtype=np.float64), np.arange(HEIGHT, dtype=np.float64))
EIGHT = np.ones((3, 3), dtype=bool)


def _disc(cx: float, cy: float, radius: float) -> np.ndarray:
    return (XX - cx) ** 2 + (YY - cy) ** 2 <= radius**2


def _roi(plane: np.ndarray, border: Border | None, roi_id: int = 1) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=30.0, center_y=30.0, shape=MaskShape(rz.crop_mask(plane)), border=border)


def _brute_rim(plane: np.ndarray, gap: float, thickness: float) -> np.ndarray:
    """The rim, from the definition: for each 8-connected blob, the pixels whose distance from that blob's edge (the edge half a pixel from the pixel
    centres) is in [gap, gap + thickness], without the pixels of the other blobs; all blobs together."""
    labels, count = ndimage.label(plane, structure=EIGHT)
    points = np.column_stack((XX.ravel(), YY.ravel()))
    out = np.zeros(plane.size, dtype=bool)
    for number in range(1, count + 1):
        blob = (labels == number).ravel()
        signed = np.empty(points.shape[0])
        signed[~blob] = cdist(points[~blob], points[blob]).min(axis=1) - 0.5
        signed[blob] = 0.5 - cdist(points[blob], points[~blob]).min(axis=1)
        out |= (signed >= gap) & (signed <= gap + thickness) & ~(plane.ravel() & ~blob)
    return out.reshape(plane.shape)


class PerBlobRimTests(unittest.TestCase):
    def test_a_map_of_two_blobs_has_the_union_of_their_own_rims(self) -> None:
        plane = _disc(25.0, 30.0, 5.0) | _disc(43.0, 30.0, 5.0)  # 8 px apart: each blob's rim reaches the other
        self.assertEqual(blob_count(plane), 2)
        roi = _roi(plane, Border(2.0, 3.0))
        got = rz.rasterize_reference(roi, SHAPE, IDENTITY)
        np.testing.assert_array_equal(got, _brute_rim(plane, 2.0, 3.0))
        self.assertFalse((got & plane).any())  # no pixel of the map itself

    def test_a_pixel_between_the_blobs_is_kept_although_a_neighbour_is_closer(self) -> None:
        """Measured from the map as a whole, a pixel that is 2 px from blob A (inside A's band) but 1.3 px from blob B is below the gap of B and was dropped; per blob it
        stays: it is in A's band, and it is not a pixel of B."""
        plane = _disc(25.0, 30.0, 5.0) | _disc(37.0, 30.0, 5.0)
        roi = _roi(plane, Border(2.0, 3.0))
        got = rz.rasterize_reference(roi, SHAPE, IDENTITY)
        whole = (lambda signed: (signed >= 2.0) & (signed <= 5.0))(
            np.where(plane, 0.5 - ndimage.distance_transform_edt(plane), ndimage.distance_transform_edt(~plane) - 0.5)
        )
        self.assertTrue((got & ~whole).any(), "the per-blob rim must reach pixels that the whole map's band leaves out")
        self.assertFalse((whole & ~got).any(), "and never lose one")

    def test_a_map_of_one_blob_keeps_the_rim_it_always_had(self) -> None:
        plane = _disc(40.0, 30.0, 7.0)
        for gap, thickness in ((2.0, 3.0), (0.0, 1.0), (-2.0, 3.0), (4.0, 4.0)):
            roi = _roi(plane, Border(gap, thickness))
            whole = np.where(plane, 0.5 - ndimage.distance_transform_edt(plane), ndimage.distance_transform_edt(~plane) - 0.5)
            np.testing.assert_array_equal(rz.rasterize_reference(roi, SHAPE, IDENTITY), (whole >= gap) & (whole <= gap + thickness), f"gap {gap}, thickness {thickness}")

    def test_blobs_that_touch_by_a_corner_are_one_blob(self) -> None:
        plane = np.zeros(SHAPE, dtype=bool)
        plane[20:25, 20:25] = True
        plane[25:30, 25:30] = True  # touches the first at one corner
        self.assertEqual(blob_count(plane), 1)
        roi = _roi(plane, Border(1.0, 2.0))
        np.testing.assert_array_equal(rz.rasterize_reference(roi, SHAPE, IDENTITY), _brute_rim(plane, 1.0, 2.0))

    def test_random_maps_against_the_brute_force(self) -> None:
        rng = np.random.default_rng(4)
        for trial in range(12):
            plane = np.zeros(SHAPE, dtype=bool)
            for _ in range(int(rng.integers(2, 5))):
                plane |= _disc(float(rng.uniform(12, 78)), float(rng.uniform(12, 48)), float(rng.uniform(2, 6)))
            gap, thickness = float(rng.uniform(-1, 4)), float(rng.uniform(1, 5))
            roi = _roi(plane, Border(gap, thickness))
            np.testing.assert_array_equal(rz.rasterize_reference(roi, SHAPE, IDENTITY), _brute_rim(plane, gap, thickness), f"trial {trial}")

    def test_the_drawn_rim_the_scans_pixels_and_the_pointer_agree(self) -> None:
        plane = _disc(25.0, 30.0, 5.0) | _disc(41.0, 28.0, 4.0)
        border = Border(2.0, 3.0)
        roi = _roi(plane, border)
        x0, y0, band = rz.mask_border_pixels(roi.shape.mask, border)
        drawn = np.zeros(SHAPE, dtype=bool)
        drawn[y0:y0 + band.shape[0], x0:x0 + band.shape[1]] |= band
        np.testing.assert_array_equal(drawn, rz.rasterize_reference(roi, SHAPE, IDENTITY))
        flat = rz.reference_flat_indices(roi, SHAPE, IDENTITY, default_border=border)
        np.testing.assert_array_equal(flat, np.flatnonzero(drawn))
        pointed = rz.region_contains(roi, "border", border, XX.ravel(), YY.ravel()).reshape(SHAPE)
        np.testing.assert_array_equal(pointed, drawn)

    def test_a_partial_patch_equals_the_cut_of_the_whole_plane(self) -> None:
        plane = _disc(25.0, 30.0, 5.0) | _disc(41.0, 28.0, 4.0)
        roi = _roi(plane, Border(2.0, 3.0))
        whole = rz.rasterize_reference(roi, SHAPE, IDENTITY)
        for origin, patch in (((10, 10), (30, 40)), ((0, 0), (25, 30))):
            crop = whole[origin[1]:origin[1] + patch[0], origin[0]:origin[0] + patch[1]]
            np.testing.assert_array_equal(rz.rasterize_reference_for_patch(roi, origin, patch, IDENTITY), crop)

    def test_the_band_is_cached_so_a_second_pass_computes_nothing(self) -> None:
        plane = _disc(25.0, 30.0, 5.0) | _disc(41.0, 28.0, 4.0)
        roi = _roi(plane, Border(2.0, 3.0))
        rz._mask_band.cache_clear()
        rz.reference_flat_indices(roi, SHAPE, IDENTITY)
        misses = rz._mask_band.cache_info().misses
        rz.reference_flat_indices(roi, SHAPE, IDENTITY)
        self.assertEqual(rz._mask_band.cache_info().misses, misses)


class CellHashTests(unittest.TestCase):
    """A many-blob map's rim changed (each blob has its own), so such a ROI's stored cells must go stale; nothing else's may."""

    DEFAULT = Border(4.0, 4.0)

    def test_only_a_many_blob_map_with_a_rim_carries_the_marker(self) -> None:
        one = _disc(25.0, 30.0, 5.0)
        two = one | _disc(45.0, 30.0, 5.0)
        for plane, border, marked in ((two, Border(2.0, 3.0), True), (two, None, True), (two, NO_BORDER, False), (one, Border(2.0, 3.0), False)):
            roi = _roi(plane, border)
            now = fr.roi_row_digests(fr.build_roi_set([roi], self.DEFAULT))[0]
            self.assertEqual(now != self._without_marker(roi), marked, f"blobs {blob_count(plane)}, border {border}")

    def _without_marker(self, roi: AreaRoi) -> bytes:
        """The digest the code made before 2026-11-19: with `blob_count` pinned to 1 for the call."""
        original = fr.blob_count
        fr.blob_count = lambda _pixels: 1
        try:
            return fr.roi_row_digests(fr.build_roi_set([roi], self.DEFAULT))[0]
        finally:
            fr.blob_count = original


if __name__ == "__main__":
    unittest.main()
