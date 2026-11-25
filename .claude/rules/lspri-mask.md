---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/mask/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/mask_*"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/feature_map_task.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/mask*"
---

# LSPRi: Mask tab (Scan, Auto mask, Feature map), mask undo

Docs: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`, `docs/feature_map_as_selection_2026-11-23.md`, `docs/roi_feature_detection_2026-10-29.md` (§12: controls), lifecycle audit `docs/transient_scan_automask_slider_audit_2026-10-17.md`.

- A mask Scan or Auto mask run belongs to the dataset, session and mask it started with.
- Mask pointers (`MaskPointersModule`) are cosmetic, never a mask. Auto mask turns standing pointers into masks as **one undo step**. Only border-sitting, going-nowhere objects are `artefact` (never masked); a particle that arrives and rests is masked.
- Feature map (`image_tools/mask/feature_map.py`, `panels/feature_map_task.py`) uses **no ROIs and never the mask**: moving ROIs keeps its result; new rotation / crop / chromatic model drops it. Averages cubes, no background flattening. `prepare` (slow, processes) and `score` (limits, ~2 s) stay separate: limits out of `prepare` (`stage_a_key`). Faint dirt is found, not masked by default.
- **Kinds and margin are not search settings**: ribbon row, act on a finished result (`FeatureMapTask.set_kinds` / `set_margin`; margin regrown on a thread; the run's selection follows while untouched). New "what to add" controls stay out of `prepare` / `score`; search settings stay off the ribbon row.
- **`MaskModule` undo**: Mask-row commands, the Auto mask batch (`set_mask_changes`) and every edit (`apply_candidate`, `apply_morphology`, `paint_brush` via `_commit_edit`) are undoable. Loading a PNG (`set_mask_change`), clearing all masks and session load are not, so every entry carries a version token and is skipped with a message if the mask changed since. Brush strokes wrap stamps in `begin_batch` / `end_batch`. Masks stored compressed (`MaskChange`; `.mask` read-only). **Don't make loading or clearing masks undoable without checking in.**
