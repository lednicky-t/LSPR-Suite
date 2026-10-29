"""Image panel ROIs tab, "Groups" section: create a group, group by rows / by columns (controls -> toolbox)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app.panels.image.group_actions import GroupActions
    from lspr_imaging_app.panels.image.group_controls import GroupControls
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as error:  # pragma: no cover
    skip_if_lspri_rewrite_absent(error)
    raise

_APP = QApplication.instance() or QApplication([])


class GroupActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.controls = GroupControls()
        self.names: list[str | None] = []
        self.told: list[str] = []
        self.statuses: list[str] = []
        self.actions = GroupActions(
            self.controls,
            toolbox=self.toolbox,
            selection=self.selection,
            ask_name=lambda title, default: self.names.pop(0) if self.names else default,
            tell=lambda title, text: self.told.append(text),
        )
        self.actions.status.connect(self.statuses.append)
        # 2 rows x 3 columns, added in scrambled order
        for x, y in [(60, 10), (10, 50), (10, 10), (60, 50), (35, 10), (35, 50)]:
            self.toolbox.add_roi(float(x), float(y), sample_diameter_px=10.0)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _group_spots(self) -> list[set[tuple[float, float]]]:
        spots = {r.area_roi_id: (r.center_x, r.center_y) for r in self.toolbox.rois()}
        return [{spots[i] for i in g.area_roi_ids} for g in self.toolbox.groups()]

    def test_group_by_rows_makes_one_group_per_row_with_distinct_colours_and_one_undo(self) -> None:
        self.controls.by_rows_requested.emit()
        groups = self.toolbox.groups()
        self.assertEqual([g.name for g in groups], ["Row 1", "Row 2"])
        self.assertEqual(self._group_spots(), [{(10, 10), (35, 10), (60, 10)}, {(10, 50), (35, 50), (60, 50)}])
        self.assertNotEqual(groups[0].sample_color_hex, groups[1].sample_color_hex)
        undo_manager.undo()
        self.assertEqual(self.toolbox.groups(), ())

    def test_group_by_columns_makes_one_group_per_column(self) -> None:
        self.controls.by_columns_requested.emit()
        self.assertEqual([g.name for g in self.toolbox.groups()], ["Column 1", "Column 2", "Column 3"])
        self.assertEqual(self._group_spots()[1], {(35, 10), (35, 50)})

    def test_group_by_rows_uses_only_the_selection_when_there_is_one(self) -> None:
        top_row = {r.area_roi_id for r in self.toolbox.rois() if r.center_y == 10}
        self.selection.set_roi_selection(top_row)
        self.controls.by_columns_requested.emit()  # each selected ROI is alone in its column
        self.assertEqual(len(self.toolbox.groups()), 3)
        self.assertEqual({i for g in self.toolbox.groups() for i in g.area_roi_ids}, top_row)

    def test_create_group_from_selection_and_empty(self) -> None:
        self.names = ["Mine"]
        self.selection.set_roi_selection({1, 2})
        self.controls.create_requested.emit()
        self.assertEqual(self.toolbox.groups()[0].area_roi_ids, [1, 2])
        self.selection.set_roi_selection(set())
        self.names = ["Empty one"]
        self.controls.create_requested.emit()
        self.assertEqual(self.toolbox.groups()[1].area_roi_ids, [])
        self.names = [None]  # dialog cancelled: nothing happens
        self.controls.create_requested.emit()
        self.assertEqual(len(self.toolbox.groups()), 2)

    def test_ungroup_takes_the_place_of_group_when_grouped_rois_are_selected(self) -> None:
        self.names = ["Mine"]
        self.selection.set_roi_selection({1, 2})
        self.controls.create_requested.emit()
        slot = self.controls._slot
        self.assertIs(slot.currentWidget(), self.controls._buttons["ungroup"], "grouped ROIs are selected")
        self.selection.set_roi_selection({3})
        self.assertIs(slot.currentWidget(), self.controls._buttons["create"], "no grouped ROI selected: Group")
        self.selection.set_roi_selection({1})
        self.controls._buttons["ungroup"].click()  # one ROI of the group only: it leaves, the group stays
        self.assertEqual(self.toolbox.groups()[0].area_roi_ids, [2])
        self.selection.set_roi_selection({2})
        self.controls._buttons["ungroup"].click()  # the last one: the group goes away
        self.assertEqual(self.toolbox.groups(), ())
        self.assertIs(slot.currentWidget(), self.controls._buttons["create"])

if __name__ == "__main__":
    unittest.main()
