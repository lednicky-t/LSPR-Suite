"""What this computer is, in a few numbers (2026-10-22). Qt-free, cheap (a few milliseconds), never raises.

**Why.** Parallel work (export, scans, analysis) has to know how many cores, how much free memory and what kind of disk it can
count on. The numbers set safe starting values and upper limits; the real choice is then made by measuring the running job
(`tuner.py`). Nothing here is benchmarked: a benchmark at start-up is slow and noisy (heat, power state, antivirus), and what it
measured can be wrong ten minutes later.

`psutil` is soft-imported: without it the numbers fall back to `os.cpu_count()` and "unknown".
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised implicitly; the fallback paths are tested by monkeypatching `_psutil`
    import psutil as _psutil
except ImportError:  # pragma: no cover
    _psutil = None

_GIB = 1024**3


@dataclass(frozen=True)
class SystemProfile:
    logical_cores: int
    """Cores (hardware threads) this process may use: the machine's, minus any CPU-affinity restriction."""
    physical_cores: int
    """Real cores. A hyper-threaded pair counts once: it shares one core's execution units."""
    total_ram_bytes: int
    available_ram_bytes: int
    """Memory that can be handed out now without swapping (psutil's ``available``); 0 when unknown."""
    on_battery: bool | None
    """True: running on battery. None: unknown (desktop, or no psutil)."""
    platform: str
    """``sys.platform`` plus the machine type, for the fingerprint."""

    def fingerprint(self) -> str:
        """Stable id of this *kind* of machine (cores, memory rounded to GiB, platform). Free memory and battery are not in it:
        they change from minute to minute, the machine does not. Used as the key of the learned-settings file."""
        return f"{self.platform}-p{self.physical_cores}-l{self.logical_cores}-r{round(self.total_ram_bytes / _GIB)}"

    def describe(self) -> str:
        """One line for the Preferences dialog and the log: "4 cores / 8 threads, 15.8 GB memory (9.1 GB free), on mains"."""
        cores = (
            f"{self.physical_cores} cores / {self.logical_cores} threads"
            if self.physical_cores != self.logical_cores
            else f"{self.logical_cores} cores"
        )
        memory = f"{self.total_ram_bytes / _GIB:.1f} GB memory" if self.total_ram_bytes else "memory unknown"
        if self.available_ram_bytes:
            memory += f" ({self.available_ram_bytes / _GIB:.1f} GB free)"
        power = {True: ", on battery", False: ", on mains", None: ""}[self.on_battery]
        return f"{cores}, {memory}{power}"


def _usable_logical_cores() -> int:
    """Logical cores this process may run on (honours a CPU-affinity restriction, which `os.cpu_count()` does not)."""
    count = int(os.cpu_count() or 1)
    if _psutil is not None:
        try:
            affinity = _psutil.Process().cpu_affinity()
            if affinity:
                return max(1, min(count, len(affinity)))
        except (AttributeError, NotImplementedError, OSError, _psutil.Error):  # not every platform has affinity
            pass
    return max(1, count)


def probe_system() -> SystemProfile:
    """Read the numbers now. Safe to call from any thread; never raises (an unreadable value is reported as unknown / the fallback)."""
    logical = _usable_logical_cores()
    physical = logical
    total_ram = available_ram = 0
    on_battery: bool | None = None
    if _psutil is not None:
        try:
            counted = _psutil.cpu_count(logical=False)
            if counted:
                physical = max(1, min(int(counted), logical))
        except Exception:  # noqa: BLE001 - a diagnostic number must not be able to stop the app
            logger.debug("Could not read the physical core count", exc_info=True)
        try:
            memory = _psutil.virtual_memory()
            total_ram, available_ram = int(memory.total), int(memory.available)
        except Exception:  # noqa: BLE001
            logger.debug("Could not read the memory size", exc_info=True)
        try:
            battery = _psutil.sensors_battery() if hasattr(_psutil, "sensors_battery") else None
            if battery is not None and battery.power_plugged is not None:
                on_battery = not bool(battery.power_plugged)
        except Exception:  # noqa: BLE001
            logger.debug("Could not read the power state", exc_info=True)
    return SystemProfile(
        logical_cores=logical,
        physical_cores=physical,
        total_ram_bytes=total_ram,
        available_ram_bytes=available_ram,
        on_battery=on_battery,
        platform=f"{sys.platform}-{platform.machine().lower() or 'unknown'}",
    )


# -- the destination disk -----------------------------------------------------------------------------------------------

DESTINATION_LOCAL = "local"
DESTINATION_REMOVABLE = "removable"
DESTINATION_NETWORK = "network"
DESTINATION_UNKNOWN = "unknown"

_DRIVE_REMOVABLE, _DRIVE_FIXED, _DRIVE_REMOTE = 2, 3, 4  # GetDriveTypeW return values


@dataclass(frozen=True)
class DestinationProfile:
    kind: str
    """``local`` (a fixed disk), ``removable`` (USB stick / SD card), ``network`` (share) or ``unknown``."""
    free_bytes: int | None

    def key(self) -> str:
        return self.kind


def _windows_drive_kind(path: Path) -> str:
    text = str(path)
    if text.startswith("\\\\") and not text.startswith("\\\\?\\"):
        return DESTINATION_NETWORK  # UNC path: \\server\share
    anchor = path.anchor
    if not anchor:
        return DESTINATION_UNKNOWN
    try:
        kind = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW(anchor)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return DESTINATION_UNKNOWN
    return {_DRIVE_FIXED: DESTINATION_LOCAL, _DRIVE_REMOVABLE: DESTINATION_REMOVABLE, _DRIVE_REMOTE: DESTINATION_NETWORK}.get(
        kind, DESTINATION_UNKNOWN
    )


def probe_destination(path: Path | str) -> DestinationProfile:
    """What kind of disk ``path`` is on, and its free space. ``path`` need not exist yet: the nearest existing parent is used.
    Network and removable disks want fewer simultaneous writers than a fixed SSD; the tuner finds the number, this only tells it
    which learned setting applies (a setting learned on the SSD says nothing about a share)."""
    target = Path(path)
    try:
        target = target.resolve()
    except OSError:
        pass
    kind = _windows_drive_kind(target) if sys.platform == "win32" else DESTINATION_UNKNOWN
    free: int | None = None
    probe = target
    for _ in range(64):  # walk up to the first parent that exists
        if probe.exists():
            break
        if probe.parent == probe:
            break
        probe = probe.parent
    try:
        import shutil

        free = int(shutil.disk_usage(probe).free)
    except OSError:
        free = None
    return DestinationProfile(kind=kind, free_bytes=free)
