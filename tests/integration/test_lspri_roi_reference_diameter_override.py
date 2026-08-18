from __future__ import annotations

import sys

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects (QColor, pyqtgraph internals) are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi
from lspr_imaging_app.gui.analysis_tasks import (
    _selected_roi_masks_for_spectrum,
    compute_roi_union_bounding_box,
)

# Regression test for a bug where a per-ROI custom reference-ring diameter
# (set via the ROI table / "Edit reference ROI region" dialog, stored on
# AreaRoi.reference_inner_diameter_px / reference_outer_diameter_px) was shown
# in the UI and saved to disk, but silently ignored by the actual mask-building
# code, which only ever used the shared global default radii.

IMAGE_SHAPE = (80, 80)
GLOBAL_INNER_RADIUS_PX = 14.0
GLOBAL_OUTER_RADIUS_PX = 18.0


def _make_roi(area_roi_id: int, center: tuple[float, float], **overrides) -> AreaRoi:
    return AreaRoi(
        area_roi_id=area_roi_id,
        center_x=center[0],
        center_y=center[1],
        sample_radius_px=1.0,
        **overrides,
    )


class TestPerRoiReferenceDiameterOverride(unittest.TestCase):
    def test_roi_without_override_uses_global_default_ring(self) -> None:
        roi = _make_roi(1, (40.0, 40.0))
        _, reference_mask = _selected_roi_masks_for_spectrum(
            IMAGE_SHAPE, [roi], (1,), GLOBAL_INNER_RADIUS_PX, GLOBAL_OUTER_RADIUS_PX, None
        )
        # A point 16px out (between the global inner=14 and outer=18) is in the ring.
        self.assertTrue(reference_mask[40, 40 + 16])
        # A point 4px out (inside the global inner radius) is excluded.
        self.assertFalse(reference_mask[40, 40 + 4])

    def test_roi_with_override_uses_its_own_ring_not_global(self) -> None:
        roi = _make_roi(
            2,
            (40.0, 40.0),
            reference_inner_diameter_px=4.0,  # radius 2
            reference_outer_diameter_px=12.0,  # radius 6
        )
        _, reference_mask = _selected_roi_masks_for_spectrum(
            IMAGE_SHAPE, [roi], (2,), GLOBAL_INNER_RADIUS_PX, GLOBAL_OUTER_RADIUS_PX, None
        )
        # A point 4px out is within the override ring (inner=2, outer=6).
        self.assertTrue(reference_mask[40, 40 + 4])
        # A point 16px out would be inside the *global* ring but is well
        # outside the override outer radius of 6 - must NOT be included.
        self.assertFalse(reference_mask[40, 40 + 16])

    def test_mixed_selection_applies_each_roi_own_override_independently(self) -> None:
        default_roi = _make_roi(1, (20.0, 20.0))
        override_roi = _make_roi(
            2,
            (60.0, 60.0),
            reference_inner_diameter_px=4.0,
            reference_outer_diameter_px=12.0,
        )
        _, reference_mask = _selected_roi_masks_for_spectrum(
            IMAGE_SHAPE, [default_roi, override_roi], (1, 2), GLOBAL_INNER_RADIUS_PX, GLOBAL_OUTER_RADIUS_PX, None
        )
        # default_roi still gets the global ring.
        self.assertTrue(reference_mask[20, 20 + 16])
        self.assertFalse(reference_mask[20, 20 + 4])
        # override_roi gets its own, much smaller ring.
        self.assertTrue(reference_mask[60, 60 + 4])
        self.assertFalse(reference_mask[60, 60 + 16])

    def test_bounding_box_expands_for_a_larger_than_global_override(self) -> None:
        roi = _make_roi(
            3,
            (40.0, 40.0),
            reference_outer_diameter_px=50.0,  # radius 25, bigger than global outer=18
        )
        box = compute_roi_union_bounding_box(
            [roi], GLOBAL_OUTER_RADIUS_PX, [None], IMAGE_SHAPE[0], IMAGE_SHAPE[1], margin_px=0.0
        )
        self.assertIsNotNone(box)
        x0, y0, x1, y1 = box
        # The box must reach at least the override radius (25px) from center,
        # not just the global default (18px), or a scoped/patch read would
        # clip the actual reference ring.
        self.assertLessEqual(x0, 40 - 25)
        self.assertGreaterEqual(x1, 40 + 25)


if __name__ == "__main__":
    unittest.main()
