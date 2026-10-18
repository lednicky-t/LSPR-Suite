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
The script used lived in the session scratchpad (about 30 lines: `subprocess.run([python, "-m", "pytest", file, "-q"])` in a thread pool).
It does not replace the CI run: it cannot see order dependencies between files.

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
