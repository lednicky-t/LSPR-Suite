# Architecture Overview

This document describes the top-level design of the LSPR Suite.
For per-subsystem details follow the links below.

---

## Ecosystem Map

The suite is an umbrella repository containing four applications and three shared packages.

```
LSPR Suite
├── apps/
│   ├── sLSPR/acq   singleLSPR Acquisition   — live spectrometer control, experiment execution
│   ├── sLSPR/eva   singleLSPR Evaluation    — offline single-spot spectral analysis
│   ├── LSPRi/eva   LSPRimaging Evaluation   — offline TIFF image-stack analysis
│   └── suite_launcher                       — startup selector for all four apps
└── packages/
    ├── lspr_core   domain models, schema identity, experiment plan primitives
    ├── lspr_io     HDF5/session file helpers, version stamping, migration readers
    └── lspr_ui     Qt theme tokens, icon helpers, application bootstrap
```

`LSPRimaging Acquisition` is reserved as a separate repository and is not yet implemented.

---

## Package Boundaries

`lspr_core`, `lspr_io`, and `lspr_ui` must not depend on each other or on any app package.
App packages may depend on shared packages, but never on other app packages.

Rule of thumb:
- Scientific domain objects → `lspr_core`
- File read/write and schema rules → `lspr_io`
- Qt theme and icon helpers → `lspr_ui`
- App-specific hardware, GUI, and analysis code stays inside the app package

---

## Data Flow (singleLSPR Acquisition)

```
Spectrometer (Ocean / simulated)
    → raw spectrum (lossless)
    → async HDF5 writer (every frame, no drops)
    → processing pipeline (may skip stale frames)
    → GUI plot (freshness-based, drops allowed)
```

Raw recording and display are separate layers. See
[`apps/sLSPR/acq/docs/runtime_pipeline_architecture.md`](../../apps/sLSPR/acq/docs/runtime_pipeline_architecture.md)
for the authoritative rules.

---

## Data Flow (LSPRimaging Evaluation)

```
TIFF image stack on disk
    → image loader / format detector
    → ROI manager
    → per-ROI absorbance spectrum reconstruction
    → spectral metric extraction
    → export (CSV / HDF5)
```

---

## HDF5 File Contract

All measurement files across the suite follow a shared schema defined in
[`docs/schemas/hdf_standard.md`](../schemas/hdf_standard.md).

Key rules:
- Every file carries `schema_name`, `schema_version`, `app_name`, `app_version`, `created_at_utc`.
- Raw data is appended; derived data lives in separate groups.
- Breaking changes require a major version bump; additive changes require a minor bump.

---

## Further Reading

| Topic | Document |
|-------|----------|
| Shared package and dependency split | [`general/dependency-matrix.md`](general/dependency-matrix.md) |
| Suite launcher design | [`general/app-selector.md`](general/app-selector.md) |
| HDF5 standardization notes | [`general/hdf_standardization.md`](general/hdf_standardization.md) |
| Plot view cache design | [`general/plot_view_cache.md`](general/plot_view_cache.md) |
| singleLSPR acquisition pipeline rules | [`apps/sLSPR/acq/docs/runtime_pipeline_architecture.md`](../../apps/sLSPR/acq/docs/runtime_pipeline_architecture.md) |
| LSPRimaging versioning and repo practices | [`apps/LSPRi/`](apps/LSPRi/README.md) |
| HDF5 schema contracts | [`../schemas/`](../schemas/) |
| Decision records | [`../decisions/`](../decisions/) |
