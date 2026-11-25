---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/roi/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/domain/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/*roi*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/*shape*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/*overlay*"
---

# LSPRi: ROI model (shapes, rims, links, coordinates)

Plan: `docs/roi_shapes_and_linking_plan_2026-11-18.md` (supersedes `roi_system_roadmap.md`). Scan side: `lspri-scan.md`.

## Shapes

- `AreaRoi.shape` (`domain/roi_shapes.py`: `Circle` / `Rectangle` / `MaskShape`) and `AreaRoi.border` (`Border(gap, thickness)` = pixels g .. g+t from the edge; `None` follows `AreaRoiDetectionSettings.default_border()`, `NO_BORDER` = none). No loose diameter / geometry / mask fields: ask the shape (`reach_radius` / `nominal_diameter`).
- Rectangles and masks are found inside their own box (`roi/rasterize.py` `_region_hits`, shared by scan and drawing) and enter the neighbour test with their **reach** (`RoiSet.sample_r` / `ring_out_r` hold a reach for them: `roi_shapes.reach_radius`, `border_reach_radius`). `tests/unit/test_lspri_roi_shape_reach.py` pins that no pixel falls outside a window, read box or neighbour answer.
- Diameter commands (`resize_rois`, `reset_roi_diameters`, `refine_rois`, `place_array`) are circle-only and refuse other shapes; **changing the sample diameter alone carries the ring with it**. Rotate / flip / crop turns a rectangle's angle (`remap_rectangle`).
- **A pixel mask is its pixels**, not its centre (centre = label point): drawn and hit-tested from its pixels through the frame affine (`shape_paths_item.placed`, `shape_hits`). **It moves by whole pixels**: `RoiToolbox._at_position` (drag, typed X/Y, wheel, paste) shifts pixels and label by `whole_pixels(asked - centre)`, no resampling; a move under half a pixel for every ROI raises instead of doing nothing; drags keep their own count (`RoiGestures._map_applied`); `MaskOutlines` caches outlines by pixels, so a drag translates, not retraces.
- Session schema 1.12; a 1.11 file is read once via `_legacy_shape_and_border` (no other migration).

## Rims and links

- The user's word is **rim** (labels, tips, messages, undo labels); code and saved keys keep `Border` / `border`.
- **Every blob of a pixel map has its own rim** (`rasterize._mask_band`: union of per-blob bands, minus other blobs' pixels; a many-blob map with a rim adds `|blob-rims` to its hash).
- `RoiToolbox.rims_to_references(ids, image_shape)` turns a sample's rim into a linked pixel-map reference (frozen at the reference wavelength, taken on the cube on screen via `cube=`); the sample becomes **rim-less** (`turn_rim_off` on every cube, offset / thickness kept for `add_rim`). `add_rim` gives a reference or rim-less sample a rim. Both are in the right-click ROI menu.
- Links live on the sample (`AreaRoi.reference_roi_ids`); rules in `domain/roi_links.py`; every command that renumbers or removes ROIs carries them (`RoiToolbox._survivor_links`). A linked reference never moves with its sample.

## Pixel-index vs view coordinates

Stored and measured positions (scan, detectors, table X/Y, mask pixels, `RoiToolbox.display_positions`) are **pixel-index** (pixel `(row, col)` = point `(col, row)`); canvas and pointer are **view** (pixel covers `[col, col+1)`). The difference, `overlay_geometry.CELL_OFFSET` = 0.5, is added once, after the affine. **Draw ROIs / compare the pointer with `RoiQueries.view_centers`; turn a pointer into a stored centre only with `RoiQueries.display_to_reference`.** A new overlay from stored positions adds the shift itself.
