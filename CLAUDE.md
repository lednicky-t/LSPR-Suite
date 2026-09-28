# CLAUDE.md — LSPR Suite

This file is for AI coding agents. It describes what this repo is, how to navigate it, and what rules to follow.

See `AGENTS.md` for the full engineering policy, scientific computing rules, GUI/UX rules, and prompt templates.
This file focuses on repo topology, commands, quick-reference maps, and **how to collaborate with the maintainer**.

---
## Abbreviations

The maintainer uses shorthand in conversation. Recognize these; expand on first use in your own
writing unless the maintainer clearly knows the term already. Add new ones here as they come up.

| Abbrev | Meaning |
|--------|---------|
| CC | Chromatic correction |
| wl | Wavelength |

## Who You're Working With (read this first)

The maintainer of this project is a **scientist, not a professional software developer**.
They understand the science (LSPR / nano-optics) deeply but are still learning to code.
This changes *how you should communicate*, not what the engineering standards are.

- **Always explain *why*, in plain language.** When you suggest a change, an alternative, or
  a "better" approach, briefly say what problem it solves and why it helps — in everyday words.
  Define a technical term the first time you use it.
- **Teach as you go.** When you use a library feature, pattern, or concept the maintainer may
  not know, add a one-line "what this means" note. The aim is that they understand their own
  codebase a little better after each session.
- **Be a proactive advisor, not just a typist.** If you notice something that could be cleaner,
  safer, faster, or more correct — say so, even if it wasn't asked. Offer it as a suggestion with
  the trade-off explained, and let them decide. Don't silently change unrelated things; mention them.
- **Don't assume a subtle mistake will be caught in review.** The maintainer may not spot a wrong
  variable name or a missed edge case in a diff. Be careful, and flag anything you're unsure about.
- **Explain clearly without talking down.** Assume high intelligence and growing coding experience.

**On caution — use judgment per situation:**
- *Proceed, then show the diff and explain* for low-risk, easily reversible changes that are clearly
  within an explicit request and covered by passing tests.
- *Explain the plan and check in first* when a change touches a hard rule below, file formats / HDF5
  schemas, scientific calculations or their results, shared `packages/`, multiple submodules, public
  function signatures, or anything you're genuinely unsure about.
- *Never without explicit approval:* delete data/files, change saved data formats without a migration
  plan, rewrite git history, or commit app changes to the wrong repo (see Submodule Workflow).
- *Ask before driving a GUI app yourself* (screenshots, pywinauto, or similar automation to click
  through a Qt app and verify behavior). The maintainer can usually do this in seconds themselves,
  and it costs meaningfully more of your effort than it saves — a blind screen-coordinate click can
  also miss the target window entirely (e.g. hitting an unrelated app on another monitor) with no
  easy way to undo it. Default to static verification (read the diff, run existing tests, reason
  about the code) and offer to hand off manual/visual verification to the maintainer; only drive the
  GUI yourself if they ask you to.

When you finish, explain **what changed, where, and how to verify it**, and name which engineering
priority the change serves (see below) so the maintainer can judge it.

---

## Usage Efficiency (read this too)

The maintainer is usage-conscious and often clears the session and starts a fresh one specifically to
control how much they spend. Respect that — be economical without sacrificing correctness:

- **Don't re-run the full test suite after every small edit.** Run only the specific test file(s)
  relevant to the change while iterating on a fix; run the full suite (`pytest tests/`) once, near the
  end — right before calling the work done or before committing — not after each intermediate step.
- **Don't re-read a file you just edited or wrote** "to confirm it worked." Edit/Write already fail
  loudly if something went wrong; trust that.
- **Batch verification to the end of a task.** Run tests once, review the diff once, summarize once —
  rather than re-checking after every small change in a multi-step task.
- **Keep tool output lean.** Prefer a targeted grep/read over a broad one; don't dump a full test-suite
  log or a whole file into the conversation when a pass/fail count or a short excerpt would do.
- **Avoid spawning subagents for work you can do directly** (see Agent Routing above). An agent call
  re-derives context from scratch, which usually costs more than doing a small task inline.
- **Proactively suggest clearing the session when it makes sense** — don't wait to be asked. Good
  moments: a task is finished and about to be committed; the conversation has a lot of exploratory
  back-and-forth (failed approaches, large file dumps, long research) that the next task won't need;
  you're about to start something unrelated to what's been discussed. A short line is enough, e.g.
  "This looks done — probably a good point to `/clear` before the next task, since none of this
  investigation is needed going forward."
- **Write a short reference doc after non-trivial exploration or design work.** If a task required
  tracing a feature across many files, reverse-engineering an undocumented subsystem, or landed on a
  non-obvious design decision (a new shared widget/pattern, a multi-file wiring scheme), save a short
  summary as a markdown file in the relevant app's `docs/` folder (matching the existing
  `apps/sLSPR/acq/docs/*.md` / `apps/LSPRi/eva/docs/*.md` convention) — what was learned, key
  `file:line` pointers, and why the decision was made. A future session can then read one file instead
  of re-deriving the same context via exploration, which is the expensive path. Skip this for small,
  self-contained changes — the goal is avoiding repeated re-exploration, not documenting everything.

---

## What This Repo Is

Python scientific software suite for LSPR (Localized Surface Plasmon Resonance) measurements.
Target users: scientists and students, not IT professionals.

Four apps, three as git submodules, one (suite launcher) living directly in this repo:

| App | Path | Package | Entry point |
|-----|------|---------|-------------|
| singleLSPR Acquisition | `apps/sLSPR/acq` | `lspr_app` | `lspr-acquisition` |
| singleLSPR Evaluation | `apps/sLSPR/eva` | `lspr_single_evaluation` | `lspr-single-evaluation` |
| LSPRimaging Evaluation | `apps/LSPRi/eva` | `lspr_imaging_app` | `lspri-evaluation` |
| Suite Launcher | `apps/suite_launcher` | `suite_launcher` | `lspr-suite` |

`LSPRimaging Acquisition` is reserved for future work and does not exist yet.

---

## Shared Packages

| Package | Path | Purpose |
|---------|------|---------|
| `lspr-core` | `packages/lspr_core` | Domain models, schema identity, experiment plan steps, units |
| `lspr-io` | `packages/lspr_io` | HDF5/session file helpers, schema stamping, version readers |
| `lspr-ui` | `packages/lspr_ui` | Qt theme tokens, icon helpers, app bootstrap utilities |
| `lspr-acq-shell` | `packages/lspr_acq_shell` | Shared live-acquisition shell (fluidics device framework, experiment-control plan editing/execution, session/HDF5-writer plumbing) being extracted out of `apps/sLSPR/acq` for reuse by both acquisition apps |

Add cross-app logic here, not inside individual app packages.

**Icons**: all icons come from `packages/lspr_ui/src/lspr_ui/icon_assets/`, individually vendored
SVG files loaded via `lspr_ui.load_tabler_icon()` - not from an icon-library package dependency.
See `packages/lspr_ui/ICONS.md` before adding a new icon or a new icon dependency.

---

## Setup

`requirements.txt` installs all packages and apps in editable mode (clone with `--recurse-submodules`). Python >= 3.12 is required. On Windows, make sure `python` resolves to your system install, not the Inkscape-bundled interpreter.

Optional hardware dependency (AMF M-Switch): `python -m pip install AMFTools`. Without it, M-Switch controls in the acquisition app are disabled.

---

## Dependency Pinning (Reproducibility)

Every third-party (PyPI) dependency in a `pyproject.toml` needs both a floor and a ceiling version constraint, never a bare name - otherwise a fresh install months later can silently pull a different numpy/scipy/h5py and change results (priority #2 below). Internal `lspr-*` packages are exempt. Use the `dependency-pinning` skill when adding or bumping a dependency, or when asked to pin an app (`apps/sLSPR/eva` is still unpinned).

---

## Running Apps

Entry points are the `[project.scripts]` of each app's `pyproject.toml`; `lspr-suite` (the Suite launcher) is the recommended one. For VS Code "Run Python File", use the `run.py` file in each app directory.

The launcher supports three profiles for the acquisition app (selectable inline in the card):
- `Full` — real hardware discovery and auto-connect
- `Simulation` — skips discovery, runs in simulation mode
- `Control editor` — opens the experiment-control editor only

---

---

## Running Tests

Tests live in `tests/unit/` (pure logic, no Qt, no files) and `tests/integration/` (Qt, HDF5, device-mock, workflow). All pass without real hardware; simulated instruments replace it. Use tolerances for floating-point assertions, not exact equality.

---

## Code Quality / Maintainability Tools (advisory only)

Five dev-only tools are installed (`requirements-dev.txt`) for periodic code-health checks:
`radon`, `pytest-cov`, `vulture`, `import-linter`, `mypy`. They are **not** wired into pre-commit
or CI — running them is manual, by design.

**Rule for agents: never run these on your own initiative.** Instead, notice when a moment fits
and *say so* — name the tool and why it's relevant — then wait for the maintainer to say go ahead.
Don't run them just because a task touched code; only flag it when one of the situations below
actually applies.

Run each tool by its standard command (see `requirements-dev.txt`); `import-linter` reads `.importlinter`, and `mypy` must be run **per package/app root** - a combined multi-root run hits "Duplicate module named ..." because each app's `src/` has its own top-level `main.py`.

Moments worth flagging (suggest, don't act):
- After a refactor touching many files across one app/package → suggest `import-linter`, to confirm no layering rule broke.
- When a GUI file keeps growing, or after splitting one further → suggest `radon mi`, to see if complexity actually improved.
- Before a release, or after finishing a feature branch → suggest `pytest-cov`, to see if the new code is covered.
- After deleting/renaming code (an old alias, a legacy fallback path) → suggest `vulture`, to check nothing was left behind.
- After touching a dataclass/attribute contract shared across mixins or controllers → suggest `mypy` on that file, to catch stale type annotations like a field typed as the base Qt class instead of the actual custom subclass assigned to it.
- When the maintainer directly asks about code quality, tech debt, or maintainability.

mypy output is dominated by two structural false-positive patterns rather than real bugs: `attr-defined` from the mixin/controller-split architecture (mypy checks each mixin in isolation and doesn't see attributes defined on sibling mixins), and `union-attr` from PyQt6 stubs typing things as `X | None` even where Qt guarantees non-null. Real bugs are mixed in (e.g. a dataclass field typed as the base Qt widget class instead of a custom subclass), so triage per item - no blanket fix or suppress. `lspr_core`/`lspr_io` are kept free of GUI imports and mypy-clean.

---

## Where Code Lives

Each app's source is under `apps/<app>/src/<package>/` (see the table above); read the app's `docs/` folder before changing its architecture. Two gotchas: the main window is split across several files (see Common Pitfalls), and in LSPRimaging Evaluation prefer the relevant `*_controller.py` over adding more to `main_window.py` (~6.8k lines).

---

## Submodule Workflow

Each app repo has its own git history. Editing the files in a submodule folder is **not enough** —
think of the folder as a shortcut to a separate project. You commit in the real project first, then
tell the umbrella repo which version to use.

To change app code:

1. Edit files inside the submodule directory (`apps/sLSPR/acq`, etc.).
2. Commit and push from within that directory (it is its own repo).
3. Update the submodule pointer in this umbrella repo:
   ```powershell
   git add apps/sLSPR/acq
   git commit -m "bump sLSPR/acq submodule"
   ```

Do not commit app changes directly to the umbrella repo — commit them in the submodule first.
**Tell the maintainer which repo a change will land in before committing**, since this is a common point of confusion.

---

## Key Settings and Config Files

- `lspr_settings.json` — runtime state (window positions, UI mode). **Gitignored.** Do not commit it.
- `apps/sLSPR/eva/lspr_evaluation_settings.json` — evaluation-app UI state. Also gitignored.
- `docs/schemas/` — HDF5 format contracts (authoritative, do not change lightly).

---

## HDF5 Data Contract

Shared rules for measurement files are in `docs/schemas/hdf_standard.md`. Short version:

- Every file must carry: `schema_name`, `schema_version`, `app_name`, `app_version`, `created_at_utc`.
- Raw data is appended, never overwritten.
- Derived/processed data goes in separate groups.
- Readers must reject unknown schema names and incompatible major versions.
- Breaking changes → major version bump; additive changes → minor bump.

---

## Architecture Documents to Read Before Changing the Acquisition Pipeline

These are in `apps/sLSPR/acq/docs/` and are referenced as authoritative in `AGENTS.md`:

- `runtime_pipeline_architecture.md` — lossless raw acquisition vs lossy UI rules (read first)
- `spectral_processing_pipeline_architecture.md` — raw/dark/reference/absorbance data flow and the crop/baseline/smoothing/fit overlay contract
- `CODEX_ARCHITECTURE_RAILS_V7.md` — architecture split design
- `CODEX_IMPLEMENTATION_GUIDE_V8_LOSSLESS_ACQ_AND_LOSSY_UI.md` — step-by-step implementation
- `CODEX_RUNTIME_SIMPLICITY_GUIDE_V12.md` — anti-orchestration guidance

Core rule: **acquisition and file writing must be lossless; processing and GUI display may skip stale frames.** Separately: **processing (crop/baseline/smoothing) must never change a value at a wavelength still in view, and baseline/smoothing must only ever apply to the Absorbance spectrum** — see `spectral_processing_pipeline_architecture.md`.

---

## Engineering Priority Order

From `AGENTS.md` (do not reorder without explicit instruction). When two goals conflict, prefer the
higher one, and name which priority a change serves when you explain it:

1. Correctness and scientific validity
2. Data integrity and reproducibility
3. Maintainability and readability
4. Modularity and testability
5. Performance
6. GUI polish

---

## Performance Work and Numeric Changes

Any change that alters measured or derived scientific values needs a standalone before/after comparison against realistic data (not just unit tests), and must be flagged to the maintainer with the quantified impact, even when it is negligible. Instrument new hot paths with cheap debug-level stage timing, and verify any performance fix with a real before/after measurement. Use the `performance-work` skill for the full method and case studies.

## GUI Testability

**Prefer widgets that are directly callable, for testability.** Prefer real
`QAbstractButton` subclasses (`QPushButton`, `QToolButton`, `QCheckBox`) over
a bare `QLabel` with a hand-rolled click handler for anything clickable. A
real button's `.click()` method fires the exact same signal a mouse click
would, callable directly on the Python object with no coordinates, no
visible window, and no OS-level automation needed — this is what makes this
repo's own existing GUI test pattern possible
(`tests/integration/test_lspri_preferences_dialog.py`: a real `QApplication`
built in-process without ever calling `.exec()`, the real widget constructed
directly, driven via direct method/signal calls, never screen coordinates).
A `QLabel`-based "button" has no such method and is unreachable by
UI-automation tooling and screen readers alike.

---

## Common Pitfalls

- **Startup popup bug pattern**: do not call `showPopup()` during widget construction. Default popup readiness to `False` and enable only after startup wiring is complete. Use explicit state propagation, not `getattr(..., True)` fallbacks.
- **Main window is split across files**: `main_window.py`, `main_window_layout.py`, `main_window_lifecycle.py`, etc. Check all of them before assuming you know how a feature is wired.
- **GUI thread blocking**: long acquisition, file loading, fitting, and image processing must run off the main thread. Workers are in `gui/workers.py` (acquisition) or the thread pool (imaging).
- **Don't mix scientific code with GUI code.** Analysis functions must work without a running Qt application.
- **Raw data is sacred**: never overwrite raw measurement data. Derived results live in separate groups/files.
- **Parentless widget + `setVisible()`/`.show()`/`.hide()` before attaching it = phantom top-level window flash.** Pass the parent at construction (`QLabel(text, self)`), not just via a layout that isn't wired up yet. Full explanation and the diagnosis method: `apps/suite_launcher/CLAUDE.md`.
- **LSPRimaging-specific pitfalls** (ROI coordinates live in processed image space - mixing spaces silently gives wrong results; `image_tools_enabled` must not be persisted as off; the sample/reference ROI rename) are in `apps/LSPRi/eva/CLAUDE.md`, loaded when working under that directory.
