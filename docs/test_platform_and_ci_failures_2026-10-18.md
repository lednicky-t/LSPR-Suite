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
- `test_lspri_rewrite_image_panel.py::...::test_spin_boxes_share_one_width_fitted_to_their_text` failed once in a headless full
  `unittest discover` run (about 1 hour in) and passes alone, per file, and after the three font-loading files before it. Not reproduced.
  If CI shows it, suspect state leaked from an earlier test file in the same process (application font or stylesheet).

## Running the suite fast

One process per file, 6 at a time, took 9.4 minutes for all 287 files (the single-process run takes over an hour).
The script used lived in the session scratchpad (about 30 lines: `subprocess.run([python, "-m", "pytest", file, "-q"])` in a thread pool).
It does not replace the CI run: it cannot see order dependencies between files.
