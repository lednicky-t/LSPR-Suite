# LSPR Suite

This workspace will host the shared ecosystem for:

- singleLSPR acquisition and evaluation
- LSPRimaging acquisition and evaluation
- shared file formats, workflow rules, and analysis primitives

The acquisition apps stay separate from the offline evaluation apps.
Shared logic lives in `packages/`.
The first shared library is `packages/lspr_core`.
Shared Qt styling and icon helpers live in `packages/lspr_ui`.
HDF and file-format rules live in `packages/lspr_io` and `docs/schemas/`.
Architecture notes live in `docs/architecture/`.
Legacy references are kept in `references/`.
The suite launcher lives in `apps/suite_launcher`.

## Local Setup

Create a virtual environment at the repo root, activate it, then install the
workspace packages in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

After that, you can launch the installed apps with `lspr-suite`,
`lspr-acquisition`, or `lspri-evaluation`.

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

