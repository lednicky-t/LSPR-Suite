---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/storage/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/undo/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/ui_state.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/app_rewrite.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/threaded_task.py"
---

# LSPRi: sessions, autosave, undo / redo, remembered UI state

## Sessions

A **dataset** (raw images + metadata) is never written. A **session** is one reproducible working copy of everything derived (ROIs, masks, settings, `analysis/scan.h5`); `storage/session_coordinator.py` knows the active one.

- **Autosave never writes on the GUI thread.** The GUI thread runs `prepare_session` (copies live module objects into plain data; must not move to the worker); one `threading.Thread` runs `SessionWriter.write`. `flush()` waits for disk, `set_root()` doesn't: code reading a session folder back calls `autosave.wait_until_written(root)`. A new session field goes through `prepare_session`, never read live by the writer. (`docs/session_autosave_blocking_analysis_2026-10-17.md`)
- **Loading never reads on the GUI thread.** `active_session_changed` -> `restore` blanks the modules and starts `SessionLoadTask`; **autosave stays off for that root until the load is applied**, and stays off after a failed / cancelled load (never write blank over saved). Code that opens a dataset then reads session state waits for the load (tests: `_settle()` in `test_lspri_rewrite_session_autosave.py`). Shared thread / signal / Cancel wrapper: `threaded_task.py` `ThreadedTask`. (`docs/session_load_off_gui_thread_2026-10-22.md`)
- New sessions start blank; the one exception is the export's Session carry (`storage/session_carry.py`, `docs/session_carry_export_2026-10-22.md`).

## Undo / redo

- One shared `undo.undo_manager` (design: `undo/manager.py` docstring). Each mutating command pushes one `FunctionCommand` (label + `undo()`/`redo()` closures over its own state), not a deep clone. `begin_batch(label)` / `end_batch()` merge a gesture; `UndoManager` does nothing while a batch is open.
- **`SelectionModule` (ROI / cube selection) is never undoable. `AreaSelectionModule` is**: one step per change; `reset()` (rotate / flip / crop) is not a step and voids older steps (version token). Picking a tool is not a step.
- UI: Edit -> Undo / Redo, Ctrl+Z, Ctrl+Y / Ctrl+Shift+Z (`app_rewrite._wire_edit_menu`). Wired: `RoiToolbox`, `GeometryModule`, `ChromaticModule`, `BackgroundModule`, `AreaSelectionModule`, `MaskModule` (`lspri-mask.md`).

## Remembering UI state

- Every user-changeable control is remembered unless the maintainer says otherwise: `ui_state.bind("area/name", widget)` (`panels/ui_state.py`; store passed as optional `ui_state` or via `restore_ui_state(store)`); non-widget state via `store.get` / `store.set`.
- A hand-wired value = one row in `storage/ui_state_keys.py` + its wiring in `app_rewrite.py`, **never a new `AppSettings` field** (`AppSettings` = last dataset, theme, layout, presets, `ui_state`).
- Per-dataset state (highlight range, manual reference frame, fixed display range) is saved with the dataset folder. Armed canvas tools and the area-selection mode are deliberately not restored.
