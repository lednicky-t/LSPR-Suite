---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/selection/area*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/geometry/**"
---

# LSPRi: Image panel, selection tools, rails, canvas drawing

Moved verbatim from `apps/LSPRi/eva/CLAUDE.md` (2026-11-25). Docs: `docs/selection_wand_2026-11-14.md`, `docs/selection_lock_and_tool_move_2026-11-15.md`, `docs/image_layers_2026-11-16.md`, `docs/view_side_ribbon_2026-11-21.md`, `docs/manual_edit_retired_and_roi_selection_lock_2026-11-16.md`.

- New behaviour goes in a file of its own with a small builder (the pattern of `selection_tools.py`), not into `ImagePanel`; construction code lives in `canvas_build.py`, `ribbon_build.py`, `navigation_build.py` (size budgets: see the LSPRi CLAUDE.md).
- A selection is only a selection (it limits the editing tools); the Mask tab turns it into mask pixels. Editing tools read `AreaSelectionModule.mask(shape)` (selection, else lock, else None); `selected_mask(shape)` is the selection itself. Tools compute on the whole image and are cut to the lock afterwards. **Every selection producer, the brush included, obeys the picked mode and Shift / Alt**, exactly.
- **Selection speed rules** (`docs/selection_lock_and_tool_move_2026-11-15.md` section 14): outline pens stay **width 0**; a pixel selection's outline is traced (`region_outline`), never built from rectangles and `simplified()`; polygons are filled by `rasterize_polygon`, never `skimage.draw.polygon`. Dense outlines are `CachedLineItem`s; the look is one state object, `selection_style.py`; a Subtract / Intersect result wears the Selection's look (`selection_palette.LOOK_OF`), no third row.
- **Every tool with numbers** has them in a pop-up on a right-click of its button and changes them with a right-drag on the image (`panels/image/tool_drag.py`). `create_relative_contrast_mask(side="both")` stays bit-identical to the old function.
- **The wand reads the active layer**, never a hidden one; the select-by-value tools need the Image layer active; hiding a layer never changes the active one.
- On the View rail an overlay's icon is its show / hide switch and a right-click opens the rest of its controls (`right_click_menu.py`). **A rail's pop-ups and tool tips are placed with `side_rails.beside_point` / `tooltip_point`**, never `QPoint(button.width(), 0)`. Tests that read rail geometry wait with `is_animating()`. The Histogram follows the area selection only while the View ribbon is rolled down (maintainer's choice).
- **Every code path that changes the ROI selection goes through `set_roi_selection`**, so the ROI selection lock holds (`remap_roi_ids` and `restore_roi_selection` pass it). The lock is saved in the session, not undoable.
- **Esc leaves the canvas tool that is on** (2026-11-19, `panels/image/tool_escape.py`; first Esc drops what the tool is doing, the next leaves it): a new canvas tool needs nothing extra if it is an `ImageTool`; a tool with something in progress adds it to `CanvasInteraction.handle_key` (return True only when it dropped something).
- **Many small marks on the canvas are drawn with `drawPoints` and a cosmetic pen, never as antialiased path sub-shapes** (2026-11-18, the rectangle handles): 4,000 small antialiased squares cost 43 ms as one path, 7.5 ms as one `drawRect` each, **2.7 ms** as `drawPoints` with a square-capped cosmetic pen (`panels/image/rectangle_handles_item.py`). And never work out geometry in `boundingRect()` / `paint()`: Qt calls `boundingRect()` several times per paint, so keep the result until the data or the zoom changes. Benchmark a painting change in isolation before choosing (the first rewrite of those handles was slower than the code it replaced).
- **`image_tools_enabled` must not be persisted as off.** It is turned off while the crop/rotate tool is active so the full image shows. If persisted, crops silently fail to re-apply on reload.
- Image panel heavy numpy: the value range and, for a frame with NaN pixels, the ready-made 8-bit picture come from the render thread (`panels/image/display_picture.py`, byte-identical to pyqtgraph's own and pinned by `tests/integration/test_lspri_display_picture.py`).
