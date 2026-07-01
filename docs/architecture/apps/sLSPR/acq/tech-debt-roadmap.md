# sLSPR Acquisition App — Technical Debt & Improvement Roadmap

This document tracks known technical debt, robustness improvements, and polish tasks
identified during a systematic codebase audit (2026-06).  Items are grouped by
priority.  Completed items are marked ✅.

---

## Priority 1 — Real risk / data safety

### 1.1 HDF5 flush interval default too high ✅
**File:** `gui/main_window.py:603`, `gui/main_window_preferences.py`  
Default was 5 s — means up to 5 s of spectra are lost on a hard crash.  Changed
default to **1 s**.  The value is already user-configurable in Preferences →
Acquisition & storage → "HDF5 flush interval" (range 0.25 – 60 s).  The setter
`_set_measurement_hdf5_flush_interval_s` hot-patches a running writer immediately.

### 1.2 HDF5 flush on acquisition stop  
**File:** `gui/acquisition_controller.py`, `gui/main_window_lifecycle.py:434`  
The writer is closed cleanly on stop/close (which forces a flush), so data loss
only affects crashes.  The remaining gap is: a crash between two timer-triggered
flushes.  Mitigations already in place: the writer closes on `_stop_measurement_run`
and on `close_event_for`.  No further code change required unless we add a
"flush on every N spectra" option.

### 1.3 Background-thread file probe in import dialog ✅
**File:** `gui/main_window_import_dialog.py`  
`probe_measurement_hdf5` used to run synchronously on the main thread.  Opening
a file on a slow or network-mounted drive would freeze the entire UI.  Fixed by
running the probe in a `QRunnable` via `QThreadPool.globalInstance()`.  The dialog
is shown once the probe result arrives on the main thread via a queued signal.

### 1.4 QRunnable tasks emit signals into a potentially-destroyed window ✅
**File:** `gui/acquisition_controller.py`, `gui/main_window.py`  
If the main window is closed while a background task is running, the signal
`finished`/`failed` could arrive after the Qt C++ peer is deleted.  Added
`_closing` early-exit guards in `_handle_measurement_file_compression_finished`,
`_handle_measurement_file_compression_failed`, and
`_handle_sensorgram_metric_archive_reload_failed`.
(`_handle_sensorgram_metric_archive_reload_result` already had this guard.)

---

## Priority 2 — Correctness / robustness

### 2.1 ECW signal connections never disconnected ✅
**File:** `gui/main_window_lifecycle.py:391`  
Six signals (`availability_changed`, `valve_availability_changed`,
`mswitch_availability_changed`, `recording_control_requested`,
`experimental_control_state_recorded`, `theme_changed`) are now explicitly
disconnected in `close_event_for` before `ecw.close()`.  The `try/except
RuntimeError` guard handles the edge case where a signal was never connected.

### 2.2 ECW theme-change set_theme() did not propagate to plan model ✅  
**File:** `gui/experiment_control_window.py:set_theme()`  
`set_theme()` re-applied the QSS stylesheet but did not call
`_plan_model.set_theme_palette()`.  The model kept returning dark-mode `QBrush`
values from `BackgroundRole`/`ForegroundRole`, overriding the QSS light colors.  
Fixed by adding `self._plan_model.set_theme_palette(self._theme_palette())` inside
`set_theme()`.

### 2.3 Delegate editor stylesheets used hardcoded dark-mode fallbacks ✅  
**File:** `gui/flow_plan_model.py`  
`_style_combo_editor`, `_style_spin_editor`, `ExperimentPlanCommentDelegate`, and
the valve/color-popup `paint()` methods all had `"#151a20"` / `"#e6ebf1"` as
fallback colors.  These are dark-mode values — they made editors appear dark in
light mode.  Fixed by reading `self._window._theme_palette()` at editor-creation
time and using light-mode defaults (`"#f4f6f8"` / `"#1d2733"`).

### 2.4 Silent failures in HDF5 probe  ✅
**File:** `gui/main_window_import_dialog.py:106,155`  
Exceptions inside the processing-settings and experiment-plan detection blocks
were swallowed with a bare `except: pass`.  Users saw "Not found in this file"
with no indication of the real error.  Fixed by logging at DEBUG level.

### 2.5 File-dialog start directory never created  ✅
**File:** `gui/main_window_import_dialog.py:271`  
`DEFAULT_CONFIG_PATH.parent` was passed as `start_dir` without checking whether
the directory exists.  On first launch the config directory does not exist yet,
so the file picker silently falls back to the OS default directory.  Fixed by
calling `start_dir.mkdir(parents=True, exist_ok=True)` before opening the picker.

---

## Priority 3 — Performance

### 3.1 model dataChanged for full table on theme-palette change  
**File:** `gui/flow_plan_model.py:set_theme_palette()`  
`set_theme_palette()` emits `dataChanged` for the entire table rectangle.  This
is correct (all cells need to repaint their background/foreground), and the roles
are scoped to `BackgroundRole` + `ForegroundRole` only — Qt avoids a full text
re-layout.  The other setters (`set_tube_mm_by_channel`, `set_switch_solution_labels`,
etc.) already emit scoped ranges for the relevant columns.  No change needed.

### 3.2 Plot view cache grows without bound ✅
**File:** `gui/plot_view_cache.py`  
`PlotViewCache` accumulates full metric series for the lifetime of the session.
Long runs (6+ hours at 4 Hz) could exhaust memory.  Added
`max_live_cache_l0_blocks=36_000` to `PlotViewCache.__init__` and a
`_trim_live_cache_l0_if_needed` function that caps level-0 block count.  Only
level 0 (finest granularity, ~2.5 h at 4 Hz) is trimmed; higher levels are kept
intact so full-session history remains visible at coarser zoom levels.

### 3.3 Heartbeat polling on main thread  
**File:** `gui/main_window.py` — 100 ms `_ui_heartbeat_timer`  
The heartbeat triggers multiple deferred refresh closures.  If any closure does
heavy work (large array copies, layout recalculation), the UI can jitter.  
**Fix:** Profile the heartbeat callbacks with `QElapsedTimer` and move any
closure that consistently takes > 5 ms to a background worker.  
**Status:** TODO

---

## Priority 4 — Maintainability

### 4.1 200+ instance attributes on MainWindow  
**File:** `gui/main_window.py:__init__`  
The window has grown to 200+ instance attributes.  The most redundant cluster is
the sensorgram metric state (`_sensorgram_metric_visible_modes`,
`_sensorgram_metric_primary_mode`, `_trace_stats_metric_name`) which all describe
the same selection.  
**Fix:** Introduce a `SensorgamDisplayState` dataclass that owns these three
fields, with a single `window._sensorgram_display` attribute.  
**Status:** TODO

### 4.2 Timer-based debounce duplicated in multiple places  
**File:** `gui/main_window.py`, `gui/main_window_state.py`  
`_acquisition_state_timer` and `_ui_state_timer` both follow an identical
start/connect/timeout pattern.  A small `DebouncedCallback(interval_ms, callback)`
helper would centralise this and make future timers trivially correct.  
**Status:** TODO

### 4.3 Schema migration coercions are hardcoded ✅
**File:** `storage/app_config.py:_coerce_processing_settings()`  
Field renames are now centralised in `_PROCESSING_SETTINGS_FIELD_RENAMES: dict[str, str]`
so future renames are a one-line entry.  The `fit_enabled → fit_method` coercion
was also moved into `_coerce_processing_settings` (was only in
`load_processing_settings`) so it now applies to HDF5 loads too.  A bug was
fixed: the coercion previously only triggered when `fit_method == "none"`, missing
the case where `fit_method` was absent.

### 4.4 `_restoring_ui_state` flag as implicit cross-function communication  
**File:** `gui/main_window_lifecycle.py:29`  
`restore_ui_state_for()` already uses `try/finally`, which is correct.  The flag
itself is the right pattern; there is no bug here.  If the codebase grows, this
could be promoted to a context-manager class so callers can nest it safely, but
it is not urgent.  
**Status:** Low priority / optional

---

## Quick wins (already done)

| Item | File | Status |
|---|---|---|
| HDF5 flush default 5 s → 1 s | `main_window.py:603` | ✅ |
| Probe runs in background thread | `main_window_import_dialog.py` | ✅ |
| Silent probe exceptions now logged | `main_window_import_dialog.py` | ✅ |
| File-dialog start dir is created if missing | `main_window_import_dialog.py` | ✅ |
| ECW theme change propagates to plan model | `experiment_control_window.py` | ✅ |
| Delegate editor dark-fallback colors fixed | `flow_plan_model.py` | ✅ |
| Sensorgram not reloaded on processing-settings import | `main_window_import_dialog.py` | ✅ |
| `_closing` guard in compression/archive-reload handlers | `acquisition_controller.py`, `main_window.py` | ✅ |
| ECW signals disconnected on close | `main_window_lifecycle.py` | ✅ |
| Live metric cache level-0 ring-buffer cap | `plot_view_cache.py` | ✅ |
| `fit_enabled` migration bug + consolidate into `_coerce_processing_settings` | `storage/app_config.py` | ✅ |
| `_closing` guard in `handle_hardware_init_finished_for` | `main_window_lifecycle.py` | ✅ |
| ECW theme sync on first open (was always forced dark) | `main_window_lifecycle.py`, `experiment_control_window.py` | ✅ |
| ECW `set_theme` idempotency guard | `experiment_control_window.py` | ✅ |
| `datetime.utcnow()` → `datetime.now(timezone.utc)` | `storage/measurement_archive.py`, `storage/metric_archive.py` | ✅ |
| Session writer flush fallback `5.0` → `1.0` | `storage/measurement_archive.py:82` | ✅ |
| `return` inside `finally` swallowed callback exceptions | `experiment_control_window.py:_run_gui_callback_timed` | ✅ |
