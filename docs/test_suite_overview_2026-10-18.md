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
- Anything in `packages/`, several apps, or before a release: everything, with `python tools/run_tests_per_file.py` (one process per file, several at a time). A full run took 38 min here on 2026-10-30 (6 files at a time, 4602 tests). CI runs the same command since 2026-10-30; until then it ran everything in one process (1 hour in October, 3 to 5 hours by the end of the month).
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
- Fixed (same day): 91 test files used to `raise unittest.SkipTest` on any `ImportError` of the LSPRi rewrite modules, so a real
  import error in app code (a typo, a renamed module, a missing dependency) showed as green "skipped" instead of red.
  They now call `skip_if_lspri_rewrite_absent(exc)` from `tests/_paths.py` and then `raise`: the file is skipped only when
  `apps/LSPRi/eva/src/lspr_imaging_app/app_rewrite.py` is not on disk (submodule not checked out, or on a branch without the
  rewrite). With the code present, an `ImportError` fails the file (pytest "1 error", unittest "FAILED (errors=1)"; proven with a
  throwaway file holding a bad import). Expect CI to turn red once if some file was secretly skipping there before.
- Load-sensitive tests (fail when the machine is busy, pass alone): in one 20-minute parallel run (the usual one takes 9) these failed and
  then passed alone: `test_live_processing_worker.py` (relays child logs; fewer results when frames queue up; its child process also
  kept the runner's pipe open until the 20-minute timeout) and `test_lspri_rewrite_visual_settings_restore.py::...highlight_range_is_persisted_with_its_dataset`.
  Earlier: some Histogram tests. If one of these fails in CI, rerun it alone before looking for a cause.
- Tests can be wrong too. On 2026-10-18 the red CI came from stale tests, a timing flake, a measuring artefact and test pollution
  (see `test_platform_and_ci_failures_2026-10-18.md`), not from broken measurements.
