"""What the ROI / Groups table says about reference ROIs, links and the new shapes (phase 3 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

A reference ROI is "R7" in the first column; the first column's tooltip says what the ROI is measured against (a sample) or which samples use it (a reference);
the Shape cell says "Rectangle" and its tooltip gives the size. Row builder (pure) and the model's cells.
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
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Circle, MaskShape, Rectangle, RoiMask
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_ID, COLUMN_SHAPE, SHAPE_RECTANGLE, build_roi_rows
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def _rois() -> list[AreaRoi]:
    return [
        AreaRoi(1, 10.0, 10.0, Circle(8.0), reference_roi_ids=(3, 4)),
        AreaRoi(2, 30.0, 10.0, Circle(8.0), border=NO_BORDER),
        AreaRoi(3, 50.0, 10.0, MaskShape(RoiMask(48, 8, np.ones((3, 4), dtype=bool))), role="reference"),
        AreaRoi(4, 70.0, 10.0, Rectangle(12.0, 5.0, 30.0), role="reference"),
        AreaRoi(5, 90.0, 10.0, Rectangle(12.0, 5.0, 0.0)),
        AreaRoi(6, 90.0, 40.0, Circle(8.0), reference_roi_ids=(4,)),
    ]


class RowsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = {row.roi_id: row for row in build_roi_rows(_rois(), [], AreaRoiDetectionSettings())}

    def test_the_role_is_on_the_row(self) -> None:
        self.assertEqual([self.rows[i].role for i in range(1, 7)], ["sample", "sample", "reference", "reference", "sample", "sample"])

    def test_a_sample_says_what_it_is_measured_against(self) -> None:
        self.assertEqual(self.rows[1].link_note, "Measured against references (pooled by pixel) R3, R4.")
        self.assertEqual(self.rows[6].link_note, "Measured against reference R4.")
        self.assertEqual(self.rows[5].link_note, "Measured against its own rim.")  # follows the default border
        self.assertEqual(self.rows[2].link_note, "No reference: its results are missing.")

    def test_a_reference_says_which_samples_use_it(self) -> None:
        self.assertEqual(self.rows[3].link_note, "Reference ROI, used by sample 1.")
        self.assertEqual(self.rows[4].link_note, "Reference ROI, used by samples 1, 6.")

    def test_an_unused_reference_says_so(self) -> None:
        rows = build_roi_rows([AreaRoi(1, 1.0, 1.0, Circle(8.0), role="reference")], [], AreaRoiDetectionSettings())
        self.assertEqual(rows[0].link_note, "Reference ROI: not linked to any sample yet.")

    def test_a_link_to_a_roi_that_is_not_a_reference_is_not_claimed(self) -> None:
        rows = build_roi_rows([AreaRoi(1, 1.0, 1.0, Circle(8.0), reference_roi_ids=(2,)), AreaRoi(2, 9.0, 1.0, Circle(8.0))], [], AreaRoiDetectionSettings())
        self.assertEqual(rows[0].link_note, "Measured against its own rim.")

    def test_a_rectangle_and_a_mask_have_a_shape_note(self) -> None:
        self.assertEqual(self.rows[4].shape, SHAPE_RECTANGLE)
        self.assertEqual(self.rows[4].shape_note, "12 x 5 px, turned 30 degrees clockwise.")
        self.assertEqual(self.rows[3].shape_note, "12 pixels.")
        self.assertEqual(self.rows[1].shape_note, "")


class ModelCellsTest(unittest.TestCase):
    def setUp(self) -> None:
        from lspr_imaging_app.panels.roi_table.model import RoiTreeModel
        from lspr_imaging_app.panels.roi_table.rows import PIXELS

        self.model = RoiTreeModel()
        self.model.set_content(_rois(), [], AreaRoiDetectionSettings(), PIXELS)

    def _cell(self, roi_id: int, column: int, role: int):
        """The cell of ROI ``roi_id`` (no groups: the ROIs are the top-level rows, in the toolbox's order)."""
        row = [r.roi_id for r in self.model.rows()].index(roi_id)
        return self.model.index(row, column).data(role)

    def test_the_first_column_shows_r_for_a_reference_and_the_plain_number_for_a_sample(self) -> None:
        self.assertEqual(self._cell(1, COLUMN_ID, Qt.ItemDataRole.DisplayRole), "1")
        self.assertEqual(self._cell(3, COLUMN_ID, Qt.ItemDataRole.DisplayRole), "R3")
        self.assertEqual(self._cell(3, COLUMN_ID, Qt.ItemDataRole.EditRole), "3")  # editing starts from the number

    def test_the_first_column_tooltip_and_the_shape_cell(self) -> None:
        self.assertIn("used by", self._cell(3, COLUMN_ID, Qt.ItemDataRole.ToolTipRole))
        self.assertEqual(self._cell(4, COLUMN_SHAPE, Qt.ItemDataRole.DisplayRole), "Rectangle")  # a reference: the plain shape
        self.assertEqual(
            self._cell(4, COLUMN_SHAPE, Qt.ItemDataRole.ToolTipRole), "Rectangle. Reference ROI, used by samples 1, 6. 12 x 5 px, turned 30 degrees clockwise."
        )


if __name__ == "__main__":
    unittest.main()
