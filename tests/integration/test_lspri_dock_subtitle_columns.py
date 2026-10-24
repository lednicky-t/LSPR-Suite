"""Tests for the dock title bar's fixed-place status fields (`PanelContainer.set_subtitle_columns`,
2026-10-16, maintainer request: "fields will have fixed position ... the comments section should be
the largest, left align everything").

A real `QApplication` built in-process, never `.exec()`; real widgets on the offscreen platform.
Text widths are measured, so the app's real fonts are loaded first (`tests/_qt_fonts.py`).
"""

from __future__ import annotations

import re
import sys
import time
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFontMetrics

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

from tests._qt_fonts import load_system_fonts  # noqa: E402

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.dock_container import PanelContainer
    from lspr_imaging_app.panels.subtitle_column import SubtitleColumn
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _fields(role: str = "", cube: str = "", wavelength: str = "", clock: str = "", comment: str = "", tooltip: str = ""):
    """The same five fields the Image panel sends (role, cube, wavelength, time, comment)."""
    return (
        SubtitleColumn(role, "Dark background", "#ff8800" if role else None),
        SubtitleColumn(cube, "Cube 314"),
        SubtitleColumn(wavelength, "1000 nm"),
        SubtitleColumn(clock, "00:00:00.00"),
        SubtitleColumn(comment, tooltip=tooltip),
    )


class DockSubtitleColumnsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        load_system_fonts()

    def setUp(self) -> None:
        self.window = QtWidgets.QMainWindow()
        self.dock = PanelContainer("Image", QtWidgets.QWidget(), self.window)
        self.window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.window.resize(1100, 400)
        self.window.show()
        _pump()

    def tearDown(self) -> None:
        self.window.close()

    def _labels(self) -> list[QtWidgets.QLabel]:
        return self.dock._subtitle_column_labels

    def _x_positions(self) -> list[int]:
        return [label.mapTo(self.window, QPoint(0, 0)).x() for label in self._labels()]

    def test_fields_keep_their_places_when_a_role_a_time_or_a_comment_appears_or_goes(self) -> None:
        self.dock.set_subtitle_columns(_fields(cube="Cube 3", wavelength="550 nm"))
        _pump()
        bare = self._x_positions()
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3", "550 nm", "1:02:05.00", "buffer"))
        _pump()
        full = self._x_positions()
        self.dock.set_subtitle_columns(_fields("Dark background", "Cube 314", "1000 nm", "12:34:56.78", "x" * 40))
        _pump()
        widest = self._x_positions()
        self.dock.set_subtitle_columns(_fields(comment="No dataset loaded."))
        _pump()
        message = self._x_positions()
        self.assertEqual(len(bare), 5)
        self.assertEqual(bare, full)
        self.assertEqual(bare, widest)
        self.assertEqual(bare, message)

    def test_a_narrow_dock_drops_the_trailing_fields_whole_instead_of_staying_wide(self) -> None:
        """2026-10-24: the fields no longer set the dock's minimum width; the ones that do not fit are hidden from the right."""
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3", "550 nm", "0:03.24", "buffer"))
        _pump()
        self.assertTrue(all(not label.isHidden() for label in self._labels()))
        self.assertLess(self.dock.minimumSizeHint().width(), 250)  # the fields add 480+ px when they all have to fit
        self.window.resize(self.dock.minimumSizeHint().width() + 120, 400)  # the dock is the only thing in the window
        for _ in range(4):
            _pump()
        shown = [not label.isHidden() for label in self._labels()]
        self.assertTrue(shown[0] and not shown[-1], shown)  # the first is kept, the last (comment) goes
        self.assertEqual(shown, sorted(shown, reverse=True), shown)  # always from the right: no hidden field before a shown one
        host = self.dock._subtitle_columns_host
        for label in self._labels():
            if not label.isHidden():
                self.assertLessEqual(label.mapTo(host, QPoint(label.width(), 0)).x(), host.width())  # a shown field is whole

    def test_an_empty_fixed_field_at_the_end_gives_its_room_to_the_comment(self) -> None:
        """2026-10-24: the Image dock ends with the frame role word, empty on most frames; it must not leave a blank beside the comment."""
        def fields(role: str) -> tuple[SubtitleColumn, ...]:
            return (SubtitleColumn("Cube 3", "Cube 314"), SubtitleColumn("a long comment " * 4), SubtitleColumn(role, "Dark background"))

        self.dock.set_subtitle_columns(fields(""))
        _pump()
        cube, comment, role = self._labels()
        self.assertTrue(role.isHidden())
        host = self.dock._subtitle_columns_host
        self.assertEqual(comment.mapTo(host, QPoint(comment.width(), 0)).x(), host.width())  # the comment runs to the host's right edge
        wide_comment = comment.width()
        self.dock.set_subtitle_columns(fields("Reference"))
        _pump()
        self.assertFalse(role.isHidden())
        self.assertLess(comment.width(), wide_comment)
        self.assertEqual(role.mapTo(host, QPoint(role.width(), 0)).x(), host.width())  # the role word sits at the right end

    def test_fields_are_left_aligned_in_order_after_the_title_and_the_comment_takes_the_rest(self) -> None:
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3", "550 nm", "0:03.24", "buffer"))
        _pump()
        xs = self._x_positions()
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(set(xs)), 5)
        title_bar = self.dock.titleBarWidget()
        title = next(w for w in title_bar.findChildren(QtWidgets.QLabel) if w.text() == "Image")
        self.assertGreater(xs[0], title.mapTo(self.window, QPoint(0, 0)).x() + title.width() - 1)
        widths = [label.width() for label in self._labels()]
        self.assertEqual(max(widths), widths[-1])  # the comment is the largest
        self.assertGreater(widths[-1], 3 * max(widths[:-1]))
        for label in self._labels():
            self.assertTrue(label.alignment() & Qt.AlignmentFlag.AlignLeft)

    def test_every_fixed_field_is_as_wide_as_its_widest_text_with_a_margin(self) -> None:
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3", "550 nm", "0:03.24", "buffer"))
        _pump()
        metrics = QFontMetrics(self._labels()[0].font())
        digit = max("0123456789", key=metrics.horizontalAdvance)
        for label, widest in zip(self._labels()[:4], ("Dark background", "Cube 314", "1000 nm", "00:00:00.00")):
            worst = re.sub(r"\d", digit, widest)
            margin = label.width() - metrics.horizontalAdvance(worst)
            self.assertGreaterEqual(margin, 2, f"{widest!r}: only {margin}px to spare")
            self.assertLessEqual(margin, 8, f"{widest!r}: {margin}px is wasted")

    def test_a_long_comment_is_cut_with_dots_keeps_its_tooltip_and_does_not_widen_the_title_bar(self) -> None:
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3", "550 nm", "0:03.24", "short"))
        _pump()
        short_min = self.dock.titleBarWidget().minimumSizeHint().width()
        long_text = "a long experiment comment " * 40
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3", "550 nm", "0:03.24", long_text, tooltip="the comment"))
        _pump()
        comment = self._labels()[-1]
        self.assertTrue(comment.text().endswith("…"))
        self.assertLess(len(comment.text()), len(long_text))
        self.assertEqual(comment.toolTip(), "the comment")
        self.assertEqual(self.dock.titleBarWidget().minimumSizeHint().width(), short_min)
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3", "550 nm", "0:03.24", long_text))
        _pump()
        self.assertEqual(comment.toolTip(), long_text)  # no tooltip given: the cut text is shown whole on hover

    def test_a_field_takes_its_colour_and_no_columns_goes_back_to_the_centred_text(self) -> None:
        self.dock.set_subtitle_columns(_fields("Reference", "Cube 3"))
        _pump()
        self.assertIn("#ff8800", self._labels()[0].styleSheet())
        self.assertNotIn("#ff8800", self._labels()[1].styleSheet())
        self.assertTrue(self.dock._subtitle_centered.isHidden())
        self.dock.set_subtitle_columns(())
        self.dock.set_subtitle("plain text")
        _pump()
        self.assertFalse(self.dock._subtitle_centered.isHidden())
        self.assertTrue(self.dock._subtitle_columns_host.isHidden())
        self.assertEqual(self.dock._subtitle_label.text(), "plain text")

    def test_theme_refresh_keeps_the_fields(self) -> None:
        self.dock.set_subtitle_columns(_fields("Ignored", "Cube 3", "550 nm", "0:03.24", "buffer"))
        _pump()
        before = self._x_positions()
        self.dock.refresh_theme()
        _pump()
        self.assertEqual([label.text() for label in self._labels()], ["Ignored", "Cube 3", "550 nm", "0:03.24", "buffer"])
        self.assertEqual(self._x_positions(), before)


if __name__ == "__main__":
    unittest.main()
