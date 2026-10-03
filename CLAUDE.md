# CLAUDE.md — LSPR Suite

For AI coding agents: repo map, commands, and rules. Situational detail loads on demand from `.claude/rules/`, `docs/`, and each app's own `CLAUDE.md`. `AGENTS.md` is a human-facing pointer; this file is the authoritative rule set.

## Abbreviations

The maintainer uses shorthand. Expand it on first use unless the maintainer clearly knows the term.

| Abbrev | Meaning |
|--------|---------|
| CC | Chromatic correction |
| wl | Wavelength |

## Who You're Working With

The maintainer is a **scientist, not a professional software developer**. They know LSPR and nano-optics deeply and are still learning to code. This changes how you communicate, not the engineering standards.

- **Explain *why*, in plain language.** Say what problem a suggested change solves. Define a technical term the first time you use it.
- **Teach as you go.** For a library feature or pattern they may not know, add a one-line "what this means" note.
- **Be a proactive advisor.** Flag anything cleaner, safer, faster, or more correct, with the trade-off. Let the maintainer decide. Don't silently change unrelated things.
- **Flag your own uncertainty.** Subtle mistakes may not be caught in review.

## Match Effort to Task Size

- **Small / cosmetic** (icons, labels, colors, spacing, moving a widget): be terse. No teaching notes, no GUI design write-up, no priority label. Reply with file:line, which repo, and what to look at. The maintainer checks visually. See `/quick`.
- **Normal code changes**: a brief "why" in plain language; define a new term once; end with what changed, where, and how to verify.
- **Science, data formats, threading, architecture, shared packages**: full explanation, check in first, and name the engineering priority served.

## Usage Efficiency

The maintainer is usage-conscious. Be economical without sacrificing correctness.

- While iterating, run only the relevant test file(s). Run `pytest tests/` once, near the end, before calling work done or committing.
- Don't re-read a file you just edited. Edit and Write fail loudly if something went wrong.
- Keep tool output lean: targeted grep or read; pass/fail counts, not full logs. Avoid subagents for small inline work.
- Suggest `/clear` when a task is finished or the next task is unrelated.
- After non-trivial exploration or a non-obvious design decision, save a short summary (what you learned, `file:line` pointers, why) in the app's `docs/` folder, like `apps/sLSPR/acq/docs/*.md`. Skip for small changes.

## What This Repo Is

Python scientific software suite for LSPR (Localized Surface Plasmon Resonance) measurements. Target users: scientists and students, not IT professionals.

Four apps, three as git submodules, one (suite launcher) living directly in this repo:

| App | Path | Package | Entry point |
|-----|------|---------|-------------|
| singleLSPR Acquisition | `apps/sLSPR/acq` | `lspr_app` | `lspr-acquisition` |
| singleLSPR Evaluation | `apps/sLSPR/eva` | `lspr_single_evaluation` | `lspr-single-evaluation` |
| LSPRimaging Evaluation | `apps/LSPRi/eva` | `lspr_imaging_app` | `lspri-evaluation` |
| Suite Launcher | `apps/suite_launcher` | `suite_launcher` | `lspr-suite` |

`LSPRimaging Acquisition` is reserved for future work and does not exist yet.

## Shared Packages

| Package | Path | Purpose |
|---------|------|---------|
| `lspr-core` | `packages/lspr_core` | Domain models, schema identity, plan steps, units |
| `lspr-io` | `packages/lspr_io` | HDF5/session helpers, schema stamping, version readers |
| `lspr-ui` | `packages/lspr_ui` | Qt theme tokens, icon helpers, app bootstrap |
| `lspr-acq-shell` | `packages/lspr_acq_shell` | Shared live-acquisition shell, extracted from `apps/sLSPR/acq` |

Add cross-app logic here, not inside app packages. Icons come from `packages/lspr_ui/src/lspr_ui/icon_assets/` via `lspr_ui.load_tabler_icon()`; read `packages/lspr_ui/ICONS.md` before adding one.

## Setup and Running

- Setup (Python 3.12+, editable install, optional `AMFTools`), launcher profiles (`Full`, `Simulation`, `Control editor`): `README.md`.
- Recommended entry point: `lspr-suite`. For VS Code "Run Python File", use the `run.py` in each app directory.
- **LSPRi:** `run.py` starts the old app, not the rewrite. Read `apps/LSPRi/eva/CLAUDE.md` first.

## Dependency Pinning

Every PyPI dependency in a `pyproject.toml` needs a floor and a ceiling. Internal `lspr-*` packages are exempt. Use the `dependency-pinning` skill. `apps/sLSPR/eva` is still unpinned.

## Running Tests

`tests/unit/` (pure logic, no Qt, no files) and `tests/integration/` (Qt, HDF5, device mock, workflow). All pass without hardware; simulated instruments replace it. Use tolerances for floating-point assertions.

## Code-Quality Tools (advisory)

Never run `radon`, `pytest-cov`, `vulture`, `import-linter`, or `mypy` on your own initiative. When a moment fits, name the tool and the reason, then wait for a go-ahead. Details: `docs/code_health_tools.md`.

## Where Code Lives

- App source: `apps/<app>/src/<package>/`. Read the app's `docs/` before changing its architecture.
- The main window is split across several files. Check all of them before assuming how a feature is wired.
- **LSPRi:** GUI work targets the new generation (`panels/`, `roi/`, `analysis/`). The old `gui/main_window.py` is very large; prefer its `*_controller.py` files if you must touch it.

## Submodule Workflow

Each app repo has its own git history. Editing a submodule folder is **not enough**: it is a shortcut to a separate project. To change app code:

1. Edit files inside the submodule directory (`apps/sLSPR/acq`, etc.).
2. Commit and push from inside that directory.
3. Update the pointer in this repo: `git add apps/sLSPR/acq`, then `git commit -m "bump sLSPR/acq submodule"`.

Never commit app changes directly to the umbrella repo. **Tell the maintainer which repo a change will land in before committing.**

## Key Settings and Config Files

- `lspr_settings.json` (runtime state) and `apps/sLSPR/eva/lspr_evaluation_settings.json` (UI state): **gitignored**. Do not commit them.
- `docs/schemas/`: HDF5 format contracts. Authoritative; do not change lightly.

## Never Without Explicit Approval

Check in and wait for a yes before:

- Deleting data or files; rewriting git history; committing app changes to the wrong repo.
- Changing a saved data format without a migration plan.
- Rewriting application architecture (the LSPRi rewrite is an approved exception, documented in `apps/LSPRi/eva/CLAUDE.md`).
- Removing existing measurement workflows; adding a large new dependency without explaining why.
- Hiding errors from users, failing silently, or optimizing in ways that make scientific code hard to verify.
- Driving a GUI app yourself (screenshots, pywinauto). Default to static checks; offer manual checking.

Also check in first when a change touches a hard rule, a file format or HDF5 schema, a scientific calculation or its results, a shared `packages/` module, multiple submodules, a public function signature, or anything you are unsure about.

## Engineering Priority Order

From `docs/engineering_policy.md`. Do not reorder without explicit instruction. When goals conflict, prefer the higher one and name which priority a change serves.

1. Correctness and scientific validity.
2. Data integrity and reproducibility.
3. Maintainability and readability.
4. Modularity and testability.
5. Performance and memory efficiency.
6. GUI polish and user convenience.

Don't make code clever if it becomes hard for scientists or future agents to understand.

## Common Pitfalls

- **Raw data is sacred.** Never overwrite raw measurement data. Derived results go in separate groups or files.
- **Keep long work off the GUI thread.** Acquisition, loading, fitting, and image processing run off the main thread (acquisition workers: `gui/workers.py`). In LSPRi, anything touching the dataset or zarr layer uses `threading.Thread`, never `QThreadPool` (one HDF5-only write exception; see `apps/LSPRi/eva/CLAUDE.md`).
- **Keep science out of GUI code.** Analysis functions must work without a running Qt application.
- **Numeric changes need a before/after comparison** on realistic data, flagged with the quantified impact even if negligible. See `.claude/rules/numerics.md`.
- **HDF5 files** follow `docs/schemas/hdf_standard.md`. See `.claude/rules/hdf5.md` when touching storage code.

Path-scoped rules, loaded when you touch matching files: `.claude/rules/gui.md`, `acq-runtime.md`, `numerics.md`, `hdf5.md`.
