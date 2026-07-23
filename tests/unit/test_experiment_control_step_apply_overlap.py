"""Overlap-safety regression test for async experiment-control step-apply.

Before this fix, step-apply dispatch tracked "is anything in flight" with a
single shared bool and (implicitly) a single pending on_success callback
slot. Once every GUI trigger dispatches async instead of just auto-advance,
two dispatches can legitimately be in flight at once (e.g. a manual step
jump fired while an earlier dispatch's switch rotary valve move is still in flight on
device_io_pool()). This test proves the fix: each dispatch's on_success is
carried on its own _StepApplyResult, so two overlapping completions -
arriving in either order, one of them failed - never cross-fire the wrong
callback and never lose track of the inflight count.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_SRC = Path(__file__).resolve().parents[2] / "apps" / "sLSPR" / "acq" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lspr_app.gui.experiment_control_step_runner import _StepApplyResult
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


class _FakeSignal:
    """Stand-in for the real pyqtSignal - emitting on a __new__-constructed
    QWidget without running QWidget.__init__ raises RuntimeError, and unit
    tests deliberately never construct real Qt objects (see CLAUDE.md)."""

    def __init__(self) -> None:
        self.emit_count = 0

    def emit(self) -> None:
        self.emit_count += 1


def _make_controller() -> ExperimentControlWindow:
    controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
    controller._step_apply_inflight = 0
    controller.hw_status_refresh_requested = _FakeSignal()
    controller._update_mswitch_state_from_probe = lambda: None
    controller._set_status_message = lambda text: None
    controller._emit_experimental_control_state = lambda event, step=None, status="": None
    return controller


class StepApplyOverlapTests(unittest.TestCase):
    def test_two_overlapping_dispatches_each_fire_their_own_callback_once(self) -> None:
        controller = _make_controller()
        controller._step_apply_inflight = 2  # two dispatches already sent

        first_calls: list[int] = []
        second_calls: list[int] = []
        first_result = _StepApplyResult(
            success=True,
            step=SimpleNamespace(step=1),
            status_messages=[],
            needs_mswitch_refresh=False,
            on_success=lambda: first_calls.append(1),
        )
        second_result = _StepApplyResult(
            success=True,
            step=SimpleNamespace(step=2),
            status_messages=[],
            needs_mswitch_refresh=False,
            on_success=lambda: second_calls.append(1),
        )

        # Completions arrive out of dispatch order.
        controller._on_step_apply_async_done(second_result)
        self.assertTrue(controller._step_apply_pending)
        self.assertEqual(second_calls, [1])
        self.assertEqual(first_calls, [])

        controller._on_step_apply_async_done(first_result)
        self.assertFalse(controller._step_apply_pending)
        self.assertEqual(second_calls, [1])
        self.assertEqual(first_calls, [1])

    def test_failed_result_does_not_invoke_on_success(self) -> None:
        controller = _make_controller()
        controller._step_apply_inflight = 1

        calls: list[int] = []
        failed_result = _StepApplyResult(
            success=False,
            step=SimpleNamespace(step=1),
            status_messages=["command failed"],
            needs_mswitch_refresh=False,
            on_success=lambda: calls.append(1),
        )

        controller._on_step_apply_async_done(failed_result)

        self.assertEqual(calls, [])
        self.assertFalse(controller._step_apply_pending)

    def test_inflight_counter_never_goes_negative(self) -> None:
        controller = _make_controller()
        controller._step_apply_inflight = 0

        result = _StepApplyResult(
            success=True,
            step=SimpleNamespace(step=1),
            status_messages=[],
            needs_mswitch_refresh=False,
        )

        controller._on_step_apply_async_done(result)

        self.assertEqual(controller._step_apply_inflight, 0)
        self.assertFalse(controller._step_apply_pending)


if __name__ == "__main__":
    unittest.main()
