# CLAUDE.md — LSPR Suite

Authoritative agent rules. Detail loads on demand: path-scoped `.claude/rules/*.md`, skills, `docs/`, each app's `CLAUDE.md`. Abbreviations: **CC** = chromatic correction, **wl** = wavelength.

## Who You're Working With

The maintainer is a **scientist, not a professional developer**: deep in LSPR and nano-optics, still learning to code. This changes communication, not standards.

- **Explain *why* in plain language**; define a technical term the first time; a one-line "what this means" for unfamiliar library features or patterns.
- **Be a proactive advisor**: flag anything cleaner, safer, faster or more correct, with the trade-off; let the maintainer decide. Don't silently change unrelated things.
- **Flag your own uncertainty**: subtle mistakes may not be caught in review.

## Match Effort to Task Size

- **Small / cosmetic** (icons, labels, colors, spacing, moving a widget): terse; no teaching notes or write-ups. Reply with file:line, which repo, what to look at. See `/quick`.
- **Normal changes**: brief "why"; end with what changed, where, how to verify.
- **Science, data formats, threading, architecture, shared packages**: full explanation, check in first, name the engineering priority served.

## Usage Efficiency

- Iterate with the relevant test file(s) only, not after every fix; run the scoped suite once near the end, before done or commit. The full suite only when `packages/` or several apps changed; otherwise the app's subset (`--pattern "test_*lspri*.py"`).
- Don't re-read a file you just edited. Lean tool output (targeted grep/read, pass/fail counts). No subagents for small inline work. Suggest `/clear` between unrelated tasks.
- After non-trivial exploration or a non-obvious decision, save a short summary (what, `file:line`, why) in the app's `docs/`.

## Repo Map

| App | Path | Package | Entry |
|-----|------|---------|-------|
| singleLSPR Acquisition | `apps/sLSPR/acq` | `lspr_app` | `lspr-acquisition` |
| singleLSPR Evaluation | `apps/sLSPR/eva` | `lspr_single_evaluation` | `lspr-single-evaluation`; placeholder, leave out of cross-app work |
| LSPRimaging Evaluation | `apps/LSPRi/eva` | `lspr_imaging_app` | `lspri-evaluation` |
| LSPRimaging Acquisition | `apps/LSPRi/acq` | `lspri_acq_app` | `lspri-acquisition`; early scaffold on `lspr-acq-shell`, launcher card disabled |
| Suite Launcher | `apps/suite_launcher` | `suite_launcher` | `lspr-suite` (in this repo) |

Shared packages (`packages/`): `lspr_core` (domain models, schema identity, plan steps, units, `perf`), `lspr_io` (HDF5/session helpers, schema stamping), `lspr_ui` (theme, icons, bootstrap), `lspr_acq_shell` (live-acquisition shell). Cross-app logic goes there. Icons: `lspr_ui.load_tabler_icon()`; read `packages/lspr_ui/ICONS.md` before adding one.

App source: `apps/<app>/src/<package>/`; read the app's `docs/` before changing its architecture. Main windows span several files: check all before assuming wiring. Setup and launcher profiles: `README.md`; VS Code runs each app's `run.py`.

## Tests and Tools

- `tests/unit/` (pure logic) and `tests/integration/` (Qt, HDF5, device mock); no hardware needed. Float assertions use tolerances.
- Run `python tools/run_tests_per_file.py` (one process per file; `--pattern "test_*lspri*.py"`; CI `--shard K/N`). Never all files in one process (`docs/test_platform_and_ci_failures_2026-10-18.md`).
- Every PyPI dependency needs floor and ceiling (`dependency-pinning` skill; internal `lspr-*` exempt).
- Never run `radon`, `pytest-cov`, `vulture`, `import-linter` or `mypy` unasked: name tool and reason, wait (`docs/code_health_tools.md`).

## Submodules

Apps (not the launcher) are submodules with their own history. Edit inside, commit and push there, then `git add apps/<app>` and commit "bump <app> submodule" here. Never commit app changes to the umbrella. **Say which repo a change lands in before committing.**

`lspr_settings.json` and `apps/sLSPR/eva/lspr_evaluation_settings.json` are gitignored state: never commit. `docs/schemas/` (HDF5 contracts) is authoritative.

## Never Without Explicit Approval

- Deleting data or files; rewriting git history; committing to the wrong repo.
- Changing a saved data format without a migration plan.
- Rewriting application architecture; removing measurement workflows; adding a large dependency without explaining why.
- Hiding errors, failing silently, or optimizing in ways that make scientific code hard to verify.
- Driving a GUI app yourself (screenshots, pywinauto); default to static checks, offer manual checking.

Also check in first for: hard rules, file formats / HDF5 schemas, scientific calculations or results, shared `packages/`, multiple submodules, public signatures, or anything you're unsure about.

## Engineering Priority Order

(`docs/engineering_policy.md`; don't reorder; name which one a change serves.) 1. Correctness and scientific validity. 2. Data integrity and reproducibility. 3. Maintainability and readability. 4. Modularity and testability. 5. Performance and memory. 6. GUI polish and convenience. No clever code that scientists or future agents can't follow.

## Common Pitfalls

- **Raw data is sacred**: never overwrite it; derived results go in separate groups or files.
- **Long work off the GUI thread** (acquisition, loading, fitting, image processing, saving, file I/O): design it off-thread from the start, never "cheaper but still on the GUI thread". Acquisition workers: `gui/workers.py`. LSPRi dataset/zarr work: `threading.Thread`, never `QThreadPool`.
- **Science out of GUI code**: analysis works without a running Qt app.
- **Parallel fan-out** takes its worker count from `lspr_core.perf` (`get_runtime().begin(...)`), never hard-coded or bare `os.cpu_count()`; identical output at any count (pinned by a test); never tune what is saved. Full rules: `performance-work` skill.
- **Numeric changes need a before/after comparison** on realistic data with quantified impact (`.claude/rules/numerics.md`). HDF5 follows `docs/schemas/hdf_standard.md`.
