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
- keep relative elapsed timing in `t_ms` where possible

## Data Policy

- append raw series instead of rewriting files during acquisition
- store derived metrics in separate processed groups
- keep human-entered annotations and presentation state separate from raw data

## Shared Python Home

The suite-level Python package for HDF standardization is:

- `packages/lspr_io`
