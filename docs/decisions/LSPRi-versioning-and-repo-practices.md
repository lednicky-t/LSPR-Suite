# Versioning and Repo Practices

This document proposes how the project should handle versioning, compatibility, and engineering culture as it grows.

## Why This Matters

The app is moving from a single-purpose circle workflow toward a broader ROI platform. That means:

- file formats will evolve
- old output files will need to remain readable for a while
- changes will be safer if they are versioned explicitly

## Recommended Version Layers

Use three different version concepts.

### 1. App version

This is the user-facing application release version.

Recommendation:

- use semantic versioning: `MAJOR.MINOR.PATCH`
- examples:
  - `0.1.0` for the first published usable series
  - `0.2.0` for feature additions that stay compatible
  - `1.0.0` once the ROI model and core file formats are stable

Rules:

- patch: bug fixes
- minor: backward-compatible features
- major: breaking format or behavior changes

### 2. Schema version

This is the version of saved data formats.

The repo already uses `profile_version` in processing profiles, so the project is already partway there.

Recommendation:

- add a schema version to each persisted format
- version separately for:
  - processing profile
  - workspace/session
  - ROI export
  - ROI import

This makes it possible to keep older files readable even when the app version changes.

### 3. Build or package version

If the project is built into installers or executables, the package/build version should mirror the app version, but it can be stored separately from the schema version.

## Compatibility Policy

Default policy:

- new versions should load older supported files
- exported files should include a schema version field
- old formats should be migrated on load when possible
- breaking changes should be rare and documented

Suggested rule:

- if a format changes in a breaking way, keep the loader for at least one major release
- if a field is renamed, support both names during a transition period

## Suggested Project Files

Add a few small repo-level files to make this easier to maintain:

- `CHANGELOG.md`
- `CONTRIBUTING.md`
- `docs/roi_editor_branch_plan.md`
- `docs/versioning_and_repo_practices.md`
- `.github/pull_request_template.md`
- optional `.github/issue_template` files later

## Proposed Coding Culture

The repo will stay healthier if we write down a few norms.

### Clarity

- use domain names that match the product, not just the implementation
- prefer `ROI` once that becomes the product concept
- keep compatibility helpers isolated from the main model

### Small, reviewable changes

- one logical feature per branch
- one format change per migration step
- keep UI renames separate from data-model rewrites when possible

### Backward compatibility discipline

- never change a file format silently
- add migration code before removing old support
- test older exported files after every format change

### Documentation discipline

- if a user-visible workflow changes, update the docs in the same branch
- if a save/load format changes, document the versioning decision in the same branch

### Testing discipline

- add tests for every importer and exporter
- add at least one compatibility test for old files
- add regression tests for any shape conversion logic

## Git and GitHub Practices

### Branch naming

Use descriptive branch names such as:

- `feature/roi-editor`
- `feature/roi-json-schema`
- `fix/spot-table-import`
- `docs/versioning-plan`

### Pull requests

Keep PRs narrow and easy to understand:

- describe the user-visible impact
- mention any compatibility risk
- list file formats touched
- note any migration steps

### Review checklist

Every PR should answer:

- does this change a data format?
- if yes, what version changes?
- can old files still load?
- does the UI text still match the code?

### Release habit

Each release should include:

- version number
- compatibility notes
- migration notes if needed
- a short changelog entry

## What I Would Do Next In This Repo

1. Add `CHANGELOG.md` and start recording user-visible changes.
2. Add a package version source such as `src/lspr_imaging_app/version.py`.
3. Add explicit schema version fields to workspace and ROI exports.
4. Add a PR template that asks about compatibility and documentation.
5. Add a `CONTRIBUTING.md` that explains branch and review expectations.
6. Add compatibility tests for old JSON files and old CSV exports.
