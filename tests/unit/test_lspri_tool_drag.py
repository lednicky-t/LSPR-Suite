"""The maths of the right-drag gestures (`panels/image/tool_drag.dragged_value`), 2026-11-15.

Pure: a number moves on a log scale (every `px_per_doubling` screen pixels double it), the other way when the axis is not `positive` (a lower
threshold selects more), stays inside its range, and is a whole number for an integer spin box. No Qt event loop, no drawing.
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
    from lspr_imaging_app.panels.image.tool_drag import DragAxis, dragged_value
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _axis(**overrides) -> DragAxis:
    fields = dict(caption="x", read=lambda: 0.0, write=lambda _v: None, low=0.1, high=500.0, along="x")
    fields.update(overrides)
    return DragAxis(**fields)


class DraggedValueTest(unittest.TestCase):
    def test_a_doubling_distance_doubles_and_halves_the_number(self) -> None:
        axis = _axis()
        self.assertEqual(dragged_value(20.0, 80.0, axis), 40.0)
        self.assertEqual(dragged_value(20.0, -80.0, axis), 10.0)
        self.assertEqual(dragged_value(20.0, 0.0, axis), 20.0)
        self.assertAlmostEqual(dragged_value(20.0, 40.0, axis), 28.3, places=1, msg="half the distance: a factor of sqrt(2)")

    def test_an_axis_that_is_not_positive_runs_the_other_way(self) -> None:
        """A lower threshold selects more, and sideways-right always means "bigger region"."""
        axis = _axis(positive=False)
        self.assertEqual(dragged_value(20.0, 80.0, axis), 10.0)
        self.assertEqual(dragged_value(20.0, -80.0, axis), 40.0)

    def test_the_number_stays_inside_its_range(self) -> None:
        axis = _axis(low=0.1, high=500.0)
        self.assertEqual(dragged_value(400.0, 800.0, axis), 500.0)
        self.assertEqual(dragged_value(0.5, -800.0, axis), 0.1)

    def test_an_integer_axis_gives_a_whole_number(self) -> None:
        axis = _axis(integer=True, low=1, high=200)
        got = dragged_value(10, 80.0, axis)
        self.assertIsInstance(got, int)
        self.assertEqual(got, 20)
        self.assertIsInstance(dragged_value(7, 13.0, axis), int)
        self.assertEqual(dragged_value(150, 200.0, axis), 200)

    def test_the_scale_is_the_axes_own(self) -> None:
        self.assertEqual(dragged_value(20.0, 40.0, _axis(px_per_doubling=40.0)), 40.0)

    def test_decimals_are_rounded(self) -> None:
        self.assertEqual(dragged_value(3.0, 10.0, _axis(decimals=1)), round(3.0 * 2 ** (10 / 80), 1))


if __name__ == "__main__":
    unittest.main()
