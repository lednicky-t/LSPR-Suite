"""Background export pipeline for experiment plans.

Provides a ``QRunnable`` task that writes an experiment plan to disk in either
CSV (semicolon-delimited) or native YAML format, depending on which fields of
:class:`ExperimentPlanExportData` are populated.

Extracted from singleLSPR Acquisition's `gui/experiment_control_export.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 0 extraction, 2026-08-09)
verbatim - zero window coupling to begin with (takes only a Path + plain
data, no window reference).

Public API used by ``ExperimentControlWindow``
----------------------------------------------
``ExperimentPlanExportData``
    Payload dataclass — populate ``header``/``rows`` for CSV or ``document``
    for YAML.
``ExperimentPlanExportSignals``
    Qt signals emitted when the task finishes or fails.
``ExperimentPlanExportTask``
    ``QRunnable`` that performs the file write on a thread-pool thread.

Signals
-------
``ExperimentPlanExportSignals.finished(int, object)``
    Emitted with the generation counter and the completed payload on success.
``ExperimentPlanExportSignals.failed(int, str)``
    Emitted with the generation counter and an error message on failure.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency guard
    yaml = None

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal


@dataclass(slots=True)
class ExperimentPlanExportData:
    """Payload for a single export task.

    Populate *either* ``header`` + ``rows`` (for CSV output) or ``document``
    (for native YAML output); not both.
    """

    path: Path
    header: list[str] | None = None
    rows: list[list[str]] | None = None
    document: dict[str, object] | None = None


class ExperimentPlanExportSignals(QObject):
    """Qt signals for :class:`ExperimentPlanExportTask`."""

    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class ExperimentPlanExportTask(QRunnable):
    """Thread-pool task that writes an experiment plan payload to disk.

    On success emits :attr:`signals.finished`; on any exception emits
    :attr:`signals.failed` with the stringified error.

    The *generation* counter lets the caller discard results from stale tasks
    when the user starts a new export before a previous one completes.
    """

    def __init__(self, generation: int, payload: ExperimentPlanExportData) -> None:
        super().__init__()
        self._generation = generation
        self._payload = payload
        self.signals = ExperimentPlanExportSignals()

    def run(self) -> None:  # pragma: no cover - thread-pool path
        try:
            self._payload.path.parent.mkdir(parents=True, exist_ok=True)
            if self._payload.document is not None:
                if yaml is None:
                    raise RuntimeError("PyYAML is required to export native YAML experiment plans.")
                text = yaml.safe_dump(
                    self._payload.document,
                    sort_keys=False,
                    allow_unicode=False,
                    default_flow_style=False,
                )
                self._payload.path.write_text(text, encoding="utf-8")
            else:
                with self._payload.path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle, delimiter=";", lineterminator="\n")
                    writer.writerow(self._payload.header or [])
                    writer.writerows(self._payload.rows or [])
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))
            return
        self.signals.finished.emit(self._generation, self._payload)
