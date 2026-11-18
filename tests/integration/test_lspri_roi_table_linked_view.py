"""The ROI / Groups table's Linked view (plan phase 3, apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

Pure part: `rows.build_linked_sections` (one section per sample that has linked references, headed by what it is measured against; the sample's row first, then
its references'; "Not linked" for the rest). Panel part: the view menu's switch, the arrangement in the model, read-only (no drag, no drop), header
tooltips and blank step cells, the refresh after Link / Unlink, and the remembered switch.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_model import AreaRoi, AreaRoiDetectionSettings
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Circle
    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.model import KIND_ROLE
    from lspr_imaging_app.panels.roi_table.rows import LINKED_NOT_LINKED_KEY, build_linked_sections, build_roi_rows
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _rois() -> list[AreaRoi]:
    return [
        AreaRoi(1, 10.0, 10.0, Circle(8.0), reference_roi_ids=(4, 5)),
        AreaRoi(2, 20.0, 10.0, Circle(8.0), reference_roi_ids=(4,)),
        AreaRoi(3, 30.0, 10.0, Circle(8.0)),
        AreaRoi(4, 40.0, 10.0, Circle(8.0), role="reference"),
        AreaRoi(5, 50.0, 10.0, Circle(8.0), role="reference"),
        AreaRoi(6, 60.0, 10.0, Circle(8.0), role="reference"),  # used by nobody
        AreaRoi(7, 70.0, 10.0, Circle(8.0), border=NO_BORDER),
    ]


class LinkedSectionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rois = _rois()
        self.rows = build_roi_rows(self.rois, [], AreaRoiDetectionSettings())
        self.sections = build_linked_sections(self.rows, self.rois)

    def test_one_section_per_linked_sample_with_the_sample_first_then_its_references(self) -> None:
        ids = [[row.roi_id for row in section.rows] for section in self.sections]
        self.assertEqual(ids, [[1, 4, 5], [2, 4], [3, 6, 7]])
        self.assertEqual([s.group.name for s in self.sections[:2]], ["1 measured against R4, R5", "2 measured against R4"])

    def test_the_rest_is_under_not_linked_and_every_roi_is_shown(self) -> None:
        last = self.sections[-1]
        self.assertEqual(last.group.collapse_key, LINKED_NOT_LINKED_KEY)
        self.assertEqual(last.group.name, "Not linked")
        self.assertEqual([row.roi_id for row in last.rows], [3, 6, 7])  # an own-border sample, an unused reference, a sample with nothing
        shown = {row.roi_id for section in self.sections for row in section.rows}
        self.assertEqual(shown, {1, 2, 3, 4, 5, 6, 7})

    def test_a_shared_reference_appears_in_each_of_its_samples_sections(self) -> None:
        self.assertEqual([[r.roi_id for r in s.rows].count(4) for s in self.sections[:2]], [1, 1])

    def test_headers_are_read_only_labels(self) -> None:
        for section in self.sections:
            self.assertIsNone(section.group.group_id)
            self.assertTrue(section.group.synthetic)
            self.assertTrue(section.group.note)
        self.assertEqual(len({s.group.collapse_key for s in self.sections}), len(self.sections))  # each remembers its own expansion

    def test_a_link_to_a_roi_that_is_not_a_reference_is_not_shown(self) -> None:
        rois = [AreaRoi(1, 1.0, 1.0, Circle(8.0), reference_roi_ids=(2,)), AreaRoi(2, 9.0, 1.0, Circle(8.0))]
        sections = build_linked_sections(build_roi_rows(rois, [], AreaRoiDetectionSettings()), rois)
        self.assertEqual([[r.roi_id for r in s.rows] for s in sections], [[1, 2]])
        self.assertEqual(sections[0].group.name, "Not linked")

    def test_no_rois_no_sections(self) -> None:
        self.assertEqual(build_linked_sections([], []), [])


class LinkedViewPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.toolbox.roi_ids_renumbered.connect(self.selection.remap_roi_ids)
        for x in (10.0, 20.0, 30.0, 40.0, 50.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.toolbox.set_roi_role([4, 5], "reference")
        self.toolbox.link_references([1, 2], [4])
        self.panel = RoiTablePanel(self.toolbox, self.selection, GeometryModule())
        self.model = self.panel._model
        self.panel.refresh_now()
        undo_manager.clear()

    def tearDown(self) -> None:
        undo_manager.clear()
        self.panel.deleteLater()

    def header_names(self) -> list[str]:
        return [self.model.index(i, 0).data(Qt.ItemDataRole.DisplayRole) for i in range(self.model.rowCount())]

    def switch(self, on: bool) -> None:
        self.panel._linked_action.setChecked(on)
        _APP.processEvents()

    def test_the_view_menu_has_the_switch_and_it_arranges_the_table_by_links(self) -> None:
        self.assertFalse(self.panel._linked_action.isChecked())
        self.assertFalse(self.model.is_linked_view())
        self.switch(True)
        self.assertTrue(self.model.is_linked_view())
        self.assertEqual(self.header_names(), ["1 measured against R4", "2 measured against R4", "Not linked"])
        self.assertFalse(self.model.is_grouped_view())  # its headers are not groups (row movement and the like treat the table as a flat list)
        self.switch(False)
        self.assertEqual(self.model.rowCount(), 5)  # the flat list again

    def test_a_row_under_a_header_selects_only_itself_and_a_shared_reference_is_found_in_both_places(self) -> None:
        self.switch(True)
        indexes = self.model.roi_indexes({4})
        self.assertEqual(len(indexes), 2)  # R4 sits under sample 1 and under sample 2

    def test_the_headers_are_read_only_and_nothing_is_dragged_or_dropped(self) -> None:
        self.switch(True)
        header = self.model.index(0, 0)
        self.assertEqual(self.model.data(header, KIND_ROLE), "group")
        flags = self.model.flags(header)
        self.assertFalse(flags & Qt.ItemFlag.ItemIsDropEnabled)
        self.assertFalse(flags & Qt.ItemFlag.ItemIsEditable)
        row = self.model.index(0, 0, header)
        self.assertFalse(self.model.flags(row) & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertIsNone(self.model.drop_target(0, header))
        self.switch(False)
        header = self.model.index(0, 0)
        self.assertTrue(self.model.flags(self.model.index(0, 0)) & Qt.ItemFlag.ItemIsDragEnabled)
        del header

    def test_a_header_has_a_tooltip_and_no_step_numbers(self) -> None:
        self.switch(True)
        header = self.model.index(0, 0)
        self.assertIn("linked to", self.model.data(header, Qt.ItemDataRole.ToolTipRole))
        self.assertEqual(self.model.data(header, Qt.ItemDataRole.DisplayRole), "1 measured against R4")
        # a step column cell of a header is blank (`StepCells` is not even asked: the "Ungrouped" numbers would be wrong there)
        self.assertIsNone(self.model._step_cell(self.model.index(0, 0).internalPointer(), 8, Qt.ItemDataRole.DisplayRole))

    def test_linking_and_unlinking_refresh_the_table_and_its_tooltips(self) -> None:
        self.switch(True)
        self.toolbox.link_references([3], [5])
        self.assertTrue(self._wait(lambda: "3 measured against R5" in self.header_names()))
        self.toolbox.unlink_references([1, 2])
        self.assertTrue(self._wait(lambda: self.header_names() == ["3 measured against R5", "Not linked"]))
        self.switch(False)
        row = self.model.roi_index(5, 0)
        self.assertIn("used by sample 3", self.model.data(row, Qt.ItemDataRole.ToolTipRole))

    @staticmethod
    def _wait(condition, seconds: float = 5.0) -> bool:
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            _APP.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_the_switch_is_remembered(self) -> None:
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        self.switch(True)
        self.assertIs(store.get("roi_table/linked_view"), True)
        other = RoiTablePanel(self.toolbox, self.selection, GeometryModule())
        other.refresh_now()
        other.restore_ui_state(store)
        self.assertTrue(other._model.is_linked_view())
        self.assertTrue(other._linked_action.isChecked())
        other.deleteLater()

    def test_a_collapsed_linked_header_is_remembered_under_its_own_key(self) -> None:
        self.switch(True)
        header = self.model.index(0, 0)
        self.assertEqual(self.panel._group_key_of(header), "linked:1")
        self.assertEqual(self.panel._group_key_of(self.model.index(2, 0)), LINKED_NOT_LINKED_KEY)


if __name__ == "__main__":
    unittest.main()
