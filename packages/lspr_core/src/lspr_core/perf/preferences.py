"""The user's say in how much of the computer a job may use (2026-10-22). Pure data, no Qt.

Three modes. **Standard** (the default): every core but the ones kept free, no searching; if a tuning run has saved a better count for
this machine, that count is used. **Tune automatically** (opt-in): the worker count is searched while the job runs and the best
count found is saved for this machine. **Fixed number**: exactly that many, for a shared workstation, a PC that also runs an
acquisition, or someone reproducing a benchmark. Measured on the development laptop, tuning found nothing a fixed default missed and cost a
few percent while it searched (`docs/performance_tuning.md`), so it is something to switch on where it may pay off, not the default.
**Whatever is chosen here changes only how work is scheduled, never what is computed** (rule in the root ``CLAUDE.md``).
"""

from __future__ import annotations

from dataclasses import dataclass

MODE_STANDARD = "standard"
MODE_AUTO = "auto"  # "Tune automatically": the count is searched while the job runs, and saved
MODE_MANUAL = "manual"
MODES = (MODE_STANDARD, MODE_AUTO, MODE_MANUAL)
MAX_MANUAL_WORKERS = 256


@dataclass(frozen=True)
class PerfPreferences:
    mode: str = MODE_STANDARD
    """``standard``: every core but the reserved ones (or the count a tuning run saved for this machine). ``auto``: the tuner searches
    while the job runs and saves the best count. ``manual``: exactly ``manual_workers``, no tuning, nothing saved."""
    manual_workers: int = 0
    """Used only in manual mode. 0 = not chosen yet (manual then behaves like the automatic starting value, without tuning)."""
    reserved_cores: int = 1
    """Cores kept free for the window and the rest of the computer (standard and automatic modes; ignored on machines of 2 cores or fewer)."""
    low_priority: bool = True
    """Run background worker processes at below-normal priority, so the window keeps its turn on a busy computer."""

    def normalized(self) -> PerfPreferences:
        """The same preferences with every value forced into its legal range (a settings file is JSON a person can edit)."""
        mode = self.mode if self.mode in MODES else MODE_STANDARD
        return PerfPreferences(
            mode=mode,
            manual_workers=min(max(int(self.manual_workers), 0), MAX_MANUAL_WORKERS),
            reserved_cores=min(max(int(self.reserved_cores), 0), 64),
            low_priority=bool(self.low_priority),
        )

    @property
    def is_auto(self) -> bool:
        """Tuning is switched on (the count is searched while a job runs)."""
        return self.mode == MODE_AUTO

    @property
    def is_manual(self) -> bool:
        return self.mode == MODE_MANUAL


DEFAULT_PERF_PREFERENCES = PerfPreferences()
