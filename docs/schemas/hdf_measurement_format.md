# Measurement File Format

This is the canonical spectroscopy measurement file layout for `singleLSPR`.

The shared implementation lives in `packages/lspr_io`.

## File Identity

Recommended root attributes:

- `schema_name = lspr_measurement`
- `schema_version = 6.3`
- `schema_major = 6`
- `schema_minor = 3`
- `format_name = experiment_run`
- `format_version = 6`
- `app_name`
- `app_version`
- `created_by`
- `created_at_utc`
- `started_at_utc`

## Core Groups

- `/data/wavelengths_nm`
- `/data`
- `/metadata`
- `/data/spectra`
- `/processed`
- `/devices`

## Required Data Concepts

- raw sample spectra
- dark spectra
- reference spectra
- processed metrics
- experiment-control runtime logs
- pump-plan rows in metadata
- assignment tables for switch labels, valve labels/colors, and palette entries
- the valve assignment table is unified as a single state/label/color table
- optional concentration/concentration_unit/notes per switch port (`switch_solution_details`,
  schema 6.3+), keyed by port to join against the switch-label assignment table
- a device inventory snapshot at `/devices/inventory/devices` (schema 6.3+): one row per known
  device (label, type, role, driver, endpoint, display_name, model, serial_number, connected)

## Raw Spectra

Sample spectra should keep:

- `acquired_at_unix_ms` (sole per-row timestamp as of schema 6.0 - the earlier relative
  `t_ms` column was removed; see the "Time Model" section of
  `apps/sLSPR/acq/docs/measurement_file_format.md` for why)
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

`/processed/metrics` also carries its own schema/version metadata and a human-readable
processing-settings snapshot under `/processed/metrics/config/processing_settings_json`.
The derived metric vectors should also include `acquired_at_unix_ms` so they can be
joined back to the originating spectrum with absolute UTC precision.

## Notes

- the file format is append-oriented
- readers should tolerate unknown extra groups and fields when the schema version is compatible
- this format is shared by the acquisition and offline evaluation apps
