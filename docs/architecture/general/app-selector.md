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

The singleLSPR acquisition card also carries visible launch-mode controls:

- a launch-profile selector for `Full`, `Simulation`, or `Control editor`
- a `Quiet logs` toggle that passes diagnostics mode to the acquisition app
- a `File info` toggle that suppresses INFO-level diagnostics in the acquisition app log file

This makes it possible to migrate each app into the suite without rewriting the launcher UI.
