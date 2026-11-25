---
paths:
  - "apps/sLSPR/acq/**"
  - "packages/lspr_acq_shell/**"
---

# Acquisition runtime rules

**Core rule: acquisition and file writing must be lossless. Processing and GUI display may skip stale frames.**

Runtime pipeline invariant (authoritative: `apps/sLSPR/acq/docs/runtime_pipeline_architecture.md`):
Raw acquisition and active recording are lossless and independent of GUI speed. Latest-only queues and skipped-frame policies are allowed only for processing, fitting, metric preview, and plotting. Manual refresh rate controls processing/display freshness, not raw recording. Any raw recording queue overflow is a serious acquisition/recording error and must be surfaced explicitly.

- Keep raw acquisition/recording, processing, and GUI plotting as separate layers with clear ownership.
- Use queues only at real thread, process, disk, or device boundaries. Use one clear GUI refresh cadence where possible.
- Do not chain callbacks such as queue -> scheduler -> deferred flush -> throttle -> log -> GUI update.
- Do not solve performance or coordination problems with more schedulers, deferred tasks, queues, throttles, wrappers, or diagnostics unless there is a measured need and no simpler alternative. Prefer deleting or bypassing unnecessary orchestration.
- Before adding a helper or abstraction, explain what concrete boundary or repeated logic it simplifies, and check that it creates no hidden control flow, duplicate state, or event-loop work.

**Spectral processing** (`apps/sLSPR/acq/docs/spectral_processing_pipeline_architecture.md`): processing (crop, baseline, smoothing) must never change a value at a wavelength still in view, and baseline/smoothing must only ever apply to the Absorbance spectrum.

Read these before changing the acquisition pipeline:
- `apps/sLSPR/acq/docs/runtime_pipeline_architecture.md` (read first)
- `apps/sLSPR/acq/docs/spectral_processing_pipeline_architecture.md`
- `apps/sLSPR/acq/docs/CODEX_ARCHITECTURE_RAILS_V7.md`
- `apps/sLSPR/acq/docs/CODEX_IMPLEMENTATION_GUIDE_V8_LOSSLESS_ACQ_AND_LOSSY_UI.md`
- `apps/sLSPR/acq/docs/CODEX_RUNTIME_SIMPLICITY_GUIDE_V12.md`
