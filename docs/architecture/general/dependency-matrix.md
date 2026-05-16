# Suite Dependency Map

This note captures the current recommended split for the LSPR Suite.

## Shared Packages

These should live in `packages/` and be usable by both singleLSPR and LSPRimaging:

- `lspr_core` - common domain objects, versioning, units, validation, timestamps, identifiers
- `lspr_flow` - pump plan / step model, experiment plan import-export, runtime step editing logic
- `lspr_io` - HDF5/session read-write helpers, schema detection, migration helpers
- `lspr_analysis` - reusable sensorgram math, smoothing, metric extraction, alignment helpers
- `lspr_imaging` - ROI/session abstractions that can be shared between acquisition preview and offline evaluation

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

