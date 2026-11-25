---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/roi_table/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/selection/**"
---

# LSPRi: ROI / Groups table

Moved verbatim from `apps/LSPRi/eva/CLAUDE.md` (2026-11-25). Docs: `docs/roi_table_performance_2026-11-19.md` (tool `tools/roi_table_timing.py`), `docs/step_table_2026-11-05.md` (step columns), `docs/roi_resize_drag_cost_2026-11-19.md`. Rim columns 5-7 (Type): see `lspri-scan-roi.md`.

- **Never ask the table's selection model for `selectedRows()` / `selectedIndexes()`** (they call the Python `flags()` of every cell of every selected row: 70 ms to select 160 ROIs): read `selection()` ranges (`RoiTablePanel._view_selection`) and select runs of rows (`RoiTreeModel.roi_index_runs`). A rebuild that lists the same rows in the same groups **updates in place** (`RoiTreeModel._adopt`, `dataChanged` with `VALUES_ONLY_ROLE`, no reset: groups, selection, scroll and Qt's current row stay); `RoiTablePanel._rebuild` re-applies view state only after `model.take_reset_flag()` or with `restore_view=True` (a change whose point is view state, like the Groups-only lock). One `PAINT_ROLE` answers what a ROI cell needs, not one role per fact. A speed-only change must keep the pixel hashes the timing tool prints. DEBUG stage records (one per unit of work: `roi_table.rebuild`, `.fit_step_columns`, `.step_state`, `.slow_paint`, built with `applog.StageTimer`) say where a slow rebuild spent its time: read the log with `python -m lspr_imaging_app.applog grep <file> --event roi_table.rebuild`; a new stage of the rebuild gets a `stages.mark(...)`.
- **One `SensorgramAxis` per settings module, always made with `SensorgramAxis.of`**. `SensorgramAxis` and `StepTableModule` live in `selection/`.
- Comparison column titles are formulas with real subscripts (`step_columns.subscript_runs`), written only by `step_table_module.compare_title`, never with underscores; a step's title is the user's note and is never parsed; CSV labels stay ASCII.
- A "which rows are on screen" answer is asked again after the table's own rebuild (`RoiTablePanel._rebuild`), not only after the module's signals.
- **Frozen columns:** read `panels/roi_table/view.py`'s module docstring before touching `drawRow`, `indexAt`, `visualRect` or the delegate's column positions (they stay relative to the cell's own rectangle). A coloured step header is drawn by hand and must follow the header style sheet in `panel.py`.
- **`RoiTreeView.refresh_column_visibility` is the only code that shows or hides a column**; never call `setColumnHidden` elsewhere. Fixed widths only through `RoiTreeView.set_fixed_column_width`.
- **While a ROI is being dragged the table waits** (`HiddenPanelGate.set_held`, fed by `ImagePanel.roi_edit_running`; 2026-11-19): a debounce timer fires at every pause of the mouse, i.e. in the middle of the drag, and the next move waited ~0.5 s behind the table's rebuild. A new panel that rebuilds on ROI changes does the same.
