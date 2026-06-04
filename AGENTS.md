# AGENTS.md

## Project Context

This repository contains a Python scientific software suite for controlling measurements, acquiring data, and evaluating LSPR / LSPR imaging experiments.

The software should be understandable and maintainable by scientists, students, and non-IT users, while still being reliable, modular, and efficient for scientific work.

Primary goals:
- Correct scientific results and reproducible analysis.
- Simple, readable, maintainable Python code.
- Modular applications inside one coherent suite.
- Clear GUI behavior that is easy for non-technical users to understand.
- Efficient processing using appropriate scientific Python libraries.
- AI-friendly structure so future coding agents can safely modify the project.

## Engineering Priorities

Optimize in this order unless the user explicitly says otherwise:

1. Correctness and scientific validity.
2. Data integrity and reproducibility.
3. Maintainability and readability.
4. Modularity and testability.
5. Performance and memory efficiency.
6. GUI polish and user convenience.

Do not make code clever if it becomes difficult for scientists or future AI agents to understand.

## General Workflow

Before editing code:
- Inspect the existing project structure and reuse existing patterns.
- Identify the affected modules, GUI screens, data models, and tests.
- Prefer small, focused changes over large rewrites.
- If requirements are unclear, make reasonable assumptions and state them briefly.

When implementing:
- Keep changes minimal and coherent.
- Do not modify unrelated files.
- Do not remove existing functionality unless explicitly requested.
- Preserve user data, measurement data, configuration files, and calibration files.
- Add or update tests for scientific calculations, data transformations, and critical workflows.
- Keep startup popup behavior fail-closed:
  - do not call `showPopup()` during widget construction unless an explicit ready flag is set
  - default popup readiness to `False` and enable it only after the UI has finished startup wiring
  - prefer explicit startup-state propagation over `getattr(..., True)` fallbacks for combo-box popups

Before finishing:
- Run relevant tests, type checks, linting, or a small manual verification when available.
- Explain what changed, where it changed, and how to verify it.
- Mention any risks, assumptions, or parts that could not be verified.

## Python Standards

Use modern, readable Python.

Preferred practices:
- Use type hints for public functions, core data structures, and non-trivial logic.
- Use `dataclasses`, `pydantic`, or typed configuration objects where they clarify data flow.
- Prefer explicit names over short abbreviations.
- Keep functions small and focused when practical.
- Separate pure computation from I/O, hardware control, plotting, and GUI code.
- Prefer composition over inheritance unless inheritance clearly improves the design.
- Avoid global mutable state.
- Avoid hidden side effects.

Avoid:
- Large monolithic files.
- Copy-pasted logic.
- Broad `except Exception` blocks without clear handling.
- Silent failures.
- Hardcoded paths, calibration constants, or device settings hidden inside logic.
- Mixing GUI code directly with scientific calculations.

## Suggested Project Structure

When adding or reorganizing code, prefer a structure similar to this:

```text
src/
  lspr_suite/
    app/              # application startup and dependency wiring
    gui/              # windows, widgets, dialogs, view models
    instruments/      # hardware/device interfaces and drivers
    acquisition/      # measurement workflows and acquisition orchestration
    analysis/         # LSPR/LSPR imaging algorithms and data evaluation
    models/           # typed data models and domain objects
    io/               # file import/export, metadata, formats
    plotting/         # plots, image visualization, colormaps
    services/         # reusable application services
    config/           # settings, defaults, profiles
    utils/            # small generic helpers only
tests/
  unit/
  integration/
  data/               # small test fixtures only
```

This structure is guidance, not a reason to perform a large rewrite without need.

## Modular Design Rules

Design the suite as a set of cooperating modules:

- Measurement control must be separate from data analysis.
- Scientific algorithms must be usable without launching the GUI.
- GUI screens must call services or controllers, not directly manipulate hardware or raw files when avoidable.
- Instrument drivers must expose clear interfaces so real hardware can be replaced with simulated devices for testing.
- Analysis pipelines should accept explicit inputs and return explicit outputs.
- Each app inside the suite should share common models, configuration, logging, and plotting utilities.

When adding a new feature, consider whether it belongs in:
- `gui` for user interaction.
- `analysis` for scientific computation.
- `acquisition` for measurement sequence logic.
- `instruments` for hardware-specific behavior.
- `io` for reading/writing files.
- `models` for shared data structures.

## Scientific Computing Rules

Use established scientific Python tools when appropriate:

- `numpy` for numerical arrays and vectorized operations.
- `scipy` for fitting, signal processing, optimization, interpolation, and statistics.
- `pandas` or `polars` for tabular experiment metadata and exported summaries.
- `xarray` when data has labeled dimensions such as time, wavelength, x/y image coordinates, channels, or experiment conditions.
- `scikit-image` or OpenCV for image processing when needed.
- `matplotlib`, `pyqtgraph`, or similar libraries for plotting depending on GUI performance needs.
- `numba`, multiprocessing, or chunked processing only after identifying a real bottleneck.

Scientific code should:
- Preserve units and metadata.
- Clearly document assumptions, units, coordinate systems, and calibration steps.
- Avoid changing raw measured data in place.
- Keep raw data, processed data, and derived results distinguishable.
- Make algorithms reproducible from saved inputs and parameters.

## Performance Rules

Prefer simple correct code first, then optimize measured bottlenecks.

Performance guidelines:
- Use vectorized `numpy` operations instead of Python loops for large arrays.
- Avoid unnecessary copies of large images, spectra, or time series.
- Use lazy loading, memory mapping, chunking, or streaming for large datasets when appropriate.
- Keep GUI responsive by moving long-running acquisition, loading, fitting, or image-processing tasks off the main UI thread.
- Cache expensive derived results only when invalidation rules are clear.
- Add comments explaining non-obvious performance optimizations.

Do not add complex optimization techniques unless they are justified by data size, profiling, or obvious performance needs.

## GUI And UX Rules

The GUI is for scientific users who may not be programmers.

General GUI principles:
- Make workflows visible and logical: setup -> measurement -> preview -> analysis -> export.
- Use clear scientific labels, units, and status messages.
- Avoid ambiguous buttons such as `OK` when a specific action label is better.
- Validate user inputs before running measurements or analysis.
- Show understandable error messages with recovery guidance.
- Never fail silently during acquisition, saving, loading, fitting, or export.
- Keep dangerous actions explicit and reversible when possible.

When designing or modifying GUI:
- First describe the intended layout and user flow in text.
- Identify panels, controls, plots, tables, status bars, and dialogs.
- Separate GUI layout from business logic.
- Use mock data or simulated devices when useful for previewing behavior.
- Keep GUI state synchronized with underlying models.
- Avoid blocking the GUI thread.

For GUI descriptions, use this structure before coding:

```text
Screen purpose:
Main user actions:
Layout:
- Left panel:
- Center panel:
- Right panel:
- Bottom/status area:
Important states:
Validation rules:
Error messages:
```

## Data And File Handling

Measurement and analysis data are valuable and must be handled carefully.

Rules:
- Never overwrite raw data without explicit user confirmation.
- Save metadata with measured data whenever possible.
- Include timestamps, instrument settings, calibration references, software version, and analysis parameters.
- Prefer open formats where practical: CSV for simple tables, TIFF for images, HDF5/Zarr/NetCDF for multidimensional scientific data.
- Keep export code separate from analysis code.
- Validate file formats and show clear errors for incompatible files.
- Avoid hardcoded absolute paths.
- For the `sLSPR acq` runtime pipeline, follow `apps/sLSPR/acq/docs/runtime_pipeline_architecture.md` as the authoritative rule set for lossless raw acquisition, asynchronous file writing, and UI drop accounting.
- For future architecture and performance work, treat `apps/sLSPR/acq/docs/CODEX_ARCHITECTURE_RAILS_V7.md` as the controlling guide for the split between lossless acquisition/storage and lossy UI/analysis.
- For step-by-step implementation work on that split, follow `apps/sLSPR/acq/docs/CODEX_IMPLEMENTATION_GUIDE_V8_LOSSLESS_ACQ_AND_LOSSY_UI.md`.

## Error Handling And Logging

Use explicit, useful error handling.

Rules:
- Surface hardware, file, calibration, and analysis errors clearly to the user.
- Log technical details for debugging.
- Do not swallow exceptions silently.
- Avoid broad exception handlers unless they add useful context and re-raise or report the issue.
- Use domain-specific exceptions for expected scientific, hardware, or file-format failures when helpful.

## Testing Rules

Prioritize tests for:
- Scientific calculations and fitting routines.
- Data import/export.
- Calibration and unit conversion.
- Image-processing algorithms.
- Measurement workflow state transitions.
- Regression tests for fixed bugs.

Testing guidance:
- Use small deterministic fixtures.
- Avoid requiring real hardware for normal unit tests.
- Provide simulated instruments or mocks for acquisition workflows.
- Test edge cases such as empty data, saturated images, missing metadata, invalid calibration, and failed device communication.
- Numerical tests should use appropriate tolerances rather than exact equality for floating point values.

## Comments And Documentation

Write code that is readable first, then add comments where they add value.

Commenting rules:
- Explain why a non-obvious decision was made.
- Explain scientific assumptions, formulas, units, and calibration logic.
- Explain performance optimizations that make code less obvious.
- Do not add comments that merely repeat what the code says.

Documentation should include:
- How to run the suite.
- How to run tests.
- Basic measurement workflow.
- Supported data formats.
- Instrument setup assumptions.
- Known limitations.

## AI Agent Behavior

When acting as a coding agent on this repository:

- Be conservative with architecture changes.
- Prefer incremental improvements over broad rewrites.
- Ask before destructive operations or data-format migrations.
- Explain assumptions briefly.
- Keep code friendly for non-IT maintainers.
- When adding GUI elements, describe the user flow before implementation.
- When adding scientific algorithms, include units, assumptions, and tests.
- When optimizing, explain the bottleneck and why the chosen approach helps.
- If a quick fix would create technical debt, mention the cleaner alternative.

## Reusable Task Prompts

Use these prompts when asking Codex to perform common tasks.

### New Feature

```text
Analyze the current project structure first. Propose a minimal implementation plan for this feature, including which modules should change. Keep scientific computation separate from GUI code. Then implement the feature with tests where practical.

Feature:
[describe feature]

Expected user workflow:
[describe workflow]
```

### GUI Screen Or Dialog

```text
Before coding, describe the GUI design using this structure:
- Screen purpose
- Main user actions
- Layout: left/center/right/bottom areas
- Important states
- Validation rules
- Error messages

Then implement it using existing GUI patterns in the project. Keep layout code separate from measurement, analysis, and file I/O logic.

GUI request:
[describe screen]
```

### Scientific Algorithm

```text
Implement this scientific calculation as a pure, testable function or small module. Include type hints, units, assumptions, and numerical tests with tolerances. Do not mix this logic with GUI or file I/O.

Algorithm:
[describe algorithm]

Inputs:
[list inputs with units]

Outputs:
[list outputs with units]
```

### Performance Improvement

```text
Find the likely bottleneck first. Prefer simple vectorized NumPy/SciPy improvements before adding complex parallelism. Preserve scientific correctness and add/keep regression tests.

Performance problem:
[describe slow operation and data size]
```

### Refactor

```text
Refactor this area for readability and modularity without changing behavior. Search for existing patterns first. Keep the diff small, preserve public APIs if possible, and add tests if behavior is currently untested.

Area to refactor:
[describe files/modules]
```

### Bug Fix

```text
Reproduce or reason about the bug first. Identify the root cause, then make the smallest safe fix. Add a regression test if practical.

Bug:
[describe bug]

Expected behavior:
[describe expected behavior]

Actual behavior:
[describe actual behavior]
```

## Do Not Do Without Explicit Approval

- Do not rewrite the whole application architecture.
- Do not change saved data formats without migration strategy.
- Do not remove existing measurement workflows.
- Do not introduce large new dependencies without explaining why.
- Do not require real hardware for normal tests.
- Do not hide errors from users.
- Do not optimize in ways that make scientific code hard to verify.
