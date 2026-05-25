from __future__ import annotations

import getpass
from dataclasses import dataclass
import csv
from datetime import datetime, timezone
from pathlib import Path
import socket
from typing import Any

import h5py
import numpy as np

from lspr_core import ExperimentPlan, ExperimentPlanStep, SuiteIdentity, summarize_experiment_plan
from .schema import (
    LSPR_FLOW_PLAN_FORMAT_NAME,
    LSPR_FLOW_PLAN_FORMAT_VERSION,
    LSPR_FLOW_PLAN_SCHEMA_NAME,
    LSPR_FLOW_PLAN_SCHEMA_VERSION,
    LSPR_EXPERIMENT_PLAN_DATASET_NAME,
    LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME,
    LSPR_MEASUREMENT_FORMAT_NAME,
    LSPR_MEASUREMENT_FORMAT_VERSION,
    LSPR_MEASUREMENT_FLOW_EVENTS_DATASET_NAME,
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


@dataclass(slots=True)
class MeasurementFileValidation:
    is_valid: bool
    errors: list[str]
    warnings: list[str]


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


def write_measurement_manifest_metadata(
    group: h5py.Group,
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
    storage_compression_enabled: bool = False,
    storage_compression_filter: str = "none",
    storage_compression_level: int = 0,
    extra_attrs: dict[str, Any] | None = None,
) -> None:
    write_measurement_root_metadata(
        group,
        schema_name=schema_name,
        schema_version=schema_version,
        schema_major=schema_major,
        schema_minor=schema_minor,
        format_name=format_name,
        format_version=format_version,
        app_name=app_name,
        app_version=app_version,
        created_by=created_by,
        created_at_utc=created_at_utc,
        started_at_utc=started_at_utc,
        experiment_name=experiment_name,
    )
    group.attrs["export_host"] = socket.gethostname()
    try:
        group.attrs["export_user"] = getpass.getuser()
    except Exception:
        group.attrs["export_user"] = ""
    group.attrs["storage_compression_enabled"] = bool(storage_compression_enabled)
    group.attrs["storage_compression_filter"] = str(storage_compression_filter or "none")
    group.attrs["storage_compression_level"] = int(storage_compression_level)
    group.attrs["description"] = "Human-readable export manifest for the measurement file."
    group.attrs["manifest_kind"] = str((extra_attrs or {}).get("manifest_kind", "measurement"))
    for key, value in (extra_attrs or {}).items():
        group.attrs[key] = value


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


def validate_measurement_metadata(metadata: dict[str, Any]) -> MeasurementFileValidation:
    errors: list[str] = []
    warnings: list[str] = []

    schema_name = str(metadata.get("schema_name", "") or "")
    if schema_name != LSPR_MEASUREMENT_SCHEMA_NAME:
        errors.append(f"Unexpected measurement schema name: {schema_name!r}")

    version_text = str(metadata.get("schema_version", "") or "").strip()
    try:
        major_text, minor_text = version_text.split(".", 1)
        major = int(major_text)
        minor = int(minor_text)
    except Exception:
        errors.append(f"Invalid measurement schema version: {version_text!r}")
        major = None
        minor = None

    if major is not None:
        if major > LSPR_MEASUREMENT_SCHEMA_MAJOR:
            errors.append(
                f"Measurement schema major version {major} is newer than supported {LSPR_MEASUREMENT_SCHEMA_MAJOR}."
            )
        elif major < LSPR_MEASUREMENT_SCHEMA_MAJOR:
            warnings.append(
                f"Measurement schema major version {major} is older than current {LSPR_MEASUREMENT_SCHEMA_MAJOR}."
            )
        elif minor is not None and minor > LSPR_MEASUREMENT_SCHEMA_MINOR:
            warnings.append(
                f"Measurement schema minor version {major}.{minor} is newer than current "
                f"{LSPR_MEASUREMENT_SCHEMA_MAJOR}.{LSPR_MEASUREMENT_SCHEMA_MINOR}."
            )

    format_name = str(metadata.get("format_name", "") or "")
    if format_name and format_name != LSPR_MEASUREMENT_FORMAT_NAME:
        warnings.append(f"Measurement format name differs from current export format: {format_name!r}")

    format_version_value = metadata.get("format_version", None)
    if format_version_value is None:
        warnings.append("Missing root attribute: format_version")
    else:
        try:
            format_version = int(format_version_value)
        except Exception:
            warnings.append(f"Invalid measurement format version: {format_version_value!r}")
        else:
            if format_version > LSPR_MEASUREMENT_FORMAT_VERSION:
                errors.append(
                    f"Measurement format version {format_version} is newer than supported "
                    f"{LSPR_MEASUREMENT_FORMAT_VERSION}."
                )
            elif format_version < LSPR_MEASUREMENT_FORMAT_VERSION:
                warnings.append(
                    f"Measurement format version {format_version} is older than current "
                    f"{LSPR_MEASUREMENT_FORMAT_VERSION}."
                )

    if "started_at_utc" not in metadata:
        errors.append("Missing required root attribute: started_at_utc")
    if "created_at_utc" not in metadata:
        warnings.append("Missing root attribute: created_at_utc")

    return MeasurementFileValidation(is_valid=not errors, errors=errors, warnings=warnings)


def _validate_required_group(handle: h5py.Group | h5py.File, path: str) -> list[str]:
    if path not in handle:
        return [f"Missing required group or dataset: {path}"]
    return []


def validate_measurement_file(handle: h5py.File) -> MeasurementFileValidation:
    result = validate_measurement_metadata(read_root_metadata(handle))
    result.errors.extend(_validate_required_group(handle, "data"))
    result.errors.extend(_validate_required_group(handle, "metadata"))
    result.errors.extend(_validate_required_group(handle, "axes"))
    has_plans = "plans" in handle
    has_runs = "runs" in handle

    if "manifest" not in handle:
        result.warnings.append("Missing /manifest group.")
    else:
        manifest_validation = validate_measurement_metadata(read_root_metadata(handle["manifest"]))
        result.warnings.extend(f"Manifest: {warning}" for warning in manifest_validation.warnings)
        if manifest_validation.errors:
            result.warnings.extend(f"Manifest: {error}" for error in manifest_validation.errors)
        manifest_attrs = handle["manifest"].attrs
        if "manifest_kind" not in manifest_attrs:
            result.warnings.append("Manifest is missing manifest_kind metadata.")
        elif str(manifest_attrs.get("manifest_kind", "") or "") != "measurement":
            result.warnings.append(
                f"Unexpected manifest_kind value: {manifest_attrs.get('manifest_kind', '')!r}"
            )

    if "data" in handle:
        data_group = handle["data"]
        for dataset_name in ("wavelengths", "raw_spectra_extinction", "time_series", "peak_position_nm"):
            if dataset_name not in data_group:
                result.warnings.append(f"Missing expected data dataset: data/{dataset_name}")
    if "axes" in handle and "wavelengths_nm" not in handle["axes"]:
        result.warnings.append("Missing expected axes dataset: axes/wavelengths_nm")
    if has_plans:
        plans_group = handle["plans"]
        if LSPR_EXPERIMENT_PLAN_DATASET_NAME not in plans_group:
            result.warnings.append(f"Missing expected plans dataset: plans/{LSPR_EXPERIMENT_PLAN_DATASET_NAME}")
        if LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME not in plans_group:
            result.warnings.append(
                f"Missing expected plans dataset: plans/{LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME}"
            )
    else:
        result.warnings.append("Missing /plans group.")
    if has_runs:
        runs_group = handle["runs"]
        if LSPR_MEASUREMENT_FLOW_EVENTS_DATASET_NAME not in runs_group:
            if "flow_state" in runs_group:
                result.warnings.append(
                    "Legacy flow state storage detected at runs/flow_state; preferred location is "
                    f"runs/{LSPR_MEASUREMENT_FLOW_EVENTS_DATASET_NAME}."
                )
            else:
                result.warnings.append(
                    f"Missing expected runs dataset: runs/{LSPR_MEASUREMENT_FLOW_EVENTS_DATASET_NAME}"
                )
    else:
        result.warnings.append("Missing /runs group.")
    if "metadata" in handle:
        metadata_group = handle["metadata"]
        legacy_plan_present = LSPR_EXPERIMENT_PLAN_DATASET_NAME in metadata_group
        legacy_tmp_present = LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME in metadata_group
        if not has_plans and not legacy_plan_present:
            result.warnings.append(f"Missing expected metadata dataset: metadata/{LSPR_EXPERIMENT_PLAN_DATASET_NAME}")
        elif legacy_plan_present and not has_plans:
            result.warnings.append(
                f"Legacy plan storage detected at metadata/{LSPR_EXPERIMENT_PLAN_DATASET_NAME}; "
                f"preferred location is plans/{LSPR_EXPERIMENT_PLAN_DATASET_NAME}."
            )
        if legacy_tmp_present and not has_plans:
            result.warnings.append(
                f"Legacy plan staging storage detected at metadata/{LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME}; "
                f"preferred location is plans/{LSPR_EXPERIMENT_PLAN_TMP_DATASET_NAME}."
            )
    if "flow" in handle and not has_runs:
        flow_group = handle["flow"]
        if "state" in flow_group:
            result.warnings.append(
                "Legacy flow state storage detected at flow/state; preferred location is "
                f"runs/{LSPR_MEASUREMENT_FLOW_EVENTS_DATASET_NAME}."
            )
    result.is_valid = not result.errors
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
