"""ROI overlay outlines (`roi/overlay_geometry.py`) and the bulk centre lookup
(`RoiToolbox.display_positions`).

Regression for a bug found 2026-10-07: the Image panel passed the already
transformed centre from `display_position()` into `transformed_circle_points`,
which applies the chromatic affine again, so at a non-reference wavelength the
drawn circle sat off the measured region by about the affine's translation
(1.9 px for a 1.2 / -0.8 px shift on a 20 px ROI). Tests below used the
identity affine only, which cannot see that."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.image_tools.chromatic.affine import identity_affine_matrix
from lspr_imaging_app.roi import RoiToolbox
from lspr_imaging_app.roi.model import AreaRoiDetectionSettings
from lspr_imaging_app.roi.overlay_geometry import DEFAULT_CIRCLE_POINTS, circle_outlines
from lspr_imaging_app.roi.rasterize import rasterize_sample, transformed_circle_points

# A plausible chromatic affine at a non-reference wavelength: about a pixel of
# shift, 0.05 % scale and rotation.
_AFFINE = np.array([[1.0005, 0.0004, 1.2], [-0.0004, 1.0005, -0.8]])
_FRAME = (1, 540.0)


def _split_on_nan(values: np.ndarray) -> list[np.ndarray]:
    groups, current = [], []
    for value in values:
        if np.isnan(value):
            groups.append(np.asarray(current))
            current = []
        else:
            current.append(value)
    groups.append(np.asarray(current))
    return groups


class CircleOutlinesTest(unittest.TestCase):
    def test_identity_affine_matches_the_old_per_roi_circles(self) -> None:
        centers = np.array([[50.0, 40.0], [120.5, 33.25]])
        diameters = np.array([20.0, 14.0])
        xs, ys = circle_outlines(centers, diameters, identity_affine_matrix())

        theta = np.linspace(0.0, 2.0 * np.pi, DEFAULT_CIRCLE_POINTS, endpoint=True)
        for index, (circle_x, circle_y) in enumerate(zip(_split_on_nan(xs), _split_on_nan(ys), strict=True)):
            old_x, old_y = transformed_circle_points(
                tuple(centers[index]), diameters[index], identity_affine_matrix(), theta
            )
            np.testing.assert_allclose(circle_x, old_x, atol=1e-12)
            np.testing.assert_allclose(circle_y, old_y, atol=1e-12)

    def test_circles_are_separated_by_one_nan_and_none_trails(self) -> None:
        xs, ys = circle_outlines(np.zeros((3, 2)), np.full(3, 10.0), identity_affine_matrix())
        self.assertEqual(xs.size, 3 * DEFAULT_CIRCLE_POINTS + 2)
        self.assertEqual(int(np.isnan(xs).sum()), 2)
        np.testing.assert_array_equal(np.isnan(xs), np.isnan(ys))
        self.assertFalse(np.isnan(xs[-1]))

    def test_no_circles_gives_empty_arrays(self) -> None:
        xs, ys = circle_outlines(np.empty((0, 2)), np.empty(0), _AFFINE)
        self.assertEqual((xs.size, ys.size), (0, 0))

    def test_outline_is_the_measured_circle_not_the_affine_applied_twice(self) -> None:
        """At a non-reference wavelength the outline must be the reference-frame
        circle pushed through the affine once - the region `rasterize_sample`
        measures."""
        reference_center = (700.0, 500.0)
        toolbox = RoiToolbox()
        roi_id = toolbox.add_roi(*reference_center, sample_diameter_px=20.0)
        display = toolbox.display_positions(_FRAME, _AFFINE)

        xs, ys = circle_outlines(display, np.array([20.0]), _AFFINE)

        theta = np.linspace(0.0, 2.0 * np.pi, DEFAULT_CIRCLE_POINTS, endpoint=True)
        measured_x, measured_y = transformed_circle_points(reference_center, 20.0, _AFFINE, theta)
        np.testing.assert_allclose(xs, measured_x, atol=1e-9)
        np.testing.assert_allclose(ys, measured_y, atol=1e-9)

        # ... and it is centred on the measured pixels, to within a pixel.
        mask = rasterize_sample(toolbox.roi_by_id(roi_id), (1000, 1400), _AFFINE)
        rows, cols = np.nonzero(mask)
        self.assertAlmostEqual(float(cols.mean()), float(xs[:-1].mean()), delta=0.6)
        self.assertAlmostEqual(float(rows.mean()), float(ys[:-1].mean()), delta=0.6)

    def test_the_old_double_application_was_measurably_off(self) -> None:
        """Documents the size of the bug this replaced (quantified, not asserted
        as a goal): passing the transformed centre to `transformed_circle_points`
        moves the circle by about the affine's translation."""
        reference_center = (700.0, 500.0)
        display = np.asarray(
            [(reference_center[0] * 1.0005 + reference_center[1] * 0.0004 + 1.2,
              -reference_center[0] * 0.0004 + reference_center[1] * 1.0005 - 0.8)]
        )
        theta = np.linspace(0.0, 2.0 * np.pi, DEFAULT_CIRCLE_POINTS, endpoint=True)
        old_x, old_y = transformed_circle_points(tuple(display[0]), 20.0, _AFFINE, theta)
        new_x, new_y = circle_outlines(display, np.array([20.0]), _AFFINE)
        offset = float(np.hypot(old_x.mean() - new_x.mean(), old_y.mean() - new_y.mean()))
        self.assertGreater(offset, 1.5)


class DisplayPositionsTest(unittest.TestCase):
    def _toolbox_with_a_nudge(self) -> RoiToolbox:
        toolbox = RoiToolbox()
        for x, y in ((10.0, 20.0), (300.0, 40.0), (55.5, 600.0)):
            toolbox.add_roi(x, y)
        rois = list(toolbox.rois())
        rois[1] = replace(rois[1], per_wavelength={_FRAME: (301.5, 41.5)})
        toolbox.restore_state(AreaRoiDetectionSettings(), tuple(rois), (), ())
        return toolbox

    def test_bulk_lookup_equals_the_single_lookup_for_every_roi(self) -> None:
        toolbox = self._toolbox_with_a_nudge()
        bulk = toolbox.display_positions(_FRAME, _AFFINE)
        for index, roi in enumerate(toolbox.rois()):
            single = toolbox.display_position(roi.area_roi_id, _FRAME, _AFFINE)
            np.testing.assert_allclose(bulk[index], single, atol=1e-12)

    def test_a_nudge_wins_for_its_wavelength_only(self) -> None:
        toolbox = self._toolbox_with_a_nudge()
        np.testing.assert_allclose(toolbox.display_positions(_FRAME, _AFFINE)[1], (301.5, 41.5))
        other = toolbox.display_positions((1, 560.0), _AFFINE)[1]
        self.assertFalse(np.allclose(other, (301.5, 41.5)))

    def test_no_rois_gives_an_empty_array(self) -> None:
        self.assertEqual(RoiToolbox().display_positions(_FRAME, _AFFINE).shape, (0, 2))


if __name__ == "__main__":
    unittest.main()
