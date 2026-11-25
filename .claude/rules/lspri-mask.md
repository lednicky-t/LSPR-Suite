---
paths:
  - "apps/LSPRi/eva/src/lspr_imaging_app/image_tools/**"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/feature_map_task.py"
  - "apps/LSPRi/eva/src/lspr_imaging_app/panels/image/mask*"
---

# LSPRi: Mask tab (Scan, Auto mask, Feature map), mask undo

Moved verbatim from `apps/LSPRi/eva/CLAUDE.md` (2026-11-25). Docs: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`, `docs/feature_map_as_selection_2026-11-23.md`, `docs/roi_feature_detection_2026-10-29.md`.

- A scan or an Auto mask run belongs to the dataset, session and mask it started with (lifecycle rules in the audit `docs/transient_scan_automask_slider_audit_2026-10-17.md`).
- Mask pointers (`MaskPointersModule`) are cosmetic and never a mask. Auto mask turns the standing pointers into individual masks as **one undo step**. Only what sits on the image border and goes nowhere is an `artefact` (never masked); a particle that arrives and rests is masked.
- The Feature map (`image_tools/mask/feature_map.py`, task `panels/feature_map_task.py`) uses **no ROIs** (it finds the disks in the picture) and never looks at the mask: moving ROIs keeps its result; a new rotation / crop or chromatic model drops it. It averages cubes before looking and reads them without background flattening (that may use the ROIs). `prepare` (slow, worker processes) and `score` (limits, ~2 s) are separate: keep the limits out of `prepare` (`stage_a_key`). Faint dirt (soft shadows of out-of-focus dust) is found but not masked by default. Masking features did not raise SNR on the bulk-step data. **The kinds and the margin are not search settings** (2026-11-05): they live on the ribbon row and act on a finished result at once (`FeatureMapTask.set_kinds` / `set_margin`; the result keeps each kind's ungrown mask; the margin is regrown on a thread, never on the GUI thread; the selection the run made follows them while it is untouched, 2026-11-23). Keep new "what to add" controls out of `prepare` and `score`, and a search setting out of the ribbon row (section 12 of `docs/roi_feature_detection_2026-10-29.md`).
- **`MaskModule` undo**: the Mask rows' commands (maintainer OK 2026-10-16), the Auto mask batch (`set_mask_changes`, one undo step, 2026-10-17) and, since 2026-11-14, every edit (`apply_candidate`, `apply_morphology`, `paint_brush` through `_commit_edit`; maintainer: "make also painting undoable") are undoable. Loading a PNG (`set_mask_change`), clearing all masks and a session load are not, so every undo entry carries a version token and is skipped, with a message, if the mask changed after it (`image_tools/mask/module.py` docstring). A future brush stroke wraps its stamps in `undo_manager.begin_batch` / `end_batch`. Masks are stored compressed (`MaskChange`, a few KB each; `.mask` is read-only), which is what makes a batch of hundreds cheap to undo. Don't make loading or clearing masks undoable without checking in.
