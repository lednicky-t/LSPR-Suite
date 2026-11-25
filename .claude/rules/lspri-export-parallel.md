---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/io/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/dataset/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/plane_reader.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/perf_setup.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/export_stats.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/export*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/scan_pool*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/scan_jobs*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/mask/feature_map*"
---

# LSPRi: parallel work and export

Docs: `docs/session_carry_export_2026-10-22.md`, `docs/export_slow_on_exfat_2026-11-20.md`, root `docs/performance_tuning.md`.

- Fan-out jobs (export, scan, analysis step 1, Feature map) take worker counts from `lspr_core.perf` (`get_runtime().begin(...)`), default **Standard**. **Identical results at any worker count** (hash test `tests/integration/test_lspri_export_workers.py`); never tune what is saved. Saved counts: `perf_profile.json` (`perf_setup.py`, installed in `main()` only, so tests never touch it).
- The libtiff decoder (`plane_reader.py`) is on for every thread, verified against tifffile at run time; `LSPR_TIFF_DECODER=tifffile` turns it off.
