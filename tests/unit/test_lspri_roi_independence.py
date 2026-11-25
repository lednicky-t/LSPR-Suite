"""Every ROI is reduced on its own: pixels are never pooled across ROIs (rule in `apps/LSPRi/eva/CLAUDE.md`, "Non-negotiable invariants").

Why this test exists: the original app once pooled the sample pixels of several ROIs before forming the sample / reference ratio, which is not the
same number as the average of each ROI's own ratio. Its regression test (`test_lspri_roi_absorbance_multi_roi_isolation.py`) went with the old engine
in the 2026-10-29 cut-over, so this one guards the scan's reduction (`analysis/frame_reduce.reduce_frame`) instead.

Two ROIs sit close together, so the second ROI's sample disc overlaps the first one's reference ring. The second is made much brighter than the first.
Pinned: (1) adding a ROI, or changing its brightness, does not change the other ROI's numbers (sample mean, std, pixel counts, reference mean);
(2) each ROI's sample mean is its own, not a mix. No Qt, no files.
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
    from lspr_imaging_app.analysis.frame_reduce import REFERENCE, SAMPLE, build_roi_set, reduce_frame
    from lspr_imaging_app.domain.roi_model import AreaRoi
    from lspr_imaging_app.domain.roi_shapes import Border, Circle
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

H, W = 70, 110
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
BORDER = Border.from_ring_diameters(10.0, 14.0, 24.0)  # sample diameter, ring inner and outer diameter
BASE = 1000.0

# ROI 1 at x=30 (sample radius 5: x 25..35, ring radius 7..12: x up to 42); ROI 2 at x=42 (sample disc x 37..47), inside ROI 1's ring.
ROI_A = AreaRoi(1, 30.0, 35.0, Circle(10.0), border=BORDER)
ROI_B = AreaRoi(2, 42.0, 35.0, Circle(10.0), border=BORDER)


def _image(b_level: float) -> np.ndarray:
    """A flat field with a little fixed noise; ROI B's sample disc is set to ``b_level``."""
    image = BASE + np.random.default_rng(7).normal(0.0, 5.0, (H, W))
    yy, xx = np.mgrid[:H, :W]
    # radius 4.5 < the sample radius 5: the bright pixels all lie inside B's sample disc, which is what the reference exclusion removes
    image[(yy - 35.0) ** 2 + (xx - 42.0) ** 2 <= 4.5**2] = b_level + np.random.default_rng(8).normal(0.0, 5.0, 1)
    return image.astype(np.float32)


def _reduce(rois, b_level: float):
    return reduce_frame(build_roi_set(rois, BORDER), _image(b_level), None, IDENTITY, ("mean",))


class RoiIndependenceTests(unittest.TestCase):
    def test_a_rois_sample_is_the_same_alone_and_next_to_a_bright_neighbour(self) -> None:
        # Only the sample side: a neighbour does change the reference ring on purpose (its sample disc is cut out of the ring).
        alone = _reduce([ROI_A], 5000.0)
        pair = _reduce([ROI_A, ROI_B], 5000.0)
        self.assertAlmostEqual(float(alone.values["mean"][0, SAMPLE]), float(pair.values["mean"][0, SAMPLE]), delta=1e-9 * BASE)
        self.assertAlmostEqual(float(alone.std[0, SAMPLE]), float(pair.std[0, SAMPLE]), delta=1e-9 * BASE)
        self.assertEqual(int(alone.n_valid[0, SAMPLE]), int(pair.n_valid[0, SAMPLE]))
        # and the bright neighbour is why the alone reference is contaminated while the paired one is not
        self.assertGreater(float(alone.values["mean"][0, REFERENCE]) - float(pair.values["mean"][0, REFERENCE]), 100.0)

    def test_changing_the_neighbours_brightness_does_not_move_this_rois_numbers(self) -> None:
        dim = _reduce([ROI_A, ROI_B], 1500.0)
        bright = _reduce([ROI_A, ROI_B], 9000.0)
        for side in (SAMPLE, REFERENCE):
            self.assertAlmostEqual(float(dim.values["mean"][0, side]), float(bright.values["mean"][0, side]), delta=1e-9 * BASE)
        # and the neighbour's own sample did change, so the test can tell the two apart
        self.assertGreater(float(bright.values["mean"][1, SAMPLE]) - float(dim.values["mean"][1, SAMPLE]), 5000.0)

    def test_each_rois_sample_mean_is_its_own_not_a_mix(self) -> None:
        pair = _reduce([ROI_A, ROI_B], 5000.0)
        self.assertAlmostEqual(float(pair.values["mean"][0, SAMPLE]), BASE, delta=10.0)
        self.assertGreater(float(pair.values["mean"][1, SAMPLE]), 3500.0)  # ~80 % of B's sample pixels are bright
        # the reference of ROI A is the plain background: the neighbour's bright disc, inside A's ring, is left out of it
        self.assertAlmostEqual(float(pair.values["mean"][0, REFERENCE]), BASE, delta=10.0)
        # a pooled sample mean would sit between the two; make sure neither ROI reports it
        pooled = (float(pair.values["mean"][0, SAMPLE]) * pair.n_valid[0, SAMPLE] + float(pair.values["mean"][1, SAMPLE]) * pair.n_valid[1, SAMPLE]) / (
            pair.n_valid[0, SAMPLE] + pair.n_valid[1, SAMPLE]
        )
        for r in (0, 1):
            self.assertGreater(abs(float(pair.values["mean"][r, SAMPLE]) - pooled), 1000.0)


if __name__ == "__main__":
    unittest.main()
