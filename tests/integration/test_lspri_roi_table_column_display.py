"""ROI / Groups table (2026-11-26): the "Display" menu of a column (decimals, notation), and the step columns' common decimals no longer worked out again for nothing.

Pinned: a column with no setting reads exactly as before; "More / Fewer / Automatic decimals" and the three notations change that column only (a fixed column and a step column),
through the header menu and the cell menu; the setting is display only (tool tip, copied text, editor text keep their plain numbers); it is remembered by column key and comes back in
a new panel; a column that holds no number offers nothing; the common number of decimals is the exact median result, and worked out again exactly when the numbers, the display
mode, the cells or the step columns changed (also a change that came while nothing was shown).
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, Qt

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table import model as model_module
    from lspr_imaging_app.panels.roi_table.column_keys import key_to_payload
    from lspr_imaging_app.panels.roi_table.number_format import NORMAL, SCIENTIFIC, ColumnDisplay
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_ANGLE, COLUMN_COUNT, COLUMN_ID, COLUMN_NAME, COLUMN_SHAPE, COLUMN_X, COLUMN_Y
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.state import measured_ref
    from lspr_imaging_app.selection.step_table_module import StepColumn
    from lspr_imaging_app.storage.ui_state_keys import ROI_TABLE_COLUMN_DISPLAY
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_roi_table_performance import _Cells, _Grouped
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()


def texts_of(menu) -> list[str]:
    return [a.text() for a in menu.actions() if a.text() and a.isVisible()]


class DisplayBase(_Grouped):
    def setUp(self) -> None:
        super().setUp()
        self.ui = UiStateStore({})
        self.panel.restore_ui_state(self.ui)
        self.model.set_step_columns([StepColumn(measured_ref(i + 1, 1000 + i, f"S{i + 1}"), i + 1, f"step {i + 1}", "", (), "#58a6ff") for i in range(3)], _Cells())
        self.panel.resize(900, 400)
        self.panel.show()
        self.refresh()
        _APP.processEvents()
        self.display = self.panel._column_display
        self.first_step = COLUMN_COUNT

    def tearDown(self) -> None:
        self.panel.hide()
        super().tearDown()

    def entries(self, column: int) -> list[str]:
        self.display.prepare(column)
        return texts_of(self.display._menu)

    def choose(self, column: int, text_start: str) -> None:
        """Click the entry of the column's Display menu whose text starts so (the header menu is made fresh, as the panel does at every opening)."""
        self.display.prepare(column)
        actions = [a for a in self.display._menu.actions() if a.text().startswith(text_start)]
        self.assertEqual(len(actions), 1, (text_start, texts_of(self.display._menu)))
        actions[0].trigger()

    def step_text(self, roi_id: int, step: int) -> str:
        return self.text(roi_id, COLUMN_COUNT + step)


class NoSettingTest(DisplayBase):
    def test_a_column_with_no_setting_reads_as_it_always_did(self) -> None:
        self.assertEqual(self.text(1, COLUMN_X), "10.0")
        self.assertEqual(self.text(1, COLUMN_Y), "5.0")
        self.assertRegex(self.step_text(1, 0), r"^\d+\.\d+ ± \d+\.\d+$")
        self.assertTrue(all(self.model.column_display(c).is_default for c in range(self.model.columnCount())))

    def test_only_a_column_that_holds_numbers_has_a_display_menu(self) -> None:
        for column in (COLUMN_ID, COLUMN_NAME, COLUMN_SHAPE):
            self.assertEqual(self.entries(column), [], column)
            self.assertFalse(self.display._menu.menuAction().isVisible(), column)
        self.assertEqual(self.entries(-1), [])
        for column in (COLUMN_X, COLUMN_ANGLE, self.first_step):
            self.assertTrue(self.entries(column), column)
            self.assertTrue(self.display._menu.menuAction().isVisible(), column)


class DecimalsTest(DisplayBase):
    def test_more_and_fewer_decimals_change_this_column_only(self) -> None:
        self.assertEqual(self.entries(COLUMN_X)[:2], ["More decimals (1 → 2)", "Fewer decimals (1 → 0)"])
        self.choose(COLUMN_X, "More decimals")
        self.assertEqual(self.text(1, COLUMN_X), "10.00")
        self.assertEqual(self.text(1, COLUMN_Y), "5.0")  # another column: untouched
        self.choose(COLUMN_X, "More decimals")
        self.assertEqual(self.text(1, COLUMN_X), "10.000")
        self.choose(COLUMN_X, "Fewer decimals")
        self.choose(COLUMN_X, "Fewer decimals")
        self.choose(COLUMN_X, "Fewer decimals")
        self.assertEqual(self.text(1, COLUMN_X), "10")
        self.assertNotIn("Fewer", " ".join(self.entries(COLUMN_X)))  # none left to take away

    def test_automatic_decimals_is_offered_only_after_a_choice_and_gives_the_column_its_own_back(self) -> None:
        self.assertNotIn("Automatic decimals", self.entries(COLUMN_X))
        self.choose(COLUMN_X, "More decimals")
        self.assertIn("Automatic decimals", self.entries(COLUMN_X))
        self.choose(COLUMN_X, "Automatic decimals")
        self.assertEqual(self.text(1, COLUMN_X), "10.0")
        self.assertTrue(self.model.column_display(COLUMN_X).is_default)
        self.assertEqual(self.model.column_displays(), {})

    def test_the_angle_and_the_step_columns_have_their_own_decimals(self) -> None:
        before_second = self.step_text(1, 1)
        digits = len(self.step_text(1, 0).split(" ")[0].split(".")[1])
        self.choose(self.first_step, "More decimals")
        self.assertEqual(len(self.step_text(1, 0).split(" ")[0].split(".")[1]), digits + 1)
        self.assertEqual(len(self.step_text(1, 0).split(" ")[2].split(".")[1]), digits + 1)  # mean and spread alike
        self.assertEqual(self.step_text(1, 1), before_second)  # the next step column: untouched
        self.assertEqual(self.text(1, COLUMN_ANGLE), "")  # (a circle has no angle: nothing to format, nothing broken)

    def test_the_tool_tip_the_editor_and_the_copy_keep_their_plain_numbers(self) -> None:
        index = self.cell(1, COLUMN_X)
        tip, edit = index.data(Qt.ItemDataRole.ToolTipRole), index.data(Qt.ItemDataRole.EditRole)
        step_tip = self.cell(1, COLUMN_COUNT).data(Qt.ItemDataRole.ToolTipRole)
        self.choose(COLUMN_X, "More decimals")
        self.choose(COLUMN_X, "Scientific")
        self.choose(self.first_step, "Scientific")
        self.assertEqual(self.cell(1, COLUMN_X).data(Qt.ItemDataRole.ToolTipRole), tip)
        self.assertEqual(self.cell(1, COLUMN_X).data(Qt.ItemDataRole.EditRole), edit)
        self.assertEqual(self.cell(1, COLUMN_COUNT).data(Qt.ItemDataRole.ToolTipRole), step_tip)


class NotationTest(DisplayBase):
    def test_scientific_keeps_the_resolution_the_plain_number_had(self) -> None:
        self.choose(COLUMN_X, "Scientific")
        self.assertEqual(self.text(1, COLUMN_X), "1.00e+01")  # 10.0 at one decimal: 0.1 resolution
        self.assertEqual(self.text(2, COLUMN_X), "2.00e+01")
        self.assertEqual(self.text(1, COLUMN_Y), "5.0")
        self.choose(COLUMN_X, "Normal")
        self.assertEqual(self.text(1, COLUMN_X), "10.0")

    def test_decimals_and_notation_combine_and_the_checked_entry_shows_the_notation(self) -> None:
        self.choose(COLUMN_X, "Scientific")
        self.assertEqual(self.entries(COLUMN_X)[:2], ["More decimals (2 → 3)", "Fewer decimals (2 → 1)"])  # (what 1.00e+01 writes now)
        self.choose(COLUMN_X, "Fewer decimals")
        self.assertEqual(self.text(1, COLUMN_X), "1.0e+01")
        self.assertEqual(self.model.column_display(COLUMN_X), ColumnDisplay(1, SCIENTIFIC))
        self.display.prepare(COLUMN_X)
        checked = [a.text() for a in self.display._menu.actions() if a.isCheckable() and a.isChecked()]
        self.assertEqual(checked, ["Scientific"])

    def test_a_step_column_in_scientific_writes_mean_and_spread_each_with_its_exponent(self) -> None:
        self.choose(self.first_step, "Scientific")
        text = self.step_text(1, 0)
        mean, spread = text.split(" ± ")
        self.assertRegex(mean, r"^\d\.\d+e\+02$")  # ~595 nm
        self.assertRegex(spread, r"^\d(\.\d+)?e-02$")  # sd 0.05


class WhereTheMenuIsTest(DisplayBase):
    def test_the_header_menu_has_it_for_the_column_clicked(self) -> None:
        header = self.tree.header()
        x = header.sectionViewportPosition(COLUMN_X) + 3
        with mock.patch.object(self.panel._view_menu, "exec"):
            header.customContextMenuRequested.emit(QPoint(x, 5))
        self.assertTrue(self.display._menu.menuAction().isVisible())
        self.assertIn("More decimals (1 → 2)", texts_of(self.display._menu))
        x = header.sectionViewportPosition(COLUMN_SHAPE) + 3
        with mock.patch.object(self.panel._view_menu, "exec"):
            header.customContextMenuRequested.emit(QPoint(x, 5))
        self.assertFalse(self.display._menu.menuAction().isVisible())

    def test_a_number_cells_right_click_menu_has_a_display_submenu_and_other_cells_do_not(self) -> None:
        def submenu_for(column: int, roi_id: int = 1) -> list[str] | None:
            seen: list[list[str] | None] = []

            def capture(menu, *_args):
                subs = [a.menu() for a in menu.actions() if a.menu() is not None and a.text() == "Display"]
                seen.append(texts_of(subs[0]) if subs else None)

            point = self.tree.visualRect(self.cell(roi_id, column)).center()
            with mock.patch("PyQt6.QtWidgets.QMenu.exec", capture):
                self.panel._show_context_menu(point)
            return seen[0]

        sub = submenu_for(COLUMN_X)
        self.assertIsNotNone(sub)
        self.assertIn("More decimals (1 → 2)", sub)
        self.assertIsNone(submenu_for(COLUMN_SHAPE))


class RememberedTest(DisplayBase):
    def test_the_setting_is_saved_by_column_key_and_comes_back_in_a_new_panel(self) -> None:
        self.choose(COLUMN_X, "More decimals")
        self.choose(self.first_step, "Scientific")
        saved = self.ui.get(ROI_TABLE_COLUMN_DISPLAY)
        self.assertEqual(len(saved), 2)
        step_key = self.tree.column_key(self.first_step)
        self.assertIn({"key": key_to_payload(step_key), "decimals": None, "notation": SCIENTIFIC}, saved)
        self.assertIn({"key": COLUMN_X, "decimals": 2, "notation": NORMAL}, saved)
        again = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        again.restore_ui_state(self.ui)
        again._model.set_step_columns(self.model.step_columns(), _Cells())  # (the step columns come after the saved state is read, as at launch)
        again.refresh_now()
        self.assertEqual(again._model.column_display(COLUMN_X), ColumnDisplay(2, NORMAL))
        self.assertEqual(again._model.column_display(self.first_step).notation, SCIENTIFIC)
        again.deleteLater()

    def test_the_setting_follows_its_step_when_the_columns_are_renumbered(self) -> None:
        self.choose(self.first_step + 1, "Scientific")  # the second step
        steps = list(self.model.step_columns())
        self.model.set_step_columns(steps[1:], _Cells())  # the first step goes: the second is now the first column
        self.assertEqual(self.model.column_display(self.first_step).notation, SCIENTIFIC)
        self.model.set_step_columns(steps, _Cells())
        self.assertEqual(self.model.column_display(self.first_step).notation, NORMAL)
        self.assertEqual(self.model.column_display(self.first_step + 1).notation, SCIENTIFIC)

    def test_junk_in_the_saved_state_is_skipped(self) -> None:
        junk = UiStateStore({ROI_TABLE_COLUMN_DISPLAY: ["x", {"key": "nope"}, {"key": COLUMN_Y, "decimals": 99, "notation": "hex"}, {"key": 999}]})
        panel = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        panel.restore_ui_state(junk)
        self.assertEqual(panel._model.column_displays(), {COLUMN_Y: ColumnDisplay(9, NORMAL)})
        panel.deleteLater()


class CommonDecimalsTest(DisplayBase):
    """`RoiTreeModel._refresh_decimals`: the exact median result, but not recomputed for a rebuild that changed nothing it depends on."""

    def fresh(self) -> int:
        from lspr_imaging_app.panels.roi_table.step_columns import table_decimals

        return table_decimals(self.model._all_cells(), self.model.step_display())

    def calls_during(self, action) -> int:
        with mock.patch.object(model_module, "table_decimals", wraps=model_module.table_decimals) as spy:
            action()
        return spy.call_count

    def test_the_stored_number_is_what_a_fresh_calculation_gives(self) -> None:
        self.assertEqual(self.model._decimals, self.fresh())

    def test_a_rebuild_that_changed_nothing_it_depends_on_does_not_calculate_again(self) -> None:
        self.assertEqual(self.calls_during(self.refresh), 0)
        self.assertEqual(self.calls_during(lambda: self.model.set_content(self.toolbox.rois_at(0), self.toolbox.groups(), self.toolbox.detection_settings(), self.panel._unit(), None)), 0)

    def test_it_is_calculated_again_when_the_numbers_may_have_changed(self) -> None:
        self.assertEqual(self.calls_during(self.model.refresh_numbers), 1)
        self.assertEqual(self.calls_during(lambda: (self.model.mark_numbers_changed(), self.refresh())), 1)  # (a change that came while the panel was hidden)
        self.assertEqual(self.model._decimals, self.fresh())

    def test_a_change_announced_while_the_panel_is_hidden_is_not_missed(self) -> None:
        controls = self.panel._step_controls
        hidden = dataclasses.replace(controls._host, deferred=lambda: True)
        with mock.patch.object(controls, "_host", hidden):  # (the step table said "numbers changed" and the panel, hidden, did nothing with it)
            self.assertEqual(self.calls_during(controls._on_numbers_changed), 0)
        self.assertEqual(self.calls_during(self.refresh), 1)  # (the catch-up rebuild when it is shown again)

    def test_it_is_calculated_again_for_another_display_mode_other_cells_and_other_step_columns(self) -> None:
        self.assertGreaterEqual(self.calls_during(lambda: self.model.set_step_display("sem")), 1)
        self.assertEqual(self.model._decimals, self.fresh())
        self.assertEqual(self.calls_during(lambda: (self.toolbox.add_roi(300.0, 5.0, sample_diameter_px=6.0), self.refresh())), 1)  # another ROI: other cells
        self.assertEqual(self.model._decimals, self.fresh())
        steps = list(self.model.step_columns())
        self.assertEqual(self.calls_during(lambda: self.model.set_step_columns(steps[:2], _Cells())), 1)
        self.assertEqual(self.model._decimals, self.fresh())

    def test_it_follows_numbers_that_really_change(self) -> None:
        class Wide(_Cells):
            def roi_cell(self, roi_id, ref):
                cell = super().roi_cell(roi_id, ref)
                return None if cell is None else type(cell)(n=cell.n, mean=cell.mean, sd=50.0, current=cell.current, sem=15.0)

        self.model.set_step_columns(list(self.model.step_columns()), _Cells())
        narrow = self.model._decimals
        self.model.set_step_columns(list(self.model.step_columns()), Wide())  # the same columns, other numbers: set_step_columns is the step table's `columns_changed` path
        self.assertLess(self.model._decimals, narrow)
        self.assertEqual(self.model._decimals, self.fresh())


if __name__ == "__main__":
    unittest.main()
