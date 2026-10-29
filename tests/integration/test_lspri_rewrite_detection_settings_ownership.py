"""`RoiToolbox` owns the detection settings: a query returns a copy, a change is one undo step, an identical value is not a change."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.roi import RoiToolbox
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class RewriteDetectionSettingsOwnershipTest(unittest.TestCase):
    """`RoiToolbox` owns `AreaRoiDetectionSettings` (since 2026-09-23): the scan and the panels read the reference radii and the
    reduction method from it. Moved here from the former analysis-engine test file (2026-10-29)."""

    def setUp(self) -> None:
        self.roi_toolbox = RoiToolbox()

    def test_query_returns_a_defensive_copy(self) -> None:
        settings = self.roi_toolbox.detection_settings()
        settings.reduction_method = "median"
        self.assertEqual(self.roi_toolbox.detection_settings().reduction_method, "mean")

    def test_set_and_undo(self) -> None:
        from lspr_imaging_app.undo import undo_manager

        self.roi_toolbox.set_detection_settings(
            replace(self.roi_toolbox.detection_settings(), reduction_method="median")
        )
        self.assertEqual(self.roi_toolbox.detection_settings().reduction_method, "median")
        undo_manager.undo()
        self.assertEqual(self.roi_toolbox.detection_settings().reduction_method, "mean")

    def test_setting_the_same_value_is_a_no_op(self) -> None:
        changes: list[str] = []
        self.roi_toolbox.geometry_changed.connect(lambda change: changes.append(change.reason))
        self.roi_toolbox.set_detection_settings(self.roi_toolbox.detection_settings())
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()
