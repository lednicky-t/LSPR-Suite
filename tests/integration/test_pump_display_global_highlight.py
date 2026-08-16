"""Coverage for the global (not per-step) pump-display highlight feature:

- ``ExperimentPlanCommentDelegate.paint()`` splits a Comment cell's text at the
  pump's 16-character limit only when the *window's* global
  ``_pump_display_enabled``/``_pump_display_highlight_enabled`` are both set -
  this is plan-wide, not read from any per-step field (there is none anymore).
- ``_HighlightingCommentLineEdit`` (the inline cell editor) live-splits the
  same way while the field's full text still fits without horizontal
  scrolling, and falls back to plain native rendering once it doesn't.
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem, QTableView

from lspr_app.device.reglo_icc import PUMP_DISPLAY_MAX_LENGTH
from lspr_app.domain.pump_plan import ACTIVE_PUMP_CHANNELS, PumpPlanStep
from lspr_app.gui.flow_plan_model import ExperimentPlanCommentDelegate, ExperimentPlanTableModel, _HighlightingCommentLineEdit


_OVERFLOW_HEX = "#c97a7a"
_DESCRIPTION_COLUMN = 4 + ACTIVE_PUMP_CHANNELS * 3 + 3
_LONG_COMMENT = "This comment is definitely longer than sixteen characters"


def _close(color_hex: str, target_hex: str, tolerance: int = 12) -> bool:
    c = tuple(int(color_hex[i : i + 2], 16) for i in (1, 3, 5))
    t = tuple(int(target_hex[i : i + 2], 16) for i in (1, 3, 5))
    return all(abs(a - b) <= tolerance for a, b in zip(c, t))


def _has_overflow_color(pixmap: QPixmap) -> bool:
    image = pixmap.toImage()
    return any(_close(image.pixelColor(x, image.height() // 2).name(), _OVERFLOW_HEX) for x in range(0, image.width(), 2))


# Held at module scope deliberately: QApplication.instance() or QApplication([]) would
# otherwise construct-and-immediately-garbage-collect a new QApplication if nothing keeps
# a Python reference to it, which crashes the process (natively, with no Python traceback)
# on the next widget construction - this keeps exactly one alive for the whole test run.
_APP = QApplication.instance() or QApplication([])


class _FakeWindow:
    def __init__(self, table: QTableView, *, pump_display_enabled: bool, pump_display_highlight_enabled: bool) -> None:
        self.plan_table = table
        self._pump_display_enabled = pump_display_enabled
        self._pump_display_highlight_enabled = pump_display_highlight_enabled

    def _theme_palette(self) -> dict[str, str]:
        return {"bg": "#20262e", "fg": "#e6ebf1", "field": "#2a313b", "selection": "#3a4250"}

    def _plan_table_is_editing(self, _row: int, _column: int) -> bool:
        return False


def _make_model_and_delegate(*, pump_display_enabled: bool, pump_display_highlight_enabled: bool):
    headers = ["x"] * (4 + ACTIVE_PUMP_CHANNELS * 3 + 4)
    model = ExperimentPlanTableModel(headers, app_name="LSPR Acquisition", app_version="0.4.0")
    model.set_steps([PumpPlanStep(step=1, description=_LONG_COMMENT)])
    table = QTableView()
    table.setModel(model)
    window = _FakeWindow(
        table, pump_display_enabled=pump_display_enabled, pump_display_highlight_enabled=pump_display_highlight_enabled
    )
    delegate = ExperimentPlanCommentDelegate(window)
    return model, delegate


class CommentDelegateGlobalHighlightTests(unittest.TestCase):
    def test_splits_text_when_both_global_toggles_are_on(self) -> None:
        model, delegate = _make_model_and_delegate(pump_display_enabled=True, pump_display_highlight_enabled=True)
        index = model.index(0, _DESCRIPTION_COLUMN)

        pixmap = QPixmap(400, 24)
        pixmap.fill()
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.rect = pixmap.rect()
        delegate.paint(painter, option, index)
        painter.end()

        self.assertTrue(_has_overflow_color(pixmap))

    def test_no_split_when_highlight_toggle_is_off(self) -> None:
        model, delegate = _make_model_and_delegate(pump_display_enabled=True, pump_display_highlight_enabled=False)
        index = model.index(0, _DESCRIPTION_COLUMN)

        pixmap = QPixmap(400, 24)
        pixmap.fill()
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.rect = pixmap.rect()
        delegate.paint(painter, option, index)
        painter.end()

        self.assertFalse(_has_overflow_color(pixmap))

    def test_no_split_when_show_on_pump_display_toggle_is_off(self) -> None:
        # Global auto-deactivation: even if the highlight flag were somehow left on,
        # nothing highlights while the main "show on pump display" toggle is off.
        model, delegate = _make_model_and_delegate(pump_display_enabled=False, pump_display_highlight_enabled=True)
        index = model.index(0, _DESCRIPTION_COLUMN)

        pixmap = QPixmap(400, 24)
        pixmap.fill()
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.rect = pixmap.rect()
        delegate.paint(painter, option, index)
        painter.end()

        self.assertFalse(_has_overflow_color(pixmap))


class HighlightingCommentLineEditTests(unittest.TestCase):
    def test_splits_live_while_text_fits_without_scrolling(self) -> None:
        # Checks the actual paint decision (was the split-text drawing helper
        # invoked, with the right text/limit) rather than sampling rendered
        # pixel colors: font hinting/DPI differences between machines shift
        # glyph pixels by a row or two, which made a pixel-color assertion
        # here flaky across environments even when the highlight painted fine.
        editor = _HighlightingCommentLineEdit(highlight_active=True)
        editor.setGeometry(QRect(0, 0, 400, 24))
        editor.setText(_LONG_COMMENT)
        self.assertTrue(editor._fits_without_scrolling())

        pixmap = QPixmap(editor.size())
        pixmap.fill()
        with patch("lspr_acq_shell.experiment_plan_table_model._draw_split_comment_text") as draw_split:
            editor.render(pixmap)

        draw_split.assert_called_once()
        _painter, _rect, text_arg, limit_arg, *_rest = draw_split.call_args.args
        self.assertEqual(text_arg, _LONG_COMMENT)
        self.assertEqual(limit_arg, PUMP_DISPLAY_MAX_LENGTH)

    def test_falls_back_to_native_rendering_when_text_would_scroll(self) -> None:
        editor = _HighlightingCommentLineEdit(highlight_active=True)
        editor.setGeometry(QRect(0, 0, 60, 24))  # too narrow for _LONG_COMMENT
        editor.setText(_LONG_COMMENT)
        self.assertFalse(editor._fits_without_scrolling())

        pixmap = QPixmap(editor.size())
        pixmap.fill()
        editor.render(pixmap)
        self.assertFalse(_has_overflow_color(pixmap))

    def test_no_split_when_highlight_inactive(self) -> None:
        editor = _HighlightingCommentLineEdit(highlight_active=False)
        editor.setGeometry(QRect(0, 0, 400, 24))
        editor.setText(_LONG_COMMENT)

        pixmap = QPixmap(editor.size())
        pixmap.fill()
        editor.render(pixmap)
        self.assertFalse(_has_overflow_color(pixmap))

    def test_no_split_under_16_characters(self) -> None:
        editor = _HighlightingCommentLineEdit(highlight_active=True)
        editor.setGeometry(QRect(0, 0, 400, 24))
        editor.setText("Short")

        pixmap = QPixmap(editor.size())
        pixmap.fill()
        editor.render(pixmap)
        self.assertFalse(_has_overflow_color(pixmap))


if __name__ == "__main__":
    unittest.main()
