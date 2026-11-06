"""The step columns of the ROI / Groups table are filled with the colour of their step (2026-11-06): the same fill the Sensorgram's plan bar paints that step with, at the bar's
opacity.

Pinned: a column's colour is the colour of the bar's segment (the user's own, else the automatic one) and follows an edit of it; the bar's opacity slider sets the table's tint
(0 = none) and only repaints; cells, group headers and the header sections carry the tint, no other column does (the fixed ones, the comparison ones); and what is *painted* is
the bar's colour blended over the table's background (real pixels, offscreen), with the selection tint still showing over it and the numbers readable on any colour.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QItemSelectionModel, QModelIndex, Qt
from PyQt6.QtGui import QColor

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.delegate import tinted_text_color
    from lspr_imaging_app.panels.roi_table.model import STEP_TINT_ROLE
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_COUNT, COLUMN_ID, COLUMN_X
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.bar_model import PlanBarSettings
    from lspr_ui import get_active_theme
    from tests.integration.test_lspri_plan_bar import _pump
    from tests.integration.test_lspri_step_table import StepTableBase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

HORIZONTAL = Qt.Orientation.Horizontal
DEFAULT_ALPHA = PlanBarSettings().alpha
TOLERANCE = 3  # colour levels: rounding of the blend


def blend(fill: str, alpha: float, base: str) -> tuple[int, int, int]:
    """`fill` at opacity `alpha` over `base`, per channel."""
    f, b = QColor(fill), QColor(base)
    return tuple(round(alpha * x + (1.0 - alpha) * y) for x, y in ((f.red(), b.red()), (f.green(), b.green()), (f.blue(), b.blue())))  # type: ignore[return-value]


def plain(base: str) -> tuple[int, int, int]:
    """`base` itself, as the three numbers a pixel of it has."""
    return blend(base, 0.0, base)


class StepTintBase(StepTableBase):
    def setUp(self) -> None:
        super().setUp()
        self.view = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        self.view.restore_ui_state(UiStateStore({}))
        self.group_id = self.m.roi_toolbox.create_group("Pair")
        self.m.roi_toolbox.add_rois_to_group([1], self.group_id)
        self.view.resize(1200, 420)
        self.view.show()
        self.view.refresh_now()
        self.view._step_controls.columns_button.click()
        self.assertTrue(_pump(lambda: not self.table.is_running()))
        _APP.processEvents()
        self.model = self.view._model
        self.tree = self.view._tree
        self.theme = get_active_theme()

    def tearDown(self) -> None:
        self.view.hide()
        self.view.deleteLater()
        super().tearDown()

    def tint_of_header(self, column: int):
        return self.model.headerData(column, HORIZONTAL, STEP_TINT_ROLE)

    def bar_colors(self) -> list[str]:
        self.m.selection.set_roi_selection({1, 2})
        self._draw()
        return [step.color for step in self.panel._plot.plan_bar().steps()]

    def pixel(self, widget: QtWidgets.QWidget, x: int, y: int) -> tuple[int, int, int]:
        color = widget.grab().toImage().pixelColor(x, y)
        return color.red(), color.green(), color.blue()

    def assert_close(self, got: tuple[int, int, int], want: tuple[int, int, int], message: str = "") -> None:
        self.assertLessEqual(max(abs(a - b) for a, b in zip(got, want)), TOLERANCE, f"{message} got {got}, wanted {want}")

    def cell_probe(self, roi_id: int, column: int) -> tuple[int, int]:
        """A point in the cell's left end: the numbers are right-aligned, so nothing is drawn there but the background."""
        rect = self.tree.visualRect(self.model.roi_index(roi_id, column))
        return rect.left() + 2, rect.center().y()


class WhatTheModelSaysTest(StepTintBase):
    def test_a_column_has_the_colour_of_the_plan_bars_segment_for_its_step(self) -> None:
        bars = self.bar_colors()
        self.assertEqual(len(bars), 2)
        self.assertEqual([c.color for c in self.table.columns()], bars)
        for position, color in enumerate(bars):
            tint = self.tint_of_header(COLUMN_COUNT + position)
            self.assertEqual(tint.name().lower(), color.lower())
            self.assertAlmostEqual(tint.alphaF(), DEFAULT_ALPHA, places=2)

    def test_a_colour_the_user_gives_a_step_reaches_its_column(self) -> None:
        self.assertTrue(self.m.experiment_plan.set_color(0, "#123456"))
        self.assertEqual(self.table.columns()[0].color, "#123456")
        self.assertEqual(self.tint_of_header(COLUMN_COUNT).name(), "#123456")
        self.assertNotEqual(self.tint_of_header(COLUMN_COUNT + 1).name(), "#123456")

    def test_the_cells_of_rois_and_of_group_headers_carry_the_same_tint_as_the_header(self) -> None:
        for column in (COLUMN_COUNT, COLUMN_COUNT + 1):
            header = self.tint_of_header(column)
            self.assertEqual(self.model.roi_index(1, column).data(STEP_TINT_ROLE), header)
            self.assertEqual(self.model.group_headers()[0].siblingAtColumn(column).data(STEP_TINT_ROLE), header)

    def test_no_other_column_is_tinted(self) -> None:
        for column in range(COLUMN_COUNT):
            self.assertIsNone(self.tint_of_header(column))
            self.assertIsNone(self.model.roi_index(1, column).data(STEP_TINT_ROLE))
        self.table.add_compare()  # a comparison column belongs to no step
        _pump(lambda: False, 0.1)
        compare = self.model.columnCount() - 1
        self.assertGreaterEqual(compare, COLUMN_COUNT + 2)
        self.assertIsNone(self.tint_of_header(compare))
        self.assertIsNone(self.model.roi_index(1, compare).data(STEP_TINT_ROLE))


class TheBarsOpacityTest(StepTintBase):
    def setUp(self) -> None:
        super().setUp()
        app_rewrite._follow_plan_bar(self.panel, self.table, self.view)

    def test_the_opacity_of_the_bar_is_the_opacity_of_the_tint_and_zero_means_none(self) -> None:
        slider = self.panel._steps.plan_controls._alpha
        slider.setValue(30)
        self.assertAlmostEqual(self.tint_of_header(COLUMN_COUNT).alphaF(), 0.30, places=2)
        self.assertAlmostEqual(self.model.roi_index(1, COLUMN_COUNT).data(STEP_TINT_ROLE).alphaF(), 0.30, places=2)
        slider.setValue(0)
        self.assertIsNone(self.tint_of_header(COLUMN_COUNT))
        self.assertIsNone(self.model.roi_index(1, COLUMN_COUNT).data(STEP_TINT_ROLE))
        slider.setValue(100)
        self.assertAlmostEqual(self.tint_of_header(COLUMN_COUNT).alphaF(), 1.0, places=2)

    def test_a_change_of_opacity_only_repaints_it_rebuilds_nothing(self) -> None:
        rebuilt: list[int] = []
        self.model.modelReset.connect(lambda: rebuilt.append(1))
        self.model.layoutChanged.connect(lambda: rebuilt.append(1))
        self.panel._steps.plan_controls._alpha.setValue(20)
        self.assertEqual(rebuilt, [])

    def test_the_opacity_the_bar_was_restored_with_is_the_tint_from_the_start(self) -> None:
        self.panel._steps.plan_controls._alpha.setValue(40)
        fresh = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        app_rewrite._follow_plan_bar(self.panel, self.table, fresh)  # (what the app does once, after the panels restored their state)
        self.assertAlmostEqual(fresh._model.step_tint_alpha(), 0.40, places=2)
        fresh.deleteLater()


class WhatIsPaintedTest(StepTintBase):
    """Real pixels: the fill is the step's colour over the table's own background, the way the bar blends it over the plot."""

    def setUp(self) -> None:
        super().setUp()
        self.colors = self.bar_colors()
        self.m.selection.set_roi_selection(set())  # (the Sensorgram needed a selection to draw; a selected row would add its highlight to the pixels below)
        self.tree.setCurrentIndex(QModelIndex())  # nor may there be a current row: Qt frames it with a faint highlight over its cells, which is not what is measured here
        _APP.processEvents()

    def test_a_roi_cell_is_the_step_colour_over_the_table_background(self) -> None:
        for position, color in enumerate(self.colors):
            x, y = self.cell_probe(1, COLUMN_COUNT + position)
            self.assert_close(self.pixel(self.tree.viewport(), x, y), blend(color, DEFAULT_ALPHA, self.theme.toolbar_bg), f"step {position + 1}")

    def test_a_fixed_column_of_the_same_row_is_not(self) -> None:
        x, y = self.cell_probe(1, COLUMN_X)  # (its number is right-aligned: the left end holds nothing)
        self.assert_close(self.pixel(self.tree.viewport(), x, y), plain(self.theme.toolbar_bg))

    def test_a_group_header_is_filled_over_its_own_bar_in_the_step_columns(self) -> None:
        header = self.model.group_headers()[0]
        row = self.tree.visualRect(header).center().y()
        self.assert_close(self.pixel(self.tree.viewport(), 2, row), plain(self.theme.toolbar_section_bg), "the group bar itself")
        for position, color in enumerate(self.colors):
            column = COLUMN_COUNT + position
            x = self.tree.header().sectionViewportPosition(column) + 2
            self.assert_close(self.pixel(self.tree.viewport(), x, row), blend(color, DEFAULT_ALPHA, self.theme.toolbar_section_bg), f"group, step {position + 1}")

    def test_the_header_section_of_a_step_is_filled_the_same(self) -> None:
        header = self.tree.header()
        for position, color in enumerate(self.colors):
            x = header.sectionViewportPosition(COLUMN_COUNT + position) + 2
            self.assert_close(self.pixel(header.viewport(), x, 3), blend(color, DEFAULT_ALPHA, self.theme.toolbar_bg), f"header, step {position + 1}")

    def _ink(self, column: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        """(pixels that differ from the section's fill in its left 20 px: the sort triangle, and in the rest: the title) as (x, y) inside the section."""
        header = self.tree.header()
        image = header.viewport().grab().toImage()
        left, width = header.sectionViewportPosition(column), header.sectionSize(column)
        fill = image.pixelColor(left + 1, 2)
        arrow, title = [], []
        for y in range(0, header.viewport().height() - 2):  # (not the line along the bottom)
            for x in range(width):
                color = image.pixelColor(left + x, y)
                if max(abs(color.red() - fill.red()), abs(color.green() - fill.green()), abs(color.blue() - fill.blue())) > 40:
                    (arrow if x < 20 else title).append((x, y))
        return arrow, title

    def test_the_sorted_step_header_has_a_triangle_up_or_down_and_its_title_does_not_move(self) -> None:
        header = self.tree.header()
        column = COLUMN_COUNT
        self.tree.setColumnWidth(column, 200)  # wide, so the title (right-aligned) is far from the triangle: how close a fitted column's title comes is pinned in test_lspri_step_column_width.py
        header.setSortIndicator(COLUMN_ID, Qt.SortOrder.AscendingOrder)
        _APP.processEvents()
        none, title_before = self._ink(column)
        self.assertEqual(none, [], "a header that is not sorted has no triangle")
        header.setSortIndicator(column, Qt.SortOrder.AscendingOrder)
        _APP.processEvents()
        up, title_up = self._ink(column)
        header.setSortIndicator(column, Qt.SortOrder.DescendingOrder)
        _APP.processEvents()
        down, title_down = self._ink(column)
        self.assertTrue(up and down, "the sorted header shows no triangle")
        self.assertEqual(title_up, title_before)  # (a short title is right-aligned: the room kept for the triangle does not touch it)
        self.assertEqual(title_down, title_before)
        top = lambda pixels: sum(1 for x, y in pixels if y < sum(p[1] for p in pixels) / len(pixels))  # noqa: E731 - ink above the triangle's middle
        self.assertLess(top(up), len(up) - top(up), "ascending should point up: narrow at the top")
        self.assertGreater(top(down), len(down) - top(down), "descending should point down: wide at the top")

    def test_a_header_that_is_not_a_step_keeps_the_plain_background(self) -> None:
        header = self.tree.header()
        self.assert_close(self.pixel(header.viewport(), header.sectionViewportPosition(COLUMN_ID) + 2, 3), plain(self.theme.toolbar_bg))

    def test_a_new_opacity_is_painted_without_any_rebuild(self) -> None:
        app_rewrite._follow_plan_bar(self.panel, self.table, self.view)
        self.panel._steps.plan_controls._alpha.setValue(100)
        _APP.processEvents()
        x, y = self.cell_probe(1, COLUMN_COUNT)
        self.assert_close(self.pixel(self.tree.viewport(), x, y), blend(self.colors[0], 1.0, self.theme.toolbar_bg))
        self.panel._steps.plan_controls._alpha.setValue(0)
        _APP.processEvents()
        self.assert_close(self.pixel(self.tree.viewport(), x, y), plain(self.theme.toolbar_bg))

    def test_a_colour_the_user_picks_is_painted(self) -> None:
        self.m.experiment_plan.set_color(0, "#00ff00")
        _pump(lambda: False, 0.1)
        _APP.processEvents()
        x, y = self.cell_probe(1, COLUMN_COUNT)
        self.assert_close(self.pixel(self.tree.viewport(), x, y), blend("#00ff00", DEFAULT_ALPHA, self.theme.toolbar_bg))

    def test_a_selected_row_still_shows_it_is_selected_over_the_tint(self) -> None:
        x, y = self.cell_probe(1, COLUMN_COUNT)
        plain = self.pixel(self.tree.viewport(), x, y)
        index = self.model.roi_index(1, COLUMN_ID)
        self.tree.selectionModel().select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        _APP.processEvents()
        selected = self.pixel(self.tree.viewport(), x, y)
        self.assertGreater(max(abs(a - b) for a, b in zip(plain, selected)), 6, "selecting the row changed nothing in a tinted cell")


class ReadableNumbersTest(unittest.TestCase):
    """The numbers' colour is chosen as the bar chooses its labels' colour: on the blended fill, so any step colour and any opacity stays readable."""

    BASE = QColor("#1e1e1e")

    @staticmethod
    def tint(color: str, alpha: float) -> QColor:
        out = QColor(color)
        out.setAlphaF(alpha)
        return out

    def test_dark_text_on_a_light_fill_and_light_text_on_a_dark_one(self) -> None:
        light = tinted_text_color(self.tint("#ffee88", 1.0), self.BASE)
        dark = tinted_text_color(self.tint("#10203a", 1.0), self.BASE)
        self.assertLess(light.lightness(), 60)
        self.assertGreater(dark.lightness(), 200)

    def test_a_faint_fill_leaves_the_light_text_of_the_dark_table(self) -> None:
        self.assertGreater(tinted_text_color(self.tint("#ffee88", 0.1), self.BASE).lightness(), 200)

    def test_an_out_of_date_number_is_the_same_colour_fainter(self) -> None:
        fill = self.tint("#ffee88", 1.0)
        sure, stale = tinted_text_color(fill, self.BASE), tinted_text_color(fill, self.BASE, stale=True)
        self.assertEqual(sure.alpha(), 255)
        self.assertLess(stale.alpha(), 255)
        self.assertEqual(stale.rgb(), sure.rgb())


if __name__ == "__main__":
    unittest.main()
