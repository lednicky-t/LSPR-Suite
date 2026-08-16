"""Background import pipeline for experiment plans.

Handles parsing CSV, native YAML, and HDF5 measurement files into
:class:`ExperimentPlanImportData` payloads, which the
``ExperimentControlWindow`` then uses to populate the plan table.

Extracted from singleLSPR Acquisition's `gui/experiment_control_import.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 0 extraction, 2026-08-09)
- effectively zero window coupling to begin with (1 `window.` token in the
original file, inside a docstring reference, not code). Only real change:
`lspr_app.domain.pump_plan` -> `lspr_acq_shell.pump_plan` (moved alongside
this file in the same extraction), and PyYAML added as a declared dependency
of this package (previously only implicitly available because sLSPR acq
happened to install it as a sibling package - see this package's pyproject.toml).

Supported file formats
----------------------
* **CSV / TSV** — delimited text; columns auto-detected by header names.
* **Native YAML** — the app's own plan export format (``steps:`` list).
* **HDF5** — LSPR measurement files; reads the embedded plan table and
  optionally the runtime log to restore the last active step.

Public API used by ``ExperimentControlWindow``
----------------------------------------------
``ExperimentPlanImportData``
    Payload dataclass returned by the import task.
``build_experiment_plan_steps_from_import_data(data, *, l_is_open)``
    Convert an ``ExperimentPlanImportData`` (CSV/HDF5 columns) into steps.
``build_experiment_plan_steps_from_native_document(document)``
    Convert a native YAML document dict into steps.
``build_experiment_plan_steps_from_hdf5_rows(columns, rows)``
    Convert raw HDF5 string-table rows into steps.
``ExperimentPlanImportSignals``
    Qt signals emitted on completion or failure.
``ExperimentPlanImportTask``
    ``QRunnable`` that performs the file read and parse on a thread-pool thread.

Utility helpers (also re-exported for window use)
--------------------------------------------------
``_safe_float(text, default)``
    Parse a string to float, returning *default* on error.
``_safe_int(value, default)``
    Parse a value to int, returning *default* on error.
``_parse_time_to_seconds`` / ``_format_duration_as_clock``
    Duration cell <-> seconds, for the external format's ``HH:MM:SS`` time column.
``_normalize_pump_direction`` / ``_pump_direction_to_external_token``
    Direction cell <-> ``"CW"``/``"CCW"``, for the external format's ``clckw``/``aclckw`` tokens.
``_experiment_plan_normalize_valve`` / ``_pack_valve_state_token``
    Valve cell <-> ``"Open"``/``"Close"``, including the external format's packed ``"V1oV2cV3cV4c"`` string.

These paired helpers are shared with ``experiment_control_window.py``'s CSV
export path (see ``_build_experiment_plan_export_rows_external``) so the two
external-format columns are always parsed and written the same way. See
``docs/experiment_plan_format.md`` (apps/sLSPR/acq) for the full column
reference of both supported CSV/TXT layouts.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import h5py
except ImportError:  # pragma: no cover - optional dependency guard
    h5py = None

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency guard
    yaml = None

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtGui import QColor

from lspr_acq_shell.pump_plan import (
    ACTIVE_PUMP_CHANNELS,
    DEFAULT_TUBE_MM,
    PumpChannelStep,
    PumpPlanStep,
    recompute_plan_timing,
)
from lspr_io import (
    LSPR_MEASUREMENT_PLAN_COLUMNS,
    LSPR_MEASUREMENT_RUNTIME_DATASET_NAME,
    validate_measurement_file,
)


# ── Shared numeric helpers ────────────────────────────────────────────────────

def _safe_float(text: str, default: float = 0.0) -> float:
    """Parse *text* to float, returning *default* on ValueError."""
    try:
        return float(str(text).strip())
    except ValueError:
        return default


def _safe_int(value: object, default: int = 0) -> int:
    """Parse *value* to int via float (handles "1.0"), returning *default* on error."""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default


def _parse_time_to_seconds(text: str, default: float = 0.0) -> float:
    """Parse a duration cell to seconds.

    Accepts a plain number of seconds (``"300"``) or a colon-separated
    clock string (``"H:MM:SS"`` / ``"MM:SS"``), as produced by some external
    pump-plan editors that record step time as a duration clock instead of
    raw seconds.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return default
    if ":" not in cleaned:
        return _safe_float(cleaned, default)
    parts = cleaned.split(":")
    try:
        numeric_parts = [float(part) for part in parts]
    except ValueError:
        return default
    seconds = 0.0
    for part in numeric_parts:
        seconds = seconds * 60.0 + part
    return max(seconds, 0.0)


def _normalize_pump_direction(text: str) -> str:
    """Normalise a pump direction token to ``"CW"`` or ``"CCW"``.

    Recognises this app's own ``"CW"``/``"CCW"`` labels as well as
    ``"clckw"``/``"aclckw"`` (clockwise/anti-clockwise), the shorthand used
    by the external FR/Direction pump-plan format (see
    :func:`_pump_direction_to_external_token` for the inverse, used on
    export). ``"aclcwk"`` is also accepted as a tolerated misspelling.
    """
    lowered = str(text or "").strip().casefold()
    if lowered in {
        "ccw",
        "aclckw",
        "aclcwk",
        "acw",
        "anticlockwise",
        "anti-clockwise",
        "counterclockwise",
        "counter-clockwise",
    }:
        return "CCW"
    return "CW"


def _pump_direction_to_external_token(direction: str) -> str:
    """Inverse of :func:`_normalize_pump_direction` for the external
    FR/Direction CSV format: ``"CCW"`` -> ``"aclckw"``, anything else ->
    ``"clckw"``.
    """
    return "aclckw" if str(direction or "").strip().casefold() == "ccw" else "clckw"


def _format_duration_as_clock(seconds: float) -> str:
    """Format a duration in seconds as ``"HH:MM:SS"`` — the inverse of the
    colon-separated clock strings :func:`_parse_time_to_seconds` accepts.
    """
    total_seconds = max(int(round(float(seconds))), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _pack_valve_state_token(valve: str) -> str:
    """Build a packed 4-valve state string (``"V1oV2cV3cV4c"``) — the
    inverse of the packed-string handling in
    :func:`_experiment_plan_normalize_valve`.

    This app only drives V1; V2-V4 are written as closed (``"c"``) since
    they're not wired to anything.
    """
    v1_state = "o" if str(valve or "").strip().casefold() == "open" else "c"
    return f"V1{v1_state}V2cV3cV4c"


# ── Import payload dataclass ──────────────────────────────────────────────────

@dataclass(slots=True)
class ExperimentPlanImportData:
    """Parsed result of a single file-import task.

    For CSV and HDF5 imports the raw *headers* / *rows* / *column_map* are
    populated; *steps* is derived from them.  For native YAML imports *headers*
    and *rows* are empty and *native_document* holds the parsed dict; *steps*
    is built directly from the document.
    """

    path: Path
    headers: list[str]
    rows: list[list[str]]
    column_map: dict[object, int]
    imported_colors: list[str]
    tube_mm_by_channel: list[float]
    uses_lr_valves: bool
    native_document: dict[str, object] | None = None
    steps: list[PumpPlanStep] | None = None
    switch_solution_rows: list[list[str]] | None = None
    switch_solution_detail_rows: list[list[str]] | None = None
    switch_solution_mode: bool | None = None
    selected_row: int | None = None
    valve_state_labels: dict[str, str] | None = None
    valve_state_colors: dict[str, str] | None = None
    color_palette_entries: list[dict[str, str]] | None = None


# ── CSV / tabular row helpers ─────────────────────────────────────────────────

def _experiment_plan_cell(row: list[str], index: object | None, default: str = "") -> str:
    """Return the cell at *index* from *row*, or *default* if out of range."""
    if not isinstance(index, int) or index < 0 or index >= len(row):
        return default
    return str(row[index] or default)


def _experiment_plan_switch_position_from_text(text: str) -> int:
    """Parse a switch-position string like ``"3: Buffer"`` to an integer (1–12)."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return 1
    head = cleaned.split(":", 1)[0].strip()
    try:
        return max(min(int(float(head)), 12), 1)
    except ValueError:
        return 1


_PACKED_VALVES_RE = re.compile(r"^(v\d+[a-z])+$")
_PACKED_VALVE_TOKEN_RE = re.compile(r"v(\d+)([a-z])")


def _experiment_plan_normalize_valve(text: str, l_is_open: bool) -> str:
    """Normalise a valve text token (``"Open"``, ``"Close"``, ``"L"``, …).

    When the import file uses L/R notation, *l_is_open* controls which
    physical direction "L" maps to.

    Also accepts a packed multi-valve string such as ``"V1oV2cV3cV4c"``
    (some external pump-plan editors support up to four valves; this app
    only drives V1, so only V1's o/c state is read).
    """
    lowered = str(text or "").strip().casefold().replace(" ", "")
    if _PACKED_VALVES_RE.match(lowered):
        for valve_number, state in _PACKED_VALVE_TOKEN_RE.findall(lowered):
            if valve_number == "1":
                return "Open" if state == "o" else "Close"
    if lowered in {"close", "closed", "c", "off", "false", "0"}:
        return "Close"
    if lowered in {"open", "opened", "o", "on", "true", "1"}:
        return "Open"
    if lowered in {"r"}:
        return "Close" if l_is_open else "Open"
    if lowered in {"right"}:
        return "Close" if l_is_open else "Open"
    if lowered in {"l"}:
        return "Open" if l_is_open else "Close"
    if lowered in {"left"}:
        return "Open" if l_is_open else "Close"
    return "Open" if l_is_open else "Close"


# ── Step builders ─────────────────────────────────────────────────────────────

def build_experiment_plan_steps_from_import_data(data: ExperimentPlanImportData, *, l_is_open: bool) -> list[PumpPlanStep]:
    """Build a step list from a CSV / HDF5 import payload.

    Args:
        data: Parsed import data with ``rows`` and ``column_map`` populated.
        l_is_open: Whether "L" in the valve column means "Open".

    Returns:
        Timing-recomputed list of :class:`~lspr_acq_shell.pump_plan.PumpPlanStep`.
    """
    steps: list[PumpPlanStep] = []
    for row_index, row in enumerate(data.rows, start=1):
        if not any(cell.strip() for cell in row):
            continue
        channels: list[PumpChannelStep] = []
        for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
            flow_text = _experiment_plan_cell(row, data.column_map.get(("flow", channel_index)), "0")
            direction_text = _experiment_plan_cell(row, data.column_map.get(("direction", channel_index)), "CW")
            flow_ml_min = max(_safe_float(flow_text), 0.0)
            direction = _normalize_pump_direction(direction_text)
            channels.append(
                PumpChannelStep(
                    flow_ul_min=max(round(flow_ml_min * 1000.0), 0),
                    direction=direction,
                )
            )
        valve = _experiment_plan_normalize_valve(
            _experiment_plan_cell(row, data.column_map.get("valve"), "Open"),
            l_is_open,
        )
        raw_color = _experiment_plan_cell(row, data.column_map.get("color"), "").strip().upper()
        color = QColor(raw_color).name().upper() if QColor(raw_color).isValid() else "#4E79A7"
        description = _experiment_plan_cell(row, data.column_map.get("description"), "").strip()
        switch_text = _experiment_plan_cell(row, data.column_map.get("solution"), "")
        switch_position = _experiment_plan_switch_position_from_text(switch_text) if switch_text else 1
        duration_s = max(_parse_time_to_seconds(_experiment_plan_cell(row, data.column_map.get("time"), "0")), 0.0)
        steps.append(
            PumpPlanStep(
                step=row_index,
                duration_s=duration_s,
                color=color,
                valve=valve,
                switch_position=switch_position,
                description=description,
                channels=channels,
            )
        )
    return recompute_plan_timing(steps)


def build_experiment_plan_steps_from_native_document(document: dict[str, object]) -> list[PumpPlanStep]:
    """Build a step list from a native YAML document dict.

    Supports an optional ``units.flow`` key (``"uL/min"`` or ``"mL/min"``)
    for unit conversion.
    """
    raw_steps = document.get("steps", [])
    if not isinstance(raw_steps, list):
        return []
    units = document.get("units", {})
    flow_factor = 1.0
    if isinstance(units, dict):
        flow_unit = str(units.get("flow", "uL/min") or "uL/min").strip().casefold()
        if flow_unit in {"ml/min", "ml min-1", "ml_per_min"}:
            flow_factor = 1000.0
    steps: list[PumpPlanStep] = []
    for row_index, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            continue
        devices = raw_step.get("devices", {})
        devices = devices if isinstance(devices, dict) else {}
        pump = devices.get("pump_1", {})
        pump = pump if isinstance(pump, dict) else {}
        channels: list[PumpChannelStep] = []
        for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
            raw_channel = pump.get(f"ch{channel_index}", {})
            raw_channel = raw_channel if isinstance(raw_channel, dict) else {}
            flow = max(_safe_float(str(raw_channel.get("flow", 0.0) or 0.0)), 0.0) * flow_factor
            direction = str(raw_channel.get("direction", "OFF") or "OFF").upper()
            if direction not in {"CW", "CCW", "OFF"}:
                direction = "CW"
            channels.append(PumpChannelStep(flow_ul_min=max(round(flow), 0), direction=direction))
        valve_payload = devices.get("valve_1", {})
        valve_payload = valve_payload if isinstance(valve_payload, dict) else {}
        raw_valve = str(valve_payload.get("state", "open") or "open").strip().casefold()
        valve = "Close" if raw_valve in {"close", "closed"} else "Open"
        switch_payload = devices.get("switch_1", {})
        switch_payload = switch_payload if isinstance(switch_payload, dict) else {}
        switch_position = _experiment_plan_switch_position_from_text(str(switch_payload.get("port", 1) or 1))
        qcolor = QColor(str(raw_step.get("color", "") or "").strip())
        color = qcolor.name().upper() if qcolor.isValid() else "#4E79A7"
        steps.append(
            PumpPlanStep(
                step=row_index,
                duration_s=max(_safe_float(str(raw_step.get("duration_s", 0.0) or 0.0)), 0.0),
                color=color,
                valve=valve,
                switch_position=switch_position,
                description=str(raw_step.get("comment", raw_step.get("description", "")) or ""),
                channels=channels,
            )
        )
    return recompute_plan_timing(steps)


def build_experiment_plan_steps_from_hdf5_rows(columns: list[str], rows: list[list[str]]) -> list[PumpPlanStep]:
    """Build a step list from raw HDF5 string-table columns and rows."""
    column_map = {str(column): index for index, column in enumerate(columns)}
    steps: list[PumpPlanStep] = []
    for row_index, row in enumerate(rows, start=1):
        if not any(str(cell).strip() for cell in row):
            continue
        channels: list[PumpChannelStep] = []
        for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
            flow_index = column_map.get(f"ch{channel_index}_flow_ul_min")
            direction_index = column_map.get(f"ch{channel_index}_direction")
            flow_text = _experiment_plan_cell(row, flow_index, "0")
            direction_text = _experiment_plan_cell(row, direction_index, "CW")
            flow_ul_min = max(round(_safe_float(flow_text)), 0)
            direction = "CCW" if str(direction_text).casefold() == "ccw" else "CW"
            channels.append(PumpChannelStep(flow_ul_min=flow_ul_min, direction=direction))
        duration_text = _experiment_plan_cell(row, column_map.get("duration_s"), "0")
        valve = _experiment_plan_cell(row, column_map.get("valve"), "Open")
        color = _experiment_plan_cell(row, column_map.get("color"), "").strip().upper() or "#4E79A7"
        if color and not QColor(color).isValid():
            color = "#4E79A7"
        description = _experiment_plan_cell(row, column_map.get("description"), "").strip()
        switch_text = _experiment_plan_cell(row, column_map.get("switch_position"), "")
        if not switch_text:
            switch_text = _experiment_plan_cell(row, column_map.get("solution"), "")
        switch_position = _experiment_plan_switch_position_from_text(switch_text) if switch_text else 1
        steps.append(
            PumpPlanStep(
                step=row_index,
                duration_s=max(_safe_float(duration_text), 0.0),
                color=color,
                valve=str(valve or "Open"),
                switch_position=switch_position,
                description=description,
                channels=channels,
            )
        )
    return recompute_plan_timing(steps)


# ── HDF5 low-level helpers ────────────────────────────────────────────────────

def _decode_hdf5_text(value: object) -> str:
    """Decode an HDF5 cell value (bytes or str) to a plain Python string."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _read_hdf5_string_table(dataset: object) -> tuple[list[str], list[list[str]]]:
    """Read a 2-D HDF5 string dataset into ``(columns, rows)``.

    Returns ``([], [])`` if *dataset* is ``None`` or not 2-D.  Column names
    are taken from ``dataset.attrs["columns"]`` when present.
    """
    if dataset is None:
        return [], []
    columns: list[str] = []
    if hasattr(dataset, "attrs") and "columns" in dataset.attrs:
        columns = [_decode_hdf5_text(value) for value in dataset.attrs["columns"]]
    raw = getattr(dataset, "shape", ())
    if not raw or len(raw) != 2 or int(raw[0]) <= 0:
        return columns, []
    table: list[list[str]] = []
    for row in dataset[...]:
        table.append([_decode_hdf5_text(cell) for cell in row])
    return columns, table


def _hdf5_table_column_map(columns: list[str]) -> dict[object, int]:
    """Build the ``column_map`` dict expected by :class:`ExperimentPlanImportData`
    from raw HDF5 column names.

    HDF5 stores ``duration_s`` / ``switch_position`` whereas the CSV column_map
    uses ``"time"`` / ``"solution"`` — this function bridges the difference.
    """
    column_map: dict[object, int] = {}
    for index, column in enumerate(columns):
        normalized = str(column or "").strip().casefold()
        if not normalized:
            continue
        if normalized == "duration_s":
            column_map["time"] = index
            continue
        if normalized == "description":
            column_map["description"] = index
            continue
        if normalized == "valve":
            column_map["valve"] = index
            continue
        if normalized == "color":
            column_map["color"] = index
            continue
        if normalized == "switch_position":
            column_map["solution"] = index
            continue
        match = re.match(r"ch(\d+)_(flow_ul_min|direction)", normalized)
        if match:
            channel = int(match.group(1))
            field = match.group(2)
            if field == "flow_ul_min":
                column_map[("flow", channel)] = index
            else:
                column_map[("direction", channel)] = index
    return column_map


# ── Qt signals and background task ───────────────────────────────────────────

class ExperimentPlanImportSignals(QObject):
    """Qt signals for :class:`ExperimentPlanImportTask`."""

    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class ExperimentPlanImportTask(QRunnable):
    """Thread-pool task that reads and parses an experiment plan file.

    Supports ``.csv`` / ``.tsv``, ``.yaml`` / ``.yml``, and ``.h5`` /
    ``.hdf5`` files.  The *generation* counter lets the caller discard results
    from tasks that are no longer current.

    On success emits :attr:`signals.finished` with the parsed
    :class:`ExperimentPlanImportData`.  On any error emits
    :attr:`signals.failed` with a human-readable message.
    """

    def __init__(
        self,
        generation: int,
        path: Path,
        *,
        import_hdf5_plan: bool = True,
        import_hdf5_runtime: bool = True,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._path = path
        self._import_hdf5_plan = bool(import_hdf5_plan)
        self._import_hdf5_runtime = bool(import_hdf5_runtime)
        self.signals = ExperimentPlanImportSignals()

    def run(self) -> None:  # pragma: no cover - thread-pool path
        try:
            suffix = self._path.suffix.casefold()
            if suffix in {".h5", ".hdf5"}:
                if h5py is None:
                    raise RuntimeError("h5py is required to import HDF5 experiment plans.")
                payload = self._load_hdf5_payload()
                self.signals.finished.emit(self._generation, payload)
                return

            text = self._path.read_text(encoding="utf-8-sig")
            if suffix in {".yaml", ".yml"}:
                if yaml is None:
                    raise RuntimeError("PyYAML is required to import native YAML experiment plans.")
                document = yaml.safe_load(text) or {}
                if not isinstance(document, dict):
                    raise ValueError("Native experiment plan must contain a mapping at the top level.")
                imported_colors: list[str] = []
                steps = document.get("steps", [])
                if isinstance(steps, list):
                    for raw_step in steps:
                        if not isinstance(raw_step, dict):
                            continue
                        qcolor = QColor(str(raw_step.get("color", "") or "").strip())
                        if qcolor.isValid():
                            color = qcolor.name().upper()
                            if color not in imported_colors:
                                imported_colors.append(color)
                tube_mm_by_channel = [DEFAULT_TUBE_MM] * ACTIVE_PUMP_CHANNELS
                devices = document.get("devices", {})
                if isinstance(devices, dict):
                    pumps = devices.get("pumps", {})
                    if isinstance(pumps, dict):
                        pump_1 = pumps.get("pump_1", {})
                        if isinstance(pump_1, dict):
                            channels = pump_1.get("channels", {})
                            if isinstance(channels, dict):
                                for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
                                    raw_channel = channels.get(f"ch{channel_index}", {})
                                    if isinstance(raw_channel, dict):
                                        try:
                                            tube_mm_by_channel[channel_index - 1] = max(
                                                float(raw_channel.get("tube_mm", DEFAULT_TUBE_MM) or DEFAULT_TUBE_MM),
                                                0.0,
                                            )
                                        except (TypeError, ValueError):
                                            tube_mm_by_channel[channel_index - 1] = DEFAULT_TUBE_MM
                payload = ExperimentPlanImportData(
                    path=self._path,
                    headers=[],
                    rows=[],
                    column_map={},
                    imported_colors=imported_colors,
                    tube_mm_by_channel=tube_mm_by_channel,
                    uses_lr_valves=False,
                    native_document=document,
                )
                payload.steps = build_experiment_plan_steps_from_native_document(document)
                self.signals.finished.emit(self._generation, payload)
                return

            lines = [line for line in text.splitlines() if line.strip()]
            if not lines:
                payload = ExperimentPlanImportData(
                    path=self._path,
                    headers=[],
                    rows=[],
                    column_map={},
                    imported_colors=[],
                    tube_mm_by_channel=[DEFAULT_TUBE_MM] * ACTIVE_PUMP_CHANNELS,
                    uses_lr_valves=False,
                )
                self.signals.finished.emit(self._generation, payload)
                return
            delimiter = ","
            candidates = [";", ",", "\t", "|"]
            counts = {candidate: lines[0].count(candidate) for candidate in candidates}
            delimiter = max(counts, key=counts.get)
            if counts[delimiter] <= 0:
                delimiter = ";"
            reader = csv.reader(lines, delimiter=delimiter)
            rows = [row for row in reader if any(cell.strip() for cell in row)]
            if not rows:
                payload = ExperimentPlanImportData(
                    path=self._path,
                    headers=[],
                    rows=[],
                    column_map={},
                    imported_colors=[],
                    tube_mm_by_channel=[DEFAULT_TUBE_MM] * ACTIVE_PUMP_CHANNELS,
                    uses_lr_valves=False,
                )
                self.signals.finished.emit(self._generation, payload)
                return
            headers = rows[0]
            data_rows = rows[1:]
            column_map = {}
            for index, header in enumerate(headers):
                normalized = re.sub(r"[^a-z0-9]+", "", str(header or "").casefold())
                if not normalized:
                    continue
                if normalized.startswith("step"):
                    column_map["step"] = index
                    continue
                if normalized.startswith("time"):
                    column_map["time"] = index
                    continue
                if normalized.startswith("valve"):
                    column_map["valve"] = index
                    continue
                if normalized.startswith("color"):
                    column_map["color"] = index
                    continue
                if "solution" in normalized:
                    column_map["solution"] = index
                    continue
                if "comment" in normalized or "description" in normalized or "descrit" in normalized or "note" in normalized:
                    column_map["description"] = index
                    continue
                match = re.match(r"ch(\d+)", normalized)
                if match:
                    channel = int(match.group(1))
                    if "flow" in normalized:
                        column_map[("flow", channel)] = index
                    elif "direction" in normalized or normalized.endswith("dir"):
                        column_map[("direction", channel)] = index
                    elif "tube" in normalized:
                        column_map[("tube", channel)] = index
                    continue
                # Some external pump-plan editors label channels "FR<n>" /
                # "Direction<n>" instead of "Ch-<n> Flow" / "Ch-<n> Direction".
                flow_match = re.match(r"fr(\d+)", normalized)
                if flow_match:
                    column_map[("flow", int(flow_match.group(1)))] = index
                    continue
                direction_match = re.match(r"direction(\d+)", normalized)
                if direction_match:
                    column_map[("direction", int(direction_match.group(1)))] = index
                    continue
            tube_mm_by_channel = [DEFAULT_TUBE_MM] * ACTIVE_PUMP_CHANNELS
            imported_colors: list[str] = []
            for row_index, row in enumerate(data_rows, start=1):
                for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
                    tube_index = column_map.get(("tube", channel_index))
                    if row_index == 1 and tube_index is not None and tube_index < len(row):
                        try:
                            tube_mm_by_channel[channel_index - 1] = max(float(str(row[tube_index]).strip()), 0.0)
                        except ValueError:
                            tube_mm_by_channel[channel_index - 1] = DEFAULT_TUBE_MM
                color_index = column_map.get("color")
                if color_index is not None and color_index < len(row):
                    qcolor = QColor(str(row[color_index]).strip())
                    if qcolor.isValid():
                        color = qcolor.name().upper()
                        if color not in imported_colors:
                            imported_colors.append(color)
            uses_lr_valves = False
            valve_index = column_map.get("valve")
            if valve_index is not None:
                for row in data_rows:
                    if valve_index >= len(row):
                        continue
                    lowered = str(row[valve_index]).strip().casefold()
                    if lowered in {"l", "r", "left", "right"}:
                        uses_lr_valves = True
                        break
            payload = ExperimentPlanImportData(
                path=self._path,
                headers=headers,
                rows=data_rows,
                column_map=column_map,
                imported_colors=imported_colors,
                tube_mm_by_channel=tube_mm_by_channel,
                uses_lr_valves=uses_lr_valves,
            )
            payload.steps = build_experiment_plan_steps_from_import_data(payload, l_is_open=True)
            self.signals.finished.emit(self._generation, payload)
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))

    def _load_hdf5_payload(self) -> ExperimentPlanImportData:
        """Read and parse an HDF5 measurement file into an import payload.

        Also reads the runtime log (if present and *import_hdf5_runtime* is
        True) to restore the last active step index as ``payload.selected_row``.
        """
        if h5py is None:
            raise RuntimeError("h5py is required to import HDF5 experiment plans.")
        with h5py.File(self._path, "r") as handle:
            validation = validate_measurement_file(handle)
            if not validation.is_valid:
                raise ValueError("; ".join(validation.errors))

            switch_solution_rows: list[list[str]] | None = None
            switch_solution_detail_rows: list[list[str]] | None = None
            switch_solution_mode: bool | None = None
            valve_state_labels: dict[str, str] | None = None
            valve_state_colors: dict[str, str] | None = None
            color_palette_entries: list[dict[str, str]] | None = None
            metadata = handle["metadata"] if "metadata" in handle else None
            assignment_tables = None
            if metadata is not None and "assignment_tables" in metadata:
                assignment_tables = metadata["assignment_tables"]
            if assignment_tables is not None:
                if "switch_solution_map" in assignment_tables:
                    _, switch_solution_rows = _read_hdf5_string_table(assignment_tables["switch_solution_map"])
                if "switch_solution_details" in assignment_tables:
                    _, switch_solution_detail_rows = _read_hdf5_string_table(assignment_tables["switch_solution_details"])
                if "switch_solution_mode" in metadata.attrs:
                    switch_solution_mode = bool(metadata.attrs["switch_solution_mode"])
                if "valve_state_map" in assignment_tables:
                    _, rows = _read_hdf5_string_table(assignment_tables["valve_state_map"])
                    labels: dict[str, str] = {}
                    colors: dict[str, str] = {}
                    for row in rows:
                        if len(row) < 3:
                            continue
                        state_name = str(row[0]).strip()
                        label = str(row[1]).strip()
                        color = str(row[2]).strip().upper()
                        if state_name:
                            labels[state_name] = label or state_name
                            if color:
                                colors[state_name] = color
                    if labels:
                        valve_state_labels = labels
                    if colors:
                        valve_state_colors = colors
                if "color_palette_entries" in assignment_tables:
                    _, rows = _read_hdf5_string_table(assignment_tables["color_palette_entries"])
                    entries: list[dict[str, str]] = []
                    for row in rows:
                        if len(row) < 2:
                            continue
                        name = str(row[0]).strip()
                        color = str(row[1]).strip().upper()
                        if color:
                            entries.append({"name": name, "color": color})
                    if entries:
                        color_palette_entries = entries
            if metadata is not None and "switch_solution_map" in metadata and switch_solution_rows is None:
                _, switch_solution_rows = _read_hdf5_string_table(metadata["switch_solution_map"])
            if metadata is not None and "switch_solution_mode" in metadata.attrs and switch_solution_mode is None:
                switch_solution_mode = bool(metadata.attrs["switch_solution_mode"])
            if metadata is not None and "valve_state_labels" in metadata and valve_state_labels is None:
                _, rows = _read_hdf5_string_table(metadata["valve_state_labels"])
                labels: dict[str, str] = {}
                for row in rows:
                    if len(row) < 2:
                        continue
                    state_name = str(row[0]).strip()
                    label = str(row[1]).strip()
                    if state_name:
                        labels[state_name] = label or state_name
                if labels:
                    valve_state_labels = labels
            if metadata is not None and "valve_state_colors" in metadata and valve_state_colors is None:
                _, rows = _read_hdf5_string_table(metadata["valve_state_colors"])
                colors: dict[str, str] = {}
                for row in rows:
                    if len(row) < 2:
                        continue
                    state_name = str(row[0]).strip()
                    color = str(row[1]).strip().upper()
                    if state_name and color:
                        colors[state_name] = color
                if colors:
                    valve_state_colors = colors

            selected_row: int | None = None
            if self._import_hdf5_runtime:
                runtime_dataset = None
                if "data" in handle and LSPR_MEASUREMENT_RUNTIME_DATASET_NAME in handle["data"]:
                    runtime_dataset = handle["data"][LSPR_MEASUREMENT_RUNTIME_DATASET_NAME]
                elif "runs" in handle:
                    runs_group = handle["runs"]
                    if "flow_events" in runs_group:
                        runtime_dataset = runs_group["flow_events"]
                    elif "flow_state" in runs_group:
                        runtime_dataset = runs_group["flow_state"]
                elif "flow" in handle and "state" in handle["flow"]:
                    runtime_dataset = handle["flow"]["state"]
                if runtime_dataset is not None:
                    runtime_columns, runtime_rows = _read_hdf5_string_table(runtime_dataset)
                    if runtime_columns:
                        try:
                            step_index = runtime_columns.index("step_index")
                        except ValueError:
                            step_index = -1
                        if step_index >= 0:
                            for runtime_row in reversed(runtime_rows):
                                if step_index >= len(runtime_row):
                                    continue
                                try:
                                    value = int(float(runtime_row[step_index]))
                                except (TypeError, ValueError):
                                    continue
                                if value > 0:
                                    selected_row = value - 1
                                    break

            if self._import_hdf5_plan and "metadata" in handle and "experiment_plan" in handle["metadata"]:
                plan_dataset = handle["metadata"]["experiment_plan"]
            elif self._import_hdf5_plan and "plans" in handle and "experiment_plan" in handle["plans"]:
                plan_dataset = handle["plans"]["experiment_plan"]
            else:
                raise ValueError("The selected HDF5 file does not contain an experiment plan table in metadata.")

            plan_columns, plan_rows = _read_hdf5_string_table(plan_dataset)
            if not plan_columns:
                plan_columns = list(LSPR_MEASUREMENT_PLAN_COLUMNS)
            payload = ExperimentPlanImportData(
                path=self._path,
                headers=list(plan_columns),
                rows=[list(row) for row in plan_rows],
                column_map=_hdf5_table_column_map(plan_columns),
                imported_colors=[],
                tube_mm_by_channel=[DEFAULT_TUBE_MM] * ACTIVE_PUMP_CHANNELS,
                uses_lr_valves=False,
                native_document=None,
                steps=build_experiment_plan_steps_from_hdf5_rows(plan_columns, plan_rows),
                switch_solution_rows=switch_solution_rows,
                switch_solution_detail_rows=switch_solution_detail_rows,
                switch_solution_mode=switch_solution_mode,
                selected_row=selected_row,
                valve_state_labels=valve_state_labels,
                valve_state_colors=valve_state_colors,
                color_palette_entries=color_palette_entries,
            )
            payload.imported_colors = []
            for step in payload.steps or []:
                if step.color not in payload.imported_colors:
                    payload.imported_colors.append(step.color)
            return payload
