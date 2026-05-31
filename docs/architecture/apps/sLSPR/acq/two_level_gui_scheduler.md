# Two-Level GUI Scheduler for `singleLSPR Acquisition`

This note describes a refactor direction for the live GUI update path.

## Why this is needed

The current GUI uses several independent Qt timers for:

- live acquisition polling
- live processing polling
- plot refresh
- deferred UI refresh
- log buffer flushing
- state persistence
- heartbeat diagnostics

That design is readable, but it lets periodic work compete on the GUI thread. The result is not usually one expensive callback. It is the accumulation of many small wakeups, delayed queue drains, and coalesced refreshes that gradually pushes the event loop behind.

The goal of the refactor is to keep the behavior explicit while reducing background churn.

## Design Goal

Use two explicit scheduling lanes:

1. A fast live-data lane for queue drains and result handoff.
2. A slower maintenance lane for plot refresh, stats refresh, logs, and persistence.

The live lane must always win when both lanes are due.

## Responsibilities

### Live lane

This lane handles the live data path:

- drain raw acquisition events
- drain processed result events
- keep only the newest item when backlog exists
- update the session model with the latest live result
- request plot refresh and telemetry updates
- re-arm itself for the next live opportunity

This lane should stay small and predictable. Its job is to keep the GUI synchronized with incoming data, not to rebuild the whole interface.

### Maintenance lane

This lane handles everything that is important but not latency-critical:

- plot redraws that are not part of live intake
- session statistics refresh
- session summary refresh
- log buffer flushing
- UI state persistence
- acquisition state persistence
- optional recording snapshots

This lane should coalesce repeated requests and run only when the live lane does not need immediate service.

## Recommended Structure

Use one visible scheduler object in the GUI layer with:

- a single Qt `singleShot` timer
- an explicit task queue or due-time map
- task priorities
- a small per-tick budget
- visible debug metrics for due tasks and skipped work

Suggested priority order:

1. live acquisition drain
2. live processing drain
3. plot refresh
4. telemetry / live estimate refresh
5. session stats / summary refresh
6. log buffer flush
7. state persistence

## Scheduling Rules

### Rule 1: live intake always preempts maintenance

If live data is waiting, the scheduler should service it before any background maintenance task.

### Rule 2: coalesce, do not queue duplicates

If the same refresh is requested multiple times before the scheduler runs, keep one pending request and discard the redundant ones.

### Rule 3: keep the per-tick budget small

The scheduler should exit after a small time budget or after a bounded number of tasks.

This prevents a single GUI tick from becoming a long stall.

### Rule 4: drain latest only for live queues

For live acquisition and live processing queues, keep the newest item and explicitly count dropped items. That preserves the latest state without trying to replay stale frames.

### Rule 5: make scheduling visible

Expose the scheduler state in the session stats panel so users and future maintainers can see:

- pending live work
- pending maintenance work
- dropped frames
- queue depth
- the last time each lane ran

## Mapping From Current Timers

### Live lane candidates

- `_live_result_timer`
- `_live_processed_timer`

These should become part of the live lane and be driven by one shared live scheduler, not separate free-running timers.

### Maintenance lane candidates

- `_plot_refresh_timer`
- `_stats_refresh_timer`
- `_log_buffer_timer`
- `_ui_state_timer`
- `_acquisition_state_timer`
- `_session_stats_recording_timer`
- `_trace_autoscale_timer` if it remains maintenance-like

These should become maintenance tasks that are scheduled through the same scheduler object.

### Keep separate

- background worker threads or processes
- hardware acquisition
- processing workers

Those are not part of the GUI scheduler and should remain separate.

## Suggested Implementation Phases

### Phase 1: introduce the scheduler shell

- add one scheduler object in the GUI layer
- give it live and maintenance queues
- keep the old timer callbacks as wrappers while the new scheduler is introduced

### Phase 2: move live drains into the live lane

- move raw acquisition queue draining
- move processed-result queue draining
- make the live lane re-arm itself based on the live interval

### Phase 3: move maintenance callbacks

- move plot refresh
- move stats and summary refresh
- move log flush and persistence tasks

### Phase 4: add diagnostics

- task backlog counts
- last-run timestamps
- tick duration
- skipped task counts

These diagnostics should be visible in the session stats panel, not hidden in debug logs only.

## Non-Goals

This refactor should not:

- hide live behavior behind implicit background magic
- replace the worker processes with a giant GUI loop
- remove the explicit absolute / rolling sensorgram controls
- remove the visible downsampling controls
- make the render path depend on undocumented background state

## Why this fits the app

This app needs:

- correct scientific data flow
- visible and predictable GUI behavior
- bounded latency for live acquisition
- understandable maintenance work

Two explicit lanes give that balance better than many independent timers, and better than one monolithic scheduler that tries to do everything at once.
