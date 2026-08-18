"""Regression tests for two related bugs in per-ROI sample/reference diameter
editing:

1. Inline-editing the D_s/d_r/D_r diameter cells in the ROI table silently
   failed (or, for d_r/D_r, did nothing at all). The table has 9 columns:
   0=id, 1=group, 2=C_s swatch, 3=C_r swatch, 4=D_s, 5=d_r, 6=D_r, 7=x, 8=y
   (see roi_table_helpers.roi_table_headers / append_roi_table_row). The
   edit-dispatch code in main_window.py and roi_table_controller.py still
   read/matched columns {2, 3, 4} - a stale reference to an older column
   layout from before the C_s/C_r swatch columns were inserted - so editing
   D_s always failed with a parse error (columns 2/3 are icon-only, no text)
   and editing d_r/D_r had no handler at all.

2. Both diameter-editing paths (the table-cell edit above, and the
   "Sample diameter" double-click dialog) only updated `roi.sample_diameter_px`
   - the value shown in the table - but never `roi.sample_radius_px`, which is
   the field the actual absorbance/sensorgram mask-building code
   (gui/analysis_tasks.py) reads. So editing a specific ROI's sample diameter
   changed what the table displayed but had zero effect on the analysis,
   exactly the same class of bug as the per-ROI reference-ring diameter being
   ignored.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402


@contextmanager
def _open_window(folder: Path):
    """Matches test_lspri_metadata_gui.py's MainWindow construction/teardown
    convention: dataset is cleared before close() so the closeEvent's
    processing-state save is a no-op instead of racing the temp dir cleanup."""
    window = MainWindow(folder, fast_startup=True)
    try:
        yield window
    finally:
        window._state.dataset = None
        window.close()
        window.deleteLater()


class TestRoiTableDiameterInlineEdit(unittest.TestCase):
    def test_editing_each_diameter_cell_updates_the_matching_roi_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
                window._state.area_rois = [roi]
                window._roi_table_controller.update_table()
                self.assertEqual(window.roi_table.rowCount(), 1)

                # Sanity-check the layout this test (and the fix) depends on.
                self.assertEqual(window.roi_table.horizontalHeaderItem(4).text(), "D_s")
                self.assertEqual(window.roi_table.horizontalHeaderItem(5).text(), "d_r")
                self.assertEqual(window.roi_table.horizontalHeaderItem(6).text(), "D_r")

                for column, new_text, attr in (
                    (4, "12.5", "sample_diameter_px"),
                    (5, "20.0", "reference_inner_diameter_px"),
                    (6, "30.0", "reference_outer_diameter_px"),
                ):
                    item = window.roi_table.item(0, column)
                    self.assertIsNotNone(item, f"column {column} has no item")
                    item.setText(new_text)
                    window._roi_table_controller.on_item_changed(item)
                    updated = window._roi_by_id(1)
                    self.assertAlmostEqual(
                        getattr(updated, attr),
                        float(new_text),
                        msg=f"editing column {column} did not update {attr}",
                    )

                self.assertNotEqual(window.status_label.text(), "ROI diameter cells must contain numbers.")

                # sample_radius_px is what the absorbance mask-building code
                # actually reads - it must track the edited diameter, not
                # just the display-only sample_diameter_px.
                updated = window._roi_by_id(1)
                self.assertAlmostEqual(updated.sample_radius_px, 12.5 / 2.0)

    def test_sample_diameter_dialog_also_updates_sample_radius_px(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
                window._state.area_rois = [roi]

                with mock.patch(
                    "lspr_imaging_app.gui.main_window.QInputDialog.getDouble",
                    return_value=(40.0, True),
                ):
                    window._edit_roi_geometry_from_table(1)

                updated = window._roi_by_id(1)
                self.assertAlmostEqual(updated.sample_diameter_px, 40.0)
                self.assertAlmostEqual(updated.sample_radius_px, 20.0)


if __name__ == "__main__":
    unittest.main()
