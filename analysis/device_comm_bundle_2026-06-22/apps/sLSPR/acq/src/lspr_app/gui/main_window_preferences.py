from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class PreferencesDialog(QDialog):
    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.setMinimumWidth(520)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")

        self.ui_state_autosave_check = QCheckBox("UI state autosave")
        self.acquisition_state_autosave_check = QCheckBox("Acquisition state autosave")
        self.log_buffering_check = QCheckBox("Log buffering")
        self.gui_housekeeping_check = QCheckBox("GUI housekeeping")
        self.sensorgram_heatmap_check = QCheckBox("Sensorgram heatmap")
        self.metric_plot_check = QCheckBox("Metric plot")
        self.processing_debug_check = QCheckBox("Processing debug mode")
        self.hdf5_compression_check = QCheckBox("Measurement HDF5 compression")

        self._build_ui()
        self._load_from_window()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "These are the main application-level preferences currently exposed through the File menu."
        )
        intro.setWordWrap(True)
        intro.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(intro)

        general_box = QGroupBox("General")
        general_layout = QFormLayout(general_box)
        general_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        general_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        general_layout.setHorizontalSpacing(16)
        general_layout.setVerticalSpacing(10)
        general_layout.addRow("Theme", self.theme_combo)
        general_layout.addRow(self.ui_state_autosave_check)
        general_layout.addRow(self.acquisition_state_autosave_check)
        general_layout.addRow(self.log_buffering_check)
        general_layout.addRow(self.gui_housekeeping_check)

        display_box = QGroupBox("Display and processing")
        display_layout = QFormLayout(display_box)
        display_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        display_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        display_layout.setHorizontalSpacing(16)
        display_layout.setVerticalSpacing(10)
        display_layout.addRow(self.sensorgram_heatmap_check)
        display_layout.addRow(self.metric_plot_check)
        display_layout.addRow(self.processing_debug_check)
        display_layout.addRow(self.hdf5_compression_check)

        layout.addWidget(general_box)
        layout.addWidget(display_box)

        footer = QLabel("Changes are applied when you click OK.")
        footer.setWordWrap(True)
        footer.setObjectName("PreferencesFooter")
        layout.addWidget(footer)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_from_window(self) -> None:
        theme = str(getattr(self._window, "_theme_mode", "dark"))
        index = self.theme_combo.findData(theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)

        self.ui_state_autosave_check.setChecked(bool(getattr(self._window, "_ui_state_autosave_enabled", True)))
        self.acquisition_state_autosave_check.setChecked(bool(getattr(self._window, "_acquisition_state_autosave_enabled", True)))
        self.log_buffering_check.setChecked(bool(getattr(self._window, "_log_buffering_enabled", True)))
        self.gui_housekeeping_check.setChecked(bool(getattr(self._window, "_gui_housekeeping_enabled", True)))
        self.sensorgram_heatmap_check.setChecked(bool(getattr(self._window, "_sensorgram_heatmap_enabled", True)))
        self.metric_plot_check.setChecked(bool(getattr(self._window, "_metric_plot_enabled", True)))
        self.processing_debug_check.setChecked(bool(getattr(self._window, "_processing_debug_mode_enabled", False)))
        self.hdf5_compression_check.setChecked(bool(getattr(self._window, "_hdf5_compression_enabled", True)))

    def apply_changes(self) -> None:
        theme = str(self.theme_combo.currentData() or "dark")
        if hasattr(self._window, "set_theme"):
            self._window.set_theme(theme)
        if hasattr(self._window, "_set_ui_state_autosave_enabled"):
            self._window._set_ui_state_autosave_enabled(self.ui_state_autosave_check.isChecked())
        if hasattr(self._window, "_set_acquisition_state_autosave_enabled"):
            self._window._set_acquisition_state_autosave_enabled(self.acquisition_state_autosave_check.isChecked())
        if hasattr(self._window, "_set_log_buffering_enabled"):
            self._window._set_log_buffering_enabled(self.log_buffering_check.isChecked())
        if hasattr(self._window, "_set_gui_housekeeping_enabled"):
            self._window._set_gui_housekeeping_enabled(self.gui_housekeeping_check.isChecked())
        if hasattr(self._window, "_set_sensorgram_heatmap_enabled"):
            self._window._set_sensorgram_heatmap_enabled(self.sensorgram_heatmap_check.isChecked())
        if hasattr(self._window, "_set_metric_plot_enabled"):
            self._window._set_metric_plot_enabled(self.metric_plot_check.isChecked())
        if hasattr(self._window, "_set_processing_debug_mode_enabled"):
            self._window._set_processing_debug_mode_enabled(self.processing_debug_check.isChecked())
        if hasattr(self._window, "_set_measurement_hdf5_compression_enabled"):
            self._window._set_measurement_hdf5_compression_enabled(self.hdf5_compression_check.isChecked())


def show_preferences_dialog_for(window) -> None:
    dialog = PreferencesDialog(window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    dialog.apply_changes()
