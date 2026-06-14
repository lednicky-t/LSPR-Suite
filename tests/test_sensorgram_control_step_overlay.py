from __future__ import annotations

import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.sensorgram_control_step_overlay import (
    build_sensorgram_control_step_overlay_segments,
    normalize_sensorgram_control_step_overlay_color,
    normalize_sensorgram_control_step_overlay_label,
    resolve_sensorgram_control_step_overlay_palette_label,
)


class SensorgramControlStepOverlayTests(unittest.TestCase):
    def test_runtime_events_build_segments_for_jumps_and_pauses(self) -> None:
        segments = build_sensorgram_control_step_overlay_segments(
            [
                {"t_ms": 0, "step_index": 1, "plan_state": "RUN", "event": "plan_started", "color": "#1f77b4"},
                {"t_ms": 300_000, "step_index": 2, "plan_state": "RUN", "event": "step_jump", "color": "#ff7f0e"},
                {"t_ms": 900_000, "step_index": 2, "plan_state": "PAUSE", "event": "plan_pause", "color": "#ff7f0e"},
                {"t_ms": 1_200_000, "step_index": 2, "plan_state": "RUN", "event": "plan_resume", "color": "#ff7f0e"},
                {"t_ms": 1_500_000, "step_index": 2, "plan_state": "STOP", "event": "plan_stopped", "color": "#ff7f0e"},
            ],
            current_elapsed_s=1_800.0,
        )

        self.assertEqual(
            [
                (round(float(segment["start_s"]), 3), round(float(segment["end_s"]), 3), segment["state"], segment["step_index"])
                for segment in segments
            ],
            [
                (0.0, 300.0, "RUN", 1),
                (300.0, 900.0, "RUN", 2),
                (900.0, 1200.0, "PAUSE", 2),
                (1200.0, 1500.0, "RUN", 2),
            ],
        )

    def test_open_running_segment_extends_to_current_time(self) -> None:
        segments = build_sensorgram_control_step_overlay_segments(
            [
                {"t_ms": 0, "step_index": 1, "plan_state": "RUN", "event": "plan_started", "color": "#1f77b4"},
            ],
            current_elapsed_s=42.5,
        )

        self.assertEqual(len(segments), 1)
        self.assertTrue(bool(segments[0]["active"]))
        self.assertEqual(round(float(segments[0]["start_s"]), 3), 0.0)
        self.assertEqual(round(float(segments[0]["end_s"]), 3), 42.5)

    def test_label_and_color_normalization_keep_their_own_fields(self) -> None:
        self.assertEqual(normalize_sensorgram_control_step_overlay_label("PBS", step_index=2), "PBS")
        self.assertEqual(normalize_sensorgram_control_step_overlay_color("#4488ff"), "#4488FF")

    def test_empty_label_falls_back_to_step_number(self) -> None:
        self.assertEqual(normalize_sensorgram_control_step_overlay_label("", step_index=3), "Step 3")

    def test_invalid_color_falls_back_to_a_valid_color(self) -> None:
        self.assertEqual(normalize_sensorgram_control_step_overlay_color("not-a-color"), "#5FA8FF")

    def test_segments_preserve_label_and_color_separately(self) -> None:
        segments = build_sensorgram_control_step_overlay_segments(
            [
                {
                    "t_ms": 0,
                    "step_index": 1,
                    "plan_state": "RUN",
                    "event": "plan_started",
                    "label": "PBS",
                    "color": "#4488ff",
                },
                {
                    "t_ms": 60_000,
                    "step_index": 1,
                    "plan_state": "STOP",
                    "event": "plan_stopped",
                    "label": "PBS",
                    "color": "#4488ff",
                },
            ],
            current_elapsed_s=90.0,
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["label"], "PBS")
        self.assertEqual(segments[0]["color"], "#4488FF")

    def test_palette_label_resolution_uses_color_name(self) -> None:
        label = resolve_sensorgram_control_step_overlay_palette_label(
            "#4488ff",
            [("PBS buffer", "#4488FF"), ("Wash", "#e15759")],
            step_index=4,
        )

        self.assertEqual(label, "PBS buffer")

    def test_palette_label_resolution_falls_back_to_step_number(self) -> None:
        label = resolve_sensorgram_control_step_overlay_palette_label(
            "#123456",
            [("PBS buffer", "#4488FF")],
            step_index=4,
        )

        self.assertEqual(label, "Step 4")


if __name__ == "__main__":
    unittest.main()
