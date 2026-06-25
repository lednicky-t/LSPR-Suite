from __future__ import annotations

import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.pump_plan import PumpChannelStep, PumpPlanStep
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


class ExperimentControlLiveEditingTests(unittest.TestCase):
    def test_active_step_change_summary_includes_device_fields(self) -> None:
        previous = PumpPlanStep(
            step=2,
            duration_s=12.0,
            color="#123456",
            valve="load",
            switch_position=3,
            description="Old comment",
            channels=[
                PumpChannelStep(flow_ul_min=10.0, direction="CW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=5.0, direction="CCW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            ],
        )
        updated = PumpPlanStep(
            step=2,
            duration_s=18.0,
            color="#654321",
            valve="measure",
            switch_position=4,
            description="New comment",
            channels=[
                PumpChannelStep(flow_ul_min=14.0, direction="CCW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=5.0, direction="CCW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            ],
        )

        summary = ExperimentControlWindow._experiment_control_step_change_summary(None, previous, updated)

        self.assertIn("duration 12 -> 18", summary)
        self.assertIn("color #123456 -> #654321", summary)
        self.assertIn("valve load -> measure", summary)
        self.assertIn("switch 3 -> 4", summary)
        self.assertIn("comment Old comment -> New comment", summary)
        self.assertIn("CH1 flow 10 -> 14", summary)
        self.assertIn("CH1 dir CW -> CCW", summary)


if __name__ == "__main__":
    unittest.main()
