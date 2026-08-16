from __future__ import annotations

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

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup, RoiArrayGroup, RoiMask
from lspr_imaging_app.format_versions import ROI_EXPORT_VERSION
from lspr_imaging_app.storage.workspace import (
    ROI_TABLE_SCHEMA_NAME,
    build_roi_table_payload,
    load_roi_table,
    save_roi_table,
)


class TestRoiTableJson(unittest.TestCase):
    def test_payload_carries_schema_identity(self) -> None:
        payload = build_roi_table_payload([], [], [])
        self.assertEqual(payload["schema_name"], ROI_TABLE_SCHEMA_NAME)
        self.assertEqual(payload["schema_version"], ROI_EXPORT_VERSION)

    def test_circle_roi_round_trips(self) -> None:
        roi = AreaRoi(
            area_roi_id=1,
            center_x=12.5,
            center_y=7.0,
            sample_radius_px=5.0,
            sample_diameter_px=10.0,
            reference_inner_diameter_px=12.0,
            reference_outer_diameter_px=18.0,
            label="s1",
        )
        group = AreaRoiGroup(group_id="group_1", name="Row A", area_roi_ids=[1])
        array = RoiArrayGroup(
            array_id="array_1", label="Grid", rows=2, cols=2,
            spacing_x_px=20.0, spacing_y_px=20.0, anchor_x_px=12.5, anchor_y_px=7.0,
            member_area_roi_ids=[1],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roi_table.json"
            save_roi_table(
                path, [roi], [group], [array],
                sample_visual_color="#f59e0b", reference_visual_color="#38bdf8",
            )
            loaded_rois, loaded_groups, loaded_arrays, sample_color, reference_color = load_roi_table(path)

        self.assertEqual(len(loaded_rois), 1)
        restored = loaded_rois[0]
        self.assertEqual(restored.area_roi_id, 1)
        self.assertAlmostEqual(restored.center_x, 12.5)
        self.assertAlmostEqual(restored.center_y, 7.0)
        self.assertAlmostEqual(restored.sample_diameter_px, 10.0)
        self.assertAlmostEqual(restored.reference_inner_diameter_px, 12.0)
        self.assertAlmostEqual(restored.reference_outer_diameter_px, 18.0)
        self.assertEqual(restored.label, "s1")
        self.assertEqual(len(loaded_groups), 1)
        self.assertEqual(loaded_groups[0].name, "Row A")
        self.assertEqual(len(loaded_arrays), 1)
        self.assertEqual(loaded_arrays[0].array_id, "array_1")
        self.assertEqual(sample_color, "#f59e0b")
        self.assertEqual(reference_color, "#38bdf8")

    def test_freeform_mask_roi_round_trips(self) -> None:
        # This is exactly the case the old roi_table_*.csv format could
        # never represent - a freeform (non-circular) sample geometry.
        mask = np.zeros((6, 6), dtype=bool)
        mask[1:4, 1:4] = True
        roi = AreaRoi(
            area_roi_id=2,
            center_x=3.0,
            center_y=3.0,
            sample_radius_px=3.0,
            sample_geometry_type="mask",
            sample_mask=RoiMask(x0=1, y0=1, mask=mask[1:4, 1:4]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roi_table.json"
            save_roi_table(path, [roi])
            loaded_rois, _groups, _arrays, _sample_color, _reference_color = load_roi_table(path)

        restored = loaded_rois[0]
        self.assertEqual(restored.sample_geometry_type, "mask")
        self.assertIsNotNone(restored.sample_mask)
        np.testing.assert_array_equal(restored.sample_mask.mask, roi.sample_mask.mask)
        self.assertEqual(restored.sample_mask.x0, 1)
        self.assertEqual(restored.sample_mask.y0, 1)

    def test_empty_table_round_trips_to_empty_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roi_table.json"
            save_roi_table(path, [])
            loaded_rois, loaded_groups, loaded_arrays, sample_color, reference_color = load_roi_table(path)
        self.assertEqual(loaded_rois, [])
        self.assertEqual(loaded_groups, [])
        self.assertEqual(loaded_arrays, [])
        self.assertIsNone(sample_color)
        self.assertIsNone(reference_color)


if __name__ == "__main__":
    unittest.main()
