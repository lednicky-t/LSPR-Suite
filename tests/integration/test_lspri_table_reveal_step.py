"""Selecting a step on the Sensorgram's plan bar scrolls the ROI / Groups table to that step's column (2026-11-26).

Pinned: `RoiTablePanel.step_link.reveal` brings a step column that is out of view into view, right of the frozen columns, and leaves the table alone when the column is already
visible, when the panel is hidden, when the step is unknown or when the column is hidden; `_follow_plan_bar` turns the bar's selection into that call for the newly selected
step only, and the table's clicks go to the bar without scrolling.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.app_rewrite import _follow_plan_bar
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_COUNT
    from tests.integration.test_lspri_step_column_width import WidthBase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class RevealStepTest(WidthBase):
    def setUp(self) -> None:
        super().setUp()
        self.view.resize(260, 420)  # narrower than the step columns together: some have to scroll in
        _APP.processEvents()
        self.bar = self.tree.horizontalScrollBar()
        self.columns = self.model.step_columns()
        self.assertGreaterEqual(len(self.columns), 2)
        self.assertGreater(self.bar.maximum(), 0)

    def centred(self, position: int) -> int:
        """The scroll value that puts the column in the middle of the room right of the frozen columns."""
        header, column = self.tree.header(), COLUMN_COUNT + position
        room = self.tree.viewport().width() - self.tree.pinned_width()
        return max(0, round(header.sectionPosition(column) + header.sectionSize(column) / 2 - self.tree.pinned_width() - room / 2))

    def left_of(self, position: int) -> int:
        return self.tree.header().sectionPosition(COLUMN_COUNT + position)

    def test_a_column_out_of_view_is_scrolled_to(self) -> None:
        last = len(self.columns) - 1
        self.bar.setValue(0)
        self.view.step_link.reveal(self.columns[last].ref)
        self.assertGreater(self.bar.value(), 0)
        self.assertEqual(self.bar.value(), min(self.centred(last), self.bar.maximum()))

    def test_a_column_that_is_scrolled_to_is_put_in_the_middle_of_the_view_not_at_its_left_edge(self) -> None:
        self.tree.setColumnWidth(COLUMN_COUNT + len(self.columns) - 1, 900)  # room to scroll on past the first step column, so it can be centred
        _APP.processEvents()
        self.assertGreater(self.left_of(0), self.tree.viewport().width())  # out of view
        self.bar.setValue(0)
        self.view.step_link.reveal(self.columns[0].ref)
        header = self.tree.header()
        centre = header.sectionViewportPosition(COLUMN_COUNT) + header.sectionSize(COLUMN_COUNT) / 2
        self.assertAlmostEqual(centre, self.tree.viewport().width() / 2, delta=2)

    def test_a_column_already_in_view_does_not_move_the_table(self) -> None:
        self.view.step_link.reveal(self.columns[0].ref)  # now it is in view (the scroll bar may stop short of left-aligning it)
        where = self.bar.value()
        self.view.step_link.reveal(self.columns[0].ref)
        self.assertEqual(self.bar.value(), where)

    def test_a_hidden_panel_is_left_alone(self) -> None:
        self.view.set_panel_visible(False)
        self.bar.setValue(0)
        self.view.step_link.reveal(self.columns[-1].ref)
        self.assertEqual(self.bar.value(), 0)

    def test_an_unknown_step_does_nothing(self) -> None:
        self.bar.setValue(0)
        self.view.step_link.reveal(("no", "such", "step"))
        self.assertEqual(self.bar.value(), 0)

    def test_a_hidden_column_does_nothing(self) -> None:
        self.bar.setValue(0)
        self.tree.header().setSectionHidden(COLUMN_COUNT + len(self.columns) - 1, True)
        self.view.step_link.reveal(self.columns[-1].ref)
        self.assertEqual(self.bar.value(), 0)


class FollowBarTest(unittest.TestCase):
    """The wiring (`_follow_plan_bar`): the bar's selection and the table's step columns are one selection; only a selection made on the bar scrolls the table."""

    def setUp(self) -> None:
        from PyQt6.QtCore import QObject, pyqtSignal

        class Panel(QObject):
            plan_bar_settings_changed = pyqtSignal()
            plan_steps_selected = pyqtSignal(object)

            def __init__(self) -> None:
                super().__init__()
                self.chosen: list[list[int]] = []

            def plan_bar_settings(self):
                return mock.Mock(text_mode="whole", separators=(), alpha=0.5)

            def bulk_controls(self):
                return mock.Mock()

            def select_plan_steps(self, indices) -> None:
                self.chosen.append(list(indices))
                self.plan_steps_selected.emit(tuple(indices))  # (what the bar does when its selection changes)

        class Link(QObject):
            selected = pyqtSignal(object)

        self.panel = Panel()
        self.link = Link()
        self.link.set_selected = mock.Mock()
        self.link.reveal = mock.Mock()
        self.table = mock.Mock(step_link=self.link)
        steps = [mock.Mock(ref=f"ref{i}") for i in range(4)]
        self.plan = mock.Mock()
        self.plan.steps.return_value = steps
        _follow_plan_bar(self.panel, mock.Mock(), self.table, self.plan)

    def test_the_newly_selected_step_is_revealed(self) -> None:
        self.panel.plan_steps_selected.emit((2,))
        self.link.reveal.assert_called_once_with("ref2")

    def test_extending_the_selection_reveals_the_added_step(self) -> None:
        self.panel.plan_steps_selected.emit((1,))
        self.link.reveal.reset_mock()
        self.panel.plan_steps_selected.emit((1, 3))
        self.link.reveal.assert_called_once_with("ref3")

    def test_dropping_a_step_or_clearing_reveals_nothing(self) -> None:
        self.panel.plan_steps_selected.emit((1, 3))
        self.link.reveal.reset_mock()
        self.panel.plan_steps_selected.emit((1,))
        self.panel.plan_steps_selected.emit(())
        self.link.reveal.assert_not_called()

    def test_the_bars_selection_selects_those_columns_in_the_table(self) -> None:
        self.panel.plan_steps_selected.emit((1, 3))
        self.link.set_selected.assert_called_with(["ref1", "ref3"])
        self.panel.plan_steps_selected.emit(())
        self.link.set_selected.assert_called_with([])

    def test_selected_columns_select_those_steps_on_the_bar_and_do_not_scroll_the_table(self) -> None:
        self.link.selected.emit(("ref3", "ref0", "gone"))
        self.assertEqual(self.panel.chosen, [[0, 3]])  # in step order; a reference that is no step any more is left out
        self.link.reveal.assert_not_called()  # (the user clicked the header: the column is where they clicked)
        self.link.set_selected.assert_called_with(["ref0", "ref3"])


if __name__ == "__main__":
    unittest.main()
