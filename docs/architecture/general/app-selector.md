# App Selector

The suite launcher is a small Qt app that starts the four main workspace apps.

## Goals

- give a single starting point for the suite
- keep acquisition and evaluation apps separate
- prefer suite-local copies when they exist
- preserve a transition path from the legacy standalone projects

## Current Targets

- `singleLSPR Acquisition`
- `singleLSPR Evaluation`
- `LSPRimaging Acquisition`
- `LSPRimaging Evaluation`

## Launch Strategy

The launcher uses a target registry with:

- a user-facing title
- a description
- a preferred suite path
- optional legacy fallback paths
- a launch command

This makes it possible to migrate each app into the suite without rewriting the launcher UI.
