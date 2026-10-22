"""The one entry point an app uses: ``get_runtime().begin(...)`` at the start of a parallel job (2026-10-22). No Qt.

A job asks for a :class:`TunedRun`, reports each finished unit of work to it, and reads back how many workers it may keep busy.
Everything else is the runtime's business: the machine probe, the user's preferences (automatic or manual), the limits, the
tuner, what earlier runs learned, and the summary for the log.

**The rule this module serves** (root ``CLAUDE.md``, "Performance tuning"): tuning changes *how work is scheduled*, never *what is
computed*. A caller must produce byte-identical output at any worker count and in any completion order.

The runtime is a process-wide object because the preferences are app-wide and a worker count is needed in places (an export in a
module, a scan in a task) that share no object with the Preferences dialog. Tests build their own with :func:`set_runtime`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .gate import ConcurrencyGate
from .limits import WorkerLimits, worker_limits
from .preferences import DEFAULT_PERF_PREFERENCES, PerfPreferences
from .profile_store import LearnedSetting, ProfileStore
from .system_profile import DestinationProfile, SystemProfile, probe_destination, probe_system
from .tuner import PHASE_FIXED, ThroughputTuner, TunerSummary

logger = logging.getLogger(__name__)

MIN_WINDOWS_TO_REMEMBER = 5
"""A run that measured fewer windows than this taught the profile nothing it should trust."""


@dataclass(frozen=True)
class RunReport:
    """What a finished run did, for the log and the status line."""

    task: str
    mode: str
    """``auto`` (tuned while running), ``manual`` (the user's count), ``saved`` (standard mode, at the count an earlier tuning run
    saved), ``standard`` (every core but the reserved ones) or ``fixed`` (tuning was on, but the job was too short or the range had one
    value)."""
    workers: int
    rate: float
    windows: int
    changes: int
    limits_reason: str
    started_from_learned: bool
    system: str
    destination: str

    def describe(self) -> str:
        """"8 workers (auto)" for the status line."""
        return f"{self.workers} workers ({self.mode})"

    def as_fields(self) -> dict[str, object]:
        return {
            "task": self.task, "mode": self.mode, "workers": self.workers, "rate": round(self.rate, 2), "windows": self.windows,
            "changes": self.changes, "limits": self.limits_reason, "learned_start": self.started_from_learned,
            "machine": self.system, "destination": self.destination,
        }


class TunedRun:
    """One parallel job. Not reusable. Thread-safe where it says so."""

    def __init__(
        self,
        runtime: PerfRuntime,
        task_key: str,
        *,
        total_units: float | None,
        per_worker_bytes: int,
        max_useful: int | None,
        destination: Path | str | None,
        allow_tuning: bool,
        start_hint: int | None,
        on_change: Callable[[int, int, str], None] | None,
    ) -> None:
        self.task_key = task_key
        self._runtime = runtime
        self.preferences = runtime.preferences
        self.system: SystemProfile = runtime.probe()
        self.destination: DestinationProfile | None = probe_destination(destination) if destination is not None else None
        self._destination_kind = self.destination.key() if self.destination is not None else ""
        self._fingerprint = self.system.fingerprint()

        self.learned: LearnedSetting | None = None
        if not self.preferences.is_manual and allow_tuning and runtime.store is not None:
            self.learned = runtime.store.lookup(self._fingerprint, task_key, self._destination_kind)
        self.limits: WorkerLimits = worker_limits(
            self.system, self.preferences, per_worker_bytes=per_worker_bytes, max_useful=max_useful,
            start_hint=self.learned.workers if self.learned else start_hint, allow_tuning=allow_tuning,
        )
        self.gate = ConcurrencyGate(self.limits.start)
        self._on_change_user = on_change
        self.tuner = ThroughputTuner(
            self.limits, total_units=total_units, initial_step=1 if self.learned else None, on_change=self._changed,
            **runtime.tuner_options,
        )
        logger.debug(
            "perf run %s: %d..%d workers (start %d, %s), machine %s", task_key, self.limits.minimum, self.limits.maximum,
            self.limits.start, self.limits.reason, self.system.describe(),
        )

    # -- during the run ------------------------------------------------------------------------------------------------

    @property
    def workers(self) -> int:
        """How many workers may be busy right now."""
        return self.tuner.workers

    @property
    def maximum(self) -> int:
        """The most workers this run can ever use: create the pool at this size."""
        return self.limits.maximum

    @property
    def low_priority(self) -> bool:
        return self.preferences.low_priority

    def unit_done(self, units: float = 1.0) -> int:
        """Report finished work (any thread); returns the worker count to use from now on."""
        return self.tuner.record(units)

    def _changed(self, old: int, new: int, reason: str) -> None:
        self.gate.set_limit(new)
        if self._on_change_user is not None:
            self._on_change_user(old, new, reason)

    # -- the end ------------------------------------------------------------------------------------------------------------

    def finish(self) -> RunReport:
        """Close the run: remember what it learned (tuning on, search finished) and return the report. Safe to call twice."""
        summary: TunerSummary = self.tuner.summary()
        tuned = not self.limits.fixed and self.tuner.phase != PHASE_FIXED  # a too-short job runs at its starting count
        if self.preferences.is_manual:
            mode = "manual"
        elif tuned:
            mode = "auto"
        elif self.preferences.is_auto:
            mode = "fixed"
        else:
            mode = "saved" if self.learned is not None else "standard"
        if mode == "auto" and summary.settled and summary.windows >= MIN_WINDOWS_TO_REMEMBER and summary.rate > 0:
            store = self._runtime.store
            if store is not None:
                store.record(self._fingerprint, self.task_key, self._destination_kind, workers=summary.workers, rate=summary.rate)
        return RunReport(
            task=self.task_key, mode=mode, workers=summary.workers, rate=summary.rate, windows=summary.windows,
            changes=summary.changes, limits_reason=self.limits.reason, started_from_learned=self.learned is not None,
            system=self.system.describe(), destination=self._destination_kind or "-",
        )


class PerfRuntime:
    def __init__(
        self,
        preferences: PerfPreferences = DEFAULT_PERF_PREFERENCES,
        *,
        store: ProfileStore | None = None,
        probe: Callable[[], SystemProfile] = probe_system,
        tuner_options: dict[str, float | int] | None = None,
    ) -> None:
        self._preferences = preferences.normalized()
        self.store = store
        self._probe = probe
        self.tuner_options: dict[str, float | int] = dict(tuner_options or {})
        self._last_system: SystemProfile | None = None

    @property
    def preferences(self) -> PerfPreferences:
        return self._preferences

    def set_preferences(self, preferences: PerfPreferences) -> None:
        """Takes effect for the next run; a run in progress keeps the preferences it started with."""
        self._preferences = preferences.normalized()

    def probe(self) -> SystemProfile:
        """The machine now (free memory and power change; the probe takes a few milliseconds)."""
        self._last_system = self._probe()
        return self._last_system

    def last_system(self) -> SystemProfile:
        return self._last_system if self._last_system is not None else self.probe()

    def learned_all(self) -> list[tuple[str, str, LearnedSetting]]:
        """Every count saved for this machine: ``(task, disk kind, setting)`` (the Preferences status line)."""
        if self.store is None:
            return []
        return self.store.entries(self.last_system().fingerprint())

    def forget_learned(self) -> int:
        """Delete the counts saved for this machine; the next run starts from the standard count again. Returns how many."""
        if self.store is None:
            return 0
        return self.store.forget(self.last_system().fingerprint())

    def learned(self, task_key: str, destination: Path | str | None = None) -> LearnedSetting | None:
        """What an earlier run learned for ``task_key`` on this machine (for the Preferences dialog's status line)."""
        if self.store is None:
            return None
        kind = probe_destination(destination).key() if destination is not None else ""
        return self.store.lookup(self.last_system().fingerprint(), task_key, kind)

    def begin(
        self,
        task_key: str,
        *,
        total_units: float | None = None,
        per_worker_bytes: int = 0,
        max_useful: int | None = None,
        destination: Path | str | None = None,
        allow_tuning: bool = True,
        start_hint: int | None = None,
        on_change: Callable[[int, int, str], None] | None = None,
    ) -> TunedRun:
        """Start a parallel job. ``task_key`` names the kind of job ("export.ome_zarr", "mask.scan.screen"): what is learned is kept
        per key. ``total_units`` (images, frames) lets the tuner skip a job too short to pay back a search and stop searching near the
        end. ``per_worker_bytes`` caps the workers by free memory. ``max_useful`` is the number of tasks. ``destination`` is the folder
        written to (a learned count is kept per kind of disk). ``allow_tuning=False`` runs at the starting count. ``start_hint`` is the
        caller's own default start (measured for that job); what an earlier run learned takes precedence over it."""
        return TunedRun(
            self, task_key, total_units=total_units, per_worker_bytes=per_worker_bytes, max_useful=max_useful,
            destination=destination, allow_tuning=allow_tuning, start_hint=start_hint, on_change=on_change,
        )


_runtime: PerfRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> PerfRuntime:
    """The app's runtime (created with default preferences and no profile file on first use)."""
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = PerfRuntime()
        return _runtime


def set_runtime(runtime: PerfRuntime | None) -> PerfRuntime | None:
    """Install ``runtime`` (the app at start-up, a test with a simulated machine); returns the previous one. ``None`` resets."""
    global _runtime
    with _runtime_lock:
        previous, _runtime = _runtime, runtime
        return previous
