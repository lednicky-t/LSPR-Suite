"""Shared live-acquisition shell for the LSPR Suite's acquisition apps.

Empty scaffold - see README.md and
docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md for
what will be extracted here and in what order. Nothing is exported yet because
nothing has been moved in yet; do not add speculative re-exports ahead of the
actual extraction.
"""

from __future__ import annotations

from .version import APP_NAME, APP_VERSION, __version__, version_string

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "__version__",
    "version_string",
]
