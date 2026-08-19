"""Regression test: RoiArrayGroup.member_area_roi_ids (the persisted grid
"recipe" tying together AreaRoi members stamped as a periodic array) must
stay in sync with the actual ROI ids after ROIs are removed or reordered.

AreaRoi.area_roi_id values are sequential integers reassigned 1..N on every
removal/reorder (main_window.py's _reindex_detected_rois /
_reorder_rois_by_position). Both already remap AreaRoiGroup.area_roi_ids the
same way, but never touched RoiArrayGroup.member_area_roi_ids - so a stale
membership list could end up silently referencing a *different* ROI that now
happens to hold that renumbered id, or a removed one.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi, RoiArrayGroup  # noqa: E402
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


def _make_array_group(member_ids: list[int]) -> RoiArrayGroup:
    return RoiArrayGroup(
        array_id="array-1", label="Grid", rows=1, cols=len(member_ids),
        spacing_x_px=20.0, spacing_y_px=20.0, anchor_x_px=0.0, anchor_y_px=0.0,
        member_area_roi_ids=member_ids,
    )


class TestRoiArrayMembershipReindex(unittest.TestCase):
    def test_removing_a_roi_remaps_surviving_array_members_and_drops_the_removed_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                rois = [
                    AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0),
                    AreaRoi(area_roi_id=2, center_x=30.0, center_y=10.0, sample_radius_px=5.0),
                    AreaRoi(area_roi_id=3, center_x=50.0, center_y=10.0, sample_radius_px=5.0),
                ]
                window._state.area_rois = rois
                window._state.area_roi_arrays = [_make_array_group([1, 2, 3])]

                window._selected_roi_ids = {2}
                window._remove_selected_rois()

                # ROI 2 is gone; the survivors (old ids 1, 3) are renumbered
                # to 1, 2 in position order - member_area_roi_ids must track
                # the same remap, not keep referencing the old numbering.
                remaining_ids = sorted(roi.area_roi_id for roi in window._state.area_rois)
                self.assertEqual(remaining_ids, [1, 2])
                self.assertEqual(len(window._state.area_roi_arrays), 1)
                self.assertEqual(window._state.area_roi_arrays[0].member_area_roi_ids, [1, 2])

    def test_array_group_is_dropped_once_all_its_members_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                rois = [
                    AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0),
                    AreaRoi(area_roi_id=2, center_x=30.0, center_y=10.0, sample_radius_px=5.0),
                ]
                window._state.area_rois = rois
                window._state.area_roi_arrays = [_make_array_group([1, 2])]

                window._selected_roi_ids = {1, 2}
                window._remove_selected_rois()

                self.assertEqual(window._state.area_roi_arrays, [])

    def test_reordering_by_position_remaps_array_members_preserving_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                # Deliberately out of left-to-right position order relative
                # to their ids, so reordering actually changes the mapping.
                rois = [
                    AreaRoi(area_roi_id=1, center_x=50.0, center_y=10.0, sample_radius_px=5.0),
                    AreaRoi(area_roi_id=2, center_x=10.0, center_y=10.0, sample_radius_px=5.0),
                    AreaRoi(area_roi_id=3, center_x=30.0, center_y=10.0, sample_radius_px=5.0),
                ]
                window._state.area_rois = rois
                # Grid recipe order matters (row-major) - this must not be
                # silently sorted/deduped by the reorder fix.
                window._state.area_roi_arrays = [_make_array_group([3, 1, 2])]

                window._reorder_rois_by_position()

                # After reordering by x position: old id 2 (x=10) -> new id 1,
                # old id 3 (x=30) -> new id 2, old id 1 (x=50) -> new id 3.
                # Reconstruct the expected remap from the now-authoritative
                # window state instead of hardcoding it, so this test only
                # depends on "the mapping used elsewhere is applied here too".
                expected_map = {}
                for new_roi in window._state.area_rois:
                    for old_id, old_roi in enumerate(rois, start=1):
                        if new_roi.center_x == old_roi.center_x and new_roi.center_y == old_roi.center_y:
                            expected_map[old_id] = new_roi.area_roi_id
                expected_members = [expected_map[old_id] for old_id in [3, 1, 2]]
                self.assertEqual(window._state.area_roi_arrays[0].member_area_roi_ids, expected_members)

    def test_reordering_by_column_numbers_top_to_bottom_within_each_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                # A 2x2 grid, ids scrambled relative to position, so
                # column-major numbering (top-to-bottom within a column,
                # left column before right) actually changes the mapping and
                # differs from what row-major would produce.
                rois = [
                    AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=5.0),  # bottom-right
                    AreaRoi(area_roi_id=2, center_x=10.0, center_y=50.0, sample_radius_px=5.0),  # bottom-left
                    AreaRoi(area_roi_id=3, center_x=50.0, center_y=10.0, sample_radius_px=5.0),  # top-right
                    AreaRoi(area_roi_id=4, center_x=10.0, center_y=10.0, sample_radius_px=5.0),  # top-left
                ]
                window._state.area_rois = rois
                window._state.area_roi_settings.array_rows = 2
                window._state.area_roi_settings.array_cols = 2

                window._reorder_rois_by_position(column_major=True)

                by_position = {(roi.center_x, roi.center_y): roi.area_roi_id for roi in window._state.area_rois}
                self.assertEqual(by_position[(10.0, 10.0)], 1)  # left column, top
                self.assertEqual(by_position[(10.0, 50.0)], 2)  # left column, bottom
                self.assertEqual(by_position[(50.0, 10.0)], 3)  # right column, top
                self.assertEqual(by_position[(50.0, 50.0)], 4)  # right column, bottom


if __name__ == "__main__":
    unittest.main()
