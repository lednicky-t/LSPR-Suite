# CLAUDE.md — LSPR Suite

This file is for AI coding agents. It describes what this repo is, how to navigate it, and what rules to follow.

See `AGENTS.md` for the full engineering policy, scientific computing rules, GUI/UX rules, and prompt templates.
This file focuses on repo topology, commands, quick-reference maps, and **how to collaborate with the maintainer**.

---
## Agent Routing
- For file reading, exploration, grep tasks → use the `explorer` agent (haiku)
- For code review → use the `code-reviewer` agent (haiku)
- For complex architecture decisions → handle in main session (opus/sonnet)
- For writing tests → use the `test-writer` agent (haiku)

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

```powershell
git clone --recurse-submodules https://github.com/lednicky-t/LSPR-Suite.git
cd LSPR-Suite
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` installs all three packages and all four apps in editable mode.

Python ≥ 3.12 is required. On Windows, make sure `python` resolves to your system install, not the Inkscape-bundled interpreter.

Optional hardware dependency (AMF M-Switch):
```powershell
python -m pip install AMFTools
```
Without it, M-Switch controls in the acquisition app are disabled.

---

## Dependency Pinning (Reproducibility)

Every third-party (PyPI) dependency in a `pyproject.toml` should carry both a
**floor** and a **ceiling** version constraint — never a bare name like
`"numpy"` with no version at all. This is priority #2 in the Engineering
Priority Order below (data integrity and reproducibility): with no
constraint, `pip install` always resolves to "whatever is newest today," so a
`git clone` + fresh install six months from now can silently pull a different
`numpy`/`scipy`/`h5py` than what a result was actually produced with — same
code, different numbers, no error and no diff to review.

**How to choose the bounds** (see `packages/lspr_io/pyproject.toml` or
`apps/LSPRi/eva/pyproject.toml` for worked examples):

1. Find the currently installed, actually-tested version:
   `.venv\Scripts\python.exe -m pip show <package>` (or
   `pip list --format=freeze | findstr <package>`).
2. Floor = that version, truncated to `major.minor` (e.g. installed `3.16.0`
   → `>=3.16`). Don't leave the floor open-ended — an old, never-tested
   version satisfying `>=1` is just as much a reproducibility gap as no
   floor at all.
3. Ceiling = the next boundary where the library is allowed to break
   compatibility:
   - Normal semver library at `major >= 1` (numpy, scipy, h5py, matplotlib,
     PyQt6, Pillow, pyserial, PyYAML, ...) → next major version
     (`>=3.16,<4`).
   - **Pre-1.0 library** (`0.x` — pyqtgraph, ome-zarr, numcodecs,
     scikit-image, ...): treat any minor bump as potentially breaking →
     ceiling is the next *minor*, not next major (`>=0.14,<0.15`).
   - **Calendar-versioned library** (imagecodecs, tifffile — versioned
     `YYYY.M.D`, not semver) → ceiling is next calendar year (`>=2026.6.6,<2027`).
4. Internal `lspr-*` packages (`lspr-core`, `lspr-io`, `lspr-ui`,
   `lspr-acq-shell`) are exempt — they're path-installed from this same
   monorepo via `requirements.txt`, not resolved from PyPI, so there's no
   "silently picks a newer version" risk. Keep their existing
   `lspr-x>=0.1.0` floor-only style.

**When adding a new dependency**: pin it at add-time using the rule above —
don't leave it open "to fix later." **When bumping an existing pin**
deliberately (e.g. to pick up a fix, the way `PyQt6-sip>=13.12.0` was pinned
to dodge a known crash): move both floor and ceiling together, re-run the
affected app's tests, and leave a one-line comment explaining *why* if the
bump was forced by something specific (a bug, a security fix) rather than
routine — matching the existing `PyQt6-sip` comment in this repo.

**Current status (2026-08)**: applied to `packages/lspr_core`, `lspr_io`,
`lspr_ui`, `lspr_acq_shell`, `apps/sLSPR/acq`, and `apps/LSPRi/eva` — the two
actively-developed apps plus everything they depend on. **Not yet applied**
to `apps/sLSPR/eva` (still has several unpinned deps); pin it the same way
next time that app gets non-trivial attention. There is no monorepo-wide
lockfile (`requirements.txt` only installs the editable local packages) —
range-pinning in each `pyproject.toml` is the current mechanism. A lockfile
would be a further step, not yet needed.

---

## Running Apps

```powershell
lspr-suite                  # Suite launcher (recommended entry point)
lspr-acquisition            # singleLSPR acquisition directly
lspr-single-evaluation      # singleLSPR evaluation directly
lspri-evaluation            # LSPRimaging evaluation directly
```

For VS Code "Run Python File", use the `run.py` file in each app directory.

The launcher supports three profiles for the acquisition app (selectable inline in the card):
- `Full` — real hardware discovery and auto-connect
- `Simulation` — skips discovery, runs in simulation mode
- `Control editor` — opens the experiment-control editor only

---

## Running Tests

Tests live in `tests/` at the repo root, split into two subdirectories.

```powershell
python -m pytest tests/              # run everything
python -m pytest tests/unit/        # pure-logic tests only (fast, no Qt, no files)
python -m pytest tests/integration/ # Qt, HDF5, device-mock, and workflow tests
```

All tests pass without real hardware. Simulated instruments replace hardware for normal test runs.
Use tolerances for floating-point assertions, not exact equality.

---

## Code Quality / Maintainability Tools (advisory only)

Five dev-only tools are installed (`requirements-dev.txt`) for periodic code-health checks:
`radon`, `pytest-cov`, `vulture`, `import-linter`, `mypy`. They are **not** wired into pre-commit
or CI — running them is manual, by design.

**Rule for agents: never run these on your own initiative.** Instead, notice when a moment fits
and *say so* — name the tool and why it's relevant — then wait for the maintainer to say go ahead.
Don't run them just because a task touched code; only flag it when one of the situations below
actually applies.

| Tool | Checks | Command |
|------|--------|---------|
| `radon` | Cyclomatic complexity + maintainability index per file | `radon mi packages apps --min B` (files below A grade); `radon cc packages apps -s -a` (complexity detail) |
| `pytest-cov` | Test coverage % | `pytest tests/unit --cov=packages --cov=apps --cov-report=term-missing` |
| `vulture` | Dead code (unused functions/vars/imports) | `vulture packages apps --min-confidence 80` |
| `import-linter` | Enforces the GUI/science-code separation rule (see Engineering Priority Order) as an automated check, config at `.importlinter` | `lint-imports --config .importlinter` |
| `mypy` | Static type checking, run **per package/app root** (a combined multi-root run hits "Duplicate module named ..." because each app's `src/` has its own top-level `main.py`) | `mypy packages/lspr_core/src --ignore-missing-imports` (repeat per package/app dir) |

Moments worth flagging (suggest, don't act):
- After a refactor touching many files across one app/package → suggest `import-linter`, to confirm no layering rule broke.
- When a GUI file keeps growing, or after splitting one further → suggest `radon mi`, to see if complexity actually improved.
- Before a release, or after finishing a feature branch → suggest `pytest-cov`, to see if the new code is covered.
- After deleting/renaming code (an old alias, a legacy fallback path) → suggest `vulture`, to check nothing was left behind.
- After touching a dataclass/attribute contract shared across mixins or controllers → suggest `mypy` on that file, to catch stale type annotations like a field typed as the base Qt class instead of the actual custom subclass assigned to it.
- When the maintainer directly asks about code quality, tech debt, or maintainability.

Baseline as of 2026-08-23: unit-test coverage ~25%; `lspr_core`/`lspr_io` clean of GUI imports;
`gui/main_window_*.py`-style files trend toward C-grade complexity (known, tracked via the
existing split-file pattern, see Common Pitfalls). mypy baseline: `lspr_core`/`lspr_io` clean
(0 errors), ~1,312 errors suite-wide, dominated (~66%) by two structural false-positive patterns
rather than real bugs — (a) `attr-defined` from the mixin/controller-split architecture, where
mypy checks each mixin class in isolation and doesn't know about attributes defined on sibling
mixins composed into the same window at runtime, and (b) `union-attr` from PyQt6 stubs typing
things as `X | None` conservatively even where Qt guarantees non-null in normal use. Real bugs are
mixed in (e.g. a dataclass field mistyped as the base Qt widget class instead of a custom
subclass with extra methods) — mypy output needs per-item triage, not a blanket fix or suppress.

---

## Where Code Lives

### singleLSPR Acquisition (`apps/sLSPR/acq/src/lspr_app/`)

```
app.py            — entry point
gui/              — all Qt windows, widgets, dialogs
  main_window*.py — main window split into lifecycle, layout, plotting, etc.
  experiment_control_*.py — experiment control subsystem
  workers.py      — background acquisition workers
device/           — hardware interfaces (Ocean spectrometer, Arduino, Reglo ICC, AMF)
  base.py         — device interface ABC
  simulated.py    — simulated spectrometer for tests/simulation mode
  ocean.py        — Ocean Insight seabreeze backend
domain/           — typed data models (measurement, pump plan, session)
storage/          — HDF5 recording and async file writing
diagnostics.py    — runtime diagnostics and probe
```

### singleLSPR Evaluation (`apps/sLSPR/eva/src/lspr_single_evaluation/`)

```
app.py            — entry point
gui/              — Qt windows
analysis.py       — peak position, centroid, FWHM computations
processing.py     — spectrum processing pipeline
models.py         — data models
io.py             — HDF5 / pump-plan file loading
```

### LSPRimaging Evaluation (`apps/LSPRi/eva/src/lspr_imaging_app/`)

```
app.py            — entry point
gui/              — Qt windows and controllers
  main_window.py  — central window (~6.8k lines); delegates to controllers below
  *_controller.py — dedicated controllers for dataset, image, analysis, ROI, etc.
domain/           — models (ROI, image stack)
processing/       — image analysis algorithms (ROI, chromatic, spot detection)
io/               — TIFF / OME-Zarr loading, format versioning
storage/          — session state persistence (processing profile JSON)
```

When adding GUI behavior here, prefer the relevant **controller** over adding more to `main_window.py`.

### Suite Launcher (`apps/suite_launcher/src/suite_launcher/`)

```
app.py            — entry point, Qt window with four app cards
targets.py        — app path resolution (workspace vs legacy paths)
version.py
```

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

## Common Pitfalls

- **Startup popup bug pattern**: do not call `showPopup()` during widget construction. Default popup readiness to `False` and enable only after startup wiring is complete. Use explicit state propagation, not `getattr(..., True)` fallbacks.
- **Parentless widget + `setVisible()`/`.hide()`/`.show()` before it's attached = phantom top-level window flash.** Building a widget with no parent (`QLabel(text)`) and then calling any visibility method on it *before it's really attached to a parent widget* makes Qt briefly realize it as a genuine OS-level top-level window — default ~640×480 geometry, actually shown/composited by Windows for a frame or more — not just a property set on an inert object. The trap: `layout.addWidget(...)` does **not** by itself fix this if `layout` is a bare `QVBoxLayout()`/`QHBoxLayout()` that hasn't itself been attached to a real parent yet (common when building up nested layouts before doing `outer_layout.addLayout(inner_layout)` at the end of a constructor) — the widget only gets a real parent once that final attachment happens, so anything set on it *before* that point sees `parent() is None`. Fix: pass the parent directly at construction (`QLabel(text, self)`), not just via a layout that isn't wired up yet. Found in `suite_launcher`'s `LaunchCard` in 2026-08 (a version-number `QLabel` calling `setVisible()` before attachment flashed a small window on every startup) after ~6 rounds of screen-recording analysis failed to pin it down — what actually found it in minutes was installing an event filter that flags top-level Show/Hide/Polish events on parentless widgets (mirroring `StartupSuspiciousWidgetTracer` in `apps/sLSPR/acq/src/lspr_app/app.py`, which exists for exactly this recurring class of bug). Prefer that instrumentation over guessing from a recording next time this pattern shows up — see `apps/suite_launcher/docs/startup_flicker_investigation.md` for the full investigation writeup.
- **Main window is split across files**: `main_window.py`, `main_window_layout.py`, `main_window_lifecycle.py`, etc. Check all of them before assuming you know how a feature is wired.
- **GUI thread blocking**: long acquisition, file loading, fitting, and image processing must run off the main thread. Workers are in `gui/workers.py` (acquisition) or the thread pool (imaging).
- **Don't mix scientific code with GUI code.** Analysis functions must work without a running Qt application.
- **Raw data is sacred**: never overwrite raw measurement data. Derived results live in separate groups/files.
- **`spot`/`ring` → sample ROI / reference ROI rename is done** (LSPRimaging, 2026-08). Code identifiers now use `sample_*`/`reference_*`/`AreaRoi`/`AreaRoiGroup`/`AreaRoiDetectionSettings` throughout `processing/` and `gui/`; the old `DetectedSpot`/`SpotGroup`/`SpotDetectionSettings` aliases were removed. `processing/spot_detection.py` is now `processing/roi_detection.py` (`detect_rois`, not `detect_spots`). Persisted JSON files from before the rename still load via legacy-key fallbacks in `storage/workspace.py`. Two things intentionally still say "spot": the unrelated `RoiDefinition` rectangle-stamp annotation tool, and the chromatic-correction "Spots" landmark-tracking option (`detect_regional_spot_landmarks`/`track_spot_landmarks`, `spot_radius_px`/`spot_mode`) — a different feature (which kind of blob to track for image registration), not the sample/reference ROI pair. The bigger Template/Placement/Pair model described in `apps/LSPRi/eva/docs/roi_implementation_direction.md` is still future work; this was the terminology-only Phase 1.
- **`image_tools_enabled` preview flag** (LSPRimaging): toggled off while the crop/rotate tool is active so the full image shows; it must not be *persisted* as off, or crops silently won't re-apply on reload.
- **ROI coordinates are in processed image space** (after rotation/flip/crop). Mixing coordinate spaces produces silently wrong results — be explicit about which space you're in.
