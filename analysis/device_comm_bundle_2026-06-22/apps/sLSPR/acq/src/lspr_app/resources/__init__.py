from __future__ import annotations

from pathlib import Path


_LOCAL_ICON_DIR = Path(__file__).resolve().parent / "icons"


def _suite_icon_dir() -> Path | None:
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "resources" / "icons"
        if candidate == _LOCAL_ICON_DIR:
            continue
        if candidate.exists():
            return candidate
    return None


def shared_icon_path(name: str) -> Path:
    suite_dir = _suite_icon_dir()
    if suite_dir is not None:
        candidate = suite_dir / name
        if candidate.exists():
            return candidate
    local_candidate = _LOCAL_ICON_DIR / name
    if local_candidate.exists():
        return local_candidate
    return local_candidate


def app_icon_path() -> Path:
    return shared_icon_path("app_icon.svg")
