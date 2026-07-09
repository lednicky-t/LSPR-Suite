# Format Versioning

This document defines how file and schema versions should be handled in LSPRimaging.

The goal is simple:

- keep the app manageable as it grows
- preserve backward compatibility where practical
- make breaking changes explicit and reviewable

## Principles

- Every persisted format should carry a version field.
- Versions should be checked on load, not guessed from shape alone.
- New versions should try to read older supported files.
- Breaking changes should be rare and documented.
- Compatibility code should live near the importer or migration layer, not spread through the UI.

## Version Layers

Use separate versions for different concerns.

### App Version

This is the user-facing release version.

Recommended style:

- semantic versioning: `MAJOR.MINOR.PATCH`
- examples:
  - `0.1.0` initial usable release
  - `0.2.0` backward-compatible feature release
  - `1.0.0` stable format milestone

Rules:

- patch: bug fix only
- minor: compatible feature or non-breaking workflow change
- major: breaking file format or behavior change

### Schema Version

This is the version of a specific saved data model.

Examples:

- workspace/session state
- processing profile
- ROI export
- CSV summary export, if we want to preserve stable behavior

Each format should own its own `schema_version` or equivalent.

### File Format Version

Some exports may need an explicit `format_version` in addition to schema version if they are meant for external exchange.

Use this when:

- the same logical data may be written in multiple encodings
- a format has a human-friendly summary variant and a full-fidelity variant
- a file is expected to be imported by other tools

## Recommended Storage Policy

### Canonical Data

Use JSON for canonical project data that needs full fidelity.

Reasons:

- easy to version
- easy to extend with new fields
- supports nested shapes and rich metadata
- works well for migration code

### Human-Friendly Export

Keep CSV for compact inspection and spreadsheet use, but treat it as a summary format.

CSV should be able to represent:

- row order
- IDs
- names
- colors
- simple geometry summaries

CSV should not be forced to represent:

- arbitrary polygon vertices in a fragile way
- nested ROI structures
- multiple geometry variants without a schema escape hatch

### Sidecar Strategy

If a spreadsheet-friendly export is needed for complex ROIs, use:

- `roi_table.csv` for readable summary data
- `roi_geometry.json` for full geometry

This is a good bridge format during migration.

## Suggested Format Rules

### Workspace

The workspace file should include:

- `format_version`
- `schema_version`
- app metadata if useful
- payload data

The loader should:

- reject obviously incompatible versions with a helpful message
- migrate supported older versions
- preserve unknown fields when practical

### ROI Export

ROI exports should include:

- ROI identity
- sample shape
- reference shape
- enabled flag
- shape kind and geometry payload
- schema version

### Processing Profiles

Processing profiles already have `profile_version`.

That should be formalized as:

- `profile_type`
- `profile_version`
- optional app version that wrote the file

The loader should continue to support older profile versions through explicit migration steps.

## Migration Policy

Migration should be deliberate.

Recommended approach:

- keep a small chain of versioned loader functions
- migrate one version step at a time
- write tests for every migration step
- preserve old readers until the next major release if possible

Example loader shape:

- read raw JSON
- inspect version
- migrate payload `v1 -> v2`
- migrate payload `v2 -> v3`
- validate final structure
- construct domain objects

## Compatibility Matrix

Suggested support levels:

- current version: full read/write support
- previous minor version: read support
- previous major version: best-effort support if easy, otherwise explicit warning

This is flexible, but the key rule is that support should be documented before breaking changes are shipped.

## Proposed Repository Files

As the format system grows, it will help to keep these files in the repo:

- `docs/format_versioning.md`
- `docs/roi_editor_branch_plan.md`
- `docs/versioning_and_repo_practices.md`
- `CHANGELOG.md`
- `CONTRIBUTING.md`

## Suggested Implementation Order

1. Add version fields to workspace and ROI exports.
2. Define the ROI JSON schema.
3. Add migration helpers for older file variants.
4. Add import tests for legacy examples.
5. Record format changes in the changelog.
