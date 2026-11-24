"""`RoiToolbox.match_rim_area` and `roi.rim_area.equal_area_thickness`: the rim thickness at which a rim has the area of its sample ROI (2026-11-24).

The offset (gap) of the rim stays, only the thickness changes. Circle and rectangle are exact (checked against their formula and against the pixels the scan would read);
a pixel map gets the nearest pixel count. No Qt event loop, no files.
"""

from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape, Rectangle
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.roi.rim_area import equal_area_thickness
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

HEIGHT, WIDTH = 200, 240
SHAPE = (HEIGHT, WIDTH)
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
YY, XX = np.mgrid[0:HEIGHT, 0:WIDTH]


def _disc(cx: float, cy: float, radius: float) -> np.ndarray:
    return (XX - cx) ** 2 + (YY - cy) ** 2 <= radius**2


def _roi_of(toolbox: RoiToolbox, roi_id: int):
    return next(roi for roi in toolbox.rois() if roi.area_roi_id == roi_id)


def _rim_pixels(toolbox: RoiToolbox, roi_id: int) -> int:
    roi = _roi_of(toolbox, roi_id)
    return int(rz.rasterize_reference(roi, SHAPE, IDENTITY, default_border=toolbox.detection_settings().default_border()).sum())


def _sample_pixels(toolbox: RoiToolbox, roi_id: int) -> int:
    return int(rz.rasterize_sample(_roi_of(toolbox, roi_id), SHAPE, IDENTITY).sum())


class EqualAreaThicknessTest(unittest.TestCase):
    def test_circle_is_exact(self) -> None:
        for diameter, gap in ((20.0, 4.0), (20.0, 0.0), (12.0, 7.5), (20.0, -4.0)):
            radius = diameter / 2.0
            t = equal_area_thickness(Circle(diameter), gap)
            inner = max(radius + gap, 0.0)
            ring = math.pi * ((inner + t) ** 2 - inner**2)
            self.assertAlmostEqual(ring, math.pi * radius**2, places=9, msg=(diameter, gap))

    def test_circle_known_value(self) -> None:
        """No gap: the ring from R to R*sqrt(2), thickness R (sqrt(2) - 1)."""
        self.assertAlmostEqual(equal_area_thickness(Circle(20.0), 0.0), 10.0 * (math.sqrt(2.0) - 1.0), places=12)

    def test_rectangle_is_exact(self) -> None:
        for width, height, gap in ((24.0, 12.0, 3.0), (24.0, 12.0, 0.0), (10.0, 10.0, 5.0), (24.0, 12.0, -2.0), (24.0, 12.0, -20.0)):
            t = equal_area_thickness(Rectangle(width, height, 30.0), gap)
            area = lambda s: width * height + 2 * (width + height) * s + math.pi * s * s if s >= 0 else max(width + 2 * s, 0) * max(height + 2 * s, 0)  # noqa: E731
            self.assertAlmostEqual(area(gap + t) - area(gap), width * height, places=6, msg=(width, height, gap))
            self.assertGreaterEqual(t, 0.0)

    def test_degenerate_shapes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            equal_area_thickness(Circle(0.0), 2.0)
        with self.assertRaises(ValueError):
            equal_area_thickness(Circle(10.0), float("nan"))


class MatchRimAreaTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.circle = self.toolbox.add_shaped_roi(40.0, 40.0, Circle(24.0), border=Border(3.0, 2.0))
        self.rectangle = self.toolbox.add_shaped_roi(100.0, 40.0, Rectangle(30.0, 16.0, 30.0), border=Border(3.0, 2.0))
        self.blobs = self.toolbox.add_shaped_roi(
            70.0, 120.0, MaskShape(rz.crop_mask(_disc(60.0, 120.0, 7.0) | _disc(85.0, 120.0, 5.0))), border=Border(2.0, 1.0)
        )
        self.line = self.toolbox.add_shaped_roi(160.0, 120.0, MaskShape(rz.crop_mask((YY == 120) & (XX >= 140) & (XX < 180))), border=Border(1.0, 1.0))
        self.bare = self.toolbox.add_shaped_roi(200.0, 40.0, Circle(10.0), border=NO_BORDER)
        self.reference = self.toolbox.add_shaped_roi(200.0, 120.0, Circle(10.0), border=None, role="reference")

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_circle_and_rectangle_rims_get_the_pixel_count_of_their_sample(self) -> None:
        self.toolbox.match_rim_area([self.circle, self.rectangle])
        for roi_id in (self.circle, self.rectangle):
            sample, rim = _sample_pixels(self.toolbox, roi_id), _rim_pixels(self.toolbox, roi_id)
            # the shape's area is exact, the pixel count of a 24 px circle is 2.4 % under it and its rim's 2.6 % over (441 vs 464 on this grid): 6 % pins "about equal"
            self.assertLess(abs(rim - sample) / sample, 0.06, (roi_id, sample, rim))

    def test_the_offset_and_shape_stay(self) -> None:
        shapes = {i: _roi_of(self.toolbox, i).shape for i in (self.circle, self.rectangle, self.blobs)}
        self.toolbox.match_rim_area([self.circle, self.rectangle, self.blobs])
        for roi_id, shape in shapes.items():
            roi = _roi_of(self.toolbox, roi_id)
            self.assertIs(roi.shape, shape)
            self.assertTrue(roi.border.enabled)
        self.assertEqual(_roi_of(self.toolbox, self.circle).border.gap_px, 3.0)
        self.assertEqual(_roi_of(self.toolbox, self.rectangle).border.gap_px, 3.0)
        self.assertEqual(_roi_of(self.toolbox, self.blobs).border.gap_px, 2.0)

    def test_a_pixel_map_gets_the_nearest_pixel_count_and_the_scans_own_pixels(self) -> None:
        """Counted the way the scan reads the rim (`rasterize_reference`), blob by blob: within a few pixels of the map's own count."""
        self.toolbox.match_rim_area([self.blobs])
        sample, rim = _sample_pixels(self.toolbox, self.blobs), _rim_pixels(self.toolbox, self.blobs)
        self.assertLessEqual(abs(rim - sample), max(4, int(0.03 * sample)), (sample, rim))

    def test_a_one_pixel_line_never_gets_an_empty_rim(self) -> None:
        """Its rim count jumps from 0 to 82 pixels for a 40 pixel line (both sides at once): the larger one is taken, not the empty one."""
        self.toolbox.match_rim_area([self.line])
        self.assertGreater(_rim_pixels(self.toolbox, self.line), 0)

    def test_what_has_no_rim_is_left_alone_and_pushes_no_step(self) -> None:
        self.assertFalse(self.toolbox.can_match_rim_area(self.bare))
        self.assertFalse(self.toolbox.can_match_rim_area(self.reference))
        self.assertTrue(self.toolbox.can_match_rim_area(self.circle))
        undo_manager.clear()
        self.toolbox.match_rim_area([self.bare, self.reference])
        self.assertEqual(_roi_of(self.toolbox, self.bare).border, NO_BORDER)
        self.assertEqual(_roi_of(self.toolbox, self.reference).border, None)
        self.assertFalse(undo_manager.can_undo)

    def test_one_undo_step_for_several_rois(self) -> None:
        old = {i: _roi_of(self.toolbox, i).border for i in (self.circle, self.rectangle, self.blobs)}
        self.toolbox.match_rim_area([self.circle, self.rectangle, self.blobs])
        self.assertNotEqual(_roi_of(self.toolbox, self.circle).border, old[self.circle])
        undo_manager.undo()
        for roi_id, border in old.items():
            self.assertEqual(_roi_of(self.toolbox, roi_id).border, border)

    def test_running_it_twice_changes_nothing(self) -> None:
        self.toolbox.match_rim_area([self.circle, self.rectangle, self.blobs])
        once = {i: _roi_of(self.toolbox, i).border for i in (self.circle, self.rectangle, self.blobs)}
        self.toolbox.match_rim_area([self.circle, self.rectangle, self.blobs])
        for roi_id, border in once.items():
            self.assertEqual(_roi_of(self.toolbox, roi_id).border, border)

    def test_unknown_id_is_an_error(self) -> None:
        with self.assertRaises(KeyError):
            self.toolbox.match_rim_area([999])


if __name__ == "__main__":
    unittest.main()
