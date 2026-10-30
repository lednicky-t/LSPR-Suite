# Test platform, and the CI failures of 2026-10-18

## What was wrong

GitHub's `tests` workflow (`python -m unittest discover -s tests -v`) had been red for several pushes. Reproduced locally with
the same command: 8 failures of 3610 tests (about 1 hour).

| Failures | Cause | Fix |
|---|---|---|
| `test_launcher` registry; 2 x `test_lspri_rewrite_ui_state` (reference frame); `test_lspri_rewrite_frame_roles` dock label | Stale tests: the app moved on (new `lspri_eva_rewrite` target; per-dataset `reference_frame` UI key removed by commit c83b1f5; `_with_role_prefix` now calls `_role_word`). | Tests updated or removed. The saved reference frame is covered by the frame-roles round trip. |
| 2 x `test_lspri_rewrite_overlay_layer_cache` (cached picture vs direct painting) | The pixel limits (3/255) were measured headless. On a real display they depend on the screen scale and on text smoothing: at 125 % about 2000 edge pixels sit on slightly different sub-pixel positions (overlay extent and centre of mass agree to within 1 px and 0.25 px: no scaling or misplacement); at 100 % only a few label-text pixels differ. | Tests now run headless (below). No app change. |
| `test_overlay_cache_option::test_turning_the_cache_off...` | Timing flake. It waited a fixed 0.6 s for the first image render; on a busy machine the render landed later, changed the view range and cleared the cache mid-test. With no wait at all it fails 3 of 3 times with the same message. | Waits for `ImagePanel.image_rendered` (up to 30 s), then drains queued events. |

## The test platform

Nothing in the repo set Qt's platform, although the docstrings and notes assume headless (`offscreen`). `tests/__init__.py` now does
`os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`; every Qt test file reaches it through `from tests._paths import ...`.
To look at the tests on a real display: set `QT_QPA_PLATFORM=windows` first.

Headless Qt on Windows has no system fonts (`tests/_qt_fonts.py`). Loading them is not enough for a widget that uses the default font:
the default stays the generic stand-in ("Sans Serif"), so set the font on the widget too (see
`test_pump_display_global_highlight.py`).

## Still open

- (Fixed, same day) `test_lspri_rewrite_roi_table_panel.py::test_toolbar_icons_are_painted_as_large_as_the_ribbons`
  (colour icon "18 px, needs 19"; 14 vs 15 headless). The icon was NOT smaller: since 2026-10-12 (9a59c5a) the colour button is a
  filled rainbow droplet, and the test, written 2026-10-07, compares it with an outline droplet in grey. Compared without the colour
  effect, both droplets have the same footprint (16 wide x 20 tall at 22 px icon size). The test's `_painted_extent` read the grabbed
  picture with `QImage.pixel()`, which on a pre-multiplied picture returns darkened values for soft edge pixels (a green edge pixel
  (25, 255, 94) at alpha 73 reads (7, 73, 27)). Its contrast limit (sum of channel differences > 120) then dropped the rainbow's
  faint green and blue edge columns but kept the grey icon's. Fix: convert the grab to straight `Format_ARGB32` first. The
  regression it guards still shows: without `padding: 0` the measured widths fall from 20 / 16 / 18 px to 6 / 4 / 4.
  Lesson: a pixel test that thresholds on colour contrast depends on the icon's hue; measure the alpha or the straight colours.
- (Fixed, same day) `test_lspri_rewrite_image_panel.py::...::test_spin_boxes_share_one_width_fitted_to_their_text`, which failed once
  in a headless full `unittest discover` run (55 != 42) and passed alone. Test pollution, reproduced on demand with three files in one
  process: `test_lspri_display_range_controls.py` (loads real fonts) + `test_lspri_log_panel.py` (builds a main window, which applies
  the app theme to the shared QApplication and leaves it there; about ten test files do this) + the image-panel file.
  Mechanism: `ImagePanel._fit_spin_widths` measures the spin box text once, in the constructor, with the default font ("Sans Serif");
  the app stylesheet then switches the spin box to Segoe UI. With no real font loaded both resolve to the same stand-in, so nothing
  shows; with fonts loaded they differ (55 px vs 42 px) and the test compared two fonts.
  It was a real (small) weakness in the app, not only in the tests: `_fit_spin_widths` ran only on value changes, never on a font
  change, so a system whose default font is not Segoe UI, or a theme switch, would keep a slightly wrong width until the next value
  change. Fix (LSPRi/eva, `panels/image/panel.py`): the panel installs its event filter on both spin boxes and re-fits on
  `QEvent.FontChange`. A first attempt cleared the app stylesheet in the test class; that was dropped because it hid the fix.
  New test `test_spin_box_widths_follow_a_font_change_after_the_panel_was_built` changes the font through the field's own
  stylesheet (wins over any app stylesheet, so independent of other tests) and fails without the fix (`90 != 188`).
  Qt applies a stylesheet's font at the widget's first polish, so a test must call `ensurePolished()` before reading `font()`.
  Related leak, not fixed: `build_main_window()` never undoes its app theme, so any later font-measuring test can see it.

## Running the suite fast

One process per file, 6 at a time, took 9.4 minutes for all 287 files (the single-process run takes over an hour).
Since 2026-10-30 the script is in the repo: `tools/run_tests_per_file.py` (`python -m unittest` per file, no pytest; `--pattern`, `--jobs`, `--timeout`, `--list`), and CI runs it (`.github/workflows/tests.yml`).
It cannot see order dependencies between files, by design: a test that needs a font or the app theme sets it up itself.

First full run with it (2026-10-30, dev laptop, 8 cores, 6 files at a time, Python 3.14.5): 4602 tests in 295 files, 38.2 min wall time (227 min of work, 40 of them two files that hit a 20-minute limit). The earlier "9.4 minutes" no longer holds: the GUI files are slow even on their own (`workflow_dock_resizable` 14 min, `visual_settings_restore` 10 min, `rewrite_preferences` 7.5 min for 26 tests) because windows pile up inside a file too.
Four files failed or timed out with six running at once and passed with fewer at a time: `lspri_analysis_warm_pool` (waits for 2 warm workers), `live_processing_worker` (gives a child process 5 s to start and answer; importing `lspr_app.gui.workers` alone takes about 2 s), `lspri_rewrite_histogram_panel` (reads a value a worker thread had not delivered yet), `lspri_rewrite_preferences` (reached the limit under load, 7.5 min alone). `live_processing_worker` even failed with three other files running. So the runner runs a file that failed or timed out once more on its own, and lists such a file under "passed alone" with its first failure instead of failing the run. Worth making those tests wait on an event instead of a fixed time.

## What CI itself still showed (read with `gh run view <id> --log-failed`, runs 96f88fe and 7dabc14)

CI (Python 3.12, windows-latest, headless since the pin, one process, 3610 tests in 32 to 49 min) failed three tests:

| Failure | Cause | Fix |
|---|---|---|
| `unit.test_lspri_scale_bar_overlay` ERROR (`unittest.loader._FailedTest`) | The file was pytest-style (`import pytest`, `@pytest.mark.parametrize`). CI installs no pytest, so the import failed; and `unittest` finds no test in a function-style file even when pytest is installed. The only pytest-style file in the suite. | Rewritten with `unittest.TestCase` and `subTest` (same 21 cases, same tolerance), and given the standard LSPRi import guard. |
| `overlay_layer_cache::test_cached_picture_differs_from_direct_painting_only_slightly` (max 252/255, 0.10 % of pixels) | Reproduced locally only with the real fonts loaded AND the app theme applied (what earlier test files leave in the single process, and what the real app always has). The strict limit had only ever been measured without them. Only the vertical group label text differs; circles, rings and ROI labels are identical (0/255). | The strict test now runs without group labels. New test for the group label text: through the cache it has about 23 % less ink (77 % of direct) and sits about 1 px to the left and 0.6 px higher; bounded at 60 % to 110 % ink and 1.5 px. |
| `RewriteImagePanelTest::test_spin_boxes_share_one_width...` (run 96f88fe only) | The font-change pollution described above. | Fixed in run 7dabc14, which no longer shows it. |

Not a bug, accepted by the maintainer as cosmetic (2026-10-18): with the overlay cache on, group label text looks lighter (about 23 % less ink)
and about a pixel off. Recorded in `overlay_cache.py`'s docstring and bounded by the new test. The way out, if it ever matters: draw the group
labels outside the cached layer (they then repaint every frame).

## CI check 2026-10-30 (runs 9644c04, 79aa22e; the 7db92bd run was still going)

The `tests` workflow is not hung, it is slow: 79aa22e ran all 4697 tests in 10,531 s (2 h 55 min); 9644c04 took 18,575 s (5 h 10 min), 56e2c4c 7,739 s. On 2026-10-18 it was 3610 tests in 32 to 49 min, so 30 % more tests cost 4 to 6 times the time.

- **Where the time goes** (gap between consecutive result lines, summed per file): 140 of 175 minutes are 8 GUI files (`workflow_dock_resizable` 47 min, `view_menu` 28, `workflow_panel_subsection_restore` 19, `rewrite_preferences` 16, `rewrite_visual_settings_restore` 15, `workflow_panel_stage_restore` 9, ...). Single tests: 1,848 s and 1,294 s. Locally these take seconds. Inside `view_menu` the tests took 99 s, 99 s, 192 s, then 1,294 s: each window build is slower than the one before.
- **Cause (measured locally, mechanism not pinned down):** a test's `tearDown` calls `window.deleteLater()`, but no event loop runs afterwards, so the window is not destroyed. 15 `build_main_window()` calls in one process (probe script, scratchpad): build time 3.4, 7.6, 14, 24, 31, 57, 49, 37 s; live top-level widgets +121 per window; `undo_manager.changed` gains one listener per window. Suspect: every build re-polishes (theme / stylesheet) all windows still alive, so the cost grows with the number of earlier windows. A run of one process per file never gets there.
- **Leaked undo listener (a real small bug, tests only see it):** `_wire_edit_menu` connects a closure `refresh` to the global `undo_manager.changed` and never disconnects it. Once the window's C++ object is gone, every later `changed` raises `RuntimeError: wrapped C/C++ object of type QAction has been deleted`: 57,535 tracebacks in the 79aa22e log (a quarter of its 236k lines).
- **The three tests that failed in all three runs:**
  - `RewriteImagePanelTest.test_the_top_ribbon_takes_all_the_room_the_bar_has_and_scrolls_only_when_the_panel_is_really_too_narrow`: `is_overflowing()` False at 700 px. The font / theme state left by earlier files (the leak described above, line 49).
  - `SessionAutosaveDatasetWiringTest.test_an_unreadable_session_disables_saving_rather_than_overwriting_it`: `assertLogs` saw no ERROR from `session_load_task`. Cause not found; nothing in the repo disables logging.
  - `BatchEqualsPerCubeTests.test_every_polynomial_setting_gives_the_reference_numbers` (centroid, order 6, window 560 nm): batch and per-cube differ by 1.5e-6 nm (2.6e-9 relative) against `TOLERANCE_NM = 1e-6`. Passed in run 9644c04, so it depends on the CPU the runner gets.
- The six Workflow subsection-restore errors of 56e2c4c / 9644c04 were stale tests, repaired in 79aa22e.

### Result of the per-file CI (run 41048305012, commit 5aa7d7a, 2026-10-30) and what could make it faster

- **The run took 19.6 min** (17 s checkout, 122 s dependencies, 17.5 min tests; was 3 to 5 hours). 4634 tests in 295 files, 3 files at a time on the 4-core runner, 51.9 min of work: the wall time is simply work / 3, nothing waits. Slowest files: `rewrite_preferences` 363 s, `workflow_dock_resizable` 181 s, `rewrite_image_panel` 144 s, `rewrite_visual_settings_restore` 140 s, `roi_features_tool` 109 s.
- **One file red: `test_lspri_rewrite_session_autosave.py`, alone, in 3.9 s.** `test_an_unreadable_session_disables_saving_rather_than_overwriting_it`: CI logs "The session ... was not loaded (cancelled); autosave is off for it", so the load was cancelled, not failed, and there is no ERROR from `session_load_task` for `assertLogs` to catch. It fails the same way every time on CI, never locally. The image-panel ribbon test and the trace_fit test passed in this run (the first was the shared-process font/theme state, the second depends on the runner's CPU).
- **Same suite on the dev laptop** (8 cores, 6 files at a time, Python 3.14): 227 min of work against 52 on CI. Part of that is the machine being oversubscribed (every test process starts its own workers and BLAS threads on all cores): `workflow_dock_resizable` took 818 s in that run and 214 s alone.
- **Windows destroyed after each test** (`sendPostedEvents(None, DeferredDelete)`, measured with a wrapper, nothing changed in the repo), file alone on the laptop: `workflow_dock_resizable` 214 -> 28 s; `view_menu` 31 -> 29 s and `rewrite_preferences` 362 -> 383 s (no change; its 14 s per test come from something else). A window build stays flat at ~2 s when windows are destroyed (3 s growing to 57 s when they are not). Even destroyed windows leave one undo listener and ~46 parentless widgets each.
- **Fixed waits in the tests are not the cause**: 5 min of literal `_pump` / `sleep` waits written in all test files against 180 min measured.
- **Ideas, in order of gain**: split the files over 3 to 4 runners with a `--shard i/n` option and a matrix (tests ~17 -> ~6 min, job ~9 min); find why `rewrite_preferences` needs 14 s per test (12 % of all work on its own); destroy windows in tests and disconnect the undo listener; `cache: pip` in the workflow (install 122 s -> maybe 60 s). `run_tests_per_file.py --pin` (each parallel file gets its own cores) is built, off by default, not yet measured on a full local run.

### The autosave failure was a race in the test (found and fixed 2026-10-30)

`test_an_unreadable_session_disables_saving_rather_than_overwriting_it` loads a dataset, clears it, writes a corrupt `session.json`, loads again and expects an ERROR from the failed read. Clearing the dataset hands the session's pending state to the autosave's background thread, which writes it into the old session folder. On a slower machine that write landed after the test's corrupt file and replaced it with a valid session, so the second load succeeded and nothing was logged (the later "was not loaded (cancelled)" line in the CI log belonged to the next test, not to this one). Reproduced on the laptop by slowing `SessionWriter.write` by 0.3 s (probe in the session scratchpad, nothing changed in the repo); fixed by `self.autosave.wait_until_written()` after `clear_dataset()`, which the other tests of that file already do; passes with 0, 0.3 and 1.0 s of delay. Not an app bug. Pitfall noted on the way: `session_path(root)` is `root/session/session.json`, so the writer's root is two levels above the file.

### First sharded CI run (run 41057214941, commit dbb61d2, 3 shards, 2026-10-30)

- **The whole run took 10.3 min** (was 19.6 min on one runner, 3 to 5 hours before the per-file runner). Per shard: setup about 2.1 min, tests 3.6 / 7.7 / 5.2 min wall (10.9 / 19.4 / 15.1 min of work). The autosave fix held on CI (that file is in a shard that passed).
- **One red file, `test_lspri_analysis_trace_fit.py`** (shard 3): the batch and per-cube metric differ by 1.545e-6 nm, the limit was 1e-6 nm. Same numbers in every CI run, so it is the runner's CPU, not chance. Measured on the laptop for all 50 settings of the test: the worst is 9.85e-7 nm (centroid, order 6, window from 560 nm), i.e. the old limit never had headroom; the rounding grows about tenfold per polynomial order (order 1: 4e-12, 3: 9e-10, 4: 1e-8, 6: 1e-6 nm). A real difference on the same data: window moved by one point 6.7 nm, order 5 instead of 6 0.18 nm, 'maximum' instead of 'centroid' 26 nm. The limit is now 1e-5 nm (6x the worst rounding seen, 18000x below the smallest real difference). No number the app computes changed.
- **Why shard 2 is the slow one**: it holds `rewrite_preferences` (335 s, 5.6 min alone) and that file starts late, because the split and the start order go by file size, and this file is not among the biggest. With the runner starting the slowest file first, shard 2's tests would take about 6.5 min instead of 7.7. Remaining ideas: make `rewrite_preferences` itself fast (26 tests at 13 s each; not caused by windows piling up, destroying them changed nothing), or give the runner measured times (a small committed table) instead of file sizes; both are worth about 2 to 3 min of the 10.

### The test windows are destroyed after each test (2026-10-30)

- **Cause of the slowness inside a file**: `build_main_window()` windows were closed or `deleteLater()`d but never destroyed (no event loop runs in the tests), so each build in a process had to cope with all earlier windows. Measured with 10 windows in one process: 3 s growing to 57 s per build; with the windows really destroyed it stays at about 2 s.
- **Fix**: `tests/_qt_cleanup.py`, installed by `tests/__init__.py`, wraps `unittest.TestCase.run`: after each test (tearDown and cleanups included) it closes and deletes the `QMainWindow`s the test created and delivers the deletion. Windows that existed before the test (`setUpClass`, module level) stay. It does nothing for a test file that did not import `PyQt6.QtWidgets` or runs on a plain `QCoreApplication`. `LSPR_TEST_KEEP_WINDOWS=1` switches it off. Tests: `tests/integration/test_qt_cleanup.py`.
- **Alone on the laptop**: `rewrite_preferences` 362 -> 55 s, `workflow_dock_resizable` 214 -> 34 s, `view_menu` 31 -> 15 s, `shortcuts_dialog` about 50 -> 15 s. `rewrite_image_panel` (144 s) is unchanged: it builds a bare `ImagePanel`, not a main window.
- **Why only main windows**: the first version deleted every new top-level widget. That aborted `test_lspri_rewrite_image_panel` and `test_lspri_rewrite_preferences` ("QObject: shared QObject was deleted directly", then `Fatal Python error: Aborted`) when a bare `ImagePanel` / dialog was deleted. Not chased further; none of them is a measured cost.
- **The full local run found one bug of the hook**: `test_live_visual_refresh_scheduling` runs on a `QCoreApplication`, which has no `topLevelWidgets()` (fixed with an `isinstance` check and a test). Everything else passed. Full run on the laptop (6 files at a time, 297 files): 81.5 min of work (was 227), of which 30 min were `test_live_processing_worker` hanging until the file limit under load (it passes in 17 s alone; the retry saw that). The per-file limit is now 10 min instead of 30.
- Still open: the live worker test hangs under load (a child process and a queue; passes on CI); the undo listener that every `build_main_window()` leaves on the global undo manager (one closure per window, raises `RuntimeError: wrapped C/C++ object ... QAction has been deleted` for every later change; in `app_rewrite._wire_edit_menu`, eva repo).

### Where the CI stands after the window clean-up (run 41065830414, commit cf5df39, 2026-10-30)

Green, **6.7 min for the whole run** (3 to 5 hours at the start of the day, 19.6 min with one runner, 10.2 min with 3 shards). Per shard: setup 1.8 min, tests 4.1 / 4.3 / 3.5 min wall (12.1 / 13.0 / 10.6 min of work, 35.7 min in all; was 51.9 before the clean-up). The shards are even now because no single file dominates: the slowest is `rewrite_image_panel` (130 s, a bare `ImagePanel` the clean-up does not destroy). Setup is now more than a quarter of the job; the install is 18 editable packages and about 70 unpacked wheels, so a pip cache would save only the 19 s of downloads. Not worth more work unless the suite grows.
