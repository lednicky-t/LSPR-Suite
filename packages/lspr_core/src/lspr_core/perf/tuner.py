"""Find the best number of workers by measuring the running job (2026-10-22). Pure: no Qt, no files, no threads of its own.

**What it does, in plain words.** A job (an export, a scan) is a long series of equal units of work (images). Every few seconds the
tuner looks at how many units were finished per second with the current number of workers, then tries a neighbouring number and
keeps whichever is faster. It stops when neither neighbour is clearly better, holds that number, and looks again now and then in case
the computer's situation changed (another program started, the laptop got hot).

**Why throughput, not "I/O fraction".** The earlier export tuner added workers when more than half of the task time looked like disk
waiting. That is a proxy, and on the development laptop it counted decoding and rotation as disk time, so it reacted to the wrong
thing. Units per second is the thing we want to maximise; the tuner measures only that, so it is right on any machine and for any
bottleneck (cores, disk, memory bandwidth) without a model of the machine.

**Rules it follows** (each is pinned by a test in ``tests/unit/test_lspr_core_perf_tuner.py``, on simulated machines):

* More workers must win by a clear margin (``margin``, 5 %) to be kept; fewer workers are kept if they lose less than that. On a
  plateau the tuner therefore settles on the *cheapest* count that is within noise of the best (less memory, a freer window).
* A lower count is compared with the best rate seen in this search, not with the previous window, so small losses cannot add up.
* The window clock starts at the first finished unit (not at construction: worker processes need seconds to start), and that first
  window is discarded. After a change, one window is discarded only when the count rose above any count used so far (new processes
  start); lowering the count needs no start-up, so a downward probe costs one window, not two.
* A downward search slows down once the rate starts to fall (the edge is near), instead of probing far below the best count.
* It never leaves ``[limits.minimum, limits.maximum]`` (the memory and core limits are not its to move) and does nothing when the
  range has one value.
* A job too short to pay back a search is not tuned, and a search is not started near the end of a job.

Thread safety: :meth:`record` may be called from any thread (worker callbacks); everything else is for the owner of the run.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .limits import WorkerLimits

logger = logging.getLogger(__name__)

PHASE_FIXED = "fixed"
PHASE_BASELINE = "baseline"
PHASE_UP = "probe_up"
PHASE_DOWN = "probe_down"
PHASE_SETTLED = "settled"

MIN_WINDOWS_FOR_SEARCH = 12
"""A job shorter than this many windows is not tuned: the search itself would cost a visible share of it."""
DRIFT_TOLERANCE = 0.25
"""While settled: a rate this far below the settled one means conditions changed (heat, another program): search again."""


@dataclass(frozen=True)
class TunerSummary:
    workers: int
    """The count the tuner settled on (or is holding). The one worth remembering for the next run."""
    rate: float
    """Units per second measured at ``workers`` (0 when no window was measured)."""
    windows: int
    changes: int
    settled: bool
    """True when the search finished (a run that ended mid-search is not a result worth remembering)."""

    def as_fields(self) -> dict[str, object]:
        return {"workers": self.workers, "rate": round(self.rate, 2), "windows": self.windows, "changes": self.changes, "settled": self.settled}


@dataclass
class _Window:
    started: float
    units: float = 0.0


class ThroughputTuner:
    """See the module docstring. Typical use::

        tuner = ThroughputTuner(limits, total_units=len(tasks))
        ...                       # run with tuner.workers workers
        workers = tuner.record(units=1)   # after each finished unit; returns the count to use from now on
    """

    def __init__(
        self,
        limits: WorkerLimits,
        *,
        total_units: float | None = None,
        window_seconds: float = 3.0,
        units_per_worker: float = 2.0,
        margin: float = 0.05,
        settle_windows: int = 1,
        reprobe_windows: int = 15,
        initial_step: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.limits = limits
        self._clock = clock
        self._on_change = on_change
        self._lock = threading.Lock()
        self._window_seconds = float(window_seconds)
        self._units_per_worker = float(units_per_worker)
        self._margin = float(margin)
        self._settle = max(0, int(settle_windows))
        self._reprobe = max(2, int(reprobe_windows))
        self._total = total_units
        self._done = 0.0

        self.workers = limits.start
        self._incumbent_n = limits.start
        self._incumbent_rate = 0.0
        self._best_rate = 0.0
        self._step = max(1, int(initial_step) if initial_step else round(limits.start * 0.25))
        self._fine = initial_step is not None and initial_step <= 1  # steps of one worker (a learned start, or a re-check)
        self._discard = self._settle
        self._started = False  # the first window starts at the first finished unit
        self._highest = limits.start  # the largest count used so far: a larger one starts new processes
        self._settled_windows = 0
        self._windows = 0
        self._changes = 0
        self.history: list[tuple[int, float, str]] = []  # (workers, rate, phase) of every measured window, for the log and tests

        self._window = _Window(self._clock())
        searchable = (not limits.fixed) and limits.maximum > limits.minimum
        if searchable and total_units is not None and total_units < MIN_WINDOWS_FOR_SEARCH * self._window_units():
            searchable = False
        self.phase = PHASE_BASELINE if searchable else PHASE_FIXED

    # -- feeding it ------------------------------------------------------------------------------------------------------

    def record(self, units: float = 1.0, now: float | None = None) -> int:
        """Count ``units`` finished units of work; return the worker count to use from now on."""
        with self._lock:
            now = self._clock() if now is None else now
            self._done += units
            if self.phase == PHASE_FIXED:
                return self.workers
            if not self._started:  # the pool is warm once the first unit is back: the clock starts here
                self._started = True
                self._window = _Window(now)
                return self.workers
            self._window.units += units
            elapsed = now - self._window.started
            if elapsed >= self._window_seconds and self._window.units >= self._window_units():
                rate = self._window.units / elapsed if elapsed > 0 else 0.0
                self._window = _Window(now)
                self._observe(rate)
            return self.workers

    def _window_units(self) -> float:
        return self._units_per_worker * max(self.workers, 1)

    # -- the search --------------------------------------------------------------------------------------------------------

    def observe_rate(self, rate: float) -> int:
        """Feed one measured window directly (units per second at the current count). The same step ``record`` takes when a window
        closes; public so tests can drive the search with a simulated machine and no clock."""
        with self._lock:
            if self.phase != PHASE_FIXED:
                self._observe(float(rate))
            return self.workers

    def _observe(self, rate: float) -> None:
        if self._discard > 0:
            self._discard -= 1
            return
        self._windows += 1
        self.history.append((self.workers, rate, self.phase))
        if self.phase != PHASE_SETTLED and self._near_the_end():
            self._settle_on_incumbent("job nearly finished")
            return

        if self.phase == PHASE_BASELINE:
            self._incumbent_rate = self._best_rate = rate
            self._begin_up()
        elif self.phase == PHASE_UP:
            if rate > self._incumbent_rate * (1.0 + self._margin):  # a clear win: keep the larger count and try one more step
                self._incumbent_n, self._incumbent_rate = self.workers, rate
                self._best_rate = max(self._best_rate, rate)
                self._begin_up()
            elif self._step > 1:  # overshot: back to the incumbent plus a smaller step
                self._step = max(1, self._step // 2)
                self._begin_up(reason="smaller step")
            else:
                self._begin_down(initial=True)  # no gain from more workers; looking for a cheaper count from the incumbent
        elif self.phase == PHASE_DOWN:
            if rate >= self._best_rate * (1.0 - self._margin):  # fewer workers cost (almost) nothing: keep the cheaper count
                self._incumbent_n, self._incumbent_rate = self.workers, rate
                self._best_rate = max(self._best_rate, rate)
                if rate < self._best_rate * (1.0 - self._margin / 2) and self._step > 1:
                    self._step = max(1, self._step // 2)  # the rate is starting to fall: the edge is near, take smaller steps
                self._begin_down()
            elif self._step > 1:  # overshot: back to the incumbent minus a smaller step
                self._step = max(1, self._step // 2)
                self._begin_down()
            else:
                self._settle_on_incumbent("search finished")
        else:  # settled
            self._settled_windows += 1
            drifted = rate < self._incumbent_rate * (1.0 - DRIFT_TOLERANCE)
            if drifted or self._settled_windows >= self._reprobe:
                self._incumbent_rate = self._best_rate = rate  # the old rate may no longer be reachable: measure from here
                self._step, self._fine = 1, True
                self._begin_up(reason="re-check (conditions changed)" if drifted else "periodic re-check")

    def _begin_up(self, *, reason: str = "trying more workers") -> None:
        self.phase = PHASE_UP
        if self._incumbent_n >= self.limits.maximum:
            self._begin_down(initial=True)
            return
        self._move_to(min(self.limits.maximum, self._incumbent_n + self._step), reason)

    def _begin_down(self, *, initial: bool = False) -> None:
        if initial:
            self._step = 1 if self._fine else max(1, round(self._incumbent_n * 0.25))
        self.phase = PHASE_DOWN
        if self._incumbent_n <= self.limits.minimum:
            self._settle_on_incumbent("search finished")
            return
        self._move_to(max(self.limits.minimum, self._incumbent_n - self._step), "trying fewer workers")

    def _settle_on_incumbent(self, reason: str) -> None:
        self.phase = PHASE_SETTLED
        self._settled_windows = 0
        self._move_to(self._incumbent_n, reason)

    def _near_the_end(self) -> bool:
        return self._total is not None and (self._total - self._done) < 3 * self._window_units()

    def _move_to(self, count: int, reason: str) -> None:
        count = max(self.limits.minimum, min(self.limits.maximum, int(count)))
        if count != self.workers:
            old, self.workers = self.workers, count
            self._changes += 1
            if count > self._highest:  # new processes have to start; lowering the count needs no warm-up
                self._discard = self._settle
                self._highest = count
            logger.debug("perf tuner: %d -> %d workers (%s)", old, count, reason)
            if self._on_change is not None:
                self._on_change(old, count, reason)

    # -- the result -----------------------------------------------------------------------------------------------------------

    def summary(self) -> TunerSummary:
        """What the run learned. ``rate`` is the median of the windows measured at the settled count (0 if none)."""
        with self._lock:
            at_best = sorted(rate for workers, rate, _ in self.history if workers == self._incumbent_n)
            rate = at_best[len(at_best) // 2] if at_best else 0.0
            return TunerSummary(
                workers=self._incumbent_n if self.phase != PHASE_FIXED else self.workers,
                rate=rate,
                windows=self._windows,
                changes=self._changes,
                settled=self.phase == PHASE_SETTLED,
            )
