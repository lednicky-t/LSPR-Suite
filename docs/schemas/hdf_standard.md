# HDF Standard

This document defines the shared HDF5 rules used across the LSPR Suite.

It applies to:

- `singleLSPR` acquisition
- `singleLSPR` evaluation
- `LSPRimaging` acquisition
- `LSPRimaging` evaluation

## Goals

- keep measurement files compatible across apps
- make schema and format versions explicit
- store raw data appendably
- keep derived data separate from raw data
- make readers tolerant of unknown future fields when possible

## Version Layers

Each persisted file should carry at least these identity fields:

- `schema_name`
- `schema_version`
- `schema_major`
- `schema_minor`
- `format_name`
- `format_version`
- `app_name`
- `app_version`
- `created_by`
- `created_at_utc`
- `started_at_utc`

## Compatibility Policy

- readers must reject unknown schema names
- readers should refuse newer incompatible major versions
- readers should accept newer minor versions when unknown fields can be ignored
- breaking semantic changes require a major version bump
- small compatible additions require a minor version bump

## Layout Policy

Recommended top-level groups:

- `/manifest`
- `/axes`
- `/devices`
- `/metadata`
- `/plans`
- `/runs`
- `/raw`
- `/processed`
- `/events`

Unknown top-level groups may be ignored with a warning.

## Time Policy

- use integer milliseconds for shared joins between spectra, flow, and events
- store the experiment start time once as `started_at_utc`
- store per-row timestamps as absolute Unix-epoch milliseconds (e.g. `acquired_at_unix_ms`);
  do not also persist a relative/elapsed variant of the same event at write time - derive
  relative/elapsed display values from the absolute column at read time instead. A relative
  value baked in at write time can desync from its own file if the write-time anchor ever
  changes mid-file (this happened in practice in `singleLSPR` acquisition's processed-metrics
  stream - see `apps/sLSPR/acq/docs/sensorgram_improvements.md`, "Correctness fixes" C1/C2 -
  and led to the schema-6.0 removal of that stream's relative `t_ms` column)
- a live, plan/step-relative control sequence log (distinct from a spectrum/event
  acquisition timestamp) may still keep its own relative `t_ms`-style column if that is the
  quantity it's actually measuring - e.g. `singleLSPR` acquisition's experiment-control
  runtime log

## Data Policy

- append raw series instead of rewriting files during acquisition
- store derived metrics in separate processed groups
- keep human-entered annotations and presentation state separate from raw data

## Shared Python Home

The suite-level Python package for HDF standardization is:

- `packages/lspr_io`
