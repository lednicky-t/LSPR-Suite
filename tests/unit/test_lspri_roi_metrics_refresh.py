"""Regression test: refresh_roi_metrics (processing/roi_detection.py) was a
no-op stub - `return rois` unconditionally - even though the GUI runs a
background worker, shows "Refreshing ROI metrics...", and treats the
(unchanged) result as authoritative (gui/roi_table_controller.py's
_on_roi_metrics_ready). Users were told metrics were refreshed but a ROI's
`score` never actually updated after the image, mask, or the ROI's own
position/size changed.

refresh_roi_metrics should recompute each ROI's contrast score at its
current position and radius (without moving it - that's detect_rois'/
_refine_detected_roi's job, not a metrics refresh).
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiDetectionSettings
from lspr_imaging_app.processing.roi_detection import refresh_roi_metrics


def _bright_disk_image(size: int = 80, center: tuple[float, float] = (40.0, 40.0), radius: float = 8.0) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    distance = np.hypot(xx - center[0], yy - center[1])
    image = np.full((size, size), 100.0, dtype=np.float32)
    image[distance <= radius] = 900.0
    return image


class TestRefreshRoiMetrics(unittest.TestCase):
    def test_empty_inputs_are_still_a_no_op(self) -> None:
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)
        self.assertEqual(refresh_roi_metrics(np.zeros((0, 0)), settings, []), [])
        roi = AreaRoi(area_roi_id=1, center_x=5.0, center_y=5.0, sample_radius_px=8.0)
        self.assertEqual(refresh_roi_metrics(np.zeros((0, 0)), settings, [roi]), [roi])

    def test_roi_sitting_on_a_bright_disk_gets_a_high_positive_score_in_bright_mode(self) -> None:
        image = _bright_disk_image()
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)
        roi = AreaRoi(area_roi_id=1, center_x=40.0, center_y=40.0, sample_radius_px=8.0, score=0.0)

        refreshed = refresh_roi_metrics(image, settings, [roi])

        self.assertEqual(len(refreshed), 1)
        self.assertGreater(refreshed[0].score, 100.0)
        # Refreshing must not move the ROI or change its radius - that's
        # detect_rois'/refine's job, not a metrics refresh.
        self.assertEqual(refreshed[0].center_x, 40.0)
        self.assertEqual(refreshed[0].center_y, 40.0)
        self.assertEqual(refreshed[0].sample_radius_px, 8.0)
        self.assertEqual(refreshed[0].area_roi_id, 1)

    def test_roi_sitting_on_flat_background_gets_a_near_zero_score(self) -> None:
        image = _bright_disk_image()
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)
        roi = AreaRoi(area_roi_id=1, center_x=70.0, center_y=70.0, sample_radius_px=8.0, score=999.0)

        refreshed = refresh_roi_metrics(image, settings, [roi])

        self.assertAlmostEqual(refreshed[0].score, 0.0, places=3)

    def test_score_updates_when_the_roi_is_moved_off_the_disk_between_refreshes(self) -> None:
        # This is the concrete bug scenario: a user drags an ROI off its
        # bright feature, then asks for a metrics refresh - the Score column
        # must reflect the new (bad) position, not the stale old one.
        image = _bright_disk_image()
        settings = AreaRoiDetectionSettings(mode="bright", sample_radius_px=8.0)
        on_disk = AreaRoi(area_roi_id=1, center_x=40.0, center_y=40.0, sample_radius_px=8.0)
        on_disk_score = refresh_roi_metrics(image, settings, [on_disk])[0].score

        moved_off = AreaRoi(area_roi_id=1, center_x=70.0, center_y=70.0, sample_radius_px=8.0, score=on_disk_score)
        moved_off_score = refresh_roi_metrics(image, settings, [moved_off])[0].score

        self.assertGreater(on_disk_score, moved_off_score + 50.0)

    def test_dark_mode_scores_a_dark_spot_positively(self) -> None:
        image = 1000.0 - _bright_disk_image()  # invert: disk is now dark on a bright field
        settings = AreaRoiDetectionSettings(mode="dark", sample_radius_px=8.0)
        roi = AreaRoi(area_roi_id=1, center_x=40.0, center_y=40.0, sample_radius_px=8.0)

        refreshed = refresh_roi_metrics(image, settings, [roi])

        self.assertGreater(refreshed[0].score, 100.0)


if __name__ == "__main__":
    unittest.main()
