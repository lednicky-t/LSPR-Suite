# Task: Rotation-fill pixels are NaN. Remove the fill option, define NaN handling in every consumer

**Branch:** `rewrite` · **Package:** `lspr_imaging_app`
**Background:** `rotation_fill_regions_audit_2026-10-03.md` (read it first; finding IDs F1–F8 refer to it).
**Rules:** follow `.claude/rules/numerics.md` for every change that alters computed values.

---

## 1. Decision

Pixels created by rotation have no measurement behind them, so they have **no value**. The user gets no say in what they are.

1. Rotation writes `NaN` into every created pixel (`mode="constant", cval=np.nan`, on a float image). There is no dark mode and no edge-stretch.
2. The whole "rotation fill" option is removed: setting, UI controls and icons in the Image panel, change reason, code branches and tests that exist only for it (§3).
3. **Single source of truth for validity:** `valid = np.isfinite(processed_image)`. Consumers derive validity from the image itself instead of recomputing a geometric mask. This automatically follows the chromatic warp at other wavelengths and any other step that moves pixels.
4. Each consumer handles `NaN` as specified in §4. Nothing may turn `NaN` back into a number (no `nan_to_num`, no fill with 0, no interpolation into the gap).

### Invariants

- Every rotation-created pixel is `NaN`; every other pixel is finite (unless raw data was non-finite).
- `NaN` is never converted to a number anywhere in the pipeline.
- **No `NaN` reaches a reduction.** Analysis removes invalid pixels explicitly with `valid`. Reductions then assert finite input and raise if not. They do **not** silently use `nanmean` and the like: a `NaN` arriving there means a step was missed, and that must be an error.

## 2. Ground rules

- Never modify raw/source data.
- No sentinel values (0 or anything else) for missing data.
- Work in phase order. Each phase is its own commit with passing tests.
- **STOP points** are marked ⛔. At each one, stop, report, and wait for the maintainer.

---

## 3. What gets removed

Search the rewrite for `rotation_fill`, `fill_dark`, `rotation_fill_mask`, `rotation_fill_pixel_mask`, `mode="nearest"` in geometry code, and any icon or action names related to the fill toggle. Expected removals:

- `GeometrySettings.rotation_fill_dark` and its handling in `GeometryModule`;
- the `rotation_fill` reason of `GeometryComputationalChange`;
- the fill checkbox/toggle and its icons/actions in the Image panel and any toolbar, plus icon assets used only by it;
- the edge-stretch branch in `apply_spatial_preprocessing` and the OpenCV edge-stretch path in `apply_spatial_preprocessing_export`;
- the `rotation_fill` special case in `RoiGeometrySync` (`roi_geometry_sync.py:54`);
- `rotation_fill_pixel_mask` and the `rotation_fill_mask` parameters (background estimate, `roi/detection.py`), **once** every consumer uses `isfinite` (Phase 3). If Phase 1 shows a consumer that genuinely needs the geometric mask (for example, before the image exists), keep the function and say why;
- tests that exist only for the checkbox or the "no remap on fill change" rule;
- mentions in docs (`docs/image_tools_coordinate_spaces.md`, build log, help text).

**Migration:** saved sessions or settings files that contain `rotation_fill_dark` must still load. Ignore the key with a one-line log message. The fingerprint changes, so stored analysis cells recompute once; note this in the build log.

---

## 4. NaN handling per consumer (the specification)

| Consumer | Required behaviour |
|---|---|
| **Image preview** (`panels/image/render.py`) | `NaN` is drawn in a "no data" style that cannot be confused with any data value in any colormap: transparent over a neutral checker or a hatched pattern, set via the colormap's bad-value colour or an overlay. Never black, never the colormap minimum. Contrast autoscale (min/max, percentiles) uses finite pixels only. A thin outline at the edge of valid data is optional. |
| **Pixel readout / cursor info / tooltips** | Show "no data" for a `NaN` pixel, not `nan`, not 0. |
| **Histogram** (`panels/histogram/panel.py`) | Bins built from finite pixels only. Count, percent and normalised Y-scales use the **finite** pixel count as the denominator. The panel shows how many pixels were excluded (e.g. "12 345 px no data, 10.4%"). Same rules for ROI-restricted histograms. Automatic bin range from finite pixels. |
| **Background flattening** (`image_tools/background/`) | The estimate uses finite pixels only (plus the existing optional dilation of the invalid region). Any smoothing or filtering must be NaN-aware (normalised convolution or equivalent), so `NaN` does not spread into valid pixels and invalid pixels do not pull the estimate. After subtraction, pixels that were `NaN` stay `NaN`; the background model must not fill them in. |
| **Ignore-mask steps** in `apply_preprocessing` | Currently write 0 (same sentinel problem). Report in Phase 1; do not change in this task unless approved at STOP 1. |
| **Chromatic warp** (other wavelengths) | Bilinear interpolation will spread `NaN` to pixels next to the data edge. This is accepted: those pixels mix in a missing value and are correctly invalid. `isfinite` picks them up automatically. |
| **ROI detection / landmark auto-detection** | Segmentation and thresholds use finite pixels only. Invalid pixels count as "not a feature". Detected ROIs or landmarks that touch invalid pixels are rejected (optionally with a margin). Test: the data edge is never detected as a feature. |
| **Analysis** (`analysis/tasks.py`, `analysis/reduction.py`) | `sample_mask &= valid`, `reference_mask &= valid`, before the ignore-mask removal. Reductions assert finite input. Coverage diagnostics and thresholds per §5 Phase 4. A sample or reference that is `NaN` (insufficient coverage) makes absorbance `NaN` with the same reason flag. |
| **Result tables, plots, time series** | `NaN` results are shown as gaps or empty cells with their reason flag. Never plotted as 0, never interpolated across. Any aggregate over ROIs or frames reports how many values it used. |
| **CSV / table export of results** | One consistent representation of missing values (empty field or `NaN`, choose one and document it) plus a reason-flag column. |
| **Image export (OME-Zarr)** | Proposal only, Phase 7. |

---

## 5. Phases

### Phase 0: Tests first

1. Pin current behaviour: at 3°, 15° and 33°, record which pixels are fill (zero pixels in dark mode on a synthetic image without real zeros). After Phase 2 the same pixels must be exactly the `NaN` pixels.
2. Boundary-straddling ROI test: flat image of 1000, 20° rotation, sample circle and reference ring partly in the fill. Assert sample and reference means of 1000. Mark `xfail(strict=True)` referencing F1 until Phase 4.
3. Background flattening test: the estimate is unaffected by what lies in the invalid region.

### Phase 1: Inventory and NaN propagation audit (no code changes)

1. **Removal inventory:** list every item from §3 actually found, with file and line, including UI icons and assets.
2. **NaN audit:** for every consumer in §4, describe its current behaviour with `NaN` input and the change needed. Also check:
   - does `cv2.warpAffine` produce `NaN` correctly with a `NaN` border value on float input? Verify against SciPy; if not, use SciPy in the export path;
   - raw dtype and the float type used today (`float32` vs `float64`); memory impact of converting before rotation;
   - any place that currently calls `nan_to_num`, fills with 0, or uses `np.mean` on whole images;
   - whether any consumer genuinely needs the geometric mask instead of `isfinite`;
   - where the ignore-mask 0 can enter a statistic today.

⛔ **STOP 1:** Present both as a short markdown report in `docs/` and wait for approval.

### Phase 2: NaN fill and removal of the option

1. Convert to float before rotation; rotate with `mode="constant", cval=np.nan` in both GUI and export paths.
2. Remove everything in the approved removal inventory (§3), including UI controls, icons and assets.
3. Implement the settings migration (§3).
4. Tests: every fill pixel is `NaN`, every other pixel finite; `NaN` pixels equal the Phase 0 recorded fill pixels; old settings files load.

### Phase 3: Consumers (display, histogram, background, detection)

Implement §4 for: image preview, pixel readout, histogram, background flattening, ROI and landmark detection. Then remove `rotation_fill_pixel_mask` and the `rotation_fill_mask` parameters (unless STOP 1 decided otherwise).

Tests:
- autoscale and histogram give identical results with and without a large `NaN` region added around the same data;
- histogram denominators use the finite count; the excluded count is displayed;
- background estimate and detection ignore the invalid region; the data edge is never detected.

### Phase 4: Analysis (F1)

In `analysis/tasks.py::compute_cell`:

1. `sample_mask &= valid` and `reference_mask &= valid`.
2. Assert that pixels passed to the reduction are all finite; raise a clear error if not.
3. For every ROI and wavelength, compute and **store in the result**:
   - `n_sample_nominal`, `n_sample_valid`, `sample_valid_fraction`;
   - `n_reference_nominal`, `n_reference_valid`, `reference_valid_fraction`;
   - `reference_min_sector_fraction`: split the reference ring into 4 angular sectors and record the lowest valid fraction (detects a one-sided ring, which biases the background under a gradient).
4. Settings `min_sample_valid_fraction`, `min_reference_valid_fraction` (optionally `min_reference_sector_fraction`). Below a threshold: `NaN` with reason flag `"insufficient_coverage"`. These settings enter the `SettingsSnapshot` fingerprint.
5. Uncertainty or standard error uses `n_valid`, not the nominal aperture size.
6. Reductions handle reduced pixel sets, including zero valid pixels; plane fit returns `NaN` with a flag when there are too few pixels to fit.
7. Result tables, plots and CSV export follow §4.
8. Remove the `xfail` from the Phase 0 boundary-ROI test. It must pass.

⛔ **STOP 2:** Do not pick threshold values yourself. Use clearly marked provisional defaults and ask for final values after Phase 5 shows the real coverage fractions.

### Phase 5: Before/after comparison on real data (numerics rule)

1. Run on real measurement data with a non-zero rotation: current `HEAD` in dark mode **and** edge-stretch, and after Phase 4.
2. Report per dataset:
   - how many ROIs touch invalid pixels and their coverage fractions (sample, reference, min sector);
   - how many become `NaN` at the provisional thresholds;
   - the change in sample, reference and absorbance for affected ROIs, against both old modes;
   - ROIs not touching invalid pixels: identical within a stated tolerance (quantify any difference from the float conversion).
3. Report in `docs/`, entry in the rewrite build log.

⛔ **STOP 3:** Present the report; wait for approval and the threshold values.

### Phase 6: ROI warnings (F5)

After a rotation or crop remap, flag ROIs that are off-canvas or below the coverage thresholds. Show a non-blocking warning listing them. Do not delete or move ROIs automatically.

### Phase 7: Image export (proposal only)

Short design proposal in `docs/` (**do not implement**): float with `NaN` vs. original dtype plus a mask array or label image (trade-offs from the Phase 1 findings); transform parameters and app commit in metadata; how a reader of the file tells missing data from data.

⛔ **STOP 4:** Wait for approval.

---

## 6. Out of scope (note only)

- Fixing the ignore-mask 0 sentinel (unless approved at STOP 1).
- Rasterizing ROIs in raw space instead of in the rotated image. Note anything relevant to its feasibility in the Phase 5 report.
- The stable app (`gui/...`, `processing/...`).

## 7. Acceptance criteria

- [ ] Rotation-created pixels are `NaN` in GUI and export paths; no other value is ever assigned to them.
- [ ] The fill option is fully removed: setting, change reason, UI controls, icons, assets, code branches, obsolete tests, docs. Old settings files still load.
- [ ] Validity comes from `isfinite`; the geometric mask is removed or its remaining use is justified.
- [ ] Every consumer in §4 behaves as specified and has a test.
- [ ] No `NaN` reaches a reduction; reductions assert finite input.
- [ ] Analysis stores coverage diagnostics and returns flagged `NaN` below thresholds; thresholds are in the fingerprint.
- [ ] Phase 1 report, Phase 5 report and Phase 7 proposal are in `docs/` and approved.
- [ ] Full test suite passes; build log updated.
