from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiDetectionSettings, PreprocessingSettings, RoiMask
from lspr_imaging_app.domain.roi_editor_tools import build_grid_positions, build_roi_array_group
from lspr_imaging_app.storage.workspace import build_processing_profile_payload, load_processing_profile, write_json_file


class TestBuildRoiArrayGroup(unittest.TestCase):
    def test_matches_build_grid_positions_row_major_order(self) -> None:
        # The array group's origin is the row=0, col=0 member, and its member
        # ids must line up with build_grid_positions' row-major iteration.
        positions = build_grid_positions(rows=2, cols=3, spacing_x=10.0, anchor_center_x=100.0, anchor_center_y=50.0)
        origin_x, origin_y = positions[0]
        member_ids = list(range(1, len(positions) + 1))
        group = build_roi_array_group(
            array_id="array_1",
            label="Test array",
            rows=2,
            cols=3,
            spacing_x=10.0,
            origin_x=origin_x,
            origin_y=origin_y,
            member_area_roi_ids=member_ids,
        )
        self.assertEqual(group.rows, 2)
        self.assertEqual(group.cols, 3)
        self.assertEqual(group.spacing_x_px, 10.0)
        self.assertEqual(group.spacing_y_px, 10.0)
        self.assertEqual((group.anchor_x_px, group.anchor_y_px), positions[0])
        self.assertEqual(group.member_area_roi_ids, member_ids)


class TestWorkspaceRoundTrip(unittest.TestCase):
    def _round_trip(self, area_rois, area_roi_arrays):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            payload = build_processing_profile_payload(
                PreprocessingSettings(),
                AreaRoiDetectionSettings(),
                area_rois,
                area_roi_arrays=area_roi_arrays,
            )
            write_json_file(path, payload)
            loaded = load_processing_profile(path)
        return loaded

    def test_array_group_round_trips(self) -> None:
        area_rois = [AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0, array_id="array_1")]
        array_group = build_roi_array_group(
            array_id="array_1",
            label="Detected array",
            rows=1,
            cols=1,
            spacing_x=20.0,
            origin_x=10.0,
            origin_y=10.0,
            member_area_roi_ids=[1],
        )
        loaded = self._round_trip(area_rois, [array_group])

        loaded_area_rois = loaded[2]
        loaded_area_roi_arrays = loaded[11]
        self.assertEqual(len(loaded_area_roi_arrays), 1)
        restored_group = loaded_area_roi_arrays[0]
        self.assertEqual(restored_group.array_id, "array_1")
        self.assertEqual(restored_group.rows, 1)
        self.assertEqual(restored_group.cols, 1)
        self.assertEqual(restored_group.member_area_roi_ids, [1])
        self.assertEqual(loaded_area_rois[0].array_id, "array_1")

    def test_legacy_file_without_area_roi_arrays_key_loads_with_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            payload = build_processing_profile_payload(
                PreprocessingSettings(),
                AreaRoiDetectionSettings(),
                [AreaRoi(area_roi_id=1, center_x=1.0, center_y=1.0, sample_radius_px=2.0)],
            )
            # Simulate a pre-existing file saved before this feature existed.
            del payload["area_roi_arrays"]
            write_json_file(path, payload)
            loaded = load_processing_profile(path)
        self.assertEqual(loaded[11], [])
        # And the AreaRoi geometry fields fall back to defaults that reproduce
        # the exact pre-existing circle/annulus behavior.
        roi = loaded[2][0]
        self.assertEqual(roi.sample_geometry_type, "circle")
        self.assertEqual(roi.reference_geometry_type, "annulus")
        self.assertIsNone(roi.sample_mask)
        self.assertIsNone(roi.array_id)

    def test_mask_geometry_area_roi_round_trips(self) -> None:
        mask = np.zeros((20, 20), dtype=bool)
        mask[5:9, 5:9] = True
        roi = AreaRoi(
            area_roi_id=1,
            center_x=7.0,
            center_y=7.0,
            sample_radius_px=5.0,
            sample_geometry_type="mask",
            sample_mask=RoiMask(x0=5, y0=5, mask=mask[5:9, 5:9]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            payload = build_processing_profile_payload(
                PreprocessingSettings(),
                AreaRoiDetectionSettings(),
                [roi],
            )
            # A raw json.dumps must succeed -- this is what would previously
            # blow up on an ndarray if the mask weren't bitpack-encoded first.
            json.dumps(payload)
            write_json_file(path, payload)
            loaded = load_processing_profile(path)
        restored_roi = loaded[2][0]
        self.assertEqual(restored_roi.sample_geometry_type, "mask")
        self.assertIsNotNone(restored_roi.sample_mask)
        self.assertEqual((restored_roi.sample_mask.x0, restored_roi.sample_mask.y0), (5, 5))
        np.testing.assert_array_equal(restored_roi.sample_mask.mask, mask[5:9, 5:9])


if __name__ == "__main__":
    unittest.main()
