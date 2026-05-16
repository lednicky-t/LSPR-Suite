# LSPR Suite Repo Map

This suite is organized as an umbrella repository with app repositories linked
as Git submodules.

## Repositories

- `LSPR-Suite`  
  Umbrella repo for suite-wide docs, shared packages, the suite launcher, and
  submodule pointers.
- `SingleSpotLSPR-Acquisition`  
  singleLSPR acquisition app.
- `SingleSpotLSPR-Evaluation`  
  singleLSPR evaluation app.
- `LSPRimaging-Evaluation`  
  LSPRimaging evaluation app.
- `LSPRimaging-Acquisition`  
  Reserved for later. Not started yet.

## What lives where

- `apps/LSPRi/eva` is the LSPRimaging evaluation submodule.
- `apps/sLSPR/acq` is the singleLSPR acquisition submodule.
- `apps/sLSPR/eva` is the singleLSPR evaluation submodule.
- `apps/suite_launcher` stays in the suite repo.
- `packages/` stays in the suite repo for shared code.

## Working rule

Edit app-specific code in the app repo itself. If a suite-level change updates
an app submodule pointer, commit the app change first, then update the umbrella
repo pointer separately.
