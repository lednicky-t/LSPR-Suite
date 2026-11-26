"""Performance awareness shared by every LSPR app (2026-10-22): look at the machine, tune parallel work while it runs, remember
what worked. Qt-free. Design record and the rules for new code: ``docs/performance_tuning.md`` and the root ``CLAUDE.md``.

Typical use in a parallel job::

    run = get_runtime().begin("export.ome_zarr", total_units=n_planes, per_worker_bytes=300_000_000, destination=folder)
    try:
        pool = ProcessPoolExecutor(max_workers=run.maximum, ...)  # created once, at the largest size
        ...                                                       # keep run.workers tasks in flight (it can change: other jobs share the cores)
        run.unit_done(1)                                          # after each finished unit
    finally:
        run.close()                                               # always: a failed or cancelled job gives its share of the cores back
    report = run.finish()                                         # the job completed: logs / remembers what the run learned

A small fixed pool that is not worth tuning asks ``get_runtime().pool_threads(cap)``; code that only needs a size asks
``get_runtime().plan(...)``. Nothing outside this package reads the core count (a test enforces it).

**Scheduling only, never results**: output must be identical at any worker count.
"""

from .budget import BudgetLease, LeaseInfo, WorkerBudget, fair_shares
from .gate import ConcurrencyGate
from .limits import WorkerLimits, pool_threads, usable_cores, worker_limits
from .preferences import DEFAULT_PERF_PREFERENCES, MODE_AUTO, MODE_MANUAL, MODE_STANDARD, MODES, PerfPreferences
from .priority import lower_process_priority
from .profile_store import LearnedSetting, ProfileStore
from .runtime import PerfRuntime, RunReport, TunedRun, get_runtime, set_runtime
from .system_profile import DestinationProfile, SystemProfile, probe_destination, probe_system
from .tuner import ThroughputTuner, TunerSummary

__all__ = [
    "BudgetLease",
    "ConcurrencyGate",
    "DEFAULT_PERF_PREFERENCES",
    "DestinationProfile",
    "LeaseInfo",
    "LearnedSetting",
    "MODE_AUTO",
    "MODE_MANUAL",
    "MODE_STANDARD",
    "MODES",
    "PerfPreferences",
    "PerfRuntime",
    "ProfileStore",
    "RunReport",
    "SystemProfile",
    "ThroughputTuner",
    "TunedRun",
    "TunerSummary",
    "WorkerBudget",
    "WorkerLimits",
    "fair_shares",
    "get_runtime",
    "lower_process_priority",
    "pool_threads",
    "probe_destination",
    "probe_system",
    "set_runtime",
    "usable_cores",
    "worker_limits",
]
