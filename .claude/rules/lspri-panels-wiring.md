---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/app_rewrite.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/shortcuts.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/launch.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/startup_tracker.py"
  - "apps/LSPRi/eva/src/main.py"
---

# LSPRi: panels, app wiring, shortcuts, menus

## Launch and task indicator

- `src/main.py` shows the launch card before heavy imports, so **`launch.py` imports only PyQt6 and `applog`**. `build_main_window` logs `app.build_stage`: add `stages.done(...)` after a new slow step. A new thing loaded at launch adds a `StartupStep` to `startup_tracker.py` `StartupTracker` (`app_rewrite._build_startup_tracker`). (`docs/startup_gui_thread_audit_2026-10-24.md`)
- `panels/task_indicator.py` `TaskIndicator` (status-bar row): a task emits `task_progress(task_id, label, fraction, message)` (fraction < 0 = unknown) and exactly once `task_finished(task_id, outcome, message)` (`completed` / `failed` / `cancelled`), plus three connects in `app_rewrite.py` (report, finish, cancel). Reference: `ChromaticAutoDetect`. Migrate the old `analysis_progress` / `export_progress` shapes when touched.

## Display panels

- Each panel has its own ~100 ms redraw-coalescing timer.
- **A hidden panel does no work** (closed dock, behind a tab, minimised): keeps data, skips compute / draw, catches up once when shown. Use `panels/visibility_gate.py` `HiddenPanelGate` (`defer()` at every redraw entry, `set_panel_visible()` on the dock's `visibilityChanged`). **Every new display panel does this.** Same for module work done only for a display: the scan's out-of-date check (`ScanResultsModule`) is held while Spectra and Sensorgram are both hidden (`app_rewrite._wire_check_follows_displays`, `set_displays_hidden`), separate from the user's Live / Paused (`set_paused`). A new display of that check joins the wiring. (`docs/snapshot_source_cost_2026-11-23.md`)
- **Heavy numpy of a panel is off the GUI thread**: gather inputs on the GUI thread, compute on a worker, draw on the GUI thread (Histogram: `compute.redraw_counts` on an `AnalysisWorker`, newest wins, stale results dropped). Arrays handed to workers are shared: never modify in place. A control needing the *redrawn* result waits for the draw.
- Handlers on `cube_changed` / `wavelength_changed` / `image_rendered` run every slideshow step: **O(1) in dataset size** (use indexed `DatasetModule` queries).
- **Fixed-width text fields**: measure the rendered margin (`field.width() - QFontMetrics(font).horizontalAdvance(worst_case_text)`), not `.text()` / `.width()`; size once for the worst case (example: `panels/histogram/highlight_range_controls.py`).

## Keyboard shortcuts and gestures

One table, `shortcuts.py` (F1 shows it; `docs/keyboard_shortcut_registry_design_2026-10-30.md`). **A new key or gesture is a row there first.** Menu actions: `bind_action(action, "id")`, never `setShortcut(...)`; key handlers: `matches(event, "id")` / `key_matches(key, modifiers, "id")`, never `event.key() == Qt.Key...`. Matching is exact (`Ctrl+Delete` ≠ `Delete`); a handler that ignores modifiers passes `NoModifier` on purpose. Mouse gestures are text rows (`keys=()`, `gesture`): add the line by hand. `tests/unit/test_lspri_shortcuts.py` catches clashes, unknown ids and unused keys.

## Menus

- **A context menu offers only what can be done now**: unmet prerequisite = entry left out, not greyed; an empty menu doesn't open (the menu bar keeps greyed entries).
- Every entry has an icon, **the same icon for a command in every menu**: `give_icon(action, "delete")` (pop-up menus) or `give_persistent_icon(...)` (window-lifetime menus) from `panels/menu_icons.py`, whose `ICONS` table is the only place that picks icons. New Tabler icons are vendored first (`packages/lspr_ui/ICONS.md`). `tests/integration/test_lspri_menu_icons.py` checks keys. (`docs/menu_icons_2026-11-19.md`)
- A right-click `QMenu(parent)` calls `deleteLater()` after `exec`.

## Logging details

`docs/logging_design_2026-10-14.md`; viewer Help -> Log Console (Ctrl+Shift+L). Console shows WARNING+ unless `LSPR_CONSOLE_LOG` is set. Read a saved log: `python -m lspr_imaging_app.applog summary|grep FILE.jsonl`. `configure_logging()` runs only in `main()`; tests never call it.
