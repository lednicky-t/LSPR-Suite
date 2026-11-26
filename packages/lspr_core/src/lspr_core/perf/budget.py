"""The computer's cores, shared between the jobs that run at the same time (2026-11-26). Pure: no Qt, no files, no clock.

**The problem.** Every parallel job (export, scan, Sensorgram fits, ...) used to work out its worker count alone: "every core but one".
Two jobs at once would each start that many workers, the window would starve, and each job's measured speed would be wrong. Mature
systems solve this with one budget that hands out the cores (Grand Central Dispatch, oneTBB, a database connection pool); this is that.

**How it works, in plain words.**

* There is one number, the *capacity*: the cores the app may use (cores minus the ones the user keeps free).
* A job takes a :class:`BudgetLease` when it starts (``get_runtime().begin(...)`` does this for it) and gives it back when it ends.
  Each lease says how many workers its job *wants*; the budget tells it how many it may *use now* (its ``allowance``).
* **Fair share**: the cores are divided as equally as possible, but nobody gets more than it wants, and what a small job does not
  need goes to the others (max-min fairness). A job that wants 2 beside a job that wants 7 on a 7-core budget gets 2 and 5.
* **Nobody starves, nobody waits**: every lease gets at least one worker, always, immediately. The budget never blocks a job. If there
  are more jobs than cores, the total may exceed the capacity by that minimum, which is what the operating system would do anyway.
* **A running job shrinks or grows as others come and go.** The budget calls the lease's ``on_change`` (from whichever thread started
  or ended the other job, so it must be quick and must not call back into the budget).
* **A hard lease** (the user's "Fixed number of workers") gets exactly what it wants, always; the others share the rest.

**What a lease does not do**: it never changes *what* a job computes (the rule in ``docs/performance_tuning.md``), and it does not
count memory: that is checked when the job starts, from the memory that is free then.

**Giving back is not optional.** A lease that is never released would starve every later job for the rest of the session. So the
owner (``TunedRun``) releases in ``finish()`` and ``close()``, and as a last resort when it is garbage collected. :meth:`release`
may be called any number of times.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def fair_shares(capacity: int, wants: Sequence[int], hard: Sequence[bool] | None = None) -> list[int]:
    """How many workers each lease may use. ``wants`` is in order of arrival (oldest first); the result has the same order.

    Pure and deterministic (a test pins every rule). Rules: a hard lease gets its whole want; the rest of the capacity goes to the soft
    leases by max-min fairness (smallest want first, each gets an equal share of what is left, never more than it wants); every
    lease gets at least 1; on a tie the *older* lease gets the larger share, so a job that is already running is disturbed less
    than the one that just arrived."""
    count = len(wants)
    hard = [False] * count if hard is None else list(hard)
    wants = [max(int(w), 1) for w in wants]
    shares = [0] * count
    left = max(int(capacity), 1)
    for index in range(count):
        if hard[index]:
            shares[index] = wants[index]
            left -= wants[index]
    left = max(left, 0)
    soft = sorted((i for i in range(count) if not hard[i]), key=lambda i: (wants[i], -i))  # newest first among equal wants
    remaining = len(soft)
    for index in soft:
        shares[index] = min(wants[index], max(1, left // remaining))
        left = max(left - shares[index], 0)
        remaining -= 1
    return shares


@dataclass(frozen=True)
class LeaseInfo:
    """One line of :meth:`WorkerBudget.snapshot` (the log, a status line, tests)."""

    task_key: str
    want: int
    allowance: int
    hard: bool


class BudgetLease:
    """One job's claim on the cores. Made by :meth:`WorkerBudget.acquire`; never constructed directly."""

    def __init__(self, budget: WorkerBudget, serial: int, task_key: str, want: int, hard: bool, on_change: Callable[[int], None] | None) -> None:
        self._budget = budget
        self.serial = serial
        self.task_key = task_key
        self.want = max(int(want), 1)
        self.hard = bool(hard)
        self._on_change = on_change
        self._allowance = self.want
        self._released = False

    @property
    def allowance(self) -> int:
        """How many workers the job may use right now (at least 1). Changes when other jobs start or end."""
        return self._allowance

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        """Give the cores back. Safe to call twice, from any thread, and from a garbage-collection callback."""
        self._budget._release(self)

    def __enter__(self) -> BudgetLease:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class WorkerBudget:
    """The shared cores. One per :class:`~lspr_core.perf.runtime.PerfRuntime` (so one per app; a test makes its own)."""

    def __init__(self, capacity: int = 1) -> None:
        self._lock = threading.Lock()
        self._capacity = max(int(capacity), 1)
        self._leases: list[BudgetLease] = []  # oldest first
        self._serials = itertools.count(1)

    @property
    def capacity(self) -> int:
        return self._capacity

    def acquire(
        self,
        task_key: str,
        *,
        want: int,
        hard: bool = False,
        on_change: Callable[[int], None] | None = None,
        capacity: int | None = None,
    ) -> BudgetLease:
        """Take a share for a job that would like ``want`` workers. Never blocks and never fails: the lease's ``allowance`` says how many
        it got (at least 1). ``capacity`` (the cores the app may use now) replaces the budget's when given: the latest start-up wins,
        because the user may have changed "cores kept free" since the last job. ``on_change(allowance)`` is told when the share changes
        later, from the thread that started or ended another job; keep it short and never call the budget from it."""
        with self._lock:
            if capacity is not None:
                self._capacity = max(int(capacity), 1)
            lease = BudgetLease(self, next(self._serials), task_key, want, hard, on_change)
            self._leases.append(lease)
            notify = self._rebalance_locked(skip=lease)
            logger.debug("perf budget: %s takes %d of %d (wants %d)%s", task_key, lease._allowance, self._capacity, lease.want, " [hard]" if hard else "")
        self._notify(notify)
        return lease

    def snapshot(self) -> list[LeaseInfo]:
        """Who holds what right now, oldest first."""
        with self._lock:
            return [LeaseInfo(lease.task_key, lease.want, lease._allowance, lease.hard) for lease in self._leases]

    def active(self) -> int:
        """How many jobs hold a share (0 when nothing is running)."""
        with self._lock:
            return len(self._leases)

    # -- internals ---------------------------------------------------------------------------------------------------------------

    def _release(self, lease: BudgetLease) -> None:
        with self._lock:
            if lease._released:
                return
            lease._released = True
            try:
                self._leases.remove(lease)
            except ValueError:  # already gone: nothing to give back
                return
            notify = self._rebalance_locked()
            logger.debug("perf budget: %s gives back (%d job(s) left)", lease.task_key, len(self._leases))
        self._notify(notify)

    def _rebalance_locked(self, skip: BudgetLease | None = None) -> list[tuple[BudgetLease, int]]:
        """Recompute every share; returns the leases whose share changed (except ``skip``, which is told by its return value)."""
        shares = fair_shares(self._capacity, [lease.want for lease in self._leases], [lease.hard for lease in self._leases])
        changed: list[tuple[BudgetLease, int]] = []
        for lease, share in zip(self._leases, shares):
            if share != lease._allowance:
                lease._allowance = share
                if lease is not skip and lease._on_change is not None:
                    changed.append((lease, share))
        return changed

    @staticmethod
    def _notify(changed: list[tuple[BudgetLease, int]]) -> None:
        """Tell the leases, outside the lock (a callback may take the job's own locks). One failing callback does not stop the others."""
        for lease, share in changed:
            callback = lease._on_change
            if callback is None or lease._released:
                continue
            try:
                callback(share)
            except Exception:  # noqa: BLE001 - a job's reaction must not be able to break another job's start
                logger.warning("perf budget: the %s job could not apply its new share (%d)", lease.task_key, share, exc_info=True)
