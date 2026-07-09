# Experiment Plan Format

This document defines the shared experiment-plan shape used by the suite.

The canonical Python model lives in `packages/lspr_core`.
The canonical serialization helpers live in `packages/lspr_io`.

## Goals

- keep experiment plans usable in acquisition and evaluation
- preserve timing, color, valve, switch, and channel states
- support round-tripping between table views and structured objects

## Shared Fields

- step id
- start time
- end time
- duration
- color
- description/comment
- valve state
- switch position
- per-channel flow
- per-channel direction
- per-channel tubing diameter

## Timing Rule

- duration is the primary editable step value
- start and end are derived from duration unless a workflow explicitly supports manual boundary editing
- exported tables should keep both duration and absolute step timing

## Legacy Compatibility

Older CSV/TXT plan tables are still supported, but the shared table model should be used for new code.

## Python Entry Points

- `lspr_core.ExperimentPlan`
- `lspr_core.ExperimentPlanStep`
- `lspr_io.build_experiment_plan_row_table(...)`
- `lspr_io.build_legacy_experiment_plan_row_table(...)`
