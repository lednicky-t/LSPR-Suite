from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtGui import QColor

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
    _sensorgram_group_buckets actually reads, so the group-partitioning
    logic can be tested without constructing a real MainWindow."""

    def __init__(self) -> None:
        self._state = _FakeState()

    def _group_for_roi(self, roi_id: int) -> AreaRoiGroup | None:
        for group in self._state.area_roi_groups:
            if roi_id in group.area_roi_ids:
                return group
        return None


class TestSensorgramGroupBuckets(unittest.TestCase):
    """AnalysisController._sensorgram_group_buckets: partitions a multi-ROI
    selection into (label, color, member_ids) buckets for the "Average by
    group" Sensogram display mode - one bucket per group actually
    represented in the selection (using only that group's *selected*
    members), plus one more for any selected ROIs that aren't in a group at
    all. See apps/LSPRi/eva/docs/analysis_pipeline_layers.md."""

    def _make_controller(self) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow()
        return AnalysisController(window), window

    def test_empty_selection_returns_no_buckets(self) -> None:
        controller, _window = self._make_controller()
        self.assertEqual(controller._sensorgram_group_buckets(()), [])

    def test_all_ungrouped_returns_one_ungrouped_bucket(self) -> None:
        controller, _window = self._make_controller()
        buckets = controller._sensorgram_group_buckets((1, 2, 3))
        self.assertEqual(len(buckets), 1)
        label, color, member_ids = buckets[0]
        self.assertEqual(label, "Ungrouped")
        self.assertEqual(QColor(color).name(), QColor("#38bdf8").name())
        self.assertEqual(member_ids, [1, 2, 3])

    def test_partitions_into_group_and_ungrouped_buckets(self) -> None:
        controller, window = self._make_controller()
        window._state.area_roi_groups = [
            AreaRoiGroup(group_id="g1", name="Group 1", sample_color_hex="#ff0000", area_roi_ids=[1, 2]),
            AreaRoiGroup(group_id="g2", name="Group 2", sample_color_hex="#00ff00", area_roi_ids=[3, 4]),
        ]
        buckets = controller._sensorgram_group_buckets((1, 2, 3, 5))
        self.assertEqual([b[0] for b in buckets], ["Group 1", "Group 2", "Ungrouped"])
        self.assertEqual(buckets[0][2], [1, 2])
        self.assertEqual(QColor(buckets[0][1]).name(), QColor("#ff0000").name())
        self.assertEqual(buckets[1][2], [3])
        self.assertEqual(QColor(buckets[1][1]).name(), QColor("#00ff00").name())
        self.assertEqual(buckets[2][2], [5])

    def test_only_includes_selected_members_of_a_group(self) -> None:
        controller, window = self._make_controller()
        window._state.area_roi_groups = [
            AreaRoiGroup(group_id="g1", name="Group 1", area_roi_ids=[1, 2, 3]),
        ]
        buckets = controller._sensorgram_group_buckets((1,))
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0][2], [1])

    def test_bucket_order_follows_area_roi_groups_order(self) -> None:
        controller, window = self._make_controller()
        window._state.area_roi_groups = [
            AreaRoiGroup(group_id="g2", name="Second", area_roi_ids=[3]),
            AreaRoiGroup(group_id="g1", name="First", area_roi_ids=[1]),
        ]
        buckets = controller._sensorgram_group_buckets((1, 3))
        self.assertEqual([b[0] for b in buckets], ["Second", "First"])

    def test_selection_not_matching_any_group_falls_into_ungrouped(self) -> None:
        controller, window = self._make_controller()
        window._state.area_roi_groups = [AreaRoiGroup(group_id="g1", name="G", area_roi_ids=[9])]
        # None of the selected ids (1, 2) belong to the one existing group,
        # so they all land in a single "Ungrouped" bucket rather than being
        # silently dropped.
        buckets = controller._sensorgram_group_buckets((1, 2))
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0][0], "Ungrouped")
        self.assertEqual(buckets[0][2], [1, 2])


class TestMemberTraceAligned(unittest.TestCase):
    """AnalysisController._member_trace_aligned reindexes one ROI's own
    (possibly sparse) result onto the requested full spectral-cube list, so
    every ROI's array is the same length/order for aggregate_group_traces
    regardless of which frames that ROI has. Uses metric_values (the fitted
    sensogram number itself) - not metric_signal, which is NaN whenever a
    trace is reconstructed from the HDF5 backup (see
    _sensorgram_result_from_disk_backup)."""

    def test_reindexes_onto_full_cube_list_with_nan_gaps(self) -> None:
        result = SensorgramComputationResult(
            spectral_cube_indices=np.array([0, 2, 3], dtype=np.int32),
            metric_values=np.array([10.0, 20.0, 30.0]),
            metric_signal=np.array([1.0, 2.0, 3.0]),
            completed_count=3,
            total_count=3,
        )
        aligned = AnalysisController._member_trace_aligned(result, [0, 1, 2, 3])
        np.testing.assert_allclose(aligned, [10.0, np.nan, 20.0, 30.0])

    def test_exact_match_needs_no_gaps(self) -> None:
        result = SensorgramComputationResult(
            spectral_cube_indices=np.array([5, 6, 7], dtype=np.int32),
            metric_values=np.array([1.5, 2.5, 3.5]),
            metric_signal=np.array([1.0, 2.0, 3.0]),
            completed_count=3,
            total_count=3,
        )
        aligned = AnalysisController._member_trace_aligned(result, [5, 6, 7])
        np.testing.assert_allclose(aligned, [1.5, 2.5, 3.5])


if __name__ == "__main__":
    unittest.main()
