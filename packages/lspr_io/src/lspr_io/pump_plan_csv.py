"""Reader for the pump-plan CSV the pump-control software exports ("external FR/Direction" layout):

    FR1 [ml/min];Direction1;FR2 [ml/min];Direction2; ... ;Time;Valves;Notes
    4.00E-02;aclckw;2.00E-02;aclckw;;;;;00:05:00;V1cV2cV3cV4c;H2O_--_ch1F_ch2P

One row is one step. Flow is per channel (the unit is read from the header), ``Time`` is the step's duration
(``HH:MM:SS``, ``MM:SS`` or plain seconds), ``Valves`` packs every valve (``V1cV2oV3cV4c``: c closed, o open)
and ``Notes`` is the free-text label that the measurement also records while the step runs.

Result: a `lspr_core.ExperimentPlan` in *plan time* (steps one after the other from t = 0; flow in uL/min) plus
a list of warnings. The reader never guesses silently:

- a number or duration that cannot be read stops the import with the line and column;
- a row with extra empty cells before ``Time`` (a stray ``;``) is realigned and reported; extra cells that are
  not empty stop the import;
- a channel whose direction is set but whose flow is 0 (or the reverse) is reported, because it is the
  signature of a mistyped value (``.00E-02`` reads as 0).

Placement in measurement time is not done here; that needs the measured notes (`lspr_core.build_imaging_plan`).
This module is the pure-Python counterpart of singleLSPR Acquisition's plan import
(``lspr_acq_shell.experiment_control_import``), which carries Qt and the device layer and silently misreads both
defects above.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from lspr_core import ExperimentPlan, ExperimentPlanStep

_DELIMITERS = (";", ",", "\t", "|")
_PACKED_VALVE_RE = re.compile(r"v(\d+)([a-z])", re.IGNORECASE)
_PACKED_VALVES_FULL_RE = re.compile(r"^(?:v\d+[a-z])+$", re.IGNORECASE)
_UNIT_RE = re.compile(r"\[([^\]]*)\]")
_CCW_TOKENS = {"ccw", "aclckw", "aclcwk", "acw", "anticlockwise", "anti-clockwise", "counterclockwise", "counter-clockwise"}
_CW_TOKENS = {"cw", "clckw", "clockwise"}
_FLOW_FACTOR_TO_UL_MIN = {"ml/min": 1000.0, "ul/min": 1.0, "µl/min": 1.0, "μl/min": 1.0}
_PLAN_UNITS = {"flow": "uL/min", "time": "s"}


@dataclass(slots=True)
class PumpPlanReadResult:
    plan: ExperimentPlan
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Layout:
    delimiter: str
    headers: list[str]
    flow_columns: dict[int, int]  # channel -> column index
    direction_columns: dict[int, int]
    time_column: int | None
    valves_column: int | None
    notes_column: int | None
    flow_factor: float
    flow_unit_known: bool

    @property
    def last_channel_column(self) -> int:
        return max([*self.flow_columns.values(), *self.direction_columns.values()], default=-1)

    @property
    def tail_columns(self) -> int:
        """How many header columns follow the last channel column (Time, Valves, Notes: 3)."""
        return len(self.headers) - 1 - self.last_channel_column


def _normalized(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", header.casefold())


def _read_text(path: Path) -> tuple[str, str | None]:
    """File text, plus a warning when it was not UTF-8 (a Windows-written file decodes as Windows-1252)."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "The file is not UTF-8; it was read as Windows-1252."


def _layout_from_header_line(line: str) -> _Layout | None:
    counts = {delimiter: line.count(delimiter) for delimiter in _DELIMITERS}
    delimiter = max(counts, key=counts.get)
    if counts[delimiter] == 0:
        return None
    headers = next(csv.reader([line], delimiter=delimiter))
    flow_columns: dict[int, int] = {}
    direction_columns: dict[int, int] = {}
    time_column = valves_column = notes_column = None
    flow_factor = 1000.0  # mL/min is what this software writes; an unrecognised unit is reported by the reader
    flow_unit_known = False
    for index, header in enumerate(headers):
        key = _normalized(header)
        if not key:
            continue
        flow = re.match(r"fr(\d+)", key)
        direction = re.match(r"direction(\d+)", key)
        if flow:
            flow_columns[int(flow.group(1))] = index
            unit = _UNIT_RE.search(header)
            if unit is not None and unit.group(1).strip().casefold() in _FLOW_FACTOR_TO_UL_MIN:
                flow_factor = _FLOW_FACTOR_TO_UL_MIN[unit.group(1).strip().casefold()]
                flow_unit_known = True
        elif direction:
            direction_columns[int(direction.group(1))] = index
        elif key.startswith("time"):
            time_column = index
        elif key.startswith("valve"):
            valves_column = index
        elif "note" in key or "comment" in key or "description" in key:
            notes_column = index
    if not flow_columns or not direction_columns or time_column is None:
        return None
    return _Layout(
        delimiter=delimiter,
        headers=headers,
        flow_columns=flow_columns,
        direction_columns=direction_columns,
        time_column=time_column,
        valves_column=valves_column,
        notes_column=notes_column,
        flow_factor=flow_factor,
        flow_unit_known=flow_unit_known,
    )


def is_pump_plan_csv(path: Path) -> bool:
    """Whether *path* looks like this pump-plan CSV, judged from its header row alone (flow and direction columns
    per channel plus a Time column). Never raises."""
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    try:
        first_line = head.decode("utf-8-sig").splitlines()[0]
    except UnicodeDecodeError:
        first_line = head.decode("cp1252", errors="replace").splitlines()[0] if head else ""
    except IndexError:
        return False
    return bool(first_line) and _layout_from_header_line(first_line) is not None


def _parse_clock_seconds(text: str) -> float:
    parts = text.strip().split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + float(part)
    return seconds


def _direction(token: str) -> tuple[str, bool]:
    """(``"CW"`` / ``"CCW"`` / ``"OFF"``, recognised). An empty cell is OFF."""
    key = token.strip().casefold()
    if not key:
        return "OFF", True
    if key in _CCW_TOKENS:
        return "CCW", True
    if key in _CW_TOKENS:
        return "CW", True
    return token.strip().upper(), False


def _valves(cell: str, location: str, warnings: list[str]) -> dict[str, str]:
    text = cell.strip()
    if not text:
        return {}
    if _PACKED_VALVES_FULL_RE.match(text):
        states: dict[str, str] = {}
        for number, state in _PACKED_VALVE_RE.findall(text):
            letter = state.casefold()
            if letter == "o":
                states[f"V{number}"] = "Open"
            elif letter == "c":
                states[f"V{number}"] = "Close"
            else:
                warnings.append(f"{location}: valve V{number} has the unknown state '{state}'.")
        return states
    warnings.append(f"{location}: the valve cell '{text}' is not in the packed form (V1cV2o...) and was not read.")
    return {}


def read_pump_plan_csv(path: Path) -> PumpPlanReadResult:
    """Read *path* into a plan. Raises ``ValueError`` (naming the line) for anything that cannot be read safely,
    ``OSError`` when the file cannot be opened."""
    text, decode_warning = _read_text(path)
    warnings: list[str] = [decode_warning] if decode_warning else []
    lines = text.splitlines()
    if not lines:
        raise ValueError(f"{path.name} is empty.")
    layout = _layout_from_header_line(lines[0])
    if layout is None:
        raise ValueError(f"{path.name} does not look like a pump-plan CSV (no FR/Direction/Time columns in the header).")
    if not layout.flow_unit_known:
        warnings.append("The flow unit in the header was not recognised; the flow columns were taken as mL/min.")

    n_header = len(layout.headers)
    n_lead = layout.last_channel_column + 1
    n_tail = layout.tail_columns
    channels = sorted(set(layout.flow_columns) | set(layout.direction_columns))
    max_channel = channels[-1]

    steps: list[ExperimentPlanStep] = []
    cursor_s = 0.0
    reader = csv.reader(StringIO(text, newline=""), delimiter=layout.delimiter)
    next(reader)  # header
    for row in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        number = len(steps) + 1
        where = f"Step {number} (line {line})"

        if len(row) > n_header:
            extra = len(row) - n_header
            lead, tail = (row[: len(row) - n_tail], row[len(row) - n_tail :]) if n_tail else (row, [])
            surplus = lead[n_lead:]
            if len(surplus) != extra or any(cell.strip() for cell in surplus):
                raise ValueError(
                    f"{path.name}, {where}: {len(row)} cells but the header has {n_header}, and the extra cells are not "
                    "empty, so the columns cannot be matched safely. Please fix this row."
                )
            row = lead[:n_lead] + tail
            warnings.append(
                f"{where}: {extra} extra empty cell(s) before the Time column (a stray '{layout.delimiter}'); "
                "the columns were realigned."
            )
        elif len(row) < n_header:
            warnings.append(f"{where}: {len(row)} cells but the header has {n_header}; the missing cells are empty.")
            row = row + [""] * (n_header - len(row))

        time_cell = row[layout.time_column].strip()
        if not time_cell:
            raise ValueError(f"{path.name}, {where}: the Time cell is empty, so the step has no duration.")
        try:
            duration_s = _parse_clock_seconds(time_cell)
        except ValueError:
            raise ValueError(f"{path.name}, {where}: cannot read the duration '{time_cell}' (use HH:MM:SS).") from None
        if duration_s < 0:
            raise ValueError(f"{path.name}, {where}: the duration '{time_cell}' is negative.")

        channel_devices: list[dict[str, Any]] = []
        for channel in range(1, max_channel + 1):
            flow_cell = row[layout.flow_columns[channel]].strip() if channel in layout.flow_columns else ""
            direction_cell = row[layout.direction_columns[channel]].strip() if channel in layout.direction_columns else ""
            flow_ul_min = 0.0
            if flow_cell:
                try:
                    flow_ul_min = round(float(flow_cell.replace(",", ".")) * layout.flow_factor, 6)
                except ValueError:
                    raise ValueError(
                        f"{path.name}, {where}: cannot read the flow '{flow_cell}' of channel {channel}."
                    ) from None
                if flow_ul_min < 0:
                    raise ValueError(f"{path.name}, {where}: the flow of channel {channel} is negative ('{flow_cell}').")
            direction, recognised = _direction(direction_cell)
            if not recognised:
                warnings.append(f"{where}: channel {channel} has the unknown direction '{direction_cell}'.")
            if direction != "OFF" and flow_ul_min == 0.0:
                warnings.append(
                    f"{where}: channel {channel} has the direction '{direction_cell}' but the flow reads "
                    f"{flow_ul_min:g} (cell '{flow_cell}'); check the file for a mistyped value."
                )
            if direction == "OFF" and flow_ul_min > 0.0:
                warnings.append(f"{where}: channel {channel} has a flow ({flow_cell}) but no direction.")
            channel_devices.append({"channel": channel, "flow_ul_min": flow_ul_min, "direction": direction})

        devices: dict[str, Any] = {"channels": channel_devices}
        if layout.valves_column is not None:
            valves = _valves(row[layout.valves_column], where, warnings)
            if valves:
                devices["valves"] = valves
                if "V1" in valves:
                    devices["valve"] = valves["V1"]  # singleLSPR Acquisition's key (it drives V1 only)
        note = row[layout.notes_column].strip() if layout.notes_column is not None else ""

        steps.append(
            ExperimentPlanStep(
                id=number,
                label=f"Step {number}",
                start_s=cursor_s,
                end_s=cursor_s + duration_s,
                comment=note,
                devices=devices,
            )
        )
        cursor_s += duration_s

    if not steps:
        raise ValueError(f"{path.name} has a header but no steps.")
    return PumpPlanReadResult(plan=ExperimentPlan(units=dict(_PLAN_UNITS), steps=steps), warnings=warnings)
