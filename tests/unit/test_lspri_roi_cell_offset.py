"""A circle or rectangle is drawn around the pixels it measures (the half-pixel convention, `roi/overlay_geometry.CELL_OFFSET`).

Two coordinate systems meet at a ROI: the scan, the detectors and the stored centre use **pixel-index coordinates** (pixel ``(row, col)`` is the point
``(col, row)``), the canvas uses **view coordinates** (the pixel covers ``[col, col + 1)``, its middle is ``col + 0.5``). A ROI drawn around its stored
centre as it stands is half a pixel up and to the left of the pixels it measures; drawn around ``centre + CELL_OFFSET`` it is on them (maintainer asked for
the fix 2026-11-18; before, the observation was in `docs/roi_shapes_and_linking_plan_2026-11-18.md`, 2.4).

The check is the one that matters to a person looking at the image: the middle of the drawn outline, against the middle of the pixels the scan reads
(`rasterize_sample`, the code the scan's golden pins check), both in view coordinates. Averaged over many ROIs the offset is the convention, not noise.
The same ROIs drawn the old way are shown to be half a pixel off, so the test would fail if the shift were dropped (or doubled).

No Qt, no files.
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
    from lspr_imaging_app.domain.roi_model import AreaRoi
    from lspr_imaging_app.domain.roi_shapes import Circle, Rectangle
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.roi.overlay_geometry import CELL_OFFSET, circle_points, rectangle_points
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])
ROTATED = np.array([[0.99819, -0.08733, 3.7], [0.08733, 0.99819, -2.9]])
FRAMES = {"identity": IDENTITY, "chromatic": CHROMATIC, "rotated": ROTATED}
IMAGE = (400, 500)
CORNER_VERTICES = 11  # `rectangle_points` writes 11 vertices per corner for 48 points (all alike for a sharp rectangle)


def _centres(make_roi, affine, *, count: int = 150, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """For ``count`` ROIs at random places: the middle of the pixels the scan reads, in view coordinates, and the display centre in pixel-index coordinates."""
    rng = np.random.default_rng(seed)
    measured, display = [], []
    for roi_id in range(1, count + 1):
        x, y = rng.uniform(60.0, 440.0), rng.uniform(60.0, 340.0)
        roi = make_roi(roi_id, float(x), float(y))
        rows, cols = np.nonzero(rz.rasterize_sample(roi, IMAGE, affine))
        measured.append((cols.mean() + CELL_OFFSET, rows.mean() + CELL_OFFSET))  # pixel index -> the middle of its cell on the canvas
        display.append(affine[:, :2] @ np.array([x, y]) + affine[:, 2])
    return np.array(measured), np.array(display)


def _circle_middle(diameter: float, display: np.ndarray, affine) -> np.ndarray:
    xs, ys = circle_points(display, np.full(len(display), diameter), affine)
    return np.column_stack((xs[:, :-1].mean(axis=1), ys[:, :-1].mean(axis=1)))  # the last vertex repeats the first


def _rectangle_middle(width: float, height: float, angle: float, display: np.ndarray, affine) -> np.ndarray:
    n = len(display)
    xs, ys = rectangle_points(display, np.full(n, width), np.full(n, height), np.full(n, angle), affine)
    corners = [k * CORNER_VERTICES for k in range(4)]  # the row is padded with its first vertex, which a plain mean would lean towards
    return np.column_stack((xs[:, corners].mean(axis=1), ys[:, corners].mean(axis=1)))


class DrawnAroundTheMeasuredPixelsTest(unittest.TestCase):
    def assert_on_the_pixels(self, measured: np.ndarray, middle_of_outline_old_way: np.ndarray, middle_of_outline_new_way: np.ndarray, label: str) -> None:
        new = (middle_of_outline_new_way - measured).mean(axis=0)
        old = (middle_of_outline_old_way - measured).mean(axis=0)
        # averaged over the ROIs the pixel grid's rounding cancels; what is left is the convention
        np.testing.assert_allclose(new, [0.0, 0.0], atol=0.02, err_msg=f"{label}: drawn around the shifted centre")
        np.testing.assert_allclose(old, [-CELL_OFFSET, -CELL_OFFSET], atol=0.03, err_msg=f"{label}: the unshifted centre is half a pixel up and left")
        # and no single ROI is further than the grid's own rounding of which pixels count
        self.assertLess(float(np.abs(middle_of_outline_new_way - measured).max()), 0.25, label)

    def test_circles_of_several_sizes_in_every_frame(self) -> None:
        for name, affine in FRAMES.items():
            for diameter in (12.0, 20.0, 40.0):
                measured, display = _centres(lambda i, x, y, d=diameter: AreaRoi(i, x, y, Circle(d)), affine)
                self.assert_on_the_pixels(
                    measured, _circle_middle(diameter, display, affine), _circle_middle(diameter, display + CELL_OFFSET, affine), f"circle {diameter:g} px, {name}"
                )

    def test_a_turned_rectangle_in_every_frame(self) -> None:
        for name, affine in FRAMES.items():
            measured, display = _centres(lambda i, x, y: AreaRoi(i, x, y, Rectangle(30.0, 14.0, 33.0)), affine)
            self.assert_on_the_pixels(
                measured, _rectangle_middle(30.0, 14.0, 33.0, display, affine), _rectangle_middle(30.0, 14.0, 33.0, display + CELL_OFFSET, affine), f"rectangle, {name}"
            )

    def test_the_shift_is_half_a_cell_and_nothing_else(self) -> None:
        """The constant is the geometry of the canvas (an image item draws pixel ``(row, col)`` over ``[col, col + 1)``), not a tuning knob."""
        self.assertEqual(CELL_OFFSET, 0.5)


if __name__ == "__main__":
    unittest.main()
