---
name: dependency-pinning
description: "Use when adding or bumping a third-party dependency in any pyproject.toml, or when asked to pin an app's dependencies (floor and ceiling version bounds for reproducibility)."
---

# Dependency Pinning (Reproducibility)

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
