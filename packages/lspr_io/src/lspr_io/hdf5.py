from __future__ import annotations

import json
import getpass
from dataclasses import dataclass
import csv
from datetime import datetime, timezone
from pathlib import Path
import socket
from typing import Any

import h5py
import numpy as np

from lspr_core import ExperimentPlan, SuiteIdentity, summarize_experiment_plan
from .schema import (
    LSPR_EXPERIMENT_PLAN_DATASET_NAME,
    LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME,
    LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME,
    LSPR_MEASUREMENT_FORMAT_NAME,
    LSPR_MEASUREMENT_FORMAT_VERSION,
    LSPR_MEASUREMENT_SCHEMA_MAJOR,
    LSPR_MEASUREMENT_SCHEMA_MINOR,
    LSPR_MEASUREMENT_SCHEMA_NAME,
    LSPR_MEASUREMENT_SCHEMA_VERSION,
    LSPR_MEASUREMENT_RUNTIME_DATASET_NAME,
    LSPR_MEASUREMENT_RUNTIME_TIMESTAMP_UTC_COLUMN,
    LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME,
    LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME,
    LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME,
    LSPR_PROCESSED_METRICS_ACQUIRED_AT_UNIX_MS_DATASET_NAME,
    LSPR_PROCESSED_METRICS_CONFIG_GROUP_NAME,
    LSPR_PROCESSED_METRICS_FORMAT_NAME,
    LSPR_PROCESSED_METRICS_FORMAT_VERSION,
    LSPR_PROCESSED_METRICS_GROUP_NAME,
    LSPR_PROCESSED_METRICS_SCHEMA_NAME,
    LSPR_PROCESSED_METRICS_SCHEMA_VERSION,
    LSPR_PROCESSING_SETTINGS_SCHEMA_NAME,
    LSPR_PROCESSING_SETTINGS_SCHEMA_VERSION,
    LSPR_SESSION_FORMAT_NAME,
    LSPR_SESSION_FORMAT_VERSION,
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


def _read_string_table_dataset(dataset: h5py.Dataset | None) -> tuple[list[str], list[list[str]]]:
    if dataset is None:
        return [], []
    columns: list[str] = []
    if hasattr(dataset, "attrs") and "columns" in dataset.attrs:
        columns = [str(value.decode("utf-8") if isinstance(value, bytes) else value) for value in dataset.attrs["columns"]]
    if not getattr(dataset, "shape", None) or len(dataset.shape) != 2:
        return columns, []
    rows: list[list[str]] = []
    for row in dataset[...]:
        rows.append([str(cell.decode("utf-8") if isinstance(cell, bytes) else cell) for cell in row])
    return columns, rows


def _write_processing_settings_json(config_group: h5py.Group, payload: dict[str, Any]) -> None:
    if "processing_settings_json" in config_group:
        del config_group["processing_settings_json"]
    json_text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    dataset = config_group.create_dataset(
        "processing_settings_json",
        data=np.asarray(json_text, dtype=h5py.string_dtype(encoding="utf-8")),
    )
    dataset.attrs["description"] = "Human-readable processing settings used to derive /processed/metrics."


def write_processed_metrics_metadata(
    metrics_group: h5py.Group,
    *,
    schema_name: str = LSPR_PROCESSED_METRICS_SCHEMA_NAME,
    schema_version: str = LSPR_PROCESSED_METRICS_SCHEMA_VERSION,
    format_name: str = LSPR_PROCESSED_METRICS_FORMAT_NAME,
    format_version: int = LSPR_PROCESSED_METRICS_FORMAT_VERSION,
    settings_schema_name: str = LSPR_PROCESSING_SETTINGS_SCHEMA_NAME,
    settings_schema_version: str = LSPR_PROCESSING_SETTINGS_SCHEMA_VERSION,
) -> None:
    metrics_group.attrs["schema_name"] = schema_name
    metrics_group.attrs["schema_version"] = schema_version
    major_text, minor_text = str(schema_version).split(".", 1)
    metrics_group.attrs["schema_major"] = int(major_text)
    metrics_group.attrs["schema_minor"] = int(minor_text)
    metrics_group.attrs["format_name"] = format_name
    metrics_group.attrs["format_version"] = int(format_version)
    metrics_group.attrs["settings_schema_name"] = settings_schema_name
    metrics_group.attrs["settings_schema_version"] = settings_schema_version
    metrics_group.attrs["description"] = "Derived metrics and the processing configuration used to compute them."


def write_processing_settings_metadata(
    metrics_group: h5py.Group,
    settings: dict[str, Any],
    *,
    schema_name: str = LSPR_PROCESSING_SETTINGS_SCHEMA_NAME,
    schema_version: str = LSPR_PROCESSING_SETTINGS_SCHEMA_VERSION,
    format_name: str = "processing_settings",
    format_version: int = 1,
) -> None:
    config_group = metrics_group.require_group("config")
    config_group.attrs["schema_name"] = schema_name
    config_group.attrs["schema_version"] = schema_version
    major_text, minor_text = str(schema_version).split(".", 1)
    config_group.attrs["schema_major"] = int(major_text)
    config_group.attrs["schema_minor"] = int(minor_text)
    config_group.attrs["format_name"] = format_name
    config_group.attrs["format_version"] = int(format_version)
    config_group.attrs["description"] = "Human-readable processing settings used to derive the metrics."
    _write_processing_settings_json(config_group, settings)


def read_processing_settings_metadata(metrics_group: h5py.Group) -> dict[str, Any] | None:
    config_group = metrics_group.get("config")
    if config_group is None or "processing_settings_json" not in config_group:
        return None
    dataset = config_group["processing_settings_json"]
    raw = dataset[()]
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def validate_measurement_file(handle: h5py.File) -> MeasurementFileValidation:
    result = validate_measurement_metadata(read_root_metadata(handle))
    result.errors.extend(_validate_required_group(handle, "data"))
    result.errors.extend(_validate_required_group(handle, "metadata"))

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
        if "time_series" not in data_group:
            result.errors.append("Missing expected data dataset: data/time_series")
        if LSPR_MEASUREMENT_RUNTIME_DATASET_NAME not in data_group:
            legacy_runtime_present = False
            if "runs" in handle and ("flow_events" in handle["runs"] or "flow_state" in handle["runs"]):
                legacy_runtime_present = True
            if not legacy_runtime_present and "flow" in handle and "state" in handle["flow"]:
                legacy_runtime_present = True
            if legacy_runtime_present:
                result.warnings.append(
                    "Legacy runtime storage detected in runs/flow_events or flow/state; preferred location is "
                    f"data/{LSPR_MEASUREMENT_RUNTIME_DATASET_NAME}."
                )
            else:
                result.errors.append(
                    f"Missing expected data dataset: data/{LSPR_MEASUREMENT_RUNTIME_DATASET_NAME}"
                )
        else:
            runtime_dataset = data_group.get(LSPR_MEASUREMENT_RUNTIME_DATASET_NAME)
            if runtime_dataset is not None:
                runtime_columns, _runtime_rows = _read_string_table_dataset(runtime_dataset)
                if runtime_columns and LSPR_MEASUREMENT_RUNTIME_TIMESTAMP_UTC_COLUMN not in runtime_columns:
                    result.warnings.append(
                        "Missing absolute runtime timestamp column: "
                        f"data/{LSPR_MEASUREMENT_RUNTIME_DATASET_NAME} column {LSPR_MEASUREMENT_RUNTIME_TIMESTAMP_UTC_COLUMN}"
                    )
        if "wavelengths_nm" not in data_group:
            if "axes" in handle and "wavelengths_nm" in handle["axes"]:
                result.warnings.append(
                    "Legacy wavelength axis detected at axes/wavelengths_nm; preferred location is "
                    f"data/{LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME}."
                )
            else:
                result.errors.append(
                    f"Missing expected data dataset: data/{LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME}"
                )
        if "peak_position_nm" in data_group:
            result.warnings.append(
                "Legacy peak position dataset detected at data/peak_position_nm; canonical processed result is "
                "stored under processed/metrics."
            )
        if "wavelengths" in data_group:
            result.warnings.append(
                "Legacy duplicate wavelength axis detected at data/wavelengths; canonical location is "
                f"data/{LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME}."
            )
        if "raw_spectra_extinction" in data_group:
            result.warnings.append(
                "Legacy duplicate sample spectra detected at data/raw_spectra_extinction; canonical location is "
                "data/spectra/sample/intensity."
            )
    else:
        result.warnings.append("Missing /data group.")
    if "data" in handle:
        spectra_group = handle["data"].get("spectra")
        if spectra_group is None:
            if "spectra" in handle:
                result.warnings.append("Legacy spectra group detected at /spectra; preferred location is data/spectra.")
            else:
                result.errors.append("Missing expected group: data/spectra")
        else:
            for spectrum_name in ("sample", "dark", "reference"):
                if spectrum_name not in spectra_group:
                    result.errors.append(f"Missing expected spectra group: data/spectra/{spectrum_name}")
                    continue
                spectrum_group = spectra_group[spectrum_name]
                if "intensity" not in spectrum_group:
                    result.errors.append(f"Missing expected spectra dataset: data/spectra/{spectrum_name}/intensity")
            sample_group = spectra_group.get("sample")
            if sample_group is not None and "dark_index" not in sample_group:
                result.warnings.append("Missing expected sample spectrum dataset: data/spectra/sample/dark_index")
            if sample_group is not None and "reference_index" not in sample_group:
                result.warnings.append(
                    "Missing expected sample spectrum dataset: data/spectra/sample/reference_index"
                )
    if "metadata" in handle:
        metadata_group = handle["metadata"]
        plan_present = LSPR_EXPERIMENT_PLAN_DATASET_NAME in metadata_group
        if not plan_present:
            if "plans" in handle and LSPR_EXPERIMENT_PLAN_DATASET_NAME in handle["plans"]:
                result.warnings.append(
                    f"Legacy plan storage detected at plans/{LSPR_EXPERIMENT_PLAN_DATASET_NAME}; "
                    f"preferred location is metadata/{LSPR_EXPERIMENT_PLAN_DATASET_NAME}."
                )
            else:
                result.errors.append(f"Missing expected metadata dataset: metadata/{LSPR_EXPERIMENT_PLAN_DATASET_NAME}")
        assignment_tables_group = metadata_group.get(LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME)
        if assignment_tables_group is None:
            result.warnings.append(
                "Missing /metadata/assignment_tables group; valve labels, colors, and palette entries will not round-trip."
            )
        else:
            for dataset_name in (
                LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME,
                LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME,
                LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME,
            ):
                dataset = assignment_tables_group.get(dataset_name)
                if dataset is None:
                    result.warnings.append(
                        f"Missing assignment table: metadata/{LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME}/{dataset_name}"
                    )
                    continue
                columns, _rows = _read_string_table_dataset(dataset)
                if not columns:
                    result.warnings.append(
                        f"Assignment table metadata/{LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME}/{dataset_name} is missing column metadata."
                    )
        if "switch_solution_map" in metadata_group:
            result.warnings.append(
                "Legacy switch_solution_map detected at /metadata/switch_solution_map; canonical location is "
                f"metadata/{LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME}/{LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME}."
            )
        if "plans" in handle:
            result.warnings.append("Legacy /plans group detected; canonical plan tables live in /metadata.")
    if "processed" in handle:
        processed_group = handle["processed"]
        metrics_group = processed_group.get(LSPR_PROCESSED_METRICS_GROUP_NAME)
        if metrics_group is None:
            result.warnings.append(
                "Missing /processed/metrics group; derived metrics and processing settings will not round-trip."
            )
        else:
            for attr_name in ("schema_name", "schema_version", "format_name", "format_version"):
                if attr_name not in metrics_group.attrs:
                    result.warnings.append(
                        f"Missing processed metrics metadata attribute: processed/metrics attr {attr_name}"
                    )
            if LSPR_PROCESSED_METRICS_ACQUIRED_AT_UNIX_MS_DATASET_NAME not in metrics_group:
                result.warnings.append(
                    "Missing processed metrics timestamp dataset: "
                    f"processed/metrics/{LSPR_PROCESSED_METRICS_ACQUIRED_AT_UNIX_MS_DATASET_NAME}"
                )
            config_group = metrics_group.get(LSPR_PROCESSED_METRICS_CONFIG_GROUP_NAME)
            if config_group is None:
                result.warnings.append(
                    "Missing /processed/metrics/config group; processing settings will not round-trip."
                )
            else:
                for attr_name in ("schema_name", "schema_version", "format_name", "format_version"):
                    if attr_name not in config_group.attrs:
                        result.warnings.append(
                            f"Missing processing settings metadata attribute: processed/metrics/config attr {attr_name}"
                        )
                if "processing_settings_json" not in config_group:
                    result.warnings.append(
                        "Missing processing settings payload: processed/metrics/config/processing_settings_json"
                    )
                elif read_processing_settings_metadata(metrics_group) is None:
                    result.warnings.append(
                        "Unable to decode processed metrics processing settings JSON."
                    )
    else:
        result.warnings.append("Missing /processed group; derived metrics will not round-trip.")
    if "runs" in handle:
        result.warnings.append(
            "Legacy /runs group detected; canonical runtime log lives in data/"
            f"{LSPR_MEASUREMENT_RUNTIME_DATASET_NAME}."
        )
    if "axes" in handle:
        result.warnings.append("Legacy /axes group detected; canonical wavelength axis lives in data/wavelengths_nm.")
    if "spectra" in handle and "data" in handle:
        result.warnings.append("Legacy /spectra group detected; canonical spectra live under data/spectra.")
    if "flow" in handle:
        flow_group = handle["flow"]
        if "state" in flow_group:
            result.warnings.append(
                "Legacy flow state storage detected at flow/state; preferred location is "
                f"data/{LSPR_MEASUREMENT_RUNTIME_DATASET_NAME}."
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
        "show_on_pump_display",
        "highlight_pump_display_limit",
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
            "1" if devices.get("show_on_pump_display") else "0",
            "1" if devices.get("highlight_pump_display_limit") else "0",
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
