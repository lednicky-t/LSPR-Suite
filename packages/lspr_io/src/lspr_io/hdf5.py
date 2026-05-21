from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from lspr_core import ExperimentPlan, ExperimentPlanStep, SuiteIdentity, summarize_experiment_plan
from .schema import (
    LSPR_FLOW_PLAN_FORMAT_NAME,
    LSPR_FLOW_PLAN_FORMAT_VERSION,
    LSPR_FLOW_PLAN_SCHEMA_NAME,
    LSPR_FLOW_PLAN_SCHEMA_VERSION,
    LSPR_MEASUREMENT_FORMAT_NAME,
    LSPR_MEASUREMENT_FORMAT_VERSION,
    LSPR_MEASUREMENT_FLOW_STATE_COLUMNS,
    LSPR_MEASUREMENT_PLAN_COLUMNS,
    LSPR_MEASUREMENT_SCHEMA_MAJOR,
    LSPR_MEASUREMENT_SCHEMA_MINOR,
    LSPR_MEASUREMENT_SCHEMA_NAME,
    LSPR_MEASUREMENT_SCHEMA_VERSION,
    LSPR_MEASUREMENT_SPECTRUM_COLUMNS,
    LSPR_SESSION_FORMAT_NAME,
    LSPR_SESSION_FORMAT_VERSION,
    LSPR_SESSION_SCHEMA_NAME,
    LSPR_SESSION_SCHEMA_VERSION,
)


def _iso_utc(value: datetime | str | None = None) -> str:
    if value is None:
        current = datetime.now(timezone.utc)
        return current.isoformat().replace("+00:00", "Z")
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text or _iso_utc()


def _string_array(values: list[str]) -> np.ndarray:
    return np.asarray(values, dtype=h5py.string_dtype(encoding="utf-8"))


@dataclass(slots=True)
class ExperimentPlanRowTable:
    columns: list[str]
    rows: list[list[str]]
def write_measurement_root_metadata(
    handle: h5py.File,
    *,
    schema_name: str,
    schema_version: str,
    schema_major: int,
    schema_minor: int,
    format_name: str,
    format_version: int,
    app_name: str,
    app_version: str,
    created_by: str,
    created_at_utc: str | None = None,
    started_at_utc: datetime | str,
    experiment_name: str = "",
) -> None:
    handle.attrs["schema_name"] = schema_name
    handle.attrs["schema_version"] = schema_version
    handle.attrs["schema_major"] = schema_major
    handle.attrs["schema_minor"] = schema_minor
    handle.attrs["format_name"] = format_name
    handle.attrs["format_version"] = format_version
    handle.attrs["created_by"] = created_by
    handle.attrs["created_at_utc"] = _iso_utc(created_at_utc)
    handle.attrs["started_at_utc"] = _iso_utc(started_at_utc)
    handle.attrs["app_name"] = app_name
    handle.attrs["app_version"] = app_version
    handle.attrs["experiment_name"] = str(experiment_name or "")


def write_session_metadata(
    group: h5py.Group,
    *,
    identity: SuiteIdentity | None = None,
    schema_name: str = "lspr_session",
    schema_version: int = 1,
    experiment_plan: ExperimentPlan | None = None,
    flow_plan: ExperimentPlan | None = None,
    extra_attrs: dict[str, Any] | None = None,
) -> None:
    group.attrs["schema_name"] = schema_name
    group.attrs["schema_version"] = schema_version
    if identity is not None:
        group.attrs["app_name"] = identity.app_name
        group.attrs["app_version"] = identity.app_version
        group.attrs["format_name"] = identity.format_name
        group.attrs["format_version"] = identity.format_version
        group.attrs["created_at_utc"] = identity.created_at_utc
    plan = experiment_plan if experiment_plan is not None else flow_plan
    if plan is not None:
        summary = summarize_experiment_plan(plan)
        group.attrs["experiment_plan_steps"] = int(summary.step_count)
        group.attrs["experiment_plan_total_duration_s"] = float(summary.total_duration_s)
    for key, value in (extra_attrs or {}).items():
        group.attrs[key] = value


def read_root_metadata(handle: h5py.File) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in handle.attrs.items():
        result[key] = value.decode("utf-8") if isinstance(value, bytes) else value
    return result


def build_experiment_plan_row_table(
    plan: ExperimentPlan,
    *,
    channel_count: int = 0,
) -> ExperimentPlanRowTable:
    columns = ["step", "start_s", "end_s", "duration_s", "color", "comment"]
    if channel_count > 0:
        for index in range(channel_count):
            columns.extend([f"ch{index + 1}_flow_ul_min", f"ch{index + 1}_direction"])
    rows: list[list[str]] = []
    for step in plan.ordered():
        row = [
            str(step.id),
            f"{float(step.start_s):g}",
            f"{float(step.end_s):g}",
            f"{max(float(step.duration_s), 0.0):g}",
            str(step.color or ""),
            str(step.comment or ""),
        ]
        if channel_count > 0:
            channels = step.devices.get("channels", []) if isinstance(step.devices, dict) else []
            for index in range(channel_count):
                channel = channels[index] if index < len(channels) and isinstance(channels[index], dict) else {}
                row.extend(
                    [
                        str(channel.get("flow_ul_min", "")),
                        str(channel.get("direction", "")),
                    ]
                )
        rows.append(row)
    return ExperimentPlanRowTable(columns=columns, rows=rows)
def build_legacy_experiment_plan_row_table(
    plan: ExperimentPlan,
    *,
    tube_mm_by_channel: list[float] | None = None,
    active_channel_count: int = 4,
    hdf5_channel_count: int = 6,
) -> ExperimentPlanRowTable:
    tube_mm_by_channel = (tube_mm_by_channel or [])[:active_channel_count]
    if len(tube_mm_by_channel) < active_channel_count:
        tube_mm_by_channel = tube_mm_by_channel + [0.25] * (active_channel_count - len(tube_mm_by_channel))

    columns = [
        "step",
        "duration_s",
        "start_s",
        "end_s",
        "color",
        "valve",
        "switch_position",
        "description",
    ]
    for index in range(hdf5_channel_count):
        columns.extend(
            [
                f"ch{index + 1}_flow_ul_min",
                f"ch{index + 1}_direction",
                f"ch{index + 1}_tube_mm",
            ]
        )

    rows: list[list[str]] = []
    for step in plan.ordered():
        devices = step.devices if isinstance(step.devices, dict) else {}
        channels = devices.get("channels", []) if isinstance(devices.get("channels", []), list) else []
        row = [
            str(step.id),
            f"{max(float(step.duration_s), 0.0):g}",
            f"{float(step.start_s):g}",
            f"{float(step.end_s):g}",
            str(step.color or ""),
            str(devices.get("valve", "")),
            str(int(devices.get("switch_position", 1) or 1)),
            str(step.comment or ""),
        ]
        for index in range(hdf5_channel_count):
            if index < active_channel_count:
                channel = channels[index] if index < len(channels) and isinstance(channels[index], dict) else {}
                row.extend(
                    [
                        f"{max(float(channel.get('flow_ul_min', 0.0)), 0.0):g}",
                        str(channel.get("direction", "OFF") or "OFF"),
                        f"{float(tube_mm_by_channel[index]):.2f}",
                    ]
                )
            else:
                row.extend(["", "", ""])
        rows.append(row)
    return ExperimentPlanRowTable(columns=columns, rows=rows)
def standard_measurement_metadata(
    *,
    created_by: str,
    started_at_utc: datetime | str,
    app_name: str,
    app_version: str,
    experiment_name: str = "",
) -> dict[str, Any]:
    return {
        "schema_name": LSPR_MEASUREMENT_SCHEMA_NAME,
        "schema_version": LSPR_MEASUREMENT_SCHEMA_VERSION,
        "schema_major": LSPR_MEASUREMENT_SCHEMA_MAJOR,
        "schema_minor": LSPR_MEASUREMENT_SCHEMA_MINOR,
        "format_name": LSPR_MEASUREMENT_FORMAT_NAME,
        "format_version": LSPR_MEASUREMENT_FORMAT_VERSION,
        "created_by": created_by,
        "created_at_utc": _iso_utc(),
        "started_at_utc": _iso_utc(started_at_utc),
        "app_name": app_name,
        "app_version": app_version,
        "experiment_name": str(experiment_name or ""),
    }


def standard_session_identity(*, app_name: str, app_version: str) -> SuiteIdentity:
    return SuiteIdentity(
        app_name=app_name,
        app_version=app_version,
        format_name=LSPR_SESSION_FORMAT_NAME,
        format_version=LSPR_SESSION_FORMAT_VERSION,
    )


def write_xy_csv(
    path: Path,
    *,
    x_label: str,
    x_values: np.ndarray,
    y_label: str,
    y_values: np.ndarray,
    metadata: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        for key, value in (metadata or {}).items():
            writer.writerow([f"# {key}", value])
        writer.writerow([x_label, y_label])
        for x_value, y_value in zip(np.asarray(x_values), np.asarray(y_values)):
            writer.writerow([f"{float(x_value):.6f}", f"{float(y_value):.10f}"])
