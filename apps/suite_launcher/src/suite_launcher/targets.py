from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def _suite_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


SUITE_ROOT = _suite_root()


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Path(value)


def _candidate_paths(*paths: Path | None) -> tuple[Path, ...]:
    return tuple(path for path in paths if path is not None)


def _venv_python(root: Path | None) -> tuple[Path, ...]:
    if root is None:
        return ()
    return (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python.exe",
    )


LEGACY_ROOT = _env_path("LSPR_LEGACY_EVAL_ROOT")
LSPR_ROOT = _env_path("LSPR_LEGACY_SINGLE_ROOT")
LSPRIMAGING_ROOT = _env_path("LSPR_LEGACY_IMAGING_ROOT")


@dataclass(frozen=True)
class AppTarget:
    key: str
    title: str
    subtitle: str
    address: str
    root_candidates: tuple[Path, ...]
    script: str = ""
    python_candidates: tuple[Path, ...] = ()
    module: str | None = None
    extra_paths: tuple[Path, ...] = ()
    note: str = ""
    enabled: bool = True

    def resolve_root(self) -> Path | None:
        for candidate in self.root_candidates:
            if candidate.exists():
                return candidate
        return None

    def resolve_python(self) -> Path:
        for candidate in self.python_candidates:
            if candidate.exists():
                return candidate
        return Path(sys.executable)

    def is_available(self) -> bool:
        if not self.enabled:
            return False
        root = self.resolve_root()
        if root is None:
            return False
        if self.module is not None:
            return True
        return (root / self.script).exists()

    def build_command(self, extra_env: dict[str, str] | None = None) -> tuple[list[str], Path, dict[str, str]]:
        root = self.resolve_root()
        if root is None:
            raise FileNotFoundError(f"No available root found for {self.title}.")
        python_exe = self.resolve_python()
        command = [str(python_exe)]
        if self.module is not None:
            command.extend(["-m", self.module])
        else:
            command.append(self.script)
        env = _build_environment(root, self.extra_paths)
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        return command, root, env


def _build_environment(root: Path, extra_paths: Iterable[Path]) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_parts = [str(path) for path in extra_paths if path.exists()]
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath_parts.extend([part for part in existing.split(os.pathsep) if part])
    if pythonpath_parts:
        deduped: list[str] = []
        for part in pythonpath_parts:
            if part not in deduped:
                deduped.append(part)
        env["PYTHONPATH"] = os.pathsep.join(deduped)
    env["LSPR_SUITE_ROOT"] = str(SUITE_ROOT)
    env["LSPR_APP_ROOT"] = str(root)
    return env


TARGETS = [
    AppTarget(
        key="slspr_acq",
        title="singleLSPR Acquisition",
        subtitle="Spectrometer + experiment control acquisition",
        address="apps/sLSPR/acq/src/main.py",
        root_candidates=_candidate_paths(
            SUITE_ROOT / "apps" / "sLSPR" / "acq",
            LSPR_ROOT,
        ),
        script="src/main.py",
        python_candidates=_candidate_paths(
            *_venv_python(SUITE_ROOT),
            *_venv_python(LSPR_ROOT),
        ),
        extra_paths=(
            SUITE_ROOT / "packages" / "lspr_ui" / "src",
            SUITE_ROOT / "packages" / "lspr_core" / "src",
            SUITE_ROOT / "packages" / "lspr_io" / "src",
            SUITE_ROOT / "apps" / "sLSPR" / "acq" / "src",
        ),
        note="Prefers the suite-local workspace when present; set LSPR_LEGACY_SINGLE_ROOT for an external legacy workspace.",
    ),
    AppTarget(
        key="slspr_eva",
        title="singleLSPR Evaluation",
        subtitle="Offline sensorgram and pump-plan evaluation",
        address="apps/sLSPR/eva/main.py",
        root_candidates=_candidate_paths(
            SUITE_ROOT / "apps" / "sLSPR" / "eva",
            LEGACY_ROOT,
        ),
        script="main.py",
        python_candidates=_candidate_paths(
            *_venv_python(LEGACY_ROOT),
        ),
        extra_paths=_candidate_paths(
            LEGACY_ROOT,
        ),
        note="Uses the suite-local evaluator when present; set LSPR_LEGACY_EVAL_ROOT only if you want the old workspace.",
    ),
    AppTarget(
        key="lspri_acq",
        title="LSPRimaging Acquisition",
        subtitle="Camera + light-source acquisition",
        address="apps/LSPRi/acq/app.py",
        root_candidates=_candidate_paths(
            SUITE_ROOT / "apps" / "LSPRi" / "acq",
        ),
        script="app.py",
        module=None,
        extra_paths=(
            SUITE_ROOT / "packages" / "lspr_core" / "src",
            SUITE_ROOT / "packages" / "lspr_io" / "src",
            SUITE_ROOT / "apps" / "LSPRi" / "acq" / "src",
        ),
        note="Coming soon.",
        enabled=False,
    ),
    AppTarget(
        key="lspri_eva",
        title="LSPRimaging Evaluation",
        subtitle="Offline ROI and sensorgram analysis",
        address="apps/LSPRi/eva/src/main.py",
        root_candidates=_candidate_paths(
            SUITE_ROOT / "apps" / "LSPRi" / "eva",
            LSPRIMAGING_ROOT,
        ),
        script="src/main.py",
        python_candidates=_candidate_paths(
            *_venv_python(SUITE_ROOT / "apps" / "LSPRi" / "eva"),
            *_venv_python(LSPRIMAGING_ROOT),
        ),
        note="Implemented in this workspace. Set LSPR_LEGACY_IMAGING_ROOT to point at a legacy workspace if needed.",
    ),
]
