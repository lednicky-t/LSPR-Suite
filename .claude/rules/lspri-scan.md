---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/scan*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/frame_reduce*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/reference_pairing*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/roi_tuning*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/tasks*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/reduction*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/provenance*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/query*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/worker*"
  - "apps/LSPRi/eva/tools/**"
---

# LSPRi: scan (analysis step 1), stored numbers, ROI tuning

Docs: `docs/analysis_redesign_plan_2026-10-19.md` §15-17, `docs/spectra_analysis_methods.md`, `docs/roi_shapes_and_linking_plan_2026-11-18.md`, `docs/roi_position_tuning_2026-10-28.md`. ROI shapes / rims: `lspri-roi-model.md`.

## Cell hash and stored numbers

- **Bump `SCAN_ALGORITHM_VERSION` when a change can alter a stored number.**
- Keep "same hash => same numbers" (`tests/unit/test_lspri_scan_local_mask_keys.py`). Anything that makes one ROI's pixels depend on another ROI goes into `frame_reduce.local_roi_keys` and `ring_reach_matrix` (shared with `build_subset_layout`). The hash falls back to whole layout / whole mask when background removal uses ROIs or masks (`masks_are_global`), with no mask resolver, and for free-form ROIs.
- Circle ring radii come only from `roi_shapes.ring_radii` (the hash holds float64 bytes: one ulp makes a cell stale). Circle pixels / numbers / radii are pinned bit for bit by `tests/unit/test_lspri_scan_circle_golden.py`; only `golden_rois()` may change, a moved pin needs the maintainer.
- Never remove `stamped_by_previous_formula` recognition (`ScanStore.restamp`) without a migration (old files would rescan every masked cube).
- Scan file schema `lspri_scan` 1.1 (has `n_masked` for the Sensorgram mask marks); older files are replaced by a rescan.

## Sample / reference

- A reference ROI is scanned as one region (pixels in the `SAMPLE` column, no ring, minus every sample ROI's pixels). Pairing is decided **afterwards on stored numbers** (`analysis/reference_pairing.py`: linked references pooled by pixel, else the ring, else none), so link / unlink needs no scan.
- **`ScanResultsModule.stored()` is the paired scan** (one row per sample ROI; with no reference and no link it is the file's own object); `_stored` is the raw file, for the staleness check only. Role is in the hash only for reference ROIs.
- **Any ROI list that feeds a calculation (Spectra, Sensorgram, step table, groups, Signal map, S_bulk) comes from `RoiToolbox.sample_ids(ids)`.**

## Threads, files, workers

- Finished cubes are written by `scan_store.ScanWriter` (own thread), never by the result-collecting thread. Plain uncompressed chunks; `write_batch` never writes outside its batch.
- `snapshot_source` runs on the GUI thread, everything else off-thread (`scan_jobs.py`). Partial OME-Zarr reads are bit-identical to whole-plane reads (`ScanSettings.partial_reads` turns them off). Known bottleneck (later): CRC32C of every shard index (ideas above `_SHARD_INDEX_CACHE_SHARDS` in `plane_reader.py`).
- Performance mode keeps a warm pool (~150 MB per idle worker); workers exit when the app is gone (`scan._start_parent_watch`); a mode chosen mid-scan applies after it.
- **Tools that build app modules (`tools/analysis_stage_timing.py` `build_modules`) keep autosave suspended** (a script once saved into the real session).

## Analysis rules

- A scan runs **only** on explicit action (Scan, Fast scan, ROI tuning Accept); selection or navigation never computes ("not yet analyzed").
- Any change to a cell's inputs recomputes that cell; no materiality or pixel-selection comparison.
- Formula, Fit method, Metric settings are **never** stored or put in provenance; recomputed live. One approved exception (2026-11-28): the Sensorgram's fitted traces have a **disk copy** `sensorgram_traces.npz` beside `scan.h5` (`analysis/trace_disk_cache.py`): a start-up speed-up only, one scan and one setup, traces used only when `scan_trace.scan_fingerprint` (every array a trace is read from) and the setup (incl. the fit modules' source hash) are equal; `scan.h5` is untouched. A change outside the hashed fit modules that alters a trace needs `CACHE_VERSION` bumped.
- One `scan.h5` per session (`analysis/scan_store.py`).
- `analysis/tasks.compute_cell`, `provenance.py`, `reduction.py` are the independent **reference implementation** the scan is checked against (inputs: `tools/reference_inputs.py`).
- Fractional pixel weighting is built (`rasterize_fractional`, weighted reductions) but **not wired** (also unused by the scan: `RoiToolbox.detect_rois`, tests only): `compute_cell` still uses binary `rasterize_sample`/`rasterize_reference`. A weighted reduction must equal the unweighted one when all weights are equal.
- Qt-free modules (`tasks`, `provenance`, `reduction`, `roi/rasterize`, `roi/detection`) test with zero Qt / dataset; contract `lspri-science-no-qt` in `.importlinter` (add new pure modules there).

## ROI position tuning

- Accept copies trial numbers into `scan.h5` **bit-identical to a real scan** (pinned by a test): only when the trial's inputs are unchanged, only into a fitting file (`ScanStore.prepare(create=False)`), never for ROIs next to another ROI or with an interpolated centre. A trial scans the stored centre (0.1 px, `roi_tuning.moved_centre`).
- Tuning never moves a reference ROI and refuses a sample with links (link-aware scoring open, plan 1.2d).
- *Tune toggle / pixel cache / preview* is **shelved**: no build without a new go-ahead (`docs/single_roi_tuning_design_2026-10-28.md`).
