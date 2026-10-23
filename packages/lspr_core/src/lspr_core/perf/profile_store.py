"""What earlier runs learned about this machine, kept in one small JSON file (2026-10-22). No Qt.

The tuner finds the best worker count for a job in a minute or so; remembering it means the next run starts there instead of at a
guess. The key is (machine fingerprint, task, destination kind): a count learned for the export onto the SSD says nothing about a
scan, or about an export onto a network share, and a settings folder that roams between computers must not hand one computer's
answer to another.

The file is a hint, never an input to a result: it can be deleted, edited or corrupt, and the app then simply starts from the
automatic guess. Writes are atomic (temp file, then replace) and only ever happen from the thread that finishes a run.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_ENTRIES = 200  # a runaway file is trimmed, oldest first


@dataclass(frozen=True)
class LearnedSetting:
    workers: int
    rate: float
    """Units per second measured at ``workers`` in the run that learned it."""
    runs: int
    updated_utc: str


def _entry_key(fingerprint: str, task_key: str, destination_kind: str) -> str:
    return f"{fingerprint}|{task_key}|{destination_kind}"


class ProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("The performance profile %s could not be read; starting without it.", self.path, exc_info=True)
            return {}
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION or not isinstance(payload.get("entries"), dict):
            return {}
        return {k: v for k, v in payload["entries"].items() if isinstance(v, dict)}

    def _write(self, entries: dict[str, dict]) -> None:
        """Atomic replace of the whole file; the caller holds the lock and handles errors."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps({"schema": SCHEMA_VERSION, "entries": entries}, indent=1), encoding="utf-8")
            os.replace(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)  # a failed write must not leave its temp file behind
            raise

    @staticmethod
    def _parse(entry: dict) -> LearnedSetting | None:
        workers, rate, runs, updated = entry.get("workers"), entry.get("rate"), entry.get("runs"), entry.get("updated_utc")
        if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
            return None
        return LearnedSetting(
            workers=workers,
            rate=float(rate) if isinstance(rate, (int, float)) else 0.0,
            runs=int(runs) if isinstance(runs, int) else 1,
            updated_utc=str(updated or ""),
        )

    def lookup(self, fingerprint: str, task_key: str, destination_kind: str = "") -> LearnedSetting | None:
        with self._lock:
            entry = self._load().get(_entry_key(fingerprint, task_key, destination_kind))
        return None if entry is None else self._parse(entry)

    def entries(self, fingerprint: str) -> list[tuple[str, str, LearnedSetting]]:
        """Everything saved for this machine kind: ``(task, destination kind, setting)``, sorted by task (for the Preferences status line)."""
        with self._lock:
            stored = self._load()
        found: list[tuple[str, str, LearnedSetting]] = []
        for key, entry in stored.items():
            machine, _, rest = key.partition("|")
            task, _, kind = rest.partition("|")
            if machine != fingerprint:
                continue
            learned = self._parse(entry)
            if learned is not None:
                found.append((task, kind, learned))
        return sorted(found, key=lambda item: (item[0], item[1]))

    def forget(self, fingerprint: str) -> int:
        """Delete everything saved for this machine kind (other machines' entries stay). Returns how many were removed. Never raises."""
        try:
            with self._lock:
                stored = self._load()
                doomed = [key for key in stored if key.partition("|")[0] == fingerprint]
                if not doomed:
                    return 0
                for key in doomed:
                    del stored[key]
                self._write(stored)
                return len(doomed)
        except Exception:  # noqa: BLE001
            logger.warning("The performance profile %s could not be cleared.", self.path, exc_info=True)
            return 0

    def record(self, fingerprint: str, task_key: str, destination_kind: str, *, workers: int, rate: float) -> None:
        """Remember ``workers`` as the best count found. Never raises (a failed write only costs the next run its head start)."""
        key = _entry_key(fingerprint, task_key, destination_kind)
        try:
            with self._lock:
                entries = self._load()
                previous = entries.get(key, {})
                entries[key] = {
                    "workers": int(workers),
                    "rate": round(float(rate), 3),
                    "runs": int(previous.get("runs", 0)) + 1 if isinstance(previous.get("runs", 0), int) else 1,
                    "updated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                if len(entries) > MAX_ENTRIES:
                    for stale in sorted(entries, key=lambda k: str(entries[k].get("updated_utc", "")))[: len(entries) - MAX_ENTRIES]:
                        del entries[stale]
                self._write(entries)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning("The performance profile %s could not be saved.", self.path, exc_info=True)
