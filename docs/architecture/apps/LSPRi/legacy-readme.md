# LSPRimaging

Desktop app for LSPR image-stack evaluation.

For local setup and dataset placement, see [`DATA.md`](DATA.md).
For the ROI refactor plan and versioning approach, see:

- [`docs/roi_editor_branch_plan.md`](docs/roi_editor_branch_plan.md)
- [`docs/roi_implementation_direction.md`](docs/roi_implementation_direction.md)
- [`docs/roi_table_direction.md`](docs/roi_table_direction.md)
- [`docs/versioning_and_repo_practices.md`](docs/versioning_and_repo_practices.md)
- [`docs/format_versioning.md`](docs/format_versioning.md)

Current first version:

- Loads TIFF images named like `imLCTFatWL480Frame0.tiff`
- Parses wavelength and frame from filenames
- Displays one image at a time
- Supports per-spot ROI evaluation with local background comparison
- Reconstructs per-ROI absorbance spectra
- Computes simple spectral metrics and exports per-spot results

Planned next steps:

- More ROI shapes and editable background strategies
- Faster batch processing and caching
- Better spot/feature fitting controls
- Time-series export across many frames
