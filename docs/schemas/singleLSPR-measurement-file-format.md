# LSPR Measurement File Format

This document describes the planned native HDF5 file layout for LSPR Acquisition measurement data.
It is intended as the implementation contract for writers, readers, validators, and future analysis tools.

The current application already writes HDF5 files. This specification defines the next structured schema so raw spectra, baseline spectra, device descriptions, experiment-control state, metrics, and experiment metadata can grow without breaking old files.

This is a living draft for the next-generation experiment file. The goal is to move from a narrow measurement container to a broader experiment-run package that can hold:

- raw spectra
- dark and reference spectra
- processing provenance
- derived sensorgrams and metrics
- device inventory and wiring
- authored plans
- executed runtime sequence
- solution registry and usage
- experiment metadata and tags

## Goals

- Preserve all raw sample, dark, and reference spectra.
- Support multiple dark and reference spectra, each with its own timestamp.
- Save live experiment-control state with timestamps.
- Keep the main sample matrix fast to append and fast to read.
- Store processing metrics such as centroid and polynomial maximum as aligned time series.
- Store enough metadata to reproduce the experiment context.
- Allow future devices and fields to be added without breaking existing readers.
- Avoid rewriting the whole file during live measurement.

## Container

The native measurement file should use HDF5 with extension `.h5`.

All live streams should be stored in chunked, appendable datasets:

```text
maxshape=(None, ...)
chunks=True
```

Writers should append new rows to datasets. They should not require loading existing datasets into memory.

## Versioning

Every measurement file must include both file-format versioning and application versioning.

Recommended root attributes:

```text
attrs["schema_name"] = "lspr_measurement"
attrs["schema_version"] = "4.0"
attrs["schema_major"] = 4
attrs["schema_minor"] = 0
attrs["format_name"] = "experiment_run"
attrs["app_version"] = "<application version>"
attrs["created_by"] = "LSPR Acquisition"
attrs["created_at_utc"] = "YYYY-MM-DDTHH:MM:SS.sssZ"
attrs["started_at_utc"] = "YYYY-MM-DDTHH:MM:SS.sssZ"
```

Compatibility rules:

- Readers must reject files with unknown `schema_name`.
- Readers should refuse newer major versions unless explicitly allowed.
- Readers should accept newer minor versions by ignoring unknown groups, datasets, columns, and attributes.
- New compatible fields require a minor version bump.
- Incompatible renames, unit changes, or semantic changes require a major version bump.
- Deprecated fields should remain readable for at least one major version when practical.

Recommended manifest group:

```text
/manifest
  schema_name
  schema_version
  schema_major
  schema_minor
  format_name
  app_version
  created_by
  created_at_utc
  started_at_utc
  export_host
  export_user
```

The root attributes are the fast compatibility check. The manifest group is the human-readable summary that can hold extra exporter details later.

Dataset-level additions should include:

```text
attrs["units"] = "<unit text>"
attrs["description"] = "<short description>"
attrs["schema_version_added"] = "3.0"
```

Table-like datasets should also include:

```text
attrs["columns"] = ["column_a", "column_b", ...]
```

## Time Model

The primary time coordinate is relative integer milliseconds:

```text
t_ms int64
```

`t_ms` is measured from `started_at_utc`. This should be the universal join key for spectra, flow state, device events, and metrics.

Rules:

- Use millisecond resolution only.
- Use `int64` for timestamps.
- Store `started_at_utc` once at the root.
- Optional absolute timestamps may be stored as Unix milliseconds for convenience.
- Readers should align streams by selecting the latest state row at or before a spectrum timestamp.

Recommended optional absolute time field:

```text
acquired_at_unix_ms int64
```

## Top-Level Layout

```text
/
  /manifest
  /axes
  /devices
  /metadata
  /plans
  /runs
  /raw
  /processed
  /events
```

Unknown top-level groups should be ignored with a warning.

Recommended semantic split:

- `manifest`: file identity and exporter identity
- `metadata`: static experiment context
- `devices`: connected devices, inventory, and wiring
- `plans`: authored configuration and assignment tables
- `runs`: measured runtime sequence and state changes
- `raw`: append-only raw spectra and baselines
- `processed`: derived spectra, metrics, and provenance
- `events`: human-readable event log

## Axes

```text
/axes/wavelengths_nm float64 [n_wavelength]
```

Attributes:

```text
units = "nm"
description = "Wavelength axis shared by spectra matrices."
```

The measurement writer should lock the wavelength axis when measurement starts. Spectra acquired on a different axis must be resampled before appending, and the row metadata should record that resampling occurred.

## Raw Spectra

Raw spectra are stored by role.

```text
/raw/spectra/sample
/raw/spectra/dark
/raw/spectra/reference
```

Each role group should contain:

```text
t_ms                 int64   [n]
acquired_at_unix_ms  int64   [n] optional
intensity            float32 [n, n_wavelength]
integration_time_ms  float32 [n]
averages             int32   [n]
source_epoch         int64   [n]
flags                uint32  [n] optional
```

Sample spectra should also contain:

```text
dark_index           int64 [n_sample]
reference_index      int64 [n_sample]
```

Index rules:

- `dark_index = -1` means no dark spectrum was assigned.
- `reference_index = -1` means no reference spectrum was assigned.
- Non-negative values point to rows in `/spectra/dark` or `/spectra/reference`.
- The indices should represent the dark/reference spectra active when that sample row was acquired or processed.

This preserves multiple baseline acquisitions and makes each sample reproducible.

## Baseline Events

Baseline changes may also be recorded as an event log:

```text
/baselines/events
  t_ms      int64
  kind      string  # dark or reference
  index     int64
  action    string  # acquired, selected, cleared
```

This group is optional but recommended because it makes the experiment timeline easier to inspect.

## Processed Metrics

Metrics derived from sample spectra should be stored as appendable numeric vectors.

```text
/processed/metrics
  t_ms                int64   [n_metric]
  sample_index        int64   [n_metric]
  centroid_nm         float64 [n_metric]
  smoothed_max_nm     float64 [n_metric]
  poly_max_nm         float64 [n_metric]
  gaussian_center_nm  float64 [n_metric]
  fwhm_nm             float64 [n_metric]
  mse                 float64 [n_metric]
  snr                 float64 [n_metric]
```

Rules:

- Missing or unavailable metric values should be `NaN`.
- New metrics may be added as new datasets.
- `sample_index` links the metric row to `/spectra/sample/intensity`.

## Plans

The authored experimental plan should live here, separate from the runtime execution log.

Recommended authored plan groups:

```text
/plans/device_plan
/plans/experiment_plan
/plans/assignment_tables
/plans/switch_table
/plans/valve_table
/plans/connector_mapping
```

Rules:

- `plans` stores the designed recipe, not the live execution trace.
- It should be safe to export and import without runtime-only fields.
- App-only helper rows, such as a synthetic pause template, should not be treated as authored plan rows unless the app explicitly decides to persist them as UI state.

### Device Plan

This section describes what devices were intended to be used and how.

Recommended fields:

- selected device identifiers
- enabled/disabled state
- device role in the run
- channel assignment
- port assignment
- backend / driver name
- model / serial number
- connection parameters

### Experiment Plan

This section may continue to hold the current step table concept:

- step order
- duration
- per-channel flow settings
- valve state
- switch position
- color
- comments

### Assignment Tables

This section should hold mapping tables such as:

- switch port to solution label
- valve state labels
- pump channel to chip inlet
- chip channel to solution role
- tubing and connector mapping

## Runtime Runs

The measured execution should live here.

Recommended groups:

```text
/runs/executed_sequence
/runs/flow_state
/runs/device_events
/runs/recording_events
```

Rules:

- runtime data is append-only
- runtime data may differ from the authored plan
- hold, pause, skip, and jump events must be represented explicitly
- device state snapshots belong here, not in the authored plan

## Spectra

Raw and baseline spectra are appendable runtime data.

Recommended layout:

```text
/raw/spectra/sample
/raw/spectra/dark
/raw/spectra/reference
```

Sample spectra should also contain:

```text
/raw/spectra/sample/dark_index
/raw/spectra/sample/reference_index
```

## Processed

Derived outputs should live here.

Recommended groups:

```text
/processed/metrics
/processed/sensorgrams
/processed/provenance
```

This section should store:

- centroid trace
- smoothed maximum trace
- polynomial maximum trace
- Gaussian center trace
- FWHM trace
- signal-to-noise trace
- processing settings and provenance
- algorithm version or implementation note

## Devices

Device-specific metadata and event streams live under `/devices`.

Recommended fixed groups:

```text
/devices/inventory
/devices/link_map
/devices/pump_1
/devices/pump_2
/devices/valve_1
/devices/mswitch_1
/devices/spectrometer_1
```

Device inventory should say:

- what was connected
- which role it played
- whether it was selected for the run
- model
- serial number
- backend
- port at start

Link maps should say:

- how the hardware was linked to the experiment
- pump channel to chip inlet mapping
- valve table to solution table mapping
- switch port to solution mapping
- connector and tubing mapping

## Metadata

Metadata that describes the experiment context should live under `/metadata`.

Recommended sections:

```text
/metadata/user
/metadata/experiment
/metadata/sensor
/metadata/measurement_setup
/metadata/environment
/metadata/solutions
/metadata/user_fields
/metadata/tags
```

### User

```text
operator_name
operator_id
organization
contact
```

### Experiment

```text
title
experiment_id
sample_id
analyte
buffer
notes
```

### Sensor

```text
sensor_type
sensor_name
sensor_id
sensor_revision
surface_chemistry
batch_id
manufacturer
notes
```

### Measurement Setup

This section should describe the full experimental setup as used in the run:

```text
spectrometer_model
integration_time_ms
averages
correct_dark_counts
correct_nonlinearity
live_rate_hz
experiment_control_enabled
selected_devices
processing_profile
```

### Environment

Optional contextual fields:

```text
temperature_c
humidity_percent
pressure_hpa
notes
```

### Solutions

This section should be a structured solution registry plus usage history.

Recommended registry fields:

```text
solution_id
name
role
preparation_date
expiry_date
concentration
concentration_unit
refractive_index
stock_id
batch_id
supplier
solvent
pH
notes
```

Recommended usage log fields:

```text
t_ms
step_index
solution_id
port_or_channel
event
notes
```

### Tags

Tags are useful for later search and machine processing.

Recommended storage:

```text
/metadata/tags
  tags = ["aptamer", "screening", "baseline-stability"]
```

Tags should be treated as a lightweight supplement, not a replacement for structured metadata.

## Events

A general run-level event log may be useful for debugging and audit history:

```text
/events
  t_ms
  level
  source
  message
```

This should not replace structured numeric streams. It is for human-readable history.

## Writer Behavior

Recommended live writer behavior:

1. Create manifest, metadata, and fixed device identity when measurement starts.
2. Lock `/axes/wavelengths_nm`.
3. Append sample spectra as they are acquired.
4. Append dark and reference spectra whenever they are acquired.
5. Store `dark_index` and `reference_index` on each sample row.
6. Append metric rows after processing.
7. Append flow state rows when state changes and optionally at heartbeat intervals.
8. Append runtime events for hold, pause, skip, jump, and recording transitions.
9. Flush periodically and on measurement stop.
10. Close the file cleanly at measurement end.

The writer may keep small in-memory counters for row indices. It should not read the full datasets to append new rows.

## Current Implementation Notes

The current writer stores a simpler v3 layout and is still being standardized.

That is okay for now, but the v4 target should converge toward the structure above so future tools can inspect:

- what was planned
- what was actually run
- what devices were connected
- what solutions were used
- how the metrics were calculated
- which app version created the file

## Open Decisions

- Exact HDF5 representation for string-heavy tables: compound datasets, parallel datasets, or 2D UTF-8 string arrays.
- Whether to store absolute Unix milliseconds for every row or only root `started_at_utc`.
- How verbose the device inventory should be by default.
- How much of the processing provenance should be written as structured fields versus JSON.
- Whether tags should allow hierarchies like `sensor:type:spr` or remain flat strings.
- How to version the experimental plan groups independently from the file format itself.

