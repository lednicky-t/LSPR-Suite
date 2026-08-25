# lspr-core

Shared domain models and utilities for the LSPR Suite.

This package is intentionally small at first. It should hold the concepts that
both singleLSPR and LSPRimaging need to agree on:

- schema identity and versioning
- stable identifiers
- experiment plan step models
- common units and metadata

App-specific GUI code, hardware drivers, and image processing should stay in the
own app packages.
