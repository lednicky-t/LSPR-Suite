"""`image_tools/mask/roi_feature_run.py` (2026-10-29): the ROI feature finder as one job - crops, fits on worker processes, findings.
No Qt, no dataset.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.

The synthetic array is the one of `test_lspri_roi_features.py` (16 dark disks, a shared lobe of varying strength, ROI 5 with a dark
spot on its reference ring, ROI 9 with a bright streak across its sample disk). What is pinned here is what the *job* adds to the method:

- the worker count changes how the fits are scheduled and nothing else (patches and mask identical at 1, 2 and 3 processes);
- the findings table and the mask follow the numbers the user sets (strength, largest feature, the share above which a ROI is left out);
- Cancel stops a fit, with or without worker processes; too few ROIs say so instead of guessing.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask import roi_feature_run as run
    from lspr_imaging_app.image_tools.mask import roi_features as rf
    from lspr_imaging_app.progress import Cancelled
    from tests.unit.test_lspri_roi_features import HALF, RADIUS_FIT, SIZE, _grid_centres, _region_masks, build_image
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def make_crops(img: np.ndarray, count: int = 16) -> list[run.RoiCrop]:
    crops = []
    for k, (cx, cy) in enumerate(_grid_centres()[:count], start=1):
        y0, y1 = max(int(cy) - HALF, 0), min(int(cy) + HALF + 1, SIZE)
        x0, x1 = max(int(cx) - HALF, 0), min(int(cx) + HALF + 1, SIZE)
        sample, ring = _region_masks(cx, cy, (y0, y1, x0, x1))
        crops.append(
            run.RoiCrop(
                roi_id=k, box=(y0, y1, x0, x1), window=img[y0:y1, x0:x1].copy(), sample=sample, ring=ring & ~sample,
                centre_xy=(cx - x0 + 1.5, cy - y0 - 1.0), radius_px=RADIUS_FIT,  # a nominal centre off by a pixel or two, as a real ROI's is
            )
        )
    return crops


class RoiFeatureRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image, _ = build_image()
        cls.crops = make_crops(cls.image)
        cls.patches = run.fit_crops(cls.crops, processes=1)

    def find(self, params: run.FeatureParams = run.FeatureParams()) -> run.FeatureOutcome:
        return run.find_in_crops(self.crops, self.patches, params, (SIZE, SIZE))

    # -- what is found -----------------------------------------------------------------------------------------------------

    def test_the_injected_features_are_the_only_findings(self) -> None:
        outcome = self.find()
        self.assertEqual({f.roi_id for f in outcome.findings}, {5, 9})
        self.assertEqual(outcome.rois_judged, 16)
        by_id = {f.roi_id: f for f in outcome.findings}
        self.assertEqual((by_id[5].dark, by_id[5].bright), (1, 0))  # the dark spot on the ring
        self.assertEqual(by_id[9].dark, 0)  # the streak is bright; no dark twin
        self.assertGreater(by_id[9].bright, 0)
        self.assertTrue(outcome.mask.any())
        self.assertEqual(outcome.mask.shape, (SIZE, SIZE))
        # every masked pixel lies in the window of a ROI that has a finding
        boxes = {c.roi_id: c.box for c in self.crops}
        covered = np.zeros((SIZE, SIZE), dtype=bool)
        for roi_id in (5, 9):
            y0, y1, x0, x1 = boxes[roi_id]
            covered[y0:y1, x0:x1] = True
        self.assertFalse((outcome.mask & ~covered).any())

    def test_a_finding_reports_how_much_of_the_disk_and_ring_the_mask_covers(self) -> None:
        by_id = {f.roi_id: f for f in self.find().findings}
        spot = by_id[5]  # on the ring, not on the disk
        self.assertEqual(spot.sample_share, 0.0)
        self.assertGreater(spot.ring_share, 0.0)
        streak = by_id[9]  # across the disk
        self.assertGreater(streak.sample_share, 0.05)
        self.assertGreaterEqual(streak.masked_px, streak.area_px)  # the mask is the feature grown by the margin

    def test_the_strength_number_decides_what_is_found(self) -> None:
        self.assertEqual(self.find(run.FeatureParams(strength=60.0, extent=60.0)).findings, ())  # nothing stands out that much
        strict = self.find(run.FeatureParams(strength=5.0))
        loose = self.find(run.FeatureParams(strength=4.0, extent=2.5))
        self.assertGreaterEqual(int(loose.mask.sum()), int(strict.mask.sum()))

    def test_the_margin_number_grows_the_mask(self) -> None:
        # (max_share 1: with a wide margin a ROI would exceed the default share and be left out, which is another test)
        sizes = [int(self.find(run.FeatureParams(margin_px=m, max_share=1.0)).mask.sum()) for m in (0, 1, 3)]
        self.assertLess(sizes[0], sizes[1])
        self.assertLess(sizes[1], sizes[2])
        # and the share the mask covers is measured on the grown mask, so a wide margin can push a ROI over the limit
        self.assertTrue(any(f.left_out for f in self.find(run.FeatureParams(margin_px=3)).findings))

    def test_the_largest_feature_number_drops_wide_features(self) -> None:
        narrow = self.find(run.FeatureParams(max_width_px=4.0))
        self.assertLess(int(narrow.mask.sum()), int(self.find().mask.sum()))

    def test_a_roi_that_would_lose_too_much_is_listed_but_left_out_of_the_mask(self) -> None:
        normal = self.find()
        strict = self.find(run.FeatureParams(max_share=0.01))  # almost any feature covers more than 1 % of a disk or ring
        self.assertEqual({f.roi_id for f in strict.findings}, {5, 9})  # still listed ...
        self.assertTrue(all(f.left_out for f in strict.findings))
        self.assertFalse(strict.mask.any())  # ... but not masked
        self.assertTrue(normal.mask.any())
        self.assertFalse(any(f.left_out for f in normal.findings))

    def test_strength_is_never_below_extent_in_the_method_settings(self) -> None:
        settings = run.FeatureParams(strength=3.0, extent=5.0).feature_settings()
        self.assertEqual((settings.z_seed, settings.z_grow), (3.0, 3.0))  # a feature grows from its seed, not from beyond it

    # -- scheduling never changes results --------------------------------------------------------------------------------

    def test_the_worker_count_changes_nothing_but_the_schedule(self) -> None:
        reference = self.find()
        for processes in (2, 3):
            with self.subTest(processes=processes):
                patches = run.fit_crops(self.crops, processes=processes)
                self.assertEqual(list(patches), list(self.patches))  # sorted by ROI id whatever finished first
                for roi_id, patch in patches.items():
                    expected = self.patches[roi_id]
                    np.testing.assert_array_equal(patch.patch, expected.patch)
                    np.testing.assert_array_equal(patch.fill, expected.fill)
                    self.assertEqual((patch.centre, patch.radius_px, patch.amplitude), (expected.centre, expected.radius_px, expected.amplitude))
                outcome = run.find_in_crops(self.crops, patches, run.FeatureParams(), (SIZE, SIZE))
                np.testing.assert_array_equal(outcome.mask, reference.mask)
                self.assertEqual(outcome.findings, reference.findings)

    def test_progress_reaches_one_and_names_the_step(self) -> None:
        reports: list[tuple[float, str]] = []
        run.fit_crops(self.crops, processes=1, progress=lambda fraction, message: reports.append((fraction, message)))
        self.assertEqual(len(reports), 16)
        self.assertEqual(reports[-1][0], 1.0)
        self.assertIn("fitting", reports[-1][1])
        self.assertEqual([f for f, _m in reports], sorted(f for f, _m in reports))

    # -- stopping and refusing -----------------------------------------------------------------------------------------

    def test_cancel_stops_the_fit_in_this_process(self) -> None:
        calls = []

        def cancelled() -> bool:
            calls.append(1)
            return len(calls) > 3  # after three ROIs

        with self.assertRaises(Cancelled):
            run.fit_crops(self.crops, processes=1, cancelled=cancelled)
        self.assertLess(len(calls), len(self.crops))

    def test_cancel_stops_the_fit_on_worker_processes(self) -> None:
        with self.assertRaises(Cancelled):
            run.fit_crops(self.crops, processes=2, cancelled=lambda: True)

    def test_too_few_rois_say_so(self) -> None:
        few = make_crops(self.image, count=rf.MIN_ROIS - 1)
        patches = run.fit_crops(few, processes=1)
        with self.assertRaises(rf.NotEnoughRois):
            run.find_in_crops(few, patches, run.FeatureParams(), (SIZE, SIZE))

    def test_a_crop_with_no_data_is_left_out_of_the_fit(self) -> None:
        crops = make_crops(self.image)
        empty = run.RoiCrop(
            roi_id=99, box=crops[0].box, window=np.full_like(crops[0].window, np.nan), sample=crops[0].sample, ring=crops[0].ring,
            centre_xy=crops[0].centre_xy, radius_px=RADIUS_FIT,
        )
        patches = run.fit_crops(crops + [empty], processes=1)
        self.assertNotIn(99, patches)
        self.assertEqual(len(patches), 16)

    def test_no_crops_is_an_empty_fit(self) -> None:
        self.assertEqual(run.fit_crops([], processes=1), {})
        self.assertEqual(run.fit_crops([]), {})


if __name__ == "__main__":
    unittest.main()
