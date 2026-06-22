from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping


def _env_flag(name: str, environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(name, "")).strip().casefold() in {"1", "true", "yes", "on"}


DiagnosticsProfile = Literal["off", "normal", "debug", "deep"]
_VALID_PROFILES = {"off", "normal", "debug", "deep"}


def _normalize_profile(value: object, default: DiagnosticsProfile = "normal") -> DiagnosticsProfile:
    profile = str(value or "").strip().casefold()
    if profile in _VALID_PROFILES:
        return profile  # type: ignore[return-value]
    return default


def _profile_defaults(profile: DiagnosticsProfile) -> dict[str, object]:
    if profile == "off":
        return {
            "quiet_mode": True,
            "suppress_info_logs": True,
            "gui_log_enabled": False,
            "gui_log_min_level": logging.WARNING,
            "file_log_min_level": logging.WARNING,
            "export_diagnostic_events": False,
            "export_snapshot_interval_s": 60.0,
            "runtime_drift_probe_enabled": False,
            "session_stats_recording_enabled_by_default": False,
            "debug_timing_enabled": False,
            "deep_timing_enabled": False,
            "diagnostics_panel_enabled": False,
        }
    if profile == "debug":
        return {
            "quiet_mode": False,
            "suppress_info_logs": False,
            "gui_log_enabled": True,
            "gui_log_min_level": logging.DEBUG,
            "file_log_min_level": logging.INFO,
            "export_diagnostic_events": False,
            "export_snapshot_interval_s": 10.0,
            "runtime_drift_probe_enabled": True,
            "session_stats_recording_enabled_by_default": False,
            "debug_timing_enabled": True,
            "deep_timing_enabled": False,
            "diagnostics_panel_enabled": True,
        }
    if profile == "deep":
        return {
            "quiet_mode": False,
            "suppress_info_logs": False,
            "gui_log_enabled": True,
            "gui_log_min_level": logging.DEBUG,
            "file_log_min_level": logging.INFO,
            "export_diagnostic_events": True,
            "export_snapshot_interval_s": 2.5,
            "runtime_drift_probe_enabled": True,
            "session_stats_recording_enabled_by_default": True,
            "debug_timing_enabled": True,
            "deep_timing_enabled": True,
            "diagnostics_panel_enabled": True,
        }
    return {
        "quiet_mode": False,
        "suppress_info_logs": False,
        "gui_log_enabled": True,
        "gui_log_min_level": logging.INFO,
        "file_log_min_level": logging.INFO,
        "export_diagnostic_events": False,
        "export_snapshot_interval_s": 10.0,
        "runtime_drift_probe_enabled": True,
        "session_stats_recording_enabled_by_default": False,
        "debug_timing_enabled": False,
        "deep_timing_enabled": False,
        "diagnostics_panel_enabled": False,
    }


@dataclass(frozen=True, slots=True)
class DiagnosticsConfig:
    profile: DiagnosticsProfile = "normal"
    quiet_mode: bool = False
    suppress_info_logs: bool = False
    gui_log_enabled: bool = True
    gui_log_min_level: int = logging.INFO
    file_log_min_level: int = logging.INFO
    export_diagnostic_events: bool = False
    export_snapshot_interval_s: float = 10.0
    runtime_drift_probe_enabled: bool = True
    session_stats_recording_enabled_by_default: bool = False
    debug_timing_enabled: bool = False
    deep_timing_enabled: bool = False
    diagnostics_panel_enabled: bool = True

    @classmethod
    def for_profile(
        cls,
        profile: DiagnosticsProfile,
        *,
        export_override: bool | None = None,
    ) -> "DiagnosticsConfig":
        normalized_profile = _normalize_profile(profile)
        defaults = _profile_defaults(normalized_profile)
        export_diagnostic_events = bool(defaults["export_diagnostic_events"])
        if export_override is not None:
            export_diagnostic_events = bool(export_override)
        suppress_info_logs = int(defaults["file_log_min_level"]) > logging.INFO
        return cls(
            profile=normalized_profile,
            quiet_mode=bool(defaults["quiet_mode"]),
            suppress_info_logs=suppress_info_logs,
            gui_log_enabled=bool(defaults["gui_log_enabled"]),
            gui_log_min_level=int(defaults["gui_log_min_level"]),
            file_log_min_level=int(defaults["file_log_min_level"]),
            export_diagnostic_events=export_diagnostic_events,
            export_snapshot_interval_s=float(defaults["export_snapshot_interval_s"]),
            runtime_drift_probe_enabled=bool(defaults["runtime_drift_probe_enabled"]),
            session_stats_recording_enabled_by_default=bool(defaults["session_stats_recording_enabled_by_default"]),
            debug_timing_enabled=bool(defaults["debug_timing_enabled"]),
            deep_timing_enabled=bool(defaults["deep_timing_enabled"]),
            diagnostics_panel_enabled=bool(defaults["diagnostics_panel_enabled"]),
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DiagnosticsConfig":
        env = environ if environ is not None else os.environ
        profile = str(env.get("LSPR_DIAGNOSTICS_PROFILE", "")).strip().casefold()
        old_quiet = _env_flag("LSPR_QUIET_DIAGNOSTICS", env)
        old_file_info = _env_flag("LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS", env)
        if profile not in _VALID_PROFILES:
            if old_quiet:
                profile = "off"
            elif old_file_info:
                profile = "debug"
            else:
                profile = "normal"
        export_enabled = _env_flag("LSPR_ENABLE_DIAGNOSTIC_EXPORT", env)
        export_disabled = _env_flag("LSPR_DISABLE_DIAGNOSTIC_EXPORT", env)
        export_override: bool | None
        if export_disabled:
            export_override = False
        elif export_enabled:
            export_override = True
        elif profile == "deep":
            export_override = True
        elif profile == "off":
            export_override = False
        else:
            export_override = None
        return cls.for_profile(profile if profile in _VALID_PROFILES else "normal", export_override=export_override)

    @classmethod
    def from_window(cls, window: Any) -> "DiagnosticsConfig":
        diagnostics = getattr(window, "_diagnostics", None)
        if isinstance(diagnostics, cls):
            return diagnostics
        return cls(
            profile=_normalize_profile(getattr(window, "_diagnostics_profile", "normal")),
            quiet_mode=bool(getattr(window, "_quiet_diagnostics_mode", False)),
            suppress_info_logs=bool(getattr(window, "_suppress_diagnostic_info_logs", False)),
            gui_log_enabled=bool(getattr(window, "_gui_log_enabled", not getattr(window, "_quiet_diagnostics_mode", False))),
            gui_log_min_level=int(getattr(window, "_gui_log_min_level", logging.INFO)),
            file_log_min_level=int(getattr(window, "_file_log_min_level", logging.INFO)),
            export_diagnostic_events=bool(getattr(window, "_export_diagnostic_events", False)),
            export_snapshot_interval_s=float(getattr(window, "_diagnostic_snapshot_export_interval_s", 10.0)),
            runtime_drift_probe_enabled=bool(getattr(window, "_runtime_drift_probe_enabled", True)),
            session_stats_recording_enabled_by_default=bool(
                getattr(window, "_session_stats_recording_enabled_by_default", False)
            ),
            debug_timing_enabled=bool(getattr(window, "_debug_timing_enabled", False)),
            deep_timing_enabled=bool(getattr(window, "_deep_timing_enabled", False)),
            diagnostics_panel_enabled=bool(getattr(window, "_diagnostics_panel_enabled", True)),
        )

    def launch_flag_text(self) -> str:
        return (
            f"profile={self.profile} | "
            f"gui={'on' if self.gui_log_enabled else 'off'} | "
            f"file_info={'off' if self.suppress_info_logs else 'on'} | "
            f"jsonl={'on' if self.export_diagnostic_events else 'off'}"
        )

    def summary_lines(self) -> list[str]:
        return [
            f"Diagnostics profile: {self.profile}",
            f"GUI log: {'on' if self.gui_log_enabled else 'off'} | min={logging.getLevelName(int(self.gui_log_min_level))}",
            f"File log info: {'off' if self.suppress_info_logs else 'on'} | min={logging.getLevelName(int(self.file_log_min_level))}",
            f"Diagnostic export: {'on' if self.export_diagnostic_events else 'off'} | interval={float(self.export_snapshot_interval_s):.1f}s",
            f"Runtime drift probe: {'on' if self.runtime_drift_probe_enabled else 'off'}",
            f"Session stats default: {'on' if self.session_stats_recording_enabled_by_default else 'off'}",
        ]

    def startup_summary_text(self) -> str:
        return (
            f"Diagnostics profile: {self.profile} | "
            f"gui_log={'on' if self.gui_log_enabled else 'off'} | "
            f"jsonl_export={'on' if self.export_diagnostic_events else 'off'} | "
            f"stats_capture={'default' if self.session_stats_recording_enabled_by_default else 'manual'} | "
            f"drift_probe={'on' if self.runtime_drift_probe_enabled else 'off'}"
        )


def apply_diagnostic_info_filter(handler: logging.Handler, config: DiagnosticsConfig) -> None:
    min_level = int(config.file_log_min_level)
    handler.addFilter(lambda record, min_level=min_level: record.levelno >= min_level)
