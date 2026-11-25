---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/io/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/dataset/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/plane_reader.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/perf_setup.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/export_stats.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/export*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/scan*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/mask/feature_map*"
---

# LSPRi: parallel work and export

Moved verbatim from `apps/LSPRi/eva/CLAUDE.md` (2026-11-25). Docs: `docs/session_carry_export_2026-10-22.md` (OME-Zarr export, Session carry, layout check before an export), `docs/export_slow_on_exfat_2026-11-20.md`, repo-root `docs/performance_tuning.md`.

- A fan-out job (export, scan, analysis step 1, Feature map) takes its worker count from `lspr_core.perf` (`get_runtime().begin(...)`); the default is **Standard** (every core but one). **A tuned job must write / find identical results at any worker count** (hash test: `tests/integration/test_lspri_export_workers.py`); never tune what is saved. A new fan-out follows `docs/performance_tuning.md` (repo root). Saved counts live in `perf_profile.json` (`perf_setup.py`, installed in `main()` only, so tests never touch it).
- The fast libtiff TIFF decoder (`plane_reader.py`) is on for every thread and verified against tifffile at run time; `LSPR_TIFF_DECODER=tifffile` switches it off.
