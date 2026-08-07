# Manuals

Device manuals for LSPRimaging Acquisition's Phase 0 hardware spikes, mirroring
sLSPR acq's `docs/manuals/` convention (see `apps/sLSPR/acq/docs/manuals/`).

- `cri-varispec-lctf-manual.pdf` — CRi VariSpec LCTF User's Manual (Doc Part No.
  MD15474 Rev. D, August 2010). Covers the serial command set used by
  `spikes/lspri_acq_phase0/illumination_probe.py` and, eventually, the real
  `variSpec_lctf.py` driver described in
  `docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md` §6.2.

This lives under `spikes/` for now because `apps/LSPRi/acq` doesn't exist yet.
Once that app is scaffolded as its own repo/submodule, move this folder to
`apps/LSPRi/acq/docs/manuals/` per the architecture plan.
