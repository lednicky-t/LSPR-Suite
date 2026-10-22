"""Let background worker *processes* yield to the window (2026-10-22). Never raises.

A worker process set to below-normal priority only gets a core when no normal-priority program wants it, so a busy export or scan
leaves the window (and the rest of the computer) responsive, and costs the job almost nothing when nothing else is running.
Process-wide, unlike LSPRi's ``thread_priority.py`` (one thread). Call it as the first thing in a worker process
(``ProcessPoolExecutor(initializer=...)``).
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def lower_process_priority() -> bool:
    """Set the calling process to below-normal priority. Returns whether it was set."""
    try:
        import psutil

        process = psutil.Process()
        if sys.platform == "win32":
            process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            process.nice(5)
        return True
    except Exception:  # noqa: BLE001 - a priority hint must not be able to stop a worker from starting
        logger.debug("Could not lower the process priority", exc_info=True)
        return False


def current_process_priority_is_lowered() -> bool | None:
    """Whether this process runs below normal priority (None: cannot be read). For tests and the log."""
    try:
        import psutil

        nice = psutil.Process().nice()
        if sys.platform == "win32":
            return nice == psutil.BELOW_NORMAL_PRIORITY_CLASS
        return nice > 0
    except Exception:  # noqa: BLE001
        return None
