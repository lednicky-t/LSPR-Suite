"""The single-lane thread pool used for all device lifecycle/command work.

Extracted from singleLSPR Acquisition's `gui/device_lifecycle_task.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 2 follow-on, 2026-08-09)
verbatim - a plain process-global singleton accessor, zero window coupling.
All device connect/disconnect/discover/command work must run on this one
worker lane, separate from any general-purpose thread pool, so that no two
hardware-touching operations can ever run concurrently - the AMF vendor SDK
in particular is not guaranteed thread-safe (see R7/R8 in
`docs/device-layer/DEVICE_LAYER_AUDIT_2026.md`). `_StepApplyRunnable`
(`experiment_control_step_runner.py`, already shared since Tier 0) is
dispatched onto this pool by callers.
"""

from __future__ import annotations

from PyQt6.QtCore import QThreadPool

_DEVICE_IO_POOL: QThreadPool | None = None


def device_io_pool() -> QThreadPool:
    """The single-lane thread pool used for all device lifecycle work."""
    global _DEVICE_IO_POOL
    if _DEVICE_IO_POOL is None:
        _DEVICE_IO_POOL = QThreadPool()
        _DEVICE_IO_POOL.setMaxThreadCount(1)
    return _DEVICE_IO_POOL
