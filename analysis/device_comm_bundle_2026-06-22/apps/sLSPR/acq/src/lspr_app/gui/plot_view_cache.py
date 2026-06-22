from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict, deque
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import numpy as np


@dataclass(slots=True)
class MetricDisplayCache:
    source_revision: int = 0
    source_len: int = 0
    target_points: int = 0
    stride: int = 1
    raw_block_size: int = 1
    combine_factor: int = 2
    pending_x: list[float] = field(default_factory=list)
    pending_y: list[float] = field(default_factory=list)
    recent_tail_x: deque[float] = field(default_factory=deque)
    recent_tail_y: deque[float] = field(default_factory=deque)
    recent_tail_max_points: int = 300
    x_display: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    y_display: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    levels: list[list["MetricCompressionBlock"]] = field(default_factory=list)
    display_revision: int = 0
    display_output_revision: int = 0
    last_display_signature: tuple[object, ...] | None = None
    rebuild_count: int = 0
    incremental_count: int = 0
    hit_count: int = 0
    last_mode: str = "empty"
    last_new_points_processed: int = 0
    last_base_blocks: int = 0
    last_levels: int = 0
    last_tail_groups_updated: int = 0
    last_display_level: int = -1
    last_display_blocks: int = 0
    last_trend_method: str = "first_last"
    last_recent_tail_configured: int = 300
    last_recent_tail_current: int = 0
    last_old_display_blocks: int = 0
    last_overlap_blocks_skipped: int = 0
    last_old_display_points: int = 0
    last_tail_display_points: int = 0
    last_total_display_points: int = 0
    last_window_start_x: float | None = None
    last_window_end_x: float | None = None
    last_append_ms: float | None = None
    last_assemble_ms: float | None = None
    last_full_rebuild_ms: float | None = None
    last_source_used: str = "empty"
    last_invalidation_reason: str = "unknown"
    archive_read_count: int = 0
    last_archive_points: int = 0


@dataclass(slots=True)
class RollingMetricDisplayCache:
    metric_name: str = ""
    window_s: float = 60.0
    latest_s: float | None = None
    window_start_x: float | None = None
    window_end_x: float | None = None
    raw_x: deque[float] = field(default_factory=deque)
    raw_y: deque[float] = field(default_factory=deque)
    compression: MetricDisplayCache = field(default_factory=MetricDisplayCache)
    pending_live_points: int = 0
    reload_request_id: int = 0
    applied_reload_request_id: int = 0
    stale_reload_count: int = 0
    archive_path: str | None = None


@dataclass(slots=True, frozen=True)
class MetricCompressionBlock:
    start_x: float
    end_x: float
    first_x: float
    first_y: float
    last_x: float
    last_y: float
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    mean_y: float
    count: int


def level_raw_weight(cache: MetricDisplayCache, level: int) -> int:
    raw_block_size = max(int(getattr(cache, "raw_block_size", 1)), 1)
    combine_factor = max(int(getattr(cache, "combine_factor", 2)), 2)
    return int(raw_block_size * (combine_factor ** max(int(level), 0)))


def _make_metric_compression_block(x: np.ndarray, y: np.ndarray) -> MetricCompressionBlock | None:
    if len(x) == 0 or len(y) == 0:
        return None
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return None
    xf = np.asarray(x[finite], dtype=np.float64)
    yf = np.asarray(y[finite], dtype=np.float64)
    if len(xf) == 0:
        return None
    min_i = int(np.argmin(yf))
    max_i = int(np.argmax(yf))
    return MetricCompressionBlock(
        start_x=float(xf[0]),
        end_x=float(xf[-1]),
        first_x=float(xf[0]),
        first_y=float(yf[0]),
        last_x=float(xf[-1]),
        last_y=float(yf[-1]),
        min_x=float(xf[min_i]),
        min_y=float(yf[min_i]),
        max_x=float(xf[max_i]),
        max_y=float(yf[max_i]),
        mean_y=float(np.mean(yf)),
        count=int(len(xf)),
    )


def _combine_metric_compression_blocks(blocks: list[MetricCompressionBlock]) -> MetricCompressionBlock | None:
    blocks = [block for block in blocks if block is not None and block.count > 0]
    if not blocks:
        return None
    first = blocks[0]
    last = blocks[-1]
    min_block = min(blocks, key=lambda block: block.min_y)
    max_block = max(blocks, key=lambda block: block.max_y)
    total_count = int(sum(block.count for block in blocks))
    weighted_mean = sum(block.mean_y * block.count for block in blocks) / max(total_count, 1)
    return MetricCompressionBlock(
        start_x=float(first.start_x),
        end_x=float(last.end_x),
        first_x=float(first.first_x),
        first_y=float(first.first_y),
        last_x=float(last.last_x),
        last_y=float(last.last_y),
        min_x=float(min_block.min_x),
        min_y=float(min_block.min_y),
        max_x=float(max_block.max_x),
        max_y=float(max_block.max_y),
        mean_y=float(weighted_mean),
        count=total_count,
    )


def _blocks_to_trend_arrays(
    blocks: list[MetricCompressionBlock],
    *,
    method: str = "first_last",
) -> tuple[np.ndarray, np.ndarray]:
    if not blocks:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty
    xs: list[float] = []
    ys: list[float] = []
    method = str(method or "first_last").lower()
    if method == "weighted_mean":
        for block in blocks:
            xs.append(0.5 * float(block.start_x + block.end_x))
            ys.append(float(block.mean_y))
    else:
        for block in blocks:
            xs.extend([float(block.first_x), float(block.last_x)])
            ys.extend([float(block.first_y), float(block.last_y)])
    return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)


def _blocks_to_envelope_line_arrays(
    blocks: list[MetricCompressionBlock],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if not blocks:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, empty, empty
    min_x: list[float] = []
    min_y: list[float] = []
    max_x: list[float] = []
    max_y: list[float] = []
    for block in blocks:
        start_x = float(min(block.start_x, block.end_x))
        end_x = float(max(block.start_x, block.end_x))
        min_x.extend([start_x, end_x])
        min_y.extend([float(block.min_y), float(block.min_y)])
        max_x.extend([start_x, end_x])
        max_y.extend([float(block.max_y), float(block.max_y)])
    return (
        np.asarray(min_x, dtype=np.float64),
        np.asarray(min_y, dtype=np.float64),
        np.asarray(max_x, dtype=np.float64),
        np.asarray(max_y, dtype=np.float64),
    )


def _recent_tail_arrays(cache: MetricDisplayCache) -> tuple[np.ndarray, np.ndarray]:
    if not cache.recent_tail_x or not cache.recent_tail_y:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty
    return (
        np.asarray(list(cache.recent_tail_x), dtype=np.float64),
        np.asarray(list(cache.recent_tail_y), dtype=np.float64),
    )


def _trim_recent_tail(cache: MetricDisplayCache) -> None:
    max_points = max(int(getattr(cache, "recent_tail_max_points", 0)), 0)
    while len(cache.recent_tail_x) > max_points:
        cache.recent_tail_x.popleft()
        cache.recent_tail_y.popleft()


def _set_recent_tail_from_arrays(
    cache: MetricDisplayCache,
    x: np.ndarray,
    y: np.ndarray,
) -> None:
    cache.recent_tail_x.clear()
    cache.recent_tail_y.clear()
    if len(x) == 0 or len(y) == 0:
        return
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return
    tail_x = np.asarray(x[finite], dtype=np.float64)
    tail_y = np.asarray(y[finite], dtype=np.float64)
    if len(tail_x) == 0 or len(tail_y) == 0:
        return
    tail_len = min(int(max(getattr(cache, "recent_tail_max_points", 0), 0)), len(tail_x))
    if tail_len <= 0:
        return
    tail_x = tail_x[-tail_len:]
    tail_y = tail_y[-tail_len:]
    cache.recent_tail_x.extend(float(value) for value in tail_x)
    cache.recent_tail_y.extend(float(value) for value in tail_y)


def _append_recent_tail_point(cache: MetricDisplayCache, x_value: float, y_value: float) -> None:
    if not np.isfinite(x_value) or not np.isfinite(y_value):
        return
    max_points = max(int(getattr(cache, "recent_tail_max_points", 0)), 0)
    if max_points <= 0:
        cache.recent_tail_x.clear()
        cache.recent_tail_y.clear()
        return
    cache.recent_tail_x.append(float(x_value))
    cache.recent_tail_y.append(float(y_value))
    _trim_recent_tail(cache)


def _build_absolute_metric_display_arrays(
    cache: MetricDisplayCache,
    *,
    target_points: int,
) -> tuple[np.ndarray, np.ndarray, int, int, int]:
    if not cache.levels:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, -1, 0, 0
    tail_x, tail_y = _recent_tail_arrays(cache)
    tail_points = int(len(tail_x))
    tail_start_x = float(tail_x[0]) if tail_points > 0 else None
    old_target_points = max(int(target_points) - tail_points, 1)
    blocks = _select_metric_compression_blocks(cache.levels, target_points=old_target_points, points_per_block=1)
    display_level = next((index for index, level in enumerate(cache.levels) if level is blocks), -1)
    old_blocks: list[MetricCompressionBlock] = []
    overlap_blocks = 0
    for block in blocks:
        if tail_start_x is not None and float(block.end_x) >= float(tail_start_x):
            overlap_blocks += 1
            continue
        old_blocks.append(block)
    x_old, y_old = _blocks_to_trend_arrays(old_blocks, method="weighted_mean")
    if len(x_old) == 0:
        display_x = tail_x
        display_y = tail_y
    elif tail_points == 0:
        display_x = x_old
        display_y = y_old
    else:
        display_x = np.concatenate((x_old, tail_x))
        display_y = np.concatenate((y_old, tail_y))
    return display_x, display_y, int(display_level), int(len(old_blocks)), int(overlap_blocks)


def _build_absolute_metric_envelope_arrays(
    cache: MetricDisplayCache,
    *,
    target_points: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    if not cache.levels:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, empty, empty, 0
    tail_x, tail_y = _recent_tail_arrays(cache)
    tail_points = int(len(tail_x))
    tail_start_x = float(tail_x[0]) if tail_points > 0 else None
    old_target_points = max(int(target_points) - tail_points, 1)
    blocks = _select_metric_compression_blocks(cache.levels, target_points=old_target_points, points_per_block=1)
    old_blocks: list[MetricCompressionBlock] = []
    overlap_blocks = 0
    for block in blocks:
        if tail_start_x is not None and float(block.end_x) >= float(tail_start_x):
            overlap_blocks += 1
            continue
        old_blocks.append(block)
    min_x, min_y, max_x, max_y = _blocks_to_envelope_line_arrays(old_blocks)
    if tail_points > 0:
        if len(min_x) == 0:
            min_x = tail_x
            min_y = tail_y
            max_x = tail_x
            max_y = tail_y
        else:
            min_x = np.concatenate((min_x, tail_x))
            min_y = np.concatenate((min_y, tail_y))
            max_x = np.concatenate((max_x, tail_x))
            max_y = np.concatenate((max_y, tail_y))
    return min_x, min_y, max_x, max_y, int(overlap_blocks)


def _build_windowed_metric_arrays(
    cache: MetricDisplayCache,
    *,
    target_points: int,
    window_start_x: float | None,
    window_end_x: float | None,
) -> tuple[np.ndarray, np.ndarray, int, int, int]:
    if not cache.levels:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, -1, 0, 0
    tail_x, tail_y = _recent_tail_arrays(cache)
    if len(tail_x) > 0:
        if window_start_x is not None:
            tail_visible = tail_x >= float(window_start_x)
            tail_x = tail_x[tail_visible]
            tail_y = tail_y[tail_visible]
        if window_end_x is not None:
            tail_visible = tail_x <= float(window_end_x)
            tail_x = tail_x[tail_visible]
            tail_y = tail_y[tail_visible]
    tail_points = int(len(tail_x))
    tail_start_x = float(tail_x[0]) if tail_points > 0 else None
    if tail_points > int(target_points):
        tail_x = tail_x[-int(target_points):]
        tail_y = tail_y[-int(target_points):]
        tail_points = int(len(tail_x))
        tail_start_x = float(tail_x[0]) if tail_points > 0 else None

    old_target_points = max(int(target_points) - tail_points, 1)
    best_blocks: list[MetricCompressionBlock] = []
    best_level = -1
    best_error = float("inf")
    for level_index, level in enumerate(cache.levels):
        if not level:
            continue
        visible_blocks: list[MetricCompressionBlock] = []
        for block in level:
            if window_end_x is not None and float(block.start_x) > float(window_end_x):
                break
            if window_start_x is not None and float(block.end_x) < float(window_start_x):
                continue
            if tail_start_x is not None and float(block.end_x) >= float(tail_start_x):
                continue
            visible_blocks.append(block)
        estimated_points = int(len(visible_blocks) * 2 + tail_points)
        error = abs(float(estimated_points - old_target_points - tail_points))
        if error < best_error:
            best_error = error
            best_level = level_index
            best_blocks = visible_blocks
            continue
        if error == best_error and len(visible_blocks) > len(best_blocks):
            best_level = level_index
            best_blocks = visible_blocks

    if best_level < 0:
        best_level = 0
        best_blocks = []

    x_old, y_old = _blocks_to_trend_arrays(best_blocks, method="weighted_mean")
    if len(x_old) == 0 and len(tail_x) == 0:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, int(best_level), 0, 0
    if len(x_old) == 0:
        display_x = tail_x
        display_y = tail_y
    elif len(tail_x) == 0:
        display_x = x_old
        display_y = y_old
    else:
        display_x = np.concatenate((x_old, tail_x))
        display_y = np.concatenate((y_old, tail_y))
    return display_x, display_y, int(best_level), int(len(best_blocks)), int(0)


def _build_windowed_metric_envelope_arrays(
    cache: MetricDisplayCache,
    *,
    target_points: int,
    window_start_x: float | None,
    window_end_x: float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    if not cache.levels:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, empty, empty, 0
    tail_x, tail_y = _recent_tail_arrays(cache)
    if len(tail_x) > 0:
        if window_start_x is not None:
            tail_visible = tail_x >= float(window_start_x)
            tail_x = tail_x[tail_visible]
            tail_y = tail_y[tail_visible]
        if window_end_x is not None:
            tail_visible = tail_x <= float(window_end_x)
            tail_x = tail_x[tail_visible]
            tail_y = tail_y[tail_visible]
    tail_points = int(len(tail_x))
    tail_start_x = float(tail_x[0]) if tail_points > 0 else None
    if tail_points > int(target_points):
        tail_x = tail_x[-int(target_points):]
        tail_y = tail_y[-int(target_points):]
        tail_points = int(len(tail_x))
        tail_start_x = float(tail_x[0]) if tail_points > 0 else None

    old_target_points = max(int(target_points) - tail_points, 1)
    best_blocks: list[MetricCompressionBlock] = []
    best_level = -1
    best_error = float("inf")
    for level_index, level in enumerate(cache.levels):
        if not level:
            continue
        visible_blocks: list[MetricCompressionBlock] = []
        for block in level:
            if window_end_x is not None and float(block.start_x) > float(window_end_x):
                break
            if window_start_x is not None and float(block.end_x) < float(window_start_x):
                continue
            if tail_start_x is not None and float(block.end_x) >= float(tail_start_x):
                continue
            visible_blocks.append(block)
        estimated_points = int(len(visible_blocks) * 2 + tail_points)
        error = abs(float(estimated_points - old_target_points - tail_points))
        if error < best_error:
            best_error = error
            best_level = level_index
            best_blocks = visible_blocks
            continue
        if error == best_error and len(visible_blocks) > len(best_blocks):
            best_level = level_index
            best_blocks = visible_blocks

    if best_level < 0:
        best_level = 0
        best_blocks = []

    min_x, min_y, max_x, max_y = _blocks_to_envelope_line_arrays(best_blocks)
    if len(min_x) == 0 and len(tail_x) == 0:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, empty, empty, 0
    if len(tail_x) > 0:
        if len(min_x) == 0:
            min_x = tail_x
            min_y = tail_y
            max_x = tail_x
            max_y = tail_y
        else:
            min_x = np.concatenate((min_x, tail_x))
            min_y = np.concatenate((min_y, tail_y))
            max_x = np.concatenate((max_x, tail_x))
            max_y = np.concatenate((max_y, tail_y))
    return min_x, min_y, max_x, max_y, int(best_level)


def _tail_to_trend_arrays(
    x: np.ndarray,
    y: np.ndarray,
    *,
    method: str = "weighted_mean",
) -> tuple[np.ndarray, np.ndarray]:
    if len(x) == 0 or len(y) == 0:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        empty = np.empty(0, dtype=np.float64)
        return empty, empty
    xf = np.asarray(x[finite], dtype=np.float64)
    yf = np.asarray(y[finite], dtype=np.float64)
    if method == "first_last" and len(xf) >= 2:
        return np.asarray([float(xf[0]), float(xf[-1])], dtype=np.float64), np.asarray([float(yf[0]), float(yf[-1])], dtype=np.float64)
    return np.asarray([float(np.mean(xf))], dtype=np.float64), np.asarray([float(np.mean(yf))], dtype=np.float64)


def _build_metric_compression_levels(x: np.ndarray, y: np.ndarray, *, raw_block_size: int, combine_factor: int) -> list[list[MetricCompressionBlock]]:
    blocks: list[MetricCompressionBlock] = []
    block_size = max(int(raw_block_size), 1)
    for start in range(0, len(x), block_size):
        stop = min(start + block_size, len(x))
        block = _make_metric_compression_block(x[start:stop], y[start:stop])
        if block is not None:
            blocks.append(block)
    levels: list[list[MetricCompressionBlock]] = [blocks]
    factor = max(int(combine_factor), 2)
    while len(levels[-1]) > factor:
        previous = levels[-1]
        next_level: list[MetricCompressionBlock] = []
        for start in range(0, len(previous), factor):
            combined = _combine_metric_compression_blocks(previous[start : start + factor])
            if combined is not None:
                next_level.append(combined)
        if not next_level:
            break
        levels.append(next_level)
    return levels


def _append_metric_compression_levels(
    levels: list[list[MetricCompressionBlock]],
    x_new: np.ndarray,
    y_new: np.ndarray,
    *,
    raw_block_size: int,
    combine_factor: int,
) -> list[list[MetricCompressionBlock]]:
    if not levels:
        levels = [[]]
    block_size = max(int(raw_block_size), 1)
    for start in range(0, len(x_new), block_size):
        stop = min(start + block_size, len(x_new))
        block = _make_metric_compression_block(x_new[start:stop], y_new[start:stop])
        if block is not None:
            _append_metric_compression_block(levels, block, combine_factor=combine_factor)
    return levels


def _append_metric_compression_block(
    levels: list[list[MetricCompressionBlock]],
    block: MetricCompressionBlock,
    *,
    combine_factor: int,
    level: int = 0,
) -> list[list[MetricCompressionBlock]]:
    while len(levels) <= level:
        levels.append([])
    levels[level].append(block)
    factor = max(int(combine_factor), 2)
    if len(levels[level]) % factor != 0:
        return levels
    parent = _combine_metric_compression_blocks(levels[level][-factor:])
    if parent is None:
        return levels
    return _append_metric_compression_block(levels, parent, combine_factor=combine_factor, level=level + 1)


def _select_metric_compression_blocks(
    levels: list[list[MetricCompressionBlock]],
    target_points: int,
    *,
    points_per_block: int = 2,
) -> list[MetricCompressionBlock]:
    if not levels:
        return []
    target_points = max(int(target_points), 1)
    points_per_block = max(int(points_per_block), 1)
    target_output_points = int(target_points)
    target_bins = max(int(np.ceil(float(target_points) / float(points_per_block))), 1)
    best_level = levels[0]
    best_error = float("inf")
    for level in levels:
        if not level:
            continue
        estimated_points = int(len(level) * points_per_block)
        error = abs(float(estimated_points - target_output_points))
        if error < best_error:
            best_error = error
            best_level = level
            continue
        if error == best_error and len(level) > len(best_level):
            best_level = level
        if len(level) <= target_bins and error <= best_error:
            best_level = level
    return best_level


def _display_signature(x: np.ndarray, y: np.ndarray) -> tuple[object, ...]:
    if len(x) == 0 or len(y) == 0:
        return (0,)
    return (
        int(len(x)),
        float(x[0]),
        float(x[-1]),
        float(np.nanmin(y)) if len(y) else float("nan"),
        float(np.nanmax(y)) if len(y) else float("nan"),
    )


def build_active_trace_series_token(window) -> tuple[object, ...]:
    selected_metrics = frozenset(getattr(window, "_selected_trace_metrics", lambda: [])())
    archive_path = getattr(window, "_metric_archive_path", None)
    archive_path = Path(archive_path).expanduser() if archive_path else None
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    if plot_view_cache is not None:
        try:
            live_states = tuple(
                state
                for metric_name in sorted(selected_metrics)
                if (state := plot_view_cache.live_absolute_metric_state(metric_name)) is not None
            )
        except Exception:
            live_states = ()
        if live_states:
            return ("live_absolute", live_states, tuple(sorted(selected_metrics)))
    if archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return ("archive", str(archive_path), int(mtime_ns), tuple(sorted(selected_metrics)))
    return ("empty", ())


def build_heatmap_history_token(window) -> tuple[object, ...]:
    archive_times = getattr(window, "_sensorgram_heatmap_archive_times", None)
    archive_matrix = getattr(window, "_sensorgram_heatmap_archive_matrix", None)
    archive_token = getattr(window, "_sensorgram_heatmap_archive_request_token", None)
    archive_wavelengths = getattr(window, "_sensorgram_heatmap_wavelengths", None)
    if isinstance(archive_times, np.ndarray) and isinstance(archive_matrix, np.ndarray) and len(archive_times) > 0 and archive_matrix.ndim == 2:
        return (
            "archive",
            archive_token,
            int(getattr(window, "_sensorgram_heatmap_archive_rows", len(archive_times))),
            id(archive_times),
            int(len(archive_times)),
            id(archive_matrix),
            int(archive_matrix.shape[0]),
            int(archive_matrix.shape[1]),
            id(archive_wavelengths),
            len(archive_wavelengths) if isinstance(archive_wavelengths, np.ndarray) else 0,
        )
    history = getattr(window, "_sensorgram_heatmap_history", None)
    wavelengths = getattr(window, "_sensorgram_heatmap_wavelengths", None)
    axis_key = getattr(window, "_sensorgram_heatmap_axis_key", None)
    return (
        id(history),
        int(getattr(window, "_sensorgram_heatmap_history_revision", 0)),
        len(history) if isinstance(history, list) else 0,
        axis_key,
        id(wavelengths),
        len(wavelengths) if isinstance(wavelengths, np.ndarray) else 0,
    )


def build_metric_series_token(window, metric_name: str) -> tuple[object, ...]:
    archive_path = getattr(window, "_metric_archive_path", None)
    archive_path = Path(archive_path).expanduser() if archive_path else None
    if archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return (str(metric_name), "archive", str(archive_path), int(mtime_ns))
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    return (str(metric_name), "empty", 0, 0, 0)


def build_heatmap_arrays(history: list[tuple[float, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    if not history:
        return np.empty(0, dtype=np.float64), np.empty((0, 0), dtype=np.float64)
    times = np.asarray([float(item[0]) for item in history], dtype=np.float64)
    rows = [np.asarray(item[1], dtype=np.float64) for item in history]
    if not rows:
        return np.empty(0, dtype=np.float64), np.empty((0, 0), dtype=np.float64)
    return times, np.vstack(rows)


def derive_heatmap_levels_from_matrix(matrix: np.ndarray, *, pad_fraction: float = 0.05) -> tuple[float, float]:
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.size == 0:
        return 0.0, 1.0
    finite = matrix[np.isfinite(matrix)]
    if finite.size == 0:
        return 0.0, 1.0
    low = float(np.min(finite))
    high = float(np.max(finite))
    if not np.isfinite(low) or not np.isfinite(high):
        return 0.0, 1.0
    if high <= low:
        high = low + 1e-6
    span = max(high - low, 1e-6)
    pad = max(span * float(pad_fraction), 1e-6)
    return low - pad, high + pad


def expand_heatmap_levels(
    current_levels: tuple[float, float] | None,
    row_values: np.ndarray,
    *,
    pad_fraction: float = 0.05,
) -> tuple[float, float]:
    row = np.asarray(row_values, dtype=np.float64)
    finite = row[np.isfinite(row)]
    if finite.size == 0:
        if current_levels is not None and len(current_levels) == 2:
            low, high = float(current_levels[0]), float(current_levels[1])
            if np.isfinite(low) and np.isfinite(high) and high > low:
                return low, high
        return 0.0, 1.0
    row_low = float(np.min(finite))
    row_high = float(np.max(finite))
    if current_levels is None or len(current_levels) != 2:
        return derive_heatmap_levels_from_matrix(finite, pad_fraction=pad_fraction)
    low = float(current_levels[0])
    high = float(current_levels[1])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low, high = row_low, row_high
    else:
        low = min(low, row_low)
        high = max(high, row_high)
    if high <= low:
        high = low + 1e-6
    span = max(high - low, 1e-6)
    pad = max(span * float(pad_fraction), 1e-6)
    return low - pad, high + pad


def select_heatmap_rows_for_view(
    times: np.ndarray,
    matrix: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    max_rows: int = 2000,
    view_height_px: float | None = None,
    oversample: float = 2.0,
    minimum_rows: int = 256,
    enabled: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    times = np.asarray(times, dtype=np.float64)
    matrix = np.asarray(matrix, dtype=np.float64)
    if len(times) == 0 or len(matrix) == 0:
        return times[:0], matrix[:0]
    if matrix.ndim != 2 or matrix.shape[0] != len(times):
        return times[:0], matrix[:0]
    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        visible = np.flatnonzero((times >= view_x_min) & (times <= view_x_max))
        if len(visible) > 0:
            start = max(int(visible[0]) - 1, 0)
            stop = min(int(visible[-1]) + 2, len(times))
            times = times[start:stop]
            matrix = matrix[start:stop]
    if not enabled:
        return times, matrix
    max_rows = _target_rows_from_height(
        view_height_px,
        enabled=enabled,
        max_rows=max_rows,
        minimum_rows=minimum_rows,
        oversample=oversample,
    )
    if max_rows <= 0 or len(times) <= max_rows:
        return times, matrix
    indices = np.linspace(0, len(times) - 1, num=max_rows, dtype=np.int64)
    indices = np.unique(indices)
    return times[indices], matrix[indices]


def _peak_preserving_downsample_indices(y: np.ndarray, target_bins: int) -> np.ndarray:
    if target_bins <= 0 or len(y) == 0:
        return np.empty(0, dtype=np.int64)
    if len(y) <= target_bins:
        return np.arange(len(y), dtype=np.int64)

    bins = max(int(target_bins), 1)
    edges = np.linspace(0, len(y), num=bins + 1, dtype=np.int64)
    keep: list[int] = []
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        segment = y[start:stop]
        finite = np.isfinite(segment)
        if not np.any(finite):
            keep.append(start + (stop - start - 1) // 2)
            continue
        finite_segment = segment[finite]
        finite_positions = np.flatnonzero(finite) + start
        keep.append(int(finite_positions[int(np.argmin(finite_segment))]))
        keep.append(int(finite_positions[int(np.argmax(finite_segment))]))
    if not keep:
        return np.arange(len(y), dtype=np.int64)
    return np.unique(np.asarray(keep, dtype=np.int64))


def _minmax_preserving_downsample_indices(y: np.ndarray, target_bins: int) -> np.ndarray:
    if target_bins <= 0 or len(y) == 0:
        return np.empty(0, dtype=np.int64)
    if len(y) <= target_bins * 2:
        return np.arange(len(y), dtype=np.int64)

    bins = max(int(target_bins), 1)
    edges = np.linspace(0, len(y), num=bins + 1, dtype=np.int64)
    keep: list[int] = [0, len(y) - 1]
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        segment = y[start:stop]
        finite = np.isfinite(segment)
        if not np.any(finite):
            continue
        finite_segment = segment[finite]
        finite_positions = np.flatnonzero(finite) + start
        min_index = int(finite_positions[int(np.argmin(finite_segment))])
        max_index = int(finite_positions[int(np.argmax(finite_segment))])
        keep.append(min_index)
        keep.append(max_index)
    if not keep:
        return np.arange(len(y), dtype=np.int64)
    return np.unique(np.asarray(keep, dtype=np.int64))


def quantize_view_target_points(points: int) -> int:
    points = max(int(points), 1)
    bucket = 1
    while bucket < points:
        bucket <<= 1
    return bucket


def _target_points_from_width(
    view_width_px: float | None,
    *,
    enabled: bool,
    minimum_points: int,
    oversample: float,
    default_points: int,
) -> int:
    max_points = max(int(default_points), 1)
    min_points = max(1, min(int(minimum_points), max_points))
    if not enabled:
        return quantize_view_target_points(max_points)
    if view_width_px is None or view_width_px <= 0:
        target_points = max_points
    else:
        target_points = max(min_points, int(float(view_width_px) * float(oversample)))
        target_points = min(target_points, max_points)
    return quantize_view_target_points(target_points)


def _target_rows_from_height(
    view_height_px: float | None,
    *,
    enabled: bool,
    max_rows: int,
    minimum_rows: int,
    oversample: float,
) -> int:
    if not enabled:
        return quantize_view_target_points(max_rows)
    target_rows = max_rows
    if view_height_px is not None and view_height_px > 0:
        target_rows = min(target_rows, max(minimum_rows, int(view_height_px * oversample)))
    return quantize_view_target_points(target_rows)


def _stride_sample_indices(length: int, stride: int) -> np.ndarray:
    if length <= 0:
        return np.empty(0, dtype=np.int64)
    stride = max(int(stride), 1)
    indices = np.arange(0, length, stride, dtype=np.int64)
    if indices.size == 0 or indices[-1] != length - 1:
        indices = np.append(indices, length - 1)
    return np.unique(indices)


def _absolute_metric_cache_key(token: tuple[object, ...], target_points: int) -> tuple[object, ...]:
    metric_name = token[0] if len(token) > 0 else None
    source_kind = token[1] if len(token) > 1 else None
    source_id = token[2] if len(token) > 2 else None
    return (metric_name, source_kind, source_id, int(target_points))


def _absolute_heatmap_cache_key(token: tuple[object, ...], target_rows: int) -> tuple[object, ...]:
    history_id = token[0] if len(token) > 0 else None
    axis_key = token[3] if len(token) > 3 else None
    wavelengths_id = token[4] if len(token) > 4 else None
    return (history_id, axis_key, wavelengths_id, int(target_rows))


def sample_absolute_metric_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_width_px: float | None = None,
    enabled: bool = True,
    minimum_points: int = 128,
    oversample: float = 1.0,
    default_points: int = 2048,
) -> tuple[np.ndarray, np.ndarray]:
    if len(x) == 0 or len(y) == 0:
        return x, y
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return x[:0], y[:0]
    x = x[finite]
    y = y[finite]
    if len(x) == 0:
        return x, y
    if not enabled:
        return x, y
    target_points = _target_points_from_width(
        view_width_px,
        enabled=enabled,
        minimum_points=minimum_points,
        oversample=oversample,
        default_points=default_points,
    )
    if len(x) <= target_points:
        return x, y
    target_bins = max(int(np.ceil(target_points / 2.0)), 1)
    indices = _minmax_preserving_downsample_indices(y, target_bins)
    return x[indices], y[indices]


def sample_absolute_heatmap_rows_for_view(
    times: np.ndarray,
    matrix: np.ndarray,
    *,
    view_height_px: float | None = None,
    enabled: bool = True,
    max_rows: int = 2000,
    minimum_rows: int = 256,
    oversample: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    times = np.asarray(times, dtype=np.float64)
    matrix = np.asarray(matrix, dtype=np.float64)
    if len(times) == 0 or len(matrix) == 0:
        return times[:0], matrix[:0]
    if matrix.ndim != 2 or matrix.shape[0] != len(times):
        return times[:0], matrix[:0]
    if not enabled:
        return times, matrix
    target_rows = _target_rows_from_height(
        view_height_px,
        enabled=enabled,
        max_rows=max_rows,
        minimum_rows=minimum_rows,
        oversample=oversample,
    )
    if len(times) <= target_rows:
        return times, matrix
    stride = max(int(np.ceil(len(times) / float(target_rows))), 1)
    indices = np.arange(0, len(times), stride, dtype=np.int64)
    if indices[-1] != len(times) - 1:
        indices = np.append(indices, len(times) - 1)
    return times[indices], matrix[indices]


def downsample_metric_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    view_width_px: float | None = None,
    enabled: bool = True,
    minimum_points: int = 128,
    oversample: float = 1.0,
    default_points: int = 2048,
) -> tuple[np.ndarray, np.ndarray]:
    if len(x) == 0 or len(y) == 0:
        return x, y
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return x[:0], y[:0]
    x = x[finite]
    y = y[finite]

    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        visible = np.flatnonzero((x >= view_x_min) & (x <= view_x_max))
        if len(visible) > 0:
            start = max(int(visible[0]) - 1, 0)
            stop = min(int(visible[-1]) + 2, len(x))
            x = x[start:stop]
            y = y[start:stop]

    if len(x) == 0:
        return x, y

    if not enabled:
        return x, y

    target_points = _target_points_from_width(
        view_width_px,
        enabled=enabled,
        minimum_points=minimum_points,
        oversample=oversample,
        default_points=default_points,
    )

    if len(x) <= target_points:
        return x, y

    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        start = int(np.searchsorted(x, view_x_min, side="left"))
        stop = int(np.searchsorted(x, view_x_max, side="right"))
        if stop > start:
            start = max(start - 1, 0)
            stop = min(stop + 1, len(x))
            x = x[start:stop]
            y = y[start:stop]

    target_bins = max(1, target_points // 2)
    keep = _peak_preserving_downsample_indices(y, target_bins)
    if len(keep) == 0:
        return x, y
    return x[keep], y[keep]


class PlotViewCache:
    def __init__(self, *, max_view_entries: int = 16) -> None:
        self._max_view_entries = max(int(max_view_entries), 1)
        self._active_trace_series_token: tuple[object, ...] | None = None
        self._active_trace_series_result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._metric_view_cache: OrderedDict[tuple[object, ...], tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._heatmap_arrays_token: tuple[object, ...] | None = None
        self._heatmap_arrays_result: tuple[np.ndarray, np.ndarray] = (
            np.empty(0, dtype=np.float64),
            np.empty((0, 0), dtype=np.float64),
        )
        self._heatmap_view_cache: OrderedDict[tuple[object, ...], tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._absolute_metric_view_cache: OrderedDict[tuple[object, ...], MetricDisplayCache] = OrderedDict()
        self._absolute_heatmap_view_cache: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
        self._live_absolute_metric_cache: dict[str, MetricDisplayCache] = {}
        self._live_absolute_archive_read_count = 0

    def clear(self) -> None:
        self._active_trace_series_token = None
        self._active_trace_series_result = {}
        self._metric_view_cache.clear()
        self._heatmap_arrays_token = None
        self._heatmap_arrays_result = (
            np.empty(0, dtype=np.float64),
            np.empty((0, 0), dtype=np.float64),
        )
        self._heatmap_view_cache.clear()
        self._absolute_metric_view_cache.clear()
        self._absolute_heatmap_view_cache.clear()
        self._live_absolute_metric_cache.clear()

    def cached_active_trace_series(
        self,
        token: tuple[object, ...],
        builder: Callable[[], dict[str, tuple[np.ndarray, np.ndarray]]],
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        if token == self._active_trace_series_token:
            return self._active_trace_series_result
        result = builder()
        self._active_trace_series_token = token
        self._active_trace_series_result = result
        return result

    def clear_live_absolute_metric_cache(self) -> None:
        self._live_absolute_metric_cache.clear()

    def clear_active_trace_series_cache(self) -> None:
        self._active_trace_series_token = None
        self._active_trace_series_result = {}

    def set_live_absolute_metric_tail_size(
        self,
        metric_names: set[str] | frozenset[str],
        *,
        recent_tail_points: int,
    ) -> bool:
        metric_names = frozenset(str(name) for name in metric_names)
        tail_points = max(int(recent_tail_points), 0)
        updated = False
        for metric_name in metric_names:
            cache = self._live_absolute_metric_cache.get(metric_name)
            if not isinstance(cache, MetricDisplayCache):
                continue
            cache.recent_tail_max_points = tail_points
            cache.last_recent_tail_configured = tail_points
            _trim_recent_tail(cache)
            cache.last_recent_tail_current = int(len(cache.recent_tail_x))
            cache.last_invalidation_reason = "tail_resize"
            cache.display_output_revision += 1
            if cache.levels:
                display_x, display_y, display_level, old_blocks, overlap_blocks = _build_absolute_metric_display_arrays(
                    cache,
                    target_points=int(cache.target_points or tail_points or 1),
                )
                cache.x_display = display_x
                cache.y_display = display_y
                cache.last_display_level = int(display_level)
                cache.last_display_blocks = int(old_blocks)
                cache.last_old_display_blocks = int(old_blocks)
                cache.last_overlap_blocks_skipped = int(overlap_blocks)
                cache.last_old_display_points = int(max(len(display_x) - len(cache.recent_tail_x), 0))
                cache.last_tail_display_points = int(len(cache.recent_tail_x))
                cache.last_total_display_points = int(len(display_x))
                cache.last_trend_method = "weighted_mean"
                cache.last_display_signature = _display_signature(display_x, display_y)
            updated = True
        return updated

    def live_absolute_metric_series(
        self,
        metric_names: set[str] | frozenset[str],
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for metric_name in metric_names:
            cache = self._live_absolute_metric_cache.get(metric_name)
            if not isinstance(cache, MetricDisplayCache):
                continue
            if len(cache.x_display) == 0 or len(cache.y_display) == 0:
                continue
            result[str(metric_name)] = (cache.x_display, cache.y_display)
        return result

    def live_absolute_metric_state(self, metric_name: str) -> tuple[object, ...] | None:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            return None
        return (
            str(metric_name),
            int(cache.display_output_revision),
            int(cache.source_len),
            int(cache.target_points),
            int(len(cache.x_display)),
            float(cache.x_display[0]) if len(cache.x_display) else None,
            float(cache.x_display[-1]) if len(cache.x_display) else None,
        )

    def seed_live_absolute_metric_cache(
        self,
        metric_name: str,
        x: np.ndarray,
        y: np.ndarray,
        *,
        target_points: int,
        recent_tail_points: int | None = None,
    ) -> None:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            cache = MetricDisplayCache(target_points=int(target_points))
            self._live_absolute_metric_cache[metric_name] = cache
        if recent_tail_points is not None:
            cache.recent_tail_max_points = max(int(recent_tail_points), 0)
        cache.source_len = 0
        cache.source_revision += 1
        cache.target_points = int(target_points)
        cache.last_invalidation_reason = "seed"
        cache.last_recent_tail_configured = int(max(getattr(cache, "recent_tail_max_points", 0), 0))
        cache.levels = _build_metric_compression_levels(
            np.asarray(x, dtype=np.float64),
            np.asarray(y, dtype=np.float64),
            raw_block_size=cache.raw_block_size,
            combine_factor=cache.combine_factor,
        )
        cache.source_len = int(min(len(x), len(y)))
        cache.pending_x.clear()
        cache.pending_y.clear()
        _set_recent_tail_from_arrays(cache, np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
        cache.last_recent_tail_current = int(len(cache.recent_tail_x))
        cache.last_archive_points = int(cache.source_len)
        cache.rebuild_count += 1
        display_x, display_y, display_level, old_blocks, overlap_blocks = _build_absolute_metric_display_arrays(
            cache,
            target_points=int(target_points),
        )
        cache.x_display = display_x
        cache.y_display = display_y
        cache.last_display_level = int(display_level)
        cache.last_display_blocks = int(old_blocks)
        cache.last_old_display_blocks = int(old_blocks)
        cache.last_overlap_blocks_skipped = int(overlap_blocks)
        cache.last_old_display_points = int(max(len(display_x) - len(cache.recent_tail_x), 0))
        cache.last_tail_display_points = int(len(cache.recent_tail_x))
        cache.last_total_display_points = int(len(display_x))
        cache.last_trend_method = "weighted_mean"
        cache.last_display_signature = _display_signature(display_x, display_y)
        cache.display_output_revision += 1
        cache.last_mode = "seeded"
        cache.last_source_used = "archive_seed" if cache.last_archive_points > 0 else "seed"

    def append_live_absolute_metric_point(
        self,
        metric_name: str,
        x_value: float,
        y_value: float,
        *,
        target_points: int,
        recent_tail_points: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            cache = MetricDisplayCache(target_points=int(target_points))
            self._live_absolute_metric_cache[metric_name] = cache
        if recent_tail_points is not None:
            cache.recent_tail_max_points = max(int(recent_tail_points), 0)
        started = perf_counter()
        cache.target_points = int(target_points)
        cache.source_len += 1
        cache.source_revision += 1
        cache.last_invalidation_reason = "live_append"
        if not cache.levels:
            cache.levels = [[]]
        cache.pending_x.append(float(x_value))
        cache.pending_y.append(float(y_value))
        _append_recent_tail_point(cache, float(x_value), float(y_value))
        cache.last_recent_tail_configured = int(max(getattr(cache, "recent_tail_max_points", 0), 0))
        cache.last_recent_tail_current = int(len(cache.recent_tail_x))
        block_size = max(int(cache.raw_block_size), 1)
        appended_blocks = 0
        if len(cache.pending_x) >= block_size:
            pending_x = np.asarray(cache.pending_x, dtype=np.float64)
            pending_y = np.asarray(cache.pending_y, dtype=np.float64)
            cache.pending_x.clear()
            cache.pending_y.clear()
            cache.levels = _append_metric_compression_levels(
                cache.levels,
                pending_x,
                pending_y,
                raw_block_size=cache.raw_block_size,
                combine_factor=cache.combine_factor,
            )
            appended_blocks = int(np.ceil(float(len(pending_x)) / float(block_size)))
        display_x, display_y, display_level, old_blocks, overlap_blocks = _build_absolute_metric_display_arrays(
            cache,
            target_points=int(target_points),
        )
        cache.x_display, cache.y_display = display_x, display_y
        cache.last_display_level = int(display_level)
        cache.last_display_blocks = int(old_blocks)
        cache.last_old_display_blocks = int(old_blocks)
        cache.last_overlap_blocks_skipped = int(overlap_blocks)
        cache.last_old_display_points = int(max(len(display_x) - len(cache.recent_tail_x), 0))
        cache.last_tail_display_points = int(len(cache.recent_tail_x))
        cache.last_total_display_points = int(len(display_x))
        cache.last_trend_method = "weighted_mean"
        cache.last_display_signature = _display_signature(cache.x_display, cache.y_display)
        cache.display_output_revision += 1
        cache.last_mode = "incremental"
        cache.last_source_used = "cache_incremental"
        cache.last_new_points_processed = 1
        cache.last_tail_groups_updated = appended_blocks
        cache.last_append_ms = (perf_counter() - started) * 1000.0
        return cache.x_display, cache.y_display

    def live_absolute_metric_view(
        self,
        metric_name: str,
        *,
        target_points: int,
        recent_tail_points: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            return None
        requested_recent_tail_points = max(
            int(recent_tail_points) if recent_tail_points is not None else int(cache.recent_tail_max_points),
            0,
        )
        cache.recent_tail_max_points = requested_recent_tail_points
        cache.last_recent_tail_configured = requested_recent_tail_points
        cache.last_recent_tail_current = int(len(cache.recent_tail_x))
        if cache.target_points != int(target_points):
            cache.target_points = int(target_points)
        if not cache.levels:
            return None
        display_x, display_y, display_level, old_blocks, overlap_blocks = _build_absolute_metric_display_arrays(
            cache,
            target_points=int(target_points),
        )
        cache.x_display = display_x
        cache.y_display = display_y
        cache.last_display_level = int(display_level)
        cache.last_display_blocks = int(old_blocks)
        cache.last_old_display_blocks = int(old_blocks)
        cache.last_overlap_blocks_skipped = int(overlap_blocks)
        cache.last_old_display_points = int(max(len(display_x) - len(cache.recent_tail_x), 0))
        cache.last_tail_display_points = int(len(cache.recent_tail_x))
        cache.last_total_display_points = int(len(display_x))
        cache.last_trend_method = "weighted_mean"
        signature = _display_signature(display_x, display_y)
        if signature != cache.last_display_signature:
            cache.display_output_revision += 1
            cache.last_display_signature = signature
        cache.last_mode = "live_recompute"
        cache.last_source_used = "cache_recompute"
        cache.last_invalidation_reason = "reuse"
        return display_x, display_y

    def live_absolute_metric_envelope_view(
        self,
        metric_name: str,
        *,
        target_points: int,
        recent_tail_points: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache) or not cache.levels:
            return None
        requested_recent_tail_points = max(
            int(recent_tail_points) if recent_tail_points is not None else int(cache.recent_tail_max_points),
            0,
        )
        cache.recent_tail_max_points = requested_recent_tail_points
        cache.last_recent_tail_configured = requested_recent_tail_points
        cache.last_recent_tail_current = int(len(cache.recent_tail_x))
        min_x, min_y, max_x, max_y, overlap_blocks = _build_absolute_metric_envelope_arrays(
            cache,
            target_points=int(target_points),
        )
        cache.last_overlap_blocks_skipped = int(overlap_blocks)
        cache.last_tail_display_points = int(len(cache.recent_tail_x))
        cache.last_total_display_points = int(len(min_x))
        return min_x, min_y, max_x, max_y

    def refresh_live_absolute_metric_cache(
        self,
        metric_names: set[str] | frozenset[str],
        *,
        target_points: int,
        archive_path: Path | None = None,
    ) -> bool:
        metric_names = frozenset(str(name) for name in metric_names)
        refreshed = False
        for metric_name in metric_names:
            cache = self._live_absolute_metric_cache.get(metric_name)
            if isinstance(cache, MetricDisplayCache):
                cache.last_invalidation_reason = "reuse"
                cache.target_points = int(target_points)
            if self.live_absolute_metric_view(metric_name, target_points=int(target_points)) is not None:
                refreshed = True
        return refreshed

    def _rebuild_metric_compression_cache(
        self,
        cache: MetricDisplayCache,
        x: np.ndarray,
        y: np.ndarray,
        *,
        target_points: int,
    ) -> int:
        started = perf_counter()
        cache.source_len = 0
        cache.target_points = int(target_points)
        cache.levels = _build_metric_compression_levels(
            np.asarray(x, dtype=np.float64),
            np.asarray(y, dtype=np.float64),
            raw_block_size=cache.raw_block_size,
            combine_factor=cache.combine_factor,
        )
        cache.source_len = int(len(x))
        cache.pending_x.clear()
        cache.pending_y.clear()
        cache.display_revision += 1
        cache.rebuild_count += 1
        cache.last_mode = "full_rebuild"
        cache.last_new_points_processed = int(len(x))
        cache.last_base_blocks = len(cache.levels[0]) if cache.levels else 0
        cache.last_levels = len(cache.levels)
        cache.last_tail_groups_updated = cache.last_base_blocks
        cache.last_full_rebuild_ms = (perf_counter() - started) * 1000.0
        cache.last_append_ms = None
        return cache.last_base_blocks

    def _append_metric_compression_cache(
        self,
        cache: MetricDisplayCache,
        x_new: np.ndarray,
        y_new: np.ndarray,
    ) -> int:
        if len(x_new) == 0 or len(y_new) == 0:
            cache.last_new_points_processed = 0
            cache.last_tail_groups_updated = 0
            cache.last_append_ms = 0.0
            return 0
        started = perf_counter()
        block_size = max(int(cache.raw_block_size), 1)
        block_count = int(np.ceil(float(len(x_new)) / float(block_size)))
        cache.levels = _append_metric_compression_levels(
            cache.levels,
            np.asarray(x_new, dtype=np.float64),
            np.asarray(y_new, dtype=np.float64),
            raw_block_size=cache.raw_block_size,
            combine_factor=cache.combine_factor,
        )
        cache.source_len += int(len(x_new))
        cache.display_revision += 1
        cache.incremental_count += 1
        cache.last_mode = "incremental"
        cache.last_new_points_processed = int(len(x_new))
        cache.last_base_blocks = len(cache.levels[0]) if cache.levels else 0
        cache.last_levels = len(cache.levels)
        cache.last_tail_groups_updated = block_count
        cache.last_append_ms = (perf_counter() - started) * 1000.0
        return block_count

    def _select_metric_compression_blocks(
        self,
        cache: MetricDisplayCache,
        *,
        target_points: int,
    ) -> tuple[int, list[MetricCompressionBlock]]:
        if not cache.levels:
            return -1, []
        target_bins = max(int(np.ceil(float(max(target_points, 1)) / 2.0)), 1)
        chosen_level = 0
        chosen_blocks = cache.levels[0]
        for level_index, level in enumerate(cache.levels):
            chosen_level = level_index
            chosen_blocks = level
            if len(level) <= target_bins:
                break
        return chosen_level, chosen_blocks

    def metric_view(
        self,
        token: tuple[object, ...],
        x: np.ndarray,
        y: np.ndarray,
        *,
        view_x_min: float | None = None,
        view_x_max: float | None = None,
        view_width_px: float | None = None,
        enabled: bool = True,
        minimum_points: int = 128,
        oversample: float = 1.0,
        default_points: int = 2048,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_points = _target_points_from_width(
            view_width_px,
            enabled=enabled,
            minimum_points=minimum_points,
            oversample=oversample,
            default_points=default_points,
        )
        cache_key = (
            token,
            round(view_x_min, 6) if view_x_min is not None else None,
            round(view_x_max, 6) if view_x_max is not None else None,
            int(target_points),
        )
        cached = self._metric_view_cache.get(cache_key)
        if cached is not None:
            self._metric_view_cache.move_to_end(cache_key)
            return cached
        result = downsample_metric_series_for_view(
            x,
            y,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            view_width_px=view_width_px,
            enabled=enabled,
            minimum_points=minimum_points,
            oversample=oversample,
            default_points=default_points,
        )
        self._metric_view_cache[cache_key] = result
        self._metric_view_cache.move_to_end(cache_key)
        while len(self._metric_view_cache) > self._max_view_entries:
            self._metric_view_cache.popitem(last=False)
        return result

    def heatmap_arrays(
        self,
        token: tuple[object, ...],
        builder: Callable[[], tuple[np.ndarray, np.ndarray]],
    ) -> tuple[np.ndarray, np.ndarray]:
        if token == self._heatmap_arrays_token:
            return self._heatmap_arrays_result
        result = builder()
        self._heatmap_arrays_token = token
        self._heatmap_arrays_result = result
        return result

    def heatmap_arrays_from_history(
        self,
        token: tuple[object, ...],
        history: list[tuple[float, np.ndarray]],
        builder: Callable[[list[tuple[float, np.ndarray]]], tuple[np.ndarray, np.ndarray]],
    ) -> tuple[np.ndarray, np.ndarray]:
        if token == self._heatmap_arrays_token:
            return self._heatmap_arrays_result
        previous_token = self._heatmap_arrays_token
        previous_times, previous_matrix = self._heatmap_arrays_result
        if (
            isinstance(previous_token, tuple)
            and len(previous_token) >= 4
            and len(token) >= 4
            and previous_token[0] == token[0]
            and previous_token[3] == token[3]
            and len(history) == len(previous_times) + 1
            and len(previous_times) > 0
        ):
            last_time, last_row = history[-1]
            times = np.concatenate((previous_times, np.asarray([float(last_time)], dtype=np.float64)))
            row = np.asarray(last_row, dtype=np.float64)
            if previous_matrix.ndim == 2 and previous_matrix.shape[1] == len(row):
                matrix = np.vstack((previous_matrix, row[np.newaxis, :]))
                self._heatmap_arrays_token = token
                self._heatmap_arrays_result = (times, matrix)
                return self._heatmap_arrays_result
        result = builder(history)
        self._heatmap_arrays_token = token
        self._heatmap_arrays_result = result
        return result

    def absolute_metric_view(
        self,
        token: tuple[object, ...],
        x: np.ndarray,
        y: np.ndarray,
        *,
        view_width_px: float | None = None,
        enabled: bool = True,
        minimum_points: int = 128,
        oversample: float = 1.0,
        default_points: int = 2048,
        recent_tail_points: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_points = _target_points_from_width(
            view_width_px,
            enabled=enabled,
            minimum_points=minimum_points,
            oversample=oversample,
            default_points=default_points,
        )
        cache_key = _absolute_metric_cache_key(token, target_points)
        source_revision = token[3] if len(token) > 3 else None
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if len(x) == 0 or len(y) == 0:
            result = (x[:0], y[:0])
            cache = self._absolute_metric_view_cache.get(cache_key)
            if not isinstance(cache, MetricDisplayCache):
                cache = MetricDisplayCache()
            cache.source_revision = int(source_revision or 0)
            cache.source_len = 0
            cache.target_points = int(target_points)
            cache.x_display = result[0]
            cache.y_display = result[1]
            cache.levels = []
            cache.last_mode = "empty"
            cache.display_output_revision += 1
            cache.last_display_signature = _display_signature(cache.x_display, cache.y_display)
            self._absolute_metric_view_cache[cache_key] = cache
            return result
        if not enabled:
            cache = self._absolute_metric_view_cache.get(cache_key)
            if not isinstance(cache, MetricDisplayCache):
                cache = MetricDisplayCache()
            cache.source_revision = int(source_revision or 0)
            cache.source_len = len(x)
            cache.target_points = int(target_points)
            cache.x_display = x
            cache.y_display = y
            cache.levels = []
            cache.last_mode = "disabled"
            cache.display_output_revision += 1
            cache.last_display_signature = _display_signature(cache.x_display, cache.y_display)
            self._absolute_metric_view_cache[cache_key] = cache
            return x, y

        cached = self._absolute_metric_view_cache.get(cache_key)
        if not isinstance(cached, MetricDisplayCache):
            cached = MetricDisplayCache(target_points=int(target_points))
            self._absolute_metric_view_cache[cache_key] = cached
        requested_recent_tail_points = max(
            int(recent_tail_points) if recent_tail_points is not None else int(getattr(cached, "recent_tail_max_points", 300)),
            0,
        )
        if (
            cached.source_len == len(x)
            and cached.target_points == int(target_points)
            and cached.source_revision == int(source_revision or 0)
            and cached.last_recent_tail_configured == int(requested_recent_tail_points)
        ):
            cached.hit_count += 1
            cached.last_mode = "hit"
            self._absolute_metric_view_cache.move_to_end(cache_key)
            return cached.x_display, cached.y_display
        cached.recent_tail_max_points = int(requested_recent_tail_points)
        cached.last_recent_tail_configured = int(requested_recent_tail_points)
        if (
            cached.source_len > len(x)
            or cached.source_len < 0
            or cached.source_revision > int(source_revision or 0)
            or not cached.levels
        ):
            self._rebuild_metric_compression_cache(cached, x, y, target_points=target_points)
            _set_recent_tail_from_arrays(cached, x, y)
        else:
            old_len = int(cached.source_len)
            self._append_metric_compression_cache(cached, x[old_len:], y[old_len:])
            cached.target_points = int(target_points)
            cached.source_revision = int(source_revision or 0)
            if len(x) > old_len:
                tail_x = x[old_len:]
                tail_y = y[old_len:]
                for tail_x_value, tail_y_value in zip(tail_x, tail_y, strict=False):
                    _append_recent_tail_point(cached, float(tail_x_value), float(tail_y_value))
        cached.last_recent_tail_current = int(len(cached.recent_tail_x))
        assemble_started = perf_counter()
        display_x, display_y, display_level, old_blocks, overlap_blocks = _build_absolute_metric_display_arrays(
            cached,
            target_points=int(target_points),
        )
        cached.last_assemble_ms = (perf_counter() - assemble_started) * 1000.0
        cached.last_display_level = int(display_level)
        cached.last_display_blocks = int(old_blocks)
        cached.last_old_display_blocks = int(old_blocks)
        cached.last_overlap_blocks_skipped = int(overlap_blocks)
        cached.last_old_display_points = int(max(len(display_x) - len(cached.recent_tail_x), 0))
        cached.last_tail_display_points = int(len(cached.recent_tail_x))
        cached.last_total_display_points = int(len(display_x))
        cached.last_trend_method = "weighted_mean"
        signature = _display_signature(display_x, display_y)
        if signature != cached.last_display_signature:
            cached.display_output_revision += 1
            cached.last_display_signature = signature
        cached.x_display = display_x
        cached.y_display = display_y
        cached.target_points = int(target_points)
        cached.source_revision = int(source_revision or 0)
        self._absolute_metric_view_cache.move_to_end(cache_key)
        while len(self._absolute_metric_view_cache) > self._max_view_entries:
            self._absolute_metric_view_cache.popitem(last=False)
        return display_x, display_y

    def absolute_metric_envelope_view(
        self,
        token: tuple[object, ...],
        x: np.ndarray,
        y: np.ndarray,
        *,
        view_width_px: float | None = None,
        enabled: bool = True,
        minimum_points: int = 128,
        oversample: float = 1.0,
        default_points: int = 2048,
        recent_tail_points: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        target_points = _target_points_from_width(
            view_width_px,
            enabled=enabled,
            minimum_points=minimum_points,
            oversample=oversample,
            default_points=default_points,
        )
        cache_key = _absolute_metric_cache_key(token, target_points)
        cached = self._absolute_metric_view_cache.get(cache_key)
        if not isinstance(cached, MetricDisplayCache):
            return (np.empty(0, dtype=np.float64),) * 4
        if not cached.levels:
            return (np.empty(0, dtype=np.float64),) * 4
        if recent_tail_points is not None:
            cached.recent_tail_max_points = max(int(recent_tail_points), 0)
        cached.last_recent_tail_configured = int(max(cached.recent_tail_max_points, 0))
        cached.last_recent_tail_current = int(len(cached.recent_tail_x))
        min_x, min_y, max_x, max_y, overlap_blocks = _build_absolute_metric_envelope_arrays(
            cached,
            target_points=int(target_points),
        )
        cached.last_overlap_blocks_skipped = int(overlap_blocks)
        cached.last_tail_display_points = int(len(cached.recent_tail_x))
        cached.last_total_display_points = int(len(min_x))
        return min_x, min_y, max_x, max_y

    def absolute_metric_display_state(self, token: tuple[object, ...]) -> tuple[object, ...] | None:
        for cache_key, cached in reversed(list(self._absolute_metric_view_cache.items())):
            if not isinstance(cached, MetricDisplayCache):
                continue
            if len(cache_key) < 3:
                continue
            if cache_key[0] != token[0] or cache_key[1] != token[1] or cache_key[2] != token[2]:
                continue
            return (
                cache_key,
                int(cached.display_output_revision),
                int(len(cached.x_display)),
                float(cached.x_display[0]) if len(cached.x_display) else None,
                float(cached.x_display[-1]) if len(cached.x_display) else None,
            )
        return None

    def metric_cache_debug_snapshot(self) -> dict[str, object]:
        modes: dict[str, object] = {}
        for key, cached in self._absolute_metric_view_cache.items():
            if not isinstance(cached, MetricDisplayCache):
                continue
            level_counts = [len(level) for level in cached.levels]
            modes[f"absolute:{key}"] = {
                "view_mode": "absolute",
                "raw_block_size": int(cached.raw_block_size),
                "combine_factor": int(cached.combine_factor),
                "recent_tail_points_configured": int(cached.recent_tail_max_points),
                "recent_tail_points_current": int(len(cached.recent_tail_x)),
                "source_len": int(cached.source_len),
                "source_points": int(cached.source_len),
                "target_points": int(cached.target_points),
                "display_len": int(len(cached.x_display)),
                "display_points": int(len(cached.x_display)),
                "last_mode": str(cached.last_mode),
                "trend_method": str(cached.last_trend_method),
                "last_source_used": str(cached.last_source_used),
                "last_invalidation_reason": str(cached.last_invalidation_reason),
                "archive_read_count": int(cached.archive_read_count),
                "last_archive_points": int(cached.last_archive_points),
                "hits": int(cached.hit_count),
                "incremental": int(cached.incremental_count),
                "rebuilds": int(cached.rebuild_count),
                "levels": level_counts,
                "level_counts": level_counts,
                "level_weights": [level_raw_weight(cached, level_index) for level_index in range(len(cached.levels))],
                "new_points": int(cached.last_new_points_processed),
                "base_blocks": int(cached.last_base_blocks),
                "level_count": int(cached.last_levels),
                "tail_groups_updated": int(cached.last_tail_groups_updated),
                "display_level": int(cached.last_display_level),
                "selected_level": int(cached.last_display_level),
                "selected_level_raw_weight": level_raw_weight(cached, cached.last_display_level) if cached.last_display_level >= 0 else 0,
                "display_blocks": int(cached.last_display_blocks),
                "display_old_blocks": int(cached.last_old_display_blocks),
                "display_overlap_blocks_skipped": int(cached.last_overlap_blocks_skipped),
                "display_old_points": int(cached.last_old_display_points),
                "display_tail_points": int(cached.last_tail_display_points),
                "display_total_points": int(cached.last_total_display_points),
                "append_ms": cached.last_append_ms,
                "display_assembly_ms": cached.last_assemble_ms,
                "assemble_ms": cached.last_assemble_ms,
                "full_rebuild_ms": cached.last_full_rebuild_ms,
                "window_start_x": cached.last_window_start_x,
                "window_end_x": cached.last_window_end_x,
            }
        return modes

    def absolute_heatmap_view(
        self,
        token: tuple[object, ...],
        times: np.ndarray,
        matrix: np.ndarray,
        *,
        view_height_px: float | None = None,
        enabled: bool = True,
        max_rows: int = 2000,
        minimum_rows: int = 256,
        oversample: float = 2.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_rows = _target_rows_from_height(
            view_height_px,
            enabled=enabled,
            max_rows=max_rows,
            minimum_rows=minimum_rows,
            oversample=oversample,
        )
        cache_key = _absolute_heatmap_cache_key(token, target_rows)
        times = np.asarray(times, dtype=np.float64)
        matrix = np.asarray(matrix, dtype=np.float64)
        if len(times) == 0 or len(matrix) == 0:
            result = (times[:0], matrix[:0])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": 0,
                "stride": 1,
                "result": result,
            }
            return result
        if matrix.ndim != 2 or matrix.shape[0] != len(times):
            result = (times[:0], matrix[:0])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": 0,
                "stride": 1,
                "result": result,
            }
            return result
        if not enabled:
            result = (times, matrix)
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": len(times),
                "stride": 1,
                "result": result,
            }
            return result

        desired_stride = max(1, int(np.ceil(len(times) / float(max(target_rows, 1)))))
        cached = self._absolute_heatmap_view_cache.get(cache_key)
        if not isinstance(cached, dict) or int(cached.get("source_len", -1)) > len(times):
            cached = None
        if cached is None or int(cached.get("stride", 0)) != desired_stride:
            indices = _stride_sample_indices(len(times), desired_stride)
            result = (times[indices], matrix[indices])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": len(times),
                "stride": desired_stride,
                "result": result,
            }
            self._absolute_heatmap_view_cache.move_to_end(cache_key)
            while len(self._absolute_heatmap_view_cache) > self._max_view_entries:
                self._absolute_heatmap_view_cache.popitem(last=False)
            return result

        sampled_times, sampled_matrix = cached["result"]  # type: ignore[index]
        sampled_times = np.asarray(sampled_times, dtype=np.float64)
        sampled_matrix = np.asarray(sampled_matrix, dtype=np.float64)
        previous_len = int(cached.get("source_len", len(times)))
        if previous_len == len(times):
            return sampled_times, sampled_matrix
        if previous_len < 0 or previous_len > len(times):
            indices = _stride_sample_indices(len(times), desired_stride)
            result = (times[indices], matrix[indices])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": len(times),
                "stride": desired_stride,
                "result": result,
            }
            return result
        first_new_index = ((previous_len + desired_stride - 1) // desired_stride) * desired_stride
        if first_new_index < len(times):
            new_indices = _stride_sample_indices(len(times) - first_new_index, desired_stride) + first_new_index
            if len(new_indices) > 0:
                sampled_times = np.concatenate((sampled_times, times[new_indices]))
                sampled_matrix = np.vstack((sampled_matrix, matrix[new_indices]))
        result = (sampled_times, sampled_matrix)
        self._absolute_heatmap_view_cache[cache_key] = {
            "source_len": len(times),
            "stride": desired_stride,
            "result": result,
        }
        self._absolute_heatmap_view_cache.move_to_end(cache_key)
        while len(self._absolute_heatmap_view_cache) > self._max_view_entries:
            self._absolute_heatmap_view_cache.popitem(last=False)
        return result

    def refresh_live_absolute_heatmap_cache(
        self,
        token: tuple[object, ...],
        history: list[tuple[float, np.ndarray]],
        *,
        max_rows: int,
        view_height_px: float | None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        times, matrix = self.heatmap_arrays_from_history(token, history, build_heatmap_arrays)
        if len(times) == 0 or len(matrix) == 0:
            return None
        return self.absolute_heatmap_view(
            token,
            times,
            matrix,
            view_height_px=view_height_px,
            enabled=True,
            max_rows=max_rows,
            minimum_rows=256,
            oversample=2.0,
        )

    def heatmap_view(
        self,
        token: tuple[object, ...],
        times: np.ndarray,
        matrix: np.ndarray,
        *,
        view_x_min: float | None = None,
        view_x_max: float | None = None,
        max_rows: int = 2000,
        view_height_px: float | None = None,
        oversample: float = 2.0,
        minimum_rows: int = 256,
        enabled: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_rows = _target_rows_from_height(
            view_height_px,
            enabled=enabled,
            max_rows=max_rows,
            minimum_rows=minimum_rows,
            oversample=oversample,
        )
        cache_key = (
            token,
            round(view_x_min, 6) if view_x_min is not None else None,
            round(view_x_max, 6) if view_x_max is not None else None,
            int(target_rows),
        )
        cached = self._heatmap_view_cache.get(cache_key)
        if cached is not None:
            self._heatmap_view_cache.move_to_end(cache_key)
            return cached
        result = select_heatmap_rows_for_view(
            times,
            matrix,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            max_rows=target_rows,
            view_height_px=view_height_px,
            oversample=oversample,
            minimum_rows=minimum_rows,
            enabled=enabled,
        )
        self._heatmap_view_cache[cache_key] = result
        self._heatmap_view_cache.move_to_end(cache_key)
        while len(self._heatmap_view_cache) > self._max_view_entries:
            self._heatmap_view_cache.popitem(last=False)
        return result
