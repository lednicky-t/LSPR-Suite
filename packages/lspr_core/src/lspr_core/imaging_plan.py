"""Experiment steps for an imaging dataset: from the measured notes, joined with a plan file, and which
cubes sit inside one step.

Pure functions, no Qt and no files. All times are unix milliseconds (the clock of `ImagingCubeTiming` and
`ImagingCommentEvent`).

Where the numbers come from, and why:

- **Step boundaries** come from the *measured* notes (`ImagingCommentEvent`: the note changes when the step
  changes). The plan file's own durations are not used for placement: every step ran a fraction of a second
  longer than planned, so planned start times drift away from the measurement by seconds over a run
  (3.6 s after 12 steps in the 2026-10 bulk-sensitivity example).
- **Device settings** (flows, directions, valves) come from the plan file and are joined to the measured steps
  by order. The join is refused, with a warning, when the two disagree on the number of steps, rather than
  guessed.
- **A step starts at the first image that carries its note** and ends where the next note begins. The legacy
  tool changes the note mid-cube, so the boundary is only as precise as one image interval (about 0.35 s).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .imaging_models import (
    PLAN_TIME_SOURCE_MEASURED_NOTES,
    ImagingCommentEvent,
    ImagingCubeTiming,
    ImagingPlan,
    ImagingPlanStep,
)
from .models import ExperimentPlan

# Notes that mean "no step is running" (the legacy tool writes this text while the pump is off, before the
# first and after the last step). A blank note is treated the same way.
DEFAULT_IDLE_COMMENTS: tuple[str, ...] = ("", "Pump is not running")

# A measured step duration this far from the planned one is reported (the larger of the two limits applies).
DURATION_TOLERANCE_ABS_S = 2.0
DURATION_TOLERANCE_REL = 0.02


@dataclass(frozen=True, slots=True)
class ObservedStep:
    label: str
    start_unix_ms: int
    end_unix_ms: int


def is_idle_comment(comment: str, idle_comments: Sequence[str] = DEFAULT_IDLE_COMMENTS) -> bool:
    key = comment.strip().casefold()
    return any(key == idle.strip().casefold() for idle in idle_comments)


def end_of_data_unix_ms(image_timings: Iterable[ImagingCubeTiming]) -> int | None:
    """Just after the last image (exclusive end, so the last image is inside a step that runs to the end of the
    data). `None` without timings."""
    latest = max((timing.acquired_at_unix_ms for timing in image_timings), default=None)
    return None if latest is None else latest + 1


def observed_steps(
    comment_events: Iterable[ImagingCommentEvent],
    *,
    data_end_unix_ms: int | None = None,
    idle_comments: Sequence[str] = DEFAULT_IDLE_COMMENTS,
) -> list[ObservedStep]:
    """The steps the measured notes show: one per non-idle note, ending where the next note (idle or not)
    begins. A last step with no following note ends at *data_end_unix_ms* (zero length without it)."""
    events = sorted(comment_events, key=lambda event: event.acquired_at_unix_ms)
    steps: list[ObservedStep] = []
    for index, event in enumerate(events):
        if is_idle_comment(event.comment, idle_comments):
            continue
        start = event.acquired_at_unix_ms
        if index + 1 < len(events):
            end = events[index + 1].acquired_at_unix_ms
        else:
            end = start if data_end_unix_ms is None else max(start, data_end_unix_ms)
        steps.append(ObservedStep(label=event.comment, start_unix_ms=start, end_unix_ms=end))
    return steps


def build_imaging_plan(
    comment_events: Iterable[ImagingCommentEvent],
    *,
    data_end_unix_ms: int | None = None,
    planned: ExperimentPlan | None = None,
    source_file: str | None = None,
    read_warnings: Sequence[str] = (),
    idle_comments: Sequence[str] = DEFAULT_IDLE_COMMENTS,
) -> ImagingPlan | None:
    """The measured steps, with the device settings of *planned* attached when it matches.

    Returns None when there is nothing to record: no step in the notes and no plan file. With a plan file but
    no step changes in the notes, returns a plan with no steps and a warning (the plan could not be placed in
    time, and the saved file says so). With a different step count, the measured steps are kept without
    settings and a warning says why; nothing is paired by guesswork.
    """
    observed = observed_steps(comment_events, data_end_unix_ms=data_end_unix_ms, idle_comments=idle_comments)
    warnings = list(read_warnings)
    planned_steps = planned.ordered() if planned is not None else []
    plan_name = f"The plan file {source_file}" if source_file else "The plan file"

    if planned is not None and not observed:
        warnings.append(
            f"{plan_name} has {len(planned_steps)} step(s), but no step changes were found in "
            "the measured notes, so they could not be placed in time."
        )
        return ImagingPlan(time_source=PLAN_TIME_SOURCE_MEASURED_NOTES, source_file=None, steps=[], warnings=warnings)
    if not observed:
        return None

    joined = planned is not None and len(planned_steps) == len(observed)
    if planned is not None and not joined:
        warnings.append(
            f"{plan_name} has {len(planned_steps)} step(s) but the measured notes show "
            f"{len(observed)}, so its device settings were not attached. (Two successive steps with the same note "
            "cannot be told apart in the notes.)"
        )

    steps: list[ImagingPlanStep] = []
    for number, measured in enumerate(observed, start=1):
        planned_step = planned_steps[number - 1] if joined else None
        planned_duration_s = None
        devices: dict = {}
        color = None
        if planned_step is not None:
            planned_duration_s = planned_step.duration_s
            devices = dict(planned_step.devices)
            color = planned_step.color or None
            planned_note = (planned_step.comment or "").strip()
            if planned_note != measured.label.strip():
                warnings.append(
                    f"Step {number}: the plan file says '{planned_note}', the measurement recorded "
                    f"'{measured.label}'; the recorded note is kept."
                )
            measured_duration_s = (measured.end_unix_ms - measured.start_unix_ms) / 1000.0
            limit_s = max(DURATION_TOLERANCE_ABS_S, DURATION_TOLERANCE_REL * planned_duration_s)
            if abs(measured_duration_s - planned_duration_s) > limit_s:
                warnings.append(
                    f"Step {number}: the plan says {planned_duration_s:.1f} s but {measured_duration_s:.1f} s "
                    "were measured."
                )
        steps.append(
            ImagingPlanStep(
                step=number,
                label=measured.label,
                start_unix_ms=measured.start_unix_ms,
                end_unix_ms=measured.end_unix_ms,
                planned_duration_s=planned_duration_s,
                color=color,
                devices=devices,
            )
        )
    return ImagingPlan(
        time_source=PLAN_TIME_SOURCE_MEASURED_NOTES,
        source_file=source_file if joined else None,
        steps=steps,
        warnings=warnings,
    )


class StepLookup:
    """`ImagingPlan.step_at` in O(log n), for loops over many images. Build once per plan."""

    __slots__ = ("_starts", "_steps")

    def __init__(self, plan: ImagingPlan | None) -> None:
        steps = sorted(plan.steps if plan is not None else [], key=lambda step: step.start_unix_ms)
        self._steps = steps
        self._starts = [step.start_unix_ms for step in steps]

    def step_at(self, acquired_at_unix_ms: int) -> ImagingPlanStep | None:
        index = bisect_right(self._starts, acquired_at_unix_ms) - 1
        if index < 0:
            return None
        step = self._steps[index]
        return step if acquired_at_unix_ms < step.end_unix_ms else None


@dataclass(frozen=True, slots=True)
class CubeStepAssignment:
    """Which step a cube's images belong to.

    `step` is the step number when every image is in the same step, else None. `mixed` is True when the images
    fall in more than one place (two steps, or a step and an idle stretch): the cube then holds measurements
    made in two different conditions. `counts` is ``((step or None, number of images), ...)`` in time order,
    None standing for "no step", e.g. ``((1, 22), (2, 5))``.
    """

    step: int | None
    mixed: bool
    counts: tuple[tuple[int | None, int], ...]


def classify_cube(lookup: StepLookup, image_times_unix_ms: Iterable[int]) -> CubeStepAssignment:
    """Place one cube's images. The caller decides which images count (a dark frame carries no sample
    information, so a caller may leave it out)."""
    counts: dict[int | None, int] = {}
    for time_ms in sorted(image_times_unix_ms):
        step = lookup.step_at(time_ms)
        key = None if step is None else step.step
        counts[key] = counts.get(key, 0) + 1
    mixed = len(counts) > 1
    only = next(iter(counts)) if len(counts) == 1 else None
    return CubeStepAssignment(step=only, mixed=mixed, counts=tuple(counts.items()))


def classify_cubes(
    plan: ImagingPlan | None, image_times_by_cube: Mapping[int, Iterable[int]]
) -> dict[int, CubeStepAssignment]:
    lookup = StepLookup(plan)
    return {cube: classify_cube(lookup, times) for cube, times in image_times_by_cube.items()}
