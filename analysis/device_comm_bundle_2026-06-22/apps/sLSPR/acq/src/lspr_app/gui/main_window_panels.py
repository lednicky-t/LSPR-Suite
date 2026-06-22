from __future__ import annotations

from PyQt6.QtWidgets import (
    QLayout,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from lspr_ui import make_info_button

SPECTRA_PROCESSING_SECTION_LABEL_COL_WIDTH = 78
SPECTRA_PROCESSING_SECTION_H_SPACING = 5
SPECTRA_PROCESSING_SECTION_V_SPACING = 3
PROCESSING_CONTROL_COL_WIDTH = SPECTRA_PROCESSING_SECTION_LABEL_COL_WIDTH


def build_spectrometer_page(window) -> QWidget:
    device_group = QGroupBox("Spectrometer device")
    device_layout = QFormLayout()
    device_layout.setHorizontalSpacing(12)
    device_layout.setVerticalSpacing(8)

    integration_row = QHBoxLayout()
    integration_row.setContentsMargins(0, 0, 0, 0)
    integration_row.setSpacing(8)
    integration_row.addWidget(window.integration_spin)
    integration_row.addWidget(window.auto_integration_button)
    integration_row.addStretch(1)
    device_layout.addRow("Integration time", integration_row)

    window.averages_spin.setToolTip("Average this many frames into one displayed spectrum.")
    accumulation_row = QHBoxLayout()
    accumulation_row.setContentsMargins(0, 0, 0, 0)
    accumulation_row.setSpacing(8)
    accumulation_row.addWidget(window.averages_spin)
    accumulation_row.addStretch(1)
    device_layout.addRow("Accumulation", accumulation_row)

    window.correct_dark_check.setText("")
    window.correct_dark_check.setToolTip("Apply dark-count correction to spectrometer data.")
    window.correct_nonlinearity_check.setText("")
    window.correct_nonlinearity_check.setToolTip("Apply nonlinearity correction to spectrometer data.")
    device_layout.addRow("Correct dark counts", window.correct_dark_check)
    device_layout.addRow("Correct nonlinearity", window.correct_nonlinearity_check)
    device_group.setLayout(device_layout)

    acquisition_stats_row = QWidget(window)
    acquisition_stats_layout = QHBoxLayout()
    acquisition_stats_layout.setContentsMargins(0, 0, 0, 0)
    acquisition_stats_layout.setSpacing(8)
    acquisition_stats_layout.addWidget(window.spectrometer_stats_label, 1)
    acquisition_stats_row.setLayout(acquisition_stats_layout)
    acquisition_stats_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    page_layout = QVBoxLayout()
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.addWidget(device_group)
    page_layout.addWidget(acquisition_stats_row)
    page_layout.addStretch(1)
    page = QWidget(window)
    page.setLayout(page_layout)
    return page


def build_simulation_page(window) -> QWidget:
    simulation_group = QGroupBox("Simulation display model")
    simulation_layout = QGridLayout()
    simulation_layout.setHorizontalSpacing(8)
    simulation_layout.setVerticalSpacing(3)

    simulation_info_row = QHBoxLayout()
    simulation_info_row.setContentsMargins(0, 0, 0, 0)
    simulation_info_row.setSpacing(2)
    simulation_info_row.addWidget(
        make_info_button(
            "Synthetic spectrum controls used in Simulation mode. "
            "These settings shape the generated display data, including the primary and secondary peaks, "
            "and do not affect spectrometer hardware."
        )
    )
    simulation_info_row.addStretch(1)
    simulation_layout.addLayout(simulation_info_row, 0, 0, 1, 2)

    _add_sim_section_header(simulation_layout, 1, "Primary peak")
    _add_sim_row(
        simulation_layout,
        2,
        "Peak center",
        window.sim_peak_center_slider,
        window.sim_peak_center_value,
        "Center wavelength of the synthetic spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        3,
        "Peak width",
        window.sim_peak_width_slider,
        window.sim_peak_width_value,
        "Width of the synthetic spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        4,
        "Peak height",
        window.sim_peak_height_slider,
        window.sim_peak_height_value,
        "Peak height in the synthetic display model.",
    )
    _add_sim_section_header(simulation_layout, 5, "Secondary peak")
    _add_sim_row(
        simulation_layout,
        6,
        "2nd center offset",
        window.sim_secondary_peak_offset_slider,
        window.sim_secondary_peak_offset_value,
        "Center-to-center offset of the second peak relative to the first peak.",
    )
    _add_sim_row(
        simulation_layout,
        7,
        "2nd relative height",
        window.sim_secondary_peak_height_slider,
        window.sim_secondary_peak_height_value,
        "Second peak height as a percentage of the first peak height.",
    )
    _add_sim_row(
        simulation_layout,
        8,
        "2nd relative width",
        window.sim_secondary_peak_width_slider,
        window.sim_secondary_peak_width_value,
        "Second peak width as a percentage of the first peak width.",
    )
    _add_sim_row(
        simulation_layout,
        9,
        "Baseline",
        window.sim_baseline_slider,
        window.sim_baseline_value,
        "Baseline offset in the synthetic display model.",
    )
    _add_sim_row(
        simulation_layout,
        10,
        "Relative slope",
        window.sim_slope_slider,
        window.sim_slope_value,
        "Relative linear slope in the synthetic display model, scaled by baseline + peak height across the wavelength span.",
    )
    _add_sim_row(
        simulation_layout,
        11,
        "Noise",
        window.sim_noise_slider,
        window.sim_noise_value,
        "Random noise level added to the synthetic display model.",
    )

    resolution_label = QLabel("Resolution")
    resolution_label.setToolTip("Wavelength spacing of the synthetic spectrum grid.")
    simulation_layout.addWidget(resolution_label, 12, 0)
    window.sim_resolution_spin.setToolTip("Wavelength spacing of the synthetic spectrum grid.")
    simulation_layout.addWidget(window.sim_resolution_spin, 12, 1)
    output_rate_label = QLabel("Output rate")
    output_rate_label.setToolTip(
        "Frame production rate of the simulation backend. This does not affect spectrometer hardware."
    )
    simulation_layout.addWidget(output_rate_label, 13, 0)
    simulation_layout.addWidget(window.sim_output_rate_spin, 13, 1)
    simulation_group.setLayout(simulation_layout)

    page_layout = QVBoxLayout()
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.addWidget(simulation_group)
    page_layout.addStretch(1)
    page = QWidget(window)
    page.setLayout(page_layout)
    return page


def build_spectra_processing_group(window) -> QGroupBox:
    spectra_processing_group = QGroupBox(window)
    spectra_processing_layout = QVBoxLayout()
    spectra_processing_layout.setContentsMargins(0, 0, 0, 0)
    spectra_processing_layout.setSpacing(8)

    spectra_processing_layout.addWidget(_build_processing_range_widget(window))
    spectra_processing_layout.addWidget(
        _build_processing_single_row_widget(
            window,
            "Base removal",
            window.baseline_method_combo,
            "Baseline subtraction method.",
        )
    )
    spectra_processing_layout.addWidget(_build_processing_smoothing_widget(window))
    spectra_processing_layout.addWidget(_build_processing_fitting_widget(window))
    spectra_processing_group.setLayout(spectra_processing_layout)
    return spectra_processing_group


def configure_spectra_processing_group_controls(window) -> None:
    range_width = max(window.range_min_spin.sizeHint().width(), window.range_max_spin.sizeHint().width())
    expanded_range_width = max(int(round(range_width * 0.92)), 36)
    resolution_width = max(int(round(expanded_range_width * 0.78)), 58)
    window.range_min_spin.setFixedWidth(expanded_range_width)
    window.range_max_spin.setFixedWidth(expanded_range_width)
    window.analysis_resolution_spin.setFixedWidth(resolution_width)

    crop_width = max(window.fit_window_spin.sizeHint().width(), window.crop_fraction_spin.sizeHint().width())
    crop_width = max(int(round(crop_width * 0.85)), 72)
    window.fit_window_spin.setFixedWidth(crop_width)
    window.crop_fraction_spin.setFixedWidth(crop_width)
    window.crop_parameter_stack.setFixedWidth(crop_width)

    uniform_controls = (
        window.baseline_method_combo,
        window.smoothing_method_combo,
        window.smoothing_window_spin,
        window.temporal_smoothing_spin,
        window.crop_method_combo,
        window.fit_method_combo,
        window.poly_order_spin,
        window.metric_mode_combo,
    )
    uniform_width = max(control.sizeHint().width() for control in uniform_controls)
    uniform_width = max(int(round(uniform_width * 0.8)), 84)
    for control in uniform_controls:
        control.setFixedWidth(uniform_width)
def _build_processing_single_row_widget(window, title: str, control, tooltip: str) -> QWidget:
    widget = QWidget(window)
    layout = QGridLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(SPECTRA_PROCESSING_SECTION_H_SPACING)
    layout.setVerticalSpacing(SPECTRA_PROCESSING_SECTION_V_SPACING)
    layout.setColumnMinimumWidth(0, SPECTRA_PROCESSING_SECTION_LABEL_COL_WIDTH)
    layout.setColumnStretch(1, 1)

    title_widget = QLabel(title)
    title_widget.setToolTip(tooltip)
    layout.addWidget(title_widget, 0, 0, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    if isinstance(control, QLayout):
        control_widget = QWidget(window)
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(6)
        control_layout.addLayout(control)
        control_layout.addStretch(1)
        control_widget.setLayout(control_layout)
        layout.addWidget(control_widget, 0, 1, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    else:
        layout.addWidget(control, 0, 1, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    widget.setLayout(layout)
    widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return widget


def _build_processing_range_widget(window) -> QWidget:
    range_widget = _build_processing_two_row_grid(
        window,
        "Range",
        [
            ("Min", "Minimum wavelength used for processing and fit range.", window.range_min_spin),
            ("Max", "Maximum wavelength used for processing and fit range.", window.range_max_spin),
            ("Resolution", "Resolution used for peak and centroid analysis.", window.analysis_resolution_spin),
        ],
        section_tooltip="Wavelength range in nm used for processing and fits.",
        section_label_tooltip="Wavelength range in nm used for processing and fits.",
    )
    range_widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return range_widget


def _build_processing_smoothing_widget(window) -> QWidget:
    smoothing_widget = _build_processing_two_row_grid(
        window,
        "Smoothing",
        [
            ("Temporal", "Temporal smoothing of displayed processed spectra.", window.temporal_smoothing_spin),
            ("Spectral", "Spectral smoothing method.", window.smoothing_method_combo),
            ("Window", "Spectral smoothing window size.", window.smoothing_window_spin),
        ],
        section_tooltip="Smoothing settings for displayed and fitted spectra.",
        section_label_tooltip="Smoothing settings for displayed and fitted spectra.",
    )
    smoothing_widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return smoothing_widget
def _build_processing_fitting_widget(window) -> QWidget:
    fitting_widget = QWidget(window)
    fitting_layout = QGridLayout()
    fitting_layout.setContentsMargins(0, 0, 0, 0)
    fitting_layout.setHorizontalSpacing(SPECTRA_PROCESSING_SECTION_H_SPACING)
    fitting_layout.setVerticalSpacing(SPECTRA_PROCESSING_SECTION_V_SPACING)
    fitting_layout.setColumnMinimumWidth(0, SPECTRA_PROCESSING_SECTION_LABEL_COL_WIDTH)
    fitting_layout.setColumnMinimumWidth(1, PROCESSING_CONTROL_COL_WIDTH)
    fitting_layout.setColumnMinimumWidth(2, PROCESSING_CONTROL_COL_WIDTH)
    fitting_layout.setColumnMinimumWidth(3, PROCESSING_CONTROL_COL_WIDTH)
    fitting_layout.setColumnMinimumWidth(4, PROCESSING_CONTROL_COL_WIDTH)
    fitting_layout.setColumnStretch(5, 1)

    section_label = QLabel("Fitting")
    section_label.setToolTip("Peak fitting configuration.")
    fitting_layout.addWidget(section_label, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    method_title = QLabel("Method")
    method_title.setToolTip("Peak fitting model used to estimate the peak position.")
    fitting_layout.addWidget(method_title, 0, 1, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    fitting_layout.addWidget(window.fit_method_combo, 1, 1, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    order_title = QLabel("Order")
    order_title.setToolTip("Polynomial order used when polynomial fitting is selected.")
    fitting_layout.addWidget(order_title, 0, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    order_row = QWidget(window)
    order_layout = QHBoxLayout()
    order_layout.setContentsMargins(0, 0, 0, 0)
    order_layout.setSpacing(6)
    order_layout.addWidget(window.poly_order_spin)
    order_layout.addStretch(1)
    order_row.setLayout(order_layout)
    fitting_layout.addWidget(order_row, 1, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    crop_title = QLabel("Crop")
    crop_title.setToolTip("Choose how the fit range is cropped around the detected peak.")
    fitting_layout.addWidget(crop_title, 0, 3, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    fitting_layout.addWidget(window.crop_method_combo, 1, 3, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    parameter_title = QLabel("Parameter")
    parameter_title.setToolTip(window.crop_parameter_label.toolTip())
    fitting_layout.addWidget(parameter_title, 0, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    fitting_layout.addWidget(window.crop_parameter_stack, 1, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    window._processing_fit_method_title_widget = method_title
    window._processing_fit_order_title_widget = order_title
    window._processing_fit_crop_title_widget = crop_title
    window._processing_fit_parameter_title_widget = parameter_title

    fitting_widget.setLayout(fitting_layout)
    fitting_widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return fitting_widget


def _build_processing_two_row_grid(
    window,
    section_label: str,
    specs: list[tuple[str | QLabel, str, QWidget]],
    *,
    section_tooltip: str = "",
    section_label_tooltip: str = "",
) -> QWidget:
    widget = QWidget(window)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(SPECTRA_PROCESSING_SECTION_H_SPACING)
    grid.setVerticalSpacing(SPECTRA_PROCESSING_SECTION_V_SPACING)
    grid.setColumnMinimumWidth(0, SPECTRA_PROCESSING_SECTION_LABEL_COL_WIDTH)
    for column in range(1, len(specs) + 1):
        grid.setColumnMinimumWidth(column, PROCESSING_CONTROL_COL_WIDTH)
    grid.setColumnStretch(len(specs) + 1, 1)

    section_widget = QLabel(section_label)
    section_widget.setToolTip(section_label_tooltip or section_tooltip)
    grid.addWidget(section_widget, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    for index, (label, tooltip, control) in enumerate(specs, start=1):
        label_widget = label if isinstance(label, QLabel) else QLabel(str(label))
        label_widget.setToolTip(tooltip)
        control.setToolTip(tooltip)
        grid.addWidget(label_widget, 0, index, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        grid.addWidget(control, 1, index, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    widget.setLayout(grid)
    widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return widget


def _add_sim_row(
    layout: QGridLayout,
    row: int,
    label: str,
    slider,
    value_label: QLabel,
    tooltip: str,
) -> None:
    value_label.setMinimumWidth(70)
    label_widget = QLabel(label)
    label_widget.setToolTip(tooltip)
    slider.setToolTip(tooltip)
    value_label.setToolTip(tooltip)
    layout.addWidget(label_widget, row, 0)
    layout.addWidget(slider, row, 1)
    layout.addWidget(value_label, row, 2)


def _add_sim_section_header(layout: QGridLayout, row: int, text: str) -> None:
    header = QLabel(text)
    header.setStyleSheet("font-weight: 700; margin-top: 2px; margin-bottom: 0px;")
    header.setToolTip(text)
    layout.addWidget(header, row, 0, 1, 3)
