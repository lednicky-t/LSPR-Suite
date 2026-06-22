from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from lspr_core import ExperimentPlan, ExperimentPlanStep as CoreExperimentPlanStep, SuiteIdentity, retime_steps, summarize_experiment_plan
from lspr_io import build_legacy_experiment_plan_row_table
from lspr_app.version import APP_VERSION

ACTIVE_PUMP_CHANNELS = 4
HDF5_PUMP_CHANNELS = 6
DEFAULT_TUBE_MM = 0.25


@dataclass(slots=True)
class PumpChannelStep:
    flow_ul_min: float = 0.0
    direction: str = "OFF"


@dataclass(slots=True)
class PumpPlanStep:
    step: int = 1
    duration_s: float = 60.0
    start_s: float = 0.0
    end_s: float = 60.0
    color: str = "#4E79A7"
    valve: str = ""
    switch_position: int = 1
    description: str = ""
    channels: list[PumpChannelStep] = field(
        default_factory=lambda: [PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)]
    )


def to_core_experiment_step(step: PumpPlanStep) -> CoreExperimentPlanStep:
    return CoreExperimentPlanStep(
        id=int(step.step),
        label=f"Step {int(step.step)}",
        start_s=0.0,
        end_s=max(float(step.duration_s), 0.0),
        color=str(step.color or ""),
        comment=str(step.description or ""),
        devices={
            "valve": str(step.valve or ""),
            "switch_position": int(step.switch_position),
            "channels": [
                {
                    "flow_ul_min": float(channel.flow_ul_min),
                    "direction": str(channel.direction or "OFF"),
                }
                for channel in step.channels
            ],
        },
    )


def from_core_experiment_step(core_step: CoreExperimentPlanStep, template: PumpPlanStep | None = None) -> PumpPlanStep:
    base = deepcopy(template) if template is not None else PumpPlanStep(step=int(core_step.id))
    base.step = int(core_step.id)
    base.start_s = float(core_step.start_s)
    base.end_s = float(core_step.end_s)
    base.duration_s = max(float(core_step.duration_s), 0.0)
    if core_step.color:
        base.color = str(core_step.color)
    if core_step.comment is not None:
        base.description = str(core_step.comment)
    if isinstance(core_step.devices, dict):
        base.valve = str(core_step.devices.get("valve", base.valve or ""))
        try:
            base.switch_position = max(min(int(core_step.devices.get("switch_position", base.switch_position)), 12), 1)
        except (TypeError, ValueError):
            base.switch_position = 1
        channels = core_step.devices.get("channels", [])
        if isinstance(channels, list):
            for index, channel_payload in enumerate(channels[: len(base.channels)]):
                if not isinstance(channel_payload, dict):
                    continue
                base.channels[index].flow_ul_min = max(float(channel_payload.get("flow_ul_min", base.channels[index].flow_ul_min)), 0.0)
                direction = str(channel_payload.get("direction", base.channels[index].direction or "OFF")).upper()
                base.channels[index].direction = "CCW" if direction == "CCW" else ("OFF" if direction == "OFF" else "CW")
    return base


def to_core_experiment_plan(steps: list[PumpPlanStep], *, app_name: str = "LSPR Acquisition") -> ExperimentPlan:
    core_steps = [to_core_experiment_step(step) for step in steps]
    core_steps = retime_steps(core_steps)
    return ExperimentPlan(
        identity=SuiteIdentity(
            app_name=app_name,
            app_version=APP_VERSION,
            format_name="LSPR Experiment Plan",
            format_version=1,
        ),
        steps=core_steps,
    )


def make_default_pump_plan() -> list[PumpPlanStep]:
    steps = [
        PumpPlanStep(
            step=1,
            duration_s=60.0,
            color="#4E79A7",
            valve="load",
            switch_position=1,
            description="Prime / load",
            channels=[
                PumpChannelStep(flow_ul_min=50.0, direction="CW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            ],
        ),
        PumpPlanStep(
            step=2,
            duration_s=120.0,
            color="#59A14F",
            valve="measure",
            switch_position=2,
            description="Measure",
            channels=[
                PumpChannelStep(flow_ul_min=20.0, direction="CW"),
                PumpChannelStep(flow_ul_min=20.0, direction="CW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            ],
        ),
        PumpPlanStep(
            step=3,
            duration_s=45.0,
            color="#E15759",
            valve="wash",
            switch_position=3,
            description="Wash",
            channels=[
                PumpChannelStep(flow_ul_min=70.0, direction="CCW"),
                PumpChannelStep(flow_ul_min=70.0, direction="CCW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            ],
        ),
    ]
    return recompute_plan_timing(steps)


def recompute_plan_timing(steps: list[PumpPlanStep]) -> list[PumpPlanStep]:
    core_plan = to_core_experiment_plan(steps)
    summary = summarize_experiment_plan(core_plan)
    if summary.step_count == 0:
        return []
    normalized: list[PumpPlanStep] = []
    for index, (step, core_step) in enumerate(zip(steps, core_plan.steps, strict=False), start=1):
        normalized_step = deepcopy(step)
        normalized_step.step = index
        normalized_step.duration_s = max(float(core_step.duration_s), 0.0)
        normalized_step.start_s = float(core_step.start_s)
        normalized_step.end_s = float(core_step.end_s)
        normalized.append(normalized_step)
    return normalized


def duplicate_plan_step(step: PumpPlanStep) -> PumpPlanStep:
    return deepcopy(step)


def steps_to_hdf5_rows(
    steps: list[PumpPlanStep],
    tube_mm_by_channel: list[float] | None = None,
) -> list[list[str]]:
    core_plan = to_core_experiment_plan(steps)
    table = build_legacy_experiment_plan_row_table(
        core_plan,
        tube_mm_by_channel=tube_mm_by_channel,
        active_channel_count=ACTIVE_PUMP_CHANNELS,
        hdf5_channel_count=HDF5_PUMP_CHANNELS,
    )
    return table.rows


to_core_flow_step = to_core_experiment_step
from_core_flow_step = from_core_experiment_step
to_core_flow_plan = to_core_experiment_plan
recompute_experiment_plan_timing = recompute_plan_timing
steps_to_experiment_plan_rows = steps_to_hdf5_rows
