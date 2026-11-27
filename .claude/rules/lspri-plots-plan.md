---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/sensorgram/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/spectra/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/dataset_manager/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/step_picker.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/plan/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/analysis/{statistics,step_*,trace_*,*_fit,fit_window,template_shift,spectral_*,passband_*,baseline_drift,bulk_sensitivity,lbl_series,signal_map,metric_range,order_quality,spectrum_errors}.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/selection/{experiment_plan_module,spectral_correction_module,step_table_module,plot_line_order_module,signal_map_module}.py"
---

# LSPRi: Spectra, Sensorgram, plots, experimental plan

Docs: `experimental_plan_bar_design_2026-10-26`, `step_statistics_backbone_2026-10-26`, `signal_map_2026-11-10`, `refractive_indices_2026-11-12`, `bulk_sensitivity_2026-11-12`, `lbl_series_2026-11-22`, `spectral_correction_2026-11-20` (no temperature yet: measure first), `sensorgram_view_tab_2026-11-13`, `plot_windows_ribbon_2026-11-22`, `legend_order_2026-11-22`, `draw_order_2026-11-22`, `plot_axes_2026-11-27` (all in `docs/`).

## Spectra and Sensorgram

- **A fit loop or long Python-heavy numpy work runs in a process, never a thread next to the GUI** (GIL starvation froze the window: `docs/sensorgram_fit_freeze_2026-10-24.md`); the helper falls back to a thread with a WARNING.
- Template shift: never split a ROI's cubes; `fit_spectrum("template")` and a template-less `fit_and_metric` raise (never fall through to the polynomial); no baseline removal.
- Smoothing stays inside each plan step (`StatisticsSettings.smooth_within_steps`, default on, saved), so a window never mixes two solutions.
- Step stats show mean ± SD of every line **as drawn**; narrow steps get shorter text, never a smaller font.
- **Spectral correction rides on the scan object**: `ScanResultsModule.stored()` returns a copy with `StoredScan.spectral` set (arrays shared; same object until table, mode or pairing changes). `wavelength_nm` stays nominal (hashes, plan matching); **spectrum builders read `stored.abscissa_nm()` and `passband_moments()`, never `wavelength_nm`**; a cache key not going through `trace_key` adds `stored.spectral_key()`. Nothing in `MetricSettings`. A mode with no usable table is *said* (`SpectralCorrectionModule.problem()`). Table I/O off the GUI thread. The correction never changes the scan, images or stored numbers.

## Experimental plan

- Measured steps (`ImagingAcquisitionMetadata.plan`) are never written; user edits are a session overlay (`plan/`, block `"plan"`), applied only to the step they were made on (fingerprint: number + measured start + note), else reported and left out.
- **A "range" is a plan step**; no separate ranges concept. Functions needing a stretch take a baseline step and a signal step from `ExperimentPlanModule` (`panels/step_picker.py`).
- **Refractive indices**: one RI per *solution* (first name of the step note, `refractive_index.solution_of`). The session keeps the imported file + SHA-256 (block `"refractive_index"`); a changed RIs.txt is reported, **never taken in silently**. **The imported copy is never changed**: edits live in the working table `RiTable.rows` that all calculations read; an edited value is flagged `SolutionRi.edited` and must be said wherever a derived number is shown or exported (plan bar hover, S_bulk tool tip, export `ri_edits`); a new import replaces edits only after asking. Refractometer wl stamped at import from Preferences (default 589.3 nm; 0 = "not given", never guessed).
- `dataset_cleared` must reach `SessionCoordinator.unbind` before any module resets (`_build_modules` connects it first), or the cleared state is saved over the session.
- pyqtgraph gives right-clicks to the ViewBox first: the plan bar claims them via `_MiddlePanViewBox.claim_right_click`; its `shape()` needs the winding fill rule.

## Plots

- **Points / lines / legend menus are one definition, `PlotLookControls` (`panels/sensorgram/plot_look.py`)**, separate values per plot. Add options there; never copy the menus.
- **Re-ordering never redraws lines**: order-only options go in `SensorgramViewSettings.differs_only_in_order`. Manual line order is the session's (`PlotLineOrderModule`).
- **Axis options (tick spacing, display range, grid, x-axis breaks) are session values** (`PlotAxesModule`, per plot and per quantity the axis shows; `domain/plot_axes.py`, `domain/axis_breaks.py`). **With breaks every item of the Sensorgram plot is placed through `BreakMap.forward`, and everything the plot reports goes back through `inverse`**: a new item or signal that carries an x goes through them too (`plot.py` `_drawn_x` / `_data_x`; the plan bar and the step table take the map). A break never changes a stored number. **The x zero (`AxisOptions.zero`) changes only the printed numbers** (`BrokenAxisItem.set_zero`); a typed x range is in printed numbers, breaks and everything the plot reports stay in data x. **Guides** are the y side's `AxisOptions.guides` (reference lines behind the data, never in the autoscale; layer "Guides"); a guide-only change must not re-range or redraw (`PlotAxes.only_guides_differ`). Guides are changed only through `GuideControls` (right-click gestures and drags on the plot only report).
- Sensorgram View-tab options are display only and remembered (`panels/sensorgram/group_menu.py`). Signal-map colours are a display override (`RoiOverlay.draw(color_override=)`).
