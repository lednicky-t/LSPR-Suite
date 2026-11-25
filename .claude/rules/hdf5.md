---
paths:
  - "packages/lspr_io/**"
  - "apps/**/storage/**"
  - "apps/**/io/**"
---

# Storage and HDF5 rules

The authoritative contract is `docs/schemas/hdf_standard.md`. Read it before changing a format.

- Every file must carry `schema_name`, `schema_version`, `app_name`, `app_version`, and `created_at_utc`.
- Raw data is appended, never overwritten. Derived or processed data goes in separate groups.
- Readers must reject unknown schema names and incompatible major versions.
- Breaking changes need a major version bump; additive changes need a minor bump.
- Do not change a saved data format without a migration strategy. Ask first.

File handling:
- Never overwrite raw data without explicit user confirmation.
- Save metadata with measured data: timestamps, instrument settings, calibration references, software version, and analysis parameters.
- Prefer open formats: CSV for simple tables, TIFF for images, HDF5/Zarr/NetCDF for multidimensional data.
- Keep export code separate from analysis code.
- Validate file formats and show clear errors for incompatible files.
- No hardcoded absolute paths.
