# LSPRimaging Evaluation

This folder now hosts the evaluation app moved from the standalone `LSPRimaging` repository.

## What Lives Here

- the existing `src/lspr_imaging_app` application code
- imaging evaluation docs
- the repo-level metadata that belongs to the evaluation app
- `_refs/` for local historical references

## What Should Be Shared

The evaluation app should depend on shared suite packages for:

- experiment/session metadata
- experiment plan parsing
- schema/version handling
- ROI and sensorgram math
- HDF5/Zarr IO helpers

## What Should Stay Local

Keep these in this app rather than moving them into shared packages:

- TIFF and image-stack loading details
- ROI visualization controllers
- image rendering and overlay code
- imaging-specific preprocessing
- app-specific UI workflows

## Current Imaging Dependencies

From the moved app requirements, the main split is:

- shared with the suite: `PyQt6`, `pyqtgraph`, `numpy`, `scipy`, `h5py`-style session IO
- imaging-specific: `Pillow`, `tifffile`, `scikit-image`, `zarr`-style stack support
- UI extras: `tabler-icons`, `lucide`

## Next Normalization Step

Add a local `pyproject.toml` here that depends on the shared suite packages instead of hard-coding everything in one place.

