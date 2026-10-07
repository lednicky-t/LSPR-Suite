"""ROI centres are stored to 0.1 px (Options -> Round ROI Positions), and the
preference can be turned off. **Only runs on the `rewrite` branch.**"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()
APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


class RoiPositionRoundingTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()

    def test_add_and_move_store_one_decimal(self) -> None:
        roi_id = self.toolbox.add_roi(10.26, 20.84)
        roi = self.toolbox.roi_by_id(roi_id)
        self.assertEqual((roi.center_x, roi.center_y), (10.3, 20.8))
        self.toolbox.move_roi(roi_id, 5.04, 7.96)
        self.assertEqual((roi.center_x, roi.center_y), (5.0, 8.0))
        self.toolbox.translate_rois([roi_id], 0.04, 0.04)
        self.assertEqual((roi.center_x, roi.center_y), (5.0, 8.0))

    def test_preference_off_keeps_full_precision(self) -> None:
        self.toolbox.round_positions = False
        roi = self.toolbox.roi_by_id(self.toolbox.add_roi(10.26, 20.84))
        self.assertEqual((roi.center_x, roi.center_y), (10.26, 20.84))


if __name__ == "__main__":
    unittest.main()
