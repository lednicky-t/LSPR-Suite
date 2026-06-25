from __future__ import annotations

import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from lspr_app.gui.experiment_control_window import PumpPlanTimelineWidget


class ExperimentControlTimelineFontTests(unittest.TestCase):
    def test_scaled_font_falls_back_to_positive_point_size(self) -> None:
        app = QApplication.instance() or QApplication([])
        widget = PumpPlanTimelineWidget()
        font = widget._scaled_font(QFont())
        self.assertGreater(font.pointSizeF(), 0.0)
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
