---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/roi_table/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/selection/step_table_module.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/selection/sensorgram_axis.py"
---

# LSPRi: ROI / Groups table

Docs: `docs/roi_table_performance_2026-11-19.md` (tool `tools/roi_table_timing.py`), `docs/step_table_2026-11-05.md`, `docs/roi_resize_drag_cost_2026-11-19.md`.

- **Never call `selectedRows()` / `selectedIndexes()`** on the selection model (70 ms for 160 ROIs): read `selection()` ranges (`RoiTablePanel._view_selection`), select runs (`RoiTreeModel.roi_index_runs`).
- A rebuild with the same rows in the same groups **updates in place** (`RoiTreeModel._adopt`, `dataChanged` + `VALUES_ONLY_ROLE`, no reset). `_rebuild` re-applies view state only after `model.take_reset_flag()` or with `restore_view=True`. One `PAINT_ROLE` per ROI cell. A speed-only change keeps the timing tool's pixel hashes. Slow-rebuild stages: DEBUG records via `applog.StageTimer` (`roi_table.rebuild` etc.; `python -m lspr_imaging_app.applog grep <file> --event roi_table.rebuild`); a new stage gets `stages.mark(...)`.
- **The step columns' common decimals are cached** (`RoiTreeModel._refresh_decimals`, key incl. `_numbers_version`): a new way for the step table's values to change without `numbers_changed` / `columns_changed` must call `mark_numbers_changed()`. `RoiTreeModel.layout_pass()` wraps `expandAll` only (Qt asks every column for a row height). Paint inside a spanned group header only for the step columns on screen (`_step_columns_on_screen`). (`docs/roi_table_group_header_paint_2026-11-26.md`)
- **Per-column number format** (right-click header / number cell -> Display): `number_format.py` + `column_display.py`, settings by column key in the model, display only (tool tips, copy, editor stay plain). A cell text goes through `format_value(value, plain_decimals, display)`; never format a table number another way.
- **While a ROI is dragged the table waits** (`HiddenPanelGate.set_held` fed by `ImagePanel.roi_edit_running`); any panel rebuilding on ROI changes does the same.
- "Which rows are on screen" is asked again after the table's own rebuild, not only after module signals.
- **Frozen columns:** read `panels/roi_table/view.py`'s docstring before touching `drawRow`, `indexAt`, `visualRect` or delegate column positions. Coloured step headers are hand-drawn and follow the header style sheet in `panel.py`.
- **`RoiTreeView.refresh_column_visibility` is the only code that shows / hides columns** (never `setColumnHidden` elsewhere); fixed widths only via `set_fixed_column_width`.
- Columns 5 / 6 = rim offset `d` (from the shape edge, negative = inside) / thickness `w` for every shape (`rows.COLUMN_RIM_OFFSET / COLUMN_RIM_THICKNESS`, `has_cell`, `RoiToolbox.set_rim`); blank when no rim. Column 7 **Type**: `rows.icon_of` / `kind_of` pick pictogram and colour (green rim or links, amber no reference, blue reference in use, gray unused; `type_icons.py`); the first column's dot keeps the ROI colour; `row.shape` stays the editing key.
- **Column selection** (2026-11-26): a header click selects the column (Ctrl / Shift extend), the left ~20 px of a header sorts; selection is kept by `column_keys` key in `RoiTreeView.column_selection` (`column_selection_controller.py`), wash + frame painted by the delegate and the header, and the step columns are the Sensorgram plan bar's selection (`step_link.py`, wired in `app_rewrite._follow_plan_bar`). Never sort from `sectionClicked`; connect `header.sort_requested`. (`docs/roi_table_column_selection_2026-11-26.md`)
- **One `SensorgramAxis` per settings module, made with `SensorgramAxis.of`**; it and `StepTableModule` live in `selection/`.
- Comparison column titles use real subscripts (`step_columns.subscript_runs`), written only by `step_table_module.compare_title`; a step title is the user's note, never parsed; CSV labels stay ASCII.
