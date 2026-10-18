# Test suite overview (2026-10-18)

Numbers from the headless per-file run of 2026-10-18 (6 files at a time, so each file's seconds are inflated compared with running it alone).

## Size

| | files | tests | seconds (summed) |
|---|---|---|---|
| `tests/unit/` (logic, no files) | 169 | 1979 | 978 |
| `tests/integration/` (Qt windows, HDF5, fake devices) | 118 | 1649 | 2246 |
| LSPRi rewrite (`test_lspri_rewrite_*`) | 69 | 1449 | 1454 |
| LSPRi, shared / older | 121 | 1225 | 1276 |
| singleLSPR + shared packages | 92 | 888 | 477 |
| Suite launcher / install | 5 | 66 | 16 |
| **All** | **287** | **3628** | **3223 (about 54 min in one process; 9.4 min with 6 in parallel)** |

110 files (1145 tests) take under 5 s each; 25 files take 30 s or more (744 tests, 1457 s together), nearly all of them
full-window GUI tests. Slowest per test: `lspri_workflow_panel_subsection_restore` (about 12 s per test),
`lspri_rewrite_visual_settings_restore` (about 8.5 s), `lspri_group_table_selection` (about 4 s). They build whole main windows.

## Which tests to run when

- While editing: the test file(s) of the code you touched (seconds).
- Before committing: every test file of the app you touched, one process per file, in parallel (LSPRi: 190 files, about 8 to 10 min).
- Anything in `packages/`, several apps, or before a release: everything. CI runs everything in one process (about 1 hour).
- The cheap, high-value ones to keep forever: pure-logic tests of the science and of data safety (NaN handling, atomic writes,
  cache poisoning, schemas, pump calibration, ROI maths).

## Findings

- No file is broken or non-functional: all 287 import, run and pass (after the fixes of the same day). One test is skipped (a Windows thread-timer helper; reason not checked).
- Obsolete later, not now: 18 files test the **old** LSPRi window (`lspr_imaging_app.gui.main_window`): busy_progress_speed_text,
  chromatic_reference_points_toggle_sync, live_preview_prompt_deferred, sensorgram_completion_summary, wavelength_zero_exclusion,
  chromatic_seed_preserves_analysis_cache, close_cancels_sensorgram, drag_undo_commit, group_table_selection, import_export_status_reporting,
  metadata_gui, ome_zarr_chunk_estimate_widget, ome_zarr_export_controller_move, ram_only_backup_toggle_widget,
  roi_array_membership_reindex, roi_table_diameter_edit, sensorgram_stale_mid_run_settings_change, sensorgram_time_axis.
  `run.py` still starts the old app, so they still protect something real; retire them with the old code at the cut-over (audit item D3).
  Also dead with its code (audit D5): the test of `workflow/mask_highlight_actions.py`.
- Risk: 91 test files raise `unittest.SkipTest` when `lspr_imaging_app` rewrite modules cannot be imported. A real import
  error in app code (a typo, a missing dependency) then shows as green "skipped" instead of red. Narrower rule suggested: skip only when
  the submodule folder is absent, let a genuine `ImportError` fail. Not changed (91 files; hiding errors is on the approval list).
- Tests can be wrong too. On 2026-10-18 the red CI came from stale tests, a timing flake, a measuring artefact and test pollution
  (see `test_platform_and_ci_failures_2026-10-18.md`), not from broken measurements.
