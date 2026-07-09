# LSPR Experiment Control Plan Format

This document describes the native text format for editable LSPR experiment-control plans.

The preferred native extension is `.flow.yaml`. Regular `.yaml` and `.yml` are also accepted. The app keeps CSV/TXT import and export for compatibility with existing pump-plan tables, but YAML is the format intended to grow with future devices.

## Goals

- Human-readable and editable in a text editor.
- Explicit units instead of units hidden in column names.
- Stable device IDs with user-editable labels.
- Forward compatible with additional pumps, valves, switches, and other devices.
- Older app versions may skip unknown devices or fields while still loading supported parts.

## Compatibility Rules

- `format.name` should be `LSPR Experiment Plan`.
- `format.version` is required. Version `1` is the first native schema.
- Unknown top-level sections should be ignored with a warning.
- Unknown device IDs should be skipped with a warning.
- Missing devices mean "no action" for that device.
- Missing pump channels default to `flow: 0` and `direction: OFF`.
- Device IDs are stable identifiers, for example `pump_1` or `switch_1`.
- Labels are display text and may be changed without changing device identity.
- Newer versions may add fields, devices, or metadata without breaking older readers.

## Units

The current native writer exports:

```yaml
units:
  flow: uL/min
  time: s
  tube_diameter: mm
```

Native import accepts `uL/min` directly. It also accepts `ml/min` for flow and converts it to `uL/min`.

## Example

```yaml
format:
  name: LSPR Experiment Plan
  version: 1

metadata:
  created_by: LSPR Acquisition
  exported_at: '2026-05-02T14:30:00'
  notes: ''

units:
  flow: uL/min
  time: s
  tube_diameter: mm

devices:
  pumps:
    pump_1:
      label: Reglo ICC
      channels:
        ch1:
          label: CH1
          tube_mm: 0.25
        ch2:
          label: CH2
          tube_mm: 0.25
        ch3:
          label: CH3
          tube_mm: 0.25
        ch4:
          label: CH4
          tube_mm: 0.25
  valves:
    valve_1:
      labels:
        open: L
        close: R
      display_labels:
        open: Open
        close: Close
  switches:
    switch_1:
      ports:
        1: empty
        2: MCH
        3: SSC
        4: CB

steps:
  - id: 1
    duration_s: 300
    color: '#D0CECE'
    comment: H20_4F_3P
    devices:
      pump_1:
        ch1:
          flow: 0
          direction: CW
        ch2:
          flow: 0
          direction: CW
        ch3:
          flow: 10
          direction: CW
        ch4:
          flow: 20
          direction: CW
      valve_1:
        state: open
      switch_1:
        port: 3
```

## Extending Devices

A future two-pump plan can add another pump without changing the step format:

```yaml
devices:
  pumps:
    pump_1:
      label: Main pump
      channels:
        ch1: {label: CH1, tube_mm: 0.25}
    pump_2:
      label: Reagent pump
      channels:
        ch1: {label: Reagent A, tube_mm: 0.50}

steps:
  - id: 1
    duration_s: 60
    devices:
      pump_1:
        ch1: {flow: 20, direction: CW}
      pump_2:
        ch1: {flow: 50, direction: CCW}
```

Current app versions only apply `pump_1`, `valve_1`, and `switch_1`. Other device IDs are reported and skipped.

## CSV/TXT Compatibility

CSV/TXT export keeps the legacy semicolon-separated table layout:

```text
Step;Ch-1 Flow [ml/min];Ch-1 Direction;Ch-1 Tubesize [mm];...;Time;Valve;Color;Descritption;;Solution;volume:?L
```

This remains useful for older scripts and external pump-plan tables. For new workflows, prefer `.flow.yaml`.
