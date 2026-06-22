from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import inspect

import h5py
import numpy as np

from lspr_app.storage.output_paths import recording_experiment_base_dir_for


_WRITER_ATTR = "_metric_archive_writer"
_WRITER_PATH_ATTR = "_metric_archive_writer_path"
_WRITER_TEMP_ATTR = "_metric_archive_writer_is_temp"


def ensure_temp_measurement_path(window: Any) -> Path:
    path = getattr(window, _WRITER_PATH_ATTR, None)
    if path:
        return Path(path)

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    session_name = getattr(getattr(window, "_session", None), "name", None) or "session"
    safe_session = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(session_name))

    base_dir = recording_experiment_base_dir_for(window, fallback_base=Path.cwd() / "data")
    candidate = base_dir / f"temp_measurement_{safe_session}_{stamp}.h5"
    setattr(window, _WRITER_PATH_ATTR, candidate)
    return candidate


def _builder_kwargs(window: Any, path: Path) -> dict[str, Any]:
    session = getattr(window, "_session", None)
    kwargs: dict[str, Any] = {
        "path": path,
        "file_path": path,
        "measurement_path": path,
        "output_path": path,
        "session": session,
        "window": window,
        "logger": getattr(window, "logger", None),
        "name": getattr(session, "name", None),
        "temporary": True,
        "is_temporary": True,
        "temp": True,
        "metadata": getattr(window, "_session_metadata", None),
    }
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    return kwargs


def _instantiate_official_writer(window: Any, path: Path):
    from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter

    candidate = AsyncHDF5MeasurementWriter
    kwargs = _builder_kwargs(window, path)
    try:
        signature = inspect.signature(candidate)
    except Exception:
        signature = None

    if signature is not None:
        params = signature.parameters
        filtered: dict[str, Any] = {}
        for name, value in kwargs.items():
            if name in params:
                filtered[name] = value
        try:
            return candidate(**filtered)
        except Exception:
            pass

    try:
        return candidate(path)
    except Exception:
        return None


@dataclass
class _FallbackTempMeasurementWriter:
    path: Path
    handle: h5py.File

    @classmethod
    def open(cls, path: Path) -> "_FallbackTempMeasurementWriter":
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = h5py.File(path, "a")
        processed = handle.require_group("processed").require_group("metrics")
        if "t_ms" not in processed:
            processed.create_dataset("t_ms", shape=(0,), maxshape=(None,), dtype="f8")
        handle.require_group("raw").require_group("events")
        handle.require_group("device_state")
        handle.require_group("control")
        return cls(path=path, handle=handle)

    def _append_generic_event(self, group_path: str, payload: dict[str, Any]) -> None:
        group = self.handle.require_group(group_path)
        dataset_name = "events"
        if dataset_name not in group:
            dt = h5py.string_dtype(encoding="utf-8")
            group.create_dataset(dataset_name, shape=(0,), maxshape=(None,), dtype=dt)
        ds = group[dataset_name]
        row = np.array([str(payload)], dtype=ds.dtype)
        size = int(ds.shape[0])
        ds.resize((size + 1,))
        ds[size] = row[0]

    def append_metrics(self, rows: list[dict[str, Any]]) -> None:
        metrics = self.handle["processed"]["metrics"]
        for row in rows:
            if not isinstance(row, dict):
                try:
                    row = dict(row)
                except Exception:
                    row = {}
            t_ms = row.get("t_ms")
            if t_ms is None:
                t_ms = row.get("acquired_at_unix_ms")
            if t_ms is None:
                t_ms = float(metrics["t_ms"].shape[0]) * 1000.0
            ds = metrics["t_ms"]
            size = int(ds.shape[0])
            ds.resize((size + 1,))
            ds[size] = float(t_ms)
            for key, value in row.items():
                if key in {"t_ms", "acquired_at_unix_ms", "sample_index"}:
                    continue
                if key not in metrics:
                    metrics.create_dataset(key, shape=(0,), maxshape=(None,), dtype="f8")
                ds_key = metrics[key]
                ds_size = int(ds_key.shape[0])
                ds_key.resize((ds_size + 1,))
                try:
                    ds_key[ds_size] = float(np.asarray(value).item())
                except Exception:
                    ds_key[ds_size] = np.nan
        self.handle.flush()

    def append_batch(self, batch: list[Any], *args: Any, **kwargs: Any) -> None:
        payload = {
            "kind": "raw_batch",
            "batch_size": len(batch),
            "args": [str(arg) for arg in args],
            "kwargs": {key: str(value) for key, value in kwargs.items()},
        }
        self._append_generic_event("raw", payload)
        self.handle.flush()

    def flush(self) -> None:
        self.handle.flush()

    def close(self) -> None:
        self.handle.flush()
        self.handle.close()


def ensure_temp_measurement_writer(window: Any):
    writer = getattr(window, _WRITER_ATTR, None)
    if writer is not None:
        return writer

    path = ensure_temp_measurement_path(window)
    writer = _instantiate_official_writer(window, path)
    if writer is None:
        writer = _FallbackTempMeasurementWriter.open(path)

    setattr(window, _WRITER_ATTR, writer)
    setattr(window, _WRITER_PATH_ATTR, path)
    setattr(window, "_metric_archive_path", path)
    setattr(window, _WRITER_TEMP_ATTR, True)
    return writer


def close_temp_measurement_writer(window: Any) -> None:
    writer = getattr(window, _WRITER_ATTR, None)
    if writer is None:
        return
    try:
        close = getattr(writer, "close", None)
        if callable(close):
            close()
    finally:
        setattr(window, _WRITER_ATTR, None)
        setattr(window, _WRITER_TEMP_ATTR, False)


def metric_archive_path(window: Any) -> Path:
    return ensure_temp_measurement_path(window)
