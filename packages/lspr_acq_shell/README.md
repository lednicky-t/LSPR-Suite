# lspr-acq-shell

Shared, modality-agnostic *live acquisition* code for the LSPR Suite's acquisition
apps (currently `singleLSPR Acquisition`; `LSPRimaging Acquisition` once it exists).

This package is being built up incrementally by extracting already-working code out
of `apps/sLSPR/acq`, not written fresh. See
[`docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md`](../../docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md)
(§4, "Phase 1") for the extraction plan and order, and
[`docs/architecture/general/lspri_acq_build_log.md`](../../docs/architecture/general/lspri_acq_build_log.md)
for a dated log of what has actually moved so far and why.

## What belongs here

Code that is genuinely about *running a live acquisition experiment*, independent of
what physical measurement is being taken (a spectrometer reading vs. a camera frame):

- Fluidics device framework (pump/valve/selector discovery, connect, lifecycle,
  the device-family registry)
- Experiment-control plan editing/execution (the V49 split — see
  `apps/sLSPR/acq/docs/experiment-control/CODEX_EXPERIMENT_CONTROL_REUSE_SPLIT_V49.md`)
- Sensorgram plotting and session/run bookkeeping
- Async HDF5-writer plumbing (the threading/queue mechanism, not any app-specific
  dataset schema)
- Diagnostics and settings-persistence patterns

## What does NOT belong here

- Anything shaped around a specific measurement type (a 1D spectrum vs. an image
  cube) - that stays in the app package.
- Camera/illumination-source drivers, ROI/image-processing logic - LSPRimaging
  Acquisition's own concern.
- Spectrometer drivers, spectrum processing - singleLSPR Acquisition's own concern.

## Why this package exists (dependency-matrix exception)

`lspr_core`, `lspr_io`, and `lspr_ui` are pairwise-independent by rule (see
`docs/architecture/general/dependency-matrix.md`). This package is not a fourth
peer of those three - it is shared *application* code (Qt widgets, device runtime)
that legitimately depends on all three. It exists because the experiment-control
and fluidics code was originally written once, inside `apps/sLSPR/acq`, and needs
to be usable from a second app without being copy-pasted or drifting out of sync.

## Status

Empty scaffold as of 2026-08-06 - nothing has been extracted into it yet. Do not
depend on any API here being stable until the corresponding entry in the build log
says the extraction is complete and verified.
