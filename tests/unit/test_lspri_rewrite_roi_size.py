"""An ROI's size is one number, `sample_diameter_px`: what the overlay draws,
what `resize_roi` edits, and what analysis rasterizes must all agree.

Regression for a bug found 2026-10-06: ROIs stored both a radius and a
diameter; the overlay preferred the diameter, analysis read the radius, and
`resize_roi` wrote only the radius - so after a resize the drawn circle and
the measured circle differed."""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.image_tools.chromatic.affine import identity_affine_matrix
from lspr_imaging_app.roi import RoiToolbox
from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
from lspr_imaging_app.roi.rasterize import (
    effective_reference_diameters,
    rasterize_reference,
    rasterize_sample,
    transformed_circle_points,
)

_IMAGE_SHAPE = (100, 100)


def _equivalent_radius(mask: np.ndarray) -> float:
    return float(np.sqrt(mask.sum() / np.pi))


class RoiSizeSingleSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.toolbox = RoiToolbox()
        self.affine = identity_affine_matrix()

    def test_add_roi_default_uses_a_diameter(self) -> None:
        roi_id = self.toolbox.add_roi(50.0, 50.0)
        self.assertEqual(self.toolbox.roi_by_id(roi_id).sample_diameter_px, 20.0)

    def test_resize_changes_drawn_and_measured_circle_together(self) -> None:
        roi_id = self.toolbox.add_roi(50.0, 50.0, sample_diameter_px=20.0)
        self.toolbox.resize_roi(roi_id, sample_diameter_px=40.0)
        roi = self.toolbox.roi_by_id(roi_id)

        theta = np.linspace(0.0, 2.0 * np.pi, 64)
        xs, ys = transformed_circle_points((50.0, 50.0), roi.sample_diameter_px, self.affine, theta)
        drawn_radius = float(np.max(np.hypot(xs - 50.0, ys - 50.0)))
        measured_radius = _equivalent_radius(rasterize_sample(roi, _IMAGE_SHAPE, self.affine))

        self.assertAlmostEqual(drawn_radius, 20.0, places=6)
        self.assertAlmostEqual(measured_radius, 20.0, delta=0.6)  # pixel-grid rounding

    def test_resize_is_undoable(self) -> None:
        from lspr_imaging_app.undo import undo_manager

        roi_id = self.toolbox.add_roi(50.0, 50.0, sample_diameter_px=20.0)
        self.toolbox.resize_roi(roi_id, sample_diameter_px=40.0)
        undo_manager.undo()
        self.assertEqual(self.toolbox.roi_by_id(roi_id).sample_diameter_px, 20.0)

    def test_reference_ring_defaults_come_from_settings_diameters(self) -> None:
        settings = AreaRoiDetectionSettings(reference_inner_diameter_px=30.0, reference_outer_diameter_px=44.0)
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_diameter_px=20.0)
        self.assertEqual(
            effective_reference_diameters(roi, settings.reference_inner_diameter_px, settings.reference_outer_diameter_px),
            (30.0, 44.0),
        )
        ring = rasterize_reference(
            roi, _IMAGE_SHAPE, self.affine,
            default_inner_diameter_px=30.0, default_outer_diameter_px=44.0,
        )
        rows, cols = np.nonzero(ring)
        distance = np.hypot(cols - 50.0, rows - 50.0)
        self.assertLessEqual(distance.max(), 22.0)
        self.assertGreaterEqual(distance.min(), 15.0)


if __name__ == "__main__":
    unittest.main()
