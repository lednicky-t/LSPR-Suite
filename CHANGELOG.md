# Changelog

## Unreleased

### Milestone

- Sensorgram and launcher workflow milestone:
  - rolling sensorgram compression now follows the selected window and keeps the display bounded
  - the rolling-window toggle no longer skips values on click
  - metric selector controls now support custom colors, clearer labels, and a stable primary selector
  - the sensogram settings window was reorganized into clearer live/preview tabs with mode-aware tooltips
  - launcher diagnostics were consolidated into a single selector and documented in the launcher README

### Added

- Root-level unit tests for core models, HDF metadata helpers, and launcher registry behavior.
- A GitHub Actions workflow to run the repository test suite.

### Changed

- The suite launcher no longer depends on a hardcoded personal desktop fallback path for the singleLSPR acquisition workspace.

### Notes

- Repository governance is still being expanded to better match the compatibility policy documented under `docs/`.
