# Measurement File Format

This is the canonical spectroscopy measurement file layout for `singleLSPR`.

The shared implementation lives in `packages/lspr_io`.

## File Identity

Recommended root attributes:

- `schema_name = lspr_measurement`
- `schema_version = 3.0`
- `schema_major = 3`
- `schema_minor = 0`
- `format_name = experiment_run`
- `format_version = 3`
- `app_name`
- `app_version`
- `created_by`
- `created_at_utc`
- `started_at_utc`

## Core Groups

- `/axes/wavelengths_nm`
- `/data`
- `/metadata`
- `/spectra`
- `/processed`
- `/flow`
- `/devices`

## Required Data Concepts

- raw sample spectra
- dark spectra
- reference spectra
- processed metrics
- flow-state events
- pump-plan rows

## Raw Spectra

Sample spectra should keep:

- `t_ms`
- `acquired_at_unix_ms`
- `intensity`
- `integration_time_ms`
- `averages`
- `source_epoch`
- `flags`
- `dark_index`
- `reference_index`

Dark and reference spectra should keep the same core series except the baseline indices.

## Experiment Plan

The measurement file should store a tabular experiment-plan representation in metadata.
The canonical column order is exported by `lspr_io`.

## Processed Metrics

Derived values should be stored in `/processed/metrics` as appendable vectors.

## Notes

- the file format is append-oriented
- readers should tolerate unknown extra groups and fields when the schema version is compatible
- this format is shared by the acquisition and offline evaluation apps
