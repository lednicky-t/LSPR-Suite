"""Golden fingerprint of what the scan does for circle ROIs (phase 2.0 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

Written BEFORE the shape work of phase 2 changed anything, from the code as it was on 2026-11-18. It is the "before" of the before / after
comparison that phase 2 owes (`.claude/rules/numerics.md`): the circle's pixels, cell keys, window boxes and reduced numbers must come out
**exactly** as they did, whatever the ROI model, the border description (gap + thickness) or the pixel-finding code become.

How to read a failure:

* ``KEYS`` changed: the stored cell hash of every circle ROI would change, so every existing session would rescan. Never acceptable
  without a migration decision by the maintainer.
* ``PIXELS`` changed: a circle covers other pixels than before (a stored number could change). Never acceptable for circles.
* ``NUMBERS`` changed: a reduced value, standard deviation or count changed. (Values are compared to 10 significant digits, not bit for
  bit: the plane fit's least squares is not bit-reproducible across platforms. Counts and pixel lists are exact.)
* ``RADII``: the exact float64 ring and sample radii of the circles, made by the code **before** phase 2 (descriptions as inner / outer
  diameters) and reproduced bit for bit by the new gap + thickness description (``Border.from_ring_diameters`` then ``ring_radii``): the proof
  that converting circles to gap + thickness moved nothing.
* ``MASK_SAMPLE``: the sample pixels of a free-form (mask) ROI as the chromatic affine warps it (scipy, nearest neighbour), also made by the
  code before phase 2. **Phase 2.2 (2026-11-18) kept the scipy warp and runs it inside the mask's own box instead of on a full canvas, so this pin did
  not move** (the decision was: no separate "fast lookup", which could disagree with the warp at half-pixel ties). A change here is still a
  **conscious, measured decision** (count the pixels that differ and report it), not a silent one. (A mask ROI's *border* is new in phase 2: a
  distance band from its edge, not pinned here.)
* ``KEYS`` was **re-pinned at phase 2.1** (2026-11-18): the digest of a circle ROI no longer contains the shared default ring diameters (a
  circle's border is already in its radii, so changing the default changes exactly the ROIs that follow it). ``RADII`` is unchanged by that.

When the ROI model changes (phase 2.1), only :func:`golden_rois` may be edited, to build the *same* circles in the new model. The pinned
digests must not change. The ROIs as today's code writes them into a session are also kept in ``tests/data/lspri_golden_rois_session_1_11.json``
(the migration test of phase 2.5 reads them).

No Qt, no files (except that fixture, read by another test), small synthetic images; coordinates and affines are decimal literals, so the
numbers do not depend on a platform's math library.
"""

from __future__ import annotations

import hashlib
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
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape
    from lspr_imaging_app.roi import rasterize as rz
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

HEIGHT, WIDTH = 120, 170
SHAPE = (HEIGHT, WIDTH)
DEFAULT_INNER, DEFAULT_OUTER = 28.0, 36.0  # the shared default ring diameters of the code before phase 2 (a ROI without its own used them)
DEFAULT_BORDER = Border(4.0, 4.0)  # the same ring as gap + thickness around a 20 px sample
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])
ROTATED = np.array([[0.99819, -0.08733, 3.7], [0.08733, 0.99819, -2.9]])  # about 5 degrees, scale ~1.002 (literals: no cos / sin)
AFFINES = (("identity", IDENTITY), ("chromatic", CHROMATIC), ("rotated", ROTATED))
MODES = (fr.EXCLUDE_ALL_SAMPLE_ROIS, "none")
METHODS = ("mean", "median", "trimmed_mean", "plane_fit")
SUBSETS = ((0,), (3,), (2, 4), (8, 6, 0))


def golden_rois() -> list[AreaRoi]:
    """Twelve circle ROIs chosen to be awkward: shared / own / no / zero-thickness / inside-the-sample rings, non-integer diameters,
    windows that leave the image, overlapping discs, an empty sample aperture and one reference ROI. **The only function that may change
    when the ROI model changes** (it must build the same circles)."""

    def roi(i, x, y, d, inner=None, outer=None, ring=None, role="sample") -> AreaRoi:
        # the ring as the code before phase 2 described it (inner / outer diameters, the shared 28 / 36 when not given), written as a
        # border exactly as the old-session reader does (`Border.from_ring_diameters`); a ring that is the shared default follows it
        if ring == "none":
            border = NO_BORDER
        else:
            border = Border.from_ring_diameters(
                d, DEFAULT_INNER if inner is None else inner, DEFAULT_OUTER if outer is None else outer
            )
            if inner is None and outer is None and border == DEFAULT_BORDER:
                border = None
        return AreaRoi(area_roi_id=i, center_x=x, center_y=y, shape=Circle(d), border=border, role=role)

    return [
        roi(1, 40.0, 40.0, 20.0),                    # the shared ring, 28 / 36 (gap 4, thickness 4)
        roi(2, 70.3, 41.7, 20.3, 28.7, 37.1),        # non-integer diameters and its own ring
        roi(3, 95.0, 60.0, 20.0, ring="none"),       # no ring
        roi(4, 100.0, 66.0, 20.0),                   # overlaps ROI 3's disc: exclusion between ROIs
        roi(5, 4.0, 6.0, 20.0),                      # window leaves the image (top-left)
        roi(6, 160.0, 110.0, 17.7, 25.1, 33.3),      # window leaves the image (bottom-right)
        roi(7, 60.0, 90.0, 16.0, role="reference"),  # a reference ROI (one region, no ring)
        roi(8, 30.0, 100.0, 20.0, 0.0, 30.0),        # inner diameter 0: the ring is a disc
        roi(9, 125.0, 30.0, 24.0, 16.0, 30.0),       # the ring starts inside the sample (negative gap)
        roi(10, 20.0, 60.0, 14.0, 30.0, 20.0),       # outer < inner: clamped to zero thickness
        roi(11, 140.0, 80.0, 0.0),                   # empty sample aperture, ring from the defaults
        roi(12, 80.0, 15.0, 20.0),                   # alone
    ]


def golden_mask_rois() -> list[AreaRoi]:
    """A circle ROI and a free-form (mask) ROI with an irregular, off-centre sample; its ring is the shared circular one."""
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    blob = (((yy - 62) ** 2) * 400 + ((xx - 120) ** 2) * 144 <= 144 * 400 * 1.0) | ((yy >= 70) & (yy < 76) & (xx >= 118) & (xx < 134))
    return [
        AreaRoi(area_roi_id=1, center_x=40.0, center_y=40.0, shape=Circle(20.0)),
        AreaRoi(area_roi_id=2, center_x=120.0, center_y=64.0, shape=MaskShape(rz.crop_mask(blob)), border=NO_BORDER),
    ]


def _image() -> tuple[np.ndarray, np.ndarray]:
    image = np.random.default_rng(5).normal(1000.0, 40.0, SHAPE).astype(np.float32)
    image[50:56, 90:100] = np.nan  # pixels with no value (e.g. made by rotation)
    ignore = np.zeros(SHAPE, dtype=bool)
    ignore[38:44, 30:50] = True  # an ignore mask over the first ROI
    return image, ignore


def _feed(digest, *parts) -> None:
    for part in parts:
        if isinstance(part, bytes):
            digest.update(part)
        elif isinstance(part, str):
            digest.update(part.encode())
        else:
            array = np.ascontiguousarray(part)
            digest.update(str(array.dtype).encode() + str(array.shape).encode())
            digest.update(array.tobytes())


def _significant(array: np.ndarray) -> bytes:
    """An array of floats as text with 11 significant digits (NaN as 'nan'): equal on any platform, different for any real change."""
    return ";".join("nan" if not np.isfinite(v) else f"{v:.10e}" for v in np.asarray(array, dtype=np.float64).ravel()).encode()


def keys_digest(rois) -> str:
    """The cell-hash ingredients: the set key, the per-ROI row digests, the local keys (both exclusion modes) and the reach matrix.
    Exact bytes: these decide which stored cells a session finds stale."""
    digest = hashlib.sha256()
    rs = fr.build_roi_set(rois, DEFAULT_BORDER)
    _feed(digest, rs.key, np.array([rs.cx, rs.cy, rs.sample_r, rs.ring_in_r, rs.ring_out_r]), rs.sample_on, rs.ring_on, rs.vectorised, rs.is_ref)
    _feed(digest, *fr.roi_row_digests(rs))
    for mode in MODES:
        _feed(digest, mode, *fr.local_roi_keys(rs, mode))
    _feed(digest, fr.ring_reach_matrix(rs, np.arange(rs.n)))
    return digest.hexdigest()


def pixels_digest(rois) -> str:
    """Where every ROI's pixels are: full and subset layouts for three affines and both exclusion modes, and the ROI window boxes. Exact."""
    digest = hashlib.sha256()
    rs = fr.build_roi_set(rois, DEFAULT_BORDER)
    for name, affine in AFFINES:
        _feed(digest, name, *fr.roi_window_boxes(rs, affine, SHAPE))
        for mode in MODES:
            layout = fr.build_layout(rs, affine, SHAPE, mode)
            _feed(digest, name, mode, layout.sample_roi, layout.sample_flat, layout.ring_roi, layout.ring_flat, layout.sample_count,
                  layout.ring_count, layout.needed_flat)
            for subset in SUBSETS:
                if max(subset) < rs.n:
                    part = fr.build_subset_layout(rs, affine, SHAPE, mode, subset)
                    _feed(digest, name, mode, np.array(subset), part.sample_roi, part.sample_flat, part.ring_roi, part.ring_flat)
    return digest.hexdigest()


def numbers_digest(rois) -> str:
    """The reduced numbers of one frame, every method, with and without an ignore mask: counts exact, values to 10 significant digits."""
    digest = hashlib.sha256()
    rs = fr.build_roi_set(rois, DEFAULT_BORDER)
    image, ignore = _image()
    for name, affine in AFFINES:
        for mode in MODES:
            for ignore_mask in (None, ignore):
                red = fr.reduce_frame(rs, image, ignore_mask, affine, METHODS, exclusion_mode=mode)
                _feed(digest, name, mode, int(ignore_mask is not None), red.n_valid, red.n_nominal, red.n_masked, _significant(red.std))
                for method in METHODS:
                    _feed(digest, method, _significant(red.values[method]))
    return digest.hexdigest()


# Made on 2026-11-18 from the code before phase 2 (see the module docstring). Do not edit to make a test pass.
PINNED = {
    "keys": "f48e83d8f750d0ae7b3e06084c953b24daefd5e2219cdfd15ec7fad8b767c319",  # re-pinned at phase 2.1, see the docstring
    "pixels": "afcebd02aa50ebdf0390bebb1127f43f907ab78412b22909ac4bde566e069204",
    "numbers": "56cfe935914c69688554f6c5d43284a11d916998ddf30b064c4d7ef2b6461fe8",
    "radii": "9d9f60cadbd0dbf6",  # made by the code before phase 2 (sources of commit 24f0179), see the docstring
    "mask_sample": "c944078e14de3399",  # likewise
}


def _short(*arrays) -> str:
    """The first 16 hex digits of a SHA-256 over the arrays (dtype, shape and bytes); the form the pins made by the old code use."""
    digest = hashlib.sha256()
    for array in arrays:
        array = np.ascontiguousarray(array)
        digest.update(str(array.dtype).encode() + str(array.shape).encode() + array.tobytes())
    return digest.hexdigest()[:16]


def radii_digest(rois) -> str:
    """The exact float64 centres and radii of the set (and which parts exist): what the pixel test reads and the cell hash contains."""
    rs = fr.build_roi_set(rois, DEFAULT_BORDER)
    return _short(rs.cx, rs.cy, rs.sample_r, rs.ring_in_r, rs.ring_out_r, rs.ring_on, rs.sample_on, rs.is_ref, rs.vectorised)


def mask_sample_digest(rois) -> str:
    """The sample pixels of every ROI of ``golden_mask_rois()`` for three affines (before any exclusion): the circle's and the mask's."""
    rs = fr.build_roi_set(rois, DEFAULT_BORDER)
    parts = []
    for _name, affine in AFFINES:
        s_roi, s_flat, _r_roi, _r_flat = fr.locate_pixels(rs, affine, SHAPE)
        parts += [s_flat[s_roi == 0], s_flat[s_roi == 1]]
    return _short(*parts)


class CircleGoldenTests(unittest.TestCase):
    def test_the_golden_set_covers_what_it_claims(self) -> None:
        """The fingerprint is only worth something if the set really holds the awkward cases."""
        rs = fr.build_roi_set(golden_rois(), DEFAULT_BORDER)
        has_ring = rs.ring_on & ~rs.is_ref
        self.assertTrue(rs.vectorised.all())
        self.assertTrue((rs.ring_in_r[has_ring] < rs.sample_r[has_ring]).any(), "a ring that starts inside its sample")
        self.assertTrue((rs.ring_out_r[has_ring] == rs.ring_in_r[has_ring]).any(), "a ring of zero thickness")
        self.assertTrue((rs.ring_in_r[has_ring] == 0.0).any(), "a ring with inner diameter 0")
        self.assertTrue((~rs.ring_on & ~rs.is_ref).any(), "a ROI without a ring")
        self.assertTrue((~rs.sample_on).any(), "a ROI with an empty sample aperture")
        self.assertTrue(rs.is_ref.any(), "a reference ROI")
        self.assertTrue(((rs.sample_r * 2) % 1.0 != 0.0).any(), "a non-integer diameter")
        layout = fr.build_layout(rs, CHROMATIC, SHAPE)
        self.assertTrue((layout.sample_count > 0).sum() >= 9 and (layout.ring_count > 0).sum() >= 8)
        self.assertLess(layout.ring_count[3], int(np.pi * (rs.ring_out_r[3] ** 2 - rs.ring_in_r[3] ** 2)) - 20, "neighbour exclusion cuts a ring")
        y0, y1, x0, x1, _ = fr.roi_window_boxes(rs, IDENTITY, SHAPE, 0.0)
        self.assertTrue(((x1 - x0) < 2 * 18).any(), "a window clipped by the image edge")

    def test_cell_keys_are_what_they_were(self) -> None:
        self.assertEqual(keys_digest(golden_rois()), PINNED["keys"], "circle cell keys changed: every existing session would rescan")

    def test_circle_pixels_are_what_they_were(self) -> None:
        self.assertEqual(pixels_digest(golden_rois()), PINNED["pixels"], "a circle now covers other pixels than before")

    def test_circle_numbers_are_what_they_were(self) -> None:
        self.assertEqual(numbers_digest(golden_rois()), PINNED["numbers"], "a reduced circle number changed")

    def test_circle_radii_are_bit_for_bit_what_the_diameter_description_gave(self) -> None:
        self.assertEqual(radii_digest(golden_rois()), PINNED["radii"], "a circle's sample or ring radius differs in the last bits from the old code's")

    def test_the_free_form_roi_sample_pixels_are_as_they_were_after_phase_2_2(self) -> None:
        self.assertEqual(mask_sample_digest(golden_mask_rois()), PINNED["mask_sample"], "today's free-form (mask) ROI sample pixels changed")


if __name__ == "__main__":
    unittest.main()
