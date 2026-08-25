# LSPR Suite

This repository is the umbrella for the LSPR application suite.

It now connects several separate app repositories through Git submodules:

- `apps/LSPRi/eva` -> LSPRimaging evaluation
- `apps/sLSPR/acq` -> singleLSPR acquisition
- `apps/sLSPR/eva` -> singleLSPR evaluation

The remaining suite-level content stays in this repository:

- shared packages in `packages/`
- suite launcher in `apps/suite_launcher`
- shared docs in `docs/`
- bootstrap and workspace files in the repo root

The suite launcher also exposes acquisition launch profiles for the singleLSPR app:

- `Full`
- `Simulation`
- `Control editor`

`LSPRimaging-Acquisition` is reserved as a separate repo for later work and is
not started yet.

Shared logic lives in the `packages/` tree:

- `packages/lspr_core` for common flow and domain primitives
- `packages/lspr_ui` for shared Qt styling and icon helpers
- `packages/lspr_io` for HDF and file-format rules
- `packages/lspr_acq_shell` for the shared live-acquisition shell (fluidics
  device framework, experiment control, session/HDF5-writer plumbing) used by
  the acquisition apps

See [`docs/README.md`](docs/README.md) for the full documentation map
(architecture notes, schema documents, decisions, and workflows).

## Local Setup

Clone the suite with submodules, then create a virtual environment at the repo
root and install the workspace packages in editable mode.

On Windows, make sure `python` points to a normal Python install, not the
Inkscape-bundled interpreter. If `python` resolves to the wrong executable,
use the full path to your system Python when creating the venv:

```powershell
git clone --recurse-submodules https://github.com/lednicky-t/LSPR-Suite.git
cd LSPR-Suite
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

After that, you can launch the installed apps with `lspr-suite`,
`lspr-acquisition`, or `lspri-evaluation`.

If you use the AMF M-Switch hardware, install the optional vendor package in
the same virtual environment:

```powershell
python -m pip install AMFTools
```

Without `AMFTools`, the M-Switch controls in the acquisition app will stay
disabled and discovery will report the backend as unavailable.

If you want to run the launcher directly from the repo instead of the installed
console script, use:

```powershell
.\.venv\Scripts\python.exe apps\suite_launcher\run.py
```

The suite launcher remembers the last app you opened and will auto-launch it
again after about 3 seconds on the next start.

### Portable Paths

The suite launcher uses repo-relative paths by default.

If you want to point it at older standalone workspaces, set one or more of
these environment variables before launching the suite:

- `LSPR_LEGACY_SINGLE_ROOT`
- `LSPR_LEGACY_EVAL_ROOT`
- `LSPR_LEGACY_IMAGING_ROOT`

The root `requirements.txt` is the one-command bootstrap for a fresh clone.
It installs the shared packages and app entry points in editable mode.

## Repository Layout

When you work in this checkout:

- edit suite-wide docs, shared packages, and the launcher in this repository
- edit app-specific code inside the corresponding submodule repositories
- commit app changes in the app repo first, then update the suite submodule
  pointer in this umbrella repo
