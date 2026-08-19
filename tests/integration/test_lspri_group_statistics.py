from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects (QColor, pyqtgraph internals) are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup
from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.gui.worker import SensorgramComputationResult


def _make_roi(roi_id: int) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=0.0, center_y=0.0, sample_radius_px=5.0)


class _FakeState:
    def __init__(self) -> None:
        self.area_rois: list[AreaRoi] = []
        self.area_roi_groups: list[AreaRoiGroup] = []


class _FakeWindow:
    """Duck-typed stand-in exposing only what
    _group_members_for_current_selection actually reads, so the group-
    detection logic can be tested without constructing a real MainWindow."""

    def __init__(self) -> None:
        self._state = _FakeState()
        self._selected_roi_ids: tuple[int, ...] = ()

    def _selected_spectrum_roi_ids(self) -> tuple[int, ...]:
        return self._selected_roi_ids

    def _group_for_roi(self, roi_id: int) -> AreaRoiGroup | None:
        for group in self._state.area_roi_groups:
            if roi_id in group.area_roi_ids:
                return group
        return None


class TestGroupMembersForCurrentSelection(unittest.TestCase):
    """AnalysisController._group_members_for_current_selection: group stats
    only apply to a single-ROI selection that belongs to a genuine
    multi-member group - covers each way that should instead return None."""

    def _make_controller(self) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow()
        return AnalysisController(window), window

    def test_single_roi_in_multi_member_group_returns_members(self) -> None:
        controller, window = self._make_controller()
        window._state.area_rois = [_make_roi(1), _make_roi(2), _make_roi(3)]
        window._state.area_roi_groups = [AreaRoiGroup(group_id="g1", name="Group 1", area_roi_ids=[1, 2, 3])]
        window._selected_roi_ids = (1,)
        result = controller._group_members_for_current_selection()
        self.assertIsNotNone(result)
        name, members = result
        self.assertEqual(name, "Group 1")
        self.assertEqual({m.area_roi_id for m in members}, {1, 2, 3})

    def test_no_selection_returns_none(self) -> None:
        controller, window = self._make_controller()
        window._selected_roi_ids = ()
        self.assertIsNone(controller._group_members_for_current_selection())

    def test_multi_roi_selection_returns_none(self) -> None:
        controller, window = self._make_controller()
        window._state.area_rois = [_make_roi(1), _make_roi(2)]
        window._state.area_roi_groups = [AreaRoiGroup(group_id="g1", name="G", area_roi_ids=[1, 2])]
        window._selected_roi_ids = (1, 2)
        self.assertIsNone(controller._group_members_for_current_selection())

    def test_roi_not_in_any_group_returns_none(self) -> None:
        controller, window = self._make_controller()
        window._state.area_rois = [_make_roi(1)]
        window._selected_roi_ids = (1,)
        self.assertIsNone(controller._group_members_for_current_selection())

    def test_single_member_group_returns_none(self) -> None:
        controller, window = self._make_controller()
        window._state.area_rois = [_make_roi(1)]
        window._state.area_roi_groups = [AreaRoiGroup(group_id="g1", name="G", area_roi_ids=[1])]
        window._selected_roi_ids = (1,)
        self.assertIsNone(controller._group_members_for_current_selection())


class TestMemberTraceAligned(unittest.TestCase):
    """AnalysisController._member_trace_aligned reindexes a member's own
    (possibly sparse) result onto the group's full requested spectral-cube
    list, so every member's array is the same length/order for
    aggregate_group_traces regardless of which frames that member has."""

    def test_reindexes_onto_full_cube_list_with_nan_gaps(self) -> None:
        result = SensorgramComputationResult(
            spectral_cube_indices=np.array([0, 2, 3], dtype=np.int32),
            metric_values=np.array([1.0, 2.0, 3.0]),
            metric_signal=np.array([10.0, 20.0, 30.0]),
            completed_count=3,
            total_count=3,
        )
        aligned = AnalysisController._member_trace_aligned(result, [0, 1, 2, 3])
        np.testing.assert_allclose(aligned, [10.0, np.nan, 20.0, 30.0])

    def test_exact_match_needs_no_gaps(self) -> None:
        result = SensorgramComputationResult(
            spectral_cube_indices=np.array([5, 6, 7], dtype=np.int32),
            metric_values=np.array([1.0, 2.0, 3.0]),
            metric_signal=np.array([1.5, 2.5, 3.5]),
            completed_count=3,
            total_count=3,
        )
        aligned = AnalysisController._member_trace_aligned(result, [5, 6, 7])
        np.testing.assert_allclose(aligned, [1.5, 2.5, 3.5])


if __name__ == "__main__":
    unittest.main()
