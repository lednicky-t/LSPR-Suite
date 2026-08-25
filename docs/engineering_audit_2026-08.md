# Engineering Audit — 2026-08

A gap analysis of testing, CI, dependency pinning, and documentation across the
umbrella repo, its four apps, and the shared `packages/` libraries. Static
review only — no code, tests, or config were changed while producing it.
Scope was infrastructure/process, not app features (app-specific backlogs
already live in each app's own `TODO.md`/`CHANGELOG.md`).

An interactive version with severity color-coding is available as a Claude
artifact; this file is the durable, repo-tracked copy.

## 1. Testing gaps that touch correctness

Priority order in `CLAUDE.md`/`AGENTS.md` puts correctness and scientific
validity first, data integrity second. These are the three places where that
priority isn't backed by a test suite yet.

### [critical] singleLSPR Evaluation's scientific core has effectively zero test coverage

`apps/sLSPR/eva` has no `tests/` folder of its own. In the root `tests/`
suite, the only trace of it is a stale
`test_eva_measurement_control.cpython-314.pyc` in `tests/__pycache__` — the
compiled cache of a test file that no longer exists on disk. Searching every
current test file for `lspr_single_evaluation` (the app's package name) turns
up nothing. That means `analysis.py` (peak position, centroid, FWHM) and
`processing.py` (the spectrum processing pipeline) run with no automated
check that their numbers are right.

**Why it matters:** a test is a recorded promise about what a known input
should produce. Without one, a future refactor (or an AI agent's "obvious"
cleanup) can silently shift a peak-fitting result by a few nanometers and
nothing will flag it — the exact failure mode the priority order ranks above
everything else.

### [critical] The new shared acquisition package has no tests behind it

`packages/lspr_acq_shell` — built up by extracting working device/experiment
control code out of `apps/sLSPR/acq` — has no test files of its own, and it
isn't mentioned in `CLAUDE.md`'s package table at all (only `lspr_core`,
`lspr_io`, `lspr_ui` are listed there).

**Why it matters:** extraction refactors are exactly the kind of change that
silently breaks a subtle behavior (an off-by-one in retry logic, a lost lock,
a changed default) — the code "still runs" but no longer does quite the same
thing. This package will eventually be shared by two acquisition apps instead
of one, so a bug introduced now gets a second blast radius once LSPRimaging
Acquisition exists. Separately, since it's missing from `CLAUDE.md`'s map, a
future session may not know it exists at all when looking for "where does
shared acquisition logic live."

### [moderate] The shared domain/IO/UI packages have no dedicated test files

`packages/lspr_core`, `packages/lspr_io`, and `packages/lspr_ui` have no
`tests/` directories of their own. They get some indirect coverage from the
root suite (e.g. `tests/unit/test_core.py` imports from `lspr_core`), but
there's no way to tell from the repo layout how much of each package is
actually exercised, and running `lspr_core`'s tests requires pulling in the
whole umbrella test suite rather than testing the package in isolation.

**Why it matters:** these three packages are the foundation every app builds
on — a bug here propagates to all four apps at once. `pytest-cov` is already
installed (`requirements-dev.txt`); running it once
(`pytest tests/unit --cov=packages --cov-report=term-missing`) would turn
"unclear how much is covered" into an actual number worth tracking.

## 2. Reproducibility: dependencies aren't pinned

The one that most directly threatens the #2 priority (data integrity and
reproducibility), because it's invisible until it isn't.

> **Status update (2026-08-26): addressed for the active apps.** Every
> dependency in `packages/lspr_core`, `lspr_io`, `lspr_ui`, `lspr_acq_shell`,
> `apps/sLSPR/acq`, and `apps/LSPRi/eva` now has a floor + ceiling constraint
> in its `pyproject.toml`, chosen from the actually-installed/tested version.
> The policy for choosing and maintaining these bounds is now documented in
> `CLAUDE.md` under "Dependency Pinning (Reproducibility)". **Still open:**
> `apps/sLSPR/eva` was left unpinned (out of scope for this pass — pin it the
> same way next time it gets attention), and there's still no monorepo-wide
> lockfile — see the original finding below for the reasoning.

### [critical] Almost every scientific dependency has no version floor or ceiling

Across every `pyproject.toml` in the suite, the numerically load-bearing
packages are declared with no version constraint at all: `numpy`, `scipy`,
`h5py`, `matplotlib`, `pyqtgraph`, `Pillow`, `ome-zarr`, `numcodecs`,
`imagecodecs`, `PyYAML`, `pyserial`. Only a handful of things are pinned at
all — `PyQt6-sip>=13.12.0` (deliberately, to dodge a known crash),
`pydantic>=2`, and internal `lspr-*>=0.1.0` packages. There's also no
lockfile anywhere in the repo (no `requirements-lock.txt`, no `uv.lock`, no
pip-freeze snapshot) — `requirements.txt` only lists the editable local
packages.

**Why it matters:** `pip install` always resolves to the newest version
available that satisfies the constraint — with no constraint, that's
"whatever is newest today." A `scipy` or `numpy` minor release six months
from now can change floating-point behavior in a fitting routine or a
default algorithm parameter, and a fresh `git clone` +
`pip install -r requirements.txt` could silently reproduce a different
number than what you got today, with no error and no diff to review.
Pinning (or at minimum floor-pinning, e.g. `numpy>=1.26,<2`) plus an
occasional lockfile snapshot turns "the environment happens to match" into
"the environment is guaranteed to match."

## 3. CI/CD: checks that exist locally but aren't enforced

Real tooling already exists — `pre-commit`, `ruff`, `mypy`, `import-linter`,
`radon`, `vulture`. The gap is that almost none of it runs automatically; it
only runs if a human remembers to.

### [moderate] CI runs tests but no lint, type, or layering checks

`.github/workflows/tests.yml` runs `python -m unittest discover -s tests` on
every push/PR — that's it. `.pre-commit-config.yaml` (ruff, pyflakes, the
standard hygiene hooks) only runs if a contributor has locally run
`pre-commit install`; `import-linter` (which enforces the GUI/science-code
separation rule) and `mypy` are documented as "advisory, run manually" and
confirmed to never run in CI.

**Why it matters:** "manual, by design" is a fine choice for the heavier
tools like `radon`/`vulture`/`mypy` that need human triage. But `ruff` and
the layering check (`import-linter`) are both fast, binary pass/fail checks
with no judgment call involved — the kind of thing CI exists for. Right now,
a PR could merge with a science-module importing PyQt and nothing would
catch it automatically; `import-linter` would only catch it the next time
someone happens to run it by hand. Adding a second CI job that runs
`ruff check` + `lint-imports` (both take seconds) closes that gap without
touching the "advisory" status of the heavier tools.

### [nice to have] Submodule repos have no test workflow of their own

`apps/sLSPR/acq`, `apps/sLSPR/eva`, and `apps/LSPRi/eva` each have their own
`.github/workflows/`, but it's only `release.yml` in every case — the actual
test suite lives centrally in the umbrella repo's `tests/`, keyed off
whatever commit the submodule pointer happens to reference.

**Why it matters:** this is a deliberate, documented structure (per
`CONTRIBUTING.md`) and not necessarily wrong for a solo-maintainer project —
but a PR opened directly against one of the app repos gets no automated test
signal at all until someone bumps the umbrella pointer and CI runs there.
Worth knowing about even if the current structure stays.

### [nice to have] No coverage tracking over time

`pytest-cov` is installed and documented as manual/advisory, which fits the
usage-efficiency philosophy. There's just no record anywhere of what the
number is or whether it's trending — the closest thing is the "~25% as of
2026-08-23" baseline noted by hand in `CLAUDE.md`.

**Why it matters:** not a case for gating merges on a coverage percentage —
that fights the stated preference to keep CI lean. But a hand-typed number
goes stale the moment it's written. A single scheduled (e.g. weekly) CI job
that runs `pytest --cov` and writes the result to a job summary would keep
that baseline honest for free, without turning it into a merge blocker.

## 4. Documentation & project hygiene

> **Status update (2026-08-26): deeper pass completed for the active repos**
> (umbrella + `apps/sLSPR/acq` + `apps/LSPRi/eva`). Beyond the items below,
> a closer read turned up doc drift that was actively misleading rather than
> just missing:
> - Root `README.md` pointed at a `references/` folder that doesn't exist —
>   removed, and the package list there (and in `CLAUDE.md`) now includes
>   `packages/lspr_acq_shell`, which was missing from both.
> - Root `CHANGELOG.md` had drifted from the actual auto-tagged release
>   history (the per-app `CHANGELOG.md`s are properly maintained; the root
>   one wasn't) — retired in favor of pointing at the per-app logs and the
>   GitHub Releases page.
> - **16 tracked files carried a stray UTF-8 BOM** (`apps/sLSPR/acq/docs/*`,
>   several `docs/architecture/**`, `packages/lspr_core/README.md`, one test
>   file) — a recurring problem (`sLSPR/acq`'s own git log already shows one
>   prior manual fix for this). Stripped, and added the `fix-byte-order-marker`
>   pre-commit hook (already available from the `pre-commit-hooks` repo
>   already in use here) plus a root `.editorconfig` in all three repos so it
>   doesn't come back.
> - `packages/lspr_ui/README.md` was 3 lines with no usage example and no
>   link to its own `ICONS.md` — expanded.
> - Added `docs/README.md` as an index for the `docs/` tree.
>
> **Still open:** LICENSE rollout (below) — holding on citation-details input
> before adding `CITATION.cff` alongside it. README badges and
> Dependabot/Renovate remain deferred, unchanged from the original pass.

### [moderate] No LICENSE at the umbrella-repo level

`apps/sLSPR/acq` has its own MIT `LICENSE` file, but the umbrella
`LSPR-Suite` repo itself — and the other three app repos and all three
packages — have none.

**Why it matters:** without a LICENSE file, the legal default is "all rights
reserved" — nobody (including a collaborator, or future work on a different
machine/account) has clear permission to use, modify, or redistribute the
code, regardless of intent. If the sLSPR/acq MIT choice reflects the intent
suite-wide, copying that file to the other repos is a five-minute fix that
removes real ambiguity.

### [nice to have] No README badges or at-a-glance CI/health status

The root `README.md` has no build-status, coverage, or version badges —
nothing that shows, without clicking into Actions, whether the last push's
tests passed. Low priority for a solo project, but a single shields.io badge
wired to `tests.yml`'s status is a near-zero-cost way to notice a broken
build at a glance.

### [nice to have] No dependency-update automation (Dependabot / Renovate)

No `.github/dependabot.yml` or Renovate config anywhere. Pairs with the
pinning issue above — once dependencies *are* pinned, something needs to
periodically propose bumping them, or they calcify into a risky all-at-once
upgrade years later instead of small, reviewable ones. Treat as a "later"
item, after pinning is in place — an automated bump PR against unpinned
deps doesn't add much.

## 5. Other classic tooling, lower priority

| Tool | Status | Worth it here? |
|---|---|---|
| `SECURITY.md` | Missing | Low — no network-facing service; skip unless accepting outside contributions. |
| GitHub issue templates | Missing (PR template exists) | Low — mainly valuable once other people file issues. |
| `py.typed` markers in packages | Missing | Skip — only matters for external installs; everything here is editable-installed from source, so `mypy` already sees real types. |
| CODEOWNERS | Missing | Skip — meaningless for a single maintainer; revisit if a collaborator joins. |

## What's already solid

Worth naming explicitly, since a report that only lists problems undersells
the baseline:

- **The advisory tool stack is genuinely good.** `ruff`, `pyflakes`, `mypy`,
  `radon`, `vulture`, and `import-linter` are all installed, documented, and
  have recorded baselines (mypy's ~1,312-error breakdown even distinguishes
  real bugs from structural false positives) — most solo-maintainer projects
  have none of this.
- **The umbrella `tests/` suite is substantial.** 150 test files split
  cleanly into `unit/` (no Qt, no files) and `integration/` (Qt, HDF5, device
  mocks) — the acquisition and LSPRimaging-evaluation apps in particular are
  well covered.
- **Architecture documentation is unusually thorough.** The
  `runtime_pipeline_architecture.md` / `spectral_processing_pipeline_architecture.md`
  / ADR-style docs in `docs/decisions/` are exactly the kind of "why," not
  just "what," documentation most codebases lack entirely.
- **The HDF5 data contract is explicit and versioned** — schema
  name/version stamping, reject-unknown-schema readers, major/minor bump
  rules. The right instinct for scientific data longevity, and most labs
  never formalize it.

## If you only do three things

| Priority | Action | Effort | Status |
|---|---|---|---|
| 1 | Write a first test file for `lspr_single_evaluation/analysis.py` (peak position, centroid, FWHM against known synthetic spectra) | Medium | Open |
| 2 | Floor-pin `numpy`/`scipy`/`h5py` in every `pyproject.toml` — no lockfile needed to start, just constraints | Small | **Done 2026-08-26** for `lspr_core`/`lspr_io`/`lspr_ui`/`lspr_acq_shell`/`sLSPR-acq`/`LSPRi-eva`; `sLSPR-eva` still open |
| 3 | Add a second CI job to `tests.yml` that runs `ruff check` and `lint-imports --config .importlinter` | Small | Open |
