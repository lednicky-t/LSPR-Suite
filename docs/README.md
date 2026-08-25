# Documentation Map

Suite-wide documentation for the LSPR Suite umbrella repo. App-specific docs
live inside each app (`apps/sLSPR/acq/docs/`, `apps/LSPRi/eva/docs/`, ...) —
this tree is for things that span apps, or describe the suite as a whole.

## Where to look

- **[`architecture/`](architecture/)** — how the suite is put together.
  Start at [`architecture/overview.md`](architecture/overview.md) for the
  ecosystem map and package-boundary rules, then follow into
  `architecture/apps/` (per-app architecture notes) or `architecture/general/`
  (cross-cutting design docs — the LSPRimaging Acquisition build plan, the
  HDF standardization effort, the app-selector/launcher design, dependency
  matrix).
- **[`schemas/`](schemas/)** — the HDF5 data format contracts. Start at
  [`schemas/hdf_standard.md`](schemas/hdf_standard.md) for the shared rules
  (every file stamps a schema name/version, raw data is append-only, readers
  reject unknown schemas), then the per-app measurement/experiment-plan
  format docs and versioning policies.
- **[`decisions/`](decisions/)** — ADR-style records of *why* a
  suite-level choice was made, not just what it is.
- **[`workflows/`](workflows/)** — process docs: the repo map
  ([`workflows/repo-map.md`](workflows/repo-map.md), which repo owns what)
  and the Codex recovery log.
- Loose files at this level cover things that don't fit the categories
  above: [`device_communication_inventory.md`](device_communication_inventory.md)
  (hardware protocols in use), [`portable_installation_guide.md`](portable_installation_guide.md)
  (end-user install steps for the portable bundle), and dated audit reports
  like [`engineering_audit_2026-08.md`](engineering_audit_2026-08.md).

## Also see

- [`CLAUDE.md`](../CLAUDE.md) at the repo root — the primary map for AI
  agents working in this repo (topology, commands, engineering priorities,
  collaboration rules). Read that first; it links back into this tree where
  relevant.
- [`AGENTS.md`](../AGENTS.md) — the full engineering policy this repo
  follows (scientific-computing rules, GUI/UX rules, prompt templates).
