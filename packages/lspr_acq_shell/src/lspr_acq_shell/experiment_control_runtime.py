"""Experiment run/hold/pause/stop state naming - pure, no Qt, no window.

Extracted from singleLSPR Acquisition's `gui/experiment_control_runtime.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 0 extraction, 2026-08-09)
verbatim - this file had zero window/Qt coupling to begin with (see the
extraction survey referenced in the build log's 2026-08-09 entry), the
easiest of the Tier 0 pieces.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExperimentRuntimeSnapshot:
    running: bool
    holding: bool
    paused: bool
    recording: bool
    has_steps: bool

    @property
    def state_name(self) -> str:
        return experiment_runtime_state_name(running=self.running, holding=self.holding, paused=self.paused)

    @property
    def payload_state(self) -> str:
        return experiment_runtime_payload_state(running=self.running, holding=self.holding, paused=self.paused)

    @property
    def play_active(self) -> bool:
        return bool(self.running)

    @property
    def hold_visible(self) -> bool:
        return bool(self.running or self.holding)

    @property
    def hold_enabled(self) -> bool:
        return bool(self.running or self.holding)

    @property
    def pause_available(self) -> bool:
        return bool(self.running or self.holding or self.paused or self.has_steps)

    @property
    def pause_active(self) -> bool:
        return bool(self.paused)

    @property
    def stop_visible(self) -> bool:
        return bool(self.running or self.holding or self.paused)

    @property
    def blink_active(self) -> bool:
        return bool(self.running or self.holding or self.paused)

    def label(self) -> str:
        return experiment_runtime_label(
            running=self.running,
            holding=self.holding,
            paused=self.paused,
            recording=self.recording,
        )

    def tooltip(self, *, source_name: str) -> str:
        return experiment_runtime_tooltip(
            running=self.running,
            holding=self.holding,
            paused=self.paused,
            recording=self.recording,
            source_name=source_name,
        )


def experiment_runtime_state_name(*, running: bool, holding: bool, paused: bool) -> str:
    if running:
        return "running"
    if holding:
        return "hold"
    if paused:
        return "paused"
    return "stopped"


def experiment_runtime_payload_state(*, running: bool, holding: bool, paused: bool) -> str:
    if running:
        return "RUN"
    if holding:
        return "HOLD"
    if paused:
        return "PAUSE"
    return "STOP"


def experiment_runtime_label(*, running: bool, holding: bool, paused: bool, recording: bool) -> str:
    state = experiment_runtime_state_name(running=running, holding=holding, paused=paused)
    if recording:
        return f"Experiment: {state} & recording"
    return f"Experiment: {state}"


def experiment_runtime_snapshot(
    *,
    running: bool,
    holding: bool,
    paused: bool,
    recording: bool,
    has_steps: bool,
) -> ExperimentRuntimeSnapshot:
    return ExperimentRuntimeSnapshot(
        running=bool(running),
        holding=bool(holding),
        paused=bool(paused),
        recording=bool(recording),
        has_steps=bool(has_steps),
    )


def experiment_runtime_tooltip(
    *,
    running: bool,
    holding: bool,
    paused: bool,
    recording: bool,
    source_name: str,
) -> str:
    state = experiment_runtime_state_name(running=running, holding=holding, paused=paused)
    source_name = str(source_name or "").strip().lower() or "source"
    if state == "running":
        tooltip = f"Experiment measurement is running on the {source_name} source."
    elif state == "hold":
        tooltip = f"Experiment measurement is on hold on the {source_name} source."
    elif state == "paused":
        tooltip = f"Experiment measurement is paused on the {source_name} source."
    else:
        tooltip = f"No experiment measurement is running on the {source_name} source."
    if recording:
        tooltip += " Recording is active."
    return tooltip
