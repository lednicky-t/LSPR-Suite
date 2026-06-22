from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import tempfile

import h5py
import numpy as np

from lspr_app.storage.measurement_archive import ensure_temp_measurement_writer

_ARCHIVE_PATH_ATTR = "_metric_archive_path"
_ARCHIVE_HANDLE_ATTR = "_metric_archive_handle"
_ARCHIVE_ROWS_ATTR = "_metric_archive_rows"
_ARCHIVE_LAST_FLUSH_ATTR = "_metric_archive_last_flush_at"


@dataclass(slots=True)
class _ArchiveState:
    path: Path
    handle: h5py.File
    row_count: int = 0


def _candidate_project_dirs(window: Any) -> list[Path]:
    candidates: list[Path] = []
    for attr in (
        "_project_dir",
        "_project_path",
        "_session_dir",
        "_session_path",
        "_measurement_dir",
        "_measurement_path",
        "_output_dir",
        "_output_path",
        "_save_dir",
        "_save_path",
    ):
        value = getattr(window, attr, None)
        if value:
            try:
                candidates.append(Path(value).expanduser())
            except Exception:
                pass

    session = getattr(window, "_session", None)
    if session is not None:
        for attr in (
            "project_dir",
            "project_path",
            "session_dir",
            "session_path",
            "output_dir",
            "output_path",
            "measurement_dir",
            "measurement_path",
        ):
            value = getattr(session, attr, None)
            if value:
                try:
                    candidates.append(Path(value).expanduser())
                except Exception:
                    pass

    return [path for path in candidates if path.exists()]


def ensure_metric_archive_path(window: Any) -> Path:
    path = getattr(window, _ARCHIVE_PATH_ATTR, None)
    if path:
        return Path(path)

    try:
        from lspr_app.storage.measurement_archive import ensure_temp_measurement_path

        candidate = ensure_temp_measurement_path(window)
    except Exception:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        session_name = getattr(getattr(window, "_session", None), "name", None) or "session"
        safe_session = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(session_name))
        candidate = Path(tempfile.gettempdir()) / f"temp_metric_archive_{safe_session}_{stamp}.h5"

    setattr(window, _ARCHIVE_PATH_ATTR, candidate)
    return candidate


def _open_archive_handle(window: Any) -> _ArchiveState:
    handle = getattr(window, _ARCHIVE_HANDLE_ATTR, None)
    path = ensure_metric_archive_path(window)
    if handle is not None and not getattr(handle, "id", None) is None:
        return _ArchiveState(path=path, handle=handle, row_count=int(getattr(window, _ARCHIVE_ROWS_ATTR, 0)))

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = h5py.File(path, "a")

    def _ensure_group(root: h5py.Group, name: str) -> h5py.Group:
        if name in root:
            return root[name]
        return root.create_group(name)

    _ensure_group(handle, "processed")
    processed = handle["processed"]
    metrics = _ensure_group(processed, "metrics")
    if "t_ms" not in metrics:
        metrics.create_dataset("t_ms", shape=(0,), maxshape=(None,), dtype="f8")
    setattr(window, _ARCHIVE_HANDLE_ATTR, handle)
    setattr(window, _ARCHIVE_ROWS_ATTR, int(getattr(window, _ARCHIVE_ROWS_ATTR, 0)))
    return _ArchiveState(path=path, handle=handle, row_count=int(getattr(window, _ARCHIVE_ROWS_ATTR, 0)))


def _append_dataset(group: h5py.Group, name: str, value: float) -> None:
    ds = group[name]
    size = int(ds.shape[0])
    ds.resize((size + 1,))
    ds[size] = float(value)


def _normalize_metric_row(metric_row: Any) -> dict[str, float]:
    if isinstance(metric_row, dict):
        items = metric_row.items()
    else:
        try:
            items = dict(metric_row).items()
        except Exception:
            items = []

    normalized: dict[str, float] = {}
    for key, value in items:
        if key in {"t_ms", "acquired_at_unix_ms", "sample_index"}:
            continue
        try:
            normalized[str(key)] = float(np.asarray(value).item())
        except Exception:
            continue
    return normalized


def append_metric_archive_row(window: Any, metric_row: Any) -> Path:
    writer = ensure_temp_measurement_writer(window)
    row = _normalize_metric_row(metric_row)
    if row:
        try:
            writer.append_metrics([row])
        except Exception:
            pass
    return ensure_metric_archive_path(window)


def close_metric_archive(window: Any) -> None:
    handle = getattr(window, _ARCHIVE_HANDLE_ATTR, None)
    if handle is not None:
        try:
            handle.flush()
        finally:
            try:
                handle.close()
            finally:
                setattr(window, _ARCHIVE_HANDLE_ATTR, None)


def _row_timestamp_s(row: dict[str, Any], fallback_index: int) -> float:
    t_ms = row.get("t_ms")
    if t_ms is not None:
        try:
            return float(t_ms) / 1000.0
        except Exception:
            pass
    acquired_at_unix_ms = row.get("acquired_at_unix_ms")
    if acquired_at_unix_ms is not None:
        try:
            return float(acquired_at_unix_ms) / 1000.0
        except Exception:
            pass
    time_s = row.get("time_s")
    if time_s is not None:
        try:
            return float(time_s)
        except Exception:
            pass
    return float(fallback_index)


def load_metric_archive_history(
    path: Path,
    metric_names: set[str] | None = None,
    *,
    time_range_s: tuple[float, float] | None = None,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    if not path.exists():
        return {}

    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        series: dict[str, list[tuple[float, float]]] = {}
        with path.open("r", encoding="utf-8") as handle:
            for fallback_index, line in enumerate(handle):
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                timestamp_s = _row_timestamp_s(row, fallback_index)
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
                            if timestamp_s < start_s or timestamp_s > end_s:
                                continue
                for key, value in row.items():
                    if key in {"t_ms", "acquired_at_unix_ms", "time_s", "sample_index"}:
                        continue
                    if metric_names is not None and key not in metric_names:
                        continue
                    try:
                        numeric_value = float(value)
                    except Exception:
                        continue
                    series.setdefault(key, []).append((timestamp_s, numeric_value))

        result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for metric_name, points in series.items():
            if not points:
                continue
            times_s = np.asarray([p[0] for p in points], dtype=float)
            values = np.asarray([p[1] for p in points], dtype=float)
            result[metric_name] = (times_s, values)
        return result

    if suffix not in {".h5", ".hdf5"}:
        return {}

    series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    with h5py.File(path, "r") as handle:
        processed = handle.get("processed")
        if processed is None:
            return {}
        metrics = processed.get("metrics")
        if metrics is None:
            return {}
        time_ds = metrics.get("t_ms")
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
        for metric_name, dataset in metrics.items():
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
