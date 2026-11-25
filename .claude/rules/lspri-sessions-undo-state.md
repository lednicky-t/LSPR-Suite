---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/storage/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/undo/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/ui_state.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/app_rewrite.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/threaded_task.py"
---

# LSPRi: sessions, autosave, undo / redo, remembered UI state

Moved verbatim from `apps/LSPRi/eva/CLAUDE.md` (2026-11-25).

## Sessions

A **dataset** is raw images plus acquisition metadata, never written to. A **session** is one independently reproducible working copy of everything derived from it (ROI table, masks, settings, `analysis/scan.h5`). `storage/session_coordinator.py` knows the active session; `app_rewrite.py` wires it to the modules. Full history: the design record's Sessions section.

**Autosave never writes on the GUI thread** (2026-10-17, maintainer rule: saving has no business there). Two steps: the GUI thread reads the modules and `prepare_session` copies them into plain data (several module queries return *live* mutable objects, so this must not move to the worker); one plain `threading.Thread` runs `SessionWriter.write` (PNGs + JSON; it remembers masks already on disk, so an unchanged save is ~20 ms). `flush()` waits until the data is on disk; `set_root()` does not, so code that reads a session folder back calls `autosave.wait_until_written(root)` first. A new field in the session must go through `prepare_session`, never be read from a live module by the writer. Record: `docs/session_autosave_blocking_analysis_2026-10-17.md`.

**Loading a session never reads on the GUI thread either** (2026-10-22, maintainer's choice: open blank, fill in when ready). `SessionCoordinator.active_session_changed` -> `restore` resets the modules to blank at once and starts `SessionLoadTask` (`storage/session_load_task.py`; status-bar entry "Loading session: Loading masks n of N"); **autosave stays off for that root until the load has been applied**, and a failed or cancelled load leaves it off (a blank state must never be written over the saved one). So code that opens a dataset and then reads session state (ROIs, masks) must wait for the load: tests call `_settle()` in `tests/integration/test_lspri_rewrite_session_autosave.py`. A session with no file yet is still applied synchronously. The shared thread/signal/Cancel wrapper for tasks is `threaded_task.py` (`ThreadedTask`; scan, Auto mask, mask export and session load use it). Record: `docs/session_load_off_gui_thread_2026-10-22.md`.

Session carry on export: `storage/session_carry.py`, `docs/session_carry_export_2026-10-22.md` (the one approved exception to "new sessions start blank").

## Undo / redo

- One shared `undo.undo_manager`. Import it directly, as modules import `diagnostics_hub`. Design reasoning: `undo/manager.py` module docstring.
- Each mutating command pushes one `undo.FunctionCommand` (label plus `undo()`/`redo()` closures over its own module's state). Not a deep clone of module state.
- `begin_batch(label)` / `end_batch()` merge a burst (one mouse drag) into one entry.
- **The ROI / cube selection (`SelectionModule`) is never undoable. The *area* selection (`AreaSelectionModule`: rectangle, lasso, wand, invert, deselect) is undoable since 2026-11-15** (maintainer: "implement undo for selection"; it replaced the wand's Max size guard): one step per change, restoring the whole previous state; `reset()` (rotate / flip / crop) is not a step and voids older steps (version token, skipped with a message), like `MaskModule`'s. Picking a tool is not a step.
- **Reachable from the UI** (2026-10-07): Edit -> Undo / Redo, Ctrl+Z and Ctrl+Y / Ctrl+Shift+Z, application-wide (`app_rewrite.py` `_wire_edit_menu`). `UndoManager` does nothing while a gesture batch is open.
- Wired: `RoiToolbox`, `GeometryModule`, `ChromaticModule`, `BackgroundModule`, `AreaSelectionModule`, `MaskModule` (its rules: `lspri-mask.md`).

## Remembering UI state

Every user-changeable control is remembered across restarts unless the maintainer says otherwise (stated 2026-10-05). For a checkbox, checkable button, spin box or combo box, add one line: `ui_state.bind("area/name", widget)` (`panels/ui_state.py`; the store is passed in as an optional `ui_state` argument, or `restore_ui_state(store)` on a panel). For state outside a widget use `store.get`/`store.set`. A value wired by hand (not a widget `bind`) is one row in `storage/ui_state_keys.py` (key, default, type) plus its wiring line in `app_rewrite.py`: never a new `AppSettings` field. `AppSettings` holds only app-level state (last dataset, theme, window/dock layout, layout presets) and `ui_state` itself; a settings file from before 2026-10-07 is migrated on load. Values live in `AppSettings.ui_state`, written after a short pause. State that only means something for one dataset (highlight range, manual reference frame, fixed display range) is saved together with the dataset folder and restored only for that dataset. Armed canvas tools and the area-selection mode are deliberately not restored (see `docs/rewrite_build_log_2026-09.md`, 2026-10-05).
