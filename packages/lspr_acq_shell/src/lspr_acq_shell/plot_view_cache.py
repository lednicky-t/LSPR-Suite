"""Multi-resolution cache/downsampling engine for rendering large time-series
curves (sensorgram metrics, spectra) without redrawing every raw point.

Extracted from singleLSPR Acquisition's `gui/plot_view_cache.py` (Phase 1,
2026-08-07). Genuinely curve-data-shaped (x/y arrays in, x/y arrays out) and
already used for both the sensorgram and spectrum plots in sLSPR acq - only
2 of this file's original ~30 top-level functions ever touched the app's
main window (`build_active_trace_series_token`/`build_metric_series_token`,
small cache-invalidation-token helpers that read a few window attributes);
those two stayed behind in sLSPR acq's own `gui/plot_view_cache.py` since
they're app-specific glue, not part of the reusable engine. Everything below
has zero Qt/window coupling.

`PlotViewCache` is the main entry point: an instance owns per-metric
`MetricDisplayCache` state (a multi-level pyramid of `MetricCompressionBlock`
summaries - min/max/mean/first/last per block, at increasing block sizes) and
exposes view-building methods (`metric_view`, `absolute_metric_view`,
`absolute_metric_envelope_view`, live-append/seed/rebase methods for
streaming data) that pick the coarsest pyramid level that still meets a
target on-screen point count, so a plot with millions of accumulated points
renders in roughly constant time regardless of session length.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict, deque
from collections.abc import Callable
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


def _trim_live_cache_l0_if_needed(cache: MetricDisplayCache, max_l0_blocks: int) -> None:
    """Cap level-0 block count to prevent unbounded memory growth on long sessions.

    Only level 0 (finest granularity) is trimmed.  Higher levels are kept intact
    so full-session history remains accessible at coarser zoom levels.
    """
    if max_l0_blocks <= 0 or not cache.levels or not cache.levels[0]:
        return
    l0 = cache.levels[0]
    if len(l0) <= max_l0_blocks:
        return
    keep = max(max_l0_blocks // 2, 1)
    cache.levels[0] = l0[-keep:]


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
    def __init__(self, *, max_view_entries: int = 16, max_live_cache_l0_blocks: int = 36_000) -> None:
        self._max_view_entries = max(int(max_view_entries), 1)
        self._max_live_cache_l0_blocks = max(int(max_live_cache_l0_blocks), 0)
        self._active_trace_series_token: tuple[object, ...] | None = None
        self._active_trace_series_result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._metric_view_cache: OrderedDict[tuple[object, ...], tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._absolute_metric_view_cache: OrderedDict[tuple[object, ...], MetricDisplayCache] = OrderedDict()
        self._live_absolute_metric_cache: dict[str, MetricDisplayCache] = {}
        self._live_absolute_archive_read_count = 0

    def clear(self) -> None:
        self._active_trace_series_token = None
        self._active_trace_series_result = {}
        self._metric_view_cache.clear()
        self._absolute_metric_view_cache.clear()
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

    def rebase_live_absolute_metric_recent_tail(self, offset_s: float) -> None:
        """Shift every metric's raw recent-tail x-values by a constant offset.

        Used when the live cache's time anchor changes (e.g. a measurement
        stopping, where cached tail points were relative to measurement
        start but the plot is about to switch to session-start-relative
        time) - a pure shift preserves point order and x/y pairing exactly,
        unlike clearing the tail outright (which would lose not-yet-flushed
        points until the next archive reload catches up). Only the raw tail
        needs this: the compressed display pyramid (x_display/levels) is
        always rebuilt from scratch on the next reload, never rebased.
        """
        if not offset_s:
            return
        for cache in self._live_absolute_metric_cache.values():
            if not isinstance(cache, MetricDisplayCache):
                continue
            tail_x = cache.recent_tail_x
            for index in range(len(tail_x)):
                tail_x[index] += offset_s

    def live_tail_snapshot(
        self,
        metric_names: set[str] | frozenset[str],
        *,
        after_t: float,
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Return raw tail points newer than `after_t` for the given metrics.

        Used by the reload handler to avoid discarding in-flight live data when
        seeding the cache from an archive file that may not yet include the most
        recent un-flushed points.
        """
        result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for metric_name in metric_names:
            cache = self._live_absolute_metric_cache.get(str(metric_name))
            if not isinstance(cache, MetricDisplayCache):
                continue
            rx = np.asarray(getattr(cache, "recent_tail_x", []), dtype=np.float64)
            ry = np.asarray(getattr(cache, "recent_tail_y", []), dtype=np.float64)
            if len(rx) == 0:
                continue
            mask = rx > after_t
            if np.any(mask):
                result[str(metric_name)] = (rx[mask].copy(), ry[mask].copy())
        return result

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
        # Defensive: callers reload-from-file data with a live tail
        # concatenated onto the end (main_window_sensorgram_archive.py) - even
        # if the file portion is sorted, the concatenation itself isn't
        # guaranteed monotonic. This is the single point every seed goes
        # through, so it's the most robust place to guarantee the compression
        # pyramid is always built from a monotonic x-array - an unsorted one
        # makes pyqtgraph draw the line backward over already-drawn history.
        # See docs/sensorgram_improvements.md.
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if len(x) > 1:
            sort_order = np.argsort(x, kind="stable")
            if not np.array_equal(sort_order, np.arange(len(x))):
                x = x[sort_order]
                y = y[sort_order]
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
        _trim_live_cache_l0_if_needed(cache, self._max_live_cache_l0_blocks)
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
            _trim_live_cache_l0_if_needed(cache, self._max_live_cache_l0_blocks)
            appended_blocks = int(np.ceil(float(len(pending_x)) / float(block_size)))
        # NOTE: this rebuild WAS removed in an earlier pass on the theory that
        # render_metric_series() (plot_controller.py, via live_absolute_metric_view)
        # always rebuilds it again immediately afterward in the same frame, making
        # this one redundant. That was wrong: refresh_metric_plot() first computes
        # active_series = window._active_trace_series(), which calls
        # live_absolute_metric_series() below - and that method SKIPS any metric
        # whose cache.x_display is empty (see live_absolute_metric_series just
        # below). With this rebuild removed, x_display never gets populated, so
        # active_series comes back empty every frame and refresh_metric_plot takes
        # its "no data" branch and returns *before* ever reaching
        # live_absolute_metric_view - the curve never renders live data at all
        # (only a manual archive reload, which seeds x_display directly, made it
        # reappear). Keep this rebuild here; it is not actually redundant.
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
