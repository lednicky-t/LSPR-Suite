# CLAUDE.md — LSPR Suite

Repo map, commands and rules for AI coding agents. Situational detail loads on demand: path-scoped `.claude/rules/*.md` (gui, acq-runtime, numerics, hdf5, `lspri-*`), skills, `docs/`, and each app's own `CLAUDE.md`. This file is the authoritative rule set; `AGENTS.md` is a human-facing pointer.

Abbreviations: **CC** = chromatic correction, **wl** = wavelength. Expand on first use unless clearly known.

## Who You're Working With

The maintainer is a **scientist, not a professional software developer**: deep in LSPR and nano-optics, still learning to code. This changes communication, not engineering standards.

- **Explain *why* in plain language**; define a technical term the first time.
- **Teach as you go**: a one-line "what this means" for an unfamiliar library feature or pattern.
- **Be a proactive advisor**: flag anything cleaner, safer, faster or more correct, with the trade-off; let the maintainer decide. Don't silently change unrelated things.
- **Flag your own uncertainty**: subtle mistakes may not be caught in review.

## Match Effort to Task Size

- **Small / cosmetic** (icons, labels, colors, spacing, moving a widget): terse. No teaching notes, design write-up or priority label. Reply with file:line, which repo, what to look at. See `/quick`.
- **Normal code changes**: brief plain-language "why"; define a new term once; end with what changed, where, how to verify.
- **Science, data formats, threading, architecture, shared packages**: full explanation, check in first, name the engineering priority served.

## Usage Efficiency

- While iterating, run only the relevant test file(s); run the full suite once near the end, before calling work done or committing.
- Don't re-read a file you just edited. Keep tool output lean (targeted grep/read; pass/fail counts). Avoid subagents for small inline work.
- Suggest `/clear` when a task is finished or the next is unrelated.
- After non-trivial exploration or a non-obvious design decision, save a short summary (what, `file:line`, why) in the app's `docs/` folder. Skip for small changes.

## Apps

| App | Path | Package | Entry point |
|-----|------|---------|-------------|
| singleLSPR Acquisition | `apps/sLSPR/acq` | `lspr_app` | `lspr-acquisition` |
| singleLSPR Evaluation | `apps/sLSPR/eva` | `lspr_single_evaluation` | `lspr-single-evaluation` |
| LSPRimaging Evaluation | `apps/LSPRi/eva` | `lspr_imaging_app` | `lspri-evaluation` |
| LSPRimaging Acquisition | `apps/LSPRi/acq` | `lspri_acq_app` | `lspri-acquisition` |
| Suite Launcher | `apps/suite_launcher` | `suite_launcher` | `lspr-suite` |

All but the launcher are git submodules. LSPRi Acquisition is an early scaffold on `lspr-acq-shell` (launcher card disabled). **sLSPR Evaluation is a placeholder: leave it out of cross-app work** (still unpinned). App source: `apps/<app>/src/<package>/`; read the app's `docs/` before changing its architecture. Main windows are split across several files: check all before assuming how a feature is wired.

## Shared Packages

| Package | Path | Purpose |
|---------|------|---------|
| `lspr-core` | `packages/lspr_core` | Domain models, schema identity, plan steps, units; `lspr_core.perf` (machine probe, worker counts) |
| `lspr-io` | `packages/lspr_io` | HDF5/session helpers, schema stamping, version readers |
| `lspr-ui` | `packages/lspr_ui` | Qt theme tokens, icon helpers, app bootstrap |
| `lspr-acq-shell` | `packages/lspr_acq_shell` | Shared live-acquisition shell |

Cross-app logic goes here, not in app packages. Icons: `lspr_ui.load_tabler_icon()` from vendored `icon_assets/`; read `packages/lspr_ui/ICONS.md` before adding one.

## Setup, Running, Tests

- Setup, launcher profiles: `README.md`. Recommended entry point `lspr-suite`; VS Code "Run Python File" uses each app's `run.py`.
- Every PyPI dependency in a `pyproject.toml` needs a floor and a ceiling (internal `lspr-*` exempt): `dependency-pinning` skill.
- Tests: `tests/unit/` (pure logic) and `tests/integration/` (Qt, HDF5, device mock); all pass without hardware. Use tolerances for floats. Run with `python tools/run_tests_per_file.py` (one process per file; `--pattern "test_*lspri*.py"` narrows; CI shards with `--shard K/N`). Never run all files in one process. Details: `docs/test_platform_and_ci_failures_2026-10-18.md`.
- Never run `radon`, `pytest-cov`, `vulture`, `import-linter` or `mypy` on your own initiative: name the tool and reason, wait for a go-ahead (`docs/code_health_tools.md`).

## Submodule Workflow

Each app repo has its own git history; editing the folder is not enough. Edit inside the submodule, commit and push from inside it, then in this repo `git add apps/<app>` and commit "bump <app> submodule". Never commit app changes directly to the umbrella repo. **Tell the maintainer which repo a change lands in before committing.**

## Config Files

- `lspr_settings.json` and `apps/sLSPR/eva/lspr_evaluation_settings.json` are gitignored runtime/UI state: never commit.
- `docs/schemas/`: HDF5 format contracts. Authoritative; do not change lightly.

## Never Without Explicit Approval

Check in and wait for a yes before:

- Deleting data or files; rewriting git history; committing app changes to the wrong repo.
- Changing a saved data format without a migration plan.
- Rewriting application architecture.
- Removing measurement workflows; adding a large new dependency without explaining why.
- Hiding errors from users, failing silently, or optimizing in ways that make scientific code hard to verify.
- Driving a GUI app yourself (screenshots, pywinauto). Default to static checks; offer manual checking.

Also check in first when a change touches a hard rule, a file format or HDF5 schema, a scientific calculation or its results, a shared `packages/` module, multiple submodules, a public function signature, or anything you are unsure about.

## Engineering Priority Order

From `docs/engineering_policy.md`. Don't reorder. When goals conflict, prefer the higher one and name it.

1. Correctness and scientific validity.
2. Data integrity and reproducibility.
3. Maintainability and readability.
4. Modularity and testability.
5. Performance and memory efficiency.
6. GUI polish and user convenience.

Don't make code clever if it becomes hard for scientists or future agents to understand.

## Common Pitfalls

- **Raw data is sacred.** Never overwrite raw measurement data; derived results go in separate groups or files.
- **Keep long work off the GUI thread** (acquisition, loading, fitting, image processing, saving). Acquisition workers: `gui/workers.py`. In LSPRi, dataset/zarr work uses `threading.Thread`, never `QThreadPool` (see its CLAUDE.md).
- **Keep science out of GUI code.** Analysis functions must work without a running Qt application.
- **Parallel work:** a fan-out (export, scan, batch analysis, bulk reads) gets its worker count from `lspr_core.perf` (`get_runtime().begin("domain.task", ...)`), never a hard-coded number or bare `os.cpu_count()`. Output must be identical at any worker count (pinned by a test); never tune what is saved. Full rules: `performance-work` skill, `docs/performance_tuning.md`.
- **Numeric changes need a before/after comparison** on realistic data, with the quantified impact even if negligible (`.claude/rules/numerics.md`).
- **HDF5 files** follow `docs/schemas/hdf_standard.md` (`.claude/rules/hdf5.md`).
