from __future__ import annotations

from dataclasses import dataclass

from .models import ExperimentPlan, ExperimentPlanStep


@dataclass(frozen=True)
class ExperimentPlanTimingSummary:
    step_count: int
    total_duration_s: float
    start_s: float
    end_s: float


def summarize_experiment_plan(plan: ExperimentPlan) -> ExperimentPlanTimingSummary:
    ordered = plan.ordered()
    if not ordered:
        return ExperimentPlanTimingSummary(step_count=0, total_duration_s=0.0, start_s=0.0, end_s=0.0)
    start_s = ordered[0].start_s
    end_s = max(step.end_s for step in ordered)
    return ExperimentPlanTimingSummary(
        step_count=len(ordered),
        total_duration_s=max(0.0, end_s - start_s),
        start_s=start_s,
        end_s=end_s,
    )
def retime_steps(steps: list[ExperimentPlanStep], start_s: float = 0.0) -> list[ExperimentPlanStep]:
    """Return a new step list with consecutive timing based on each step duration."""
    current_start = start_s
    retimed: list[ExperimentPlanStep] = []
    for step in steps:
        duration = max(0.0, step.duration_s)
        current_end = current_start + duration
        retimed.append(step.with_timing(current_start, current_end))
        current_start = current_end
    return retimed


def shift_steps(steps: list[ExperimentPlanStep], delta_s: float) -> list[ExperimentPlanStep]:
    """Return a new step list shifted in time by a constant delta."""
    return [step.with_timing(step.start_s + delta_s, step.end_s + delta_s) for step in steps]
