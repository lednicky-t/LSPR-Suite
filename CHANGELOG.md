# Changelog

This umbrella repo doesn't keep its own changelog. The authoritative history
lives in two places:

- **Per-app changes** — each app tracks its own history in its own repo:
  - [`apps/sLSPR/acq/CHANGELOG.md`](apps/sLSPR/acq/CHANGELOG.md) — singleLSPR Acquisition
  - [`apps/LSPRi/eva/CHANGELOG.md`](apps/LSPRi/eva/CHANGELOG.md) — LSPRimaging Evaluation
  - (`apps/sLSPR/eva` and the reserved `apps/LSPRi/acq` don't have one yet)
- **Suite bundle releases** — every push to `main` that touches `apps/**`,
  `packages/**`, or `requirements.txt` is auto-tagged and published as a
  GitHub Release (see `.github/workflows/auto-release.yml`); the release
  notes are auto-generated from the commits in that release. See the
  [Releases page](https://github.com/lednicky-t/LSPR-Suite/releases) for the
  actual version history of the portable Suite bundle.
