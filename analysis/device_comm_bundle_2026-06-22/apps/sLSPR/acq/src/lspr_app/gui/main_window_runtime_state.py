from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UiRefreshState:
    summary_dirty: bool = False
    telemetry_dirty: bool = False
    live_estimate_dirty: bool = False
    stats_dirty: bool = False
    metric_plot_dirty: bool = False
    session_stats_dirty: bool = False
    pending_metric_label: str | None = None

    @property
    def trace_plot_dirty(self) -> bool:
        return self.metric_plot_dirty

    @trace_plot_dirty.setter
    def trace_plot_dirty(self, value: bool) -> None:
        self.metric_plot_dirty = bool(value)

    @property
    def pending_trace_label(self) -> str | None:
        return self.pending_metric_label

    @pending_trace_label.setter
    def pending_trace_label(self, value: str | None) -> None:
        self.pending_metric_label = value
