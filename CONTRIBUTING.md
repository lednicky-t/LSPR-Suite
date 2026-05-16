# Contributing to LSPR Suite

This repository is the umbrella for the LSPR application suite.

## Where to make changes

- App-specific code lives in the app repositories linked as submodules.
- Shared libraries live in `packages/`.
- Suite launcher changes live in `apps/suite_launcher/`.
- Suite-wide documentation lives in `docs/`.

## App repositories

- `apps/LSPRi/eva` -> LSPRimaging evaluation
- `apps/sLSPR/acq` -> singleLSPR acquisition
- `apps/sLSPR/eva` -> singleLSPR evaluation

`LSPRimaging-Acquisition` is reserved for later and is not part of this checkout yet.

## Working on an app

1. Make the change in the app repository.
2. Commit and push it there.
3. Update the submodule pointer in this suite repository if needed.
4. Commit the submodule pointer update in the suite repository.

## Shared code

Use the shared packages in `packages/` for logic that is reused across apps:

- `lspr_core` for common domain and flow helpers
- `lspr_io` for file formats and HDF rules
- `lspr_ui` for shared Qt styling and UI helpers

## Notes

- This repo keeps the umbrella structure only.
- The app histories stay in their own repositories.
- Submodule updates should stay small and intentional.
