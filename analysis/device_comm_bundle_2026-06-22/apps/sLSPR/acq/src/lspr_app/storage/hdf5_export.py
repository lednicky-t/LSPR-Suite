from __future__ import annotations

import json
import os
import queue
import threading
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from lspr_core import ExperimentPlan
from lspr_io import (
    LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME,
    LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME,
    LSPR_EXPERIMENT_PLAN_DATASET_NAME,
    LSPR_MEASUREMENT_PLAN_COLUMNS,
    LSPR_MEASUREMENT_RUNTIME_COLUMNS,
    LSPR_MEASUREMENT_RUNTIME_DATASET_NAME,
    LSPR_MEASUREMENT_RUNTIME_TIMESTAMP_UTC_COLUMN,
    LSPR_MEASUREMENT_TIME_COLUMNS,
    LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME,
    LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME,
    LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME,
    LSPR_PROCESSED_METRICS_ACQUIRED_AT_UNIX_MS_DATASET_NAME,
    LSPR_SESSION_SCHEMA_NAME,
    LSPR_SESSION_SCHEMA_VERSION,
    standard_measurement_metadata,
    standard_session_identity,
    write_processing_settings_metadata,
    write_processed_metrics_metadata,
    write_measurement_manifest_metadata,
    write_measurement_root_metadata,
    write_session_metadata,
)
from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.domain.pump_plan import to_core_experiment_plan
from lspr_app.version import APP_VERSION


def repack_measurement_hdf5_file(source_path: Path) -> Path:
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    temp_dir = source_path.parent
    temp_handle = tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=".tmp",
        prefix=f".{source_path.stem}.gzip-",
        dir=temp_dir,
        delete=False,
    )
    temp_handle.close()
    temp_path = Path(temp_handle.name)

    try:
        with h5py.File(source_path, "r") as source_handle, h5py.File(temp_path, "w") as destination_handle:
            _copy_hdf5_node(source_handle, destination_handle, compression="gzip")
            destination_handle.attrs["storage_compression_enabled"] = np.bool_(True)
            destination_handle.attrs["storage_compression_filter"] = "gzip"
            destination_handle.attrs["storage_compression_level"] = 4
            if "metadata" in destination_handle:
                destination_handle["metadata"].attrs["storage_compression_enabled"] = np.bool_(True)
                destination_handle["metadata"].attrs["storage_compression_filter"] = "gzip"
                destination_handle["metadata"].attrs["storage_compression_level"] = 4
            if "manifest" in destination_handle:
                destination_handle["manifest"].attrs["storage_compression_enabled"] = np.bool_(True)
                destination_handle["manifest"].attrs["storage_compression_filter"] = "gzip"
                destination_handle["manifest"].attrs["storage_compression_level"] = 4
            destination_handle.flush()
        os.replace(temp_path, source_path)
    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        raise
    return source_path


def _copy_hdf5_node(source_node, destination_node, *, compression: str | None = None) -> None:
    destination_node.attrs.update(source_node.attrs)
    for name, item in source_node.items():
        if isinstance(item, h5py.Group):
            child = destination_node.create_group(name)
            _copy_hdf5_node(item, child, compression=compression)
            continue
        _copy_hdf5_dataset(item, destination_node, name, compression=compression)


def _copy_hdf5_dataset(source_dataset, destination_parent, name: str, *, compression: str | None = None) -> None:
    create_kwargs: dict[str, object] = {}
    if source_dataset.ndim > 0:
        create_kwargs["chunks"] = source_dataset.chunks or True
        if compression is not None:
            create_kwargs["compression"] = compression
            if compression == "gzip":
                create_kwargs["compression_opts"] = 4
            if np.issubdtype(source_dataset.dtype, np.number):
                create_kwargs["shuffle"] = True
    destination_dataset = destination_parent.create_dataset(
        name,
        shape=source_dataset.shape,
        maxshape=source_dataset.maxshape,
        dtype=source_dataset.dtype,
        fillvalue=source_dataset.fillvalue,
        **create_kwargs,
    )
    if source_dataset.ndim == 0:
        destination_dataset[...] = source_dataset[()]
    elif source_dataset.size:
        destination_dataset[...] = source_dataset[...]
    destination_dataset.attrs.update(source_dataset.attrs)


class HDF5MeasurementWriter:
    def __init__(
        self,
        path: Path,
        signal_kind: str,
        wavelengths_nm: np.ndarray,
        processing: ProcessingSettings,
        experiment_name: str = "",
        started_at_utc: datetime | None = None,
        *,
        compression_enabled: bool = False,
    ) -> None:
        self.path = path
        self.signal_kind = signal_kind
        self._wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)
        self._started_at_utc = started_at_utc or datetime.now(timezone.utc)
        self._compression_enabled = bool(compression_enabled)
        self._compression_kwargs: dict[str, object] = {}
        self._dark_index = -1
        self._reference_index = -1
        self._last_dark_key: tuple[object, ...] | None = None
        self._last_reference_key: tuple[object, ...] | None = None
        self._handle = h5py.File(path, "w")
        write_measurement_root_metadata(
            self._handle,
            **standard_measurement_metadata(
                created_by="LSPR Acquisition",
                started_at_utc=self._started_at_utc,
                app_name="LSPR Acquisition",
                app_version=APP_VERSION,
                experiment_name=experiment_name,
            ),
        )
        self._manifest = self._handle.create_group("manifest")
        write_measurement_manifest_metadata(
            self._manifest,
            **standard_measurement_metadata(
                created_by="LSPR Acquisition",
                started_at_utc=self._started_at_utc,
                app_name="LSPR Acquisition",
                app_version=APP_VERSION,
                experiment_name=experiment_name,
            ),
            storage_compression_enabled=False,
            storage_compression_filter="none",
            storage_compression_level=0,
            extra_attrs={
                "manifest_kind": "measurement",
            },
        )
        self._data = self._handle.create_group("data")
        self._metadata = self._handle.create_group("metadata")
        self._processed = self._handle.create_group("processed")
        self._devices = self._handle.create_group("devices")
        self._data_spectra = self._data.create_group("spectra")
        self._session_identity = standard_session_identity(app_name="LSPR Acquisition", app_version=APP_VERSION)
        write_session_metadata(
            self._metadata,
            identity=self._session_identity,
            schema_name=LSPR_SESSION_SCHEMA_NAME,
            schema_version=LSPR_SESSION_SCHEMA_VERSION,
            extra_attrs={
                "created_by": "LSPR HDF5 Builder",
                "version": "2.0",
                "experiment_name": str(experiment_name or ""),
            },
        )
        self._handle.attrs["storage_compression_enabled"] = False
        self._handle.attrs["storage_compression_filter"] = "none"
        self._handle.attrs["storage_compression_level"] = 0
        self._metadata.attrs["storage_compression_enabled"] = False
        self._metadata.attrs["storage_compression_filter"] = "none"
        self._metadata.attrs["storage_compression_level"] = 0
        self._assignment_tables = self._metadata.create_group(LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME)
        self._write_processing_metadata(processing)

        wavelength_ds = self._data.create_dataset(
            LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME,
            data=self._wavelengths_nm,
            **self._compression_kwargs,
        )
        wavelength_ds.attrs["units"] = "nm"
        wavelength_ds.attrs["description"] = "Wavelength axis shared by spectra matrices."

        self._sample_group = self._create_spectrum_group("sample", include_baseline_indices=True)
        self._dark_group = self._create_spectrum_group("dark")
        self._reference_group = self._create_spectrum_group("reference")
        self._metrics_group = self._create_metrics_group()
        self._write_processed_metrics_metadata(processing)
        self._runtime = self._create_runtime_dataset()

        self._time = self._data.create_dataset(
            "time_series",
            shape=(0,),
            maxshape=(None,),
            dtype=np.float64,
            chunks=True,
            **self._compression_kwargs,
        )
        self._time.attrs["columns"] = _string_array(LSPR_MEASUREMENT_TIME_COLUMNS)
        self._time.attrs["prepend_zero"] = np.True_
        columns = list(LSPR_MEASUREMENT_PLAN_COLUMNS)
        self._plan_table_columns = columns
        self._write_plan_tables([])
        self._metadata.attrs["schema_name"] = LSPR_SESSION_SCHEMA_NAME
        self._metadata.attrs["schema_version"] = LSPR_SESSION_SCHEMA_VERSION
        self._metadata.attrs["app_name"] = "LSPR Acquisition"
        self._metadata.attrs["app_version"] = APP_VERSION

    def update_processing(self, processing: ProcessingSettings) -> None:
        self._write_processing_metadata(processing)
        self._write_processed_metrics_metadata(processing)

    def update_acquisition_state(self, state: dict[str, object]) -> None:
        self._write_acquisition_state_metadata(state)
        self._write_switch_solution_metadata(state)
        self._write_assignment_tables_metadata(state)
        plan = state.get("experiment_plan")
        if plan is None:
            experiment_control = state.get("experiment_control")
            if isinstance(experiment_control, dict):
                plan = experiment_control.get("experiment_plan")
        if plan is not None:
            try:
                if isinstance(plan, ExperimentPlan):
                    core_plan = plan
                else:
                    core_plan = to_core_experiment_plan(list(plan))
                write_session_metadata(self._metadata, identity=self._session_identity, experiment_plan=core_plan)
            except Exception:
                pass
        self._write_plan_tables_from_state(state)

    def update_baselines(self, dark: Spectrum | None, reference: Spectrum | None) -> None:
        if dark is not None:
            self._upsert_vector("dark_intensity", np.asarray(dark.values, dtype=np.float32), ["D"])
            self._dark_index = self._append_baseline_if_new("dark", dark, self._dark_group, self._last_dark_key)
            self._last_dark_key = self._spectrum_key(dark)
        if reference is not None:
            self._upsert_vector("light_intensity", np.asarray(reference.values, dtype=np.float32), ["I0"])
            self._reference_index = self._append_baseline_if_new(
                "reference",
                reference,
                self._reference_group,
                self._last_reference_key,
            )
            self._last_reference_key = self._spectrum_key(reference)

    def append_batch(
        self,
        spectra: list[Spectrum],
        time_series_s: list[float],
        peak_positions_nm: list[float],
    ) -> None:
        if not spectra:
            return

        start = self._sample_group["t_ms"].shape[0]
        end = start + len(spectra)

        self._time.resize((end,))
        self._time[start:end] = np.asarray(time_series_s, dtype=np.float64)
        self._append_spectra_rows(self._sample_group, spectra, time_series_s, self._dark_index, self._reference_index)

    def append_metrics(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        start = self._metrics_group["t_ms"].shape[0]
        end = start + len(rows)
        for name, dataset in self._metrics_group.items():
            if not isinstance(dataset, h5py.Dataset):
                continue
            dataset.resize((end,))
            if name == LSPR_PROCESSED_METRICS_ACQUIRED_AT_UNIX_MS_DATASET_NAME:
                dataset[start:end] = np.asarray([int(row.get(name, 0) or 0) for row in rows], dtype=np.int64)
            elif name == "t_ms":
                dataset[start:end] = np.asarray([int(row.get(name, 0) or 0) for row in rows], dtype=np.int64)
            elif name == "sample_index":
                dataset[start:end] = np.asarray([int(row.get(name, -1) or -1) for row in rows], dtype=np.int64)
            else:
                dataset[start:end] = np.asarray([_float_or_nan(row.get(name, np.nan)) for row in rows], dtype=np.float64)

    def append_experiment_control_runtime(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        columns = [
            column.decode("utf-8") if isinstance(column, bytes) else str(column)
            for column in self._runtime.attrs["columns"]
        ]
        start = self._runtime.shape[0]
        end = start + len(rows)
        self._runtime.resize((end, len(columns)))
        table = []
        for row in rows:
            table.append([str(row.get(column, "")) for column in columns])
        table_array = np.asarray(table, dtype=h5py.string_dtype(encoding="utf-8"))
        self._runtime[start:end, :] = table_array

    def append_flow_state(self, rows: list[dict[str, object]]) -> None:
        self.append_experiment_control_runtime(rows)

    def append_device_state(self, row: dict[str, object]) -> None:
        self.append_flow_state([row])

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        self._handle.flush()
        self._handle.close()

    def _upsert_vector(self, name: str, values: np.ndarray, columns: list[str]) -> None:
        if name in self._data:
            dataset = self._data[name]
            dataset[...] = values
            return
        ds = self._data.create_dataset(name, data=values)
        ds.attrs["columns"] = _string_array(columns)

    def _create_spectrum_group(self, name: str, *, include_baseline_indices: bool = False):
        group = self._data_spectra.create_group(name)
        group.create_dataset("t_ms", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True, **self._compression_kwargs)
        group.create_dataset(
            "acquired_at_unix_ms", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True, **self._compression_kwargs
        )
        group.create_dataset(
            "intensity",
            shape=(0, len(self._wavelengths_nm)),
            maxshape=(None, len(self._wavelengths_nm)),
            dtype=np.float32,
            chunks=True,
            **self._compression_kwargs,
        )
        group.create_dataset(
            "integration_time_ms", shape=(0,), maxshape=(None,), dtype=np.float32, chunks=True, **self._compression_kwargs
        )
        group.create_dataset("averages", shape=(0,), maxshape=(None,), dtype=np.int32, chunks=True, **self._compression_kwargs)
        group.create_dataset("source_epoch", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True, **self._compression_kwargs)
        group.create_dataset("flags", shape=(0,), maxshape=(None,), dtype=np.uint32, chunks=True, **self._compression_kwargs)
        if include_baseline_indices:
            group.create_dataset(
                "dark_index", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True, **self._compression_kwargs
            )
            group.create_dataset(
                "reference_index", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True, **self._compression_kwargs
            )
        return group

    def _create_metrics_group(self):
        group = self._processed.create_group("metrics")
        for name, dtype in (
            (LSPR_PROCESSED_METRICS_ACQUIRED_AT_UNIX_MS_DATASET_NAME, np.int64),
            ("t_ms", np.int64),
            ("sample_index", np.int64),
            ("centroid_nm", np.float64),
            ("smoothed_max_nm", np.float64),
            ("poly_max_nm", np.float64),
            ("gaussian_center_nm", np.float64),
            ("fwhm_nm", np.float64),
            ("mse", np.float64),
            ("snr", np.float64),
        ):
            ds = group.create_dataset(name, shape=(0,), maxshape=(None,), dtype=dtype, chunks=True, **self._compression_kwargs)
            ds.attrs["schema_version_added"] = "3.0"
        return group

    def _write_processed_metrics_metadata(self, processing: ProcessingSettings) -> None:
        write_processed_metrics_metadata(self._metrics_group)
        write_processing_settings_metadata(self._metrics_group, asdict(processing))

    def _create_runtime_dataset(self):
        columns = list(LSPR_MEASUREMENT_RUNTIME_COLUMNS)
        ds = self._data.create_dataset(
            LSPR_MEASUREMENT_RUNTIME_DATASET_NAME,
            shape=(0, len(columns)),
            maxshape=(None, len(columns)),
            dtype=h5py.string_dtype(encoding="utf-8"),
            chunks=True,
            **self._compression_kwargs,
        )
        ds.attrs["columns"] = _string_array(columns)
        ds.attrs["schema_version_added"] = "3.0"
        return ds

    def _append_baseline_if_new(self, _role: str, spectrum: Spectrum, group, previous_key: tuple[object, ...] | None) -> int:
        key = self._spectrum_key(spectrum)
        if key == previous_key:
            return max(group["t_ms"].shape[0] - 1, -1)
        self._append_spectra_rows(group, [spectrum], [self._relative_seconds(spectrum)])
        return group["t_ms"].shape[0] - 1

    def _append_spectra_rows(
        self,
        group,
        spectra: list[Spectrum],
        time_series_s: list[float],
        dark_index: int | None = None,
        reference_index: int | None = None,
    ) -> None:
        if not spectra:
            return
        start = group["t_ms"].shape[0]
        end = start + len(spectra)
        for dataset in group.values():
            if dataset.ndim == 1:
                dataset.resize((end,))
            else:
                dataset.resize((end, dataset.shape[1]))
        group["t_ms"][start:end] = np.asarray([int(round(float(t) * 1000.0)) for t in time_series_s], dtype=np.int64)
        group["acquired_at_unix_ms"][start:end] = np.asarray([_unix_ms(s.acquired_at) for s in spectra], dtype=np.int64)
        group["intensity"][start:end, :] = np.vstack([self._resample_spectrum(s) for s in spectra])
        group["integration_time_ms"][start:end] = np.asarray(
            [_float_or_nan(s.metadata.get("integration_time_ms", np.nan)) for s in spectra],
            dtype=np.float32,
        )
        group["averages"][start:end] = np.asarray(
            [int(s.metadata.get("averages", 0) or 0) for s in spectra],
            dtype=np.int32,
        )
        group["source_epoch"][start:end] = np.asarray(
            [int(s.metadata.get("source_epoch", 0) or 0) for s in spectra],
            dtype=np.int64,
        )
        group["flags"][start:end] = np.zeros(len(spectra), dtype=np.uint32)
        if "dark_index" in group and dark_index is not None:
            group["dark_index"][start:end] = np.full(len(spectra), int(dark_index), dtype=np.int64)
        if "reference_index" in group and reference_index is not None:
            group["reference_index"][start:end] = np.full(len(spectra), int(reference_index), dtype=np.int64)

    def _relative_seconds(self, spectrum: Spectrum) -> float:
        return max((spectrum.acquired_at - self._started_at_utc).total_seconds(), 0.0)

    def _spectrum_key(self, spectrum: Spectrum) -> tuple[object, ...]:
        return (
            spectrum.acquired_at,
            len(spectrum.values),
            float(np.nanmean(spectrum.values)) if len(spectrum.values) else 0.0,
        )

    def _upsert_table(self, group, name: str, rows: list[list[str]], columns: list[str]) -> None:
        if rows:
            table = np.asarray(rows, dtype=h5py.string_dtype(encoding="utf-8"))
        else:
            table = np.empty((0, len(columns)), dtype=h5py.string_dtype(encoding="utf-8"))
        if name in group:
            dataset = group[name]
            needs_recreate = dataset.chunks is None or dataset.shape != table.shape or dataset.ndim != table.ndim
            if needs_recreate:
                del group[name]
                dataset = group.create_dataset(
                    name,
                    shape=table.shape,
                    maxshape=(None, table.shape[1] if table.ndim > 1 else None),
                    dtype=table.dtype,
                    chunks=True,
                    **self._compression_kwargs,
                )
            elif dataset.shape != table.shape:
                dataset.resize(table.shape)
            dataset[...] = table
            dataset.attrs["columns"] = _string_array(columns)
            return
        ds = group.create_dataset(
            name,
            data=table,
            maxshape=(None, table.shape[1] if table.ndim == 2 else 0),
            chunks=True,
            **self._compression_kwargs,
        )
        ds.attrs["columns"] = _string_array(columns)

    def _resample_spectrum(self, spectrum: Spectrum) -> np.ndarray:
        values = np.asarray(spectrum.values, dtype=np.float64)
        wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
        target = self._wavelengths_nm
        if len(values) == 0:
            return np.zeros(len(target), dtype=np.float32)

        if len(values) == len(target) and np.array_equal(wavelengths, target):
            return values.astype(np.float32, copy=False)

        finite_mask = np.isfinite(wavelengths) & np.isfinite(values)
        if np.count_nonzero(finite_mask) < 2:
            fill_value = float(np.nanmean(values)) if np.any(np.isfinite(values)) else 0.0
            return np.full(len(target), fill_value, dtype=np.float32)

        source_x = wavelengths[finite_mask]
        source_y = values[finite_mask]
        order = np.argsort(source_x)
        source_x = source_x[order]
        source_y = source_y[order]
        unique_x, unique_indices = np.unique(source_x, return_index=True)
        source_y = source_y[unique_indices]
        if len(unique_x) < 2:
            fill_value = float(source_y[0]) if len(source_y) else 0.0
            return np.full(len(target), fill_value, dtype=np.float32)
        resampled = np.interp(target, unique_x, source_y, left=float(source_y[0]), right=float(source_y[-1]))
        return resampled.astype(np.float32, copy=False)

    def _write_processing_metadata(self, processing: ProcessingSettings) -> None:
        self._metadata.attrs["processing_range_min_nm"] = processing.wavelength_min_nm
        self._metadata.attrs["processing_range_max_nm"] = processing.wavelength_max_nm
        self._metadata.attrs["processing_baseline_method"] = processing.baseline_method
        self._metadata.attrs["processing_smoothing_method"] = processing.smoothing_method
        self._metadata.attrs["processing_smoothing_window"] = processing.smoothing_window
        self._metadata.attrs["processing_temporal_smoothing"] = processing.temporal_smoothing
        self._metadata.attrs["processing_crop_method"] = processing.crop_method
        self._metadata.attrs["processing_crop_fraction"] = processing.crop_fraction
        self._metadata.attrs["processing_fit_method"] = processing.fit_method
        self._metadata.attrs["processing_polynomial_order"] = processing.polynomial_order
        self._metadata.attrs["processing_fit_window_width_nm"] = processing.fit_window_width_nm
        self._metadata.attrs["processing_analysis_resolution_nm"] = processing.analysis_resolution_nm
        self._metadata.attrs["spectrum_tracking_mode"] = processing.spectrum_tracking_mode
        self._metadata.attrs["peak_tracking_mode"] = processing.spectrum_tracking_mode
        self._metadata.attrs["processing_trace_noise_window_s"] = processing.trace_noise_window_s
        self._metadata.attrs["trace_metrics"] = _string_array(processing.trace_metrics)

    def _write_acquisition_state_metadata(self, state: dict[str, object]) -> None:
        self._metadata.attrs["acquisition_state_json"] = json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        self._metadata.attrs["acquisition_source_mode"] = str(state.get("source_mode", ""))
        self._metadata.attrs["acquisition_plot_mode"] = str(state.get("plot_mode", ""))
        self._metadata.attrs["acquisition_live_rate_hz"] = float(state.get("live_rate_hz", 0.0) or 0.0)
        self._metadata.attrs["acquisition_show_residual"] = bool(state.get("show_residual", False))
        self._metadata.attrs["acquisition_freeze_plots"] = bool(state.get("freeze_plots", False))

    def _write_switch_solution_metadata(self, state: dict[str, object]) -> None:
        experiment_control = state.get("experiment_control")
        if not isinstance(experiment_control, dict):
            return
        self._metadata.attrs["switch_solution_mode"] = bool(experiment_control.get("switch_solution_mode", False))
        rows = experiment_control.get("switch_solution_rows", [])
        if isinstance(rows, list):
            cleaned_rows: list[list[str]] = []
            for row in rows[:12]:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                cleaned_rows.append([str(row[0]), str(row[1])])
            if cleaned_rows:
                self._upsert_table(
                    self._assignment_tables,
                    LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME,
                    cleaned_rows,
                    ["switch_port", "solution_label"],
                )

    def _write_assignment_tables_metadata(self, state: dict[str, object]) -> None:
        experiment_control = state.get("experiment_control")
        if not isinstance(experiment_control, dict):
            return

        valve_labels = experiment_control.get("valve_state_labels")
        valve_colors = experiment_control.get("valve_state_colors")
        if isinstance(valve_labels, dict) or isinstance(valve_colors, dict):
            rows: list[list[str]] = []
            for state_name, default_color in (("Open", "#4E79A7"), ("Close", "#B44A4A")):
                label = state_name
                color = default_color
                if isinstance(valve_labels, dict):
                    label = str(valve_labels.get(state_name, state_name)).strip() or state_name
                if isinstance(valve_colors, dict):
                    color = str(valve_colors.get(state_name, default_color)).strip().upper() or default_color
                rows.append([state_name, label, color])
            self._upsert_table(
                self._assignment_tables,
                LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME,
                rows,
                ["state", "label", "color"],
            )

        palette_entries = experiment_control.get("color_palette_entries")
        if isinstance(palette_entries, list):
            rows = []
            for index, raw_entry in enumerate(palette_entries):
                name = ""
                color = ""
                if isinstance(raw_entry, dict):
                    name = str(raw_entry.get("name", "") or "").strip()
                    color = str(raw_entry.get("color", "") or "").strip().upper()
                elif isinstance(raw_entry, (list, tuple)) and len(raw_entry) >= 2:
                    name = str(raw_entry[0] or "").strip()
                    color = str(raw_entry[1] or "").strip().upper()
                if not color:
                    continue
                if not name:
                    name = f"Custom {index + 1}"
                rows.append([name, color])
            self._upsert_table(
                self._assignment_tables,
                LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME,
                rows,
                ["name", "color"],
            )

    def _plan_rows_from_state(self, state: dict[str, object]) -> list[list[str]]:
        experiment_control = state.get("experiment_control")
        if not isinstance(experiment_control, dict):
            return []
        rows = experiment_control.get("plan_rows")
        if not isinstance(rows, list):
            return []
        cleaned_rows: list[list[str]] = []
        for row in rows:
            if not isinstance(row, list):
                continue
            cleaned_rows.append([str(cell) for cell in row[: len(self._plan_table_columns)]])
        return cleaned_rows

    def _write_plan_tables_from_state(self, state: dict[str, object]) -> None:
        rows = self._plan_rows_from_state(state)
        if not rows:
            return
        self._write_plan_tables(rows)

    def _write_plan_tables(self, rows: list[list[str]]) -> None:
        self._upsert_table(self._metadata, LSPR_EXPERIMENT_PLAN_DATASET_NAME, rows, self._plan_table_columns)


class AsyncHDF5MeasurementWriter:
    def __init__(
        self,
        path: Path,
        signal_kind: str,
        wavelengths_nm: np.ndarray,
        processing: ProcessingSettings,
        experiment_name: str = "",
        started_at_utc: datetime | None = None,
        *,
        flush_interval_s: float = 2.0,
        compression_enabled: bool = False,
    ) -> None:
        self._path = path
        self._signal_kind = signal_kind
        self._wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)
        self._processing = processing
        self._experiment_name = str(experiment_name or "")
        self._started_at_utc = started_at_utc or datetime.now(timezone.utc)
        self._compression_enabled = bool(compression_enabled)
        self._dark: Spectrum | None = None
        self._reference: Spectrum | None = None
        self._acquisition_state: dict[str, object] | None = None
        self._flush_interval_s = max(float(flush_interval_s), 0.25)
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop_event = threading.Event()
        self._closed = False
        self._state_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="hdf5-writer", daemon=True)
        self._thread.start()

    def update_processing(self, processing: ProcessingSettings) -> None:
        if self._closed:
            return
        self._queue.put(("processing", processing))

    def update_acquisition_state(self, state: dict[str, object]) -> None:
        if self._closed:
            return
        self._queue.put(("acquisition_state", dict(state)))

    def update_baselines(self, dark: Spectrum | None, reference: Spectrum | None) -> None:
        if self._closed:
            return
        self._queue.put(("baselines", (dark, reference)))

    def append_batch(
        self,
        spectra: list[Spectrum],
        time_series_s: list[float],
        peak_positions_nm: list[float],
    ) -> None:
        if not spectra:
            return
        if self._closed:
            return
        self._queue.put(("append", (spectra, time_series_s, peak_positions_nm)))

    def append_metrics(self, rows: list[dict[str, object]]) -> None:
        if not rows or self._closed:
            return
        self._queue.put(("metrics", rows))

    def append_experiment_control_runtime(self, row: dict[str, object]) -> None:
        if self._closed:
            return
        self._queue.put(("experiment_control_runtime", row))

    def append_flow_state(self, row: dict[str, object]) -> None:
        self.append_experiment_control_runtime(row)

    def append_device_state(self, row: dict[str, object]) -> None:
        if self._closed:
            return
        self._queue.put(("device_state", row))

    def flush(self) -> None:
        if self._closed:
            return
        self._queue.put(("flush", None))

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            self._queue.put(("close", None))
        self._thread.join(timeout=10.0)

    def _run(self) -> None:
        writer = HDF5MeasurementWriter(
            self._path,
            self._signal_kind,
            self._wavelengths_nm,
            self._processing,
            self._experiment_name,
            self._started_at_utc,
            compression_enabled=self._compression_enabled,
        )
        pending_spectra: list[Spectrum] = []
        pending_times: list[float] = []
        pending_peaks: list[float] = []
        last_flush = monotonic()
        try:
            while True:
                timeout = max(0.0, self._flush_interval_s - (monotonic() - last_flush))
                try:
                    kind, payload = self._queue.get(timeout=timeout)
                except queue.Empty:
                    kind, payload = "timeout", None

                if kind == "processing" and isinstance(payload, ProcessingSettings):
                    self._processing = payload
                    writer.update_processing(payload)
                elif kind == "acquisition_state" and isinstance(payload, dict):
                    self._acquisition_state = dict(payload)
                    writer.update_acquisition_state(payload)
                elif kind == "baselines" and isinstance(payload, tuple):
                    dark, reference = payload
                    self._dark = dark
                    self._reference = reference
                    writer.update_baselines(dark, reference)
                elif kind == "append" and isinstance(payload, tuple):
                    spectra, time_series_s, peak_positions_nm = payload
                    pending_spectra.extend(spectra)
                    pending_times.extend(time_series_s)
                    pending_peaks.extend(peak_positions_nm)
                elif kind == "metrics" and isinstance(payload, list):
                    writer.append_metrics(payload)
                elif kind == "experiment_control_runtime" and isinstance(payload, dict):
                    writer.append_flow_state([payload])
                elif kind == "device_state" and isinstance(payload, dict):
                    writer.append_device_state(payload)
                elif kind == "flush":
                    if pending_spectra:
                        writer.append_batch(pending_spectra, pending_times, pending_peaks)
                        writer.flush()
                        pending_spectra.clear()
                        pending_times.clear()
                        pending_peaks.clear()
                    last_flush = monotonic()
                elif kind == "close":
                    if pending_spectra:
                        writer.append_batch(pending_spectra, pending_times, pending_peaks)
                        writer.flush()
                    break
                elif kind == "timeout":
                    if pending_spectra:
                        writer.append_batch(pending_spectra, pending_times, pending_peaks)
                        writer.flush()
                        pending_spectra.clear()
                        pending_times.clear()
                        pending_peaks.clear()
                        last_flush = monotonic()
                if self._stop_event.is_set() and self._queue.empty():
                    break
        finally:
            writer.close()
def _string_array(values: list[str]) -> np.ndarray:
    return np.array(values, dtype=h5py.string_dtype(encoding="utf-8"))

def _unix_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(round(value.astimezone(timezone.utc).timestamp() * 1000.0))


def _float_or_nan(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def load_processed_metric_history(
    path: Path,
    metric_names: set[str] | None = None,
    *,
    time_range_s: tuple[float, float] | None = None,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    from lspr_app.storage.metric_archive import load_metric_archive_history

    if path.suffix.lower() == ".jsonl":
        return load_metric_archive_history(path, metric_names=metric_names, time_range_s=time_range_s)

    series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    with h5py.File(path, "r") as handle:
        processed_group = handle.get("processed")
        if processed_group is None:
            return {}
        metrics_group = processed_group.get("metrics")
        if metrics_group is None:
            return {}

        time_ds = metrics_group.get("t_ms")
        if time_ds is None:
            return {}
        times_s = np.asarray(time_ds[...], dtype=float) / 1000.0
        start_index = 0
        end_index = len(times_s)
        if time_range_s is not None:
            try:
                start_s = float(time_range_s[0])
                end_s = float(time_range_s[1])
            except Exception:
                start_s = end_s = None  # type: ignore[assignment]
            else:
                if np.isfinite(start_s) and np.isfinite(end_s):
                    if end_s < start_s:
                        start_s, end_s = end_s, start_s
                    start_index = int(np.searchsorted(times_s, start_s, side="left"))
                    end_index = int(np.searchsorted(times_s, end_s, side="right"))
                    times_s = times_s[start_index:end_index]

        for metric_name, dataset in metrics_group.items():
            if metric_name in {"t_ms", "acquired_at_unix_ms", "sample_index"}:
                continue
            if metric_names is not None and metric_name not in metric_names:
                continue
            if time_range_s is not None:
                values = np.asarray(dataset[start_index:end_index], dtype=float)
            else:
                values = np.asarray(dataset[...], dtype=float)
            series[metric_name] = (times_s.copy(), values)
    return series


def load_spectrum_heatmap_history(
    path: Path,
    *,
    max_rows: int | None = None,
    time_range_s: tuple[float, float] | None = None,
    wavelength_range_nm: tuple[float, float] | None = None,
    spectrum_group_name: str = "sample",
) -> tuple[np.ndarray, list[tuple[float, np.ndarray]]]:
    path = Path(path).expanduser()
    if not path.exists():
        return np.empty(0, dtype=np.float64), []
    try:
        with h5py.File(path, "r") as handle:
            data_group = handle.get("data")
            if data_group is None:
                return np.empty(0, dtype=np.float64), []
            wavelengths_ds = data_group.get(LSPR_MEASUREMENT_WAVELENGTHS_DATASET_NAME)
            spectra_root = data_group.get("spectra")
            if wavelengths_ds is None or spectra_root is None:
                return np.empty(0, dtype=np.float64), []
            spectrum_group = spectra_root.get(spectrum_group_name)
            if spectrum_group is None:
                return np.empty(0, dtype=np.float64), []
            time_ds = spectrum_group.get("t_ms")
            intensity_ds = spectrum_group.get("intensity")
            if time_ds is None or intensity_ds is None:
                return np.empty(0, dtype=np.float64), []

            wavelengths = np.asarray(wavelengths_ds[...], dtype=np.float64)
            if wavelengths.size == 0:
                return np.empty(0, dtype=np.float64), []

            if wavelength_range_nm is not None:
                low_nm = float(min(wavelength_range_nm))
                high_nm = float(max(wavelength_range_nm))
                wavelength_mask = (wavelengths >= low_nm) & (wavelengths <= high_nm)
                if np.any(wavelength_mask):
                    wavelengths = wavelengths[wavelength_mask]
                else:
                    wavelength_mask = np.ones(len(wavelengths), dtype=bool)
            else:
                wavelength_mask = np.ones(len(wavelengths), dtype=bool)

            times_s = np.asarray(time_ds[...], dtype=np.float64) / 1000.0
            if times_s.size == 0:
                return wavelengths, []

            row_indices = np.arange(len(times_s), dtype=np.int64)
            if time_range_s is not None:
                start_s = float(min(time_range_s))
                end_s = float(max(time_range_s))
                row_mask = (times_s >= start_s) & (times_s <= end_s)
                row_indices = row_indices[row_mask]
            if max_rows is not None and max_rows > 0 and len(row_indices) > max_rows:
                row_indices = row_indices[-int(max_rows) :]

            if len(row_indices) == 0:
                return wavelengths, []

            selected_times = times_s[row_indices]
            selected_intensity = np.asarray(intensity_ds[row_indices][:, wavelength_mask], dtype=np.float64)
            history = [
                (float(time_s), np.asarray(values, dtype=np.float64).copy())
                for time_s, values in zip(selected_times.tolist(), selected_intensity, strict=False)
            ]
            return wavelengths, history
    except Exception:
        return np.empty(0, dtype=np.float64), []
