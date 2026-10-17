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
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QWheelEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.data_axis_slider import DataAxisSlider, axis_x
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


class DataAxisSliderFollowTests(unittest.TestCase):
    """A zoomed slider keeps its handle in the middle and slides the ticks past it (2026-10-17, maintainer request)."""

    COUNT = 60

    def setUp(self) -> None:
        self.slider = DataAxisSlider()
        self.slider.resize(400, 32)
        self.slider.setEnabled(True)
        self.slider.setMaximum(self.COUNT - 1)
        self.slider.setSingleStep(1)  # as the Image panel sets them: one tick per wheel notch
        self.slider.setPageStep(1)
        self.slider.set_ticks(list(range(self.COUNT)), {})
        self.window = self.slider.axis_window()
        self.window.zoom(5.0, 0.0)  # about 12 ticks on screen, from the start

    def middle(self) -> float:
        return sum(self.window.view()) / 2.0

    def send_mouse(self, kind: QEvent.Type, index_for_x: int, view: tuple[float, float]) -> None:
        x = axis_x(index_for_x, self.COUNT, self.slider.width(), view)
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
        event = QMouseEvent(
            kind, QPointF(x, 5.0), QPointF(self.slider.mapToGlobal(QPoint(int(x), 5))),
            Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(self.slider, event)

    def test_a_new_value_slides_the_ticks_so_the_handle_is_in_the_middle(self) -> None:
        self.slider.setValue(30)
        self.assertAlmostEqual(self.middle(), 30.0)
        x = axis_x(30, self.COUNT, self.slider.width(), self.window.view())
        self.assertAlmostEqual(x, self.slider.width() / 2.0)  # the handle is drawn at the middle of the widget

    def test_it_follows_also_when_the_signals_are_blocked_as_the_panel_sets_it(self) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(40)
        self.slider.blockSignals(False)
        self.assertAlmostEqual(self.middle(), 40.0)

    def test_stepping_moves_the_ticks_by_one_tick_not_the_handle(self) -> None:
        self.slider.setValue(30)
        x30 = axis_x(40, self.COUNT, self.slider.width(), self.window.view())
        self.slider.setValue(31)
        x30_next = axis_x(40, self.COUNT, self.slider.width(), self.window.view())
        tick_width = (self.slider.width() - 28.0) / (self.window.view()[1] - self.window.view()[0])
        self.assertAlmostEqual(x30 - x30_next, tick_width)  # tick 40 moved one tick to the left
        self.assertAlmostEqual(self.middle(), 31.0)

    def test_a_plain_wheel_steps_the_value_and_the_ticks_follow(self) -> None:
        self.slider.setValue(30)
        event = QWheelEvent(
            QPointF(200.0, 5.0), QPointF(200.0, 5.0), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(self.slider, event)
        self.assertEqual(self.slider.value(), 31)
        self.assertAlmostEqual(self.middle(), 31.0)

    def test_near_the_start_the_handle_runs_to_the_end_of_the_rail_instead_of_staying_in_the_middle(self) -> None:
        self.slider.setValue(2)
        self.assertEqual(self.window.view()[0], 0.0)
        self.assertLess(axis_x(2, self.COUNT, self.slider.width(), self.window.view()), self.slider.width() / 2.0)

    def test_the_whole_axis_is_left_alone(self) -> None:
        self.window.reset()
        self.slider.setValue(30)
        self.assertEqual(self.window.view(), (0.0, self.COUNT - 1.0))

    def test_while_the_button_is_down_the_ticks_stand_still_and_on_release_the_value_is_centred(self) -> None:
        self.slider.setValue(30)
        view = self.window.view()
        self.send_mouse(QEvent.Type.MouseButtonPress, 33, view)
        self.assertEqual(self.slider.value(), 33)
        self.assertEqual(self.window.view(), view)  # not moved under the pointer
        self.send_mouse(QEvent.Type.MouseMove, 35, view)  # dragged on: the pointer still means the tick it points at
        self.assertEqual(self.slider.value(), 35)
        self.assertEqual(self.window.view(), view)
        self.send_mouse(QEvent.Type.MouseButtonRelease, 35, view)
        self.assertAlmostEqual(self.middle(), 35.0)

    def test_hiding_the_slider_never_leaves_the_axis_held(self) -> None:
        self.slider.show()
        self.slider.setValue(30)
        self.send_mouse(QEvent.Type.MouseButtonPress, 33, self.window.view())
        self.slider.hide()  # the release will never arrive
        self.slider.setValue(20)
        self.assertAlmostEqual(self.middle(), 20.0)


if __name__ == "__main__":
    unittest.main()
