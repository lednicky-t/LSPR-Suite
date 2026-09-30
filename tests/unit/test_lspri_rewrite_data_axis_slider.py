"""Regression tests for the rewrite's `DataAxisSlider`
(`panels/image/data_axis_slider.py`), specifically `_large_gap_boundaries`/
`_paint_gap_break` (2026-09-30, revised same day): an unusually large
consecutive gap in the real values passed to `set_ticks` (e.g. wavelength
data with a real acquisition at 0 nm - a dark/reference frame - followed by
the first real spectral wavelength hundreds of nm later) gets a scale-break
glyph drawn between the two real ticks on either side of it. Both ticks
stay real, labeled, and selectable - nothing is added, removed, or
relabeled.

Supersedes a same-day first attempt (`set_axis_break`) that invented a
synthetic, non-selectable "0" tick outside the real value range - which
duplicated an already-real 0 nm tick whenever the dataset genuinely had one
(maintainer report, screenshot: "created two 0 ticks, first one not
working"). See this module's own docstring for the full story.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

Pure widget geometry/state, no rendering needed - `QApplication` is built
only because `QWidget` subclasses require one to construct at all.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.data_axis_slider import DataAxisSlider
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


class DataAxisSliderGapBreakTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slider = DataAxisSlider()
        self.slider.resize(300, 32)
        self.slider.setEnabled(True)

    def test_no_break_for_a_regular_grid(self) -> None:
        self.slider.setMaximum(4)
        self.slider.set_ticks([470.0, 500.0, 550.0, 600.0, 650.0], {})
        self.assertEqual(self.slider._large_gap_boundaries(), [])

    def test_break_detected_at_a_real_gap(self) -> None:
        """The exact reported scenario: a real 0 nm frame, then a big jump
        to the first real spectral wavelength, then a regular grid."""
        self.slider.setMaximum(4)
        self.slider.set_ticks([0.0, 470.0, 500.0, 550.0, 600.0], {})
        self.assertEqual(self.slider._large_gap_boundaries(), [0])

    def test_break_boundary_index_is_before_the_jump(self) -> None:
        """Boundary `i` means "between tick i and tick i+1" - pins the
        off-by-one direction explicitly, not just that *a* boundary is
        found somewhere."""
        self.slider.setMaximum(5)
        self.slider.set_ticks([100.0, 150.0, 200.0, 700.0, 750.0, 800.0], {})
        self.assertEqual(self.slider._large_gap_boundaries(), [2])

    def test_multiple_gaps_are_all_detected(self) -> None:
        self.slider.setMaximum(5)
        self.slider.set_ticks([0.0, 400.0, 420.0, 440.0, 900.0, 920.0], {})
        self.assertEqual(self.slider._large_gap_boundaries(), [0, 3])

    def test_fewer_than_three_points_never_breaks(self) -> None:
        """Need at least two gaps to know what "typical" even means - one
        gap has nothing to compare itself against."""
        self.slider.setMaximum(1)
        self.slider.set_ticks([0.0, 900.0], {})
        self.assertEqual(self.slider._large_gap_boundaries(), [])

    def test_both_ticks_around_a_break_stay_real_and_selectable(self) -> None:
        """The whole point of this design over the reverted `set_axis_break`
        one: no synthetic index, no reserved dead zone - every index in the
        real array is reachable exactly as if no break existed."""
        self.slider.setMaximum(4)
        self.slider.set_ticks([0.0, 470.0, 500.0, 550.0, 600.0], {})
        track = self.slider._track_rect()
        self.assertEqual(self.slider._index_from_x(track.left()), 0)
        self.assertEqual(
            self.slider._index_from_x(track.left() + track.width() * (1 / 4)), 1
        )
        # No reserved zone at all - the track starts at the ordinary inset.
        self.assertAlmostEqual(track.left(), self.slider._SIDE_INSET)

    def test_paints_without_error_with_and_without_a_break(self) -> None:
        """No assertion beyond "doesn't raise" - `paintEvent` is the one
        method real screen rendering actually exercises. `grab()` forces a
        real paint regardless of the widget's visibility, unlike `repaint()`
        /`update()` which are no-ops for a widget that was never shown."""
        self.slider.setMaximum(4)
        self.slider.set_ticks([470.0, 500.0, 550.0, 600.0, 650.0], {0: "470"})
        self.slider.grab()
        self.slider.set_ticks([0.0, 470.0, 500.0, 550.0, 600.0], {0: "0", 1: "470"})
        self.slider.grab()
        self.slider.setEnabled(False)
        self.slider.grab()


if __name__ == "__main__":
    unittest.main()
