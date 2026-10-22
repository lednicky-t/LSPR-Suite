"""Performance awareness shared by every LSPR app (2026-10-22): look at the machine, tune parallel work while it runs, remember
what worked. Qt-free. Design record and the rules for new code: ``docs/performance_tuning.md`` and the root ``CLAUDE.md``.

Typical use in a parallel job::

    run = get_runtime().begin("export.ome_zarr", total_units=n_planes, per_worker_bytes=300_000_000, destination=folder)
    pool = ProcessPoolExecutor(max_workers=run.maximum, ...)      # created once, at the largest size
    ...                                                           # keep run.workers tasks in flight
    run.unit_done(1)                                              # after each finished unit
    report = run.finish()                                         # logs / remembers what the run learned

**Scheduling only, never results**: output must be identical at any worker count.
"""

from .gate import ConcurrencyGate
from .limits import WorkerLimits, worker_limits
from .preferences import DEFAULT_PERF_PREFERENCES, MODE_AUTO, MODE_MANUAL, MODE_STANDARD, MODES, PerfPreferences
from .priority import current_process_priority_is_lowered, lower_process_priority
from .profile_store import LearnedSetting, ProfileStore
from .runtime import PerfRuntime, RunReport, TunedRun, get_runtime, set_runtime
from .system_profile import DestinationProfile, SystemProfile, probe_destination, probe_system
from .tuner import ThroughputTuner, TunerSummary

__all__ = [
    "ConcurrencyGate",
    "DEFAULT_PERF_PREFERENCES",
    "DestinationProfile",
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
    "WorkerLimits",
    "current_process_priority_is_lowered",
    "get_runtime",
    "lower_process_priority",
    "probe_destination",
    "probe_system",
    "set_runtime",
    "worker_limits",
]
