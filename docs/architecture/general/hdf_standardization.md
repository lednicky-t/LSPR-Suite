# HDF Standardization

The suite should treat HDF layout and versioning as a shared contract, not an app detail.

## Shared Ownership

- `packages/lspr_io` owns the reusable schema constants and HDF helpers.
- `packages/lspr_core` owns the experiment-plan model and related timing logic.
- `docs/schemas/` owns the canonical human-readable specification.

## What Belongs Here

- root file identity fields
- measurement session metadata
- standardized dataset and table column names
- compatibility rules for readers and writers
- migration notes when a file layout changes

## What Should Stay App-Specific

- hardware driver implementation
- GUI widgets and workflows
- domain-specific analysis that does not affect the shared file contract

## Rule

If a format is written by more than one app, define it here first and implement it in `lspr_io` before wiring app code to it.
