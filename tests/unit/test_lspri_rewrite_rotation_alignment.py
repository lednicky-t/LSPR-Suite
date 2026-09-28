"""Pure-logic tests for the rotate-by-line angle math.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring.

The property that matters here is the *direction*: the arithmetic (atan2,
folding to +-90) is trivial, but whether "+tilt" or "-tilt" gets added to
``rotation_angle_deg`` depends on scipy's rotate convention, on y pointing
down, and on the rotate -> flip -> crop order - and a wrong sign is
invisible in code review and plainly wrong (double the tilt) on real data.
So the sign tests do not check numbers copied from the implementation: they
draw a line of known tilt, push it through the *real*
``apply_spatial_preprocessing`` with the returned correction, and measure the
result.
"""

from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.geometry.alignment import (
        MIN_SEGMENT_PX,
        fold_to_half_turn,
        line_tilt_deg,
        rotation_correction_deg,
    )
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import apply_spatial_preprocessing
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402


def _line_image(tilt_deg: float, size: int = 400) -> np.ndarray:
    """A one-pixel-wide bright line through the centre; tilt positive = slopes
    downward to the right (row grows with column)."""
    img = np.zeros((size, size), np.float32)
    c = size / 2
    t = np.linspace(-150.0, 150.0, 3000)
    x = c + t * math.cos(math.radians(tilt_deg))
    y = c + t * math.sin(math.radians(tilt_deg))
    img[np.round(y).astype(int), np.round(x).astype(int)] = 1.0
    return img


def _measured_tilt(img: np.ndarray) -> float:
    ys, xs = np.nonzero(img > 0.3)
    return math.degrees(math.atan(np.polyfit(xs, ys, 1)[0]))


class FoldTest(unittest.TestCase):
    def test_folds_into_the_smaller_of_the_two_solutions(self) -> None:
        for raw, expected in ((10.0, 10.0), (-10.0, -10.0), (170.0, -10.0), (-170.0, 10.0), (180.0, 0.0), (0.0, 0.0)):
            self.assertAlmostEqual(fold_to_half_turn(raw), expected, places=9, msg=f"raw={raw}")

    def test_exactly_vertical_is_deterministic(self) -> None:
        self.assertAlmostEqual(fold_to_half_turn(90.0), 90.0)
        self.assertAlmostEqual(fold_to_half_turn(-90.0), 90.0)

    def test_result_is_always_within_half_turn(self) -> None:
        for raw in np.linspace(-720.0, 720.0, 601):
            folded = fold_to_half_turn(float(raw))
            self.assertGreater(folded, -90.0 - 1e-9)
            self.assertLessEqual(folded, 90.0 + 1e-9)


class TiltTest(unittest.TestCase):
    def test_point_order_does_not_matter(self) -> None:
        """p1 -> p2 and p2 -> p1 describe the same line - the two solutions
        the maintainer described; the smaller one must win either way."""
        a, b = (100.0, 200.0), (300.0, 210.0)
        self.assertAlmostEqual(line_tilt_deg(a, b), line_tilt_deg(b, a), places=9)

    def test_known_values(self) -> None:
        self.assertAlmostEqual(line_tilt_deg((0.0, 0.0), (100.0, 0.0)), 0.0)
        self.assertAlmostEqual(line_tilt_deg((0.0, 0.0), (100.0, 100.0)), 45.0)  # slopes down-right
        self.assertAlmostEqual(line_tilt_deg((0.0, 0.0), (100.0, -100.0)), -45.0)
        self.assertAlmostEqual(line_tilt_deg((100.0, 0.0), (0.0, 0.0)), 0.0)

    def test_too_close_points_have_no_direction(self) -> None:
        self.assertIsNone(line_tilt_deg((5.0, 5.0), (5.0, 5.0)))
        self.assertIsNone(line_tilt_deg((5.0, 5.0), (5.0 + MIN_SEGMENT_PX * 0.5, 5.0)))
        self.assertIsNone(rotation_correction_deg((5.0, 5.0), (5.0, 5.0)))


class CorrectionLevelsTheLineTest(unittest.TestCase):
    """The real check: apply the correction through the real transform and
    measure the line afterwards."""

    def _assert_levels(self, tilt: float, *, flip_h: bool, flip_v: bool, start_rotation: float = 0.0) -> None:
        # Draw the tilted line in the *source*; whatever the transform makes of
        # it is what the user sees and clicks on.
        source = _line_image(tilt)
        before = GeometrySettings(rotation_angle_deg=start_rotation, flip_horizontal=flip_h, flip_vertical=flip_v)
        displayed = apply_spatial_preprocessing(source, before)
        visible_tilt = _measured_tilt(displayed)

        # The user clicks two points on the visible line.
        c = displayed.shape[0] / 2
        dx = 120.0
        p1 = (c - dx, c - dx * math.tan(math.radians(visible_tilt)))
        p2 = (c + dx, c + dx * math.tan(math.radians(visible_tilt)))
        correction = rotation_correction_deg(p1, p2, flip_horizontal=flip_h, flip_vertical=flip_v)
        self.assertIsNotNone(correction)

        after = GeometrySettings(
            rotation_angle_deg=start_rotation + correction, flip_horizontal=flip_h, flip_vertical=flip_v
        )
        self.assertAlmostEqual(
            _measured_tilt(apply_spatial_preprocessing(source, after)),
            0.0,
            delta=0.15,  # pixel-quantised line + interpolation, not the math
            msg=f"tilt={tilt} flipH={flip_h} flipV={flip_v} start={start_rotation}",
        )

    def test_levels_with_every_flip_combination(self) -> None:
        for tilt in (3.0, -2.0, 7.5):
            for flip_h in (False, True):
                for flip_v in (False, True):
                    self._assert_levels(tilt, flip_h=flip_h, flip_v=flip_v)

    def test_levels_when_a_rotation_is_already_applied(self) -> None:
        """Clicking again refines: the correction is added to the current
        angle, not substituted for it."""
        for start in (1.0, -4.0):
            for flip_h in (False, True):
                self._assert_levels(3.0, flip_h=flip_h, flip_v=False, start_rotation=start)

    def test_flipping_exactly_one_axis_negates_the_correction(self) -> None:
        p1, p2 = (100.0, 100.0), (300.0, 110.0)
        plain = rotation_correction_deg(p1, p2)
        self.assertAlmostEqual(rotation_correction_deg(p1, p2, flip_horizontal=True), -plain)
        self.assertAlmostEqual(rotation_correction_deg(p1, p2, flip_vertical=True), -plain)
        self.assertAlmostEqual(rotation_correction_deg(p1, p2, flip_horizontal=True, flip_vertical=True), plain)


if __name__ == "__main__":
    unittest.main()
