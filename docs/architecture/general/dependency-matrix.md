# Suite Dependency Map

This note captures the current recommended split for the LSPR Suite.

## Shared Packages

These should live in `packages/` and be usable by both singleLSPR and LSPRimaging:

- `lspr_core` - common domain objects, versioning, units, validation, timestamps, identifiers (exists)
- `lspr_io` - HDF5/session read-write helpers, schema detection, migration helpers (exists)
- `lspr_acq_shell` - shared *live-acquisition* application code: fluidics device
  framework, experiment-control plan editing/execution, sensorgram plotting,
  session/run bookkeeping, async HDF5-writer plumbing (exists, scaffold only as
  of 2026-08-06 - see `lspri_acq_build_log.md` for extraction progress)
- `lspr_flow`, `lspr_analysis`, `lspr_imaging` - not yet created; aspirational
  groupings for pump-plan/step logic, sensorgram math, and ROI/session
  abstractions respectively. Some of this scope now overlaps with
  `lspr_acq_shell` above - reconcile before creating any of these three rather
  than duplicating what's already being extracted there.

### Pairwise-independence rule and its one exception

Per `docs/architecture/overview.md`: `lspr_core`, `lspr_io`, and `lspr_ui` must
not depend on each other or on any app package - they are peers.
`lspr_acq_shell` is *not* a fourth peer in that set: it is shared **application**
code (Qt widgets, device runtime), not a domain/IO/theme primitive, and it
legitimately depends on all three. This is a deliberate, documented exception,
not a violation - see `packages/lspr_acq_shell/README.md`.

## Shared Runtime Dependencies

Likely shared across most apps:

- `numpy`
- `scipy`
- `h5py`
- `PyQt6`
- `pyqtgraph`
- `PyYAML`
- `pydantic` if we adopt structured validation
- `pytest` for compatibility tests

## singleLSPR-Specific Dependencies

- `pyserial`
- `seabreeze`
- device/controller adapters
- any hardware SDKs for the spectrometer and flow devices

## LSPRimaging-Specific Dependencies

- `Pillow`
- `tifffile`
- `scikit-image`
- `zarr` / `ome-zarr` if the image stacks grow large enough to justify chunked storage
- optional image-codec helpers

## UI Decoration Dependencies

If we want consistent icon/theme resources across apps, keep those as UI assets rather than core logic. They can be reused, but they should not be required by `lspr_core`.

## Rule of Thumb

- If a dependency is about file formats, validation, or sensorgram math, it belongs in shared packages.
- If a dependency talks to hardware or interprets imaging pixels, keep it in the specific app.
- If a dependency only affects look and feel, keep it in the app UI layer.
