"""The sensorgram's per-step overlay labels must match whichever label
mode (comment vs color name) the experiment-control panel's own timeline
widget is currently showing - previously _emit_experimental_control_state
hardcoded step.description regardless of that setting, so the overlay
always showed comments even when the timeline was set to color names.
See docs/sensorgram_improvements.md.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_acq_shell.experiment_control_timeline import PumpPlanTimelineWidget
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


def _make_step(*, color: str = "#4E79A7", description: str = "PBS wash") -> SimpleNamespace:
    return SimpleNamespace(color=color, description=description)


class StepLabelTextModeTests(unittest.TestCase):
    """PumpPlanTimelineWidget._step_label_text itself is pure logic (only
    reads self._label_mode/_color_palette_entries), so it's exercised
    directly here with a bare SimpleNamespace standing in for the widget -
    this is the exact method _experiment_control_step_label_for_overlay
    now delegates to."""

    def test_comment_mode_returns_the_description(self) -> None:
        fake_widget = SimpleNamespace(_label_mode="comment", _color_palette_entries=[])
        step = _make_step(description="PBS wash")

        self.assertEqual(PumpPlanTimelineWidget._step_label_text(fake_widget, step), "PBS wash")

    def test_color_name_mode_resolves_the_palette_name(self) -> None:
        fake_widget = SimpleNamespace(
            _label_mode="color_name",
            _color_palette_entries=[("Blue", "#4E79A7"), ("Wash", "#E15759")],
        )
        step = _make_step(color="#4E79A7", description="PBS wash")

        self.assertEqual(PumpPlanTimelineWidget._step_label_text(fake_widget, step), "Blue")

    def test_color_name_mode_falls_back_to_raw_color_when_unresolved(self) -> None:
        fake_widget = SimpleNamespace(_label_mode="color_name", _color_palette_entries=[])
        step = _make_step(color="#123456", description="PBS wash")

        self.assertEqual(PumpPlanTimelineWidget._step_label_text(fake_widget, step), "#123456")


class ExperimentControlStepLabelForOverlayTests(unittest.TestCase):
    def test_delegates_to_the_live_timeline_widget(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller.timeline_widget = SimpleNamespace(_step_label_text=lambda step: f"resolved:{step.description}")
        step = _make_step(description="PBS wash")

        result = controller._experiment_control_step_label_for_overlay(step)

        self.assertEqual(result, "resolved:PBS wash")

    def test_falls_back_to_description_when_timeline_widget_is_missing(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        # A bare __new__() PyQt object raises instead of returning a
        # getattr() default for attributes truly absent from __dict__ (Qt's
        # own __getattr__ needs the C++ side constructed) - set it to None
        # explicitly instead, which is what the real "not built yet" state
        # looks like once __init__ has run far enough to declare the
        # attribute but not yet construct the widget.
        controller.timeline_widget = None
        step = _make_step(description="PBS wash")

        result = controller._experiment_control_step_label_for_overlay(step)

        self.assertEqual(result, "PBS wash")

    def test_falls_back_to_description_when_step_label_text_raises(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)

        def _raise(_step):
            raise RuntimeError("boom")

        controller.timeline_widget = SimpleNamespace(_step_label_text=_raise)
        step = _make_step(description="PBS wash")

        result = controller._experiment_control_step_label_for_overlay(step)

        self.assertEqual(result, "PBS wash")

    def test_end_to_end_matches_the_real_timeline_widget_in_color_name_mode(self) -> None:
        # Uses the real PumpPlanTimelineWidget._step_label_text (not a
        # stub), proving the overlay would show exactly what the timeline
        # itself would show for color-name mode.
        fake_timeline_widget = SimpleNamespace(_label_mode="color_name", _color_palette_entries=[("Blue", "#4E79A7")])
        fake_timeline_widget._step_label_text = lambda step: PumpPlanTimelineWidget._step_label_text(fake_timeline_widget, step)
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller.timeline_widget = fake_timeline_widget
        step = _make_step(color="#4E79A7", description="PBS wash")

        result = controller._experiment_control_step_label_for_overlay(step)

        self.assertEqual(result, "Blue")


class CycleLabelModeTriggersOverlaySyncTests(unittest.TestCase):
    """Toggling the timeline's label-mode switch must immediately refresh
    the sensorgram overlay too, not just the timeline widget itself -
    otherwise the overlay only picks up the new mode whenever its next
    tick/view-range-change sync happens to fire, which can be a while if
    nothing else is triggering a redraw right at that moment."""

    def _make_controller(self) -> tuple[ExperimentControlWindow, dict[str, list]]:
        calls: dict[str, list] = {"sync_overlay": [], "save_ui_state": []}
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._experiment_control_timeline_label_mode = "comment"
        controller._color_palette_entries = []
        controller.timeline_widget = SimpleNamespace(
            set_label_mode=lambda _mode: None,
            set_color_palette_entries=lambda _entries: None,
            update=lambda: None,
        )
        controller.save_ui_state = lambda: calls["save_ui_state"].append(1)
        controller.recording_controller = SimpleNamespace(
            _sync_sensorgram_control_step_overlay=lambda: calls["sync_overlay"].append(1)
        )
        return controller, calls

    def test_toggle_triggers_an_immediate_overlay_sync(self) -> None:
        controller, calls = self._make_controller()

        ExperimentControlWindow._cycle_experiment_control_timeline_label_mode(controller)

        self.assertEqual(controller._experiment_control_timeline_label_mode, "color_name")
        self.assertEqual(calls["sync_overlay"], [1])
        self.assertEqual(calls["save_ui_state"], [1])

    def test_missing_recording_controller_does_not_raise(self) -> None:
        controller, _calls = self._make_controller()
        controller.recording_controller = None

        ExperimentControlWindow._cycle_experiment_control_timeline_label_mode(controller)  # must not raise

    def test_sync_overlay_exception_does_not_propagate(self) -> None:
        controller, _calls = self._make_controller()

        def _raise() -> None:
            raise RuntimeError("boom")

        controller.recording_controller = SimpleNamespace(_sync_sensorgram_control_step_overlay=_raise)

        ExperimentControlWindow._cycle_experiment_control_timeline_label_mode(controller)  # must not raise


if __name__ == "__main__":
    unittest.main()
