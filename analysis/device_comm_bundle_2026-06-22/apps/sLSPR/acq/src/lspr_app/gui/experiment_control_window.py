from __future__ import annotations

import csv
import logging
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic, perf_counter

try:
    import h5py
except ImportError:  # pragma: no cover - optional dependency guard
    h5py = None

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency guard
    yaml = None

from PyQt6.QtCore import QByteArray, QObject, QRectF, QRunnable, QSize, QThreadPool, QTimer, Qt, QEvent, QModelIndex, QItemSelectionModel, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QKeySequence, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QFileDialog,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QSpinBox,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from lspr_app.device.device_comm_service import (
    PortRefreshData,
    connect_mswitch_port,
    connect_valve_port,
    probe_pump_port,
    refresh_device_ports,
)
from lspr_app.device.connection_registry import claim_port, release_port
from lspr_app.device.port_assignments import get_port_assignment
from lspr_app.device.serial_controllers import ControllerProbe, SerialController, controller_port_priority
from lspr_app.device.reglo_icc import PumpProbe, RegloICCClient, is_probable_reglo_port
from lspr_app import __version__
from lspr_app.gui.experiment_control_runtime import ExperimentRuntimeSnapshot, experiment_runtime_snapshot
from lspr_app.resources import app_icon_path
from lspr_app.domain.pump_plan import (
    ACTIVE_PUMP_CHANNELS,
    DEFAULT_TUBE_MM,
    HDF5_PUMP_CHANNELS,
    PumpChannelStep,
    PumpPlanStep,
    recompute_plan_timing,
    to_core_experiment_plan,
)
from lspr_app.gui.experiment_control_builders import (
    create_direction_button,
    create_flow_step_action_button,
    create_table_color_combo,
    create_table_comment_edit,
    create_table_duration_spin,
    create_table_flow_spin,
    create_table_switch_combo,
    create_table_valve_button,
    direction_glyph,
    set_step_valve_button_state_for_button,
)
from lspr_app.gui.flow_plan_model import (
    ExperimentPlanColorDelegate,
    ExperimentPlanDurationDelegate,
    ExperimentPlanFlowDelegate,
    ExperimentPlanSwitchDelegate,
    ExperimentPlanTableModel,
    ExperimentPlanValveDelegate,
)
from lspr_app.gui.experiment_control_table import (
    configure_experiment_control_table_columns,
    configure_experiment_control_plan_table,
    fit_plan_table_columns_to_viewport,
    sync_experiment_control_tube_columns,
    update_plan_detail_toggle_icon,
    update_plan_table_height,
)
from lspr_app.gui.experiment_control_editing import ExperimentControlEditingController
from lspr_app.gui.experiment_control_dialogs import ExperimentControlDialogs
from lspr_app.gui.icon_helpers import flow_tabler_icon, tint_tabler_icon, transport_icon
from lspr_app.gui.ui_helpers import make_compact_spinbox
from lspr_app.storage.app_config import load_app_setting, save_app_setting, save_window_ui_state
from lspr_io import (
    LSPR_MEASUREMENT_PLAN_COLUMNS,
    LSPR_MEASUREMENT_RUNTIME_DATASET_NAME,
    build_legacy_experiment_plan_row_table,
    validate_measurement_file,
)


_LOGGER = logging.getLogger("lspr_app.experiment_control")


def _make_frameless_icon_button(icon: QIcon, tooltip: str, *, size: int = 22, parent: QWidget | None = None) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("framelessIconButton")
    button.setIcon(icon)
    button.setToolTip(tooltip)
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setIconSize(QSize(size - 8, size - 8))
    button.setFixedSize(size, size)
    button.setStyleSheet(
        "QToolButton#framelessIconButton { background: transparent; border: none; padding: 0px; }"
        "QToolButton#framelessIconButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
        "QToolButton#framelessIconButton:checked { background: rgba(127, 127, 127, 0.14); border: none; }"
        "QToolButton#framelessIconButton:checked:hover { background: rgba(127, 127, 127, 0.18); border: none; }"
        "QToolButton#framelessIconButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
    )
    return button


def _safe_float(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).strip())
    except ValueError:
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default


class _NoFocusItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.showDecorationSelected = False
        super().paint(painter, opt, index)


class ExperimentControlTableView(QTableView):
    step_move_requested = pyqtSignal(int)
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # type: ignore[override]
        super().scrollTo(index, hint)
        self.horizontalScrollBar().setValue(0)

    def currentRow(self) -> int:
        return self.currentIndex().row()

    def currentColumn(self) -> int:
        return self.currentIndex().column()

    def rowCount(self) -> int:
        model = self.model()
        return int(model.rowCount()) if model is not None else 0

    def columnCount(self) -> int:
        model = self.model()
        return int(model.columnCount()) if model is not None else 0

    def setCurrentCell(self, row: int, column: int) -> None:
        model = self.model()
        if model is None or row < 0 or column < 0 or row >= model.rowCount() or column >= model.columnCount():
            self.setCurrentIndex(QModelIndex())
            return
        self.setCurrentIndex(model.index(row, column))

    def selectRow(self, row: int) -> None:  # noqa: N802 - Qt API compatibility
        model = self.model()
        selection_model = self.selectionModel()
        if model is None or selection_model is None:
            return
        if row < 0 or row >= model.rowCount():
            self.setCurrentCell(-1, -1)
            self.viewport().update()
            return
        column = self.currentColumn()
        if column < 0 or column >= model.columnCount():
            column = 0
        index = model.index(row, column)
        if index.isValid():
            self.setCurrentIndex(index)
            if self.selectionMode() != QAbstractItemView.SelectionMode.NoSelection:
                selection_model.select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
            else:
                selection_model.clearSelection()
        self.horizontalScrollBar().setValue(0)
        self.viewport().update()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        edit_mode = bool(self.property("experiment_control_edit_mode"))
        if edit_mode and event.matches(QKeySequence.StandardKey.Copy):
            self.copy_requested.emit()
            event.accept()
            return
        if edit_mode and event.matches(QKeySequence.StandardKey.Paste):
            self.paste_requested.emit()
            event.accept()
            return
        if edit_mode and event.key() in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.step_move_requested.emit(-1 if event.key() == Qt.Key.Key_PageUp else 1)
            event.accept()
            return
        super().keyPressEvent(event)


class PumpConnectSignals(QObject):
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class PumpConnectTask(QRunnable):
    def __init__(self, generation: int, port: str) -> None:
        super().__init__()
        self._generation = generation
        self._port = port
        self.signals = PumpConnectSignals()

    def run(self) -> None:
        try:
            probe = probe_pump_port(self._port)
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))
            return
        self.signals.finished.emit(self._generation, probe)


class ValveConnectSignals(QObject):
    finished = pyqtSignal(object)


class ValveConnectTask(QRunnable):
    def __init__(self, port: str) -> None:
        super().__init__()
        self._port = port
        self.signals = ValveConnectSignals()

    def run(self) -> None:
        try:
            client, probe = connect_valve_port(self._port)
        except Exception as exc:
            self.signals.finished.emit((self._port, None, None, str(exc)))
            return
        self.signals.finished.emit((self._port, client, probe, None))


class MSwitchConnectSignals(QObject):
    finished = pyqtSignal(object)


class MSwitchConnectTask(QRunnable):
    def __init__(self, port: str) -> None:
        super().__init__()
        self._port = port
        self.signals = MSwitchConnectSignals()

    def run(self) -> None:
        try:
            client, probe = connect_mswitch_port(self._port)
        except Exception as exc:
            self.signals.finished.emit((self._port, None, None, str(exc)))
            return
        self.signals.finished.emit((self._port, client, probe, None))


class PortRefreshSignals(QObject):
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class PortRefreshTask(QRunnable):
    def __init__(self, generation: int) -> None:
        super().__init__()
        self._generation = generation
        self.signals = PortRefreshSignals()

    def run(self) -> None:
        try:
            payload = refresh_device_ports(self._generation)
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))
            return
        self.signals.finished.emit(self._generation, payload)


@dataclass(slots=True)
class ExperimentPlanImportData:
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
    switch_solution_mode: bool | None = None
    selected_row: int | None = None
    valve_state_labels: dict[str, str] | None = None
    valve_state_colors: dict[str, str] | None = None
    color_palette_entries: list[dict[str, str]] | None = None


def _experiment_plan_cell(row: list[str], index: object | None, default: str = "") -> str:
    if not isinstance(index, int) or index < 0 or index >= len(row):
        return default
    return str(row[index] or default)


def _experiment_plan_switch_position_from_text(text: str) -> int:
    cleaned = str(text or "").strip()
    if not cleaned:
        return 1
    head = cleaned.split(":", 1)[0].strip()
    try:
        return max(min(int(float(head)), 12), 1)
    except ValueError:
        return 1


def _experiment_plan_normalize_valve(text: str, l_is_open: bool) -> str:
    lowered = str(text or "").strip().casefold()
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


def build_experiment_plan_steps_from_import_data(data: ExperimentPlanImportData, *, l_is_open: bool) -> list[PumpPlanStep]:
    steps: list[PumpPlanStep] = []
    for row_index, row in enumerate(data.rows, start=1):
        if not any(cell.strip() for cell in row):
            continue
        channels: list[PumpChannelStep] = []
        for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
            flow_text = _experiment_plan_cell(row, data.column_map.get(("flow", channel_index)), "0")
            direction_text = _experiment_plan_cell(row, data.column_map.get(("direction", channel_index)), "CW")
            flow_ml_min = max(_safe_float(flow_text), 0.0)
            direction = "CCW" if str(direction_text).casefold() == "ccw" else "CW"
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
        duration_s = max(_safe_float(_experiment_plan_cell(row, data.column_map.get("time"), "0")), 0.0)
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


def _decode_hdf5_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _read_hdf5_string_table(dataset: object) -> tuple[list[str], list[list[str]]]:
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


class ExperimentPlanImportSignals(QObject):
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class ExperimentPlanImportTask(QRunnable):
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

    def run(self) -> None:
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
                if "comment" in normalized or "description" in normalized or "descrit" in normalized:
                    column_map["description"] = index
                    continue
                match = re.match(r"ch(\d+)", normalized)
                if not match:
                    continue
                channel = int(match.group(1))
                if "flow" in normalized:
                    column_map[("flow", channel)] = index
                elif "direction" in normalized or normalized.endswith("dir"):
                    column_map[("direction", channel)] = index
                elif "tube" in normalized:
                    column_map[("tube", channel)] = index
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
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))

    def _load_hdf5_payload(self) -> ExperimentPlanImportData:
        if h5py is None:
            raise RuntimeError("h5py is required to import HDF5 experiment plans.")
        with h5py.File(self._path, "r") as handle:
            validation = validate_measurement_file(handle)
            if not validation.is_valid:
                raise ValueError("; ".join(validation.errors))

            switch_solution_rows: list[list[str]] | None = None
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
            return
        self.signals.finished.emit(self._generation, payload)


@dataclass(slots=True)
class ExperimentPlanExportData:
    path: Path
    header: list[str] | None = None
    rows: list[list[str]] | None = None
    document: dict[str, object] | None = None


class ExperimentPlanExportSignals(QObject):
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class ExperimentPlanExportTask(QRunnable):
    def __init__(self, generation: int, payload: ExperimentPlanExportData) -> None:
        super().__init__()
        self._generation = generation
        self._payload = payload
        self.signals = ExperimentPlanExportSignals()

    def run(self) -> None:
        try:
            self._payload.path.parent.mkdir(parents=True, exist_ok=True)
            if self._payload.document is not None:
                if yaml is None:
                    raise RuntimeError("PyYAML is required to export native YAML experiment plans.")
                text = yaml.safe_dump(
                    self._payload.document,
                    sort_keys=False,
                    allow_unicode=False,
                    default_flow_style=False,
                )
                self._payload.path.write_text(text, encoding="utf-8")
            else:
                with self._payload.path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle, delimiter=";", lineterminator="\n")
                    writer.writerow(self._payload.header or [])
                    writer.writerows(self._payload.rows or [])
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))
            return
        self.signals.finished.emit(self._generation, self._payload)


class PumpPlanTimelineWidget(QWidget):
    step_activated = pyqtSignal(int)
    step_double_activated = pyqtSignal(int)
    step_reordered = pyqtSignal(int, int)
    label_mode_toggled = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps: list[PumpPlanStep] = []
        self._selected_row: int | None = None
        self._segment_rects: list[QRectF] = []
        self._bar_rect = QRectF()
        self._progress_s: float | None = None
        self._runtime_s: float | None = None
        self._step_runtime_s: float | None = None
        self._plan_active_row: int | None = None
        self._dragging = False
        self._drag_start_row: int | None = None
        self._drag_target_row: int | None = None
        self._drag_mode: str | None = None
        self._drag_press_point = None
        self._drag_origin_pan_px = 0.0
        self._hover_row: int | None = None
        self._zoom_factor = 1.0
        self._pan_px = 0.0
        self._min_zoom = 1.0
        self._max_zoom = 24.0
        self._follow_current_step = True
        self._theme_mode = "light"
        self._time_unit_mode = "s"
        self._theme_palette: dict[str, str] = {}
        self._color_palette_entries: list[tuple[str, str]] = []
        self._label_mode = "comment"
        self._title_button_gap_px = 10
        self._title_button = QToolButton(self)
        self._title_button.setObjectName("flowViewModeButton")
        self._title_button.setAutoRaise(True)
        self._title_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._title_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_button.clicked.connect(lambda _checked=False: self.label_mode_toggled.emit())
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)
        self._update_label_mode_button()

    def set_theme(self, theme_mode: str) -> None:
        self._theme_mode = theme_mode if theme_mode in {"light", "dark"} else "light"
        self.update()

    def set_theme_palette(self, palette: dict[str, str]) -> None:
        self._theme_palette = dict(palette or {})
        self.update()

    def set_time_unit_mode(self, mode: str) -> None:
        self._time_unit_mode = mode if mode in {"s", "min", "h"} else "s"
        self.update()

    def set_color_palette_entries(self, entries: list[tuple[str, str]] | list[dict[str, str]]) -> None:
        normalized: list[tuple[str, str]] = []
        for entry in list(entries or []):
            if isinstance(entry, dict):
                name = str(entry.get("name", "") or "").strip()
                color = str(entry.get("color", "") or "").strip()
            elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
                name = str(entry[0] or "").strip()
                color = str(entry[1] or "").strip()
            else:
                continue
            if name and color:
                normalized.append((name, color))
        self._color_palette_entries = normalized
        self.update()

    def set_label_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in {"comment", "color_name"}:
            normalized = "comment"
        if self._label_mode != normalized:
            self._label_mode = normalized
        self._update_label_mode_button()
        self.update()

    def _label_mode_button_text(self) -> str:
        return {
            "comment": "[Label: Comment]",
            "color_name": "[Label: ColorName]",
        }.get(self._label_mode, "[Label: Comment]")

    def _label_mode_button_tooltip(self) -> str:
        current = self._label_mode_button_text().strip("[]")
        return f"Current timeline label mode: {current}. Click to switch label source."

    def _update_label_mode_button(self) -> None:
        if not hasattr(self, "_title_button"):
            return
        self._title_button.setText(self._label_mode_button_text())
        self._title_button.setToolTip(self._label_mode_button_tooltip())
        self._position_title_button()

    def _position_title_button(self) -> None:
        if not hasattr(self, "_title_button"):
            return
        title_font = self._scaled_font(self.font(), delta=-1.0, minimum=10.0)
        metrics = QFontMetricsF(title_font)
        title_width = int(round(metrics.horizontalAdvance("Timeline")))
        x = self._left_pad() + title_width + self._title_button_gap_px
        y = 4
        size = self._title_button.sizeHint()
        self._title_button.setGeometry(x, y, max(size.width(), 120), max(size.height(), 20))

    def _contrast_text_color(self, color: QColor) -> QColor:
        if not color.isValid():
            return QColor("#1d2733" if self._theme_mode != "dark" else "#e6ebf1")
        luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        return QColor("#111111" if luminance > 150 else "#ffffff")

    def set_steps(
        self,
        steps: list[PumpPlanStep],
        selected_row: int | None = None,
        progress_s: float | None = None,
        runtime_s: float | None = None,
        step_runtime_s: float | None = None,
    ) -> None:
        self._steps = recompute_plan_timing(steps)
        self._selected_row = selected_row
        self._progress_s = progress_s
        self._runtime_s = runtime_s
        self._step_runtime_s = step_runtime_s
        self._recalculate_zoom_floor()
        self._zoom_factor = max(1.0, min(float(self._zoom_factor), self._max_zoom))
        if not self._steps or self._zoom_factor <= 1.0 + 1e-6:
            self._pan_px = 0.0
        self._clamp_pan()
        self._ensure_step_visible(selected_row, center=bool(selected_row is not None))
        self.update()

    def set_progress(self, progress_s: float | None) -> None:
        self._progress_s = progress_s
        if progress_s is not None:
            self._follow_progress_step(progress_s)
        self.update()

    def set_follow_current_step(self, enabled: bool) -> None:
        self._follow_current_step = bool(enabled)

    def set_zoom_factor(self, factor: float, *, anchor_x: float | None = None) -> None:
        factor = max(1.0, min(float(factor), self._max_zoom))
        if abs(factor - self._zoom_factor) < 1e-6:
            return
        total = max(self._steps[-1].end_s if self._steps else 0.0, 1.0)
        viewport_width = max(self.width() - 2 * self._left_pad(), 1)
        old_content_width = max(viewport_width * self._zoom_factor, viewport_width)
        new_content_width = max(viewport_width * factor, viewport_width)
        if anchor_x is None:
            anchor_x = self.width() / 2.0
        anchor_x = max(0.0, min(float(anchor_x), float(self.width())))
        anchor_t = self._time_at_x(anchor_x, total=total, content_width=old_content_width)
        self._zoom_factor = factor
        self._pan_px = self._pan_for_time(anchor_t, anchor_x, total=total, content_width=new_content_width)
        self._clamp_pan()
        self._ensure_visible_target()
        self._position_title_button()
        self.update()

    def reset_zoom(self) -> None:
        self._recalculate_zoom_floor()
        self._zoom_factor = 1.0
        self._pan_px = 0.0
        self._ensure_visible_target()
        self._position_title_button()
        self.update()

    def _status_time_text(self, label: str, value_s: float | None) -> str:
        if value_s is None:
            return f"{label}: -"
        return f"{label}: {self._format_duration(max(float(value_s), 0.0))}"

    def _timeline_status_text(self) -> str:
        parts = self._timeline_status_parts()
        return " | ".join(part["text"] for part in parts)

    def _step_label_text(self, step: PumpPlanStep) -> str:
        if self._label_mode == "color_name":
            color_text = str(step.color or "").strip()
            for name, color in self._color_palette_entries:
                if str(color or "").strip().casefold() == color_text.casefold() and name:
                    return name
            return color_text or "-"
        return str(step.description or "").strip() or "-"

    def _timeline_status_parts(self) -> list[dict[str, object]]:
        total_end_s = self._steps[-1].end_s if self._steps else 0.0
        step_count = len(self._steps)
        plan_active_row = getattr(self, "_plan_active_row", None)
        current_step_index: int | None = None
        step_runtime_s: float | None = None
        step_eta_s: float | None = None
        step_eta_clock: str | None = None
        total_runtime_s: float | None = None
        total_eta_s: float | None = None
        total_eta_clock: str | None = None

        if self._steps and self._progress_s is not None and plan_active_row is not None and 0 <= plan_active_row < step_count:
            progress_s = min(max(float(self._progress_s), 0.0), total_end_s)
            current_step = self._steps[plan_active_row]
            active_elapsed_s = max(float(self._plan_elapsed_s), 0.0)
            total_runtime_s = max(float(self._runtime_s if self._runtime_s is not None else 0.0), 0.0)
            step_runtime_s = max(float(self._step_runtime_s if self._step_runtime_s is not None else active_elapsed_s), 0.0)
            step_eta_s = max(current_step.duration_s - active_elapsed_s, 0.0)
            total_eta_s = step_eta_s + sum(max(step.duration_s, 0.0) for step in self._steps[plan_active_row + 1 :])
            total_eta_clock = (datetime.now() + timedelta(seconds=total_eta_s)).strftime("%H:%M")
            step_eta_clock = (datetime.now() + timedelta(seconds=step_eta_s)).strftime("%H:%M")
            current_step_index = plan_active_row + 1
        elif self._steps and self._selected_row is not None and 0 <= self._selected_row < step_count:
            current_step_index = self._selected_row + 1
            total_runtime_s = max(float(self._runtime_s or 0.0), 0.0)
            total_eta_s = max(total_end_s, 0.0)
            total_eta_clock = (datetime.now() + timedelta(seconds=total_eta_s)).strftime("%H:%M")
            current_step = self._steps[self._selected_row]
            step_runtime_s = max(float(self._step_runtime_s or 0.0), 0.0)
            step_eta_s = max(current_step.duration_s, 0.0)
            step_eta_clock = (datetime.now() + timedelta(seconds=step_eta_s)).strftime("%H:%M")

        if current_step_index is None:
            current_step_index = self._selected_row + 1 if self._selected_row is not None else 1

        step_part = f"Step {current_step_index}/{step_count}" if step_count else "Step -"
        parts: list[dict[str, object]] = [
            {"text": step_part, "accent": True, "bold": True},
            {"text": self._status_time_text("Runtime", step_runtime_s), "accent": True, "bold": True},
            {
                "text": self._status_eta_text("ETA", step_eta_s, step_eta_clock),
                "accent": True,
                "bold": True,
            },
            {"text": self._status_time_text("Total Runtime", total_runtime_s), "accent": False, "bold": False},
            {
                "text": self._status_eta_text("Total ETA", total_eta_s, total_eta_clock),
                "accent": False,
                "bold": False,
            },
        ]
        return parts

    def _status_eta_text(self, label: str, value_s: float | None, clock_text: str | None) -> str:
        if value_s is None or clock_text is None:
            return f"{label}: -"
        return f"{label}: {self._format_duration(max(float(value_s), 0.0))} / {clock_text}"

    def _scaled_font(self, base_font: QFont, *, delta: float = 0.0, minimum: float = 1.0, bold: bool | None = None) -> QFont:
        font = QFont(base_font)
        base_size = float(font.pointSizeF())
        if base_size <= 0:
            base_size = 10.0
        new_size = max(base_size + delta, minimum, 1.0)
        font.setPointSizeF(new_size)
        if bold is not None:
            font.setBold(bold)
        return font

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        dark = self._theme_mode == "dark"
        palette = self._theme_palette or {}
        border = QColor(palette.get("border", "#2b3138" if dark else "#d9e0e7"))
        text_color = QColor(palette.get("fg", "#e6ebf1" if dark else "#1d2733"))
        muted = QColor(palette.get("muted", "#a8b0ba" if dark else "#5f7388"))
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            title_font = self._scaled_font(self.font(), delta=-1.0, minimum=10.0)
            painter.setFont(title_font)
            painter.setPen(QPen(QColor(palette.get("title", text_color.name()))))
            title_y = 18
            left_pad = 6
            painter.drawText(left_pad, title_y, "Timeline")
            title_width = painter.fontMetrics().horizontalAdvance("Timeline") + self._title_button.width() + self._title_button_gap_px

            status_parts = self._timeline_status_parts()
            x = max(left_pad + title_width + 12, 120)
            for index, part in enumerate(status_parts):
                text = str(part["text"])
                is_step_part = index < 3
                font = self._scaled_font(self.font(), delta=-1.0, minimum=9.0, bold=False)
                painter.setFont(font)
                metrics = painter.fontMetrics()
                if index < len(status_parts) - 1:
                    text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(self.width() - x - 90, 20))
                if is_step_part:
                    part_color = text_color
                else:
                    part_color = QColor("#f4f8fc" if dark else "#314355")
                painter.setPen(QPen(part_color))
                painter.drawText(x, title_y, text)
                x += metrics.horizontalAdvance(text) + 10
                if index == 2 and index < len(status_parts) - 1:
                    painter.setPen(QPen(muted))
                    painter.drawText(x, title_y, "|")
                    x += painter.fontMetrics().horizontalAdvance("|") + 10

            left_pad = self._left_pad()
            bar_rect = QRectF(left_pad, 28, self._content_width(), 22)
            self._bar_rect = bar_rect
            painter.setPen(Qt.PenStyle.NoPen)
            self._segment_rects = []

            if not self._steps:
                painter.setPen(QPen(muted))
                painter.setFont(self._scaled_font(self.font(), minimum=9.0))
                painter.drawText(left_pad, 56, "No pump-plan steps.")
                return

            total = max(self._steps[-1].end_s, 1.0)
            for index, step in enumerate(self._steps):
                left = bar_rect.left() + bar_rect.width() * (step.start_s / total) - self._pan_px
                right = bar_rect.left() + bar_rect.width() * (step.end_s / total) - self._pan_px
                width = max(right - left, 2.0)
                rect = QRectF(left, bar_rect.top(), width, bar_rect.height())
                self._segment_rects.append(rect)
                color = QColor(step.color if step.color else "#aab7c4")
                painter.fillRect(rect.adjusted(0, 0, -1, -1), color)
                if index == self._selected_row:
                    painter.setPen(QPen(QColor(255, 255, 255, 255), 2.2))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -2, -2), 4, 4)
                if step.description and width >= 32:
                    text_rect = rect.adjusted(3, 3, -3, -3)
                    label_value = self._step_label_text(step)
                    label_text = painter.fontMetrics().elidedText(
                        label_value,
                        Qt.TextElideMode.ElideRight,
                        max(int(text_rect.width()), 10),
                    )
                    painter.save()
                    painter.setPen(QPen(self._contrast_text_color(color)))
                    painter.setClipRect(text_rect)
                    painter.drawText(
                        text_rect,
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        label_text,
                    )
                    painter.restore()

            if self._progress_s is not None:
                total = max(self._steps[-1].end_s, 1.0)
                clamped = min(max(float(self._progress_s), 0.0), total)
                x_pos = bar_rect.left() + bar_rect.width() * (clamped / total) - self._pan_px
                painter.setPen(QPen(text_color, 2))
                painter.drawLine(
                    int(x_pos),
                    int(bar_rect.top()) - 3,
                    int(x_pos),
                    int(bar_rect.bottom()) + 3,
                )
        finally:
            painter.end()

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        point_f = event.position()
        point = point_f.toPoint()
        if self._dragging and self._drag_mode == "pan" and self._drag_press_point is not None:
            delta_x = float(point_f.x() - self._drag_press_point.x())
            if abs(delta_x) >= 1.0:
                self._pan_px = self._drag_origin_pan_px - delta_x
                self._clamp_pan()
                self.update()
            return
        if self._dragging and self._drag_mode == "reorder":
            row = self._row_for_point(point_f)
            if row is not None:
                self._drag_target_row = row
                self.step_activated.emit(row)
            return
        self._hover_row = None
        for index, rect in enumerate(self._segment_rects):
            if rect.contains(point_f):
                step = self._steps[index]
                self._hover_row = index
                QToolTip.showText(
                    self.mapToGlobal(point),
                    (
                        f"Step {step.step}\n"
                        f"{step.description or '-'}\n"
                        f"Valve: {step.valve or '-'}\n"
                        f"Switch: port {max(min(int(step.switch_position), 12), 1)}\n"
                        f"Start: {self._format_duration(step.start_s)}\n"
                        f"End: {self._format_duration(step.end_s)}\n"
                        f"Duration: {self._format_duration(step.duration_s)}"
                    ),
                    self,
                )
                return
        QToolTip.hideText()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() == Qt.MouseButton.RightButton and self._steps:
            self._dragging = True
            self._drag_mode = "pan"
            self._drag_press_point = event.position()
            self._drag_origin_pan_px = self._pan_px
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_press_point = event.position()
            self._drag_start_row = self._row_for_point(event.position())
            self._drag_target_row = self._drag_start_row
            self._drag_mode = "reorder"
            if self._drag_start_row is not None:
                self.step_activated.emit(self._drag_start_row)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_step_for_point(event.position(), double_click=True)
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if self._dragging and self._drag_mode == "reorder" and self._drag_start_row is not None and self._drag_target_row is not None:
            if self._drag_start_row != self._drag_target_row:
                self.step_reordered.emit(self._drag_start_row, self._drag_target_row)
        self._dragging = False
        self._drag_start_row = None
        self._drag_target_row = None
        self._drag_mode = None
        self._drag_press_point = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            event.ignore()
            return
        step = 1.15 if delta_y > 0 else 1 / 1.15
        self.set_zoom_factor(self._zoom_factor * step, anchor_x=event.position().x())
        event.accept()

    def resizeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().resizeEvent(event)
        self._position_title_button()
        self._recalculate_zoom_floor()
        self._zoom_factor = max(1.0, min(float(self._zoom_factor), self._max_zoom))
        self._clamp_pan()
        self._ensure_visible_target()
        self.update()

    def _row_for_point(self, point_f) -> int | None:
        if not self._steps:
            return None
        total = max(self._steps[-1].end_s, 1.0)
        visible_rect = QRectF(
            self._bar_rect.left() - self._pan_px,
            self._bar_rect.top(),
            self._bar_rect.width(),
            self._bar_rect.height(),
        )
        if not visible_rect.contains(point_f):
            return None
        rel = (float(point_f.x()) + self._pan_px - self._bar_rect.left()) / max(self._bar_rect.width(), 1.0)
        elapsed_s = min(max(rel, 0.0), 1.0) * total
        for index, step in enumerate(self._steps):
            if step.start_s <= elapsed_s <= step.end_s or index == len(self._steps) - 1:
                return index
        return None

    def _emit_step_for_point(self, point_f, *, double_click: bool = False) -> None:
        row = self._row_for_point(point_f)
        if row is None:
            return
        if double_click:
            self.step_double_activated.emit(row)
        else:
            self.step_activated.emit(row)

    def _duration_display_decimals(self) -> int:
        if self._time_unit_mode == "min":
            return 1
        if self._time_unit_mode == "h":
            return 2
        return 0

    def _format_duration(self, seconds: float, *, decimals: int | None = None) -> str:
        if decimals is None:
            decimals = self._duration_display_decimals()
        seconds = max(float(seconds), 0.0)
        if decimals == 0:
            value = f"{int(round(seconds))}"
        else:
            value = f"{seconds:.{decimals}f}"
        if self._time_unit_mode == "min":
            return f"{seconds / 60.0:.{decimals}f} min"
        if self._time_unit_mode == "h":
            return f"{seconds / 3600.0:.{decimals}f} h"
        return f"{value} s"

    def _left_pad(self) -> int:
        return 6

    def _recalculate_zoom_floor(self) -> None:
        if not self._steps:
            self._max_zoom = 24.0
            return
        viewport_width = max(self.width() - 2 * self._left_pad(), 1)
        min_duration = min((float(step.duration_s) for step in self._steps if float(step.duration_s) > 0.0), default=0.0)
        if min_duration <= 0.0:
            self._max_zoom = 24.0
            return
        fm = self.fontMetrics()
        label_width = max(
            fm.horizontalAdvance("00:00"),
            max(
                (
                    max(
                        fm.horizontalAdvance(f"Step {step.step}"),
                        fm.horizontalAdvance(self._step_label_text(step)),
                    )
                    for step in self._steps
                ),
                default=0,
            ),
        )
        desired_step_px = max(80.0, min(180.0, float(label_width) + 28.0))
        total = max(float(self._steps[-1].end_s), 1.0)
        required_zoom = (desired_step_px * total) / max(viewport_width * min_duration, 1.0)
        self._max_zoom = max(8.0, min(required_zoom, 64.0))

    def _content_width(self) -> float:
        viewport_width = max(self.width() - 2 * self._left_pad(), 1)
        return max(viewport_width * self._zoom_factor, viewport_width)

    def _max_pan(self) -> float:
        viewport_width = max(self.width() - 2 * self._left_pad(), 1)
        return max(self._content_width() - viewport_width, 0.0)

    def _clamp_pan(self) -> None:
        self._pan_px = max(0.0, min(float(self._pan_px), self._max_pan()))

    def _time_at_x(self, x: float, *, total: float, content_width: float | None = None) -> float:
        if content_width is None:
            content_width = self._content_width()
        rel = (float(x) + self._pan_px - self._left_pad()) / max(content_width, 1.0)
        return min(max(rel, 0.0), 1.0) * total

    def _pan_for_time(self, elapsed_s: float, anchor_x: float, *, total: float, content_width: float | None = None) -> float:
        if content_width is None:
            content_width = self._content_width()
        rel = min(max(float(elapsed_s), 0.0), total) / max(total, 1.0)
        return (self._left_pad() + rel * content_width) - float(anchor_x)

    def _step_row_for_elapsed(self, elapsed_s: float | None) -> int | None:
        if elapsed_s is None or not self._steps:
            return None
        total = max(self._steps[-1].end_s, 1.0)
        clamped = min(max(float(elapsed_s), 0.0), total)
        for index, step in enumerate(self._steps):
            if step.start_s <= clamped < step.end_s or index == len(self._steps) - 1:
                return index
        return None

    def _ensure_step_visible(self, row: int | None, *, center: bool = False) -> None:
        if row is None or not self._steps or self._zoom_factor <= 1.0:
            return
        row = max(min(int(row), len(self._steps) - 1), 0)
        total = max(self._steps[-1].end_s, 1.0)
        content_width = self._content_width()
        left = self._left_pad() + (self._steps[row].start_s / total) * content_width
        right = self._left_pad() + (self._steps[row].end_s / total) * content_width
        visible_left = float(self._pan_px)
        visible_right = float(self._pan_px) + max(self.width() - 2 * self._left_pad(), 1)
        if center or left < visible_left or right > visible_right:
            target_s = (self._steps[row].start_s + self._steps[row].end_s) / 2.0
            self._pan_px = self._pan_for_time(target_s, self.width() / 2.0, total=total, content_width=content_width)
            self._clamp_pan()

    def _ensure_visible_target(self) -> None:
        if self._follow_current_step and self._progress_s is not None:
            self._follow_progress_step(self._progress_s)
        elif self._selected_row is not None:
            self._ensure_step_visible(self._selected_row, center=False)

    def _follow_progress_step(self, progress_s: float) -> None:
        if not self._follow_current_step or self._zoom_factor <= 1.0 or not self._steps:
            return
        row = self._step_row_for_elapsed(progress_s)
        self._ensure_step_visible(row, center=True)


class PlanColorDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index) -> None:  # pragma: no cover - GUI runtime path
        value = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip() or "#4E79A7"
        color = QColor(value)
        if not color.isValid():
            color = QColor("#4E79A7")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect.adjusted(3, 3, -3, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 5, 5)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#d8b44a"), 2))
            painter.drawRoundedRect(rect, 5, 5)

        luminance = (0.2126 * color.redF()) + (0.7152 * color.greenF()) + (0.0722 * color.blueF())
        text_color = QColor("#111111" if luminance > 0.62 else "#f5f7fb")
        painter.setPen(QPen(text_color))
        text_rect = rect.adjusted(8, 0, -8, 0)
        label = painter.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, max(text_rect.width(), 10))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

        painter.restore()


class ExperimentControlWindow(QWidget):
    availability_changed = pyqtSignal(object)
    valve_availability_changed = pyqtSignal(object)
    mswitch_availability_changed = pyqtSignal(object)
    recording_control_requested = pyqtSignal(str)
    experimental_control_state_recorded = pyqtSignal(object)
    flow_state_recorded = experimental_control_state_recorded
    theme_changed = pyqtSignal(str)
    PLAN_COLOR_OPTIONS = [
        ("Blue", "#4E79A7"),
        ("Green", "#59A14F"),
        ("Red", "#E15759"),
        ("Orange", "#F28E2B"),
        ("Purple", "#B07AA1"),
        ("Teal", "#76B7B2"),
        ("Gold", "#EDC948"),
        ("Gray", "#9C9DA1"),
    ]
    STARTUP_DEVICE_ORDER = ("pump", "valve", "mswitch")

    PLAN_COLUMNS = [
        "step",
        "duration_s",
        "start_s",
        "end_s",
        *[
            item
            for channel_index in range(ACTIVE_PUMP_CHANNELS)
            for item in (
                f"ch{channel_index + 1}_flow_ul_min",
                f"ch{channel_index + 1}_direction",
                f"ch{channel_index + 1}_tube_mm",
            )
        ],
        "valve",
        "switch",
        "color",
        "description",
    ]

    def __init__(
        self,
        ui_state: dict[str, object],
        known_probe: PumpProbe | None = None,
        theme_mode: str | None = None,
        initial_mswitch_devices: list[ControllerProbe] | None = None,
        auto_connect_devices: bool = False,
        show_runtime_controls: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bootstrap_t0 = perf_counter()
        self._bootstrap_batches_logged = 0
        self._ui_state = ui_state
        self._pause_state_dialog_state: dict[str, object] = {}
        self._start_maximized = False
        self._updating_table = False
        self._plan_table_active_editor: tuple[int, int] | None = None
        self._client = RegloICCClient()
        self._probe: PumpProbe | None = known_probe
        self._last_selected_port: str | None = None
        self._last_selected_valve_port: str | None = None
        self._last_selected_mswitch_port: str | None = None
        self._thread_pool = QThreadPool.globalInstance()
        self._connect_generation = 0
        self._connect_in_progress = False
        self._connect_task: PumpConnectTask | None = None
        self._valve_client: SerialController | None = None
        self._valve_probe: ControllerProbe | None = None
        self._valve_connect_in_progress = False
        self._valve_connect_task: ValveConnectTask | None = None
        self._mswitch_client: AMFSwitchController | None = None
        self._mswitch_probe: ControllerProbe | None = None
        self._mswitch_probe_cache: list[ControllerProbe] | None = list(initial_mswitch_devices or [])
        self._mswitch_connect_in_progress = False
        self._mswitch_connect_task: MSwitchConnectTask | None = None
        self._connection_sync_in_progress = False
        self._auto_connect_devices = bool(auto_connect_devices)
        self._startup_auto_connect_enabled = False
        self._startup_auto_connect_scheduled = False
        self._startup_auto_connect_active = False
        self._startup_auto_connect_stage: str | None = None
        self._startup_auto_connect_queue: list[str] = []
        self._show_runtime_controls = bool(show_runtime_controls)
        self._ui_startup_ready = False
        self._plan_running = False
        self._plan_holding = False
        self._plan_paused = False
        self._plan_hold_blink_frame = 0
        self._plan_hold_blink_timer = QTimer(self)
        self._plan_hold_blink_timer.setInterval(110)
        self._plan_hold_blink_timer.timeout.connect(self._advance_plan_hold_blink_indicator)
        self._paused_plan_step: PumpPlanStep | None = None
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_runtime_s = 0.0
        self._plan_resume_runtime_s = 0.0
        self._plan_started_monotonic: float | None = None
        self._step_started_monotonic: float | None = None
        self._measurement_started_monotonic: float | None = None
        self._pending_experiment_control_start_after_recording: tuple[bool, int | None] | None = None
        self._plan_active_row: int | None = None
        self._applied_plan_step: PumpPlanStep | None = None
        self._status_message_base = "Pump not connected."
        self._show_plan_details = bool(ui_state.get("show_plan_details", False))
        self._editor_duration_seconds = 60.0
        self._suspend_duration_tracking = False
        self._updating_switch_editor = False
        self._experiment_control_steps_cache: list[PumpPlanStep] = []
        self._switch_solution_mode = bool(ui_state.get("switch_solution_mode", False))
        self._wait_for_mswitch_first = bool(ui_state.get("wait_for_mswitch_first", False))
        self._valve_state_labels = self._load_valve_state_labels(ui_state)
        self._valve_state_colors = self._load_valve_state_colors(ui_state)
        self._switch_solution_labels = [
            "empty"
            for index in range(1, 13)
        ]
        self._color_palette_entries = self._load_color_palette_entries(ui_state)
        self._sync_custom_plan_colors_from_palette()
        self._tint_icon = tint_tabler_icon
        self._experiment_plan_import_generation = 0
        self._experiment_plan_import_task: ExperimentPlanImportTask | None = None
        self._experiment_plan_import_in_progress = False
        self._experiment_plan_import_pending_steps: list[PumpPlanStep] = []
        self._experiment_plan_import_pending_payload: ExperimentPlanImportData | None = None
        self._experiment_plan_import_pending_selected_row: int | None = None
        self._experiment_plan_import_pending_step_index = 0
        self._experiment_plan_import_pending_batch_size = 24
        self._experiment_plan_export_generation = 0
        self._experiment_plan_export_task: ExperimentPlanExportTask | None = None
        self._experiment_plan_export_in_progress = False
        self._port_refresh_generation = 0
        self._port_refresh_task: PortRefreshTask | None = None
        self._port_refresh_in_progress = False
        self._experiment_control_bootstrap_in_progress = False
        self._experiment_control_bootstrap_started = False
        self._experiment_control_bootstrap_pending_steps: list[PumpPlanStep] = []
        self._experiment_control_bootstrap_pending_row_order: list[int] = []
        self._experiment_control_bootstrap_pending_selected_row: int | None = None
        self._experiment_control_bootstrap_pending_pause_selected = False
        self._experiment_control_bootstrap_pending_state: dict[str, object] | None = None
        self._experiment_control_bootstrap_pending_step_index = 0
        self._experiment_control_bootstrap_batch_size = 24
        self._experiment_control_view_mode_sizes: dict[str, list[int]] = {}
        self._experiment_control_view_mode_panel_sizes: dict[str, list[int]] = {}
        self._experiment_control_view_mode_apply_pending = False
        self._experiment_control_visible_rows_timer = QTimer(self)
        self._experiment_control_visible_rows_timer.setSingleShot(True)
        self._experiment_control_visible_rows_timer.setInterval(0)
        self._experiment_control_visible_rows_timer.timeout.connect(self._load_visible_experiment_control_rows)
        self._experiment_control_loaded_widget_rows: set[int] = set()
        self._experiment_control_pause_template = PumpPlanStep(
            step=0,
            duration_s=0.0,
            color=self._default_experiment_control_color(0),
            valve="Open",
            switch_position=1,
            description="Pause",
            channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
        )
        self._plan_timer = QTimer(self)
        self._plan_timer.setInterval(150)
        self._plan_timer.timeout.connect(self._advance_experiment_control_progress)
        loaded_theme = str(theme_mode or load_app_setting("theme_mode", "dark"))
        self._theme_mode = "dark" if loaded_theme not in {"light", "dark"} else loaded_theme
        if self._theme_mode != "dark":
            self._theme_mode = "dark"
            save_app_setting("theme_mode", self._theme_mode)
        loaded_time_unit = str(ui_state.get("time_unit_mode", "s"))
        self._time_unit_mode = loaded_time_unit if loaded_time_unit in {"s", "min", "h"} else "s"
        self._experiment_control_view_mode_sizes = self._load_experiment_control_view_mode_sizes(ui_state)
        self._experiment_control_view_mode_panel_sizes = self._load_experiment_control_view_mode_panel_sizes(ui_state)
        self._experiment_control_view_mode = self._normalize_experiment_control_view_mode(
            ui_state.get("experiment_control_view_mode", "full")
        )
        self._experiment_control_timeline_label_mode = self._normalize_experiment_control_timeline_label_mode(
            ui_state.get("timeline_label_mode", "comment")
        )
        legacy_sizes = ui_state.get("experiment_control_editor_splitter_sizes")
        if legacy_sizes is None:
            legacy_sizes = ui_state.get("flow_editor_splitter_sizes")
        if not self._experiment_control_view_mode_sizes and isinstance(legacy_sizes, list):
            parsed_legacy: list[int] = []
            for value in legacy_sizes[:2]:
                try:
                    parsed_legacy.append(max(int(value), 20))
                except (TypeError, ValueError):
                    parsed_legacy = []
                    break
            if len(parsed_legacy) == 2:
                self._experiment_control_view_mode_sizes[self._experiment_control_view_mode] = parsed_legacy
        self._experiment_control_view_mode_apply_pending = True

        _LOGGER.info("Experiment control bootstrap +%.1f ms: init state prepared", (perf_counter() - self._bootstrap_t0) * 1000.0)

        self.setWindowTitle(f"Experiment Control {__version__}")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.resize(1220, 860)
        self._apply_style()

        self.port_combo = QComboBox(self)
        self.port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.refresh_ports_button = QPushButton("Refresh", self)
        self.connection_toggle_button = QPushButton("Connect", self)
        self.connection_toggle_button.setObjectName("accentButton")
        self.pump_info_button = QToolButton(self)
        self.pump_info_button.setText("i")
        self.pump_info_button.setToolTip("Pump details")
        self.connection_dot = QLabel(self)
        self.connection_dot.setFixedSize(10, 10)
        self.connection_status_label = QLabel("Pump not connected.")
        self.connection_status_label.setWordWrap(True)
        self.protocol_value = QLabel("-", self)
        self.model_value = QLabel("-", self)
        self.serial_value = QLabel("-", self)
        self.channels_value = QLabel("-", self)

        self.valve_connection_dot = QLabel(self)
        self.valve_connection_dot.setFixedSize(10, 10)
        self.valve_connection_status_label = QLabel("Valve controller offline.")
        self.valve_port_combo = QComboBox(self)
        self.valve_port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.valve_refresh_ports_button = QPushButton("Refresh", self)
        self.valve_connection_toggle_button = QPushButton("Connect", self)
        self.valve_connection_toggle_button.setObjectName("accentButton")

        self.mswitch_connection_dot = QLabel(self)
        self.mswitch_connection_dot.setFixedSize(10, 10)
        self.mswitch_connection_status_label = QLabel("M-Switch offline.")
        self.mswitch_connection_status_label.setWordWrap(True)
        self.mswitch_port_combo = QComboBox(self)
        self.mswitch_port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.mswitch_refresh_ports_button = QPushButton("Refresh", self)
        self.mswitch_connection_toggle_button = QPushButton("Connect", self)
        self.mswitch_connection_toggle_button.setObjectName("accentButton")
        self.mswitch_home_button = QPushButton("Home", self)
        self.mswitch_move_button = QPushButton("Move", self)
        self.mswitch_target_spin = QSpinBox(self)
        make_compact_spinbox(self.mswitch_target_spin)
        self.mswitch_target_spin.setRange(1, 12)
        self.mswitch_target_spin.setValue(1)
        self.mswitch_target_spin.setSuffix("")
        self.mswitch_current_value = QLabel("-")

        self.manual_flow_spins: list[QDoubleSpinBox] = []
        self.manual_direction_buttons: list[QToolButton] = []
        self.manual_tube_spins: list[QDoubleSpinBox] = []
        self.shared_direction_button = create_direction_button(self, "CW")
        self.shared_tube_spin = QDoubleSpinBox(self)
        make_compact_spinbox(self.shared_tube_spin)
        self.shared_tube_spin.setRange(0.13, 3.17)
        self.shared_tube_spin.setDecimals(2)
        self.shared_tube_spin.setSingleStep(0.01)
        self.shared_tube_spin.setValue(DEFAULT_TUBE_MM)
        self.shared_tube_spin.setSuffix("")
        self.manual_uniform_button = QToolButton(self)
        self.manual_uniform_button.setCheckable(True)
        self.manual_uniform_button.setChecked(True)
        self.plan_detail_toggle = QToolButton(self)
        self.plan_detail_toggle.setObjectName("flowStepActionButton")
        self.plan_detail_toggle.setCheckable(True)
        self.plan_detail_toggle.setChecked(self._show_plan_details)
        self.plan_detail_toggle.setAutoRaise(True)
        self.plan_detail_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.plan_detail_toggle.setFixedSize(32, 32)
        self.plan_detail_toggle.setIconSize(QSize(24, 24))
        self.plan_detail_toggle.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self._update_plan_detail_toggle_icon()
        self.pause_state_button = self._make_icon_button(
            self._pause_state_button_icon(),
            "Edit the synthetic pause state applied when the plan enters pause mode.",
        )
        self.color_comment_button = QToolButton(self)
        self.color_comment_button.setObjectName("flowStepActionButton")
        self.color_comment_button.setAutoRaise(True)
        self.color_comment_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.color_comment_button.setFixedSize(32, 32)
        self.color_comment_button.setIconSize(QSize(24, 24))
        self.color_comment_button.setIcon(tint_tabler_icon(flow_tabler_icon("arrow_move_right"), QColor("#9b7dff")))
        self.color_comment_button.setToolTip("Copy each row color name into the comment field.")
        self.color_comment_button.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self.step_duration_spin = QDoubleSpinBox(self)
        make_compact_spinbox(self.step_duration_spin)
        self.step_duration_spin.setRange(0.0, 86400.0)
        self.step_duration_spin.setDecimals(1)
        self.step_duration_spin.setSingleStep(5.0)
        self.step_duration_spin.setValue(60.0)
        self.step_duration_spin.setSuffix(" s")
        self.time_unit_toggle = QToolButton(self)
        self.time_unit_toggle.setMinimumWidth(34)
        self.time_unit_toggle.setMaximumWidth(42)
        self.time_unit_toggle.setToolTip("Cycle time display/editing between seconds, minutes, and hours. Internally and in saved data, times stay in seconds.")
        self.color_palette_button = QToolButton(self)
        self.color_palette_button.setObjectName("flowColorAddButton")
        self.color_palette_button.setAutoRaise(True)
        self.color_palette_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.color_palette_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")))
        self.color_palette_button.setIconSize(QSize(21, 21))
        self.color_palette_button.setToolTip("Edit and overwrite the color palette used by the dropdown.")
        self.remove_custom_color_button = QToolButton(self)
        self.remove_custom_color_button.setObjectName("flowColorRemoveButton")
        self.remove_custom_color_button.setAutoRaise(True)
        self.remove_custom_color_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.remove_custom_color_button.setIcon(tint_tabler_icon(flow_tabler_icon("x"), QColor("#b44a4a")))
        self.remove_custom_color_button.setIconSize(QSize(21, 21))
        self.remove_custom_color_button.setToolTip("Remove the selected palette entry.")
        self.remove_custom_color_button.setVisible(False)
        self.step_color_combo = QComboBox(self)
        self.step_color_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._populate_color_combo(self.step_color_combo)
        self.step_color_combo.setToolTip("Step color used in the plan timeline for quick visual identification.")
        self._install_click_to_open_combo_filter(self.step_color_combo)
        self.step_valve_button = QToolButton(self)
        self.step_valve_button.setToolTip("Valve state to associate with this step. Click to toggle between Open and Close.")
        self.step_valve_button.setCheckable(True)
        self.step_valve_button.setAutoRaise(True)
        self.step_valve_settings_button = QToolButton(self)
        self.step_valve_settings_button.setObjectName("flowValveSettingsButton")
        self.step_valve_settings_button.setAutoRaise(True)
        self.step_valve_settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.step_valve_settings_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")))
        self.step_valve_settings_button.setIconSize(QSize(20, 20))
        self.step_valve_settings_button.setToolTip("Edit the text labels used for valve states.")
        self._set_step_valve_button_state("Open")
        self.step_valve_button.clicked.connect(self._toggle_step_valve_button)
        self.step_switch_spin = QSpinBox(self)
        make_compact_spinbox(self.step_switch_spin)
        self.step_switch_spin.setRange(1, 12)
        self.step_switch_spin.setValue(1)
        self.step_switch_spin.setFixedWidth(96)
        self.step_switch_spin.setToolTip("AMF switch position for this step. Select a port from 1 to 12.")
        self.step_switch_combo = QComboBox(self)
        self.step_switch_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.step_switch_combo.setMinimumWidth(148)
        self.step_switch_combo.setToolTip("AMF switch position and solution for this step.")
        self.step_switch_combo.currentIndexChanged.connect(self._handle_step_switch_combo_changed)
        self.step_switch_combo.setVisible(False)
        self.step_switch_mode_button = QToolButton(self)
        self.step_switch_mode_button.setObjectName("flowSwitchModeButton")
        self.step_switch_mode_button.setAutoRaise(True)
        self.step_switch_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.step_switch_mode_button.setIconSize(QSize(20, 20))
        self.step_switch_mode_button.toggled.connect(self._set_switch_solution_mode)
        self.step_switch_mode_button.setCheckable(True)
        self.step_switch_mode_button.blockSignals(True)
        self.step_switch_mode_button.setChecked(False)
        self.step_switch_mode_button.blockSignals(False)
        self.step_switch_mode_button.setVisible(False)
        self.step_switch_settings_button = QToolButton(self)
        self.step_switch_settings_button.setObjectName("flowSwitchSettingsButton")
        self.step_switch_settings_button.setAutoRaise(True)
        self.step_switch_settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.step_switch_settings_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")))
        self.step_switch_settings_button.setIconSize(QSize(20, 20))
        self.step_switch_settings_button.setToolTip("Edit the switch solution labels.")
        self.step_comment_edit = QLineEdit(self)
        self.step_comment_edit.setPlaceholderText("Comment")
        self.step_comment_edit.setToolTip("Free-text note for the step. It is shown in the timeline when there is enough space.")
        self.plan_toggle_button = self._make_icon_button(
            transport_icon(self._theme_mode, "play"),
            "Run or resume plan",
        )
        self.plan_toggle_button.setToolTip("Run or resume the plan.")
        self.hold_plan_button = self._make_icon_button(
            transport_icon(self._theme_mode, "hold"),
            "Hold plan",
        )
        self.hold_plan_button.setCheckable(True)
        self.pause_plan_button = self._make_icon_button(
            self._runtime_pause_button_icon(),
            "Pause plan",
        )
        self.pause_plan_button.setCheckable(True)
        self.stop_plan_button = self._make_icon_button(
            transport_icon(self._theme_mode, "stop"),
            "Stop plan",
        )
        self.previous_step_button = self._make_icon_button(
            transport_icon(self._theme_mode, "previous"),
            "Previous step",
        )
        self.next_step_button = self._make_icon_button(
            transport_icon(self._theme_mode, "next"),
            "Next step",
        )

        self.plan_table = ExperimentControlTableView()
        self.plan_table.setObjectName("flowControlTable")
        # The plan table setup is centralized so the view, model, delegates, and layout rules stay in one place.
        configure_experiment_control_plan_table(self)

        self.pause_table = QTableWidget(self)
        self.pause_table.setObjectName("flowControlPauseTable")
        self.pause_table.setColumnCount(len(self.PLAN_COLUMNS))
        self.pause_table.setHorizontalHeaderLabels(self.PLAN_COLUMNS)
        self.pause_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pause_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pause_table.verticalHeader().setVisible(False)
        self.pause_table.verticalHeader().setDefaultSectionSize(20)
        self.pause_table.horizontalHeader().setMinimumHeight(20)
        self.pause_table.horizontalHeader().setMaximumHeight(20)
        self.pause_table.setAlternatingRowColors(True)
        self.pause_table.horizontalHeader().setStretchLastSection(False)
        self.pause_table.setWordWrap(False)
        self.pause_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.pause_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pause_table.setItemDelegate(_NoFocusItemDelegate(self.pause_table))
        pause_palette = self.pause_table.palette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pause_palette.setColor(group, QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
            pause_palette.setColor(group, QPalette.ColorRole.HighlightedText, QColor(self._theme_palette()["fg"]))
        self.pause_table.setPalette(pause_palette)
        self.pause_table.viewport().setPalette(pause_palette)
        self.pause_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pause_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.pause_table.setMaximumHeight(self.pause_table.verticalHeader().defaultSectionSize() + self.pause_table.horizontalHeader().height() + 6)
        self.pause_table.setFrameShape(QFrame.Shape.NoFrame)
        self.pause_table.setShowGrid(True)
        self.pause_table.setVisible(False)
        self._plan_table_layout_save_timer = QTimer(self)
        self._plan_table_layout_save_timer.setSingleShot(True)
        self._plan_table_layout_save_timer.setInterval(150)
        self._plan_table_layout_save_timer.timeout.connect(self.save_ui_state)
        self._plan_table_fit_timer = QTimer(self)
        self._plan_table_fit_timer.setSingleShot(True)
        self._plan_table_fit_timer.setInterval(0)
        self._plan_table_fit_timer.timeout.connect(self._fit_plan_table_columns_to_viewport)
        self._suppress_plan_table_layout_save = True
        self._plan_table_layout_locked = False
        self._plan_table_initial_fit_pending = True
        self._experiment_control_edit_mode = False
        self._flow_editor_splitter_initialized = False

        self.add_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("square_plus"), QColor("#47a861")),
            "Add a step after the selected row.",
        )
        self.duplicate_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("copy"), QColor("#4f88ff")),
            "Duplicate the selected step.",
        )
        self.remove_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("trash"), QColor("#b44a4a")),
            "Remove the selected step.",
        )
        self.apply_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("edit"), QColor("#e8d85f")),
            "Toggle table edit mode.",
        )
        self.apply_step_button.setCheckable(True)
        self.apply_step_button.toggled.connect(lambda checked: self._set_experiment_control_edit_mode_button_icon(bool(checked)))
        self._experiment_control_edit_controller = ExperimentControlEditingController(self, self.plan_table, self.apply_step_button)
        self.import_plan_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("file_import"), QColor("#66d48a")),
            "Import a experiment plan from CSV or TXT.",
        )
        self.export_plan_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("file_export"), QColor("#8fbaff")),
            "Export the current experiment plan to CSV or TXT.",
        )
        self.import_plan_busy_label = QLabel("â—")
        self.import_plan_busy_label.setVisible(False)
        self.import_plan_busy_label.setFixedSize(16, 16)
        self.import_plan_busy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.import_plan_busy_label.setToolTip("Import is running.")
        self._import_plan_busy_frames = ["â—", "â—“", "â—‘", "â—’"]
        self._import_plan_busy_frame_index = 0
        self._import_plan_busy_timer = QTimer(self)
        self._import_plan_busy_timer.setInterval(140)
        self._import_plan_busy_timer.timeout.connect(self._advance_import_plan_busy_indicator)
        self._experiment_plan_import_fill_timer = QTimer(self)
        self._experiment_plan_import_fill_timer.setInterval(0)
        self._experiment_plan_import_fill_timer.timeout.connect(self._advance_experiment_plan_import_population)
        self.record_with_flow_button = QToolButton(self)
        self.record_with_flow_button.setObjectName("flowStepActionButton")
        self.record_with_flow_button.setAutoRaise(True)
        self.record_with_flow_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.record_with_flow_button.setFixedSize(32, 32)
        self.record_with_flow_button.setIconSize(QSize(24, 24))
        self.record_with_flow_button.setToolTip("Record measurement data while the experiment plan runs.")
        self.record_with_flow_button.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self.record_with_flow_button.setCheckable(True)
        self.record_with_flow_button.setChecked(True)
        self.record_with_flow_button.toggled.connect(self._handle_record_with_flow_button_toggled)
        self._record_with_flow_recording_active = False
        self._record_with_flow_locked_checked = bool(self.record_with_flow_button.isChecked())
        self._update_record_with_flow_button_icon()
        self.timeline_widget = PumpPlanTimelineWidget()
        self.timeline_widget.set_theme(self._theme_mode)
        self.timeline_widget.set_theme_palette(self._theme_palette())
        self.timeline_widget.set_time_unit_mode(self._time_unit_mode)
        self.timeline_widget.set_color_palette_entries(self._color_palette_entries)
        self.timeline_widget.set_label_mode(self._experiment_control_timeline_label_mode)
        self.timeline_widget.label_mode_toggled.connect(self._cycle_experiment_control_timeline_label_mode)
        self.timeline_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._startup_ui_pending = True
        self._build_ui()
        _LOGGER.info("Experiment control bootstrap +%.1f ms: UI built", (perf_counter() - self._bootstrap_t0) * 1000.0)
        self._refresh_switch_solution_combo(self.step_switch_spin.value())
        self._set_switch_solution_mode(self._switch_solution_mode)
        self._connect_signals()
        self._update_time_unit_ui()
        self._experiment_control_edit_controller.set_edit_mode(False)
        self._update_experiment_control_toggle_button()
        self._restore_ui_state()
        self._restore_experiment_control_state()
        _LOGGER.info("Experiment control bootstrap +%.1f ms: state restore queued", (perf_counter() - self._bootstrap_t0) * 1000.0)
        self._set_manual_uniform_mode(self.manual_uniform_button.isChecked())
        if self._probe is not None:
            self._apply_probe(self._probe)
        self._set_connection_visual(False, "Pump not connected.")
        self._suppress_plan_table_layout_save = False
        self._schedule_startup_device_auto_connect()
        self._startup_ui_pending = False
        self._set_switch_solution_mode(self._switch_solution_mode)
        _LOGGER.info("Experiment control bootstrap +%.1f ms: constructor finished", (perf_counter() - self._bootstrap_t0) * 1000.0)

    def _build_ui(self) -> None:
        palette = self._theme_palette()
        editor_header = QLabel("Experiment control")
        editor_header.setObjectName("flowHeaderLabel")
        editor_header.setStyleSheet(
            "QLabel#flowHeaderLabel {"
            f" color: {palette['title']};"
            " font-size: 12px;"
            " font-weight: 700;"
            "}"
        )
        editor_header.setCursor(Qt.CursorShape.PointingHandCursor)
        editor_header.setToolTip("Double-click to switch to processed spectra.")
        self._experiment_control_header_label = editor_header
        editor_hide_button = _make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("eye_off", "eye-off"), QColor("#8a98a8")),
            "Hide experimental control.",
            size=22,
        )
        editor_hide_button.clicked.connect(lambda _checked=False: self._activate_spectra_view())
        editor_header_row = QWidget(self)
        editor_header_row_layout = QHBoxLayout()
        editor_header_row_layout.setContentsMargins(0, 0, 0, 0)
        editor_header_row_layout.setSpacing(6)
        editor_header_row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        editor_header_row_layout.addWidget(editor_header)
        self._experiment_control_view_mode_button = QToolButton(self)
        self._experiment_control_view_mode_button.setObjectName("flowViewModeButton")
        self._experiment_control_view_mode_button.setAutoRaise(True)
        self._experiment_control_view_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._experiment_control_view_mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._experiment_control_view_mode_button.clicked.connect(self._cycle_experiment_control_view_mode)
        self._update_experiment_control_view_mode_button()
        editor_header_row_layout.addWidget(self._experiment_control_view_mode_button)
        editor_header_row_layout.addStretch(1)
        editor_header_row_layout.addWidget(editor_hide_button)
        editor_header_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor_header_row.setLayout(editor_header_row_layout)
        self._experiment_control_header_row = editor_header_row

        editor_layout = QVBoxLayout()
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(4)
        editor_layout.addWidget(editor_header_row)
        matrix = QGridLayout()
        matrix.setHorizontalSpacing(6)
        matrix.setVerticalSpacing(4)
        time_label = QLabel("Duration")
        time_label.setToolTip("Step duration. Displayed in seconds or minutes according to the unit switch, but stored internally in seconds.")
        equal_label = QLabel("CHs")
        equal_label.setToolTip("Shared channel mode. 'CHs' keeps direction and tube identical for all channels. 'not equal' expands per-channel editing.")
        dir_label = QLabel("Dir")
        dir_label.setToolTip("Pump rotation direction. '\u21bb' means clock-wise (CW), '\u21ba' means counter clock-wise (CCW).")
        tube_label = QLabel("Tube")
        tube_label.setToolTip("Tubing inner diameter in mm.")
        flow_header_label = QLabel("Flow")
        flow_header_label.setToolTip("Channel flow rate in uL/min.")
        color_label = QLabel("Color")
        color_label.setToolTip("Timeline color for this step.")
        valve_label = QLabel("Valve")
        valve_label.setToolTip("Valve state associated with this step.")
        switch_label = QLabel("Switch")
        switch_label.setToolTip("AMF switch position or solution label associated with this step.")
        comment_label = QLabel("Comment")
        comment_label.setToolTip("Short step description shown in the timeline.")

        matrix.addWidget(time_label, 0, 0)
        matrix.addWidget(equal_label, 0, 1)
        matrix.addWidget(dir_label, 0, 2)
        matrix.addWidget(tube_label, 0, 3)
        matrix.addWidget(flow_header_label, 0, 4)
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            flow_spin = QDoubleSpinBox()
            make_compact_spinbox(flow_spin)
            flow_spin.setRange(0.0, 100.0)
            flow_spin.setDecimals(0)
            flow_spin.setSingleStep(1.0)
            flow_spin.setMaximumWidth(82)
            flow_spin.setToolTip(f"Flow rate for CH{channel} in uL/min.")

            direction_button = create_direction_button(self, "CW")
            direction_button.setMaximumWidth(40)
            direction_button.setToolTip(
                f"Direction for CH{channel}. '\u21bb' means clock-wise (CW), '\u21ba' means counter clock-wise (CCW)."
            )

            tube_spin = QDoubleSpinBox()
            make_compact_spinbox(tube_spin)
            tube_spin.setRange(0.13, 3.17)
            tube_spin.setDecimals(2)
            tube_spin.setSingleStep(0.01)
            tube_spin.setValue(DEFAULT_TUBE_MM)
            tube_spin.setMaximumWidth(74)
            tube_spin.setToolTip(f"Tubing inner diameter for CH{channel} in mm.")

            self.manual_flow_spins.append(flow_spin)
            self.manual_direction_buttons.append(direction_button)
            self.manual_tube_spins.append(tube_spin)
            tube_spin.valueChanged.connect(lambda _value, self=self: self._sync_experiment_control_tube_columns())
            matrix.addWidget(QLabel(f"CH{channel}"), 0, channel + 4)
            matrix.addWidget(flow_spin, 1, channel + 4)
            matrix.addWidget(direction_button, 2, channel + 4)
            matrix.addWidget(tube_spin, 3, channel + 4)

        self.manual_uniform_button.setToolTip("Shared direction and tube for all channels. Click to expand per-channel settings.")
        self.manual_uniform_button.setText("=")
        self.shared_direction_button.setToolTip("Shared direction for all channels when 'CHs' mode is active. '\u21bb' means CW, '\u21ba' means CCW.")
        self.shared_tube_spin.setToolTip("Shared tubing inner diameter in mm when 'CHs' mode is active.")
        self.plan_detail_toggle.setToolTip("Show or hide the per-channel direction and tube columns in the table.")
        self.manual_flow_label = QLabel("Flow")
        self.manual_dir_label = QLabel("Dir")
        self.manual_tube_label = QLabel("Tube")
        self.step_duration_spin.setToolTip("Step duration. Display value follows the selected time unit, but the plan stores seconds.")
        time_widget = QWidget(self)
        time_layout = QHBoxLayout()
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(3)
        time_layout.addWidget(self.step_duration_spin)
        time_layout.addWidget(self.time_unit_toggle)
        time_widget.setLayout(time_layout)
        matrix.addWidget(time_widget, 1, 0)
        matrix.addWidget(self.manual_uniform_button, 1, 1)
        color_header_widget = QWidget(self)
        color_header_layout = QHBoxLayout()
        color_header_layout.setContentsMargins(0, 0, 0, 0)
        color_header_layout.setSpacing(4)
        color_header_layout.addWidget(color_label)
        color_header_layout.addWidget(self.color_palette_button)
        color_header_layout.addStretch(1)
        color_header_widget.setLayout(color_header_layout)
        color_widget = QWidget(self)
        color_layout = QHBoxLayout()
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(4)
        color_layout.addWidget(self.step_color_combo)
        color_layout.addStretch(1)
        color_widget.setLayout(color_layout)
        valve_header_widget = QWidget(self)
        valve_header_layout = QHBoxLayout()
        valve_header_layout.setContentsMargins(0, 0, 0, 0)
        valve_header_layout.setSpacing(2)
        valve_header_layout.addWidget(valve_label)
        valve_header_layout.addWidget(self.step_valve_settings_button)
        valve_header_layout.addStretch(1)
        valve_header_widget.setLayout(valve_header_layout)
        matrix.addWidget(valve_header_widget, 0, ACTIVE_PUMP_CHANNELS + 5)
        valve_widget = QWidget(self)
        valve_layout = QHBoxLayout()
        valve_layout.setContentsMargins(0, 0, 0, 0)
        valve_layout.setSpacing(2)
        valve_layout.addWidget(self.step_valve_button)
        valve_layout.addStretch(1)
        valve_widget.setLayout(valve_layout)
        matrix.addWidget(valve_widget, 1, ACTIVE_PUMP_CHANNELS + 5)
        switch_header_widget = QWidget(self)
        switch_header_layout = QHBoxLayout()
        switch_header_layout.setContentsMargins(0, 0, 0, 0)
        switch_header_layout.setSpacing(2)
        switch_header_layout.addWidget(switch_label)
        switch_header_layout.addWidget(self.step_switch_mode_button)
        switch_header_layout.addWidget(self.step_switch_settings_button)
        switch_header_layout.addStretch(1)
        switch_header_widget.setLayout(switch_header_layout)
        switch_widget = QWidget(self)
        switch_layout = QHBoxLayout()
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.setSpacing(4)
        switch_layout.addWidget(self.step_switch_spin)
        switch_layout.addWidget(self.step_switch_combo)
        switch_layout.addStretch(1)
        switch_widget.setLayout(switch_layout)
        matrix.addWidget(switch_header_widget, 0, ACTIVE_PUMP_CHANNELS + 6)
        matrix.addWidget(switch_widget, 1, ACTIVE_PUMP_CHANNELS + 6)
        matrix.addWidget(color_header_widget, 0, ACTIVE_PUMP_CHANNELS + 7)
        matrix.addWidget(color_widget, 1, ACTIVE_PUMP_CHANNELS + 7)
        matrix.addWidget(comment_label, 0, ACTIVE_PUMP_CHANNELS + 8)
        matrix.addWidget(self.step_comment_edit, 1, ACTIVE_PUMP_CHANNELS + 8)
        self.step_comment_edit.setMinimumWidth(300)

        self.shared_direction_button.setMaximumWidth(40)
        self.shared_tube_spin.setMaximumWidth(82)
        self.shared_direction_row = QWidget(self)
        shared_direction_layout = QHBoxLayout()
        shared_direction_layout.setContentsMargins(0, 0, 0, 0)
        shared_direction_layout.addWidget(self.shared_direction_button)
        shared_direction_layout.addStretch(1)
        self.shared_direction_row.setLayout(shared_direction_layout)
        self.shared_tube_row = QWidget(self)
        shared_tube_layout = QHBoxLayout()
        shared_tube_layout.setContentsMargins(0, 0, 0, 0)
        shared_tube_layout.addWidget(self.shared_tube_spin)
        shared_tube_layout.addStretch(1)
        self.shared_tube_row.setLayout(shared_tube_layout)
        matrix.addWidget(self.shared_direction_row, 1, 2)
        matrix.addWidget(self.shared_tube_row, 1, 3)
        matrix.addWidget(self.manual_dir_label, 2, 4)
        matrix.addWidget(self.manual_tube_label, 3, 4)
        matrix.setColumnStretch(ACTIVE_PUMP_CHANNELS + 8, 2)
        self._experiment_control_matrix_widget = QWidget(self)
        matrix.setContentsMargins(0, 0, 0, 0)
        self._experiment_control_matrix_widget.setLayout(matrix)
        editor_layout.addWidget(self._experiment_control_matrix_widget)

        editor_action_row = QHBoxLayout()
        editor_action_row.setSpacing(3)
        editor_action_row.addWidget(self.add_step_button)
        editor_action_row.addWidget(self.apply_step_button)
        editor_action_row.addWidget(self.duplicate_step_button)
        editor_action_row.addWidget(self.remove_step_button)
        editor_action_row.addWidget(self.plan_detail_toggle)
        editor_action_row.addWidget(self.pause_state_button)
        editor_action_row.addWidget(self.color_comment_button)
        editor_action_row.addStretch(1)
        editor_action_row.addWidget(self.import_plan_busy_label)
        editor_action_row.addWidget(self.import_plan_button)
        editor_action_row.addWidget(self.export_plan_button)
        self._experiment_control_editor_action_row = QWidget(self)
        editor_action_row.setContentsMargins(0, 0, 0, 0)
        self._experiment_control_editor_action_row.setLayout(editor_action_row)
        editor_layout.addWidget(self._experiment_control_editor_action_row)

        table_container = QWidget(self)
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        self.plan_table.setMinimumWidth(0)
        table_layout.addWidget(self.plan_table, 1)
        table_container.setLayout(table_layout)
        table_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._experiment_control_table_container = table_container

        self._flow_editor_splitter = QSplitter(Qt.Orientation.Vertical)
        self._flow_editor_splitter.setChildrenCollapsible(False)
        self._flow_editor_splitter.setHandleWidth(6)
        self._flow_editor_splitter.setOpaqueResize(True)
        self._flow_editor_splitter.addWidget(table_container)
        self.timeline_widget.step_activated.connect(self._jump_to_experiment_control_step)
        self.timeline_widget.step_double_activated.connect(self._apply_selected_experiment_control_step)
        self.timeline_widget.setMinimumHeight(48)
        self.timeline_widget.setMaximumHeight(max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height()))
        self._flow_editor_splitter.addWidget(self.timeline_widget)
        self._flow_editor_splitter.setStretchFactor(0, 1)
        self._flow_editor_splitter.setStretchFactor(1, 0)
        self._flow_editor_splitter.splitterMoved.connect(self._on_flow_editor_splitter_moved)
        timeline_controls_widget = QWidget(self)
        timeline_controls_layout = QVBoxLayout()
        timeline_controls_layout.setContentsMargins(0, 0, 0, 0)
        timeline_controls_layout.setSpacing(0)
        timeline_controls_widget.setLayout(timeline_controls_layout)
        flow_action_row = QHBoxLayout()
        flow_action_row.setSpacing(4)
        flow_action_row.setContentsMargins(0, 0, 0, 0)
        flow_action_row.addWidget(self.plan_toggle_button)
        flow_action_row.addWidget(self.hold_plan_button)
        flow_action_row.addWidget(self.stop_plan_button)
        flow_action_row.addWidget(self.previous_step_button)
        flow_action_row.addWidget(self.next_step_button)
        flow_action_row.addWidget(self.pause_plan_button)
        flow_action_row.addWidget(self.record_with_flow_button)
        flow_action_row.addStretch(1)
        self._experiment_control_flow_action_row = QWidget(timeline_controls_widget)
        self._experiment_control_flow_action_row.setLayout(flow_action_row)
        self._experiment_control_flow_action_row.setVisible(self._show_runtime_controls)
        timeline_controls_layout.addWidget(self._flow_editor_splitter)
        timeline_controls_layout.addWidget(self._experiment_control_flow_action_row)
        self._experiment_control_timeline_controls_widget = timeline_controls_widget
        editor_layout.addWidget(timeline_controls_widget)
        editor_container = QWidget(self)
        editor_container.setObjectName("flowEditorContainer")
        editor_container.setLayout(editor_layout)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(2)
        content_layout.addWidget(editor_container, 1)

        content = QWidget(self)
        content.setObjectName("flowContent")
        content.setLayout(content_layout)

        scroller = QScrollArea(self)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setStyleSheet(
            """
            QScrollArea {
                background: %(bg)s;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: %(bg)s;
            }
            """ % palette
        )
        scroller.setWidget(content)
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroller)
        self.setLayout(outer_layout)

    def _connect_signals(self) -> None:
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        self.connection_toggle_button.clicked.connect(self._toggle_connection)
        self.plan_toggle_button.clicked.connect(self._toggle_experiment_control_run_hold)
        self.hold_plan_button.clicked.connect(self._toggle_experiment_control_hold)
        self.pause_plan_button.clicked.connect(self._toggle_experiment_control_pause)
        self.stop_plan_button.clicked.connect(self._stop_experiment_control)
        self.previous_step_button.clicked.connect(lambda _checked=False: self._move_to_relative_experiment_control_step(-1))
        self.next_step_button.clicked.connect(lambda _checked=False: self._move_to_relative_experiment_control_step(1))
        self.port_combo.currentTextChanged.connect(self._remember_selected_port)
        self.pump_info_button.clicked.connect(self._show_pump_info)
        self.valve_refresh_ports_button.clicked.connect(self._refresh_valve_ports)
        self.valve_connection_toggle_button.clicked.connect(self._toggle_valve_connection)
        self.valve_port_combo.currentTextChanged.connect(self._remember_selected_valve_port)
        self.mswitch_refresh_ports_button.clicked.connect(self._refresh_mswitch_ports)
        self.mswitch_connection_toggle_button.clicked.connect(self._toggle_mswitch_connection)
        self.mswitch_home_button.clicked.connect(self._home_mswitch)
        self.mswitch_move_button.clicked.connect(self._move_mswitch_to_target)
        self.mswitch_port_combo.currentTextChanged.connect(self._remember_selected_mswitch_port)
        self.manual_uniform_button.toggled.connect(self._set_manual_uniform_mode)
        self.shared_direction_button.clicked.connect(
            lambda: self._toggle_direction_button(self.shared_direction_button, self._apply_shared_manual_settings)
        )
        self.shared_tube_spin.valueChanged.connect(self._apply_shared_manual_settings)
        self.plan_detail_toggle.toggled.connect(self._set_experiment_control_details_visible)
        self.time_unit_toggle.clicked.connect(self._cycle_time_unit_mode)
        self.step_duration_spin.valueChanged.connect(self._capture_editor_duration_from_spin)
        self.shared_tube_spin.valueChanged.connect(self._sync_experiment_control_tube_columns)
        self.color_palette_button.clicked.connect(lambda _checked=False, btn=self.color_palette_button: self._edit_color_palette_entries(btn))
        self.remove_custom_color_button.clicked.connect(self._remove_selected_custom_color)
        self.step_valve_settings_button.clicked.connect(lambda _checked=False, btn=self.step_valve_settings_button: self._edit_valve_state_labels(btn))
        self.step_switch_spin.valueChanged.connect(self._handle_step_switch_spin_changed)
        self.step_switch_settings_button.clicked.connect(lambda _checked=False, btn=self.step_switch_settings_button: self._edit_switch_solution_labels(btn))
        self.pause_state_button.clicked.connect(lambda _checked=False, btn=self.pause_state_button: self._edit_pause_state(btn))
        self.step_color_combo.currentIndexChanged.connect(
            lambda *_args: self._handle_color_selection_changed()
        )

        self.add_step_button.clicked.connect(self._add_experiment_control_step_from_editor)
        self.duplicate_step_button.clicked.connect(self._experiment_control_edit_controller.duplicate_selected_rows)
        self.remove_step_button.clicked.connect(self._experiment_control_edit_controller.remove_selected_rows)
        self.apply_step_button.toggled.connect(self._experiment_control_edit_controller.toggle_edit_mode)
        self.color_comment_button.clicked.connect(self._copy_color_names_to_comments)
        self.import_plan_button.clicked.connect(self._import_experiment_control_plan_from_file)
        self.export_plan_button.clicked.connect(self._export_experiment_control_plan_placeholder)
        self.plan_table.step_move_requested.connect(self._experiment_control_edit_controller.move_selected_rows)
        self.plan_table.copy_requested.connect(self._experiment_control_edit_controller.copy_selection)
        self.plan_table.paste_requested.connect(self._experiment_control_edit_controller.paste_selection)
        self.plan_table.verticalScrollBar().valueChanged.connect(self._experiment_control_edit_controller.sync_overlay)
        self.plan_table.horizontalScrollBar().valueChanged.connect(self._keep_plan_table_left_aligned)
        self.timeline_widget.step_reordered.connect(self._move_experiment_control_step_to_row)
        self.plan_table.horizontalHeader().sectionResized.connect(self._schedule_plan_table_layout_save)
        self._plan_model.dataChanged.connect(self._handle_experiment_control_model_changed)
        self._plan_model.modelReset.connect(self._handle_experiment_control_model_changed)
        selection_model = self.plan_table.selectionModel()
        if selection_model is not None:
            selection_model.currentChanged.connect(self._handle_experiment_control_current_index_changed)

    def _make_icon_button(self, icon: QIcon, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("flowIconButton")
        button.setAutoRaise(True)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setFixedSize(32, 32)
        button.setIconSize(QSize(24, 24))
        return button

    def _pause_state_button_icon(self) -> QIcon:
        accent = QColor("#8a98a8")
        for icon_name in ("settings_pause", "clock_pause", "pause"):
            try:
                return tint_tabler_icon(flow_tabler_icon(icon_name), accent)
            except Exception:
                continue
        return transport_icon(self._theme_mode, "pause")

    def _hold_plan_button_icon(self, *, active: bool) -> QIcon:
        if active:
            alpha_steps = [255, 255, 220, 160, 90, 40, 90, 160]
            frame_index = int(getattr(self, "_plan_hold_blink_frame", 0)) % len(alpha_steps)
            color = QColor("#66a7ff")
            color.setAlpha(alpha_steps[frame_index])
        elif self._plan_running:
            color = QColor("#66a7ff")
        else:
            color = QColor("#8a98a8")
        try:
            return tint_tabler_icon(flow_tabler_icon("clock_stop"), color)
        except Exception:
            return transport_icon(self._theme_mode, "hold")

    def _runtime_pause_button_icon(self, *, active: bool = False) -> QIcon:
        if active:
            alpha_steps = [255, 255, 220, 160, 90, 40, 90, 160]
            frame_index = int(getattr(self, "_plan_hold_blink_frame", 0)) % len(alpha_steps)
            color = QColor("#ffbf3f")
            color.setAlpha(alpha_steps[frame_index])
        else:
            color = QColor("#ffbf3f")
        return tint_tabler_icon(flow_tabler_icon("player_pause", "pause"), color)

    def _switch_solution_label(self, position: int) -> str:
        index = max(min(int(position), 12), 1) - 1
        if 0 <= index < len(self._switch_solution_labels):
            label = str(self._switch_solution_labels[index]).strip()
            if label:
                return label
        return "empty"

    def _switch_display_text(self, position: int) -> str:
        normalized = max(min(int(position), 12), 1)
        return f"{normalized}: {self._switch_solution_label(normalized)}"

    def _populate_switch_solution_combo(
        self,
        combo: QComboBox,
        selected_position: int | None = None,
        *,
        show_labels: bool | None = None,
    ) -> None:
        current_position = max(min(int(selected_position or 1), 12), 1)
        combo.blockSignals(True)
        combo.clear()
        for position in range(1, 13):
            item_text = self._switch_display_text(position)
            combo.addItem(item_text, position)
            combo.setItemData(position - 1, int(Qt.AlignmentFlag.AlignCenter), Qt.ItemDataRole.TextAlignmentRole)
        combo.setCurrentIndex(current_position - 1)
        combo.view().setMinimumWidth(max(self._switch_solution_popup_width(combo), int(combo.width())))
        self._style_combo_popup_view(combo, center_items=True, rounded=False, selection_frame=True)
        combo.blockSignals(False)

    def _refresh_switch_solution_combo(
        self,
        selected_position: int | None = None,
    ) -> None:
        if not hasattr(self, "step_switch_combo"):
            return
        current_position = max(min(int(selected_position or self.step_switch_spin.value()), 12), 1)
        self._populate_switch_solution_combo(
            self.step_switch_combo,
            current_position,
        )
        self.step_switch_combo.view().setMinimumWidth(
            max(self._switch_solution_popup_width(self.step_switch_combo), int(self.step_switch_combo.width()))
        )
        self._style_combo_popup_view(self.step_switch_combo, center_items=True, rounded=False, selection_frame=True)

    def _switch_solution_popup_width(self, combo: QComboBox) -> int:
        metrics = combo.fontMetrics()
        widest = 0
        for index in range(combo.count()):
            widest = max(widest, metrics.horizontalAdvance(combo.itemText(index)))
        return max(140, widest + 40)

    def _color_combo_popup_width(self, combo: QComboBox) -> int:
        metrics = combo.fontMetrics()
        widest = 0
        for index in range(combo.count()):
            widest = max(widest, metrics.horizontalAdvance(combo.itemText(index)))
        cell_width = max(int(combo.width()), 0)
        return max(cell_width, widest + 48)

    def _style_combo_popup_view(
        self,
        combo: QComboBox,
        *,
        center_items: bool = False,
        rounded: bool = True,
        selection_frame: bool = False,
    ) -> None:
        view = combo.view()
        if view is None:
            return
        view.setObjectName("flowComboPopup")
        palette = self._theme_palette()
        align_rule = "text-align: center;" if center_items else ""
        radius_rule = " border-radius: 10px;" if rounded else " border-radius: 0px;"
        item_radius_rule = " border-radius: 8px;" if rounded else " border-radius: 0px;"
        selected_rule = (
            "QListView#flowComboPopup::item:selected {"
            f" background: transparent;"
            f" color: {palette['fg']};"
            f" border: 1px solid {palette['selection']};"
            "}"
            if selection_frame
            else
            "QListView#flowComboPopup::item:selected {"
            f" background: {palette['selection']};"
            f" color: {palette['fg']};"
            "}"
        )
        view.setStyleSheet(
            "QListView#flowComboPopup {"
            f" background: {palette['field']};"
            f" color: {palette['fg']};"
            f" border: 1px solid {palette['border']};"
            f"{radius_rule}"
            " padding: 2px;"
            " outline: none;"
            "}"
            "QListView#flowComboPopup::item {"
            " min-height: 20px;"
            " padding: 2px 8px;"
            f"{item_radius_rule}"
            f" {align_rule}"
            "}"
            f"{selected_rule}"
        )

    def _set_switch_solution_mode(self, enabled: bool) -> None:
        _ = enabled
        self._switch_solution_mode = False
        self.step_switch_mode_button.setVisible(False)
        self.step_switch_spin.setVisible(False)
        self.step_switch_combo.setVisible(not getattr(self, "_startup_ui_pending", False))
        self._refresh_switch_solution_controls()
        self._update_timeline_selection()

    def _refresh_switch_solution_controls(self) -> None:
        current_position = self._current_switch_position_from_editor()
        self._refresh_switch_solution_combo(current_position)
        self._plan_model.set_switch_solution_labels(self._switch_solution_labels)
        self._plan_model.set_theme_palette(self._theme_palette())
        self._plan_model.set_valve_state_colors(self._valve_state_colors)
        self.plan_table.viewport().update()
        self._fit_plan_table_columns_to_viewport()
        self._update_timeline_selection()

    def _handle_step_switch_spin_changed(self, value: int) -> None:
        if self._updating_switch_editor:
            return
        self._updating_switch_editor = True
        try:
            self.step_switch_combo.setCurrentIndex(max(min(int(value), 12), 1) - 1)
        finally:
            self._updating_switch_editor = False

    def _handle_step_switch_combo_changed(self, index: int) -> None:
        if self._updating_switch_editor:
            return
        if index < 0:
            return
        self._updating_switch_editor = True
        try:
            self.step_switch_spin.setValue(index + 1)
        finally:
            self._updating_switch_editor = False

    def _current_switch_position_from_editor(self) -> int:
        if self.step_switch_combo.currentIndex() >= 0:
            data = self.step_switch_combo.currentData()
            if isinstance(data, (int, float)):
                return max(min(int(data), 12), 1)
            return max(min(self.step_switch_combo.currentIndex() + 1, 12), 1)
        return max(min(int(self.step_switch_spin.value()), 12), 1)

    def _switch_position_from_text(self, text: str) -> int:
        cleaned = str(text or "").strip()
        if not cleaned:
            return 1
        head = cleaned.split(":", 1)[0].strip()
        try:
            return max(min(int(float(head)), 12), 1)
        except ValueError:
            pass
        for position in range(1, 13):
            if cleaned.casefold() == self._switch_solution_label(position).casefold():
                return position
        return 1

    def _experiment_plan_import_default_dir(self) -> Path:
        stored = load_app_setting("experiment_plan_import_dir", "")
        if isinstance(stored, str) and stored:
            stored_path = Path(stored)
            if stored_path.exists():
                return stored_path
        examples_dir = Path.cwd().parent / "LSPR_examples" / "pumplans"
        if examples_dir.exists():
            return examples_dir
        return Path.cwd()

    def _set_experiment_plan_import_running(self, running: bool) -> None:
        self._experiment_plan_import_in_progress = running
        self.import_plan_button.setEnabled(not running)
        self.export_plan_button.setEnabled(not running)
        if not running:
            self._experiment_plan_import_fill_timer.stop()
        self._update_experiment_control_busy_indicator()

    def _set_experiment_control_bootstrap_busy(self, running: bool) -> None:
        self._experiment_control_bootstrap_in_progress = running
        if not running:
            self._experiment_plan_import_fill_timer.stop()
        self._update_experiment_control_busy_indicator()

    def _update_experiment_control_busy_indicator(self) -> None:
        busy = self._experiment_plan_import_in_progress or self._experiment_control_bootstrap_in_progress or self._port_refresh_in_progress
        self.import_plan_busy_label.setVisible(busy)
        if busy:
            self._import_plan_busy_frame_index = 0
            self._render_import_plan_busy_indicator()
            if not self._import_plan_busy_timer.isActive():
                self._import_plan_busy_timer.start()
        else:
            self._import_plan_busy_timer.stop()
            self.import_plan_busy_label.setVisible(False)

    def _set_experiment_plan_export_running(self, running: bool) -> None:
        self._experiment_plan_export_in_progress = running
        self.import_plan_button.setEnabled(not running)
        self.export_plan_button.setEnabled(not running)

    def _normalize_experiment_plan_header(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())

    def _run_gui_callback_timed(self, label: str, callback, *, warn_ms: float = 20.0) -> None:
        started = perf_counter()
        try:
            callback()
        finally:
            elapsed_ms = (perf_counter() - started) * 1000.0
            if elapsed_ms < warn_ms:
                return
            controller = getattr(self, "recording_controller", None)
            source_mode = str(getattr(controller, "_source_mode", "") or "").strip().lower()
            if source_mode == "simulation":
                if bool(getattr(self, "_debug_timing_enabled", False)) or bool(getattr(self, "_deep_timing_enabled", False)):
                    _LOGGER.debug("Experiment control GUI callback %s took %.2f ms", label, elapsed_ms)
                return
            _LOGGER.warning("Experiment control GUI callback %s took %.2f ms", label, elapsed_ms)

    def _advance_import_plan_busy_indicator(self) -> None:
        def _callback() -> None:
            if not self.import_plan_busy_label.isVisible():
                return
            self._import_plan_busy_frame_index = (self._import_plan_busy_frame_index + 1) % len(self._import_plan_busy_frames)
            self._render_import_plan_busy_indicator()

        self._run_gui_callback_timed("import_plan_busy", _callback)

    def _render_import_plan_busy_indicator(self) -> None:
        label = self.import_plan_busy_label
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            center = size / 2.0
            ring_radius = 5.5
            dot_radius = 1.4
            base_color = QColor("#39c7ba")
            trail_alphas = [255, 220, 185, 150]
            for index, alpha in enumerate(trail_alphas):
                frame = (self._import_plan_busy_frame_index + index) % 12
                angle = frame * 30.0
                radians = angle * math.pi / 180.0
                x = center + ring_radius * math.cos(radians)
                y = center + ring_radius * math.sin(radians)
                color = QColor(base_color)
                color.setAlpha(alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(
                    int(round(x - dot_radius)),
                    int(round(y - dot_radius)),
                    int(round(dot_radius * 2.0)),
                    int(round(dot_radius * 2.0)),
                )
        finally:
            painter.end()
        label.setPixmap(pixmap)

    def _detect_experiment_plan_delimiter(self, header_line: str) -> str:
        candidates = [";", ",", "\t", "|"]
        counts = {candidate: header_line.count(candidate) for candidate in candidates}
        delimiter = max(counts, key=counts.get)
        return delimiter if counts[delimiter] > 0 else ";"

    def _experiment_plan_column_map(self, headers: list[str]) -> dict[object, int]:
        mapping: dict[object, int] = {}
        for index, header in enumerate(headers):
            normalized = self._normalize_experiment_plan_header(header)
            if not normalized:
                continue
            if normalized.startswith("step"):
                mapping["step"] = index
                continue
            if normalized.startswith("time"):
                mapping["time"] = index
                continue
            if normalized.startswith("valve"):
                mapping["valve"] = index
                continue
            if normalized.startswith("color"):
                mapping["color"] = index
                continue
            if "solution" in normalized:
                mapping["solution"] = index
                continue
            if "comment" in normalized or "description" in normalized or "descrit" in normalized:
                mapping["description"] = index
                continue
            match = re.match(r"ch(\d+)", normalized)
            if not match:
                continue
            channel = int(match.group(1))
            if "flow" in normalized:
                mapping[("flow", channel)] = index
            elif "direction" in normalized or normalized.endswith("dir"):
                mapping[("direction", channel)] = index
            elif "tube" in normalized:
                mapping[("tube", channel)] = index
        return mapping

    def _experiment_plan_cell(self, row: list[str], index: int | None, default: str = "") -> str:
        if index is None or index < 0 or index >= len(row):
            return default
        return str(row[index]).strip()

    def _experiment_plan_uses_lr_valves(self, rows: list[list[str]], column_map: dict[object, int]) -> bool:
        valve_index = column_map.get("valve")
        if valve_index is None:
            return False
        for row in rows:
            valve = self._experiment_plan_cell(row, valve_index)
            if valve.casefold() in {"l", "r", "left", "right"}:
                return True
        return False

    def _prompt_experiment_plan_l_is_open(self) -> bool:
        saved = load_app_setting("experiment_plan_import_l_is_open", None)
        if isinstance(saved, bool):
            return saved
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Import experiment plan")
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setText("The imported file uses L / R valve labels.\nShould L mean Open?")
        prompt.setInformativeText("This choice will be remembered for future imports.")
        left_open_button = prompt.addButton("L = Open", QMessageBox.ButtonRole.YesRole)
        prompt.addButton("L = Close", QMessageBox.ButtonRole.NoRole)
        prompt.setDefaultButton(left_open_button)
        prompt.exec()
        choice = prompt.clickedButton() is left_open_button
        save_app_setting("experiment_plan_import_l_is_open", choice)
        return choice

    def _normalize_experiment_plan_valve(self, raw_valve: str, l_is_open: bool) -> str:
        text = str(raw_valve or "").strip()
        if not text:
            return "Open"
        lowered = text.casefold()
        if lowered in {"open", "close"}:
            return "Close" if lowered == "close" else "Open"
        for internal_state, label in self._valve_state_labels.items():
            if lowered == str(label).strip().casefold():
                return "Close" if str(internal_state).strip().casefold() == "close" else "Open"
        if lowered in {"l", "left"}:
            return "Open" if l_is_open else "Close"
        if lowered in {"r", "right"}:
            return "Close" if l_is_open else "Open"
        return "Close" if lowered.startswith("close") else "Open"

    def _build_experiment_plan_steps_from_import_data(
        self,
        data: ExperimentPlanImportData,
        *,
        l_is_open: bool,
    ) -> list[PumpPlanStep]:
        steps: list[PumpPlanStep] = []
        for row_index, row in enumerate(data.rows, start=1):
            if not any(cell.strip() for cell in row):
                continue

            channels: list[PumpChannelStep] = []
            for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
                flow_text = self._experiment_plan_cell(row, data.column_map.get(("flow", channel_index)), "0")
                direction_text = self._experiment_plan_cell(row, data.column_map.get(("direction", channel_index)), "CW")
                flow_ml_min = max(_safe_float(flow_text), 0.0)
                direction = "CCW" if direction_text.casefold() == "ccw" else "CW"
                channels.append(
                    PumpChannelStep(
                        flow_ul_min=max(round(flow_ml_min * 1000.0), 0),
                        direction=direction,
                    )
                )

            valve = self._normalize_experiment_plan_valve(
                self._experiment_plan_cell(row, data.column_map.get("valve"), "Open"),
                l_is_open,
            )
            raw_color = self._experiment_plan_cell(row, data.column_map.get("color"), "").strip().upper()
            qcolor = QColor(raw_color)
            color = qcolor.name().upper() if qcolor.isValid() else self._default_experiment_control_color(row_index - 1)
            description = self._experiment_plan_cell(row, data.column_map.get("description"), "").strip()
            switch_text = self._experiment_plan_cell(row, data.column_map.get("solution"), "")
            switch_position = self._switch_position_from_text(switch_text) if switch_text else 1
            duration_s = max(_safe_float(self._experiment_plan_cell(row, data.column_map.get("time"), "0")), 0.0)

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

    def _experiment_plan_native_flow_factor(self, document: dict[str, object]) -> float:
        units = document.get("units", {})
        if not isinstance(units, dict):
            return 1.0
        flow_unit = str(units.get("flow", "uL/min") or "uL/min").strip().casefold()
        if flow_unit in {"ml/min", "ml min-1", "ml_per_min"}:
            return 1000.0
        return 1.0

    def _build_experiment_plan_steps_from_native_document(self, document: dict[str, object]) -> list[PumpPlanStep]:
        raw_steps = document.get("steps", [])
        if not isinstance(raw_steps, list):
            return []
        flow_factor = self._experiment_plan_native_flow_factor(document)
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
            switch_position = self._switch_position_from_text(str(switch_payload.get("port", 1) or 1))

            qcolor = QColor(str(raw_step.get("color", "") or "").strip())
            color = qcolor.name().upper() if qcolor.isValid() else self._default_experiment_control_color(row_index - 1)
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

    def _native_experiment_plan_unsupported_devices(self, document: dict[str, object]) -> list[str]:
        supported = {"pump_1", "valve_1", "switch_1"}
        found: set[str] = set()
        devices = document.get("devices", {})
        if isinstance(devices, dict):
            for group_name in ("pumps", "valves", "switches"):
                group = devices.get(group_name, {})
                if isinstance(group, dict):
                    found.update(str(key) for key in group)
        raw_steps = document.get("steps", [])
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict):
                    continue
                step_devices = raw_step.get("devices", {})
                if isinstance(step_devices, dict):
                    found.update(str(key) for key in step_devices)
        return sorted(device for device in found if device not in supported)

    def _apply_native_experiment_plan_device_labels(self, document: dict[str, object]) -> None:
        devices = document.get("devices", {})
        if not isinstance(devices, dict):
            return

        switches = devices.get("switches", {})
        if isinstance(switches, dict):
            switch_1 = switches.get("switch_1", {})
            if isinstance(switch_1, dict):
                ports = switch_1.get("ports", {})
                if isinstance(ports, dict):
                    labels = list(self._switch_solution_labels)
                    while len(labels) < 12:
                        labels.append("empty")
                    for raw_port, raw_label in ports.items():
                        try:
                            port = int(raw_port)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= port <= 12:
                            labels[port - 1] = str(raw_label or "empty").strip() or "empty"
                    self._switch_solution_labels = labels[:12]
                    self._refresh_switch_solution_controls()

        valves = devices.get("valves", {})
        if isinstance(valves, dict):
            valve_1 = valves.get("valve_1", {})
            if isinstance(valve_1, dict):
                display_labels = valve_1.get("display_labels", {})
                if isinstance(display_labels, dict):
                    open_label = str(display_labels.get("open", "Open") or "Open").strip() or "Open"
                    close_label = str(display_labels.get("close", "Close") or "Close").strip() or "Close"
                    self._valve_state_labels = {"Open": open_label, "Close": close_label}
                    set_step_valve_button_state_for_button(
                        self,
                        self.step_valve_button,
                        str(self.step_valve_button.property("valve") or "Open"),
                    )

    def _experiment_plan_export_default_dir(self) -> Path:
        stored = load_app_setting("experiment_plan_export_dir", "")
        if isinstance(stored, str) and stored:
            stored_path = Path(stored)
            if stored_path.exists():
                return stored_path
        import_dir = self._experiment_plan_import_default_dir()
        if import_dir.exists():
            return import_dir
        return Path.cwd()

    def _experiment_plan_export_l_is_open(self) -> bool:
        saved = load_app_setting("experiment_plan_import_l_is_open", True)
        return bool(saved) if isinstance(saved, bool) else True

    def _experiment_plan_export_valve_text(self, valve: str) -> str:
        normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
        l_is_open = self._experiment_plan_export_l_is_open()
        if normalized == "Open":
            return "L" if l_is_open else "R"
        return "R" if l_is_open else "L"

    def _build_native_experiment_plan_document(self) -> dict[str, object]:
        steps = recompute_plan_timing(self._read_experiment_control_steps())
        tube_mm_by_channel = self._tube_mm_values()
        pump_label = self.model_value.text().strip()
        if not pump_label or pump_label == "-":
            pump_label = "Pump 1"
        return {
            "format": {
                "name": "LSPR Experiment Plan",
                "version": 1,
            },
            "metadata": {
                "created_by": "LSPR Acquisition",
                "app_version": __version__,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "notes": "",
            },
            "units": {
                "flow": "uL/min",
                "time": "s",
                "tube_diameter": "mm",
            },
            "devices": {
                "pumps": {
                    "pump_1": {
                        "label": pump_label,
                        "channels": {
                            f"ch{channel_index}": {
                                "label": f"CH{channel_index}",
                                "tube_mm": float(tube_mm_by_channel[channel_index - 1]),
                            }
                            for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1)
                        },
                    }
                },
                "valves": {
                    "valve_1": {
                        "labels": {
                            "open": self._experiment_plan_export_valve_text("Open"),
                            "close": self._experiment_plan_export_valve_text("Close"),
                        },
                        "display_labels": {
                            "open": self._valve_state_label("Open"),
                            "close": self._valve_state_label("Close"),
                        },
                    }
                },
                "switches": {
                    "switch_1": {
                        "ports": {
                            position: self._switch_solution_label(position)
                            for position in range(1, 13)
                        }
                    }
                },
            },
            "steps": [
                {
                    "id": step.step,
                    "duration_s": float(step.duration_s),
                    "color": str(step.color or self._default_experiment_control_color(step.step - 1)),
                    "comment": str(step.description or ""),
                    "devices": {
                        "pump_1": {
                            f"ch{channel_index + 1}": {
                                "flow": float(step.channels[channel_index].flow_ul_min),
                                "direction": str(step.channels[channel_index].direction or "OFF"),
                            }
                            for channel_index in range(ACTIVE_PUMP_CHANNELS)
                        },
                        "valve_1": {
                            "state": "close" if str(step.valve or "").strip().lower() == "close" else "open",
                        },
                        "switch_1": {
                            "port": int(max(min(int(step.switch_position), 12), 1)),
                        },
                    },
                }
                for step in steps
            ],
        }

    def _build_experiment_plan_export_payload(self, path: Path) -> ExperimentPlanExportData:
        if path.suffix.casefold() in {".yaml", ".yml"}:
            return ExperimentPlanExportData(path=path, document=self._build_native_experiment_plan_document())

        steps = recompute_plan_timing(self._read_experiment_control_steps())
        header = [
            "Step",
            "Ch-1 Flow [ml/min]",
            "Ch-1 Direction",
            "Ch-1 Tubesize [mm]",
            "Ch-2 Flow [ml/min]",
            "Ch-2 Direction",
            "Ch-2 Tubesize [mm]",
            "Ch-3 Flow [ml/min]",
            "Ch-3 Direction",
            "Ch-3 Tubesize [mm]",
            "Ch-4 Flow [ml/min]",
            "Ch-4 Direction",
            "Ch-4 Tubesize [mm]",
            "Ch-5 Flow [ml/min]",
            "Ch-5 Direction",
            "Ch-5 Tubesize [mm]",
            "Ch-6 Flow [ml/min]",
            "Ch-6 Direction",
            "Ch-6 Tubesize [mm]",
            "Time",
            "Valve",
            "Color",
            "Descritption",
            "",
            "Solution",
            "volume:?L",
        ]
        rows: list[list[str]] = []
        tube_mm_by_channel = self._tube_mm_values()
        for step in steps:
            row = [str(step.step)]
            for channel_index in range(HDF5_PUMP_CHANNELS):
                if channel_index < ACTIVE_PUMP_CHANNELS:
                    channel = step.channels[channel_index]
                    row.extend(
                        [
                            f"{max(float(channel.flow_ul_min), 0.0) / 1000.0:g}",
                            str(channel.direction or "OFF"),
                            f"{float(tube_mm_by_channel[channel_index]):.2f}",
                        ]
                    )
                else:
                    row.extend(["", "", ""])
            row.extend(
                [
                    f"{max(float(step.duration_s), 0.0):g}",
                    self._experiment_plan_export_valve_text(step.valve),
                    str(step.color or self._default_experiment_control_color(step.step - 1)),
                    str(step.description or ""),
                    "",
                    self._switch_solution_label(step.switch_position) if self._switch_solution_label(step.switch_position) != "empty" else "",
                    "",
                ]
            )
            rows.append(row)
        return ExperimentPlanExportData(path=path, header=header, rows=rows)

    def _start_experiment_plan_export(self, path: Path) -> None:
        if self._experiment_plan_export_in_progress:
            return
        if self._experiment_plan_import_in_progress:
            return
        try:
            payload = self._build_experiment_plan_export_payload(path)
            self._experiment_plan_export_generation += 1
            generation = self._experiment_plan_export_generation
            task = ExperimentPlanExportTask(generation, payload)
            self._experiment_plan_export_task = task
            self._set_experiment_plan_export_running(True)
            self._set_status_message(f"Exporting experiment plan to {path.name}...")
            task.signals.finished.connect(self._handle_experiment_plan_export_finished)
            task.signals.failed.connect(self._handle_experiment_plan_export_failed)
            self._thread_pool.start(task)
        except Exception as exc:
            self._experiment_plan_export_task = None
            self._set_experiment_plan_export_running(False)
            QMessageBox.warning(self, "Export experiment plan", f"Could not export experiment plan:\n{exc}")

    def _handle_experiment_plan_export_finished(self, generation: int, payload: object) -> None:
        if generation != self._experiment_plan_export_generation:
            return
        self._experiment_plan_export_task = None
        self._set_experiment_plan_export_running(False)
        if not isinstance(payload, ExperimentPlanExportData):
            self._show_error("Exported experiment plan data had an unexpected format.")
            return
        save_app_setting("experiment_plan_export_dir", str(payload.path.parent))
        self._set_status_message(f"Exported experiment plan to {payload.path.name}.")

    def _handle_experiment_plan_export_failed(self, generation: int, message: str) -> None:
        if generation != self._experiment_plan_export_generation:
            return
        self._experiment_plan_export_task = None
        self._set_experiment_plan_export_running(False)
        self._show_error(f"Could not export experiment plan:\n{message}")

    def _merge_imported_experiment_plan_colors(self, colors: list[str]) -> bool:
        if not colors:
            return False
        existing = {color for _name, color in self._color_palette_entries}
        changed = False
        for color in colors:
            qcolor = QColor(str(color).strip())
            if not qcolor.isValid():
                continue
            normalized = qcolor.name().upper()
            if normalized in existing:
                continue
            self._color_palette_entries.append((normalized, normalized))
            existing.add(normalized)
            changed = True
        return changed

    def _prompt_hdf5_experiment_plan_import_options(self, path: Path) -> tuple[bool, bool] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Import HDF5 experiment plan")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QLabel(f"Import data from:\n{path.name}")
        label.setWordWrap(True)
        layout.addWidget(label)

        plan_checkbox = QCheckBox("Import authored plan (required)", dialog)
        plan_checkbox.setChecked(True)
        plan_checkbox.setEnabled(False)
        runtime_checkbox = QCheckBox("Import runtime state", dialog)
        runtime_checkbox.setChecked(True)
        layout.addWidget(plan_checkbox)
        layout.addWidget(runtime_checkbox)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return bool(plan_checkbox.isChecked()), bool(runtime_checkbox.isChecked())

    def _start_experiment_plan_import(self, path: Path) -> None:
        if self._experiment_plan_import_in_progress:
            return
        try:
            self._experiment_plan_import_fill_timer.stop()
            self._experiment_plan_import_pending_steps = []
            self._experiment_plan_import_pending_payload = None
            self._experiment_plan_import_pending_selected_row = None
            self._experiment_plan_import_pending_step_index = 0
            self._experiment_plan_import_generation += 1
            generation = self._experiment_plan_import_generation
            import_hdf5_plan = True
            import_hdf5_runtime = True
            if path.suffix.casefold() in {".h5", ".hdf5"}:
                options = self._prompt_hdf5_experiment_plan_import_options(path)
                if options is None:
                    return
                import_hdf5_plan, import_hdf5_runtime = options
            task = ExperimentPlanImportTask(
                generation,
                path,
                import_hdf5_plan=import_hdf5_plan,
                import_hdf5_runtime=import_hdf5_runtime,
            )
            self._experiment_plan_import_task = task
            self._set_experiment_plan_import_running(True)
            self._set_status_message(f"Importing experiment plan from {path.name}...")
            task.signals.finished.connect(self._handle_experiment_plan_import_finished)
            task.signals.failed.connect(self._handle_experiment_plan_import_failed)
            self._thread_pool.start(task)
        except Exception as exc:
            self._experiment_plan_import_task = None
            self._set_experiment_plan_import_running(False)
            QMessageBox.warning(self, "Import experiment plan", f"Could not import experiment plan:\n{exc}")

    def _handle_experiment_plan_import_finished(self, generation: int, payload: object) -> None:
        if generation != self._experiment_plan_import_generation:
            return
        self._experiment_plan_import_task = None
        if not isinstance(payload, ExperimentPlanImportData):
            self._set_experiment_plan_import_running(False)
            self._show_error("Imported experiment plan data had an unexpected format.")
            return
        if payload.switch_solution_rows:
            labels = ["empty" for _ in range(12)]
            for row in payload.switch_solution_rows:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                try:
                    port = int(float(str(row[0]).strip()))
                except (TypeError, ValueError):
                    continue
                if 1 <= port <= 12:
                    labels[port - 1] = str(row[1] or "empty").strip() or "empty"
            self._switch_solution_labels = labels
            self._refresh_switch_solution_controls()
        if payload.switch_solution_mode is not None:
            self.step_switch_mode_button.blockSignals(True)
            try:
                self.step_switch_mode_button.setChecked(bool(payload.switch_solution_mode))
            finally:
                self.step_switch_mode_button.blockSignals(False)
            self._set_switch_solution_mode(bool(payload.switch_solution_mode))
        if payload.valve_state_labels is not None:
            self._valve_state_labels = self._load_valve_state_labels({"valve_state_labels": payload.valve_state_labels})
            set_step_valve_button_state_for_button(
                self,
                self.step_valve_button,
                str(self.step_valve_button.property("valve") or "Open"),
            )
        if payload.valve_state_colors is not None:
            self._valve_state_colors = self._load_valve_state_colors({"valve_state_colors": payload.valve_state_colors})
        if payload.color_palette_entries is not None:
            payload_entries: list[tuple[str, str]] = []
            for index, entry in enumerate(payload.color_palette_entries):
                name = str(entry.get("name", "")).strip()
                color = str(entry.get("color", "")).strip().upper()
                normalized = self._normalize_color_entry(name, color, index)
                if normalized is not None:
                    payload_entries.append(normalized)
            if payload_entries:
                self._color_palette_entries = payload_entries
                self._sync_custom_plan_colors_from_palette()
                self._refresh_color_palette_widgets()
                self._update_experiment_control_timeline_label_mode()
        if payload.native_document is not None:
            unsupported_devices = self._native_experiment_plan_unsupported_devices(payload.native_document)
            if unsupported_devices:
                QMessageBox.information(
                    self,
                    "Import experiment control plan",
                    "This experiment control plan contains devices that are not supported by this app version and will be skipped:\n"
                    + ", ".join(unsupported_devices),
                )
            self._apply_native_experiment_plan_device_labels(payload.native_document)
        else:
            pass
        steps = list(payload.steps or [])
        if payload.native_document is None and payload.uses_lr_valves:
            l_is_open = self._prompt_experiment_plan_l_is_open()
            steps = build_experiment_plan_steps_from_import_data(payload, l_is_open=bool(l_is_open))
        if not steps:
            self._set_experiment_plan_import_running(False)
            QMessageBox.warning(self, "Import experiment plan", "The selected file did not contain any flow steps.")
            return
        palette_changed = self._merge_imported_experiment_plan_colors(payload.imported_colors)
        if palette_changed:
            self._save_color_palette_entries()
        for index, tube_mm in enumerate(payload.tube_mm_by_channel[:ACTIVE_PUMP_CHANNELS]):
            self.manual_tube_spins[index].blockSignals(True)
            self.manual_tube_spins[index].setValue(float(tube_mm))
            self.manual_tube_spins[index].blockSignals(False)
        self.save_ui_state()
        self._begin_experiment_plan_import_population(payload, steps)

    def _handle_experiment_plan_import_failed(self, generation: int, message: str) -> None:
        if generation != self._experiment_plan_import_generation:
            return
        self._experiment_plan_import_task = None
        self._set_experiment_plan_import_running(False)
        self._show_error(f"Could not import experiment plan:\n{message}")

    def _begin_experiment_plan_import_population(self, payload: ExperimentPlanImportData, steps: list[PumpPlanStep]) -> None:
        self._experiment_plan_import_pending_payload = payload
        self._experiment_plan_import_pending_steps = list(steps)
        selected_row = payload.selected_row if payload.selected_row is not None else 0
        if steps:
            selected_row = min(max(int(selected_row), 0), len(steps) - 1)
        self._experiment_plan_import_pending_selected_row = selected_row
        self._experiment_plan_import_pending_step_index = 0
        self.plan_table.blockSignals(True)
        self.plan_table.setUpdatesEnabled(False)
        try:
            self._plan_model.set_steps(steps)
            self.plan_table.clearSelection()
        finally:
            self.plan_table.setUpdatesEnabled(True)
            self.plan_table.blockSignals(False)
        self._experiment_plan_import_fill_timer.start()

    def _advance_experiment_plan_import_population(self) -> None:
        def _callback() -> None:
            try:
                if self._experiment_control_bootstrap_pending_state is not None:
                    self._advance_experiment_control_bootstrap_population()
                    return
                steps = self._experiment_plan_import_pending_steps
                payload = self._experiment_plan_import_pending_payload
                if payload is None or not steps:
                    self._experiment_plan_import_fill_timer.stop()
                    self._finalize_experiment_plan_import_population()
                    return
                self._experiment_plan_import_fill_timer.stop()
                self._populate_experiment_control_table(steps, selected_row=self._experiment_plan_import_pending_selected_row)
                self._finalize_experiment_plan_import_population()
            except Exception as exc:
                self._abort_experiment_plan_import_population(str(exc))

        self._run_gui_callback_timed("experiment_plan_import_population", _callback)

    def _finalize_experiment_plan_import_population(self) -> None:
        try:
            payload = self._experiment_plan_import_pending_payload
            steps = self._experiment_plan_import_pending_steps
            if payload is None:
                self._set_experiment_plan_import_running(False)
                return
            if steps:
                self.timeline_widget.set_steps(steps, 0, 0.0)
                self.save_ui_state()
                save_app_setting("experiment_plan_import_dir", str(payload.path.parent))
                self._set_status_message(f"Imported experiment plan from {payload.path.name}.")
        except Exception as exc:
            self._abort_experiment_plan_import_population(str(exc))
            return
        self._experiment_plan_import_pending_payload = None
        self._experiment_plan_import_pending_steps = []
        self._experiment_plan_import_pending_selected_row = None
        self._experiment_plan_import_pending_step_index = 0
        self._set_experiment_plan_import_running(False)

    def _abort_experiment_plan_import_population(self, message: str) -> None:
        self._experiment_plan_import_fill_timer.stop()
        self._experiment_plan_import_pending_payload = None
        self._experiment_plan_import_pending_steps = []
        self._experiment_plan_import_pending_selected_row = None
        self._experiment_plan_import_pending_step_index = 0
        self._set_experiment_plan_import_running(False)
        self._show_error(f"Could not import experiment plan:\n{message}")

    def _import_experiment_control_plan_from_file(self) -> None:
        start_dir = self._experiment_plan_import_default_dir()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import experiment plan",
            str(start_dir),
            "Experiment plan files (*.flow.yaml *.yaml *.yml *.csv *.txt *.h5 *.hdf5);;Native YAML (*.flow.yaml *.yaml *.yml);;HDF5 measurement (*.h5 *.hdf5);;Compatibility CSV/TXT (*.csv *.txt);;All files (*)",
        )
        if not file_path:
            return

        self._start_experiment_plan_import(Path(file_path))

    def _export_experiment_control_plan_placeholder(self) -> None:
        if self._experiment_plan_export_in_progress or self._experiment_plan_import_in_progress:
            return
        steps = self._read_experiment_control_steps()
        if not steps:
            QMessageBox.warning(self, "Export experiment plan", "There is no experiment plan to export.")
            return
        start_dir = self._experiment_plan_export_default_dir()
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export experiment plan",
            str(start_dir / "experiment_plan.flow.yaml"),
            "Native YAML (*.flow.yaml *.yaml *.yml);;Compatibility CSV (*.csv);;Compatibility TXT (*.txt);;All files (*)",
        )
        if not file_path:
            return
        path = Path(file_path)
        if not path.suffix:
            if "TXT" in selected_filter:
                path = path.with_suffix(".txt")
            elif "CSV" in selected_filter:
                path = path.with_suffix(".csv")
            else:
                path = path.with_suffix(".flow.yaml")
        self._start_experiment_plan_export(path)

    def _edit_switch_solution_labels(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated_labels = dialogs.edit_switch_solution_labels(self._switch_solution_labels, anchor)
        if updated_labels is None:
            return
        self._switch_solution_labels = updated_labels
        self._refresh_switch_solution_controls()
        self._update_timeline_selection()

    def _edit_pause_state(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated_step = dialogs.edit_pause_state(self._pause_row_step(), anchor or self.pause_state_button)
        if updated_step is None:
            return
        self._experiment_control_pause_template = updated_step
        self.save_ui_state()
        self._set_status_message("Pause state updated.")
        _LOGGER.info("Pause state updated.")

    def _apply_pause_state(self) -> bool:
        step = self._pause_row_step()
        if step is None:
            return False
        try:
            return self._apply_experiment_control_step_to_pump(step, start=False)
        except Exception as exc:
            _LOGGER.warning("Could not apply pause state: %s", exc)
            return False

    def _edit_color_palette_entries(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated_entries = dialogs.edit_color_palette_entries(self._color_palette_entries, anchor)
        if updated_entries is None:
            return
        self._color_palette_entries = updated_entries
        self._sync_custom_plan_colors_from_palette()
        self._save_color_palette_entries()
        self._update_experiment_control_timeline_label_mode()
        self._refresh_experiment_control_view()
        self._update_timeline_selection()
        return

        dialog = QDialog(self)
        dialog.setWindowTitle("Color palette")
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        dialog.setStyleSheet(
            """
            QDialog {
                background: %(bg)s;
                color: %(fg)s;
            }
            QLabel {
                color: %(muted)s;
            }
            QWidget#paletteDialogBar {
                background: %(bg)s;
            }
            QLabel#paletteDialogTitle {
                color: %(fg)s;
                font-size: 10px;
                font-weight: 700;
            }
            QTableWidget#paletteTable {
                background: %(bg)s;
                color: %(fg)s;
                border: none;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: transparent;
                selection-color: %(fg)s;
                font-size: 11px;
            }
            QTableWidget#paletteTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableWidget#paletteTable::item {
                border: none;
                padding: 1px 4px;
            }
            QTableWidget#paletteTable::item:selected {
                background: transparent;
            }
            QToolButton#paletteColorButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-height: 18px;
                min-width: 112px;
            }
            QToolButton#paletteColorButton:hover {
                border: 1px solid %(border_hover)s;
            }
            QToolButton#paletteRowActionButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
            }
            QToolButton#paletteRowActionButton:hover {
                background: rgba(127, 127, 127, 0.10);
            }
            QToolButton#paletteRowActionButton:pressed {
                background: rgba(127, 127, 127, 0.18);
            }
            QHeaderView::section {
                background: %(header)s;
                color: %(fg)s;
                border: none;
                border-bottom: 1px solid %(border)s;
                padding: 0px 2px;
                font-size: 9px;
            }
            QToolButton#paletteDialogClose {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: %(fg)s;
            }
            QToolButton#paletteDialogClose:hover {
                background: rgba(127, 127, 127, 0.10);
            }
            QPushButton#paletteDialogAction {
                background: %(button)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 2px 8px;
            }
            QPushButton#paletteDialogAction:hover {
                background: %(button_hover)s;
                border-color: %(border_hover)s;
            }
            """ % self._theme_palette()
        )

        top_bar = QWidget(self)
        top_bar.setObjectName("paletteDialogBar")
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(2)
        title_label = QLabel("Color palette")
        title_label.setObjectName("paletteDialogTitle")
        title_label.setToolTip("Edit the palette used by the color dropdown. Save to CSV to share, or load a CSV to overwrite the current palette.")
        close_button = QToolButton(dialog)
        close_button.setObjectName("paletteDialogClose")
        close_button.setAutoRaise(True)
        close_button.setIcon(tint_tabler_icon(flow_tabler_icon("x"), QColor("#e6ebf1")))
        close_button.setIconSize(QSize(14, 14))
        close_button.setToolTip("Close")
        close_button.clicked.connect(dialog.reject)
        top_bar_layout.addWidget(title_label)
        top_bar_layout.addStretch(1)
        top_bar_layout.addWidget(close_button)
        top_bar.setLayout(top_bar_layout)
        layout.addWidget(top_bar)

        table = PaletteTableWidget(0, 2, dialog)
        table.setObjectName("paletteTable")
        table.setHorizontalHeaderLabels(["Name", "Color"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().resizeSection(1, 124)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.verticalHeader().setDefaultSectionSize(18)
        table.horizontalHeader().setMinimumHeight(18)
        table.horizontalHeader().setMaximumHeight(18)
        table.setTabKeyNavigation(True)
        table.setFixedHeight(table.horizontalHeader().height() + table.verticalHeader().defaultSectionSize() * 12 + 4)
        self._populate_color_palette_table(table, self._color_palette_entries)
        layout.addWidget(table)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(4)
        add_button = QToolButton(dialog)
        add_button.setObjectName("paletteRowActionButton")
        add_button.setAutoRaise(True)
        add_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        add_button.setIcon(tint_tabler_icon(flow_tabler_icon("plus"), QColor("#47a861")))
        add_button.setIconSize(QSize(14, 14))
        add_button.setToolTip("Add a palette row")
        remove_button = QToolButton(dialog)
        remove_button.setObjectName("paletteRowActionButton")
        remove_button.setAutoRaise(True)
        remove_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        remove_button.setIcon(tint_tabler_icon(flow_tabler_icon("x"), QColor("#b44a4a")))
        remove_button.setIconSize(QSize(14, 14))
        remove_button.setToolTip("Remove the selected palette row")
        move_up_button = QToolButton(dialog)
        move_up_button.setObjectName("paletteRowActionButton")
        move_up_button.setAutoRaise(True)
        move_up_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        move_up_button.setIcon(tint_tabler_icon(flow_tabler_icon("chevron_up"), QColor("#e6ebf1")))
        move_up_button.setIconSize(QSize(14, 14))
        move_up_button.setToolTip("Move selected row up (Page Up)")
        move_down_button = QToolButton(dialog)
        move_down_button.setObjectName("paletteRowActionButton")
        move_down_button.setAutoRaise(True)
        move_down_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        move_down_button.setIcon(tint_tabler_icon(flow_tabler_icon("chevron_down"), QColor("#e6ebf1")))
        move_down_button.setIconSize(QSize(14, 14))
        move_down_button.setToolTip("Move selected row down (Page Down)")
        load_button = QPushButton("Load CSV", dialog)
        save_button = QPushButton("Save CSV", dialog)
        apply_button = QPushButton("Apply", dialog)
        cancel_button = QPushButton("Cancel", dialog)
        for button in (load_button, save_button, apply_button, cancel_button):
            button.setObjectName("paletteDialogAction")
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)
        button_row.addWidget(move_up_button)
        button_row.addWidget(move_down_button)
        button_row.addStretch(1)
        button_row.addWidget(load_button)
        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)
        dialog.adjustSize()

        def _add_row() -> None:
            row = table.rowCount()
            table.insertRow(row)
            self._set_palette_table_row(table, row, f"Custom {row + 1}", "#4E79A7")
            table.setCurrentCell(row, 0)

        def _remove_row() -> None:
            row = table.currentRow()
            if row < 0:
                return
            table.removeRow(row)
            if table.rowCount() > 0:
                table.setCurrentCell(min(row, table.rowCount() - 1), 0)

        def _move_row(delta: int) -> None:
            row = table.currentRow()
            if row < 0:
                return
            next_row = row + delta
            if next_row < 0 or next_row >= table.rowCount():
                return
            entries = self._read_color_palette_table(table)
            if row >= len(entries) or next_row >= len(entries):
                return
            entries[row], entries[next_row] = entries[next_row], entries[row]
            self._populate_color_palette_table(table, entries)
            table.setCurrentCell(next_row, 0)

        def _load_csv() -> None:
            file_path, _filter = QFileDialog.getOpenFileName(
                self,
                "Load color palette",
                str(Path.home()),
                "Palette files (*.csv *.tsv);;CSV files (*.csv);;TSV files (*.tsv);;All files (*)",
            )
            if not file_path:
                return
            try:
                entries = self._read_color_palette_file(Path(file_path))
            except Exception as exc:
                QMessageBox.critical(self, "Load color palette", f"Could not load palette:\n{exc}")
                return
            self._populate_color_palette_table(table, entries)

        def _save_csv() -> None:
            file_path, _filter = QFileDialog.getSaveFileName(
                self,
                "Save color palette",
                str(Path.home() / "color_palette.csv"),
                "Palette files (*.csv *.tsv);;CSV files (*.csv);;TSV files (*.tsv);;All files (*)",
            )
            if not file_path:
                return
            try:
                entries = self._read_color_palette_table(table)
                self._write_color_palette_file(Path(file_path), entries)
            except Exception as exc:
                QMessageBox.critical(self, "Save color palette", f"Could not save palette:\n{exc}")

        def _apply() -> None:
            entries = self._read_color_palette_table(table)
            if not entries:
                QMessageBox.warning(self, "Color palette", "Palette is empty. Add at least one color entry.")
                return
            self._color_palette_entries = entries
            self._sync_custom_plan_colors_from_palette()
            dialog.accept()

        add_button.clicked.connect(_add_row)
        remove_button.clicked.connect(_remove_row)
        move_up_button.clicked.connect(lambda: _move_row(-1))
        move_down_button.clicked.connect(lambda: _move_row(1))
        load_button.clicked.connect(_load_csv)
        save_button.clicked.connect(_save_csv)
        apply_button.clicked.connect(_apply)
        cancel_button.clicked.connect(dialog.reject)
        table._move_selected_row = _move_row  # type: ignore[attr-defined]

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_color_palette_entries()
        self._refresh_experiment_control_view()
        self._update_timeline_selection()

    def _set_palette_table_row(self, table: QTableWidget, row: int, name: str, color: str) -> None:
        name_item = QTableWidgetItem(name)
        name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, 0, name_item)
        color_button = QToolButton(table)
        color_button.setObjectName("paletteColorButton")
        color_button.setAutoRaise(True)
        color_button.setToolTip("Click to change the color.")
        color_button.setProperty("palette_row", row)
        color_button.setProperty("palette_color", color)
        color_button.clicked.connect(lambda _checked=False, btn=color_button, tbl=table: self._choose_palette_row_color(tbl, btn))
        self._style_palette_color_button(color_button, color)
        table.setCellWidget(row, 1, color_button)

    def _style_palette_color_button(self, button: QToolButton, color: str) -> None:
        qcolor = QColor(color)
        if not qcolor.isValid():
            qcolor = QColor("#4E79A7")
        text_color = self._contrast_text_color(qcolor.name())
        button.setText(qcolor.name().upper())
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setIcon(QIcon())
        button.setIconSize(QSize(0, 0))
        button.setStyleSheet(
            f"QToolButton#paletteColorButton {{"
            f" background: {qcolor.name().upper()};"
            f" color: {text_color};"
            " border: none;"
            " border-radius: 4px;"
            " padding: 0px 6px;"
            "}}"
            "QToolButton#paletteColorButton:hover {"
            " border: 1px solid rgba(255,255,255,0.22);"
            "}"
        )

    def _choose_palette_row_color(self, table: QTableWidget, button: QToolButton) -> None:
        row = int(button.property("palette_row") or -1)
        if row < 0 or row >= table.rowCount():
            return
        current = str(button.property("palette_color") or "#4E79A7")
        chosen = QColorDialog.getColor(QColor(current), self, "Pick palette color")
        if not chosen.isValid():
            return
        color = chosen.name().upper()
        button.setProperty("palette_color", color)
        self._style_palette_color_button(button, color)

    def _populate_color_palette_table(self, table: QTableWidget, entries: list[tuple[str, str]]) -> None:
        table.setRowCount(0)
        for row, (name, color) in enumerate(entries):
            table.insertRow(row)
            self._set_palette_table_row(table, row, name, color)
        if table.rowCount() > 0:
            table.setCurrentCell(0, 0)

    def _read_color_palette_table(self, table: QTableWidget) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            color_widget = table.cellWidget(row, 1)
            name = name_item.text().strip() if name_item is not None else ""
            color_text = ""
            if isinstance(color_widget, QToolButton):
                color_text = str(color_widget.property("palette_color") or "").strip()
            entry = self._normalize_color_entry(name, color_text, row)
            if entry is not None:
                entries.append(entry)
        return entries

    def _write_color_palette_file(self, path: Path, entries: list[tuple[str, str]]) -> None:
        delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow(["name", "color"])
            for name, color in entries:
                writer.writerow([name, color])

    def _read_color_palette_file(self, path: Path) -> list[tuple[str, str]]:
        delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
        entries: list[tuple[str, str]] = []
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            rows = list(reader)
        if not rows:
            return entries
        start_index = 1 if rows[0] and [cell.strip().lower() for cell in rows[0][:2]] == ["name", "color"] else 0
        for index, row in enumerate(rows[start_index:]):
            if not row:
                continue
            name = row[0] if len(row) > 0 else ""
            color = row[1] if len(row) > 1 else ""
            entry = self._normalize_color_entry(name, color, index)
            if entry is not None:
                entries.append(entry)
        return entries

    def _set_step_valve_button_state(self, valve: str) -> None:
        set_step_valve_button_state_for_button(self, self.step_valve_button, valve)

    def _toggle_step_valve_button(self, button: QToolButton | None = None) -> None:
        if not isinstance(button, QToolButton):
            button = self.step_valve_button
        current = str(button.property("valve") or "Open")
        next_state = "Close" if current != "Close" else "Open"
        set_step_valve_button_state_for_button(self, button, next_state)

    def _load_valve_state_labels(self, state: dict[str, object]) -> dict[str, str]:
        labels = {"Open": "Open", "Close": "Close"}
        payload = state.get("valve_state_labels")
        if isinstance(payload, dict):
            open_label = str(payload.get("Open", "Open")).strip() or "Open"
            close_label = str(payload.get("Close", "Close")).strip() or "Close"
            labels["Open"] = open_label
            labels["Close"] = close_label
        return labels

    def _load_valve_state_colors(self, state: dict[str, object]) -> dict[str, str]:
        colors = {"Open": "#4E79A7", "Close": "#B44A4A"}
        payload = state.get("valve_state_colors")
        if isinstance(payload, dict):
            open_color = QColor(str(payload.get("Open", colors["Open"]) or colors["Open"]).strip())
            close_color = QColor(str(payload.get("Close", colors["Close"]) or colors["Close"]).strip())
            if open_color.isValid():
                colors["Open"] = open_color.name().upper()
            if close_color.isValid():
                colors["Close"] = close_color.name().upper()
        return colors

    def _valve_state_label(self, valve: str) -> str:
        normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
        label = str(self._valve_state_labels.get(normalized, normalized)).strip()
        return label or normalized

    def _valve_state_color(self, valve: str) -> str:
        normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
        color = QColor(str(self._valve_state_colors.get(normalized, "")).strip())
        if color.isValid():
            return color.name().upper()
        return "#4E79A7" if normalized == "Open" else "#B44A4A"

    def _edit_valve_state_labels(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated = dialogs.edit_valve_labels(self._valve_state_labels, self._valve_state_colors, anchor)
        if updated is None:
            return
        updated_labels, updated_colors = updated
        self._valve_state_labels = updated_labels
        self._valve_state_colors = updated_colors
        set_step_valve_button_state_for_button(self, self.step_valve_button, str(self.step_valve_button.property("valve") or "Open"))
        self._refresh_experiment_control_view()
        self.save_ui_state()
        return

        dialog = QDialog(self)
        dialog.setWindowTitle("Valve labels")
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dialog.resize(220, 126)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        dialog.setStyleSheet(
            """
            QDialog {
                background: %(bg)s;
                color: %(fg)s;
            }
            QLabel#valveDialogTitle {
                color: %(fg)s;
                font-size: 10px;
                font-weight: 700;
            }
            QTableWidget#valveTable {
                background: %(bg)s;
                color: %(fg)s;
                border: none;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: %(selection)s;
                selection-color: %(fg)s;
                font-size: 10px;
            }
            QTableWidget#valveTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableWidget#valveTable::item {
                border: none;
                padding: 0px 2px;
            }
            QTableWidget#valveTable::item:selected {
                background: transparent;
            }
            QHeaderView::section {
                background: %(header)s;
                color: %(fg)s;
                border: none;
                border-bottom: 1px solid %(border)s;
                padding: 0px 1px;
                font-size: 8px;
            }
            QPushButton#valveDialogAction {
                background: %(button)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                border-radius: 7px;
                padding: 1px 7px;
            }
            QPushButton#valveDialogAction:hover {
                background: %(button_hover)s;
                border-color: %(border_hover)s;
            }
            """ % self._theme_palette()
        )
        top_label = QLabel("Valve labels")
        top_label.setObjectName("valveDialogTitle")
        top_label.setToolTip("Define the display labels for Open and Close valve states.")
        layout.addWidget(top_label)

        table = QTableWidget(2, 2, dialog)
        table.setObjectName("valveTable")
        table.setHorizontalHeaderLabels(["Value", "Label"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.verticalHeader().setDefaultSectionSize(17)
        table.horizontalHeader().setMinimumHeight(16)
        table.horizontalHeader().setMaximumHeight(16)
        entries = [("Open", self._valve_state_labels.get("Open", "Open")), ("Close", self._valve_state_labels.get("Close", "Close"))]
        table.setRowCount(0)
        for row, (value, label) in enumerate(entries):
            table.insertRow(row)
            value_item = QTableWidgetItem(value)
            value_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 0, value_item)
            label_edit = ValveLabelEdit(lambda step, r=row: _move_focus(r, step), table)
            label_edit.setText(label)
            label_edit.setFrame(False)
            label_edit.setPlaceholderText("Label")
            label_edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label_edit.setStyleSheet(
                "QLineEdit {"
                " background: transparent;"
                " border: none;"
                " padding: 0px 2px;"
                " margin: 0px;"
                " }"
            )
            label_edit.textEdited.connect(lambda _text: self._handle_experiment_control_table_change(None))
            table.setCellWidget(row, 1, label_edit)
        table.setFixedHeight(table.horizontalHeader().height() + table.verticalHeader().defaultSectionSize() * 2 + 3)
        layout.addWidget(table, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        apply_button = QPushButton("Apply", dialog)
        apply_button.setObjectName("valveDialogAction")
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)

        def _apply() -> None:
            open_widget = table.cellWidget(0, 1)
            close_widget = table.cellWidget(1, 1)
            open_label = (open_widget.text().strip() if isinstance(open_widget, QLineEdit) else "") or "Open"
            close_label = (close_widget.text().strip() if isinstance(close_widget, QLineEdit) else "") or "Close"
            self._valve_state_labels = {"Open": open_label, "Close": close_label}
            set_step_valve_button_state_for_button(
                self,
                self.step_valve_button,
                str(self.step_valve_button.property("valve") or "Open"),
            )
            self._refresh_experiment_control_view()
            self.save_ui_state()
            dialog.accept()

        apply_button.clicked.connect(_apply)
        def _move_focus(row: int, step: int) -> None:
            next_row = (row + step) % 2
            table.setCurrentCell(next_row, 1)
            widget = table.cellWidget(next_row, 1)
            if isinstance(widget, QLineEdit):
                widget.setFocus()
                widget.selectAll()

        def _focus_label_cell(row: int, column: int, previous_row: int, previous_column: int) -> None:
            if row < 0:
                return
            target_row = row
            if column != 1:
                table.blockSignals(True)
                table.setCurrentCell(target_row, 1)
                table.blockSignals(False)
            widget = table.cellWidget(target_row, 1)
            if isinstance(widget, QLineEdit):
                QTimer.singleShot(0, lambda w=widget: (w.setFocus(), w.selectAll()))

        table.currentCellChanged.connect(_focus_label_cell)
        table.setCurrentCell(0, 1)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.save_ui_state()

    def _set_direction_button(self, button: QToolButton, direction: str) -> None:
        normalized = "CCW" if str(direction or "").upper() == "CCW" else "CW"
        button.setProperty("direction", normalized)
        button.setText(direction_glyph(normalized))

    def _direction_button_value(self, button: QToolButton) -> str:
        value = button.property("direction")
        return str(value) if value in {"CW", "CCW"} else "CW"

    def _toggle_direction_button(self, button: QToolButton, on_change=None) -> None:
        next_direction = "CCW" if self._direction_button_value(button) == "CW" else "CW"
        self._set_direction_button(button, next_direction)
        if on_change is not None:
            on_change()

    def _update_experiment_control_toggle_button(self) -> None:
        snapshot = self._experiment_runtime_snapshot()
        timer = getattr(self, "_plan_hold_blink_timer", None)
        if snapshot.blink_active:
            if timer is not None and not timer.isActive():
                self._plan_hold_blink_frame = 0
                timer.start()
        else:
            if timer is not None and timer.isActive():
                timer.stop()
            self._plan_hold_blink_frame = 0

        self.plan_toggle_button.setIcon(self._play_plan_button_icon(active=snapshot.play_active))
        if snapshot.running:
            self.plan_toggle_button.setToolTip("Plan running. Click to resume from the current state.")
        elif snapshot.holding or snapshot.paused:
            self.plan_toggle_button.setToolTip("Resume the plan.")
        else:
            self.plan_toggle_button.setToolTip("Start the plan.")

        self.hold_plan_button.setVisible(snapshot.hold_visible)
        self.hold_plan_button.setEnabled(snapshot.hold_enabled)
        self.hold_plan_button.setIcon(self._hold_plan_button_icon(active=snapshot.holding))
        if snapshot.holding:
            self.hold_plan_button.setToolTip("Hold active. Click to resume from hold.")
            self.hold_plan_button.setChecked(True)
        elif snapshot.running:
            self.hold_plan_button.setToolTip("Hold the running plan.")
            self.hold_plan_button.setChecked(False)
        else:
            self.hold_plan_button.setToolTip("Hold is available only while the plan is running.")
            self.hold_plan_button.setChecked(False)

        self.pause_plan_button.setEnabled(snapshot.pause_available)
        self.pause_plan_button.setIcon(self._runtime_pause_button_icon(active=snapshot.paused))
        if snapshot.paused:
            self.pause_plan_button.setToolTip("Pause active. Click to resume from pause.")
            self.pause_plan_button.setChecked(True)
        elif snapshot.running:
            self.pause_plan_button.setToolTip("Apply the pause state.")
            self.pause_plan_button.setChecked(False)
        elif snapshot.holding:
            self.pause_plan_button.setToolTip("Apply the pause state from hold.")
            self.pause_plan_button.setChecked(False)
        elif snapshot.pause_available:
            self.pause_plan_button.setToolTip("Start the plan in pause state.")
            self.pause_plan_button.setChecked(False)
        else:
            self.pause_plan_button.setToolTip("Pause is available only while the plan is running.")
            self.pause_plan_button.setChecked(False)

        self.stop_plan_button.setVisible(snapshot.stop_visible)
        self.stop_plan_button.setEnabled(snapshot.stop_visible)

    def _play_plan_button_icon(self, *, active: bool) -> QIcon:
        if active:
            alpha_steps = [255, 255, 220, 160, 90, 40, 90, 160]
            frame_index = int(getattr(self, "_plan_hold_blink_frame", 0)) % len(alpha_steps)
            color = QColor("#38d862")
            color.setAlpha(alpha_steps[frame_index])
        else:
            color = QColor("#38d862")
        try:
            return tint_tabler_icon(flow_tabler_icon("player_play", "play"), color)
        except Exception:
            return transport_icon(self._theme_mode, "play")

    def _advance_plan_hold_blink_indicator(self) -> None:
        def _callback() -> None:
            timer = getattr(self, "_plan_hold_blink_timer", None)
            if timer is None or not timer.isActive() or not (self._plan_running or self._plan_holding or self._plan_paused):
                return
            self._plan_hold_blink_frame = (int(getattr(self, "_plan_hold_blink_frame", 0)) + 1) % 8
            self._update_experiment_control_toggle_button()

        self._run_gui_callback_timed("plan_hold_blink", _callback)

    def _update_record_with_flow_button_icon(self) -> None:
        if not hasattr(self, "record_with_flow_button"):
            return
        active = self.record_with_flow_button.isChecked()
        recording_active = bool(getattr(self, "_record_with_flow_recording_active", False))
        color = QColor("#47a861" if active else "#8a98a8")
        self.record_with_flow_button.setIcon(tint_tabler_icon(flow_tabler_icon("file_pencil"), color))
        if recording_active:
            self.record_with_flow_button.setToolTip("Sensorgram recording is active. The setting is locked while recording.")
        else:
            self.record_with_flow_button.setToolTip("Record measurement data while the experiment plan runs.")

    def _handle_record_with_flow_button_toggled(self, checked: bool) -> None:
        recording_active = bool(getattr(self, "_record_with_flow_recording_active", False))
        if recording_active:
            locked_checked = bool(getattr(self, "_record_with_flow_locked_checked", True))
            if bool(checked) != locked_checked:
                self.record_with_flow_button.blockSignals(True)
                try:
                    self.record_with_flow_button.setChecked(locked_checked)
                finally:
                    self.record_with_flow_button.blockSignals(False)
            self._update_record_with_flow_button_icon()
            return
        self._record_with_flow_locked_checked = bool(checked)
        self._update_record_with_flow_button_icon()

    def _set_record_with_flow_recording_active(self, active: bool) -> None:
        self._record_with_flow_recording_active = bool(active)
        if active:
            self._record_with_flow_locked_checked = bool(self.record_with_flow_button.isChecked())
            if self._pending_experiment_control_start_after_recording is not None:
                QTimer.singleShot(0, self._run_pending_experiment_control_start_after_recording)
        if not active:
            if self._measurement_started_monotonic is not None:
                self._plan_runtime_s = self._plan_runtime_for_display()
            if self._step_started_monotonic is not None:
                self._plan_resume_runtime_s = self._step_runtime_for_display()
            self._measurement_started_monotonic = None
            self._step_started_monotonic = None
            self._pending_experiment_control_start_after_recording = None
            self._refresh_status_line()
            if self._read_experiment_control_steps():
                self.timeline_widget.set_steps(
                    self._read_experiment_control_steps(),
                    self._experiment_control_timeline_row(),
                    self._timeline_progress_for_display(),
                    self._plan_runtime_for_display(),
                    self._step_runtime_for_display(),
                )
        self._update_record_with_flow_button_icon()

    def _toggle_experiment_control_run_hold(self) -> None:
        self._start_or_resume_experiment_control()

    def _toggle_experiment_control_hold(self) -> None:
        if self._plan_running:
            self._hold_experiment_control()
            return
        if self._plan_holding:
            self._start_or_resume_experiment_control()

    def _toggle_experiment_control_pause(self) -> None:
        if self._plan_running:
            self._pause_experiment_control()
            return
        if self._plan_holding:
            self._pause_experiment_control()
            return
        if self._plan_paused:
            self._start_or_resume_experiment_control()
            return
        self._start_paused_experiment_control()

    def _set_plan_runtime_flags(self, *, running: bool, holding: bool, paused: bool) -> None:
        self._plan_running = bool(running)
        self._plan_holding = bool(holding)
        self._plan_paused = bool(paused)

    def _capture_plan_elapsed_from_clock(self) -> float:
        if self._plan_started_monotonic is None:
            return max(float(self._plan_elapsed_s), 0.0)
        elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
        self._plan_elapsed_s = elapsed
        self._plan_resume_elapsed_s = elapsed
        return elapsed

    def _reset_plan_runtime_counters(self) -> None:
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_runtime_s = 0.0
        self._plan_resume_runtime_s = 0.0

    def _ensure_measurement_started(self) -> None:
        if self._measurement_started_monotonic is None:
            self._measurement_started_monotonic = monotonic()

    def _experiment_runtime_snapshot(self) -> ExperimentRuntimeSnapshot:
        return experiment_runtime_snapshot(
            running=self._plan_running,
            holding=self._plan_holding,
            paused=self._plan_paused,
            recording=bool(self.__dict__.get("_measurement_started_monotonic") is not None),
            has_steps=bool(self._read_experiment_control_steps()),
        )

    def _set_experiment_control_runtime_row_property(self, row: int | None) -> None:
        self.plan_table.setProperty("experiment_control_runtime_row", row)
        self.plan_table.viewport().setProperty("experiment_control_runtime_row", row)

    def _ensure_experiment_control_plan_row_visible(self, plan_row: int | None, *, center: bool = False) -> None:
        if plan_row is None:
            return
        table_row = self._table_row_from_plan_row(plan_row)
        if not (0 <= table_row < self.plan_table.rowCount()):
            return
        model = self.plan_table.model()
        if model is None:
            return
        index = model.index(table_row, 0)
        if not index.isValid():
            return
        hint = QAbstractItemView.ScrollHint.PositionAtCenter if center else QAbstractItemView.ScrollHint.EnsureVisible
        self.plan_table.scrollTo(index, hint)

    def _sync_experiment_control_timeline(self, steps: list[PumpPlanStep], plan_row: int | None, *, refresh_status: bool = False) -> None:
        runtime_row = plan_row if (self._plan_running or self._plan_holding or self._plan_paused) else None
        self._set_experiment_control_runtime_row_property(runtime_row)
        self.timeline_widget.set_steps(
            steps,
            plan_row,
            self._timeline_progress_for_display(),
            self._plan_runtime_for_display(),
            self._step_runtime_for_display(),
        )
        self._ensure_experiment_control_plan_row_visible(
            plan_row if (self._plan_running or self._plan_holding or self._plan_paused) else self._selected_experiment_control_row(),
            center=bool(self._plan_running or self._plan_holding or self._plan_paused),
        )
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.sync_overlay()
        if refresh_status:
            self._refresh_status_line()

    def _resume_experiment_plan(
        self,
        *,
        restore_step: PumpPlanStep | None = None,
        status_message: str,
        log_message: str,
        emit_event: str,
        emit_step: PumpPlanStep | None = None,
    ) -> None:
        if restore_step is not None:
            self._apply_experiment_control_step_to_pump(restore_step, start=True)
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_started_monotonic = monotonic()
        if self._plan_timer.isActive():
            self._plan_timer.stop()
        self._plan_timer.start()
        self._update_experiment_control_toggle_button()
        self._set_status_message(status_message)
        _LOGGER.info(log_message)
        steps = self._read_experiment_control_steps()
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state(emit_event, emit_step or steps[self._plan_active_row])

    def _begin_experiment_plan_run(self, row: int, steps: list[PumpPlanStep]) -> None:
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._step_started_monotonic = monotonic()
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_active_row = row
        self._plan_started_monotonic = monotonic()
        self._update_experiment_control_toggle_button()
        self._activate_experiment_control_step_for_elapsed(0.0, force=True)
        self._plan_timer.start()
        self._set_status_message(f"Running experiment plan from step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}.")
        _LOGGER.info("Experiment plan started | step=%s", self._plan_active_row + 1 if self._plan_active_row is not None else 1)
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state("plan_started", steps[self._plan_active_row])

    def _begin_paused_experiment_plan_run(self, row: int, steps: list[PumpPlanStep]) -> None:
        self._paused_plan_step = deepcopy(steps[row])
        self._plan_active_row = row
        if self._plan_timer.isActive():
            self._plan_timer.stop()
        self._plan_timer.start()
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._set_plan_runtime_flags(running=False, holding=False, paused=True)
        self._plan_started_monotonic = None
        self._step_started_monotonic = None
        pause_applied = self._apply_pause_state()
        self._update_experiment_control_toggle_button()
        if pause_applied:
            self._set_status_message(
                f"Experiment plan started in pause state on step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}. Pause state applied."
            )
            _LOGGER.info(
                "Experiment plan started in pause state with pause state applied | step=%s",
                self._plan_active_row + 1 if self._plan_active_row is not None else 1,
            )
        else:
            self._set_status_message(
                f"Experiment plan started in pause state on step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}."
            )
            _LOGGER.info(
                "Experiment plan started in pause state | step=%s",
                self._plan_active_row + 1 if self._plan_active_row is not None else 1,
            )
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state("plan_pause", self._applied_plan_step, status="started in pause state")

    def _resume_experiment_control_after_manual_step_change(
        self,
        row: int,
        *,
        status_message: str,
        log_message: str,
        emit_event: str,
    ) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        if not self._apply_experiment_control_step_to_pump(steps[row], start=True):
            return
        self._paused_plan_step = None
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_active_row = row
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_started_monotonic = monotonic()
        self._step_started_monotonic = monotonic()
        if self._plan_timer.isActive():
            self._plan_timer.stop()
        self._plan_timer.start()
        self._update_experiment_control_toggle_button()
        self._sync_experiment_control_timeline(steps, row, refresh_status=True)
        self._set_status_message(status_message)
        _LOGGER.info(log_message)
        self._emit_experimental_control_state(emit_event, steps[row])

    def _queue_experiment_control_start_after_recording(self, *, paused: bool, row: int | None) -> None:
        self._pending_experiment_control_start_after_recording = (bool(paused), row)

    def _run_pending_experiment_control_start_after_recording(self) -> None:
        pending = self._pending_experiment_control_start_after_recording
        if pending is None:
            return
        self._pending_experiment_control_start_after_recording = None
        paused, row = pending
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        if row is None or not (0 <= int(row) < len(steps)):
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
                self._select_experiment_control_plan_row(0)
        if paused:
            self._begin_paused_experiment_plan_run(int(row), steps)
        else:
            self._begin_experiment_plan_run(int(row), steps)

    def _enter_hold_state(self) -> None:
        if not self._plan_running:
            return
        # HOLD freezes plan time and cursor position, but does not stop recording.
        self._capture_plan_elapsed_from_clock()
        self._set_plan_runtime_flags(running=False, holding=True, paused=False)
        self._plan_started_monotonic = None
        self._plan_runtime_s = self._step_runtime_for_display()
        self._update_experiment_control_toggle_button()
        self._set_status_message("Experiment plan hold.")
        _LOGGER.info("Experiment plan hold.")
        self._emit_experimental_control_state("plan_hold", self._applied_plan_step)

    def _enter_pause_state(self, *, restore_step: PumpPlanStep | None = None) -> None:
        if not (self._plan_running or self._plan_holding):
            return
        if self._plan_running:
            self._capture_plan_elapsed_from_clock()
        self._paused_plan_step = deepcopy(self._applied_plan_step) if self._applied_plan_step is not None else None
        if restore_step is not None:
            self._paused_plan_step = deepcopy(restore_step)
        pause_applied = self._apply_pause_state()
        self._set_plan_runtime_flags(running=False, holding=False, paused=True)
        self._plan_started_monotonic = None
        self._plan_runtime_s = self._step_runtime_for_display()
        self._step_started_monotonic = None
        self._update_experiment_control_toggle_button()
        if pause_applied:
            self._set_status_message("Experiment plan paused. Pause state applied.")
            _LOGGER.info("Experiment plan paused with pause state applied.")
        else:
            self._set_status_message("Experiment plan paused.")
            _LOGGER.info("Experiment plan paused.")
        self._emit_experimental_control_state("plan_pause", self._applied_plan_step)

    def _stop_experiment_plan(self, last_step: PumpPlanStep | None) -> None:
        steps = self._read_experiment_control_steps()
        target_row = self._plan_active_row
        if target_row is None:
            target_row = self._selected_experiment_control_row()
        if target_row is None and steps:
            target_row = 0
        if steps and target_row is not None:
            target_row = min(max(int(target_row), 0), len(steps) - 1)
            self._plan_active_row = target_row
            if not (self._plan_running or self._plan_holding or self._plan_paused):
                self._plan_elapsed_s = 0.0
            self._plan_resume_elapsed_s = self._plan_elapsed_s
            self._sync_experiment_control_timeline(steps, target_row)
        if self._plan_running:
            self._capture_plan_elapsed_from_clock()
        self._set_plan_runtime_flags(running=False, holding=False, paused=False)
        self._plan_started_monotonic = None
        self._step_started_monotonic = None
        self._measurement_started_monotonic = None
        self._plan_runtime_s = self._plan_runtime_for_display()
        self._plan_resume_runtime_s = self._step_runtime_for_display()
        self._applied_plan_step = None
        self._paused_plan_step = None
        self._plan_timer.stop()
        self._update_experiment_control_toggle_button()
        if self._client.is_connected():
            self._stop_all_channels()
        else:
            self._set_status_message("Experiment plan stopped.")
        _LOGGER.info("Experiment plan stopped.")
        self._emit_experimental_control_state("plan_stopped", last_step)
        self._request_recording_control("stop")

    def _start_paused_experiment_control(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        recording_active = bool(getattr(getattr(self, "recording_controller", None), "_measurement_active", False))
        if self.record_with_flow_button.isChecked() and not recording_active:
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
            self._queue_experiment_control_start_after_recording(paused=True, row=row)
        if not self._request_recording_control("start"):
            self._pending_experiment_control_start_after_recording = None
            self._set_status_message("Experiment plan start cancelled because recording was not started.")
            return
        if self._pending_experiment_control_start_after_recording is not None:
            return
        row = self._selected_experiment_control_row()
        if row is None:
            row = 0
            self._select_experiment_control_plan_row(0)
        self._begin_paused_experiment_plan_run(row, steps)

    def _request_recording_control(self, action: str) -> None:
        if not self.record_with_flow_button.isChecked():
            return True
        if str(action or "").strip().lower() == "pause":
            return True
        controller = getattr(self, "recording_controller", None)
        if controller is not None and hasattr(controller, "_handle_flow_recording_control"):
            return bool(controller._handle_flow_recording_control(action))  # noqa: SLF001
        self.recording_control_requested.emit(action)
        return True

    def _cycle_time_unit_mode(self) -> None:
        order = ["s", "min", "h"]
        next_index = (order.index(self._time_unit_mode) + 1) % len(order)
        new_mode = order[next_index]
        if new_mode == self._time_unit_mode:
            self._update_time_unit_ui()
            return
        steps = self._read_experiment_control_steps()
        selected_row = self._selected_experiment_control_row()
        self._time_unit_mode = new_mode
        self._update_time_unit_ui(self._editor_duration_seconds)
        self._populate_experiment_control_table(steps)
        if selected_row is not None and 0 <= selected_row < self.plan_table.rowCount():
            self._select_experiment_control_plan_row(selected_row)
        self._refresh_status_line()
        self.save_ui_state()

    def _theme_palette(self) -> dict[str, str]:
        if self._theme_mode == "dark":
            return {
                "bg": "#13161b",
                "fg": "#e6ebf1",
                "muted": "#a8b0ba",
                "field": "#171b21",
                "button": "#20252d",
                "button_hover": "#272d36",
                "button_pressed": "#303640",
                "accent_button": "#5d6876",
                "accent_hover": "#707d8c",
                "title": "#8fbaff",
                "danger_button": "#8f5a61",
                "danger_hover": "#a46a72",
                "border": "#2b3138",
                "border_hover": "#414852",
                "pressed": "#252b33",
                "scroll": "#49505a",
                "scroll_hover": "#5c6470",
                "splitter": "#2b3138",
                "timeline_bg": "#0f1216",
                "header": "#1b2026",
                "selection": "#252b33",
            }
        return {
            "bg": "#f4f6f8",
            "fg": "#1d2733",
            "muted": "#5f7388",
            "field": "#f4f6f8",
            "button": "#eef3f7",
            "button_hover": "#e6edf3",
            "button_pressed": "#dde9f3",
            "accent_button": "#2f80c1",
            "accent_hover": "#3e8dcf",
            "title": "#2f80c1",
            "danger_button": "#d65a63",
            "danger_hover": "#e06a73",
            "border": "#d9e0e7",
            "border_hover": "#9dbbd4",
            "pressed": "#dde9f3",
            "scroll": "#bcc9d5",
            "scroll_hover": "#9fb3c5",
            "splitter": "#dde5ec",
            "timeline_bg": "#f4f6f8",
            "header": "#eef3f7",
            "selection": "#dbeafe",
        }

    def _apply_style(self) -> None:
        palette = self._theme_palette()
        self.setStyleSheet(
            """
            QWidget {
                background: %(bg)s;
                color: %(fg)s;
                font-size: 12px;
            }
            QToolTip {
                background-color: %(bg)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px 6px;
            }
            QGroupBox {
                background: %(bg)s;
                border: 1px solid %(border)s;
                border-radius: 12px;
                margin-top: 8px;
                padding-top: 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                left: 10px;
                top: 2px;
            }
            QPushButton, QToolButton, QComboBox, QDoubleSpinBox, QLineEdit, QTableWidget {
                background: %(field)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
                padding: 4px 6px;
            }
            QSpinBox, QDoubleSpinBox {
                border-radius: 3px;
                padding: 1px 4px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                border: none;
                background: transparent;
            }
            QSpinBox::up-arrow, QSpinBox::down-arrow,
            QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QPushButton:hover, QToolButton:hover, QComboBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
                border-color: %(border_hover)s;
                background: %(button_hover)s;
            }
            QPushButton:pressed, QToolButton:pressed {
                background: %(button_pressed)s;
            }
            QPushButton#accentButton {
                background: %(accent_button)s;
                border-color: %(accent_button)s;
            }
            QPushButton#accentButton:hover, QToolButton#accentButton:hover {
                background: %(accent_hover)s;
                border-color: %(accent_hover)s;
            }
            QToolButton#accentButton {
                background: %(accent_button)s;
                border-color: %(accent_button)s;
            }
            QPushButton#dangerButton {
                background: %(danger_button)s;
                border-color: %(danger_button)s;
            }
            QPushButton#dangerButton:hover, QToolButton#dangerButton:hover {
                background: %(danger_hover)s;
                border-color: %(danger_hover)s;
            }
            QToolButton#dangerButton {
                background: %(danger_button)s;
                border-color: %(danger_button)s;
            }
            QToolButton#flowIconButton {
                background: transparent;
                border: none;
                padding: 0px;
            }
            QToolButton#flowIconButton:hover {
                background: rgba(127, 127, 127, 0.10);
                border: none;
            }
            QToolButton#flowIconButton:pressed {
                background: rgba(127, 127, 127, 0.18);
                border: none;
            }
            QToolButton#flowViewModeButton {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                color: #e8d85f;
                font-weight: 600;
            }
            QToolButton#flowViewModeButton:hover {
                background: rgba(127, 127, 127, 0.10);
                border: none;
            }
            QToolButton#flowViewModeButton:pressed {
                background: rgba(127, 127, 127, 0.18);
                border: none;
            }
            QToolButton#flowColorAddButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
            }
            QToolButton#flowColorAddButton:hover {
                background: rgba(47, 143, 83, 0.10);
            }
            QToolButton#flowColorAddButton:pressed {
                background: rgba(47, 143, 83, 0.18);
            }
            QToolButton#flowColorRemoveButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: #b44a4a;
            }
            QToolButton#flowColorRemoveButton:hover {
                background: rgba(180, 74, 74, 0.10);
            }
            QToolButton#flowColorRemoveButton:pressed {
                background: rgba(180, 74, 74, 0.18);
            }
            QToolButton#flowSwitchModeButton,
            QToolButton#flowSwitchSettingsButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: #f0f3f7;
            }
            QToolButton#flowValveSettingsButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: #f0f3f7;
            }
            QToolButton#flowSwitchModeButton:hover,
            QToolButton#flowSwitchSettingsButton:hover,
            QToolButton#flowValveSettingsButton:hover {
                background: rgba(127, 127, 127, 0.10);
            }
            QToolButton#flowSwitchModeButton:pressed,
            QToolButton#flowSwitchSettingsButton:pressed,
            QToolButton#flowValveSettingsButton:pressed {
                background: rgba(127, 127, 127, 0.18);
            }
            QLabel#flowHeaderLabel {
                color: %(muted)s;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.8px;
            }
            QWidget#flowContent, QWidget#flowEditorContainer {
                background: %(bg)s;
                border: none;
            }
            QTableView#flowControlTable {
                background: %(bg)s;
                border: none;
                border-radius: 0px;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: transparent;
                selection-color: %(fg)s;
                font-size: 11px;
            }
            QTableView#flowControlTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableView#flowControlTable::item {
                border: none;
                padding: 1px 4px;
            }
            QTableView#flowControlTable QComboBox,
            QTableView#flowControlTable QDoubleSpinBox,
            QTableView#flowControlTable QLineEdit,
            QTableView#flowControlTable QToolButton {
                background: transparent;
                border: none;
                padding: 0px 1px;
                margin: 0px;
            }
            QTableView#flowControlTable QComboBox::drop-down {
                border: none;
                background: transparent;
                width: 0px;
            }
            QTableView#flowControlTable QComboBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QTableView#flowControlTable QComboBox::item {
                padding: 0px 4px;
            }
            QTableView#flowControlTable QDoubleSpinBox::up-button,
            QTableView#flowControlTable QDoubleSpinBox::down-button {
                width: 0px;
                border: none;
                background: transparent;
            }
            QTableView#flowControlTable QDoubleSpinBox::up-arrow,
            QTableView#flowControlTable QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QTableView#flowControlTable::item:selected {
                background: transparent;
                background-color: transparent;
            }
            QTableView#flowControlTable::item:selected:active,
            QTableView#flowControlTable::item:selected:!active {
                background: transparent;
                background-color: transparent;
            }
            QTableView#flowControlTable QHeaderView::section {
                background: %(header)s;
                border: none;
                border-right: 1px solid %(border)s;
                border-bottom: 1px solid %(border)s;
                padding: 0px 1px;
                font-size: 10px;
                font-weight: 600;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: %(scroll)s;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: %(scroll_hover)s;
            }
            QSplitter::handle {
                background: %(splitter)s;
            }
            QSplitter::handle:vertical {
                height: 6px;
                margin: 0 4px;
                border-radius: 3px;
            }
            """ % palette
        )

    def _update_time_unit_ui(self, current_seconds: float | None = None) -> None:
        labels = {"s": "s", "min": "min", "h": "h"}
        current_label = labels.get(self._time_unit_mode, "s")
        self.time_unit_toggle.setText(current_label)
        self.time_unit_toggle.setToolTip(
            f"Current display unit: {current_label}. Click to cycle between seconds, minutes, and hours. "
            "Internally and in saved data, times stay in seconds."
        )
        if current_seconds is None:
            current_seconds = self._editor_duration_seconds
        self._apply_duration_display_precision()
        self._suspend_duration_tracking = True
        self.step_duration_spin.blockSignals(True)
        if self._time_unit_mode == "min":
            self.step_duration_spin.setDecimals(1)
            self.step_duration_spin.setRange(0.0, 1440.0)
            self.step_duration_spin.setSingleStep(0.1)
            self.step_duration_spin.setSuffix("")
        elif self._time_unit_mode == "h":
            self.step_duration_spin.setDecimals(2)
            self.step_duration_spin.setRange(0.0, 24.0)
            self.step_duration_spin.setSingleStep(0.01)
            self.step_duration_spin.setSuffix("")
        else:
            self.step_duration_spin.setDecimals(0)
            self.step_duration_spin.setRange(0.0, 86400.0)
            self.step_duration_spin.setSingleStep(1.0)
            self.step_duration_spin.setSuffix("")
        self.step_duration_spin.setValue(round(self._seconds_to_display(current_seconds), self._duration_display_decimals()))
        self.step_duration_spin.blockSignals(False)
        self._suspend_duration_tracking = False
        self.timeline_widget.set_time_unit_mode(self._time_unit_mode)
        self._plan_model.set_time_unit_mode(self._time_unit_mode)
        self._update_experiment_control_headers()
        self._refresh_status_line()

    def _duration_display_decimals(self) -> int:
        if self._time_unit_mode == "min":
            return 1
        if self._time_unit_mode == "h":
            return 2
        return 0

    def _apply_duration_display_precision(self) -> None:
        decimals = self._duration_display_decimals()
        single_step = 1.0 if decimals == 0 else (0.1 if decimals == 1 else 0.01)
        if isinstance(self.step_duration_spin, QDoubleSpinBox):
            self.step_duration_spin.blockSignals(True)
            try:
                self.step_duration_spin.setDecimals(decimals)
                self.step_duration_spin.setSingleStep(single_step)
                self.step_duration_spin.setSuffix("")
                if decimals == 0:
                    self.step_duration_spin.setMaximum(86400.0)
                elif decimals == 1:
                    self.step_duration_spin.setMaximum(1440.0)
                else:
                    self.step_duration_spin.setMaximum(24.0)
            finally:
                self.step_duration_spin.blockSignals(False)

    def _format_seconds_display_value(self, seconds: float) -> str:
        value = max(self._seconds_to_display(seconds), 0.0)
        decimals = self._duration_display_decimals()
        if decimals == 0:
            return f"{int(round(value))}"
        return f"{value:.{decimals}f}"

    def _update_experiment_control_headers(self) -> None:
        unit = self._time_unit_mode
        headers = list(self.PLAN_COLUMNS)
        headers[0] = "Step"
        headers[1] = f"Duration [{unit}]"
        headers[2] = f"start_{unit}"
        headers[3] = f"end_{unit}"
        for channel_index in range(ACTIVE_PUMP_CHANNELS):
            headers[self._flow_rate_column(channel_index)] = f"CH{channel_index + 1}"
            headers[self._direction_column(channel_index)] = f"CH{channel_index + 1} Dir"
            headers[self._tube_column(channel_index)] = f"CH{channel_index + 1} Tube"
        headers[self._valve_column()] = "Valve"
        headers[self._switch_column()] = "Switch"
        headers[self._color_column()] = "Color"
        headers[self._description_column()] = "Comment"
        self._plan_model.set_headers(headers)
        self._configure_experiment_control_table_columns()

    def _configure_experiment_control_table_columns(self) -> None:
        configure_experiment_control_table_columns(self)

    def _default_experiment_control_table_column_widths(self) -> list[int]:
        widths = [36, 92, 0, 0]
        for _channel_index in range(ACTIVE_PUMP_CHANNELS):
            widths.extend([56, 42, 54])
        widths.extend([62, 90, 70, 200])
        return widths

    def _plan_table_column_widths(self) -> list[int]:
        widths: list[int] = []
        for column in range(self.plan_table.columnCount()):
            widths.append(int(self.plan_table.columnWidth(column)))
        return widths

    def _plan_table_header_state(self) -> str:
        header = self.plan_table.horizontalHeader()
        return bytes(header.saveState().toBase64()).decode("ascii")

    def _apply_plan_table_column_widths(self, widths: list[object] | None) -> None:
        defaults = self._default_experiment_control_table_column_widths()
        if not isinstance(widths, list):
            widths = []
        self._suppress_plan_table_layout_save = True
        self.plan_table.horizontalHeader().blockSignals(True)
        try:
            for column in range(self.plan_table.columnCount()):
                width_value = defaults[column] if column < len(defaults) else 68
                if column < len(widths):
                    try:
                        width_value = int(widths[column])
                    except (TypeError, ValueError):
                        pass
                if width_value > 0:
                    self.plan_table.setColumnWidth(column, width_value)
        finally:
            self.plan_table.horizontalHeader().blockSignals(False)
            self._suppress_plan_table_layout_save = False

    def _apply_plan_table_header_state(self, state_value: object) -> bool:
        if not isinstance(state_value, str) or not state_value:
            return False
        try:
            state_bytes = QByteArray.fromBase64(state_value.encode("ascii"))
        except Exception:
            return False
        if state_bytes.isEmpty():
            return False
        header = self.plan_table.horizontalHeader()
        self._suppress_plan_table_layout_save = True
        header.blockSignals(True)
        try:
            restored = bool(header.restoreState(state_bytes))
        finally:
            header.blockSignals(False)
            self._suppress_plan_table_layout_save = False
        return restored

    def _schedule_plan_table_layout_save(self, *_args) -> None:
        if self._suppress_plan_table_layout_save:
            return
        self._plan_table_layout_locked = True
        self._plan_table_layout_save_timer.start()

    def _restore_plan_table_column_widths(self, state: dict[str, object]) -> None:
        header_state = state.get("plan_table_header_state")
        if self._apply_plan_table_header_state(header_state):
            return
        widths = state.get("plan_table_column_widths")
        if isinstance(widths, list) and widths:
            self._apply_plan_table_column_widths(widths)
            return
        self._apply_plan_table_column_widths(self._default_experiment_control_table_column_widths())

    def _fit_plan_table_columns_to_viewport(self) -> None:
        self._run_gui_callback_timed("plan_table_fit", lambda: fit_plan_table_columns_to_viewport(self))

    def _update_plan_table_height(self) -> None:
        update_plan_table_height(self)

    def _experiment_control_editor_splitter_sizes(self) -> list[int]:
        splitter = getattr(self, "_flow_editor_splitter", None)
        if splitter is None:
            return []
        return [int(size) for size in splitter.sizes()]

    def _apply_experiment_control_editor_splitter_sizes(self, sizes: list[object] | None) -> None:
        splitter = getattr(self, "_flow_editor_splitter", None)
        if splitter is None or not isinstance(sizes, list) or len(sizes) < 2:
            return
        parsed_sizes: list[int] = []
        for value in sizes[:2]:
            try:
                parsed_sizes.append(max(int(value), 20))
            except (TypeError, ValueError):
                return
        self._suppress_plan_table_layout_save = True
        try:
            splitter.setSizes(parsed_sizes)
        finally:
            self._suppress_plan_table_layout_save = False
        self._flow_editor_splitter_initialized = True

    def _flow_editor_splitter_sizes(self) -> list[int]:
        return self._experiment_control_editor_splitter_sizes()

    def _apply_flow_editor_splitter_sizes(self, sizes: list[object] | None) -> None:
        self._apply_experiment_control_editor_splitter_sizes(sizes)

    def _on_flow_editor_splitter_moved(self, *_args) -> None:
        self._flow_editor_splitter_initialized = True
        self.save_ui_state()

    def _seconds_to_display(self, seconds: float) -> float:
        if self._time_unit_mode == "min":
            return float(seconds) / 60.0
        if self._time_unit_mode == "h":
            return float(seconds) / 3600.0
        return float(seconds)

    def _display_to_seconds(self, value: float) -> float:
        if self._time_unit_mode == "min":
            return float(value) * 60.0
        if self._time_unit_mode == "h":
            return float(value) * 3600.0
        return float(value)

    def _capture_editor_duration_from_spin(self, value: float) -> None:
        if self._suspend_duration_tracking:
            return
        self._editor_duration_seconds = max(self._display_to_seconds(value), 0.0)

    def _format_duration_for_status(self, seconds: float) -> str:
        seconds = max(float(seconds), 0.0)
        if self._time_unit_mode == "min":
            return f"{seconds / 60.0:.1f} min"
        if self._time_unit_mode == "h":
            return f"{seconds / 3600.0:.2f} h"
        return f"{int(round(seconds))} s"

    def _set_status_message(self, text: str) -> None:
        self._status_message_base = text
        self._refresh_status_line()

    def _show_error(self, message: str) -> None:
        self._set_status_message(message)
        if getattr(self, "_closing", False):
            return
        QMessageBox.critical(self, "Experiment control error", message)

    def _begin_plan_table_edit(self, row: int, column: int) -> None:
        self._plan_table_active_editor = (int(row), int(column))

    def _end_plan_table_edit(self, row: int | None = None, column: int | None = None) -> None:
        active = self._plan_table_active_editor
        if active is None:
            return
        if row is None or column is None or active == (int(row), int(column)):
            self._plan_table_active_editor = None

    def _plan_table_is_editing(self, row: int, column: int) -> bool:
        active = self._plan_table_active_editor
        return active == (int(row), int(column))

    def _refresh_status_line(self) -> None:
        details: list[str] = []
        steps = self._read_experiment_control_steps()
        total_end_s = steps[-1].end_s if steps else 0.0
        if self._plan_running or self._plan_holding or self._plan_paused:
            active_row = self._plan_active_row
            if active_row is not None and 0 <= active_row < len(steps):
                step = steps[active_row]
                step_left_s = max(step.end_s - self._plan_elapsed_s, 0.0)
                plan_left_s = max(total_end_s - self._plan_elapsed_s, 0.0)
                details.append(f"Step left: {self._format_duration_for_status(step_left_s)}")
                details.append(f"Plan left: {self._format_duration_for_status(plan_left_s)}")
        elif steps:
            row = self._selected_experiment_control_row()
            if row is not None and 0 <= row < len(steps):
                step = steps[row]
                details.append(f"Step: {self._format_duration_for_status(step.duration_s)}")
            details.append(f"Plan: {self._format_duration_for_status(total_end_s)}")

        text = self._status_message_base
        if details:
            text = f"{text} | " + " | ".join(details)
        self.connection_status_label.setText(text)

    def _selected_step_start_s(self) -> float | None:
        row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        if row is None or not steps:
            return None
        if not (0 <= row < len(steps)):
            return None
        return float(steps[row].start_s)

    def set_theme(self, theme_mode: str) -> None:
        if theme_mode not in {"light", "dark"}:
            return
        self._theme_mode = theme_mode
        save_app_setting("theme_mode", self._theme_mode)
        self._apply_style()
        self.previous_step_button.setIcon(transport_icon(self._theme_mode, "previous"))
        self.next_step_button.setIcon(transport_icon(self._theme_mode, "next"))
        self.stop_plan_button.setIcon(transport_icon(self._theme_mode, "stop"))
        self._update_experiment_control_toggle_button()
        self.timeline_widget.set_theme(self._theme_mode)
        self.timeline_widget.set_theme_palette(self._theme_palette())
        self.theme_changed.emit(self._theme_mode)

    def _default_color_palette_entries(self) -> list[tuple[str, str]]:
        return list(self.PLAN_COLOR_OPTIONS)

    def _normalize_color_entry(self, name: object, color: object, fallback_index: int) -> tuple[str, str] | None:
        label = str(name).strip() if isinstance(name, str) else ""
        color_text = str(color).strip().upper() if isinstance(color, str) else ""
        if not label:
            label = f"Custom {fallback_index + 1}"
        qcolor = QColor(color_text)
        if not qcolor.isValid():
            return None
        return label, qcolor.name().upper()

    def _load_color_palette_entries(self, state: dict[str, object]) -> list[tuple[str, str]]:
        payload = state.get("color_palette_entries")
        entries: list[tuple[str, str]] = []
        if isinstance(payload, list):
            for index, raw_entry in enumerate(payload):
                if isinstance(raw_entry, dict):
                    entry = self._normalize_color_entry(raw_entry.get("name"), raw_entry.get("color"), index)
                elif isinstance(raw_entry, (list, tuple)) and len(raw_entry) >= 2:
                    entry = self._normalize_color_entry(raw_entry[0], raw_entry[1], index)
                else:
                    entry = None
                if entry is not None:
                    entries.append(entry)
        if entries:
            return entries
        legacy_colors = state.get("custom_plan_colors", [])
        if isinstance(legacy_colors, list) and legacy_colors:
            entries.extend(self._default_color_palette_entries())
            for index, raw_color in enumerate(legacy_colors, start=1):
                if isinstance(raw_color, str) and raw_color.strip():
                    qcolor = QColor(raw_color.strip())
                    if qcolor.isValid():
                        entries.append((f"Custom {index}", qcolor.name().upper()))
            return entries
        return self._default_color_palette_entries()

    def _sync_custom_plan_colors_from_palette(self) -> None:
        self._custom_plan_colors = [color for _name, color in self._color_palette_entries]

    def _refresh_color_palette_widgets(self) -> None:
        current_editor_color = str(self.step_color_combo.currentData() or "")
        self._populate_color_combo(self.step_color_combo)
        if current_editor_color:
            editor_index = self.step_color_combo.findData(current_editor_color)
            if editor_index >= 0:
                self.step_color_combo.setCurrentIndex(editor_index)
        self._plan_model.set_color_options(self._color_palette_entries)
        self._plan_model.set_theme_palette(self._theme_palette())
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.set_color_palette_entries(self._color_palette_entries)
            self.timeline_widget.update()
        self._update_timeline_selection()

    def _save_color_palette_entries(self) -> None:
        self._sync_custom_plan_colors_from_palette()
        self._refresh_color_palette_widgets()
        self.save_ui_state()

    def _populate_color_combo(self, combo: QComboBox) -> None:
        combo.clear()
        options = list(self._color_palette_entries or self._default_color_palette_entries())
        for index, (label, color) in enumerate(options):
            combo.addItem(label, color)
            combo.setItemData(index, QColor(color), Qt.ItemDataRole.BackgroundRole)
            combo.setItemData(index, QColor(self._contrast_text_color(color)), Qt.ItemDataRole.ForegroundRole)
            combo.setItemData(index, int(Qt.AlignmentFlag.AlignCenter), Qt.ItemDataRole.TextAlignmentRole)
        self._update_color_combo_style(combo)
        self._sync_custom_color_controls()

    def _contrast_text_color(self, color: str) -> str:
        qcolor = QColor(color)
        luminance = (
            0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()
        )
        return "#0f1720" if luminance > 150 else "#ffffff"

    def _update_color_combo_style(self, combo: QComboBox) -> None:
        color = combo.currentData()
        if not isinstance(color, str) or not color:
            combo.setStyleSheet("")
            return
        text_color = self._contrast_text_color(color)
        palette = combo.palette()
        qcolor = QColor(color)
        if qcolor.isValid():
            for role in (
                QPalette.ColorRole.Base,
                QPalette.ColorRole.Button,
                QPalette.ColorRole.Window,
            ):
                palette.setColor(role, qcolor)
        text_qcolor = QColor(text_color)
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.WindowText,
        ):
            palette.setColor(role, text_qcolor)
        combo.setPalette(palette)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPalette(palette)
            line_edit.setStyleSheet(
                "QLineEdit {"
                f" background-color: {color};"
                f" color: {text_color};"
                " border: none;"
                " border-radius: 10px;"
                " padding: 0px;"
                " margin: 0px;"
                "}"
            )
        combo.setStyleSheet(
            "QComboBox {"
            f" background: {color};"
            f" color: {text_color};"
            f" border: 1px solid {color};"
            " border-radius: 10px;"
            " padding: 0px 1px;"
            "}"
            "QComboBox QLineEdit {"
            f" background-color: {color};"
            f" color: {text_color};"
            " border: none;"
            " border-radius: 10px;"
            " padding: 0px;"
            " margin: 0px;"
            "}"
            "QComboBox QLineEdit:hover, QComboBox QLineEdit:focus {"
            f" background-color: {color};"
            f" color: {text_color};"
            " border: none;"
            " border-radius: 10px;"
            "}"
            "QComboBox::drop-down { border: none; width: 0px; }"
            "QComboBox::down-arrow { width: 0px; height: 0px; }"
            "QComboBox QAbstractItemView {"
            " selection-background-color: palette(highlight);"
            "}"
        )
        combo.update()

    def _handle_color_selection_changed(self) -> None:
        self._update_color_combo_style(self.step_color_combo)
        self._sync_custom_color_controls()

    def _sync_custom_color_controls(self) -> None:
        selected = self.step_color_combo.currentData()
        palette_colors = {color for _name, color in self._color_palette_entries}
        self.remove_custom_color_button.setEnabled(isinstance(selected, str) and selected in palette_colors)

    def _pick_custom_experiment_control_color(self) -> None:
        initial = QColor(str(self.step_color_combo.currentData() or "#4E79A7"))
        chosen = QColorDialog.getColor(initial, self, "Pick custom plan color")
        if not chosen.isValid():
            return
        color = chosen.name().upper()
        label = f"Custom {len(self._color_palette_entries) + 1}"
        self._color_palette_entries = [entry for entry in self._color_palette_entries if entry[1] != color]
        self._color_palette_entries.append((label, color))
        self._save_color_palette_entries()

    def _remove_selected_custom_color(self) -> None:
        selected = self.step_color_combo.currentData()
        if not isinstance(selected, str):
            return
        answer = QMessageBox.question(
            self,
            "Remove color",
            f"Remove selected color {selected} from the palette?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._color_palette_entries = [entry for entry in self._color_palette_entries if entry[1] != selected]
        if not self._color_palette_entries:
            self._color_palette_entries = self._default_color_palette_entries()
        self._save_color_palette_entries()
        fallback = self._color_palette_entries[0][1]
        index = self.step_color_combo.findData(fallback)
        if index >= 0:
            self.step_color_combo.setCurrentIndex(index)
        self._sync_custom_color_controls()

    def _default_experiment_control_color(self, step_index: int) -> str:
        palette = self._color_palette_entries or self._default_color_palette_entries()
        return palette[step_index % len(palette)][1]

    def _flow_rate_column(self, channel_index: int) -> int:
        return 4 + channel_index * 3

    def _direction_column(self, channel_index: int) -> int:
        return self._flow_rate_column(channel_index) + 1

    def _tube_column(self, channel_index: int) -> int:
        return self._flow_rate_column(channel_index) + 2

    def _valve_column(self) -> int:
        return 4 + ACTIVE_PUMP_CHANNELS * 3

    def _switch_column(self) -> int:
        return self._valve_column() + 1

    def _color_column(self) -> int:
        return self._valve_column() + 2

    def _description_column(self) -> int:
        return self._valve_column() + 3

    def _populate_ports(self, ports: list[object]) -> None:
        _LOGGER.debug("Pump port scan | %d port(s)", len(ports))
        current = self.port_combo.currentData()
        likely_indices: list[tuple[int, int]] = []
        manual_indices: list[int] = []
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for index, port in enumerate(ports):
            label = f"{port.device}  |  {port.description}"
            self.port_combo.addItem(label, port.device)
            if get_port_assignment(port.device) == "pump":
                manual_indices.append(index)
            if is_probable_reglo_port(port):
                likely_indices.append(index)
        self.port_combo.blockSignals(False)

        target = self._last_selected_port or current
        if target is not None:
            index = self.port_combo.findData(target)
            if index >= 0:
                target_port = ports[index] if 0 <= index < len(ports) else None
                if target_port is not None and get_port_assignment(target_port.device) == "pump":
                    self.port_combo.setCurrentIndex(index)
                    return

        if manual_indices:
            self.port_combo.setCurrentIndex(manual_indices[0])
            return

        if target is not None:
            index = self.port_combo.findData(target)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
                return

        if likely_indices:
            self.port_combo.setCurrentIndex(likely_indices[0])
            return

        if self.port_combo.count() == 0:
            self._set_connection_visual(False, "Pump offline. No serial ports found.")
            self.availability_changed.emit(None)
            _LOGGER.warning("Pump port scan found no serial ports.")
        else:
            self.port_combo.setCurrentIndex(-1)
            self._set_connection_visual(False, "Pump offline. Select a port to connect manually.")
            self.availability_changed.emit(None)

    def _populate_valve_ports(self, ports: list[object]) -> None:
        _LOGGER.debug("Valve port scan | %d port(s)", len(ports))
        current = self.valve_port_combo.currentData()
        likely_indices: list[tuple[int, int]] = []
        manual_indices: list[int] = []
        self.valve_port_combo.blockSignals(True)
        self.valve_port_combo.clear()
        for index, port in enumerate(ports):
            label = f"{port.device}  |  {port.description}"
            self.valve_port_combo.addItem(label, port.device)
            if get_port_assignment(port.device) == "valve":
                manual_indices.append(index)
            priority = controller_port_priority(port)
            if priority > 0:
                likely_indices.append((priority, index))
        self.valve_port_combo.blockSignals(False)

        likely_indices.sort(key=lambda item: item[0], reverse=True)
        best_likely_index = likely_indices[0][1] if likely_indices else None
        best_likely_priority = likely_indices[0][0] if likely_indices else 0

        target = self._last_selected_valve_port or current
        if target is not None and best_likely_index is None:
            index = self.valve_port_combo.findData(target)
            if index >= 0:
                self.valve_port_combo.setCurrentIndex(index)
                return
        if target is not None and best_likely_index is not None:
            index = self.valve_port_combo.findData(target)
            if index >= 0:
                target_port = ports[index] if 0 <= index < len(ports) else None
                if target_port is not None and get_port_assignment(target_port.device) == "valve":
                    self.valve_port_combo.setCurrentIndex(index)
                    return

        if manual_indices:
            self.valve_port_combo.setCurrentIndex(manual_indices[0])
            return

        if target is not None:
            index = self.valve_port_combo.findData(target)
            if index >= 0:
                self.valve_port_combo.setCurrentIndex(index)
                return

        if best_likely_index is not None:
            self.valve_port_combo.setCurrentIndex(best_likely_index)
            return

        if self.valve_port_combo.count() == 0:
            self._set_valve_connection_visual(False, "Valve controller offline. No serial ports found.")
            _LOGGER.warning("Valve port scan found no serial ports.")
        else:
            self.valve_port_combo.setCurrentIndex(-1)
            self._set_valve_connection_visual(False, "Valve controller offline. Select a port to connect manually.")

    def _populate_mswitch_ports(self, devices: list[object], *, amf_available: bool) -> None:
        current = self.mswitch_port_combo.currentData()
        if not amf_available:
            self.mswitch_port_combo.blockSignals(True)
            self.mswitch_port_combo.clear()
            self.mswitch_port_combo.addItem("AMFTools not installed", None)
            self.mswitch_port_combo.blockSignals(False)
            self._set_mswitch_connection_visual(False, "AMFTools is not installed. M-Switch unavailable.")
            self.mswitch_connection_toggle_button.setEnabled(False)
            self.mswitch_home_button.setEnabled(False)
            self.mswitch_move_button.setEnabled(False)
            return

        _LOGGER.debug("M-Switch probe scan | %d device(s)", len(devices))
        self.mswitch_port_combo.blockSignals(True)
        self.mswitch_port_combo.clear()
        for index, probe in enumerate(devices):
            label_parts = [probe.port, probe.model]
            if getattr(probe, "serial_number", None):
                label_parts.append(str(probe.serial_number))
            self.mswitch_port_combo.addItem("  |  ".join(label_parts), probe.port)
        self.mswitch_port_combo.blockSignals(False)

        target = self._last_selected_mswitch_port or current
        if target is not None:
            index = self.mswitch_port_combo.findData(target)
            if index >= 0:
                self.mswitch_port_combo.setCurrentIndex(index)
                return
        if self.mswitch_port_combo.count() > 0:
            self.mswitch_port_combo.setCurrentIndex(0)
        else:
            self._set_mswitch_connection_visual(False, "M-Switch offline. No AMF switch discovered.")

    def _start_port_refresh(self) -> None:
        if self._port_refresh_in_progress:
            return
        self._port_refresh_generation += 1
        generation = self._port_refresh_generation
        self._port_refresh_in_progress = True
        _LOGGER.info("Experiment control bootstrap +%.1f ms: port refresh started", (perf_counter() - self._bootstrap_t0) * 1000.0)
        self._update_experiment_control_busy_indicator()
        task = PortRefreshTask(generation)
        self._port_refresh_task = task
        task.signals.finished.connect(self._handle_port_refresh_finished)
        task.signals.failed.connect(self._handle_port_refresh_failed)
        self._thread_pool.start(task)

    def _handle_port_refresh_finished(self, generation: int, payload: object) -> None:
        if generation != self._port_refresh_generation:
            return
        self._port_refresh_task = None
        self._port_refresh_in_progress = False
        _LOGGER.info("Experiment control bootstrap +%.1f ms: port refresh finished", (perf_counter() - self._bootstrap_t0) * 1000.0)
        if not isinstance(payload, PortRefreshData):
            self._update_experiment_control_busy_indicator()
            return
        self._populate_ports(payload.pump_ports)
        self._populate_valve_ports(payload.valve_ports)
        self._populate_mswitch_ports(payload.mswitch_devices, amf_available=payload.amf_tools_available)
        self._update_experiment_control_busy_indicator()
        if self._experiment_control_bootstrap_in_progress:
            return
        self._schedule_startup_device_auto_connect()

    def _handle_port_refresh_failed(self, generation: int, message: str) -> None:
        if generation != self._port_refresh_generation:
            return
        self._port_refresh_task = None
        self._port_refresh_in_progress = False
        self._update_experiment_control_busy_indicator()
        _LOGGER.warning("Experiment control port refresh failed: %s", message)

    def _refresh_ports(self) -> None:
        self._start_port_refresh()

    def refresh_device_ports(self) -> bool:
        if self._port_refresh_in_progress:
            return False
        self._start_port_refresh()
        return True

    def enable_startup_device_auto_connect(self) -> None:
        if self._startup_auto_connect_enabled:
            return
        self._startup_auto_connect_enabled = True
        self._schedule_startup_device_auto_connect()

    def _schedule_startup_device_auto_connect(self) -> bool:
        if not self._auto_connect_devices:
            return False
        if not self._startup_auto_connect_enabled:
            return False
        if self._port_refresh_in_progress or self._experiment_control_bootstrap_in_progress:
            return False
        if self._startup_auto_connect_scheduled:
            return False
        if not self._startup_auto_connect_active:
            self._startup_auto_connect_active = True
            self._startup_auto_connect_queue = list(self.STARTUP_DEVICE_ORDER)
            _LOGGER.info("Startup auto-connect queue initialized: %s", ", ".join(self._startup_auto_connect_queue))
        self._startup_auto_connect_scheduled = True
        QTimer.singleShot(0, self._run_startup_device_auto_connect)
        return True

    def _run_startup_device_auto_connect(self) -> None:
        if not self._startup_auto_connect_scheduled:
            return
        self._startup_auto_connect_scheduled = False
        if not self._auto_connect_devices or not self._startup_auto_connect_enabled:
            self._startup_auto_connect_active = False
            self._startup_auto_connect_stage = None
            self._startup_auto_connect_queue = []
            return
        if self._port_refresh_in_progress or self._experiment_control_bootstrap_in_progress:
            self._schedule_startup_device_auto_connect()
            return
        if self._startup_auto_connect_stage is not None:
            return
        if not self._startup_auto_connect_queue:
            self._startup_auto_connect_active = False
            return
        _LOGGER.info("Startup auto-connect stage starting: %s", self._startup_auto_connect_queue[0])
        self._advance_startup_device_auto_connect()

    def _advance_startup_device_auto_connect(self) -> None:
        if not self._startup_auto_connect_active:
            return
        if self._startup_auto_connect_stage is not None:
            return
        if self._port_refresh_in_progress or self._experiment_control_bootstrap_in_progress:
            self._schedule_startup_device_auto_connect()
            return
        if not self._startup_auto_connect_queue:
            self._startup_auto_connect_active = False
            self._startup_auto_connect_stage = None
            return
        device_key = self._startup_auto_connect_queue[0]
        _LOGGER.info("Startup auto-connect evaluating: %s", device_key)
        if not self._startup_device_ready(device_key):
            _LOGGER.info("Startup auto-connect skipping %s (not ready).", device_key)
            self._finish_startup_device_auto_connect_stage(device_key)
            return
        if self._startup_attempt_device_connect(device_key):
            self._startup_auto_connect_stage = device_key
            _LOGGER.info("Startup auto-connect requested connect for %s.", device_key)
        else:
            _LOGGER.info("Startup auto-connect could not request connect for %s.", device_key)
            self._finish_startup_device_auto_connect_stage(device_key)

    def _finish_startup_device_auto_connect_stage(self, device_key: str) -> None:
        if not self._startup_auto_connect_active:
            return
        if self._startup_auto_connect_queue and self._startup_auto_connect_queue[0] == device_key:
            self._startup_auto_connect_queue.pop(0)
        elif self._startup_auto_connect_stage != device_key:
            return
        self._startup_auto_connect_stage = None
        _LOGGER.info(
            "Startup auto-connect stage finished: %s | remaining=%s",
            device_key,
            ", ".join(self._startup_auto_connect_queue) if self._startup_auto_connect_queue else "none",
        )
        if not self._startup_auto_connect_queue:
            self._startup_auto_connect_active = False
            _LOGGER.info("Startup auto-connect sequence complete.")
            return
        self._schedule_startup_device_auto_connect()

    def _startup_pump_ready(self) -> bool:
        if self._client.is_connected() or self._connect_in_progress:
            return False
        selected_port = self.selected_port()
        return bool(selected_port is not None and self.port_combo.findData(selected_port) >= 0)

    def _startup_valve_ready(self) -> bool:
        if self._valve_client is not None and self._valve_client.is_connected():
            return False
        if self._valve_connect_in_progress or self._valve_connect_task is not None:
            return False
        selected_port = self._selected_valve_port()
        return bool(selected_port is not None and self.valve_port_combo.findData(selected_port) >= 0)

    def _startup_mswitch_ready(self) -> bool:
        if self._mswitch_client is not None and self._mswitch_client.is_connected():
            return False
        if self._mswitch_connect_in_progress or self._mswitch_connect_task is not None:
            return False
        selected_port = self._selected_mswitch_port()
        return bool(selected_port is not None and self.mswitch_port_combo.findData(selected_port) >= 0)

    def _startup_device_ready(self, device_key: str) -> bool:
        if device_key == "pump":
            return self._startup_pump_ready()
        if device_key == "valve":
            return self._startup_valve_ready()
        if device_key == "mswitch":
            return self._startup_mswitch_ready()
        return False

    def _startup_attempt_device_connect(self, device_key: str) -> bool:
        if device_key == "pump":
            return self.connect_best_pump_controller()
        if device_key == "valve":
            return self.connect_best_valve_controller()
        if device_key == "mswitch":
            return self.connect_best_mswitch_controller()
        return False

    def _remember_selected_port(self, _: str) -> None:
        self._last_selected_port = self.selected_port()

    def _remember_selected_valve_port(self, _: str) -> None:
        self._last_selected_valve_port = self._selected_valve_port()

    def _selected_valve_port(self) -> str | None:
        data = self.valve_port_combo.currentData()
        return str(data) if data else None

    def _toggle_valve_connection(self) -> None:
        if self._valve_client is not None and self._valve_client.is_connected():
            self._disconnect_valve_controller()
        else:
            self._connect_selected_valve_port()

    def _connect_selected_valve_port(self) -> None:
        port = self._selected_valve_port()
        if not port:
            self._show_info("Select a serial port first.")
            return
        if self._valve_connect_task is not None:
            return
        self._valve_connect_in_progress = True
        self._set_valve_connection_visual(False, f"Connecting valve controller on {port}...")
        _LOGGER.info("Connecting valve controller on %s", port)
        task = ValveConnectTask(port)
        task.signals.finished.connect(self._handle_valve_connect_finished)
        self._valve_connect_task = task
        self._thread_pool.start(task)

    def _handle_valve_connect_finished(self, payload: object) -> None:
        self._valve_connect_in_progress = False
        self._valve_connect_task = None
        if not isinstance(payload, tuple) or len(payload) != 4:
            self._set_valve_connection_visual(False, "Valve connect failed.")
            self.valve_availability_changed.emit(None)
            _LOGGER.warning("Valve connect finished with unexpected payload.")
            return
        port, client, probe, error = payload
        if client is None or probe is None:
            if self._valve_client is not None:
                self._valve_client.close()
            self._valve_client = None
            self._valve_probe = None
            self._set_valve_connection_visual(False, f"Valve connect failed on {port}: {error}")
            self.valve_availability_changed.emit(None)
            _LOGGER.warning("Valve connect failed on %s: %s", port, error)
            self._finish_startup_device_auto_connect_stage("valve")
            return
        self._valve_client = client
        self._valve_probe = probe
        claim_port(probe.port, "Experiment Control / Valve")
        self._set_valve_connection_visual(True, f"Connected to {probe.model} [{probe.controller_type}] on {probe.port}.")
        self.valve_availability_changed.emit(probe)
        _LOGGER.info("Valve controller connected | model=%s type=%s port=%s", probe.model, probe.controller_type, probe.port)
        self._finish_startup_device_auto_connect_stage("valve")

    def connect_best_valve_controller(self) -> bool:
        if self._port_refresh_in_progress:
            return False
        if self._valve_client is not None and self._valve_client.is_connected():
            return False
        if self._valve_connect_in_progress or self._valve_connect_task is not None:
            return False
        selected = self._selected_valve_port()
        if not selected:
            return False
        if self.valve_port_combo.findData(selected) < 0:
            return False
        self._connect_selected_valve_port()
        return True

    def _disconnect_valve_controller(self) -> None:
        port = getattr(self._valve_probe, "port", None)
        if self._valve_client is not None:
            self._valve_client.close()
        self._valve_client = None
        self._valve_probe = None
        if port:
            release_port(port, "Experiment Control / Valve")
        self._set_valve_connection_visual(False, "Valve controller disconnected.")
        self.valve_availability_changed.emit(None)
        _LOGGER.info("Valve controller disconnected.")

    def _set_valve_connection_visual(self, connected: bool, text: str) -> None:
        color = "#2e7d32" if connected else "#9aa8b6"
        self.valve_connection_dot.setStyleSheet(
            f"background:{color}; border-radius:5px; min-width:10px; min-height:10px;"
        )
        self.valve_connection_status_label.setText(text)
        self.valve_connection_toggle_button.setText("Disconnect" if connected else "Connect")
        self.valve_connection_toggle_button.setEnabled(True)

    def _auto_connect_valve(self) -> None:
        if not self._auto_connect_devices:
            return
        if not self._startup_auto_connect_enabled:
            return
        if self._port_refresh_in_progress:
            return
        if self._valve_client is not None and self._valve_client.is_connected():
            return
        if self._valve_connect_task is not None:
            return
        selected = self._selected_valve_port()
        if selected is not None and self.valve_port_combo.findData(selected) >= 0:
            _LOGGER.debug("Auto-connecting valve controller on %s", selected)
            self._connect_selected_valve_port()

    def _refresh_valve_ports(self) -> None:
        self._start_port_refresh()

    def _refresh_mswitch_ports(self) -> None:
        self._start_port_refresh()

    def _remember_selected_mswitch_port(self, _: str) -> None:
        self._last_selected_mswitch_port = self._selected_mswitch_port()

    def _selected_mswitch_port(self) -> str | None:
        data = self.mswitch_port_combo.currentData()
        return str(data) if data else None

    def _toggle_mswitch_connection(self) -> None:
        if self._mswitch_client is not None and self._mswitch_client.is_connected():
            self._disconnect_mswitch_controller()
        else:
            self._connect_selected_mswitch_port()

    def _connect_selected_mswitch_port(self) -> None:
        port = self._selected_mswitch_port()
        if not port:
            self._show_info("Select an AMF switch port first.")
            return
        if self._mswitch_connect_in_progress:
            return
        self._mswitch_connect_in_progress = True
        self._set_mswitch_connection_visual(False, f"Connecting M-Switch on {port}...")
        _LOGGER.info("Connecting M-Switch on %s", port)
        try:
            client, probe = connect_mswitch_port(port)
        except Exception as exc:
            if self._mswitch_client is not None:
                self._mswitch_client.close()
            self._mswitch_client = None
            self._mswitch_probe = None
            self._mswitch_connect_in_progress = False
            self._set_mswitch_connection_visual(False, f"M-Switch connect failed on {port}: {exc}")
            self.mswitch_availability_changed.emit(None)
            _LOGGER.error("M-Switch connect failed on %s: %s", port, exc)
            self._finish_startup_device_auto_connect_stage("mswitch")
            return
        self._mswitch_client = client
        self._mswitch_probe = probe
        claim_port(probe.port, "Experiment Control / M-Switch")
        self.mswitch_availability_changed.emit(probe)
        self._set_mswitch_connection_visual(True, f"Connected to {probe.model} on {probe.port}.")
        self._update_mswitch_state_from_probe()
        self._ensure_mswitch_homed()
        self._mswitch_connect_in_progress = False
        _LOGGER.info("M-Switch connected | model=%s port=%s", probe.model, probe.port)
        self._finish_startup_device_auto_connect_stage("mswitch")

    def _disconnect_mswitch_controller(self) -> None:
        port = getattr(self._mswitch_probe, "port", None)
        if self._mswitch_client is not None:
            self._mswitch_client.close()
        self._mswitch_client = None
        self._mswitch_probe = None
        if port:
            release_port(port, "Experiment Control / M-Switch")
        self._mswitch_connect_in_progress = False
        self._mswitch_connect_task = None
        self._set_mswitch_connection_visual(False, "M-Switch disconnected.")
        self.mswitch_availability_changed.emit(None)
        _LOGGER.info("M-Switch disconnected.")

    def _set_mswitch_connection_visual(self, connected: bool, text: str) -> None:
        color = "#2e7d32" if connected else "#9aa8b6"
        self.mswitch_connection_dot.setStyleSheet(
            f"background:{color}; border-radius:5px; min-width:10px; min-height:10px;"
        )
        self.mswitch_connection_status_label.setText(text)
        self.mswitch_connection_toggle_button.setText("Disconnect" if connected else "Connect")
        self.mswitch_connection_toggle_button.setEnabled(True)
        self.mswitch_home_button.setEnabled(connected)
        self.mswitch_move_button.setEnabled(connected)
        self.mswitch_target_spin.setEnabled(connected)

    def _auto_connect_mswitch(self) -> None:
        if not self._auto_connect_devices:
            return
        if not self._startup_auto_connect_enabled:
            return
        if self._port_refresh_in_progress:
            return
        if self._mswitch_client is not None and self._mswitch_client.is_connected():
            return
        selected = self._selected_mswitch_port()
        if selected is not None:
            _LOGGER.debug("Auto-connecting M-Switch on %s", selected)
            self._connect_selected_mswitch_port()

    def connect_best_mswitch_controller(self) -> bool:
        if self._port_refresh_in_progress:
            return False
        if self._mswitch_client is not None and self._mswitch_client.is_connected():
            return False
        if self._mswitch_connect_in_progress:
            return False
        selected = self._selected_mswitch_port()
        if selected is None:
            return False
        if self.mswitch_port_combo.findData(selected) < 0:
            return False
        self._connect_selected_mswitch_port()
        return True

    def _update_mswitch_state_from_probe(self) -> None:
        if self._mswitch_client is None or not self._mswitch_client.is_connected():
            return
        try:
            current_position = self._mswitch_client.get_position()
            port_count = self._mswitch_client.get_port_count()
            self.mswitch_target_spin.setRange(1, max(port_count, 1))
            self.mswitch_target_spin.setValue(max(1, min(current_position, max(port_count, 1))))
            self.mswitch_current_value.setText(f"Port {current_position} / {port_count}")
        except Exception as exc:
            _LOGGER.warning("Could not refresh M-Switch state: %s", exc)

    def _ensure_mswitch_homed(self) -> bool:
        if self._mswitch_client is None or not self._mswitch_client.is_connected():
            return False
        try:
            if self._mswitch_client.is_homed():
                return True
        except Exception as exc:
            _LOGGER.warning("Could not read M-Switch homing state: %s", exc)
            return False
        try:
            _LOGGER.info("Homing M-Switch before use.")
            self._set_mswitch_connection_visual(True, "Homing M-Switch...")
            self._mswitch_client.home(block=True)
            self._update_mswitch_state_from_probe()
            self._set_mswitch_connection_visual(True, f"M-Switch homed on {self._selected_mswitch_port() or 'current port'}.")
            _LOGGER.info("M-Switch homed.")
            return True
        except Exception as exc:
            self._set_mswitch_connection_visual(True, f"M-Switch home failed: {exc}")
            _LOGGER.error("M-Switch home failed: %s", exc)
            return False

    def _home_mswitch(self) -> None:
        if self._mswitch_client is None or not self._mswitch_client.is_connected():
            return
        self._ensure_mswitch_homed()

    def _move_mswitch_to_target(self) -> None:
        if self._mswitch_client is None or not self._mswitch_client.is_connected():
            return
        target = int(self.mswitch_target_spin.value())
        try:
            self._set_mswitch_connection_visual(True, f"Moving M-Switch to port {target}...")
            self._move_mswitch_and_verify(target)
            _LOGGER.info("M-Switch moved to port %s", target)
        except Exception as exc:
            self._set_mswitch_connection_visual(True, f"M-Switch move failed: {exc}")
            _LOGGER.error("M-Switch move failed: %s", exc)

    def _move_mswitch_and_verify(self, target: int) -> bool:
        if self._mswitch_client is None or not self._mswitch_client.is_connected():
            return False
        target = max(min(int(target), 12), 1)
        self._mswitch_client.move_to(target, block=True)
        self._update_mswitch_state_from_probe()
        try:
            current = int(self._mswitch_client.get_position())
        except Exception as exc:
            _LOGGER.warning("Could not verify M-Switch position after move | target=%s error=%s", target, exc)
            self._set_mswitch_connection_visual(True, f"M-Switch moved to port {target}.")
            return True
        if current != target:
            _LOGGER.warning("M-Switch position mismatch | target=%s actual=%s", target, current)
            self._set_mswitch_connection_visual(True, f"M-Switch at port {current} (requested {target}).")
        else:
            self._set_mswitch_connection_visual(True, f"M-Switch moved to port {target}.")
        return True

    def selected_port(self) -> str | None:
        data = self.port_combo.currentData()
        return str(data) if data else None

    def _probe_selected_port(self) -> None:
        port = self.selected_port()
        if not port:
            self._show_info("Select a serial port first.")
            return
        try:
            probe = probe_pump_port(port)
        except Exception as exc:
            self._probe = None
            self._clear_probe_labels()
            self._set_connection_visual(False, f"Probe failed on {port}: {exc}")
            _LOGGER.error("Pump probe failed on %s: %s", port, exc)
            return

        self._probe = probe
        self._apply_probe(probe)
        self._set_connection_visual(False, f"Pump discovered on {probe.port}.")
        _LOGGER.info("Pump probe discovered | model=%s port=%s", probe.model, probe.port)

    def _connect_selected_port(self) -> None:
        port = self.selected_port()
        if not port:
            self._show_info("Select a serial port first.")
            return
        if self._connect_in_progress:
            return
        self._connect_generation += 1
        generation = self._connect_generation
        self._connect_in_progress = True
        self._set_connection_visual(False, f"Connecting pump on {port}...")
        _LOGGER.info("Connecting pump on %s", port)
        task = PumpConnectTask(generation, port)
        task.signals.finished.connect(self._handle_connect_probe_success)
        task.signals.failed.connect(self._handle_connect_probe_failure)
        self._connect_task = task
        self._thread_pool.start(task)

    def _disconnect_pump(self) -> None:
        port = getattr(self._probe, "port", None)
        self._connect_generation += 1
        self._connect_in_progress = False
        self._client.close()
        self._probe = None
        if port:
            release_port(port, "Experiment Control / Pump")
        self._set_connection_visual(False, "Pump disconnected.")
        self.availability_changed.emit(None)
        _LOGGER.info("Pump disconnected.")

    def _toggle_connection(self) -> None:
        if self._connect_in_progress:
            return
        if self._client.is_connected():
            self._disconnect_pump()
        else:
            self._connect_selected_port()

    def _handle_connect_probe_success(self, generation: int, probe: object) -> None:
        if generation != self._connect_generation:
            return
        self._connect_task = None
        self._connect_in_progress = False
        if not isinstance(probe, PumpProbe):
            self._set_connection_visual(False, "Pump probe returned unexpected data.")
            return
        try:
            self._client.connect(probe.port)
        except Exception as exc:
            self._client.close()
            self._set_connection_visual(False, f"Connect failed on {probe.port}: {exc}")
            _LOGGER.error("Pump connect failed on %s: %s", probe.port, exc)
            self._finish_startup_device_auto_connect_stage("pump")
            return
        claim_port(probe.port, "Experiment Control / Pump")
        self._probe = probe
        self._apply_probe(probe)
        self._set_connection_visual(True, f"Connected to {probe.model} on {probe.port}.")
        self.availability_changed.emit(probe)
        _LOGGER.info("Pump connected | model=%s port=%s", probe.model, probe.port)
        self._finish_startup_device_auto_connect_stage("pump")

    def _handle_connect_probe_failure(self, generation: int, message: str) -> None:
        if generation != self._connect_generation:
            return
        self._connect_task = None
        self._connect_in_progress = False
        self._probe = None
        self._clear_probe_labels()
        port = self.selected_port() or "selected port"
        self._set_connection_visual(False, f"Connect failed on {port}: {message}")
        self.availability_changed.emit(None)
        _LOGGER.error("Pump connect failed on %s: %s", port, message)
        self._finish_startup_device_auto_connect_stage("pump")

    def _apply_probe(self, probe: PumpProbe) -> None:
        self.protocol_value.setText(probe.protocol_version)
        self.model_value.setText(probe.model)
        self.serial_value.setText(probe.serial_number)
        self.channels_value.setText(str(probe.channel_count))

    def _clear_probe_labels(self) -> None:
        for label in (self.protocol_value, self.model_value, self.serial_value, self.channels_value):
            label.setText("-")

    def _set_connection_visual(self, connected: bool, text: str) -> None:
        color = "#2e7d32" if connected else "#9aa8b6"
        self.connection_dot.setStyleSheet(
            f"background:{color}; border-radius:5px; min-width:10px; min-height:10px;"
        )
        self._status_message_base = text
        self._refresh_status_line()
        self.connection_toggle_button.setText("Disconnect" if connected else "Connect")
        self.connection_toggle_button.setEnabled(not self._connect_in_progress or connected)

    def synchronize_device_connections(self) -> None:
        if self._connection_sync_in_progress:
            return
        if self._connect_in_progress or self._valve_connect_in_progress or self._mswitch_connect_in_progress:
            return
        self._connection_sync_in_progress = True
        try:
            available_ports = {
                str(port.device).strip()
                for port in SerialController.list_ports()
                if str(getattr(port, "device", "") or "").strip()
            }
            owner_snapshot = snapshot_port_ownership()

            pump_port = str(getattr(self._probe, "port", "") or "").strip()
            if self._client.is_connected():
                if not pump_port or pump_port not in available_ports or "Experiment Control / Pump" not in owner_snapshot.get(pump_port, ""):
                    _LOGGER.warning("Pump controller failed health check; disconnecting stale connection on %s.", pump_port or "unknown")
                    self._disconnect_pump()
            elif pump_port and pump_port in available_ports:
                release_port(pump_port, "Experiment Control / Pump")

            valve_port = str(getattr(self._valve_probe, "port", "") or "").strip()
            if self._valve_client is not None and self._valve_client.is_connected():
                if not valve_port or valve_port not in available_ports or "Experiment Control / Valve" not in owner_snapshot.get(valve_port, ""):
                    _LOGGER.warning("Valve controller failed health check; disconnecting stale connection on %s.", valve_port or "unknown")
                    self._disconnect_valve_controller()
            elif valve_port and valve_port in available_ports:
                release_port(valve_port, "Experiment Control / Valve")

            mswitch_port = str(getattr(self._mswitch_probe, "port", "") or "").strip()
            if self._mswitch_client is not None and self._mswitch_client.is_connected():
                if not mswitch_port or mswitch_port not in available_ports or "Experiment Control / M-Switch" not in owner_snapshot.get(mswitch_port, ""):
                    _LOGGER.warning("M-Switch failed health check; disconnecting stale connection on %s.", mswitch_port or "unknown")
                    self._disconnect_mswitch_controller()
            elif mswitch_port and mswitch_port in available_ports:
                release_port(mswitch_port, "Experiment Control / M-Switch")
        finally:
            self._connection_sync_in_progress = False

    def _show_info(self, message: str) -> None:
        _LOGGER.info("%s", message)
        QMessageBox.information(self, "Experiment control", message)

    def _show_pump_info(self) -> None:
        details = (
            f"Model: {self.model_value.text()}\n"
            f"Serial: {self.serial_value.text()}\n"
            f"Channels: {self.channels_value.text()}\n"
            f"Protocol: {self.protocol_value.text()}"
        )
        self._show_info(details)

    def _auto_connect_pump(self) -> None:
        if not self._auto_connect_devices:
            return
        if not self._startup_auto_connect_enabled:
            return
        if self._port_refresh_in_progress:
            return
        if self._probe is not None:
            self._set_connection_visual(False, "Connecting pump...")
            self._connect_selected_port()
            return
        selected = self.selected_port()
        if selected and self.port_combo.findData(selected) >= 0:
            self._set_connection_visual(False, "Connecting pump...")
            self._connect_selected_port()

    def connect_best_pump_controller(self) -> bool:
        if self._port_refresh_in_progress:
            return False
        if self._client.is_connected() or self._connect_in_progress:
            return False
        selected = self.selected_port()
        if selected is None or self.port_combo.findData(selected) < 0:
            return False
        self._connect_selected_port()
        return True

    def _set_manual_uniform_mode(self, enabled: bool) -> None:
        self.manual_uniform_button.setText("=" if enabled else "≠")
        self.manual_uniform_button.setToolTip(
            "Shared direction and tube for all channels." if enabled
            else "Per-channel direction and tube settings are visible."
        )
        self._sync_detail_visibility()
        self._apply_shared_manual_settings()

    def _apply_shared_manual_settings(self, *_args) -> None:
        if not self.manual_uniform_button.isChecked():
            return
        direction = self._direction_button_value(self.shared_direction_button)
        tube_mm = self.shared_tube_spin.value()
        for button in self.manual_direction_buttons:
            self._set_direction_button(button, direction)
        for spin in self.manual_tube_spins:
            spin.blockSignals(True)
            spin.setValue(tube_mm)
            spin.blockSignals(False)

    def _sync_experiment_control_tube_columns(self) -> None:
        sync_experiment_control_tube_columns(self)

    def _set_experiment_control_details_visible(self, visible: bool) -> None:
        self._show_plan_details = visible
        self._sync_detail_visibility()
        self._refresh_experiment_control_view()
        self.save_ui_state()

    def _experiment_control_pause_row_visible(self) -> bool:
        return False

    def _set_experiment_control_pause_row_visible(self, visible: bool) -> None:
        _ = visible
        if hasattr(self, "pause_table"):
            self.pause_table.setVisible(False)

    def _experiment_control_table_row_offset(self) -> int:
        return 0

    def _table_row_from_plan_row(self, plan_row: int) -> int:
        return max(int(plan_row), 0)

    def _plan_row_from_table_row(self, table_row: int) -> int | None:
        if table_row < 0:
            return None
        return table_row

    def _selected_table_row(self) -> int | None:
        if self.plan_table.selectionMode() != QAbstractItemView.SelectionMode.NoSelection:
            selected_rows = sorted({index.row() for index in self.plan_table.selectedIndexes() if index.isValid()})
            if selected_rows:
                row = selected_rows[0]
                if 0 <= row < self.plan_table.rowCount():
                    return row
        row = self.plan_table.currentRow()
        if row < 0 or row >= self.plan_table.rowCount():
            return None
        return row

    def _selected_pause_row(self) -> bool:
        return False

    def _is_pause_flow_step(self, step: PumpPlanStep | None) -> bool:
        if step is None:
            return False
        if int(step.step) != 0:
            return False
        if abs(float(step.duration_s)) > 1e-9:
            return False
        if str(step.description or "").strip().casefold() != "pause":
            return False
        if str(step.valve or "").strip().casefold() != "open":
            return False
        if int(step.switch_position) != 1:
            return False
        return all(
            abs(float(channel.flow_ul_min)) <= 1e-9
            and str(channel.direction or "").strip().upper() in {"", "OFF", "CW"}
            for channel in step.channels[:ACTIVE_PUMP_CHANNELS]
        )

    def _strip_pause_flow_step(self, steps: list[PumpPlanStep]) -> list[PumpPlanStep]:
        if not steps:
            return []
        if self._is_pause_flow_step(steps[0]):
            return [deepcopy(step) for step in steps[1:]]
        return [deepcopy(step) for step in steps]

    def _serialize_experiment_control_pause_template(self) -> dict[str, object]:
        step = self._experiment_control_pause_template
        return {
            "duration_s": float(step.duration_s),
            "color": step.color,
            "valve": step.valve,
            "switch_position": int(step.switch_position),
            "description": step.description,
            "channels": [
                {"flow_ul_min": float(channel.flow_ul_min), "direction": channel.direction}
                for channel in step.channels[:ACTIVE_PUMP_CHANNELS]
            ],
        }

    def _deserialize_experiment_control_pause_template(self, payload: object) -> PumpPlanStep:
        if not isinstance(payload, dict):
            return PumpPlanStep(
                step=0,
                duration_s=0.0,
                color=self._default_experiment_control_color(0),
                valve="Open",
                switch_position=1,
                description="Pause",
                channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
            )
        raw_channels = payload.get("channels", [])
        channels: list[PumpChannelStep] = []
        if isinstance(raw_channels, list):
            for raw_channel in raw_channels[:ACTIVE_PUMP_CHANNELS]:
                if isinstance(raw_channel, dict):
                    channels.append(
                        PumpChannelStep(
                            flow_ul_min=max(_safe_float(raw_channel.get("flow_ul_min", 0.0)), 0.0),
                            direction=str(raw_channel.get("direction", "OFF") or "OFF"),
                        )
                    )
        while len(channels) < ACTIVE_PUMP_CHANNELS:
            channels.append(PumpChannelStep())
        return PumpPlanStep(
            step=0,
            duration_s=max(_safe_float(payload.get("duration_s", 0.0)), 0.0),
            color=str(payload.get("color", self._default_experiment_control_color(0)) or self._default_experiment_control_color(0)),
            valve=str(payload.get("valve", "Open") or "Open"),
            switch_position=max(min(_safe_int(payload.get("switch_position", 1), 1), 12), 1),
            description=str(payload.get("description", "Pause") or "Pause"),
            channels=channels,
        )

    def _pause_row_step(self) -> PumpPlanStep:
        return deepcopy(self._experiment_control_pause_template)

    def _refresh_pause_row_view(self) -> None:
        return

    def _set_pause_table_item(self, row: int, column: int, text: str, editable: bool = True) -> None:
        item = self.pause_table.item(row, column)
        if item is None:
            item = QTableWidgetItem(text)
            self.pause_table.setItem(row, column, item)
        else:
            item.setText(text)
        flags = Qt.ItemFlag.ItemIsEnabled
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)

    def _set_pause_table_time_item(self, row: int, column: int, seconds: float, editable: bool) -> None:
        self._set_pause_table_item(row, column, self._format_seconds_display_value(seconds), editable=editable)

    def _select_experiment_control_plan_row(self, plan_row: int | None) -> None:
        if plan_row is None:
            return
        table_row = self._table_row_from_plan_row(plan_row)
        if 0 <= table_row < self.plan_table.rowCount():
            self.plan_table.selectRow(table_row)

    def _install_flow_navigation_filter(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setProperty("flow_navigation", True)
        widget.installEventFilter(self)
        viewport = getattr(widget, "viewport", None)
        if callable(viewport):
            try:
                child_viewport = viewport()
            except Exception:
                child_viewport = None
            if child_viewport is not None:
                child_viewport.setProperty("flow_navigation", True)
                child_viewport.installEventFilter(self)
        line_edit = getattr(widget, "lineEdit", None)
        if callable(line_edit):
            try:
                child_line_edit = line_edit()
            except Exception:
                child_line_edit = None
            if child_line_edit is not None:
                child_line_edit.setProperty("flow_navigation", True)
                child_line_edit.installEventFilter(self)

    def _install_click_to_open_combo_filter(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setProperty("open_popup_on_click", True)
        widget.installEventFilter(self)
        viewport = getattr(widget, "viewport", None)
        if callable(viewport):
            try:
                child_viewport = viewport()
            except Exception:
                child_viewport = None
            if child_viewport is not None:
                child_viewport.setProperty("open_popup_on_click", True)
                child_viewport.installEventFilter(self)
        line_edit = getattr(widget, "lineEdit", None)
        if callable(line_edit):
            try:
                child_line_edit = line_edit()
            except Exception:
                child_line_edit = None
            if child_line_edit is not None:
                child_line_edit.setProperty("open_popup_on_click", True)
                child_line_edit.installEventFilter(self)

    def _install_table_wheel_scroll_filter(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setProperty("flow_wheel_scroll", True)
        widget.installEventFilter(self)
        viewport = getattr(widget, "viewport", None)
        if callable(viewport):
            try:
                child_viewport = viewport()
            except Exception:
                child_viewport = None
            if child_viewport is not None:
                child_viewport.setProperty("flow_wheel_scroll", True)
                child_viewport.installEventFilter(self)
        line_edit = getattr(widget, "lineEdit", None)
        if callable(line_edit):
            try:
                child_line_edit = line_edit()
            except Exception:
                child_line_edit = None
            if child_line_edit is not None and not isinstance(widget, QDoubleSpinBox):
                child_line_edit.setProperty("flow_wheel_scroll", True)
                child_line_edit.installEventFilter(self)

    def _combo_popup_target(self, widget: object) -> QComboBox | None:
        candidate = widget
        while isinstance(candidate, QWidget):
            if isinstance(candidate, QComboBox):
                return candidate
            candidate = candidate.parent()
        return None

    def _flow_table_cell_for_widget(self, widget: object) -> tuple[int, int] | None:
        candidate = widget
        while isinstance(candidate, QWidget):
            row_value = candidate.property("flow_row")
            column_value = candidate.property("flow_column")
            if isinstance(row_value, int) and isinstance(column_value, int):
                return row_value, column_value
            candidate = candidate.parent()
        return None

    def _wheel_event_index_for_plan_table(self, obj: object, event) -> QModelIndex | None:
        viewport = self.plan_table.viewport()
        if viewport is None:
            return None
        if obj is viewport:
            return self.plan_table.indexAt(event.position().toPoint())
        global_pos = getattr(event, "globalPosition", None)
        if callable(global_pos):
            return self.plan_table.indexAt(viewport.mapFromGlobal(global_pos().toPoint()))
        return QModelIndex()

    def _cycle_plan_table_cell_by_wheel(self, index: QModelIndex, wheel_delta: int) -> bool:
        if not index.isValid() or wheel_delta == 0:
            return False
        if index.row() != self.plan_table.currentRow():
            return False
        model = self.plan_table.model()
        if model is None:
            return False
        cycle_delta = 1 if wheel_delta > 0 else -1
        if index.column() == 1:
            step = 10.0
            if self._time_unit_mode == "min":
                step = 0.1
            elif self._time_unit_mode == "h":
                step = 0.1
            raw_value = index.data(Qt.ItemDataRole.EditRole)
            try:
                value = float(raw_value) if raw_value is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            return bool(model.setData(index, max(value + (cycle_delta * step), 0.0), Qt.ItemDataRole.EditRole))
        flow_start = 4
        channel_count = ACTIVE_PUMP_CHANNELS
        if flow_start <= index.column() < flow_start + channel_count * 3:
            offset = index.column() - flow_start
            if offset % 3 == 0:
                raw_value = index.data(Qt.ItemDataRole.EditRole)
                try:
                    value = int(float(raw_value)) if raw_value is not None else 0
                except (TypeError, ValueError):
                    value = 0
                return bool(model.setData(index, max(value + cycle_delta, 0), Qt.ItemDataRole.EditRole))
        if index.column() == self._switch_column() and hasattr(model, "cycle_switch"):
            handled = bool(model.cycle_switch(index.row(), cycle_delta))
        elif index.column() == self._color_column() and hasattr(model, "cycle_color"):
            handled = bool(model.cycle_color(index.row(), cycle_delta))
        else:
            handled = False
        if handled:
            self.plan_table.viewport().update()
        return handled

    def _experiment_control_index_from_mouse_event(self, obj: object, event) -> QModelIndex | None:
        model = self.plan_table.model()
        if model is None:
            return None
        viewport = self.plan_table.viewport()
        if viewport is None:
            return None
        index = QModelIndex()
        if obj is viewport:
            index = self.plan_table.indexAt(event.position().toPoint())
        else:
            global_pos = getattr(event, "globalPosition", None)
            if callable(global_pos):
                index = self.plan_table.indexAt(viewport.mapFromGlobal(global_pos().toPoint()))
        if index.isValid():
            return index
        cell = self._flow_table_cell_for_widget(obj)
        if cell is None:
            return None
        row, column = cell
        if 0 <= row < model.rowCount() and 0 <= column < model.columnCount():
            candidate = model.index(row, column)
            if candidate.isValid():
                return candidate
        return None

    def _experiment_control_column_kind(self, column: int) -> str | None:
        if column == 0:
            return "step"
        if column == 1:
            return "duration"
        if column in {2, 3}:
            return "time"
        for channel_index in range(ACTIVE_PUMP_CHANNELS):
            if column == self._flow_rate_column(channel_index):
                return "flow"
            if column == self._direction_column(channel_index):
                return "direction"
            if column == self._tube_column(channel_index):
                return "tube"
        if column == self._valve_column():
            return "valve"
        if column == self._switch_column():
            return "switch"
        if column == self._color_column():
            return "color"
        if column == self._description_column():
            return "comment"
        return None

    def _update_experiment_control_edit_mode_button(self) -> None:
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            self._experiment_control_edit_mode = bool(controller.edit_mode)
        self.apply_step_button.setChecked(self._experiment_control_edit_mode)
        self._set_experiment_control_edit_mode_button_icon(self._experiment_control_edit_mode)
        self.apply_step_button.setToolTip(
            "Table edit mode is active. Copy, paste, and multi-selection are enabled."
            if self._experiment_control_edit_mode
            else "Enable table edit mode for multi-cell selection, copy/paste, and row moves."
        )

    def _set_experiment_control_edit_mode_button_icon(self, active: bool) -> None:
        color = QColor("#ffd84d" if active else "#8a98a8")
        self.apply_step_button.setIcon(tint_tabler_icon(flow_tabler_icon("edit"), color))


    def _widget_or_ancestor_has_focus(self, widget: QWidget, types: tuple[type, ...]) -> bool:
        candidate: QWidget | None = widget
        while isinstance(candidate, QWidget):
            if isinstance(candidate, types) and candidate.hasFocus():
                return True
            candidate = candidate.parent() if isinstance(candidate.parent(), QWidget) else None
        return False

    def _scroll_plan_table_by_wheel(self, wheel_delta: int) -> bool:
        if wheel_delta == 0:
            return False
        scrollbar = self.plan_table.verticalScrollBar()
        step = scrollbar.singleStep() or max(self.plan_table.rowHeight(max(self.plan_table.currentRow(), 0)), 1)
        scrollbar.setValue(scrollbar.value() - int(step * wheel_delta / 120))
        return True

    def _normalize_experiment_control_view_mode(self, mode: object) -> str:
        normalized = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"full", "table_timeline", "timeline"}:
            return normalized
        if normalized in {"tabletimeline", "tableplus_timeline", "table_plus_timeline"}:
            return "table_timeline"
        return "full"

    def _load_experiment_control_view_mode_sizes(self, state: dict[str, object]) -> dict[str, list[int]]:
        payload = state.get("experiment_control_view_mode_sizes")
        sizes: dict[str, list[int]] = {}
        if not isinstance(payload, dict):
            return sizes
        for raw_mode in ("full", "table_timeline", "timeline"):
            raw_sizes = payload.get(raw_mode)
            if not isinstance(raw_sizes, list):
                continue
            parsed = [int(value) for value in raw_sizes if isinstance(value, int) and int(value) >= 0]
            if len(parsed) == 2:
                sizes[raw_mode] = parsed
        return sizes

    def _load_experiment_control_view_mode_panel_sizes(self, state: dict[str, object]) -> dict[str, list[int]]:
        payload = state.get("experiment_control_view_mode_panel_sizes")
        sizes: dict[str, list[int]] = {}
        if not isinstance(payload, dict):
            return sizes
        for raw_mode in ("full", "table_timeline", "timeline"):
            raw_sizes = payload.get(raw_mode)
            if not isinstance(raw_sizes, list):
                continue
            parsed = [int(value) for value in raw_sizes if isinstance(value, int) and int(value) >= 0]
            if len(parsed) == 2:
                sizes[raw_mode] = parsed
        return sizes

    def _experiment_control_current_splitter_sizes(self) -> list[int]:
        splitter = getattr(self, "_flow_editor_splitter", None)
        if splitter is None:
            return []
        return [max(int(size), 0) for size in splitter.sizes()]

    def _experiment_control_parent_splitter(self):
        window = self.window()
        splitter = getattr(window, "plot_splitter", None)
        if splitter is None or splitter.count() < 2:
            return None
        return splitter

    def _experiment_control_current_parent_splitter_sizes(self) -> list[int]:
        splitter = self._experiment_control_parent_splitter()
        if splitter is None:
            return []
        return [max(int(size), 0) for size in splitter.sizes()]

    def _experiment_control_min_table_splitter_height(self) -> int:
        header = self.plan_table.horizontalHeader()
        table = self.plan_table
        rows = max(table.rowCount(), 0)
        header_height = int(header.sizeHint().height())
        if rows <= 0:
            return max(header_height + 36, 96)
        visible_rows = min(rows, 2)
        content_height = 0
        for row in range(visible_rows):
            content_height += max(int(table.rowHeight(row)), int(table.sizeHintForRow(row)), 24)
        return max(header_height + content_height + 8, header_height + 48)

    def _experiment_control_default_splitter_sizes(self, mode: str) -> list[int]:
        timeline_height = max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height(), 64)
        if mode == "timeline":
            return [0, timeline_height]
        min_table_height = self._experiment_control_min_table_splitter_height()
        if mode == "table_timeline":
            return [min_table_height, timeline_height]
        return [max(min_table_height, timeline_height * 2), timeline_height]

    def _experiment_control_target_panel_height(self, mode: str | None = None) -> int:
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        header_height = 0
        header_row = getattr(self, "_experiment_control_header_row", None)
        if header_row is not None:
            header_height = max(int(header_row.sizeHint().height()), int(header_row.minimumHeight()), 20)
        matrix_height = 0
        if normalized == "full" and hasattr(self, "_experiment_control_matrix_widget"):
            matrix_height = max(int(self._experiment_control_matrix_widget.sizeHint().height()), 0)
        action_height = 0
        if normalized == "full" and hasattr(self, "_experiment_control_editor_action_row"):
            action_height = max(int(self._experiment_control_editor_action_row.sizeHint().height()), 0)
        content_spacing = 4 if normalized == "full" else 2
        outer_margins = 16
        if normalized == "timeline":
            flow_action_height = 0
            if hasattr(self, "_experiment_control_flow_action_row"):
                flow_action_height = max(int(self._experiment_control_flow_action_row.sizeHint().height()), 0)
            timeline_height = max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height(), 64)
            splitter_handle = max(int(getattr(self._flow_editor_splitter, "handleWidth", lambda: 6)()), 6)
            return max(header_height + timeline_height + flow_action_height + splitter_handle + content_spacing + outer_margins, 140)
        timeline_controls_height = 0
        timeline_controls_widget = getattr(self, "_experiment_control_timeline_controls_widget", None)
        if timeline_controls_widget is not None:
            timeline_controls_height = max(int(timeline_controls_widget.sizeHint().height()), 0)
        return max(header_height + matrix_height + action_height + timeline_controls_height + content_spacing + outer_margins, 180)

    def _experiment_control_default_parent_splitter_sizes(self, mode: str) -> list[int]:
        splitter = self._experiment_control_parent_splitter()
        if splitter is None:
            return []
        sizes = self._experiment_control_current_parent_splitter_sizes()
        if len(sizes) != 2:
            sizes = [0, 0]
        total = sum(sizes)
        if total <= 0:
            total = max(int(splitter.height()), int(splitter.sizeHint().height()), 1)
        target_top = self._experiment_control_target_panel_height(mode)
        bottom_min = max(180, int(splitter.widget(1).minimumHeight()) if splitter.widget(1) is not None else 180)
        top = min(max(target_top, 120), max(total - bottom_min, 120))
        bottom = max(total - top, bottom_min)
        return [top, bottom]

    def _remember_experiment_control_view_mode_sizes(self, mode: str | None = None) -> None:
        splitter_sizes = self._experiment_control_current_splitter_sizes()
        if len(splitter_sizes) != 2:
            return
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        self._experiment_control_view_mode_sizes[normalized] = splitter_sizes

    def _remember_experiment_control_view_mode_panel_sizes(self, mode: str | None = None) -> None:
        splitter_sizes = self._experiment_control_current_parent_splitter_sizes()
        if len(splitter_sizes) != 2:
            return
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        self._experiment_control_view_mode_panel_sizes[normalized] = splitter_sizes

    def _apply_experiment_control_parent_splitter_sizes(self, mode: str | None = None) -> None:
        from lspr_app.gui.main_window_state import ensure_visible_top_content_splitter, normalize_top_content_mode

        splitter = self._experiment_control_parent_splitter()
        if splitter is None:
            return
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        window = self.window()
        if normalize_top_content_mode(getattr(window, "_top_view_mode", "spectra")) != "experimental_control":
            return
        stack = getattr(window, "_top_content_stack", None)
        if stack is None or stack.currentWidget() is not self:
            return
        cached_sizes = self._experiment_control_view_mode_panel_sizes.get(normalized)
        if cached_sizes is None:
            cached_sizes = self._experiment_control_default_parent_splitter_sizes(normalized)
        if len(cached_sizes) != 2:
            return
        top = max(int(cached_sizes[0]), 120)
        bottom = max(int(cached_sizes[1]), 180)
        splitter.setSizes([top, bottom])
        ensure_visible_top_content_splitter(window, mode=normalized)

    def _persist_experiment_control_view_mode_layout(self) -> None:
        self._apply_experiment_control_parent_splitter_sizes()
        self.save_ui_state()

    def _experiment_control_view_mode_label(self, mode: str | None = None) -> str:
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        return {
            "full": "Full",
            "table_timeline": "Table+Timeline",
            "timeline": "Timeline",
        }[normalized]

    def _experiment_control_view_mode_tooltip(self, mode: str | None = None) -> str:
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        current = self._experiment_control_view_mode_label(normalized)
        return (
            f"Current view: {current}. Click to cycle between Full, Table+Timeline, and Timeline."
            if normalized == "full"
            else f"Current view: {current}. Click to cycle to the next visibility mode."
        )

    def _update_experiment_control_view_mode_button(self) -> None:
        if not hasattr(self, "_experiment_control_view_mode_button"):
            return
        label = self._experiment_control_view_mode_label()
        self._experiment_control_view_mode_button.setText(f"[{label}]")
        self._experiment_control_view_mode_button.setToolTip(self._experiment_control_view_mode_tooltip())

    def _normalize_experiment_control_timeline_label_mode(self, mode: object) -> str:
        normalized = str(mode or "").strip().lower()
        return normalized if normalized in {"comment", "color_name"} else "comment"

    def _experiment_control_timeline_label_mode_label(self, mode: str | None = None) -> str:
        normalized = self._normalize_experiment_control_timeline_label_mode(mode or self._experiment_control_timeline_label_mode)
        return {
            "comment": "Comment",
            "color_name": "ColorName",
        }[normalized]

    def _update_experiment_control_timeline_label_mode(self) -> None:
        if not hasattr(self, "timeline_widget"):
            return
        self.timeline_widget.set_label_mode(self._experiment_control_timeline_label_mode)
        self.timeline_widget.set_color_palette_entries(self._color_palette_entries)
        self.timeline_widget.update()

    def _cycle_experiment_control_timeline_label_mode(self) -> None:
        current = self._normalize_experiment_control_timeline_label_mode(self._experiment_control_timeline_label_mode)
        self._experiment_control_timeline_label_mode = "color_name" if current == "comment" else "comment"
        self._update_experiment_control_timeline_label_mode()
        self.save_ui_state()

    def _apply_experiment_control_view_mode(self, *, save: bool = False) -> None:
        from lspr_app.gui.main_window_state import ensure_visible_top_content_splitter

        mode = self._normalize_experiment_control_view_mode(self._experiment_control_view_mode)
        self._experiment_control_view_mode = mode
        show_matrix = mode == "full"
        show_table = mode != "timeline"
        if hasattr(self, "_experiment_control_matrix_widget"):
            self._experiment_control_matrix_widget.setVisible(show_matrix)
        if hasattr(self, "_experiment_control_editor_action_row"):
            self._experiment_control_editor_action_row.setVisible(show_matrix)
        if hasattr(self, "_experiment_control_table_container"):
            self._experiment_control_table_container.setVisible(show_table)
        if hasattr(self, "_flow_editor_splitter"):
            if show_table:
                cached_sizes = self._experiment_control_view_mode_sizes.get(mode)
                if cached_sizes is None and mode == "table_timeline":
                    cached_sizes = self._experiment_control_view_mode_sizes.get("full")
                if cached_sizes is None:
                    cached_sizes = self._experiment_control_default_splitter_sizes(mode)
                if len(cached_sizes) == 2 and cached_sizes[0] > 0:
                    cached_sizes = [
                        max(int(cached_sizes[0]), self._experiment_control_min_table_splitter_height()),
                        max(int(cached_sizes[1]), max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height(), 64)),
                    ]
                    self._flow_editor_splitter.setSizes(cached_sizes)
                self._flow_editor_splitter.setStretchFactor(0, 1)
                self._flow_editor_splitter.setStretchFactor(1, 0)
            else:
                cached_sizes = self._experiment_control_view_mode_sizes.get("timeline")
                if cached_sizes is None:
                    cached_sizes = self._experiment_control_default_splitter_sizes("timeline")
                self._flow_editor_splitter.setSizes(cached_sizes)
        self._update_experiment_control_view_mode_button()
        if show_table:
            self._fit_plan_table_columns_to_viewport()
            self._update_plan_table_height()
        self._sync_detail_visibility()
        self._experiment_control_edit_controller.sync_overlay()
        self.updateGeometry()
        if self.parentWidget() is not None:
            self.parentWidget().updateGeometry()
        ensure_visible_top_content_splitter(self.window(), mode=getattr(self.window(), "_top_view_mode", "spectra"))
        if save:
            QTimer.singleShot(0, self._persist_experiment_control_view_mode_layout)
        else:
            QTimer.singleShot(0, self._apply_experiment_control_parent_splitter_sizes)

    def _cycle_experiment_control_view_mode(self) -> None:
        order = ["full", "table_timeline", "timeline"]
        current = self._normalize_experiment_control_view_mode(self._experiment_control_view_mode)
        self._remember_experiment_control_view_mode_sizes(current)
        self._remember_experiment_control_view_mode_panel_sizes(current)
        next_mode = order[(order.index(current) + 1) % len(order)]
        self._experiment_control_view_mode = next_mode
        self._apply_experiment_control_view_mode(save=True)

    def _apply_pending_experiment_control_view_mode(self) -> None:
        if not self._experiment_control_view_mode_apply_pending:
            return
        self._experiment_control_view_mode_apply_pending = False
        self._apply_experiment_control_view_mode()

    def _sync_detail_visibility(self) -> None:
        show_per_channel_editor = not self.manual_uniform_button.isChecked()
        self.manual_dir_label.setVisible(show_per_channel_editor)
        self.manual_tube_label.setVisible(show_per_channel_editor)
        self.shared_direction_row.setVisible(self.manual_uniform_button.isChecked())
        self.shared_tube_row.setVisible(self.manual_uniform_button.isChecked())
        for button in self.manual_direction_buttons:
            button.setVisible(show_per_channel_editor)
        for spin in self.manual_tube_spins:
            spin.setVisible(show_per_channel_editor)
        self._update_plan_detail_toggle_icon()
        if not hasattr(self, "_experiment_control_table_container") or self._experiment_control_table_container.isVisible():
            self._configure_experiment_control_table_columns()
            self._fit_plan_table_columns_to_viewport()

    def _update_plan_detail_toggle_icon(self) -> None:
        update_plan_detail_toggle_icon(self)

    # Legacy compatibility shims kept while the table restart is being validated.
    def _set_experiment_control_table_row_items(self, row_index: int, step: PumpPlanStep) -> None:
        _ = row_index, step

    def _set_experiment_control_table_row_widgets(self, row_index: int, step: PumpPlanStep) -> None:
        _ = row_index, step

    def _set_experiment_control_table_row(self, row_index: int, step: PumpPlanStep, *, with_widgets: bool = True) -> None:
        _ = row_index, step, with_widgets

    def _apply_experiment_control_row_background(self, row_index: int) -> None:
        _ = row_index

    def _populate_experiment_control_table(self, steps: list[PumpPlanStep], selected_row: int | None = None) -> None:
        if selected_row is None:
            selected_row = self._selected_experiment_control_row()
        recomputed_steps = recompute_plan_timing(self._strip_pause_flow_step(steps))
        self._updating_table = True
        try:
            self._plan_model.set_steps(recomputed_steps)
            self._plan_model.set_theme_palette(self._theme_palette())
            self._plan_model.set_time_unit_mode(self._time_unit_mode)
            self._plan_model.set_tube_mm_by_channel([spin.value() for spin in self.manual_tube_spins])
            self._plan_model.set_switch_solution_labels(self._switch_solution_labels)
            self._plan_model.set_color_options(self._color_palette_entries)
            self._plan_model.set_valve_state_labels(self._valve_state_labels)
            self._plan_model.set_valve_state_colors(self._valve_state_colors)
        finally:
            self._updating_table = False
        self._experiment_control_steps_cache = deepcopy(recomputed_steps)
        self.timeline_widget.set_steps(
            recomputed_steps,
            self._experiment_control_timeline_row(),
            self._timeline_progress_for_display(),
            self._plan_runtime_for_display(),
        )
        if recomputed_steps:
            row_to_select = 0 if selected_row is None else min(max(selected_row, 0), len(recomputed_steps) - 1)
            self._select_experiment_control_plan_row(row_to_select)
        else:
            self.plan_table.clearSelection()
        self._fit_plan_table_columns_to_viewport()
        self._update_plan_table_height()

    def _set_item(self, row: int, column: int, text: str, editable: bool = True, selectable: bool = True) -> None:
        index = self._plan_model.index(row, column)
        if index.isValid():
            self._plan_model.setData(index, text, Qt.ItemDataRole.EditRole)

    def _set_time_item(self, row: int, column: int, seconds: float, editable: bool) -> None:
        _ = editable
        index = self._plan_model.index(row, column)
        if index.isValid():
            self._plan_model.setData(index, self._seconds_to_display(float(seconds)), Qt.ItemDataRole.EditRole)

    def _get_time_item_seconds(self, row: int, column: int) -> float:
        step = self._plan_model.step_at(row)
        if step is None:
            return 0.0
        if column == 1:
            return float(step.duration_s)
        if column == 2:
            return float(step.start_s)
        if column == 3:
            return float(step.end_s)
        return 0.0

    def _read_experiment_control_steps(self) -> list[PumpPlanStep]:
        if self._experiment_control_bootstrap_pending_steps:
            return recompute_plan_timing(self._strip_pause_flow_step(self._experiment_control_bootstrap_pending_steps))
        if self._experiment_plan_import_pending_steps:
            return recompute_plan_timing(self._strip_pause_flow_step(self._experiment_plan_import_pending_steps))
        steps = self._plan_model.steps()
        if steps:
            return recompute_plan_timing(self._strip_pause_flow_step(steps))
        return recompute_plan_timing([])

    def _step_from_experiment_control_row(self, row: int) -> PumpPlanStep | None:
        if row < 0 or row >= self.plan_table.rowCount():
            return None
        return self._plan_model.step_at(row)

    def _update_experiment_control_steps_cache_from_row(self, row: int) -> None:
        step = self._step_from_experiment_control_row(row)
        if step is None:
            return
        row_count = max(self.plan_table.rowCount(), 0)
        if len(self._experiment_control_steps_cache) != row_count:
            self._experiment_control_steps_cache = recompute_plan_timing(self._plan_model.steps())
            return
        cache = list(self._experiment_control_steps_cache)
        plan_row = self._plan_row_from_table_row(row)
        if plan_row is None or plan_row >= len(cache):
            return
        cache[plan_row] = deepcopy(step)
        self._experiment_control_steps_cache = recompute_plan_timing(cache)

    def _sync_experiment_control_table_derived_columns(self, steps: list[PumpPlanStep]) -> None:
        _ = steps
        self._plan_model.set_theme_palette(self._theme_palette())
        self._plan_model.set_time_unit_mode(self._time_unit_mode)
        self._plan_model.set_tube_mm_by_channel([spin.value() for spin in self.manual_tube_spins])
        self._plan_model.set_switch_solution_labels(self._switch_solution_labels)
        self._plan_model.set_color_options(self._color_palette_entries)
        self._plan_model.set_valve_state_labels(self._valve_state_labels)
        self._plan_model.set_valve_state_colors(self._valve_state_colors)

    def _refresh_experiment_control_view(self) -> None:
        selected_row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        self._set_experiment_control_pause_row_visible(self._experiment_control_pause_row_visible())
        if self._experiment_control_bootstrap_in_progress or self._experiment_plan_import_in_progress:
            self._experiment_control_steps_cache = deepcopy(steps)
            if steps:
                self._schedule_visible_experiment_control_rows_load()
            return
        expected_rows = len(steps)
        if self.plan_table.rowCount() != expected_rows:
            self._populate_experiment_control_table(steps, selected_row=selected_row)
            return
        self._experiment_control_steps_cache = deepcopy(steps)
        self._updating_table = True
        try:
            self._plan_model.set_steps(steps)
            self._sync_experiment_control_table_derived_columns(steps)
        finally:
            self._updating_table = False
        if selected_row is not None and 0 <= selected_row < self.plan_table.rowCount():
            self._select_experiment_control_plan_row(selected_row)
        self._update_timeline_selection()
        self._fit_plan_table_columns_to_viewport()
        self._update_plan_table_height()

    def _handle_experiment_control_model_changed(self, *args) -> None:
        if self._updating_table:
            return
        previous_steps = deepcopy(self._experiment_control_steps_cache)
        self._experiment_control_steps_cache = self._plan_model.steps()
        self._update_timeline_selection()
        if (
            self._plan_running
            and len(args) >= 2
            and isinstance(args[0], QModelIndex)
            and isinstance(args[1], QModelIndex)
            and self._plan_active_row is not None
        ):
            top_left = args[0]
            bottom_right = args[1]
            if top_left.row() <= self._plan_active_row <= bottom_right.row():
                updated_step = self._plan_model.step_at(self._plan_active_row)
                previous_step = previous_steps[self._plan_active_row] if self._plan_active_row < len(previous_steps) else None
                change_summary = self._experiment_control_step_change_summary(previous_step, updated_step)
                if updated_step is not None:
                    if self._apply_experiment_control_step_to_pump(updated_step, start=True) and change_summary:
                        self._emit_experimental_control_state("step_edited", updated_step, status=change_summary)
        self.save_ui_state()

    def _handle_experiment_control_table_change(self, *_args) -> None:
        self._handle_experiment_control_model_changed()

    def eventFilter(self, obj, event):  # pragma: no cover - GUI runtime path
        if hasattr(self, "_experiment_control_edit_controller") and self._experiment_control_edit_controller.event_filter(obj, event):
            return True
        if event.type() == QEvent.Type.KeyPress and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")):
                key = event.key()
        if event.type() == QEvent.Type.Wheel and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_wheel_scroll")):
                if isinstance(obj, QDoubleSpinBox):
                    if obj.hasFocus():
                        return False
                    return self._scroll_plan_table_by_wheel(event.angleDelta().y())
                if isinstance(obj, QComboBox):
                    if obj.hasFocus():
                        return False
                    return self._scroll_plan_table_by_wheel(event.angleDelta().y())
                if isinstance(obj, QLineEdit):
                    if self._widget_or_ancestor_has_focus(obj, (QDoubleSpinBox, QComboBox, QLineEdit)):
                        return False
                    return self._scroll_plan_table_by_wheel(event.angleDelta().y())
        if event.type() == QEvent.Type.Wheel and obj in (self.plan_table, self.plan_table.viewport()):
            index = self._wheel_event_index_for_plan_table(obj, event)
            if self._cycle_plan_table_cell_by_wheel(index, event.angleDelta().y()):
                event.accept()
                return True
        if event.type() == QEvent.Type.MouseButtonPress and getattr(obj, "property", None) is not None:
            if bool(obj.property("open_popup_on_click")):
                combo = self._combo_popup_target(obj)
                if combo is not None and getattr(self, "_ui_startup_ready", False):
                    QTimer.singleShot(0, combo.showPopup)
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn) and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")):
                cell = self._flow_table_cell_for_widget(obj)
                if cell is not None:
                    row, column = cell
                    if row != self.plan_table.currentRow() or column != self.plan_table.currentColumn():
                        self.plan_table.setCurrentCell(row, column)
                    self.plan_table.horizontalScrollBar().setValue(0)
                    if not self._plan_table_layout_locked:
                        self._fit_plan_table_columns_to_viewport()
        if event.type() == QEvent.Type.Resize and (obj is self.plan_table or obj is self.plan_table.viewport()):
            if not self._plan_table_layout_locked:
                self._plan_table_fit_timer.start()
            self._experiment_control_edit_controller.sync_overlay()
            self._schedule_visible_experiment_control_rows_load()
        return super().eventFilter(obj, event)

    def _handle_experiment_control_current_index_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        _ = previous
        if not current.isValid():
            return
        if self._plan_holding or self._plan_paused:
            row = self._selected_experiment_control_row()
            if row is None:
                row = self._plan_active_row if self._plan_active_row is not None else self._plan_row_from_table_row(current.row())
            if row is not None and row >= 0:
                self._resume_experiment_control_after_manual_step_change(
                    int(row),
                    status_message=f"Running experiment plan from step {int(row) + 1}.",
                    log_message=f"Experiment plan resumed on step {int(row) + 1} after table selection.",
                    emit_event="plan_resume",
                )
                return
        self._update_timeline_selection()
        self._load_selected_step_into_editor()
        self._experiment_control_edit_controller.sync_overlay()

    def _keep_plan_table_left_aligned(self, value: int = 0) -> None:
        if value:
            self.plan_table.horizontalScrollBar().setValue(0)
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.sync_overlay()

    def _selected_experiment_control_row(self) -> int | None:
        row = self._selected_table_row()
        if row is None:
            return None
        return self._plan_row_from_table_row(row)

    def _experiment_control_timeline_row(self) -> int | None:
        if self._plan_running or self._plan_holding or self._plan_paused:
            if self._plan_active_row is not None:
                return self._plan_active_row
        return self._selected_experiment_control_row()

    def _flow_table_row_from_point(self, point_f) -> int | None:
        if not self.plan_table.rowCount():
            return None
        index = self.plan_table.indexAt(point_f.toPoint())
        if index.isValid():
            return index.row()
        return None

    def _move_experiment_control_step_to_row(self, source_row: int, target_row: int) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            return
        source_row = min(max(source_row, 0), len(steps) - 1)
        target_row = min(max(target_row, 0), len(steps) - 1)
        if source_row == target_row:
            return
        step = steps.pop(source_row)
        steps.insert(target_row, step)
        self._populate_experiment_control_table(steps, selected_row=target_row)
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.clear_copied_selection()
        self.save_ui_state()
        _LOGGER.info("Moved experiment-plan step from %s to %s via timeline drag.", source_row + 1, target_row + 1)
        self._set_status_message(f"Moved step to position {target_row + 1}.")

    def _update_timeline_selection(self) -> None:
        runtime_row = self._plan_active_row if (self._plan_running or self._plan_holding or self._plan_paused) else None
        self._set_experiment_control_runtime_row_property(runtime_row)
        self.timeline_widget.set_steps(
            self._read_experiment_control_steps(),
            self._experiment_control_timeline_row(),
            self._timeline_progress_for_display(),
            self._plan_runtime_for_display(),
        )
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.sync_overlay()
        self._update_plan_table_height()


    def _copy_color_names_to_comments(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            return
        for step in steps:
            color_name = next((name for name, color in self._color_palette_entries if color == step.color), step.color)
            step.description = str(color_name or "").strip()
        self._populate_experiment_control_table(steps, selected_row=self._selected_experiment_control_row())

    def _current_editor_step(self, step_number: int | None = None) -> PumpPlanStep:
        row = self._selected_experiment_control_row()
        color = self.step_color_combo.currentData()
        return PumpPlanStep(
            step=step_number or (row + 1 if row is not None else 1),
            duration_s=max(self._editor_duration_seconds, 0.0),
            color=str(color or self._default_experiment_control_color((step_number or 1) - 1)),
            valve=str(self.step_valve_button.property("valve") or "Open"),
            switch_position=self._current_switch_position_from_editor(),
            description=self.step_comment_edit.text().strip(),
            channels=[
                PumpChannelStep(
                    flow_ul_min=max(round(self.manual_flow_spins[index].value()), 0),
                    direction=self._direction_button_value(self.manual_direction_buttons[index]),
                )
                for index in range(ACTIVE_PUMP_CHANNELS)
            ],
        )

    def _set_editor_from_step(self, step: PumpPlanStep) -> None:
        self._editor_duration_seconds = max(float(step.duration_s), 0.0)
        self._suspend_duration_tracking = True
        self.step_duration_spin.setValue(self._seconds_to_display(self._editor_duration_seconds))
        self._suspend_duration_tracking = False
        color_index = self.step_color_combo.findData(step.color)
        if color_index >= 0:
            self.step_color_combo.setCurrentIndex(color_index)
        self._set_step_valve_button_state(step.valve)
        self._updating_switch_editor = True
        try:
            switch_position = max(min(int(step.switch_position), 12), 1)
            self.step_switch_spin.setValue(switch_position)
            self.step_switch_combo.setCurrentIndex(switch_position - 1)
        finally:
            self._updating_switch_editor = False
        self.step_comment_edit.setText(step.description)
        for index, channel in enumerate(step.channels):
            self.manual_flow_spins[index].setValue(max(round(float(channel.flow_ul_min)), 0))
            self._set_direction_button(self.manual_direction_buttons[index], channel.direction)
        self._apply_shared_manual_settings()

    def _load_selected_step_into_editor(self) -> None:
        table_row = self._selected_table_row()
        if table_row is None:
            return
        row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        if row is None or row >= len(steps):
            return
        self._set_editor_from_step(steps[row])

    def _add_experiment_control_step_from_editor(self) -> None:
        steps = self._read_experiment_control_steps()
        row = self._selected_experiment_control_row()
        insert_at = len(steps) if row is None else min(max(row + 1, 0), len(steps))
        step = self._current_editor_step(step_number=insert_at + 1)
        if not step.color:
            step.color = self._default_experiment_control_color(insert_at)
        steps.insert(insert_at, step)
        if steps:
            self._populate_experiment_control_table(steps, selected_row=insert_at)
        else:
            self._populate_experiment_control_table(steps)

    def _update_selected_experiment_control_step_from_editor(self) -> None:
        row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        if row is None:
            self._add_experiment_control_step_from_editor()
            return
        updated = self._current_editor_step(step_number=row + 1)
        steps[row] = updated
        self._populate_experiment_control_table(steps, selected_row=row)

    def _apply_experiment_control_step_to_pump(self, step: PumpPlanStep, *, start: bool) -> bool:
        previous = self._applied_plan_step
        _LOGGER.info(
            "Experiment control step apply requested | step=%s valve=%s previous_valve=%s pump_connected=%s valve_connected=%s switch_connected=%s running=%s holding=%s start=%s",
            step.step,
            str(step.valve or "").strip() or "-",
            str(previous.valve or "").strip() if previous is not None else "-",
            self._client.is_connected(),
            self._valve_client is not None and self._valve_client.is_connected(),
            self._mswitch_client is not None and self._mswitch_client.is_connected(),
            self._plan_running,
            self._plan_holding,
            start,
        )
        status_messages: list[str] = []
        try:
            _LOGGER.info(
                "Applying experiment-plan step | step=%s valve=%s previous_valve=%s controller=%s port=%s running=%s holding=%s start=%s",
                step.step,
                str(step.valve or "").strip() or "-",
                str(previous.valve or "").strip() if previous is not None else "-",
                getattr(self._valve_client, "controller_type", None),
                getattr(self._valve_client, "port", None),
                self._plan_running,
                self._plan_holding,
                start,
            )
            valve = str(step.valve or "").strip()
            previous_valve = str(previous.valve or "").strip().lower() if previous is not None else ""
            switch_position = int(max(min(int(step.switch_position), 12), 1))
            previous_switch = int(max(min(int(previous.switch_position), 12), 1)) if previous is not None else -1
            switch_changed = switch_position != previous_switch
            wait_for_switch_first = bool(self._wait_for_mswitch_first and switch_changed)

            pump_connected = self._client.is_connected()
            channels_to_stop: list[int] = []
            channels_to_start: list[int] = []
            channels_to_configure: list[tuple[int, float, str, float]] = []
            channels_to_restart_after_switch: list[int] = []
            if pump_connected:
                for index, channel in enumerate(step.channels, start=1):
                    direction = str(channel.direction or "OFF").upper()
                    active = channel.flow_ul_min > 0.0 and direction != "OFF"
                    tube_mm = self.manual_tube_spins[index - 1].value()
                    previous_channel = previous.channels[index - 1] if previous is not None else None
                    previous_direction = (
                        str(previous_channel.direction or "OFF").upper()
                        if previous_channel is not None
                        else "OFF"
                    )
                    previous_active = (
                        previous_channel is not None
                        and previous_channel.flow_ul_min > 0.0
                        and previous_direction != "OFF"
                    )
                    previous_flow = float(previous_channel.flow_ul_min) if previous_channel is not None else 0.0
                    channel_changed = (
                        previous is None
                        or previous_channel is None
                        or previous_direction != direction
                        or abs(previous_flow - float(channel.flow_ul_min)) > 1e-9
                    )

                    if previous_active and (
                        not active
                        or channel_changed
                        or (wait_for_switch_first and switch_changed)
                    ):
                        channels_to_stop.append(index)

                    if wait_for_switch_first and switch_changed and previous_active and active and not channel_changed:
                        channels_to_restart_after_switch.append(index)

                    if active and channel_changed:
                        channels_to_configure.append((index, float(channel.flow_ul_min), direction, tube_mm))
                        if start:
                            channels_to_start.append(index)
                    elif active and start and not previous_active:
                        channels_to_start.append(index)
            else:
                _LOGGER.warning(
                    "Pump controller offline; skipping pump channel updates | step=%s",
                    step.step,
                )
                status_messages.append("Pump controller not connected.")

            def _apply_pump_updates(*, skip_stop: bool = False) -> None:
                if not pump_connected:
                    return
                if channels_to_stop and not skip_stop:
                    self._client.stop_channels(channels_to_stop)
                for index, flow_ul_min, direction, tube_mm in channels_to_configure:
                    self._client.configure_channel(index, flow_ul_min, direction, tube_mm)
                effective_starts = list(channels_to_start)
                if wait_for_switch_first and switch_changed:
                    for index in channels_to_restart_after_switch:
                        if index not in effective_starts:
                            effective_starts.append(index)
                if effective_starts:
                    self._client.start_channels(effective_starts)
                if channels_to_stop or effective_starts or channels_to_configure:
                    _LOGGER.debug(
                        "Pump channels updated | stop=%s start=%s configure=%s",
                        channels_to_stop,
                        effective_starts,
                        [index for index, _, _, _ in channels_to_configure],
                    )

            def _apply_valve_command() -> None:
                nonlocal previous_valve
                if valve and valve.lower() != previous_valve:
                    _LOGGER.debug(
                        "Valve transition | step=%s valve=%s previous=%s controller=%s port=%s",
                        step.step,
                        valve,
                        previous_valve or "-",
                        getattr(self._valve_client, "controller_type", None),
                        getattr(self._valve_client, "port", None),
                    )
                    if self._valve_client is not None and self._valve_client.is_connected():
                        try:
                            self._valve_client.set_position(valve)
                            _LOGGER.info("Valve command sent | step=%s valve=%s", step.step, valve)
                        except Exception as exc:
                            status_messages.append(f"Valve command failed: {exc}")
                            _LOGGER.error("Valve command failed | step=%s valve=%s error=%s", step.step, valve, exc)
                    else:
                        status_messages.append("Valve controller not connected.")
                        _LOGGER.warning("Valve command skipped | controller not connected | step=%s valve=%s", step.step, valve)
                elif valve:
                    _LOGGER.debug(
                        "Valve unchanged | step=%s valve=%s controller=%s port=%s",
                        step.step,
                        valve,
                        getattr(self._valve_client, "controller_type", None),
                        getattr(self._valve_client, "port", None),
                    )

            def _apply_switch_command() -> None:
                if switch_changed:
                    if self._mswitch_client is not None and self._mswitch_client.is_connected():
                        try:
                            self._move_mswitch_and_verify(switch_position)
                            _LOGGER.info("M-Switch command sent | step=%s switch=%s", step.step, switch_position)
                        except Exception as exc:
                            status_messages.append(f"Switch move failed: {exc}")
                            _LOGGER.error("M-Switch move failed | step=%s switch=%s error=%s", step.step, switch_position, exc)
                    else:
                        status_messages.append("M-Switch not connected.")
                        _LOGGER.warning("M-Switch command skipped | controller not connected | step=%s switch=%s", step.step, switch_position)

            if wait_for_switch_first:
                if channels_to_stop and pump_connected:
                    self._client.stop_channels(channels_to_stop)
                _apply_switch_command()
                _apply_valve_command()
                _apply_pump_updates(skip_stop=True)
            else:
                _apply_pump_updates()
                _apply_valve_command()
                _apply_switch_command()
        except Exception as exc:
            self._set_status_message(f"Step apply failed: {exc}")
            _LOGGER.error("Experiment plan step apply failed | step=%s error=%s", step.step, exc)
            return False
        self._applied_plan_step = step
        self._set_status_message(
            ((" | ".join(status_messages) + " | ") if status_messages else "") + f"Applied experiment-plan step {step.step}."
        )
        _LOGGER.info("Applied experiment-plan step %s", step.step)
        self._emit_experimental_control_state("step_applied", step, status="; ".join(status_messages))
        return True

    def _experiment_control_step_change_summary(self, previous: PumpPlanStep | None, updated: PumpPlanStep | None) -> str:
        if previous is None or updated is None:
            return ""
        changes: list[str] = []
        if abs(float(previous.duration_s) - float(updated.duration_s)) > 1e-9:
            changes.append(f"duration {float(previous.duration_s):g} -> {float(updated.duration_s):g}")
        if str(previous.color or "").strip() != str(updated.color or "").strip():
            changes.append(f"color {previous.color or '-'} -> {updated.color or '-'}")
        if str(previous.valve or "").strip() != str(updated.valve or "").strip():
            changes.append(f"valve {previous.valve or '-'} -> {updated.valve or '-'}")
        if int(previous.switch_position) != int(updated.switch_position):
            changes.append(f"switch {int(previous.switch_position)} -> {int(updated.switch_position)}")
        if str(previous.description or "").strip() != str(updated.description or "").strip():
            prev_desc = str(previous.description or "").strip() or "-"
            new_desc = str(updated.description or "").strip() or "-"
            changes.append(f"comment {prev_desc} -> {new_desc}")
        for index in range(min(len(previous.channels), len(updated.channels), ACTIVE_PUMP_CHANNELS)):
            prev_channel = previous.channels[index]
            new_channel = updated.channels[index]
            if abs(float(prev_channel.flow_ul_min) - float(new_channel.flow_ul_min)) > 1e-9:
                changes.append(f"CH{index + 1} flow {float(prev_channel.flow_ul_min):g} -> {float(new_channel.flow_ul_min):g}")
            prev_dir = str(prev_channel.direction or "OFF").upper()
            new_dir = str(new_channel.direction or "OFF").upper()
            if prev_dir != new_dir:
                changes.append(f"CH{index + 1} dir {prev_dir} -> {new_dir}")
        if not changes:
            return ""
        return "Edited active step: " + "; ".join(changes)

    def _set_experiment_control_runtime_row(
        self,
        row: int,
        *,
        event: str,
        status: str = "",
        apply_step: bool = False,
        refresh_status: bool = True,
    ) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        self._plan_active_row = row
        if apply_step:
            self._apply_experiment_control_step_to_pump(steps[row], start=True)
        self._sync_experiment_control_timeline(steps, row, refresh_status=refresh_status)
        self._emit_experimental_control_state(event, steps[row], status=status)

    def _jump_to_experiment_control_step(self, row: int) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        if self._plan_running or self._plan_holding or self._plan_paused:
            if self._plan_running:
                self._plan_active_row = row
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._step_started_monotonic = monotonic()
                self._set_experiment_control_runtime_row(
                    row,
                    event="step_jump",
                    apply_step=True,
                )
                self._plan_runtime_s = self._step_runtime_for_display()
                self._plan_resume_runtime_s = self._plan_runtime_s
                return
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step change.",
                emit_event="plan_resume",
            )
            return
        self._select_experiment_control_plan_row(row)
        self._load_selected_step_into_editor()
        self._update_timeline_selection()
        self._set_status_message(f"Selected experiment-plan step {row + 1}.")

    def _apply_selected_experiment_control_step(self, row: int) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        if self._plan_running:
            self._set_experiment_control_runtime_row(
                row,
                event="step_apply",
                apply_step=True,
            )
            return
        if self._plan_holding:
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step apply.",
                emit_event="plan_resume",
            )
            return
        if self._plan_paused:
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step apply.",
                emit_event="plan_resume",
            )
            return
        self._jump_to_experiment_control_step(row)
        self._apply_experiment_control_step_to_pump(steps[row], start=True)

    def _run_experiment_control(self) -> None:
        self._start_or_resume_experiment_control()

    def _start_or_resume_experiment_control(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        if self._plan_running:
            return
        if self._plan_holding or self._plan_paused:
            restore_step = None
            if self._plan_paused and self._paused_plan_step is not None:
                restore_step = deepcopy(self._paused_plan_step)
                self._paused_plan_step = None
            self._resume_experiment_plan(
                restore_step=restore_step,
                status_message=f"Resumed experiment plan on step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}.",
                log_message=f"Experiment plan resumed | step={self._plan_active_row + 1 if self._plan_active_row is not None else 1}",
                emit_event="plan_resume",
            )
            return
        recording_active = bool(getattr(getattr(self, "recording_controller", None), "_measurement_active", False))
        if self.record_with_flow_button.isChecked() and not recording_active:
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
                self._select_experiment_control_plan_row(0)
            self._queue_experiment_control_start_after_recording(paused=False, row=row)
        if not self._request_recording_control("start"):
            self._pending_experiment_control_start_after_recording = None
            self._set_status_message("Experiment plan start cancelled because recording was not started.")
            return
        if self._pending_experiment_control_start_after_recording is not None:
            return
        row = self._selected_experiment_control_row()
        if row is None:
            row = 0
            self._select_experiment_control_plan_row(0)
        self._begin_experiment_plan_run(row, steps)

    def _hold_experiment_control(self) -> None:
        self._enter_hold_state()

    def _pause_experiment_control(self) -> None:
        self._enter_pause_state()

    def _stop_experiment_control(self) -> None:
        self._stop_experiment_plan(self._applied_plan_step)

    def _move_to_relative_experiment_control_step(self, delta: int) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            return
        running = self._plan_running
        row = self._plan_active_row if (self._plan_running or self._plan_holding or self._plan_paused) else self._selected_experiment_control_row()
        if row is None:
            row = 0
        target = min(max(row + delta, 0), len(steps) - 1)
        if self._plan_running or self._plan_holding or self._plan_paused:
            if running:
                self._set_experiment_control_runtime_row(
                    target,
                    event="step_jump",
                    apply_step=True,
                )
                self._step_started_monotonic = monotonic()
                self._plan_runtime_s = self._step_runtime_for_display()
                self._plan_resume_runtime_s = self._plan_runtime_s
            else:
                self._resume_experiment_control_after_manual_step_change(
                    target,
                    status_message=f"Running experiment plan from step {target + 1}.",
                    log_message=f"Experiment plan resumed on step {target + 1} after step navigation.",
                    emit_event="plan_resume",
                )
        else:
            self._jump_to_experiment_control_step(target)
        if not (self._plan_running or self._plan_holding or self._plan_paused):
            self._set_status_message(f"Selected experiment-plan step {target + 1}.")

    def _timeline_progress_for_display(self) -> float | None:
        if self._plan_running or self._plan_holding or self._plan_paused:
            row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
            steps = self._read_experiment_control_steps()
            if row is not None and 0 <= row < len(steps):
                return max(float(steps[row].start_s) + max(float(self._plan_elapsed_s), 0.0), 0.0)
            return max(float(self._plan_elapsed_s), 0.0)
        return None

    def _plan_runtime_for_display(self) -> float:
        if self._measurement_started_monotonic is not None:
            return max(monotonic() - self._measurement_started_monotonic, 0.0)
        return max(float(self._plan_runtime_s), 0.0)

    def _step_runtime_for_display(self) -> float:
        if self._step_started_monotonic is not None:
            return max(monotonic() - self._step_started_monotonic, 0.0)
        return max(float(self._plan_resume_runtime_s), 0.0)

    def _emit_experimental_control_state(self, event: str, step: PumpPlanStep | None = None, *, status: str = "") -> None:
        if step is None:
            step = self._applied_plan_step
        plan_state = self._experiment_runtime_snapshot().payload_state
        payload: dict[str, object] = {
            "event": event,
            "plan_state": plan_state,
            "step_index": int(step.step) if step is not None else "",
            "color": str(step.color or "") if step is not None else "",
            "label": str(step.description or "").strip() if step is not None else "",
            "elapsed_in_step_ms": int(round(max(float(self._plan_elapsed_s), 0.0) * 1000.0)),
            "pump_running": bool(self._plan_running),
            "valve_position": str(step.valve or "") if step is not None else "",
            "switch_position": int(step.switch_position) if step is not None else "",
            "pump_connected": bool(self._client.is_connected()),
            "valve_connected": bool(self._valve_client is not None and self._valve_client.is_connected()),
            "switch_connected": bool(self._mswitch_client is not None and self._mswitch_client.is_connected()),
            "status": status,
        }
        tube_values = self._tube_mm_values()
        for index in range(6):
            channel = step.channels[index] if step is not None and index < len(step.channels) else None
            payload[f"ch{index + 1}_flow_ul_min"] = float(channel.flow_ul_min) if channel is not None else ""
            payload[f"ch{index + 1}_direction"] = str(channel.direction or "OFF") if channel is not None else ""
            payload[f"ch{index + 1}_tube_mm"] = float(tube_values[index]) if index < len(tube_values) else ""
        self.experimental_control_state_recorded.emit(payload)

    def _emit_flow_state(self, event: str, step: PumpPlanStep | None = None, *, status: str = "") -> None:
        self._emit_experimental_control_state(event, step, status=status)

    def _advance_experiment_control_progress(self) -> None:
        def _callback() -> None:
            if self._plan_holding or self._plan_paused:
                steps = self._read_experiment_control_steps()
                if steps:
                    self._sync_experiment_control_timeline(steps, self._plan_active_row, refresh_status=True)
                return
            if not self._plan_running or self._plan_started_monotonic is None:
                return
            steps = self._read_experiment_control_steps()
            if not steps:
                self._stop_experiment_control()
                return
            elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
            current_row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
            if current_row is None or not (0 <= current_row < len(steps)):
                current_row = 0
            current_step = steps[current_row]
            if elapsed >= max(float(current_step.duration_s), 0.0):
                next_row = current_row + 1
                if next_row >= len(steps):
                    self._plan_elapsed_s = max(float(current_step.duration_s), 0.0)
                    self._plan_resume_elapsed_s = self._plan_elapsed_s
                    self._stop_experiment_control()
                    self._set_status_message("Experiment plan finished.")
                    _LOGGER.info("Experiment plan finished.")
                    return
                self._plan_active_row = next_row
                self._apply_experiment_control_step_to_pump(steps[next_row], start=True)
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._step_started_monotonic = monotonic()
                self._sync_experiment_control_timeline(steps, next_row, refresh_status=True)
                return
            self._plan_elapsed_s = elapsed
            self._sync_experiment_control_timeline(steps, current_row, refresh_status=True)

        self._run_gui_callback_timed("experiment_control_progress", _callback)

    def _activate_experiment_control_step_for_elapsed(self, elapsed_s: float, *, force: bool) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._plan_active_row = None
            self._plan_elapsed_s = 0.0
            return
        self._plan_elapsed_s = max(float(elapsed_s), 0.0)
        target_row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
        if target_row is None or not (0 <= target_row < len(steps)):
            target_row = 0
        if force or target_row != self._plan_active_row:
            self._plan_active_row = target_row
            self._apply_experiment_control_step_to_pump(steps[target_row], start=True)
        self._sync_experiment_control_timeline(steps, target_row)

    def _stop_all_channels(self) -> None:
        if not self._client.is_connected():
            self._set_status_message("Pump offline. Nothing to stop.")
            return
        try:
            self._client.stop_all(ACTIVE_PUMP_CHANNELS)
        except Exception as exc:
            self._set_status_message(f"Stop failed: {exc}")
            _LOGGER.error("Stop all channels failed: %s", exc)
            return
        self._applied_plan_step = None
        self._set_status_message("Stopped all pump channels.")
        _LOGGER.info("Stopped all pump channels.")

    def shutdown_devices(self) -> None:
        self._set_status_message("Shutting down devices...")
        try:
            if self._client.is_connected():
                try:
                    self._client.stop_all(ACTIVE_PUMP_CHANNELS)
                    _LOGGER.info("Shutdown: stopped all pump channels.")
                except Exception as exc:
                    _LOGGER.warning("Shutdown: could not stop pump channels: %s", exc)
            if self._mswitch_client is not None and self._mswitch_client.is_connected():
                _LOGGER.info("Shutdown: M-Switch left in current position before disconnect.")
        finally:
            self._stop_experiment_control()
            self._release_claimed_device_ports()
            if self._valve_client is not None:
                self._valve_client.close()
            self._valve_client = None
            self._valve_probe = None
            if self._mswitch_client is not None:
                self._mswitch_client.close()
            self._mswitch_client = None
            self._mswitch_probe = None
            if self._client.is_connected():
                self._client.close()
            self._probe = None
            self.valve_availability_changed.emit(None)
            self.mswitch_availability_changed.emit(None)
            self.availability_changed.emit(None)
            self._set_connection_visual(False, "Pump disconnected.")
            self._set_valve_connection_visual(False, "Valve controller disconnected.")
            self._set_mswitch_connection_visual(False, "M-Switch disconnected.")

    def _release_claimed_device_ports(self) -> None:
        if self._probe is not None:
            release_port(self._probe.port, "Experiment Control / Pump")
        if self._valve_probe is not None:
            release_port(self._valve_probe.port, "Experiment Control / Valve")
        if self._mswitch_probe is not None:
            release_port(self._mswitch_probe.port, "Experiment Control / M-Switch")

    def _read_live_status(self) -> None:
        if not self._client.is_connected():
            self._show_info("Connect the pump first.")
            return
        try:
            modes = [self._client.query(f"{channel}xM") for channel in range(1, ACTIVE_PUMP_CHANNELS + 1)]
            directions = [self._client.query(f"{channel}xD") for channel in range(1, ACTIVE_PUMP_CHANNELS + 1)]
        except Exception as exc:
            self._set_status_message(f"Read status failed: {exc}")
            _LOGGER.error("Live status read failed: %s", exc)
            return
        self._set_status_message(
            " | ".join(
                f"CH{index + 1}: {directions[index]} / {modes[index]}"
                for index in range(ACTIVE_PUMP_CHANNELS)
            )
        )
        _LOGGER.debug("Live status read.")

    def current_pump_plan_hdf5_rows(self) -> list[list[str]]:
        core_plan = to_core_experiment_plan(self._read_experiment_control_steps())
        table = build_legacy_experiment_plan_row_table(
            core_plan,
            tube_mm_by_channel=self._tube_mm_values(),
            active_channel_count=ACTIVE_PUMP_CHANNELS,
            hdf5_channel_count=HDF5_PUMP_CHANNELS,
        )
        return table.rows

    def switch_solution_hdf5_rows(self) -> list[list[str]]:
        return [[str(port), self._switch_solution_label(port)] for port in range(1, 13)]

    def switch_solution_hdf5_payload(self) -> dict[str, object]:
        return {
            "switch_solution_mode": bool(self._switch_solution_mode),
            "switch_solution_labels": list(self._switch_solution_labels),
            "switch_solution_rows": self.switch_solution_hdf5_rows(),
            "valve_state_labels": dict(self._valve_state_labels),
            "valve_state_colors": dict(self._valve_state_colors),
            "color_palette_entries": [
                {"name": name, "color": color} for name, color in self._color_palette_entries
            ],
        }

    def _tube_mm_values(self) -> list[float]:
        return [spin.value() for spin in self.manual_tube_spins]

    def _serialize_experiment_control_steps(self, steps: list[PumpPlanStep]) -> list[dict[str, object]]:
        payload: list[dict[str, object]] = []
        for step in self._strip_pause_flow_step(steps):
            payload.append(
                {
                    "duration_s": float(step.duration_s),
                    "color": step.color,
                    "valve": step.valve,
                    "switch_position": int(step.switch_position),
                    "description": step.description,
                    "channels": [
                        {
                            "flow_ul_min": float(channel.flow_ul_min),
                            "direction": channel.direction,
                        }
                        for channel in step.channels
                    ],
                }
            )
        return payload

    def _deserialize_experiment_control_steps(self, payload: object) -> list[PumpPlanStep]:
        if not isinstance(payload, list):
            return []
        steps: list[PumpPlanStep] = []
        for index, raw_step in enumerate(payload, start=1):
            if not isinstance(raw_step, dict):
                continue
            raw_channels = raw_step.get("channels", [])
            channels: list[PumpChannelStep] = []
            if isinstance(raw_channels, list):
                for raw_channel in raw_channels[:ACTIVE_PUMP_CHANNELS]:
                    if isinstance(raw_channel, dict):
                        channels.append(
                            PumpChannelStep(
                                flow_ul_min=max(round(_safe_float(raw_channel.get("flow_ul_min", 0.0))), 0),
                                direction=str(raw_channel.get("direction", "OFF") or "OFF"),
                            )
                        )
            while len(channels) < ACTIVE_PUMP_CHANNELS:
                channels.append(PumpChannelStep())
            steps.append(
                PumpPlanStep(
                    step=index,
                    duration_s=max(_safe_float(raw_step.get("duration_s", 0.0)), 0.0),
                    color=str(
                        raw_step.get("color", self._default_experiment_control_color(index - 1))
                        or self._default_experiment_control_color(index - 1)
                    ),
                    valve=str(raw_step.get("valve", "Open") or "Open"),
                    switch_position=max(min(_safe_int(raw_step.get("switch_position", 1), 1), 12), 1),
                    description=str(raw_step.get("description", "") or ""),
                    channels=channels,
                )
            )
        return recompute_plan_timing(self._strip_pause_flow_step(steps))

    def _restore_experiment_control_state(self) -> None:
        state = self._ui_state
        saved_time_unit = state.get("time_unit_mode")
        if isinstance(saved_time_unit, str) and saved_time_unit in {"s", "min"}:
            self._time_unit_mode = saved_time_unit
            self._update_time_unit_ui()
        tube_values = state.get("tube_mm_values")
        if isinstance(tube_values, list):
            for index, value in enumerate(tube_values[:ACTIVE_PUMP_CHANNELS]):
                try:
                    self.manual_tube_spins[index].setValue(float(value))
                except (TypeError, ValueError):
                    continue

        saved_duration = state.get("editor_duration_s")
        if isinstance(saved_duration, (int, float)):
            self._editor_duration_seconds = max(float(saved_duration), 0.0)
            self._suspend_duration_tracking = True
            self.step_duration_spin.setValue(self._seconds_to_display(self._editor_duration_seconds))
            self._suspend_duration_tracking = False
        saved_color = state.get("editor_color")
        if isinstance(saved_color, str):
            color_index = self.step_color_combo.findData(saved_color)
            if color_index >= 0:
                self.step_color_combo.setCurrentIndex(color_index)
        saved_valve = state.get("editor_valve")
        if isinstance(saved_valve, str):
            self._set_step_valve_button_state(saved_valve)
        saved_switch = state.get("editor_switch_position")
        if isinstance(saved_switch, (int, float)):
            switch_position = max(min(int(saved_switch), 12), 1)
            self.step_switch_spin.setValue(switch_position)
            self.step_switch_combo.setCurrentIndex(switch_position - 1)
        saved_comment = state.get("editor_comment")
        if isinstance(saved_comment, str):
            self.step_comment_edit.setText(saved_comment)

        editor_channels = state.get("editor_channels")
        if isinstance(editor_channels, list):
            for index, raw_channel in enumerate(editor_channels[:ACTIVE_PUMP_CHANNELS]):
                if not isinstance(raw_channel, dict):
                    continue
                self.manual_flow_spins[index].setValue(max(round(float(raw_channel.get("flow_ul_min", 0.0))), 0))
                direction = str(raw_channel.get("direction", "CW") or "CW")
                self._set_direction_button(self.manual_direction_buttons[index], direction)

        saved_uniform = state.get("manual_uniform")
        if isinstance(saved_uniform, bool):
            self.manual_uniform_button.setChecked(saved_uniform)
        saved_details = state.get("show_plan_details")
        if isinstance(saved_details, bool):
            self.plan_detail_toggle.setChecked(saved_details)
            self._show_plan_details = saved_details
        saved_switch_mode = state.get("switch_solution_mode")
        if isinstance(saved_switch_mode, bool):
            self.step_switch_mode_button.setChecked(saved_switch_mode)
        saved_wait_for_mswitch_first = state.get("wait_for_mswitch_first")
        if isinstance(saved_wait_for_mswitch_first, bool):
            self._wait_for_mswitch_first = saved_wait_for_mswitch_first
        saved_valve_labels = state.get("valve_state_labels")
        if isinstance(saved_valve_labels, dict):
            self._valve_state_labels = self._load_valve_state_labels({"valve_state_labels": saved_valve_labels})
            set_step_valve_button_state_for_button(
                self,
                self.step_valve_button,
                str(self.step_valve_button.property("valve") or "Open"),
            )
        saved_valve_colors = state.get("valve_state_colors")
        if isinstance(saved_valve_colors, dict):
            self._valve_state_colors = self._load_valve_state_colors({"valve_state_colors": saved_valve_colors})
        saved_switch_labels = state.get("switch_solution_labels")
        if isinstance(saved_switch_labels, list):
            labels: list[str] = []
            for index, raw_label in enumerate(saved_switch_labels[:12], start=1):
                labels.append(str(raw_label).strip() or f"Solution {index}")
            while len(labels) < 12:
                labels.append(f"Solution {len(labels) + 1}")
            self._switch_solution_labels = labels
        self._update_experiment_control_toggle_button()
        self._refresh_switch_solution_controls()
        saved_pause_state = state.get("pause_state_step")
        if isinstance(saved_pause_state, dict):
            self._experiment_control_pause_template = self._deserialize_experiment_control_pause_template(saved_pause_state)
        saved_pause_dialog_state = state.get("pause_state_dialog_state")
        if isinstance(saved_pause_dialog_state, dict):
            self._pause_state_dialog_state = dict(saved_pause_dialog_state)
        saved_view_mode = state.get("experiment_control_view_mode")
        if isinstance(saved_view_mode, str):
            self._experiment_control_view_mode = self._normalize_experiment_control_view_mode(saved_view_mode)
        saved_timeline_label_mode = state.get("timeline_label_mode")
        if isinstance(saved_timeline_label_mode, str):
            self._experiment_control_timeline_label_mode = self._normalize_experiment_control_timeline_label_mode(
                saved_timeline_label_mode
            )
        saved_view_mode_panel_sizes = state.get("experiment_control_view_mode_panel_sizes")
        if isinstance(saved_view_mode_panel_sizes, dict):
            self._experiment_control_view_mode_panel_sizes = self._load_experiment_control_view_mode_panel_sizes(
                {"experiment_control_view_mode_panel_sizes": saved_view_mode_panel_sizes}
            )
        if hasattr(self, "timeline_widget"):
            self._update_experiment_control_timeline_label_mode()
        self._experiment_control_view_mode_apply_pending = True

        self._experiment_control_bootstrap_pending_state = dict(state)
        self._experiment_control_bootstrap_pending_steps = self._deserialize_experiment_control_steps(state.get("plan_steps"))
        self._experiment_control_bootstrap_pending_selected_row = state.get("selected_plan_row") if isinstance(state.get("selected_plan_row"), int) else None
        self._experiment_control_bootstrap_pending_step_index = 0
        if self._experiment_control_bootstrap_pending_steps:
            self.timeline_widget.set_steps(
                self._experiment_control_bootstrap_pending_steps,
                self._experiment_control_bootstrap_pending_selected_row,
                0.0,
            )
        self._schedule_experiment_control_bootstrap()

    def _schedule_experiment_control_bootstrap(self) -> None:
        if self._experiment_control_bootstrap_started:
            return
        self._experiment_control_bootstrap_started = True
        QTimer.singleShot(0, self._start_experiment_control_bootstrap)

    def _start_experiment_control_bootstrap(self) -> None:
        try:
            self._experiment_control_bootstrap_in_progress = True
            self._set_experiment_control_bootstrap_busy(True)
            self._refresh_ports()
            self._refresh_valve_ports()
            self._refresh_mswitch_ports()
            state = self._experiment_control_bootstrap_pending_state or self._ui_state
            saved_steps = list(self._experiment_control_bootstrap_pending_steps)
            if not saved_steps:
                self._finalize_experiment_control_bootstrap_population(state, [])
                return
            self._begin_experiment_control_bootstrap_population(state, saved_steps)
        except Exception as exc:
            self._abort_experiment_control_bootstrap_population(str(exc))

    def _begin_experiment_control_bootstrap_population(self, state: dict[str, object], steps: list[PumpPlanStep]) -> None:
        self._experiment_control_bootstrap_pending_state = dict(state)
        self._experiment_control_bootstrap_pending_steps = list(steps)
        self._experiment_control_bootstrap_pending_step_index = 0
        self._experiment_control_steps_cache = deepcopy(steps)
        self._experiment_control_loaded_widget_rows.clear()
        _LOGGER.info(
            "Experiment control bootstrap +%.1f ms: populating %d step(s)",
            (perf_counter() - self._bootstrap_t0) * 1000.0,
            len(steps),
        )
        self.plan_table.blockSignals(True)
        self.plan_table.setUpdatesEnabled(False)
        try:
            self._plan_model.set_steps(steps)
            self.plan_table.clearSelection()
        finally:
            self.plan_table.setUpdatesEnabled(True)
            self.plan_table.blockSignals(False)
        self._experiment_plan_import_fill_timer.start()

    def _advance_experiment_control_bootstrap_population(self) -> None:
        try:
            steps = self._experiment_control_bootstrap_pending_steps
            state = self._experiment_control_bootstrap_pending_state or self._ui_state
            if not steps:
                self._experiment_plan_import_fill_timer.stop()
                self._finalize_experiment_control_bootstrap_population(state, [])
                return
            self._experiment_plan_import_fill_timer.stop()
            self._populate_experiment_control_table(steps, selected_row=self._experiment_control_bootstrap_pending_selected_row)
            self._finalize_experiment_control_bootstrap_population(state, steps)
        except Exception as exc:
            self._abort_experiment_control_bootstrap_population(str(exc))

    def _finalize_experiment_control_bootstrap_population(self, state: dict[str, object], steps: list[PumpPlanStep]) -> None:
        try:
            _LOGGER.info(
                "Experiment control bootstrap +%.1f ms: finalizing with %d step(s)",
                (perf_counter() - self._bootstrap_t0) * 1000.0,
                len(steps),
            )
            self._experiment_control_steps_cache = deepcopy(steps)
            if steps:
                selected_row = self._experiment_control_bootstrap_pending_selected_row
                if isinstance(selected_row, int) and 0 <= selected_row < len(steps):
                    self._select_experiment_control_plan_row(selected_row)
                elif self.plan_table.rowCount() > 0:
                    self._select_experiment_control_plan_row(0)
                self.timeline_widget.set_steps(
                    steps,
                    self._experiment_control_timeline_row(),
                    self._timeline_progress_for_display(),
                    self._plan_runtime_for_display(),
                )
                if self.plan_table.rowCount() > 0:
                    self._load_selected_step_into_editor()
                self._experiment_control_edit_controller.sync_overlay()
            self._restore_plan_table_column_widths(state)
            self._fit_plan_table_columns_to_viewport()
            self._update_plan_table_height()
            saved_splitter_sizes = state.get("experiment_control_editor_splitter_sizes")
            if saved_splitter_sizes is None:
                saved_splitter_sizes = state.get("flow_editor_splitter_sizes")
            if isinstance(saved_splitter_sizes, list) and not self._experiment_control_view_mode_sizes:
                self._apply_experiment_control_editor_splitter_sizes(saved_splitter_sizes)
            QTimer.singleShot(0, self._apply_pending_experiment_control_view_mode)
            top_stack = getattr(self.window(), "_top_content_stack", None)
            if top_stack is not None and top_stack.currentWidget() is not self:
                current_mode = str(getattr(self.window(), "_top_view_mode", "spectra") or "spectra").strip().lower()
                pending_mode = str(getattr(self.window(), "_pending_top_view_mode", current_mode) or current_mode).strip().lower()
                if pending_mode == "experimental_control" or current_mode == "experimental_control":
                    QTimer.singleShot(0, lambda stack=top_stack, widget=self: stack.setCurrentWidget(widget))
            self._set_status_message("Experiment control panel ready.")
        finally:
            self._experiment_control_bootstrap_pending_state = None
            self._experiment_control_bootstrap_pending_steps = []
            self._experiment_control_bootstrap_pending_row_order = []
            self._experiment_control_bootstrap_pending_selected_row = None
            self._experiment_control_bootstrap_pending_step_index = 0
            self._experiment_control_bootstrap_in_progress = False
            self._experiment_control_bootstrap_started = False
            self._set_experiment_control_bootstrap_busy(False)
            self._schedule_startup_device_auto_connect()

    def _abort_experiment_control_bootstrap_population(self, message: str) -> None:
        self._experiment_plan_import_fill_timer.stop()
        self._experiment_control_bootstrap_pending_state = None
        self._experiment_control_bootstrap_pending_steps = []
        self._experiment_control_bootstrap_pending_row_order = []
        self._experiment_control_bootstrap_pending_selected_row = None
        self._experiment_control_bootstrap_pending_step_index = 0
        self._experiment_control_loaded_widget_rows.clear()
        self._experiment_control_bootstrap_in_progress = False
        self._experiment_control_bootstrap_started = False
        self._set_experiment_control_bootstrap_busy(False)
        self._show_error(f"Could not load experiment control panel:\n{message}")

    def _prioritized_experiment_control_row_order(self, row_count: int, selected_row: int | None = None) -> list[int]:
        if row_count <= 0:
            return []
        visible_start, visible_end = self._visible_experiment_control_row_range(row_count)
        priority: list[int] = []
        seen: set[int] = set()

        def add(row: int) -> None:
            if 0 <= row < row_count and row not in seen:
                seen.add(row)
                priority.append(row)

        if selected_row is not None:
            add(selected_row)
        for row in range(visible_start, visible_end + 1):
            add(row)
        for row in range(row_count):
            add(row)
        return priority

    def _visible_experiment_control_row_range(self, row_count: int | None = None) -> tuple[int, int]:
        total = self.plan_table.rowCount() if row_count is None else max(int(row_count), 0)
        if total <= 0:
            return (0, -1)
        viewport = self.plan_table.viewport()
        top = self.plan_table.rowAt(0)
        if top < 0:
            top = 0
        bottom = self.plan_table.rowAt(max(viewport.height() - 1, 0))
        if bottom < 0:
            bottom = min(total - 1, top + 18)
        buffer = 8
        start = max(top - buffer, 0)
        end = min(bottom + buffer, total - 1)
        if end < start:
            end = start
        return (start, end)

    def _schedule_visible_experiment_control_rows_load(self) -> None:
        if self._experiment_control_visible_rows_timer.isActive():
            return
        self._experiment_control_visible_rows_timer.start()

    def _load_visible_experiment_control_rows(self, force: bool = False) -> None:
        self._run_gui_callback_timed("experiment_control_visible_rows", lambda: None)

    def _restore_ui_state(self) -> None:
        state = self._ui_state
        width = state.get("width")
        height = state.get("height")
        x_pos = state.get("x")
        y_pos = state.get("y")
        maximized = state.get("maximized")
        selected_port = state.get("selected_port")
        selected_valve_port = state.get("selected_valve_port")
        selected_mswitch_port = state.get("selected_mswitch_port")

        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            self.resize(width, height)
        if isinstance(x_pos, int) and isinstance(y_pos, int):
            # Check if position is within available screen geometry
            app = QApplication.instance()
            if app:
                screen_geometry = app.primaryScreen().availableGeometry()
                window_width = width if isinstance(width, int) and width > 0 else self.width()
                window_height = height if isinstance(height, int) and height > 0 else self.height()
                if not screen_geometry.contains(x_pos, y_pos):
                    # Position is off-screen, use default position
                    x_pos = max(100, screen_geometry.left())
                    y_pos = max(100, screen_geometry.top())
            self.move(x_pos, y_pos)
        if isinstance(selected_port, str) and selected_port:
            self._last_selected_port = selected_port
        if isinstance(selected_valve_port, str) and selected_valve_port:
            self._last_selected_valve_port = selected_valve_port
        if isinstance(selected_mswitch_port, str) and selected_mswitch_port:
            self._last_selected_mswitch_port = selected_mswitch_port
        self._start_maximized = bool(maximized)

    def save_ui_state(self) -> None:
        def _callback() -> None:
            if self.isMaximized():
                geometry = self.normalGeometry()
                width = geometry.width()
                height = geometry.height()
                x_pos = geometry.x()
                y_pos = geometry.y()
            else:
                width = self.width()
                height = self.height()
                x_pos = self.x()
                y_pos = self.y()

            self._remember_experiment_control_view_mode_sizes()
            self._remember_experiment_control_view_mode_panel_sizes()

            save_window_ui_state(
                "experiment_control_window",
                {
                    "x": int(x_pos),
                    "y": int(y_pos),
                    "width": int(width),
                    "height": int(height),
                    "maximized": bool(self.isMaximized()),
                    "selected_port": self.selected_port(),
                    "time_unit_mode": self._time_unit_mode,
                    "selected_plan_row": self._selected_experiment_control_row(),
                    "plan_steps": self._serialize_experiment_control_steps(self._read_experiment_control_steps()),
                    "color_palette_entries": [
                        {"name": name, "color": color}
                        for name, color in self._color_palette_entries
                    ],
                    "custom_plan_colors": list(self._custom_plan_colors),
                    "tube_mm_values": self._tube_mm_values(),
                    "manual_uniform": self.manual_uniform_button.isChecked(),
                    "show_plan_details": self._show_plan_details,
                    "experiment_control_view_mode": self._experiment_control_view_mode,
                    "timeline_label_mode": self._experiment_control_timeline_label_mode,
                    "experiment_control_view_mode_sizes": dict(self._experiment_control_view_mode_sizes),
                    "experiment_control_view_mode_panel_sizes": dict(self._experiment_control_view_mode_panel_sizes),
                    "plan_table_column_widths": self._plan_table_column_widths(),
                    "plan_table_header_state": self._plan_table_header_state(),
                    "experiment_control_editor_splitter_sizes": self._experiment_control_editor_splitter_sizes(),
                    "flow_editor_splitter_sizes": self._experiment_control_editor_splitter_sizes(),
                    "editor_duration_s": self._editor_duration_seconds,
                    "editor_color": self.step_color_combo.currentData(),
                    "editor_valve": self.step_valve_button.property("valve"),
                    "editor_switch_position": self._current_switch_position_from_editor(),
                    "editor_comment": self.step_comment_edit.text(),
                    "valve_state_labels": dict(self._valve_state_labels),
                    "valve_state_colors": dict(self._valve_state_colors),
                    "switch_solution_mode": self._switch_solution_mode,
                    "wait_for_mswitch_first": self._wait_for_mswitch_first,
                    "switch_solution_labels": list(self._switch_solution_labels),
                    "pause_state_step": self._serialize_experiment_control_pause_template(),
                    "pause_state_dialog_state": dict(getattr(self, "_pause_state_dialog_state", {})),
                    "editor_channels": [
                        {
                            "flow_ul_min": self.manual_flow_spins[index].value(),
                            "direction": self._direction_button_value(self.manual_direction_buttons[index]),
                        }
                        for index in range(ACTIVE_PUMP_CHANNELS)
                    ],
                    "selected_valve_port": self._selected_valve_port(),
                    "selected_mswitch_port": self._selected_mswitch_port(),
                },
            )

        self._run_gui_callback_timed("experiment_control_save_ui_state", _callback)

    def showEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self.setUpdatesEnabled(False)
        try:
            if self._experiment_control_view_mode_apply_pending:
                self._experiment_control_view_mode_apply_pending = False
                self._apply_experiment_control_view_mode()
            super().showEvent(event)
        finally:
            self.setUpdatesEnabled(True)
        if self._start_maximized and self.isWindow():
            self.showMaximized()
            self._start_maximized = False
        if self._plan_table_initial_fit_pending:
            self._plan_table_initial_fit_pending = False
            QTimer.singleShot(0, self._fit_plan_table_columns_to_viewport)
        QTimer.singleShot(0, self._apply_experiment_control_parent_splitter_sizes)

    def closeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        _LOGGER.info("Experiment control window closed.")
        self.save_ui_state()
        self._client.close()
        self._release_claimed_device_ports()
        if self._valve_client is not None:
            self._valve_client.close()
        self._valve_client = None
        self._valve_probe = None
        self.availability_changed.emit(None)
        self.valve_availability_changed.emit(None)
        if self._mswitch_client is not None:
            self._mswitch_client.close()
        self._mswitch_client = None
        self._mswitch_probe = None
        self.mswitch_availability_changed.emit(None)
        super().closeEvent(event)


FlowControlTableView = ExperimentControlTableView
FlowControlWindow = ExperimentControlWindow





