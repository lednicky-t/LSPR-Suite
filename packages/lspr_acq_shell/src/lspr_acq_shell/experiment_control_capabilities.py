"""Capability flags for the experiment-control panel (V49's "required
capability split" - see
apps/sLSPR/acq/docs/experiment-control/CODEX_EXPERIMENT_CONTROL_REUSE_SPLIT_V49.md).

Extracted from singleLSPR Acquisition's `gui/experiment_control_capabilities.py`
(Phase 1, 2026-08-08) as the first small, genuinely-ready piece of the V49
migration - a plain frozen dataclass with zero window/Qt coupling. The rest of
V49 (the shared visualization panel, a real window-decoupled controller, the
IO module, ~11,000 lines of GUI code across `experiment_control_window.py`
and its satellite files) is NOT part of this extraction - it's un-implemented
planning, not a near-done split, and is being tracked as its own future
effort rather than folded into this Phase 1 checklist item. See the
2026-08-08 build-log entry.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExperimentControlCapabilities:
    devices_enabled: bool = True
    runtime_control_enabled: bool = True
    plan_import_export_enabled: bool = True
    show_runtime_buttons: bool = True
    show_device_columns: bool = True
    show_device_status_strip: bool = True
    show_step_navigation_controls: bool = True

    @classmethod
    def acquisition(cls) -> "ExperimentControlCapabilities":
        return cls(
            devices_enabled=True,
            runtime_control_enabled=True,
            plan_import_export_enabled=True,
            show_runtime_buttons=True,
            show_device_columns=True,
            show_device_status_strip=True,
            show_step_navigation_controls=True,
        )

    @classmethod
    def evaluation(cls) -> "ExperimentControlCapabilities":
        return cls(
            devices_enabled=False,
            runtime_control_enabled=False,
            plan_import_export_enabled=True,
            show_runtime_buttons=False,
            show_device_columns=False,
            show_device_status_strip=False,
            show_step_navigation_controls=False,
        )
