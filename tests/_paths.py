from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
# Present only when the LSPRi submodule is checked out on a branch that has the rewrite.
LSPRI_REWRITE_MARKER = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src" / "lspr_imaging_app" / "app_rewrite.py"
PATHS = (
    REPO_ROOT / "packages" / "lspr_ui" / "src",
    REPO_ROOT / "packages" / "lspr_core" / "src",
    REPO_ROOT / "packages" / "lspr_io" / "src",
    REPO_ROOT / "apps" / "suite_launcher" / "src",
    REPO_ROOT / "apps" / "suite_launcher" / "updater",
)


def ensure_repo_paths() -> None:
    for path in reversed(PATHS):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def skip_if_lspri_rewrite_absent(error: ImportError) -> None:
    """For a test file that imports the LSPRi rewrite code, inside its `except ImportError as exc:`:

        except ImportError as exc:
            skip_if_lspri_rewrite_absent(exc)
            raise

    Skips the whole file only when the rewrite code is not on disk (submodule not checked out, or on a branch without
    the rewrite). When the code IS there, the ImportError is a real problem (a typo, a renamed module, a missing
    dependency): this returns, the bare `raise` re-raises it, and the file fails loudly instead of showing as a green
    skip that tests nothing."""
    if not LSPRI_REWRITE_MARKER.is_file():
        raise unittest.SkipTest(f"LSPRi rewrite code is not checked out ({LSPRI_REWRITE_MARKER.name} not found): {error}") from error
