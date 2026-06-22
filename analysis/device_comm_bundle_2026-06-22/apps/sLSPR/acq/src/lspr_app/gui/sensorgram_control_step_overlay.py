from __future__ import annotations

import re
from collections.abc import Iterable

import numpy as np


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def normalize_sensorgram_control_step_overlay_label(
    raw_label: object,
    *,
    step_index: int | None = None,
) -> str:
    label = str(raw_label or "").strip()
    if label:
        return label
    if step_index is not None and step_index > 0:
        return f"Step {int(step_index)}"
    return ""


def normalize_sensorgram_control_step_overlay_color(
    raw_color: object,
    *,
    fallback: str = "#5FA8FF",
) -> str:
    color_text = str(raw_color or "").strip()
    if _HEX_COLOR_RE.match(color_text):
        return color_text.upper()
    fallback_text = str(fallback or "").strip()
    if _HEX_COLOR_RE.match(fallback_text):
        return fallback_text.upper()
    return "#5FA8FF"


def resolve_sensorgram_control_step_overlay_palette_label(
    raw_color: object,
    palette_entries: Iterable[object] | None,
    *,
    step_index: int | None = None,
) -> str:
    color = normalize_sensorgram_control_step_overlay_color(raw_color, fallback="")
    if color and palette_entries is not None:
        for entry in palette_entries:
            if isinstance(entry, dict):
                name = str(entry.get("name", "") or "").strip()
                entry_color = str(entry.get("color", "") or "").strip()
            elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
                name = str(entry[0] or "").strip()
                entry_color = str(entry[1] or "").strip()
            else:
                continue
            if name and normalize_sensorgram_control_step_overlay_color(entry_color, fallback="") == color:
                return name
    return normalize_sensorgram_control_step_overlay_label("", step_index=step_index)


def build_sensorgram_control_step_overlay_segments(
    events: list[dict[str, object]],
    *,
    current_elapsed_s: float | None = None,
) -> list[dict[str, object]]:
    normalized_events: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        elapsed_s: float | None = None
        for key in ("elapsed_s", "t_s", "elapsed", "t"):
            value = event.get(key)
            if value is None:
                continue
            try:
                elapsed_s = float(value)
            except (TypeError, ValueError):
                continue
            break
        if elapsed_s is None and event.get("t_ms") is not None:
            try:
                elapsed_s = float(event.get("t_ms")) / 1000.0
            except (TypeError, ValueError):
                elapsed_s = None
        if elapsed_s is None or not np.isfinite(elapsed_s) or elapsed_s < 0.0:
            continue
        step_index: int | None = None
        step_value = event.get("step_index")
        if step_value not in {None, ""}:
            try:
                step_index = max(int(float(step_value)), 0)
            except (TypeError, ValueError):
                step_index = None
        state = str(event.get("plan_state") or event.get("state") or "").strip().upper()
        if state not in {"RUN", "HOLD", "PAUSE", "STOP"}:
            state = "STOP"
        label = normalize_sensorgram_control_step_overlay_label(
            event.get("label", event.get("description", event.get("name", ""))),
            step_index=step_index,
        )
        color = normalize_sensorgram_control_step_overlay_color(event.get("color", ""))
        normalized_events.append(
            {
                "elapsed_s": float(elapsed_s),
                "step_index": step_index,
                "state": state,
                "event": str(event.get("event") or ""),
                "status": str(event.get("status") or ""),
                "label": label,
                "color": color,
            }
        )

    if not normalized_events:
        return []

    normalized_events.sort(key=lambda item: (float(item["elapsed_s"]), int(item["step_index"] or 0), str(item["event"])))

    segments: list[dict[str, object]] = []
    for index in range(len(normalized_events) - 1):
        start_event = normalized_events[index]
        end_event = normalized_events[index + 1]
        start_s = float(start_event["elapsed_s"])
        end_s = float(end_event["elapsed_s"])
        if end_s <= start_s:
            continue
        if str(start_event["state"]) == "STOP":
            continue
        segments.append(
            {
                "start_s": start_s,
                "end_s": end_s,
                "step_index": start_event["step_index"],
                "state": start_event["state"],
                "event": start_event["event"],
                "status": start_event["status"],
                "color": start_event["color"],
                "label": start_event["label"],
                "active": False,
            }
        )

    current_event = normalized_events[-1]
    if current_elapsed_s is None:
        current_elapsed_s = float(current_event["elapsed_s"])
    try:
        current_elapsed_s = float(current_elapsed_s)
    except (TypeError, ValueError):
        current_elapsed_s = float(current_event["elapsed_s"])
    if np.isfinite(current_elapsed_s) and current_elapsed_s > float(current_event["elapsed_s"]) and str(current_event["state"]) != "STOP":
        segments.append(
            {
                "start_s": float(current_event["elapsed_s"]),
                "end_s": float(current_elapsed_s),
                "step_index": current_event["step_index"],
                "state": current_event["state"],
                "event": current_event["event"],
                "status": current_event["status"],
                "color": current_event["color"],
                "label": current_event["label"],
                "active": True,
            }
        )
    return segments
