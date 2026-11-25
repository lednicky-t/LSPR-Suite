---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/selection/area*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/geometry/**"
---

# LSPRi: Image panel, selection tools, rails, canvas drawing

Docs: `selection_wand_2026-11-14`, `selection_lock_and_tool_move_2026-11-15` (§14 speed), `image_layers_2026-11-16`, `view_side_ribbon_2026-11-21`, `manual_edit_retired_and_roi_selection_lock_2026-11-16` (all in `docs/`).

- New behaviour goes in its own file with a small builder (pattern: `selection_tools.py`), not into `ImagePanel` (size budget); construction in `canvas_build.py`, `ribbon_build.py`, `navigation_build.py`.
- A selection only limits the editing tools; the Mask tab turns it into mask pixels. Tools read `AreaSelectionModule.mask(shape)` (selection, else lock, else None; `selected_mask` = selection itself), compute on the whole image, then cut to the lock. **Every selection producer, brush included, obeys the picked mode and Shift / Alt.**
- **Selection speed**: outline pens **width 0**; pixel outlines traced (`region_outline`), never rectangles + `simplified()`; polygons via `rasterize_polygon`, never `skimage.draw.polygon`; dense outlines are `CachedLineItem`s; look = `selection_style.py`; Subtract / Intersect results wear the Selection look (`selection_palette.LOOK_OF`).
- **Tools with numbers**: pop-up on right-click of the button, right-drag on the image changes them (`tool_drag.py`). `create_relative_contrast_mask(side="both")` stays bit-identical to the old function.
- **The wand reads the active layer**, never a hidden one; select-by-value needs the Image layer active; hiding never changes the active layer.
- View rail: icon = show / hide, right-click = more (`right_click_menu.py`). **Rail pop-ups and tips are placed with `side_rails.beside_point` / `tooltip_point`**, never `QPoint(button.width(), 0)`. Geometry tests wait on `is_animating()`. The Histogram follows the area selection only while the View ribbon is rolled down.
- **Every ROI-selection change goes through `set_roi_selection`** (lock; `remap_roi_ids`, `restore_roi_selection` pass it). The lock is saved, not undoable.
- **Esc** (`tool_escape.py`): first drops the tool's work, then leaves the tool. An `ImageTool` needs nothing extra; a tool with work in progress adds it to `CanvasInteraction.handle_key` (True only when it dropped something).
- **Many small canvas marks: `drawPoints` with a square-capped cosmetic pen**, never antialiased path sub-shapes (4,000 handles: 43 ms path vs 2.7 ms; `rectangle_handles_item.py`). Never compute geometry in `boundingRect()` / `paint()`; cache until data or zoom changes. Benchmark painting changes in isolation.
- **Never persist `image_tools_enabled` as off** (off during crop / rotate; persisted, crops silently fail to re-apply).
- Value range and the 8-bit picture for NaN frames come from the render thread (`display_picture.py`, byte-identical to pyqtgraph, `tests/integration/test_lspri_display_picture.py`).
