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
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .budget import BudgetLease, WorkerBudget
from .gate import ConcurrencyGate
from .limits import WorkerLimits, pool_threads, usable_cores, worker_limits
from .preferences import DEFAULT_PERF_PREFERENCES, PerfPreferences
from .profile_store import LearnedSetting, ProfileStore
from .system_profile import DestinationProfile, SystemProfile, probe_destination, probe_system
from .tuner import PHASE_FIXED, ThroughputTuner, TunerSummary

logger = logging.getLogger(__name__)

MIN_WINDOWS_TO_REMEMBER = 5
"""A run that measured fewer windows than this taught the profile nothing it should trust."""


def _weak(method: Callable[..., object]) -> Callable[..., object]:
    """``method`` as a function that does nothing once its object is gone (so a callback never keeps its owner alive)."""
    reference = weakref.WeakMethod(method)  # type: ignore[arg-type]

    def call(*args: object) -> object:
        target = reference()
        return target(*args) if target is not None else None

    return call


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
    destination_media: str = ""
    """``ssd`` / ``hdd`` / ``unknown`` for a local destination, empty when the job writes nowhere. Information for the log only."""
    shared: bool = False
    """Another job held cores at the same time and this one had to use fewer (``budget.py``): its rate is not the computer's alone,
    so nothing was saved for it."""

    def describe(self) -> str:
        """"8 workers (auto)" for the status line."""
        return f"{self.workers} workers ({self.mode})"

    def as_fields(self) -> dict[str, object]:
        return {
            "task": self.task, "mode": self.mode, "workers": self.workers, "rate": round(self.rate, 2), "windows": self.windows,
            "changes": self.changes, "limits": self.limits_reason, "learned_start": self.started_from_learned,
            "machine": self.system, "destination": self.destination, "media": self.destination_media or "-",
            "shared": self.shared,
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
        self._on_change_user = on_change
        self.shared = False
        """True once another job held cores at the same time and this run had to use fewer than its range allowed. A shared run's
        speed is not what this computer does alone, so what it learned is not saved (``finish``)."""
        self.tuner: ThroughputTuner | None = None
        self._apply_lock = threading.Lock()

        # Take a share of the cores (budget.py). Weak references in the callbacks, so neither the budget nor the tuner keeps a forgotten
        # run alive: a run that is dropped (a job that raised before `finish()`) is freed at once and its finalizer gives the share back.
        self.lease: BudgetLease = runtime.budget.acquire(
            task_key, want=self.limits.maximum, hard=self.preferences.is_manual, on_change=_weak(self._share_changed),
            capacity=usable_cores(self.system, self.preferences),
        )
        self._finalizer = weakref.finalize(self, self.lease.release)
        starting = self._within(self.lease.allowance)
        self.shared = starting != self.limits
        self.gate = ConcurrencyGate(starting.start)
        self.tuner = ThroughputTuner(
            starting, total_units=total_units, initial_step=1 if self.learned else None, on_change=_weak(self._changed),
            **runtime.tuner_options,
        )
        self._share_changed()  # a share that changed between taking the lease and building the tuner
        logger.debug(
            "perf run %s: %d..%d workers (start %d, %s), share %d of %d, machine %s%s", task_key, self.limits.minimum, self.limits.maximum,
            starting.start, self.limits.reason, self.lease.allowance, runtime.budget.capacity, self.system.describe(),
            f", writing to a {self.destination.describe()}" if self.destination is not None else "",
        )

    # -- sharing the cores with other jobs ---------------------------------------------------------------------------------------

    def _within(self, allowance: int) -> WorkerLimits:
        """This run's range when it may use only ``allowance`` workers. Never larger than the range it was planned with. A run that
        was not searched stays one count; a searched run keeps a range below its share; with one worker there is nothing to search."""
        base = self.limits
        if allowance >= base.maximum:
            return base
        start = max(1, min(base.start, allowance))
        if base.fixed or allowance <= 1:
            return WorkerLimits(minimum=start, start=start, maximum=start, fixed=True, reason="shared")
        return WorkerLimits(minimum=1, start=start, maximum=allowance, fixed=False, reason="shared")

    def _share_changed(self, _allowance: int | None = None) -> None:
        """Another job started or ended: take the share the budget gives now (always the current one, never the argument, which may
        be stale by the time two threads' notifications are applied)."""
        tuner = self.tuner
        if tuner is None:
            return
        with self._apply_lock:
            limits = self._within(self.lease.allowance)
            if limits == tuner.limits:
                return
            if limits != self.limits:
                self.shared = True
            logger.debug("perf run %s: shared budget, now %d..%d workers", self.task_key, limits.minimum, limits.maximum)
            tuner.set_limits(limits)

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

    @property
    def share(self) -> int:
        """The workers this run's share of the cores allows right now (the pool is still created at ``maximum``)."""
        return self.lease.allowance

    def _changed(self, old: int, new: int, reason: str) -> None:
        self.gate.set_limit(new)
        if self._on_change_user is not None:
            self._on_change_user(old, new, reason)

    # -- the end ------------------------------------------------------------------------------------------------------------

    def close(self) -> None:
        """Give the cores back **without** a report: for a job that failed, was cancelled, or is leaving by an exception. Call it in a
        ``finally`` (or use ``with get_runtime().begin(...) as run:``); a run that is simply dropped is closed when it is garbage
        collected, but do not rely on that. Safe to call any number of times, and after ``finish()``."""
        self._finalizer()

    def __enter__(self) -> TunedRun:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def finish(self) -> RunReport:
        """The job completed: remember what it learned (tuning on, search finished, not shared), give the cores back and return the
        report. Safe to call twice. A job that did not complete calls ``close()`` instead."""
        try:
            return self.report()
        finally:
            self.close()

    def report(self) -> RunReport:
        """What the run did so far, and remember what it learned (as ``finish()`` does) - but **keep the share of the cores**. For a job
        whose tuned stage ends before the job does (the transient scan: later stages keep running on the same workers); such a job
        ends with ``close()``. Everything else calls ``finish()``."""
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
        if mode == "auto" and summary.settled and summary.windows >= MIN_WINDOWS_TO_REMEMBER and summary.rate > 0 and not self.shared:
            store = self._runtime.store
            if store is not None:
                store.record(self._fingerprint, self.task_key, self._destination_kind, workers=summary.workers, rate=summary.rate)
        return RunReport(
            task=self.task_key, mode=mode, workers=summary.workers, rate=summary.rate, windows=summary.windows,
            changes=summary.changes, limits_reason=self.limits.reason, started_from_learned=self.learned is not None,
            system=self.system.describe(), destination=self._destination_kind or "-",
            destination_media=self.destination.media if self.destination is not None else "", shared=self.shared,
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
        self.budget = WorkerBudget()
        """The cores shared by the jobs running now (``budget.py``). Every ``begin()`` takes a share; ``finish()`` / ``close()`` gives it back."""

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

    def pool_threads(self, cap: int, *, extra_free: int = 0, floor: int = 1) -> int:
        """Threads for a small fixed pool that is not tuned (see ``limits.pool_threads``). Asks the machine once (core counts do not
        change while the app runs) and takes the saved "cores kept free" choice, so every such pool follows the same rule."""
        return pool_threads(self.last_system(), self._preferences, cap=cap, extra_free=extra_free, floor=floor)

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

    def plan(
        self,
        *,
        per_worker_bytes: int = 0,
        max_useful: int | None = None,
        start_hint: int | None = None,
        allow_tuning: bool = True,
    ) -> WorkerLimits:
        """How many workers a job *could* use, **without** starting one: the range only, no share of the cores is taken. For code that
        only needs a size (a warm pool made ahead of its first job). Anything that then does the work calls ``begin()``."""
        return worker_limits(
            self.probe(), self._preferences, per_worker_bytes=per_worker_bytes, max_useful=max_useful, start_hint=start_hint,
            allow_tuning=allow_tuning,
        )

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
        caller's own default start (measured for that job); what an earlier run learned takes precedence over it.

        **The run holds a share of the cores until it ends** (``budget.py``): call ``finish()`` when the job completed and ``close()``
        when it failed or was cancelled, in a ``finally`` (or use ``with begin(...) as run:``). Never call ``begin()`` just to read a
        size: that is ``plan()``."""
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
