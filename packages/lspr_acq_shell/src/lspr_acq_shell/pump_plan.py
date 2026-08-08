"""Pump/valve/selector experiment-plan domain model (PumpPlanStep and its
conversion to/from lspr_core's generic ExperimentPlan).

Extracted from singleLSPR Acquisition's `domain/pump_plan.py` (Phase 2,
LSPRi acq experiment-control reuse - Tier 0 of the extraction plan) - the
fluidics device layer itself (Phase 1) already lives here, but the plan
*data model* that describes what to send to that layer did not. LSPRi acq
needs this to reuse the same pump/valve/selector plan-execution machinery,
per the maintainer's explicit confirmation that this app drives the same
fluidics hardware, not a separate system.

One real generalization, not a verbatim move: `to_core_experiment_plan()`
used to import `APP_VERSION` directly from `lspr_app.version` (sLSPR acq's
own module) and stamp it into the returned plan's identity metadata - a
backward dependency a shared package must not have (the same class of
problem already fixed once this project, for `device_lifecycle.py`'s
spectrometer-stage import - see the 2026-08-08 build-log entry). Fixed by
making `app_version` a required keyword argument instead of an implicit
import; sLSPR acq's own `domain/pump_plan.py` (now a shim over this module)
preserves its exact original call signature so none of its 5 existing call
sites needed to change - see the build-log entry.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from lspr_core import ExperimentPlan, ExperimentPlanStep as CoreExperimentPlanStep, SuiteIdentity, retime_steps, summarize_experiment_plan
from lspr_io import build_legacy_experiment_plan_row_table

from lspr_acq_shell.reglo_icc import ACTIVE_PUMP_CHANNELS, DEFAULT_ROLLER_COUNT, VALID_ROLLER_COUNTS

_ = (DEFAULT_ROLLER_COUNT, VALID_ROLLER_COUNTS)  # re-exported for other modules; not used in this file

HDF5_PUMP_CHANNELS = 6
DEFAULT_TUBE_MM = 0.25


@dataclass(slots=True, frozen=True)
class TubeDiameterOption:
    """One entry from the Reglo ICC's supported-tubing chart.

    The pump's "+" (set tube inside diameter) command only accepts these 26
    exact values - real Ismatec cassette sizes, not arbitrary millimeters.
    Anything else is rejected outright, which used to fail silently: a
    rejected tube-diameter command aborts the rest of that channel's
    configuration (direction/mode/flow rate never get sent), so an
    off-catalog value looked like a channel just ignoring its plan step.
    Source: Reglo ICC manual, section 13 "Tubing Size and Flow Rate Chart".
    """

    mm: float
    order_no: str
    min_flow_ul_min: float
    max_flow_ul_min: float


TUBE_DIAMETER_OPTIONS: tuple[TubeDiameterOption, ...] = (
    TubeDiameterOption(0.13, "SC0189T", 2.0, 110.0),
    TubeDiameterOption(0.19, "SC0049T", 3.0, 230.0),
    TubeDiameterOption(0.25, "SC0050T", 5.0, 410.0),
    TubeDiameterOption(0.38, "SC0051T", 10.0, 940.0),
    TubeDiameterOption(0.44, "SC0052T", 13.0, 1300.0),
    TubeDiameterOption(0.51, "SC0053T", 17.0, 1700.0),
    TubeDiameterOption(0.57, "SC0054T", 21.0, 2100.0),
    TubeDiameterOption(0.64, "SC0055T", 26.0, 2600.0),
    TubeDiameterOption(0.76, "SC0056T", 36.0, 3600.0),
    TubeDiameterOption(0.89, "SC0057T", 49.0, 4900.0),
    TubeDiameterOption(0.95, "SC0058T", 56.0, 5600.0),
    TubeDiameterOption(1.02, "SC0059T", 63.0, 6300.0),
    TubeDiameterOption(1.09, "SC0060T", 72.0, 7200.0),
    TubeDiameterOption(1.14, "SC0061T", 78.0, 7800.0),
    TubeDiameterOption(1.22, "SC0062T", 88.0, 8800.0),
    TubeDiameterOption(1.30, "SC0063T", 100.0, 10000.0),
    TubeDiameterOption(1.42, "SC0064T", 110.0, 11000.0),
    TubeDiameterOption(1.52, "SC0065T", 130.0, 13000.0),
    TubeDiameterOption(1.65, "SC0066T", 150.0, 15000.0),
    TubeDiameterOption(1.75, "SC0067T", 160.0, 16000.0),
    TubeDiameterOption(1.85, "SC0068T", 170.0, 17000.0),
    TubeDiameterOption(2.06, "SC0069T", 200.0, 20000.0),
    TubeDiameterOption(2.29, "SC0070T", 240.0, 24000.0),
    TubeDiameterOption(2.54, "SC0071T", 270.0, 27000.0),
    TubeDiameterOption(2.79, "SC0072T", 310.0, 31000.0),
    TubeDiameterOption(3.17, "SC0224T", 350.0, 35000.0),
)


def nearest_tube_diameter_option(mm: float) -> TubeDiameterOption:
    """Snap an arbitrary millimeter value to the closest supported tube size.

    Used when loading plan files or settings that predate this restriction
    and may hold an off-catalog value (e.g. an old default or a typo) -
    rather than reject them, pick the nearest real tube the pump can
    actually be configured with.
    """
    return min(TUBE_DIAMETER_OPTIONS, key=lambda option: abs(option.mm - float(mm)))


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


def to_core_experiment_plan(steps: list[PumpPlanStep], *, app_name: str = "LSPR Acquisition", app_version: str) -> ExperimentPlan:
    core_steps = [to_core_experiment_step(step) for step in steps]
    core_steps = retime_steps(core_steps)
    return ExperimentPlan(
        identity=SuiteIdentity(
            app_name=app_name,
            app_version=app_version,
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
    # app_version is discarded here - only core_plan.steps (timing) is used,
    # never the identity metadata, so a placeholder is safe (see this
    # module's docstring for why to_core_experiment_plan needs a real one
    # from actual callers that persist/export the plan).
    core_plan = to_core_experiment_plan(steps, app_version="n/a")
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
    # Same placeholder reasoning as recompute_plan_timing() above - the row
    # table never reads plan.identity.
    core_plan = to_core_experiment_plan(steps, app_version="n/a")
    table = build_legacy_experiment_plan_row_table(
        core_plan,
        tube_mm_by_channel=tube_mm_by_channel,
        active_channel_count=ACTIVE_PUMP_CHANNELS,
        hdf5_channel_count=HDF5_PUMP_CHANNELS,
    )
    return table.rows


from_core_flow_step = from_core_experiment_step
