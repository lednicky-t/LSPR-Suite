from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from lspr_app.domain.models import ProcessingSettings
from lspr_app import __version__ as APP_VERSION
from lspr_io import (
    read_processing_settings_metadata,
    standard_measurement_metadata,
    write_measurement_root_metadata,
    write_processed_metrics_metadata,
    write_processing_settings_metadata,
)


DEFAULT_CONFIG_PATH = Path.cwd() / "lspr_settings.json"


def _load_payload(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _coerce_processing_settings(raw: object) -> ProcessingSettings:
    defaults = asdict(ProcessingSettings())
    if isinstance(raw, dict):
        if "peak_tracking_mode" in raw and "spectrum_tracking_mode" not in raw:
            raw = dict(raw)
            raw["spectrum_tracking_mode"] = raw["peak_tracking_mode"]
        defaults.update({key: value for key, value in raw.items() if key in defaults})
    if defaults.get("baseline_method") == "asls":
        defaults["baseline_method"] = "linear"
    if defaults.get("crop_method") not in {"fixed_width", "threshold"}:
        defaults["crop_method"] = "fixed_width"
    defaults["crop_fraction"] = float(min(max(defaults.get("crop_fraction", 0.7), 0.05), 0.95))
    if defaults.get("fit_method") not in {"none", "poly", "gaussian"}:
        defaults["fit_method"] = "none"
    if defaults.get("analysis_resolution_nm") is None:
        defaults["analysis_resolution_nm"] = 0.001
    defaults["analysis_resolution_nm"] = float(
        min(max(defaults.get("analysis_resolution_nm", 0.001), 0.000001), 0.1)
    )
    defaults["trace_noise_window_s"] = float(min(max(defaults.get("trace_noise_window_s", 10.0), 0.5), 600.0))
    trace_metrics = defaults.get("trace_metrics")
    if not isinstance(trace_metrics, list):
        defaults["trace_metrics"] = ["smoothed_max", "centroid"]
    else:
        allowed = {"smoothed_max", "poly_max", "gaussian_center", "centroid"}
        filtered = [item for item in trace_metrics if item in allowed]
        defaults["trace_metrics"] = filtered or ["smoothed_max", "centroid"]
    return ProcessingSettings(**defaults)


def save_processing_settings(settings: ProcessingSettings, path: Path = DEFAULT_CONFIG_PATH) -> None:
    payload = _load_payload(path)
    payload["processing"] = asdict(settings)
    _write_payload(payload, path)


def save_processing_settings_to_hdf5(settings: ProcessingSettings, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as handle:
        write_measurement_root_metadata(
            handle,
            **standard_measurement_metadata(
                created_by="LSPR Acquisition",
                started_at_utc=datetime.now(timezone.utc),
                app_name="LSPR Acquisition",
                app_version=APP_VERSION,
                experiment_name="",
            ),
        )
        metadata = handle.create_group("metadata")
        processed = handle.create_group("processed")
        metrics_group = processed.create_group("metrics")
        write_processed_metrics_metadata(metrics_group)
        write_processing_settings_metadata(metrics_group, asdict(settings))

        for key, value in (
            ("processing_range_min_nm", settings.wavelength_min_nm),
            ("processing_range_max_nm", settings.wavelength_max_nm),
            ("processing_baseline_method", settings.baseline_method),
            ("processing_smoothing_method", settings.smoothing_method),
            ("processing_smoothing_window", settings.smoothing_window),
            ("processing_temporal_smoothing", settings.temporal_smoothing),
            ("processing_crop_method", settings.crop_method),
            ("processing_crop_fraction", settings.crop_fraction),
            ("processing_fit_method", settings.fit_method),
            ("processing_polynomial_order", settings.polynomial_order),
            ("processing_fit_window_width_nm", settings.fit_window_width_nm),
            ("processing_analysis_resolution_nm", settings.analysis_resolution_nm),
            ("spectrum_tracking_mode", settings.spectrum_tracking_mode),
            ("peak_tracking_mode", settings.spectrum_tracking_mode),
            ("processing_trace_noise_window_s", settings.trace_noise_window_s),
        ):
            metadata.attrs[key] = value
        metadata.attrs["trace_metrics"] = np.asarray(settings.trace_metrics, dtype=h5py.string_dtype(encoding="utf-8"))


def load_processing_settings(path: Path = DEFAULT_CONFIG_PATH) -> ProcessingSettings:
    if not path.exists():
        return ProcessingSettings()

    payload = _load_payload(path)
    processing = payload.get("processing", {})
    if isinstance(processing, dict) and processing.get("fit_enabled") is True and processing.get("fit_method") == "none":
        processing = dict(processing)
        processing["fit_method"] = "poly"
    return _coerce_processing_settings(processing)


def load_processing_settings_from_hdf5(path: Path) -> ProcessingSettings:
    if not path.exists():
        return ProcessingSettings()

    with h5py.File(path, "r") as handle:
        processed_group = handle.get("processed")
        if processed_group is not None:
            metrics_group = processed_group.get("metrics")
            if metrics_group is not None:
                payload = read_processing_settings_metadata(metrics_group)
                if isinstance(payload, dict) and payload:
                    return _coerce_processing_settings(payload)

        legacy_payload: dict[str, object] = {}
        metadata_group = handle.get("metadata")
        if metadata_group is not None:
            attrs = metadata_group.attrs
            legacy_payload = {
                "wavelength_min_nm": attrs.get("processing_range_min_nm", 400.0),
                "wavelength_max_nm": attrs.get("processing_range_max_nm", 900.0),
                "baseline_method": attrs.get("processing_baseline_method", "none"),
                "smoothing_method": attrs.get("processing_smoothing_method", "none"),
                "smoothing_window": attrs.get("processing_smoothing_window", 5),
                "temporal_smoothing": attrs.get("processing_temporal_smoothing", 1),
                "crop_method": attrs.get("processing_crop_method", "fixed_width"),
                "crop_fraction": attrs.get("processing_crop_fraction", 0.7),
                "fit_method": attrs.get("processing_fit_method", "none"),
                "polynomial_order": attrs.get("processing_polynomial_order", 2),
                "fit_window_width_nm": attrs.get("processing_fit_window_width_nm", 120.0),
                "analysis_resolution_nm": attrs.get("processing_analysis_resolution_nm", 0.001),
                "spectrum_tracking_mode": attrs.get("spectrum_tracking_mode", attrs.get("peak_tracking_mode", "poly_max")),
                "trace_noise_window_s": attrs.get("processing_trace_noise_window_s", 10.0),
            }
            trace_metrics = attrs.get("trace_metrics", ["smoothed_max", "centroid"])
            if isinstance(trace_metrics, np.ndarray):
                legacy_payload["trace_metrics"] = [
                    item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in trace_metrics.tolist()
                ]
            elif isinstance(trace_metrics, (list, tuple)):
                legacy_payload["trace_metrics"] = [
                    item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in trace_metrics
                ]
            elif trace_metrics is not None:
                legacy_payload["trace_metrics"] = [str(trace_metrics)]
        return _coerce_processing_settings(legacy_payload)


def save_ui_state(state: dict[str, object], path: Path = DEFAULT_CONFIG_PATH) -> None:
    payload = _load_payload(path)
    payload["ui_state"] = state
    _write_payload(payload, path)


def load_ui_state(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = _load_payload(path)
    ui_state = payload.get("ui_state", {})
    return ui_state if isinstance(ui_state, dict) else {}


def save_window_ui_state(
    window_name: str,
    state: dict[str, object],
    path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    payload = _load_payload(path)
    ui_state = payload.get("ui_state", {})
    if not isinstance(ui_state, dict):
        ui_state = {}
    ui_state[window_name] = state
    payload["ui_state"] = ui_state
    _write_payload(payload, path)


def load_window_ui_state(window_name: str, path: Path = DEFAULT_CONFIG_PATH) -> dict[str, object]:
    ui_state = load_ui_state(path)
    window_state = ui_state.get(window_name)
    if isinstance(window_state, dict):
        return window_state
    if window_name == "main_window":
        # Backward compatibility with older flat ui_state payloads.
        legacy_keys = {"x", "y", "width", "height", "maximized", "splitter_sizes"}
        if any(key in ui_state for key in legacy_keys):
            return ui_state
    return {}


def save_app_setting(
    key: str,
    value: object,
    path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    payload = _load_payload(path)
    app_state = payload.get("app", {})
    if not isinstance(app_state, dict):
        app_state = {}
    app_state[key] = value
    payload["app"] = app_state
    _write_payload(payload, path)


def load_app_setting(
    key: str,
    default: object = None,
    path: Path = DEFAULT_CONFIG_PATH,
) -> object:
    payload = _load_payload(path)
    app_state = payload.get("app", {})
    if not isinstance(app_state, dict):
        return default
    return app_state.get(key, default)


def save_acquisition_state(
    state: dict[str, object],
    path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    save_app_setting("acquisition_state", state, path)


def load_acquisition_state(
    path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, object]:
    state = load_app_setting("acquisition_state", {}, path)
    return state if isinstance(state, dict) else {}
