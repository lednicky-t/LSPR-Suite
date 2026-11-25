---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/sensorgram/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/spectra/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/dataset_manager/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/step_picker.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/plan/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/selection/**"
---

# LSPRi: Spectra, Sensorgram, plots, experimental plan

Moved verbatim from `apps/LSPRi/eva/CLAUDE.md` (2026-11-25). Docs: `docs/experimental_plan_bar_design_2026-10-26.md`, `docs/step_statistics_backbone_2026-10-26.md`, `docs/signal_map_2026-11-10.md`, `docs/refractive_indices_2026-11-12.md`, `docs/bulk_sensitivity_2026-11-12.md`, `docs/lbl_series_2026-11-22.md`, `docs/spectral_correction_2026-11-20.md` (no temperature yet, maintainer: measure first), `docs/sensorgram_view_tab_2026-11-13.md`, `docs/plot_windows_ribbon_2026-11-22.md`, `docs/legend_order_2026-11-22.md`, `docs/draw_order_2026-11-22.md`.

## Spectra and Sensorgram

- **Never run a fit loop, or any long Python-heavy numpy work, in a thread next to the GUI: use a process** (it starved the GUI of the interpreter lock and froze the window: `docs/sensorgram_fit_freeze_2026-10-24.md`). The helper process falls back to the thread with a WARNING.
- Template shift: a ROI's cubes must never be split (its reference cubes are part of its trace); `fit_spectrum("template")` and a template-less `fit_and_metric` raise, so the method can never fall through to the polynomial; no baseline removal, by decision.
- Smoothing runs inside each plan step (`StatisticsSettings.smooth_within_steps`, default on, saved with the session), so a window never mixes two solutions.
- Step stats show mean ± SD of every line **as drawn**. Narrow steps get shorter text, never a smaller font: the size is the one in the size box for every selection.
- **Spectral correction rides on the display's scan object** (2026-11-20): `ScanResultsModule.stored()` returns a copy with `StoredScan.spectral` set (arrays shared), the same object until the table, the mode or the paired scan changes, so identity-keyed caches miss by themselves. `wavelength_nm` stays the nominal axis (hashes, plan matching); **a spectrum builder reads `stored.abscissa_nm()` and `passband_moments()`, never `wavelength_nm`**; a new consumer does the same, and a cache key that does not go through `trace_key` adds `stored.spectral_key()`. Nothing in `MetricSettings`. A mode with no usable table is *said* (`SpectralCorrectionModule.problem()`), never silently none. The table is read and written off the GUI thread. The correction never changes the scan, the images or any stored number.

## Experimental plan

- The dataset's measured steps (`ImagingAcquisitionMetadata.plan`) are never written; the user's edits are an overlay in the session (`plan/`, session schema 1.5 / 1.6, block `"plan"`). An edit applies only to the step it was made on (fingerprint: number + measured start + measured note), else it is reported and left out.
- **Refractive indices**: one RI per *solution*, a step's solution is the first name of its note (`refractive_index.solution_of`). The session keeps a copy of the whole imported file with its SHA-256 (session schema 1.7, block `"refractive_index"`); a changed RIs.txt is reported, **never taken in silently**; **the imported copy is never changed: the user's edits (cells, added / removed rows, rows given to steps by hand) are a separate working table `RiTable.rows` that every calculation reads (2026-11-13, session schema 1.9, `docs/refractive_indices_2026-11-12.md` "Editing"); an edited value is flagged `SolutionRi.edited` and must be said wherever a number built from it is shown or exported (plan bar hover, S_bulk column tool tip, the export record's `ri_edits`); a new import replaces the edits only after the menu asked**; the refractometer wavelength is stamped at import from Preferences (default 589.3 nm: the lab's Rudolph Research J357 LED; 0 = "not given", never guessed for another instrument). `dataset_cleared` must reach `SessionCoordinator.unbind` before any module resets itself (`_build_modules` connects it first), or the cleared state is saved over the session.
- **A "range" is a step of the experimental plan; there is no separate ranges concept** (maintainer, 2026-10-29). A function that needs a stretch of the experiment takes a baseline step and a signal step from `ExperimentPlanModule` (`panels/step_picker.py` is the shared drop-down).
- pyqtgraph gives a right-click to the ViewBox first, so the plan bar asks first through `_MiddlePanViewBox.claim_right_click`; the bar's `shape()` needs the winding fill rule (an odd-even path makes the overlap at a shared boundary a hole).

## Plots

- **The points / lines / legend menus are one definition, `PlotLookControls` (`panels/sensorgram/plot_look.py`)**, used by every plot; the values are separate per plot (own instance, keys, defaults). Add an option there once; never copy those menus into a plot.
- **Re-ordering never draws the lines again**: an option that only moves rows (z, legend) is added to `SensorgramViewSettings.differs_only_in_order`; every other option draws again. The manual line order is the session's (`PlotLineOrderModule`).
- Sensorgram View-tab options are display only (nothing is recomputed) and remembered; they are menus of one kind (`panels/sensorgram/group_menu.py`).
- Signal-map colours are a display override (`RoiOverlay.draw(color_override=)`), no calculation of their own.
