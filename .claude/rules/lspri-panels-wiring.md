---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/app_rewrite.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/shortcuts.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/launch.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/startup_tracker.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/applog/**"
  - "apps/LSPRi/eva/src/main.py"
---

# LSPRi: panels, app wiring, shortcuts, menus, logging

Moved verbatim from `apps/LSPRi/eva/CLAUDE.md` (2026-11-25).

## Progress, task indicator, launch

- Launch screen and stage timing (2026-10-24): `launch.py` (`LaunchSplash`, `StageClock`); `src/main.py` shows the card before the heavy imports, so **`launch.py` may import only PyQt6 and `applog`**. `build_main_window` logs `app.build_stage` per step: add `stages.done(...)` after a new step that may be slow. Record: `docs/startup_gui_thread_audit_2026-10-24.md`.
- Launch: when the last dataset is reopened, `startup_tracker.py` `StartupTracker` shows "Starting up: step N of 5: ..." in that same row until the dataset, session, first image, scan results and Spectra / Sensorgram are all settled (2026-10-24; steps in `app_rewrite._build_startup_tracker`). A new thing that loads at launch adds a `StartupStep` there.
- App-wide indicator: `panels/task_indicator.py` `TaskIndicator`, a permanent status-bar row (spinner, progress bar, start time, elapsed/ETA, Cancel; keeps the last result until dismissed; History popup; every finished run is logged). A task joins it by emitting `task_progress(task_id, label, fraction, message)` (fraction < 0 = unknown) and `task_finished(task_id, outcome, message)` (always, once per run; outcome `completed`/`failed`/`cancelled`), then three connects in `app_rewrite.py` (report, finish, cancel). Reference: `ChromaticAutoDetect`. `analysis_progress(float)` and `export_progress(int, str)` are older ad-hoc shapes; migrate them when touched.

## Display panels

- Each display panel has its own ~100 ms redraw-coalescing timer. Reacting to every event immediately is not the same as reacting promptly.
- **A display panel does no work while it is hidden** (closed dock, behind a tab, window minimised): it keeps the data it is handed (cheap), skips computing and drawing, and catches up once when shown again. Use `panels/visibility_gate.py` `HiddenPanelGate` (`defer()` at every redraw entry, `set_panel_visible()` connected to the dock's `visibilityChanged` in `app_rewrite.py`). Done for the Histogram, the ROI table and the Log panel; **every new display panel (Spectra, Sensorgram, ...) must do the same.** **The same goes for work a module does only for a display** (2026-11-23): the scan's out-of-date check belongs to `ScanResultsModule`, not to a panel, and only the Spectra and Sensorgram draw its answer; it is held while both docks are hidden (`app_rewrite._wire_check_follows_displays`, `set_displays_hidden`) and runs once when one is shown. It is a hold of its own, separate from the user's Live / Paused (`set_paused`), which is never changed by it. A new display of the check's answer joins that wiring; a new module that works only for a display asks whether it should wait for one too (`docs/snapshot_source_cost_2026-11-23.md`). Measured 2026-10-14: a Histogram redraw is ~30 ms of GUI time per 1.2 MP frame. While a ROI is being dragged a panel that rebuilds on ROI changes also waits (`HiddenPanelGate.set_held`; see `lspri-roi-table.md`).
- **Heavy numpy work of a display panel does not run on the GUI thread** (2026-10-14). Pattern: gather inputs on the GUI thread, compute on a worker, draw on the GUI thread. Histogram: `compute.redraw_counts` on an `AnalysisWorker` (newest request wins; `_on_counts_ready` draws; a result from before a clear, or that arrives while hidden, is dropped). Frames, masks and regions handed to a worker are shared, not copied: never modify them in place. A control that needs the *redrawn* result (a Y refit) must wait for the draw, not assume `_redraw()` finished.
- A handler on `cube_changed` / `wavelength_changed` / `image_rendered` runs on every slideshow step: it must be O(1) in the dataset size (no scans over all records or timings; use `DatasetModule` queries, which are indexed). Two of these cost 25 ms and 18 ms per step on a 314-cube dataset before they were found (docs build log, 2026-10-14).
- **Fixed-width Qt text fields:** measure the rendered margin, not `.text()` or `.width()` alone. `field.width() - QFontMetrics(font).horizontalAdvance(worst_case_text)` near zero means clipping risk; add a few explicit pixels. Size once for worst-case content. Example: `panels/histogram/highlight_range_controls.py`.

## Keyboard shortcuts and mouse gestures

All in one table, `shortcuts.py` (design and the list of changes: `docs/keyboard_shortcut_registry_design_2026-10-30.md`); Help -> Keyboard shortcuts (F1) shows it. **A new key or gesture is a row there first.** A menu action gets its keys with `bind_action(action, "id")` (never `setShortcut("Ctrl+...")`); a key handler asks `matches(event, "id")` / `key_matches(key, modifiers, "id")` (never `event.key() == Qt.Key.Key_...`). Matching is exact: `Ctrl+Delete` is not `Delete`; a handler that ignores the modifiers passes `NoModifier` on purpose. A mouse gesture cannot be bound by the table: it is a text row (`keys=()`, a `gesture`), the one part nothing checks, so add its line when you add the gesture. Esc and canvas tools: `lspri-image-panel.md`. `tests/unit/test_lspri_shortcuts.py` fails on two bindings of one scope sharing a key, on an id used in the source but not in the table, and on a key in the table nothing uses.

## Menu icons and availability

**A context menu offers only what can be done now** (maintainer, 2026-11-19): an entry whose prerequisite is not met (no ROI selected, nothing linked, no cell picked, ...) is **left out, not greyed**, and a menu with nothing in it does not open (`docs/menu_icons_2026-11-19.md`, last section; the menu bar keeps its greyed entries). Every menu entry wears an icon, and **a command wears the same icon in every menu** (2026-11-19, `docs/menu_icons_2026-11-19.md`). A menu names what an entry does, never an icon file: `give_icon(action, "delete")` (a menu built when it pops up) or `give_persistent_icon(...)` (a menu that lives as long as the window; recoloured on a theme switch) from `panels/menu_icons.py`, whose `ICONS` table is the one place that picks the icon. A new command adds a line there; a new Tabler icon is vendored first (`packages/lspr_ui/ICONS.md`). `tests/integration/test_lspri_menu_icons.py` fails on a key that is not in the table. A right-click `QMenu(parent)` calls `deleteLater()` after `exec` (it is a child of a long-lived widget).

## Logging details

Design record: `docs/logging_design_2026-10-14.md`. Package `applog/` (Qt-free), viewer `panels/log/` (Help -> Log Console, Ctrl+Shift+L). Everything from `logging.getLogger(__name__)` is captured (ring buffer for the panel, JSON Lines file in `logs/`, console only at WARNING unless `LSPR_CONSOLE_LOG` is set). Read a saved log without the app: `python -m lspr_imaging_app.applog summary|grep FILE.jsonl`. `configure_logging()` runs only in `main()`; tests never call it (and must not rely on log files).
