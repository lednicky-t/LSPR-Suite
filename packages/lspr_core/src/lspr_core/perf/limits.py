"""How many workers a job may use at all, and where it should start (2026-10-22). Pure: numbers in, numbers out.

The *range* comes from facts about the machine (cores, free memory, the job's size); where in the range the job runs best is for the
tuner (`tuner.py`) to find by measuring. Keeping the two apart means the memory and core limits can never be tuned away.
"""

from __future__ import annotations

from dataclasses import dataclass

from .preferences import MODE_STANDARD, PerfPreferences
from .system_profile import SystemProfile

RAM_SHARE = 0.6
"""At most this share of the memory that is free now goes to the workers; the app, the GUI and the file cache need the rest."""

MANUAL_CAP_FACTOR = 2
"""A manual count is honoured up to this multiple of the logical cores (more than that only slows everything down)."""


@dataclass(frozen=True)
class WorkerLimits:
    minimum: int
    start: int
    maximum: int
    fixed: bool
    """True: the count is not tuned (standard or manual mode, a one-worker range, or tuning switched off by the caller)."""
    reason: str
    """Why the range is what it is, for the log: "cores", "memory", "tasks", "manual"."""

    def __post_init__(self) -> None:
        if not (1 <= self.minimum <= self.start <= self.maximum):
            raise ValueError(f"inconsistent worker limits: {self}")


def usable_cores(system: SystemProfile, preferences: PerfPreferences) -> int:
    """The cores the app as a whole may keep busy: the cores this process may use, minus the cores the user keeps free (ignored on a
    machine of 2 cores or fewer). This is the capacity of the shared budget (`budget.py`) and the core part of every range below."""
    preferences = preferences.normalized()
    free = preferences.reserved_cores if system.logical_cores > 2 else 0
    return max(1, system.logical_cores - free)


def pool_threads(system: SystemProfile, preferences: PerfPreferences, *, cap: int, extra_free: int = 0, floor: int = 1) -> int:
    """Threads for a small, fixed pool that is *not* tuned: reading hundreds of tiny files, a first guess handed to a tuned job.

    The one place such a count is worked out, so that none of them calls ``os.cpu_count()`` (which ignores a CPU-affinity limit and the
    user's "cores kept free"). The count is the cores this process may use, minus the cores kept free (ignored on a machine of 2 cores
    or fewer, like ``worker_limits``), minus ``extra_free`` (for a caller that runs more than one pool), kept between ``floor`` and
    ``cap``. ``cap`` is the caller's own measured limit: beyond it, more threads wait on the disk or fight for the interpreter lock.
    Fixed-number mode does not apply here: that number is for the big jobs. Scheduling only: the count never changes what is computed."""
    return max(int(floor), min(int(cap), usable_cores(system, preferences) - max(int(extra_free), 0)))


def worker_limits(
    system: SystemProfile,
    preferences: PerfPreferences,
    *,
    per_worker_bytes: int = 0,
    max_useful: int | None = None,
    start_hint: int | None = None,
    allow_tuning: bool = True,
) -> WorkerLimits:
    """The worker range for one job.

    ``per_worker_bytes``: what one worker needs in memory (0 = do not limit by memory). ``max_useful``: more workers than there are
    tasks never helps. ``start_hint``: where to begin (a count saved by an earlier tuning run, or the caller's own measured default).
    Without one the job starts at the largest allowed count, i.e. every core but the reserved ones: that is what these jobs did before
    tuning existed, and it is right for work bound by the cores. Standard mode runs at the starting count and never searches; automatic
    mode searches from it (mostly downward). ``allow_tuning=False`` also pins the count to the starting value (a caller that was told
    not to adapt)."""
    preferences = preferences.normalized()
    ceiling = max(1, int(max_useful)) if max_useful is not None else 10**6

    if preferences.is_manual:
        wanted = preferences.manual_workers or start_hint or max(1, system.logical_cores - 1)
        count = max(1, min(int(wanted), system.logical_cores * MANUAL_CAP_FACTOR, ceiling))
        return WorkerLimits(minimum=count, start=count, maximum=count, fixed=True, reason="manual")

    maximum, reason = usable_cores(system, preferences), "cores"
    if per_worker_bytes > 0 and system.available_ram_bytes > 0:
        by_memory = int(RAM_SHARE * system.available_ram_bytes // per_worker_bytes)
        if max(1, by_memory) < maximum:
            maximum, reason = max(1, by_memory), "memory"
    if ceiling < maximum:
        maximum, reason = ceiling, "tasks"

    start = max(1, min(int(start_hint) if start_hint else maximum, maximum))
    if preferences.mode == MODE_STANDARD or not allow_tuning or maximum == 1:
        why = reason if maximum == 1 else ("standard" if preferences.mode == MODE_STANDARD else "not tuned")
        return WorkerLimits(minimum=start, start=start, maximum=start, fixed=True, reason=why)
    return WorkerLimits(minimum=1, start=start, maximum=maximum, fixed=False, reason=reason)
