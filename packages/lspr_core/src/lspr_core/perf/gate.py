"""A counting semaphore whose limit can change while it is in use (2026-10-22). Pure stdlib.

Why it exists: the tuner decides, while a job runs, how many workers should be busy. A ``ThreadPoolExecutor`` cannot be resized, and
a ``ProcessPoolExecutor`` that is re-created for each change pays several seconds of process start-up (Windows "spawn"). The pool is
instead created once at the largest size the limits allow, and the *gate* lets only the wanted number of tasks run at the same
time: raising the limit wakes waiting workers at once, lowering it lets running tasks finish and starts no new ones above the limit.
Idle threads cost nothing; idle processes cost only their memory (the limits already account for it).

For a process pool the coordinator can use the limit directly (keep ``limit`` tasks in flight); the gate is for threads.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class ConcurrencyGate:
    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._active = 0
        self._cond = threading.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    def set_limit(self, limit: int) -> None:
        with self._cond:
            self._limit = max(1, int(limit))
            self._cond.notify_all()

    def acquire(self) -> None:
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait()
            self._active += 1

    def release(self) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify()

    def __enter__(self) -> ConcurrencyGate:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def wrap(self, fn: Callable[..., T]) -> Callable[..., T]:
        """``fn`` that runs only while it holds a slot of this gate."""

        def gated(*args: object, **kwargs: object) -> T:
            with self:
                return fn(*args, **kwargs)

        return gated
