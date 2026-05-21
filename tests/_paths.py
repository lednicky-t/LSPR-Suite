from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
PATHS = (
    REPO_ROOT / "packages" / "lspr_ui" / "src",
    REPO_ROOT / "packages" / "lspr_core" / "src",
    REPO_ROOT / "packages" / "lspr_io" / "src",
    REPO_ROOT / "apps" / "suite_launcher" / "src",
)


def ensure_repo_paths() -> None:
    for path in reversed(PATHS):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
