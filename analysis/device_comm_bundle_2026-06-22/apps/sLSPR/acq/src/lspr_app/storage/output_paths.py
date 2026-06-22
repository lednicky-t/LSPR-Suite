from __future__ import annotations

from pathlib import Path
from typing import Any
import re


def safe_path_component(value: object, *, fallback: str = "experiment") -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = text.strip(" ._")
    return text or fallback


def build_recording_experiment_base_dir(
    project_destination: str,
    experiment_name: str,
    *,
    fallback_base: Path | None = None,
) -> Path:
    base_dir = (
        Path(project_destination).expanduser()
        if str(project_destination or "").strip()
        else (fallback_base or (Path.cwd() / "data"))
    )
    if str(experiment_name or "").strip():
        base_dir = base_dir / safe_path_component(experiment_name)
    return base_dir


def recording_experiment_base_dir_for(window: Any, *, fallback_base: Path | None = None) -> Path:
    project_destination = ""
    if hasattr(window, "recording_project_destination"):
        project_destination = str(window.recording_project_destination() or "").strip()
    experiment_name = ""
    if hasattr(window, "recording_experiment_name"):
        experiment_name = str(window.recording_experiment_name() or "").strip()
    return build_recording_experiment_base_dir(
        project_destination,
        experiment_name,
        fallback_base=fallback_base,
    )
