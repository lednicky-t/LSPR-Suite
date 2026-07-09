# singleLSPR Acquisition

This folder documents the acquisition app moved from the standalone `LSPR` repository.

## Intended Role

- control spectrometer and flow hardware
- acquire raw spectra and runtime events
- write canonical `.h5` experiment files
- export or import experiment plans for compatibility
- provide lightweight live preview only

## Architecture Notes

- [Two-level GUI scheduler](./two_level_gui_scheduler.md) for the live-data and maintenance split

## Shareable Suite Dependencies

These are strong candidates for shared suite packages:

- `lspr_core` for schema identity, shared models, and versioning
- `lspr_flow` for pump-plan parsing and step editing rules
- `lspr_io` for HDF5 session file IO and migrations
- `lspr_analysis` for sensorgram math, metrics, and time-series alignment

## Acquisition-Specific Dependencies

Keep these local to the acquisition app:

- hardware drivers and SDK wrappers
- `pyserial`
- `seabreeze`
- any spectrometer-specific fallback or simulation code
- device-control UI logic

## Current Runtime Dependencies

From the copied app package metadata, these are present today:

- shared-ish UI/runtime: `PyQt6`, `pyqtgraph`, `numpy`, `scipy`, `h5py`, `PyYAML`
- acquisition-specific: `pyserial`, `seabreeze`
- UI asset helper: `tabler-qicon`

## Next Normalization Step

Give this app a local `pyproject.toml` that depends on the shared suite packages instead of carrying all reusable code in the app itself.
