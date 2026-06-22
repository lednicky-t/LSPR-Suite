from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lspr_app.device.port_assignments import (
    clear_port_assignment,
    device_assignment_label,
    get_port_assignment,
    set_port_assignment,
)
from lspr_app.device.hardware_inventory import ConnectedSerialDevice, scan_connected_serial_devices


class _HardwareInventorySignals(QObject):
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class _HardwareInventoryTask(QRunnable):
    def __init__(self, generation: int, *, passive: bool = False) -> None:
        super().__init__()
        self._generation = generation
        self._passive = bool(passive)
        self.signals = _HardwareInventorySignals()

    def run(self) -> None:
        try:
            inventory = scan_connected_serial_devices(passive=self._passive)
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))
            return
        self.signals.finished.emit(self._generation, inventory)


def _format_details(record: ConnectedSerialDevice) -> str:
    if not record.details:
        return ""
    return record.details


class HardwareInventoryDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connected devices")
        self.setObjectName("hardwareInventoryDialog")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(980, 420)

        self._thread_pool = QThreadPool.globalInstance()
        self._scan_generation = 0
        self._active_task: _HardwareInventoryTask | None = None
        self._inventory: list[ConnectedSerialDevice] = []
        self._selected_port_before_refresh: str | None = None

        header_label = QLabel(
            "Best-effort COM-port inventory. Recognized devices come from the existing heuristics and background "
            "probe results. Manual assignments can override the label when a port is known but the auto-detect is wrong."
        )
        header_label.setWordWrap(True)

        self._status_label = QLabel("Ready.")
        self._status_label.setObjectName("hardwareInventoryStatus")

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self.refresh_inventory)

        self._table = QTableWidget(0, 7, self)
        self._table.setObjectName("hardwareInventoryTable")
        self._table.setHorizontalHeaderLabels(
            ["Port", "Description", "HWID", "Recognized device", "Assignment", "Match", "Details"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(4, 170)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._button_box.rejected.connect(self.close)

        footer_row = QLabel("Use the Assignment column to override a port as Pump, Valve, or Auto.")
        footer_row.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(header_label)
        layout.addWidget(self._table)
        layout.addWidget(footer_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._refresh_button)
        layout.addWidget(self._button_box)
        self.setLayout(layout)

    def refresh_inventory(self) -> None:
        self._selected_port_before_refresh = self._selected_port()
        self._scan_generation += 1
        generation = self._scan_generation
        self._refresh_button.setEnabled(False)
        self._populate_table([])
        parent = self.parent()
        hardware_init_running = bool(getattr(parent, "_hardware_init_task", None)) if parent is not None else False
        if hardware_init_running:
            self._status_label.setText("Hardware initialization is running. Showing passive COM inventory.")
            inventory = scan_connected_serial_devices(passive=True)
            self._inventory = [record for record in inventory if isinstance(record, ConnectedSerialDevice)]
            self._populate_table(self._inventory)
            self._status_label.setText(f"Passive inventory completed. {len(self._inventory)} port(s) found.")
            self._refresh_button.setEnabled(True)
            self._active_task = None
            return
        self._status_label.setText("Scanning connected COM ports...")
        task = _HardwareInventoryTask(generation)
        task.signals.finished.connect(self._handle_scan_finished)
        task.signals.failed.connect(self._handle_scan_failed)
        self._active_task = task
        self._thread_pool.start(task)

    def _handle_scan_finished(self, generation: int, inventory: object) -> None:
        if generation != self._scan_generation:
            return
        self._active_task = None
        self._refresh_button.setEnabled(True)
        if not isinstance(inventory, list):
            self._status_label.setText("Scan completed.")
            self._populate_table([])
            return
        self._inventory = [record for record in inventory if isinstance(record, ConnectedSerialDevice)]
        self._populate_table(self._inventory)
        self._status_label.setText(f"Scan completed. {len(self._inventory)} port(s) found.")

    def _handle_scan_failed(self, generation: int, message: str) -> None:
        if generation != self._scan_generation:
            return
        self._active_task = None
        self._refresh_button.setEnabled(True)
        self._status_label.setText(f"Scan failed: {message}")

    def _selected_port(self) -> str | None:
        selected_row = self._table.currentRow()
        if selected_row < 0:
            return None
        item = self._table.item(selected_row, 0)
        if item is None:
            return None
        port = str(item.text() or "").strip()
        return port or None

    def _populate_table(self, inventory: list[ConnectedSerialDevice]) -> None:
        self._table.setRowCount(len(inventory) if inventory else 1)
        self._table.clearContents()
        self._table.clearSpans()
        if not inventory:
            empty_item = QTableWidgetItem("No COM ports detected.")
            empty_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(0, 0, empty_item)
            self._table.setSpan(0, 0, 1, self._table.columnCount())
            self._selected_port_before_refresh = None
            return

        for row, record in enumerate(inventory):
            assignment = get_port_assignment(record.port)
            values = (
                record.port,
                record.description,
                record.hwid,
                record.recognized_device,
                record.recognition_state,
                _format_details(record),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if column == 4:
                    if record.recognition_state == "confirmed":
                        item.setForeground(QColor("#1B7F3A"))
                    elif record.recognition_state == "probable":
                        item.setForeground(QColor("#9C6F00"))
                    else:
                        item.setForeground(QColor("#6C737F"))
                self._table.setItem(row, column, item)
            assignment_combo = QComboBox(self._table)
            assignment_combo.addItem("Auto", "auto")
            assignment_combo.addItem("Pump controller", "pump")
            assignment_combo.addItem("Valve controller", "valve")
            assignment_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            assignment_combo.setMinimumWidth(150)
            combo_index = assignment_combo.findData(assignment)
            assignment_combo.setCurrentIndex(combo_index if combo_index >= 0 else 0)
            assignment_combo.setToolTip(f"Manual assignment for {record.port}.")
            self._apply_assignment_visual(assignment_combo, assignment)
            assignment_combo.currentIndexChanged.connect(
                lambda _index, port=record.port, combo=assignment_combo, row=row: self._apply_row_assignment(
                    row, port, combo.currentData()
                )
            )
            self._table.setCellWidget(row, 4, assignment_combo)
        self._table.resizeColumnsToContents()
        if self._selected_port_before_refresh:
            index = next((row for row, record in enumerate(inventory) if record.port == self._selected_port_before_refresh), -1)
            if index >= 0:
                self._table.selectRow(index)
                self._table.setCurrentCell(index, 0)
                self._selected_port_before_refresh = None
        if self._selected_port_before_refresh:
            self._selected_port_before_refresh = None

    def _apply_row_assignment(self, row: int, port: str, raw_assignment: object) -> None:
        assignment = str(raw_assignment or "auto")
        if assignment == "auto":
            clear_port_assignment(port)
        else:
            set_port_assignment(port, assignment)
        if 0 <= row < len(self._inventory):
            self._inventory[row].manual_assignment = assignment
        cell_widget = self._table.cellWidget(row, 4)
        if isinstance(cell_widget, QComboBox):
            self._apply_assignment_visual(cell_widget, assignment)
        self._status_label.setText(f"Assigned {port} as {device_assignment_label(assignment)}.")

    def _apply_assignment_visual(self, widget: QComboBox, assignment: object) -> None:
        normalized = str(assignment or "auto").strip().casefold()
        if normalized == "pump":
            widget.setStyleSheet(
                "QComboBox { background-color: #173B26; color: #D7FCE8; border: 1px solid #2F7D4A; padding-left: 4px; }"
                "QComboBox::drop-down { border: 0px; width: 18px; }"
            )
        elif normalized == "valve":
            widget.setStyleSheet(
                "QComboBox { background-color: #3D300D; color: #FFF1C7; border: 1px solid #9C6F00; padding-left: 4px; }"
                "QComboBox::drop-down { border: 0px; width: 18px; }"
            )
        else:
            widget.setStyleSheet(
                "QComboBox { background-color: #1A2028; color: #E5EDF7; border: 1px solid #4B5563; padding-left: 4px; }"
                "QComboBox::drop-down { border: 0px; width: 18px; }"
            )


def show_hardware_inventory_dialog(window) -> HardwareInventoryDialog:
    dialog = getattr(window, "_hardware_inventory_dialog", None)
    if not isinstance(dialog, HardwareInventoryDialog):
        dialog = HardwareInventoryDialog(window)
        window._hardware_inventory_dialog = dialog
    if dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
    else:
        dialog.show()
    dialog.refresh_inventory()
    return dialog
