# LSPR Measurement File Format

This document has moved. See
[`apps/sLSPR/acq/docs/measurement_file_format.md`](../../apps/sLSPR/acq/docs/measurement_file_format.md)
for the current, canonical HDF5 measurement file format contract.

This repo-root copy previously duplicated that document nearly verbatim and repeatedly drifted
out of sync with it (most recently caught during the 2026-07-21 schema 6.0 timestamp change).
Keeping one canonical copy next to the code that implements it removes that failure mode
entirely, at the cost of one extra click from the repo root. See
[`docs/schemas/hdf_measurement_format.md`](./hdf_measurement_format.md) for the short,
suite-wide summary if you just need the high points without leaving `docs/schemas/`.
