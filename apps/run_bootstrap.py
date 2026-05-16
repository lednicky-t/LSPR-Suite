from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_SRC_DIRS = [
    REPO_ROOT / "packages" / "lspr_ui" / "src",
    REPO_ROOT / "packages" / "lspr_core" / "src",
    REPO_ROOT / "packages" / "lspr_io" / "src",
]


def bootstrap_app_environment(app_src_dir: str | Path, extra_src_dirs: Iterable[str | Path] = ()) -> None:
    paths = [REPO_ROOT / Path(app_src_dir)]
    paths.extend(REPO_ROOT / Path(extra) for extra in extra_src_dirs)
    paths.extend(SHARED_SRC_DIRS)
    for path in reversed(paths):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
