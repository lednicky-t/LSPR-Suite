from __future__ import annotations

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtWidgets import QApplication
from time import perf_counter
from copy import deepcopy

from lspr_app.domain.pump_plan import to_core_experiment_plan
from lspr_app.gui.main_window_processing import normalize_sensorgram_metric_name, sensorgram_metric_order, sync_legacy_metric_widgets_from_state
from lspr_app.storage.app_config import save_acquisition_state, save_window_ui_state
from lspr_core import LAUNCH_PROFILE_CONTROL_EDITOR, LAUNCH_PROFILE_FULL, LAUNCH_PROFILE_SIMULATION, launch_profile_spec, DEFAULT_LAUNCH_PROFILE


def normalize_top_content_mode(mode: str | None) -> str:
    text = str(mode or "spectra").strip().lower()
    if text in {"flow", "experiment", "experimental", "experimental_control", "control"}:
        return "experimental_control"
    return "spectra"


LAYOUT_PRESET_KEYS = ("spectra", "control", "measurement")


def normalize_layout_preset_key(key: str | None) -> str:
    text = str(key or "spectra").strip().lower().replace(" ", "_").replace("-", "_")
    return text if text in LAYOUT_PRESET_KEYS else "spectra"


def _default_layout_preset_snapshot(key: str) -> dict[str, object]:
    preset_key = normalize_layout_preset_key(key)
    if preset_key == "control":
        return {
            "layout_variant": "standard",
            "top_view_mode": "experimental_control",
            "left_controls_visible": False,
            "sensorgram_visible": False,
            "experiment_control_view_mode": "full",
            "timeline_label_mode": "comment",
            "left_right_splitter_sizes": [],
            "plot_splitter_sizes": [],
            "measurement_vertical_splitter_sizes": [],
            "measurement_bottom_splitter_sizes": [],
            "collapsible_sections": {},
        }
    if preset_key == "measurement":
        return {
            "layout_variant": "measurement",
            "top_view_mode": "experimental_control",
            "left_controls_visible": False,
            "sensorgram_visible": True,
            "experiment_control_view_mode": "timeline",
            "timeline_label_mode": "comment",
            "left_right_splitter_sizes": [],
            "plot_splitter_sizes": [],
            "measurement_vertical_splitter_sizes": [],
            "measurement_bottom_splitter_sizes": [],
            "collapsible_sections": {},
        }
    return {
        "layout_variant": "standard",
        "top_view_mode": "spectra",
        "left_controls_visible": True,
        "sensorgram_visible": True,
        "experiment_control_view_mode": "full",
        "timeline_label_mode": "comment",
        "left_right_splitter_sizes": [],
        "plot_splitter_sizes": [],
        "measurement_vertical_splitter_sizes": [],
        "measurement_bottom_splitter_sizes": [],
        "collapsible_sections": {},
    }


def _default_layout_presets() -> dict[str, dict[str, object]]:
    return {key: deepcopy(_default_layout_preset_snapshot(key)) for key in LAYOUT_PRESET_KEYS}


def _coerce_layout_preset_snapshot(key: str, raw: object) -> dict[str, object]:
    snapshot = _default_layout_preset_snapshot(key)
    if not isinstance(raw, dict):
        return snapshot
    for field in snapshot:
        if field in raw:
            snapshot[field] = deepcopy(raw[field])
    return snapshot


def _current_experiment_control_snapshot(window) -> dict[str, object]:
    control = getattr(window, "_experiment_control_window", None)
    if control is None:
        return {}
    snapshot: dict[str, object] = {}
    if hasattr(control, "_experiment_control_view_mode"):
        snapshot["experiment_control_view_mode"] = str(getattr(control, "_experiment_control_view_mode", "full"))
    if hasattr(control, "_experiment_control_timeline_label_mode"):
        snapshot["timeline_label_mode"] = str(getattr(control, "_experiment_control_timeline_label_mode", "comment"))
    if hasattr(control, "_experiment_control_view_mode_sizes"):
        snapshot["experiment_control_view_mode_sizes"] = deepcopy(
            getattr(control, "_experiment_control_view_mode_sizes", {})
        )
    if hasattr(control, "_experiment_control_view_mode_panel_sizes"):
        snapshot["experiment_control_view_mode_panel_sizes"] = deepcopy(
            getattr(control, "_experiment_control_view_mode_panel_sizes", {})
        )
    return snapshot


def _current_layout_preset_snapshot(window) -> dict[str, object]:
    snapshot = _default_layout_preset_snapshot(getattr(window, "_layout_preset_selected", "spectra"))
    right_stack = getattr(window, "_right_content_stack", None)
    measurement_page = getattr(window, "_measurement_right_page", None)
    active_variant = "measurement" if right_stack is not None and right_stack.currentWidget() is measurement_page else "standard"
    snapshot["layout_variant"] = active_variant if active_variant in {"standard", "measurement"} else "standard"
    snapshot["top_view_mode"] = str(getattr(window, "_top_view_mode", "spectra"))
    left_scroll = getattr(window, "_left_controls_scroll", None)
    sensorgram_block = getattr(window, "_sensorgram_block", None)
    snapshot["left_controls_visible"] = bool(left_scroll.isVisible()) if left_scroll is not None else True
    snapshot["sensorgram_visible"] = bool(sensorgram_block.isVisible()) if sensorgram_block is not None else True
    snapshot["left_right_splitter_sizes"] = [int(size) for size in getattr(getattr(window, "left_right_splitter", None), "sizes", lambda: [])()]
    snapshot["plot_splitter_sizes"] = [int(size) for size in getattr(getattr(window, "plot_splitter", None), "sizes", lambda: [])()]
    snapshot["measurement_vertical_splitter_sizes"] = [
        int(size) for size in getattr(getattr(window, "_measurement_vertical_splitter", None), "sizes", lambda: [])()
    ]
    snapshot["measurement_bottom_splitter_sizes"] = [
        int(size) for size in getattr(getattr(window, "_measurement_bottom_splitter", None), "sizes", lambda: [])()
    ]
    snapshot["collapsible_sections"] = collapsible_section_state(window)
    snapshot.update(_current_experiment_control_snapshot(window))
    return snapshot


def _infer_layout_preset_from_current_state(window) -> str:
    top_mode = normalize_top_content_mode(getattr(window, "_top_view_mode", "spectra"))
    left_scroll = getattr(window, "_left_controls_scroll", None)
    sensorgram_block = getattr(window, "_sensorgram_block", None)
    left_visible = bool(left_scroll.isVisible()) if left_scroll is not None else True
    sensor_visible = bool(sensorgram_block.isVisible()) if sensorgram_block is not None else True
    if top_mode == "experimental_control":
        if not left_visible and not sensor_visible:
            return "control"
        if not left_visible and sensor_visible:
            return "measurement"
        return "control"
    return "spectra"


def _default_layout_preset_for_launch_profile(window) -> str:
    profile = launch_profile_settings(window)
    profile_key = str(getattr(profile, "key", "") or "").strip().lower()
    if profile_key == LAUNCH_PROFILE_CONTROL_EDITOR:
        return "control"
    if profile_key == LAUNCH_PROFILE_SIMULATION:
        return "spectra"
    return "spectra"


def load_layout_presets(window) -> dict[str, dict[str, object]]:
    ui_state = window._ui_state if isinstance(getattr(window, "_ui_state", None), dict) else {}
    presets = ui_state.get("layout_presets")
    result = _default_layout_presets()
    if isinstance(presets, dict):
        for key in LAYOUT_PRESET_KEYS:
            result[key] = _coerce_layout_preset_snapshot(key, presets.get(key))
    window._layout_presets = result
    if "layout_preset_selected" in ui_state:
        selected = normalize_layout_preset_key(ui_state.get("layout_preset_selected"))
    else:
        selected = _default_layout_preset_for_launch_profile(window)
    window._layout_preset_selected = selected
    window._layout_preset_active_variant = str(result.get(selected, {}).get("layout_variant", "standard"))
    return result


def save_layout_presets(window) -> None:
    selected = normalize_layout_preset_key(getattr(window, "_layout_preset_selected", "spectra"))
    presets = getattr(window, "_layout_presets", None)
    if not isinstance(presets, dict):
        presets = _default_layout_presets()
    window._layout_presets = {
        key: _coerce_layout_preset_snapshot(key, presets.get(key))
        for key in LAYOUT_PRESET_KEYS
    }
    window._layout_preset_selected = selected


def _set_widget_into_layout(widget, layout) -> None:
    if widget is None or layout is None:
        return
    parent = widget.parentWidget()
    if parent is not None:
        try:
            parent_layout = parent.layout()
        except Exception:
            parent_layout = None
        if parent_layout is not None:
            try:
                parent_layout.removeWidget(widget)
            except Exception:
                pass
        try:
            from PyQt6.QtWidgets import QStackedWidget, QSplitter

            if isinstance(parent, QStackedWidget):
                parent.removeWidget(widget)
            elif isinstance(parent, QSplitter):
                parent.removeWidget(widget)
        except Exception:
            pass
    try:
        widget.setParent(None)
    except Exception:
        pass
    try:
        layout.addWidget(widget)
        widget.setVisible(True)
        widget.updateGeometry()
    except Exception:
        pass


def _set_widget_into_splitter(widget, splitter, index: int | None = None) -> None:
    if widget is None or splitter is None:
        return
    current_index = -1
    try:
        current_index = splitter.indexOf(widget)
    except Exception:
        current_index = -1
    if current_index >= 0:
        return
    parent = widget.parentWidget()
    if parent is not None:
        try:
            parent_layout = parent.layout()
        except Exception:
            parent_layout = None
        if parent_layout is not None:
            try:
                parent_layout.removeWidget(widget)
            except Exception:
                pass
        try:
            from PyQt6.QtWidgets import QStackedWidget

            if isinstance(parent, QStackedWidget):
                parent.removeWidget(widget)
            elif isinstance(parent, type(splitter)):
                parent.removeWidget(widget)
        except Exception:
            pass
    try:
        widget.setParent(None)
    except Exception:
        pass
    try:
        if index is None or index < 0 or index >= splitter.count():
            splitter.addWidget(widget)
        else:
            splitter.insertWidget(index, widget)
        widget.setVisible(True)
        widget.updateGeometry()
    except Exception:
        try:
            splitter.addWidget(widget)
            widget.setVisible(True)
            widget.updateGeometry()
        except Exception:
            pass


def _ensure_standard_layout_page(window) -> None:
    stack = getattr(window, "_right_content_stack", None)
    standard_page = getattr(window, "_standard_right_page", None)
    if stack is not None and standard_page is not None:
        try:
            stack.setCurrentWidget(standard_page)
        except Exception:
            pass


def _ensure_measurement_layout_page(window) -> None:
    stack = getattr(window, "_right_content_stack", None)
    measurement_page = getattr(window, "_measurement_right_page", None)
    if stack is not None and measurement_page is not None:
        try:
            stack.setCurrentWidget(measurement_page)
        except Exception:
            pass


def _restore_standard_widgets(window) -> None:
    top_stack = getattr(window, "_top_content_stack", None)
    spectra_block = getattr(window, "_spectra_block", None)
    if top_stack is not None and spectra_block is not None:
        try:
            if top_stack.indexOf(spectra_block) < 0:
                top_stack.insertWidget(0, spectra_block)
        except Exception:
            try:
                top_stack.addWidget(spectra_block)
            except Exception:
                pass
    trace_block = getattr(window, "_sensorgram_block", None)
    plot_splitter = getattr(window, "plot_splitter", None)
    if plot_splitter is not None and trace_block is not None:
        try:
            if plot_splitter.indexOf(trace_block) < 0:
                plot_splitter.addWidget(trace_block)
        except Exception:
            try:
                plot_splitter.addWidget(trace_block)
            except Exception:
                pass


def _apply_experiment_control_snapshot(window, snapshot: dict[str, object]) -> None:
    control = getattr(window, "_experiment_control_window", None)
    if control is None:
        return
    view_mode = snapshot.get("experiment_control_view_mode")
    if isinstance(view_mode, str) and hasattr(control, "_experiment_control_view_mode"):
        try:
            control._experiment_control_view_mode = control._normalize_experiment_control_view_mode(view_mode)
            control._apply_experiment_control_view_mode(save=False)
        except Exception:
            pass
    timeline_label_mode = snapshot.get("timeline_label_mode")
    if isinstance(timeline_label_mode, str) and hasattr(control, "_experiment_control_timeline_label_mode"):
        try:
            control._experiment_control_timeline_label_mode = control._normalize_experiment_control_timeline_label_mode(
                timeline_label_mode
            )
            control._update_experiment_control_timeline_label_mode()
        except Exception:
            pass
    view_mode_sizes = snapshot.get("experiment_control_view_mode_sizes")
    if isinstance(view_mode_sizes, dict) and hasattr(control, "_experiment_control_view_mode_sizes"):
        try:
            control._experiment_control_view_mode_sizes = control._load_experiment_control_view_mode_sizes(
                {"experiment_control_view_mode_sizes": view_mode_sizes}
            )
        except Exception:
            pass
    view_mode_panel_sizes = snapshot.get("experiment_control_view_mode_panel_sizes")
    if isinstance(view_mode_panel_sizes, dict) and hasattr(control, "_experiment_control_view_mode_panel_sizes"):
        try:
            control._experiment_control_view_mode_panel_sizes = control._load_experiment_control_view_mode_panel_sizes(
                {"experiment_control_view_mode_panel_sizes": view_mode_panel_sizes}
            )
        except Exception:
            pass


def _apply_standard_layout_preset(window, preset_key: str, snapshot: dict[str, object]) -> None:
    _ensure_standard_layout_page(window)
    _restore_standard_widgets(window)
    left_controls_visible = snapshot.get("left_controls_visible")
    if isinstance(left_controls_visible, bool) and hasattr(window, "_left_controls_scroll"):
        window._left_controls_scroll.setVisible(left_controls_visible)
    sensorgram_visible = snapshot.get("sensorgram_visible")
    if isinstance(sensorgram_visible, bool) and hasattr(window, "_sensorgram_block"):
        window._sensorgram_block.setVisible(sensorgram_visible)
    top_mode = snapshot.get("top_view_mode")
    if isinstance(top_mode, str):
        if normalize_top_content_mode(top_mode) == "experimental_control":
            set_top_content_mode(window, "experimental_control", save=False)
        else:
            set_top_content_mode(window, "spectra", save=False)
    else:
        set_top_content_mode(window, "spectra", save=False)
    _apply_experiment_control_snapshot(window, snapshot)
    left_sizes = snapshot.get("left_right_splitter_sizes")
    if (
        isinstance(left_sizes, list)
        and len(left_sizes) == 2
        and all(isinstance(item, int) and item >= 0 for item in left_sizes)
        and hasattr(window, "left_right_splitter")
    ):
        window.left_right_splitter.setSizes([int(left_sizes[0]), int(left_sizes[1])])
    plot_sizes = snapshot.get("plot_splitter_sizes")
    if (
        isinstance(plot_sizes, list)
        and len(plot_sizes) == 2
        and all(isinstance(item, int) and item >= 0 for item in plot_sizes)
        and hasattr(window, "plot_splitter")
    ):
        window.plot_splitter.setSizes([int(plot_sizes[0]), int(plot_sizes[1])])
    elif preset_key == "spectra" and hasattr(window, "plot_splitter"):
        try:
            total = sum(int(size) for size in window.plot_splitter.sizes())
            if total > 0:
                half = max(total // 2, 1)
                window.plot_splitter.setSizes([half, total - half])
        except Exception:
            pass
    elif preset_key == "control" and hasattr(window, "plot_splitter"):
        try:
            total = sum(int(size) for size in window.plot_splitter.sizes())
            if total > 0:
                window.plot_splitter.setSizes([total, 0])
        except Exception:
            pass
    ensure_visible_top_content_splitter(window, mode=getattr(window, "_top_view_mode", "spectra"))


def _apply_measurement_layout_preset(window, snapshot: dict[str, object]) -> None:
    from lspr_app.gui.main_window_lifecycle import ensure_experiment_control_panel_for

    ensure_experiment_control_panel_for(window)
    _ensure_measurement_layout_page(window)
    measurement_top_host = getattr(window, "_measurement_top_host", None)
    measurement_left_host = getattr(window, "_measurement_bottom_left_host", None)
    measurement_right_host = getattr(window, "_measurement_bottom_right_host", None)
    top_layout = measurement_top_host.layout() if measurement_top_host is not None else None
    left_layout = measurement_left_host.layout() if measurement_left_host is not None else None
    right_layout = measurement_right_host.layout() if measurement_right_host is not None else None
    if top_layout is not None and getattr(window, "_experiment_control_window", None) is not None:
        _set_widget_into_layout(window._experiment_control_window, top_layout)
    if left_layout is not None and getattr(window, "_spectra_block", None) is not None:
        _set_widget_into_layout(window._spectra_block, left_layout)
    if right_layout is not None and getattr(window, "_sensorgram_block", None) is not None:
        _set_widget_into_layout(window._sensorgram_block, right_layout)
    _apply_experiment_control_snapshot(window, snapshot)
    if getattr(window, "_experiment_control_window", None) is not None:
        try:
            window._experiment_control_window._apply_experiment_control_view_mode(save=False)
        except Exception:
            pass
    window._top_view_mode = "experimental_control"
    left_controls_visible = snapshot.get("left_controls_visible")
    if isinstance(left_controls_visible, bool) and hasattr(window, "_left_controls_scroll"):
        window._left_controls_scroll.setVisible(left_controls_visible)
    sensorgram_visible = snapshot.get("sensorgram_visible")
    if isinstance(sensorgram_visible, bool) and hasattr(window, "_sensorgram_block"):
        window._sensorgram_block.setVisible(sensorgram_visible)
    vertical_sizes = snapshot.get("measurement_vertical_splitter_sizes")
    if (
        isinstance(vertical_sizes, list)
        and len(vertical_sizes) == 2
        and all(isinstance(item, int) and item >= 0 for item in vertical_sizes)
        and hasattr(window, "_measurement_vertical_splitter")
    ):
        window._measurement_vertical_splitter.setSizes([int(vertical_sizes[0]), int(vertical_sizes[1])])
    else:
        try:
            splitter = getattr(window, "_measurement_vertical_splitter", None)
            if splitter is not None:
                total = sum(int(size) for size in splitter.sizes())
                if total > 0:
                    top = max(total // 3, 180)
                    bottom = max(total - top, 240)
                    splitter.setSizes([top, bottom])
        except Exception:
            pass
    bottom_sizes = snapshot.get("measurement_bottom_splitter_sizes")
    if (
        isinstance(bottom_sizes, list)
        and len(bottom_sizes) == 2
        and all(isinstance(item, int) and item >= 0 for item in bottom_sizes)
        and hasattr(window, "_measurement_bottom_splitter")
    ):
        window._measurement_bottom_splitter.setSizes([int(bottom_sizes[0]), int(bottom_sizes[1])])
    else:
        try:
            splitter = getattr(window, "_measurement_bottom_splitter", None)
            if splitter is not None:
                total = sum(int(size) for size in splitter.sizes())
                if total > 0:
                    half = max(total // 2, 1)
                    splitter.setSizes([half, total - half])
        except Exception:
            pass
    _sync_measurement_visibility(window, True)


def _sync_measurement_visibility(window, visible: bool) -> None:
    page = getattr(window, "_measurement_right_page", None)
    if page is not None:
        page.setVisible(bool(visible))


def apply_layout_preset(window, preset_key: str, *, save: bool = True) -> None:
    selected = normalize_layout_preset_key(preset_key)
    presets = getattr(window, "_layout_presets", None)
    if not isinstance(presets, dict):
        presets = load_layout_presets(window)
    snapshot = _coerce_layout_preset_snapshot(selected, presets.get(selected))
    window._layout_preset_selected = selected
    window._layout_preset_active_variant = str(snapshot.get("layout_variant", "standard"))
    if str(snapshot.get("layout_variant", "standard")) == "measurement":
        _apply_measurement_layout_preset(window, snapshot)
    else:
        _apply_standard_layout_preset(window, selected, snapshot)
        _sync_measurement_visibility(window, False)
    window._sync_view_actions()
    if save:
        save_ui_state(window)


def save_current_layout_to_preset(window, preset_key: str | None = None) -> None:
    selected = normalize_layout_preset_key(preset_key or getattr(window, "_layout_preset_selected", "spectra"))
    presets = getattr(window, "_layout_presets", None)
    if not isinstance(presets, dict):
        presets = load_layout_presets(window)
    snapshot = _current_layout_preset_snapshot(window)
    presets[selected] = snapshot
    window._layout_presets = presets
    window._layout_preset_selected = selected
    window._layout_preset_active_variant = str(snapshot.get("layout_variant", "standard"))
    save_layout_presets(window)
    save_ui_state(window)


def reset_layout_presets_to_defaults(window) -> None:
    window._layout_presets = _default_layout_presets()
    window._layout_preset_selected = normalize_layout_preset_key(getattr(window, "_layout_preset_selected", "spectra"))
    window._layout_preset_active_variant = str(
        window._layout_presets.get(window._layout_preset_selected, {}).get("layout_variant", "standard")
    )
    save_layout_presets(window)
    apply_layout_preset(window, window._layout_preset_selected, save=False)
    save_ui_state(window)


def ensure_visible_top_content_splitter(window, mode: str | None = None) -> None:
    splitter = getattr(window, "plot_splitter", None)
    if splitter is None:
        return

    try:
        sizes = [max(int(size), 0) for size in splitter.sizes()]
    except Exception:
        return
    if len(sizes) != 2:
        return

    normalized = normalize_top_content_mode(mode or getattr(window, "_top_view_mode", "spectra"))
    top_min = 160 if normalized == "experimental_control" else 120
    bottom_min = 180
    if sizes[0] >= top_min and sizes[1] >= bottom_min:
        return

    total = sum(sizes)
    if total <= 0:
        try:
            total = max(int(splitter.height()), int(splitter.sizeHint().height()), top_min + bottom_min)
        except Exception:
            total = top_min + bottom_min

    top_hint = 0
    bottom_hint = 0
    try:
        top_widget = splitter.widget(0)
        if top_widget is not None:
            top_hint = max(
                int(top_widget.sizeHint().height()),
                int(top_widget.minimumSizeHint().height()),
                int(top_widget.minimumHeight()),
            )
        bottom_widget = splitter.widget(1)
        if bottom_widget is not None:
            bottom_hint = max(
                int(bottom_widget.sizeHint().height()),
                int(bottom_widget.minimumSizeHint().height()),
                int(bottom_widget.minimumHeight()),
            )
    except Exception:
        pass

    top_target = max(top_min, top_hint, sizes[0])
    bottom_target = max(bottom_min, bottom_hint, sizes[1])
    if top_target + bottom_target > total:
        top_target = min(top_target, max(total - bottom_min, top_min))
        bottom_target = max(total - top_target, bottom_min)
    if top_target <= 0 or bottom_target <= 0:
        top_target = top_min
        bottom_target = bottom_min
    splitter.setSizes([int(top_target), int(bottom_target)])


def ensure_experimental_control_stack_page(window):
    stack = getattr(window, "_top_content_stack", None)
    if stack is None:
        return None
    if getattr(window, "_experiment_control_window", None) is None:
        from lspr_app.gui.main_window_lifecycle import ensure_experiment_control_panel_for

        ensure_experiment_control_panel_for(window)
    experiment_control_widget = getattr(window, "_experiment_control_window", None)
    if experiment_control_widget is None:
        return None
    placeholder = getattr(window, "_experiment_control_panel_placeholder", None)
    experiment_control_index = stack.indexOf(experiment_control_widget)
    placeholder_index = stack.indexOf(placeholder) if placeholder is not None else -1
    if experiment_control_index < 0:
        if placeholder_index >= 0:
            was_current = stack.currentIndex() == placeholder_index
            placeholder.hide()
            stack.removeWidget(placeholder)
            placeholder.setParent(None)
            stack.insertWidget(placeholder_index, experiment_control_widget)
            if was_current:
                stack.setCurrentWidget(experiment_control_widget)
        else:
            stack.addWidget(experiment_control_widget)
    else:
        if placeholder_index >= 0:
            placeholder.hide()
            stack.removeWidget(placeholder)
            placeholder.setParent(None)
    return experiment_control_widget


def set_top_content_mode(window, mode: str, *, save: bool = True) -> None:
    normalized = normalize_top_content_mode(mode)
    stack = getattr(window, "_top_content_stack", None)
    if stack is None:
        window._top_view_mode = normalized
        return
    if normalized == "spectra":
        if hasattr(window, "_spectra_block"):
            stack.setCurrentWidget(window._spectra_block)
    else:
        widget = ensure_experimental_control_stack_page(window)
        if widget is not None:
            startup_ready = bool(getattr(widget, "_ui_startup_ready", False))
            bootstrap_running = bool(getattr(widget, "_experiment_control_bootstrap_in_progress", False))
            if startup_ready and not bootstrap_running:
                stack.setCurrentWidget(widget)
            else:
                window._pending_top_view_mode = normalized
            ensure_visible_top_content_splitter(window, mode=normalized)
            apply_view_mode = getattr(widget, "_apply_experiment_control_view_mode", None)
            if callable(apply_view_mode):
                try:
                    apply_view_mode(save=False)
                except Exception:
                    pass
    ensure_visible_top_content_splitter(window, mode=normalized)
    window._top_view_mode = normalized
    window._sync_view_actions()
    if save:
        window._schedule_ui_state_persist()


def restore_ui_state(window) -> None:
    ui_state = window._ui_state
    if not ui_state:
        return

    width = ui_state.get("width")
    height = ui_state.get("height")
    x_pos = ui_state.get("x")
    y_pos = ui_state.get("y")
    splitter_sizes = ui_state.get("splitter_sizes")
    plot_splitter_sizes = ui_state.get("plot_splitter_sizes")
    sensorgram_header_splitter_sizes = ui_state.get("sensorgram_header_splitter_sizes")
    session_stats_splitter_sizes = ui_state.get("session_stats_splitter_sizes")
    maximized = ui_state.get("maximized")
    top_view_mode = ui_state.get("top_view_mode")
    sensorgram_content_mode = ui_state.get("sensorgram_content_mode")
    sensorgram_time_axis_mode = ui_state.get("sensorgram_time_axis_mode")
    metric_display_points = ui_state.get("metric_display_points")
    sensorgram_compression_recent_tail_points = ui_state.get("sensorgram_compression_recent_tail_points")
    metric_autoscale_follow_latest_buffer_fraction = ui_state.get("metric_autoscale_follow_latest_buffer_fraction")
    metric_autoscale_min_interval_s = ui_state.get("metric_autoscale_min_interval_s")
    metric_autoscale_throttle_mode = ui_state.get("metric_autoscale_throttle_mode")
    metric_autoscale_skip_tiny_changes_enabled = ui_state.get("metric_autoscale_skip_tiny_changes_enabled")
    sensorgram_metric_colors = ui_state.get("sensorgram_metric_colors")
    sensorgram_metric_envelope_overlay_enabled = ui_state.get("sensorgram_metric_envelope_overlay_enabled")
    sensorgram_metric_envelope_overlay_alpha = ui_state.get("sensorgram_metric_envelope_overlay_alpha")
    sensorgram_control_step_overlay_enabled = ui_state.get("sensorgram_control_step_overlay_enabled")
    sensorgram_control_step_overlay_style = ui_state.get("sensorgram_control_step_overlay_style")
    sensorgram_control_step_overlay_position = ui_state.get("sensorgram_control_step_overlay_position")
    sensorgram_control_step_overlay_opacity = ui_state.get("sensorgram_control_step_overlay_opacity")
    sensorgram_control_step_overlay_bar_height_px = ui_state.get("sensorgram_control_step_overlay_bar_height_px")
    diagnostics_panel_visible = ui_state.get("diagnostics_panel_visible")
    sensorgram_line_mode = ui_state.get("sensorgram_line_mode")
    sensorgram_line_width_px = ui_state.get("sensorgram_line_width_px")
    plot_antialias_enabled = ui_state.get("plot_antialias_enabled")
    sensorgram_heatmap_history_max_rows = ui_state.get("sensorgram_heatmap_history_max_rows")
    sensorgram_frozen = ui_state.get("sensorgram_frozen")
    left_controls_visible = ui_state.get("left_controls_visible")
    sensorgram_visible = ui_state.get("sensorgram_visible")
    sensorgram_visible_modes = ui_state.get("sensorgram_visible_modes")
    sensorgram_primary_mode = ui_state.get("sensorgram_primary_mode")
    trace_stats_metric_name = ui_state.get("trace_stats_metric_name")
    residual_y_range = ui_state.get("residual_y_range")
    residual_visible = bool(ui_state.get("show_residual", False))
    layout_presets = ui_state.get("layout_presets")
    layout_preset_selected = ui_state.get("layout_preset_selected")

    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        app = QApplication.instance()
        if app:
            screen_geometry = app.primaryScreen().availableGeometry()
            margin = 12
            width = min(width, max(screen_geometry.width() - margin * 2, 640))
            height = min(height, max(screen_geometry.height() - margin * 2, 480))
        window.resize(width, height)
    if isinstance(x_pos, int) and isinstance(y_pos, int):
        app = QApplication.instance()
        if app:
            screen_geometry = app.primaryScreen().availableGeometry()
            margin = 12
            window_rect = QRect(x_pos, y_pos, window.width(), window.height())
            if not screen_geometry.intersects(window_rect):
                x_pos = screen_geometry.x() + margin
                y_pos = screen_geometry.y() + margin
            else:
                x_pos = min(
                    max(x_pos, screen_geometry.x() + margin),
                    screen_geometry.x() + screen_geometry.width() - window.width() - margin,
                )
                y_pos = min(
                    max(y_pos, screen_geometry.y() + margin),
                    screen_geometry.y() + screen_geometry.height() - window.height() - margin,
                )
        window.move(x_pos, y_pos)
    if (
        isinstance(splitter_sizes, list)
        and len(splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in splitter_sizes)
    ):
        window.left_right_splitter.setSizes(splitter_sizes)
    if (
        isinstance(plot_splitter_sizes, list)
        and len(plot_splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in plot_splitter_sizes)
    ):
        window.plot_splitter.setSizes(plot_splitter_sizes)
    if (
        isinstance(sensorgram_header_splitter_sizes, list)
        and len(sensorgram_header_splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in sensorgram_header_splitter_sizes)
    ):
        window.sensorgram_header_splitter.setSizes(sensorgram_header_splitter_sizes)
    if (
        isinstance(session_stats_splitter_sizes, list)
        and len(session_stats_splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in session_stats_splitter_sizes)
        and getattr(window, "session_stats_splitter", None) is not None
    ):
        window.session_stats_splitter.setSizes(session_stats_splitter_sizes)
    if isinstance(top_view_mode, str):
        mode = normalize_top_content_mode(top_view_mode)
        if mode == "experimental_control" and not bool(getattr(window, "_ui_startup_ready", False)):
            window._top_view_mode = "experimental_control"
            window._pending_top_view_mode = "experimental_control"
        elif mode == "experimental_control":
            window._activate_experimental_control_view()
        else:
            window._activate_spectra_view()
        ensure_visible_top_content_splitter(window, mode=mode)
    else:
        ensure_visible_top_content_splitter(window, mode=getattr(window, "_top_view_mode", "spectra"))
    if isinstance(left_controls_visible, bool):
        window._left_controls_scroll.setVisible(left_controls_visible)
    if isinstance(sensorgram_visible, bool):
        window._sensorgram_block.setVisible(sensorgram_visible)
    have_new_metric_state = False
    if isinstance(sensorgram_visible_modes, list):
        visible = [normalize_sensorgram_metric_name(mode) for mode in sensorgram_visible_modes]
        ordered = [mode for mode in sensorgram_metric_order(window) if mode in set(visible)]
        if ordered:
            window._sensorgram_metric_visible_modes = set(ordered)
            have_new_metric_state = True
    if isinstance(sensorgram_primary_mode, str):
        primary = normalize_sensorgram_metric_name(sensorgram_primary_mode)
        window._sensorgram_metric_primary_mode = primary
        have_new_metric_state = True
    if hasattr(window, "_sensorgram_metric_visible_modes") and hasattr(window, "_sensorgram_metric_primary_mode"):
        visible = [mode for mode in sensorgram_metric_order(window) if mode in set(window._sensorgram_metric_visible_modes)]
        if not visible:
            visible = [sensorgram_metric_order(window)[0]]
        primary = normalize_sensorgram_metric_name(getattr(window, "_sensorgram_metric_primary_mode", visible[0]))
        if primary not in visible:
            primary = visible[0]
        window._sensorgram_metric_visible_modes = set(visible)
        window._sensorgram_metric_primary_mode = primary
        if isinstance(trace_stats_metric_name, str) and trace_stats_metric_name:
            current_stats = normalize_sensorgram_metric_name(trace_stats_metric_name)
            window._trace_stats_metric_name = current_stats if current_stats in visible else visible[0]
        elif not hasattr(window, "_trace_stats_metric_name") or normalize_sensorgram_metric_name(getattr(window, "_trace_stats_metric_name", visible[0])) not in visible:
            window._trace_stats_metric_name = visible[0]
        sync_legacy_metric_widgets_from_state(window)
        if hasattr(window, "_update_trace_stats"):
            window._update_trace_stats()
    if isinstance(sensorgram_metric_colors, dict):
        metric_colors = getattr(window, "TRACE_METRIC_COLORS", None)
        sensorgram_colors = getattr(window, "SENSORGRAM_TIME_PLOT_COLORS", None)
        if isinstance(metric_colors, dict):
            for mode in sensorgram_metric_order(window):
                color = sensorgram_metric_colors.get(mode)
                if isinstance(color, str) and color:
                    metric_colors[mode] = color
                    if isinstance(sensorgram_colors, dict):
                        sensorgram_colors[mode] = color
        if hasattr(window, "_apply_metric_color_styles"):
            window._apply_metric_color_styles()
    if not have_new_metric_state and isinstance(trace_stats_metric_name, str) and trace_stats_metric_name:
        current_stats = normalize_sensorgram_metric_name(trace_stats_metric_name)
        if current_stats in sensorgram_metric_order(window):
            window._trace_stats_metric_name = current_stats
    if isinstance(sensorgram_content_mode, str):
        window._sensorgram_content_mode = window._normalize_sensorgram_content_mode(sensorgram_content_mode)
        window._apply_sensorgram_content_mode(save=False)
    if isinstance(sensorgram_time_axis_mode, str):
        normalized_time_axis_mode = str(sensorgram_time_axis_mode).strip().lower()
        if normalized_time_axis_mode not in {"elapsed", "clock"}:
            normalized_time_axis_mode = "elapsed"
        window._sensorgram_time_axis_mode = normalized_time_axis_mode
        if hasattr(window, "_apply_sensorgram_time_axis_mode"):
            window._apply_sensorgram_time_axis_mode(redraw=False)
    if isinstance(metric_display_points, (int, float)) and int(metric_display_points) > 0:
        window._plot_display_points = max(int(metric_display_points), 1)
    if isinstance(sensorgram_compression_recent_tail_points, (int, float)) and int(sensorgram_compression_recent_tail_points) >= 0:
        window._sensorgram_compression_recent_tail_points = int(sensorgram_compression_recent_tail_points)
    if isinstance(metric_autoscale_follow_latest_buffer_fraction, (int, float)):
        window._metric_autoscale_follow_latest_buffer_fraction = max(float(metric_autoscale_follow_latest_buffer_fraction), 0.0)
    if isinstance(metric_autoscale_min_interval_s, (int, float)):
        window._metric_autoscale_min_interval_s = max(float(metric_autoscale_min_interval_s), 0.0)
    if isinstance(metric_autoscale_throttle_mode, str) and metric_autoscale_throttle_mode:
        window._metric_autoscale_throttle_mode = metric_autoscale_throttle_mode
    if isinstance(metric_autoscale_skip_tiny_changes_enabled, (bool, str)):
        if isinstance(metric_autoscale_skip_tiny_changes_enabled, bool):
            window._metric_autoscale_skip_tiny_changes_enabled = bool(metric_autoscale_skip_tiny_changes_enabled)
        else:
            window._metric_autoscale_skip_tiny_changes_enabled = str(metric_autoscale_skip_tiny_changes_enabled).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
    if isinstance(sensorgram_metric_envelope_overlay_enabled, (bool, str)):
        if isinstance(sensorgram_metric_envelope_overlay_enabled, bool):
            window._sensorgram_metric_envelope_overlay_enabled = bool(sensorgram_metric_envelope_overlay_enabled)
        else:
            window._sensorgram_metric_envelope_overlay_enabled = str(sensorgram_metric_envelope_overlay_enabled).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        envelope_bands = getattr(window, "trace_metric_envelope_bands", None)
        if isinstance(envelope_bands, dict):
            for band in envelope_bands.values():
                if hasattr(band, "setVisible"):
                    band.setVisible(bool(window._sensorgram_metric_envelope_overlay_enabled))
    if isinstance(sensorgram_metric_envelope_overlay_alpha, (int, float)):
        window._sensorgram_metric_envelope_overlay_alpha = max(min(int(sensorgram_metric_envelope_overlay_alpha), 100), 0)
    if isinstance(sensorgram_control_step_overlay_enabled, (bool, str)):
        if isinstance(sensorgram_control_step_overlay_enabled, bool):
            window._sensorgram_control_step_overlay_enabled = bool(sensorgram_control_step_overlay_enabled)
        else:
            window._sensorgram_control_step_overlay_enabled = str(sensorgram_control_step_overlay_enabled).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        if hasattr(window, "_sync_sensorgram_control_step_overlay"):
            try:
                window._sync_sensorgram_control_step_overlay()
            except Exception:
                pass
    if isinstance(sensorgram_control_step_overlay_style, str) and sensorgram_control_step_overlay_style:
        style = str(sensorgram_control_step_overlay_style).strip().lower()
        if style not in {"background", "bar"}:
            style = "bar"
        window._sensorgram_control_step_overlay_style = style
    if isinstance(sensorgram_control_step_overlay_position, str) and sensorgram_control_step_overlay_position:
        position = str(sensorgram_control_step_overlay_position).strip().lower()
        if position not in {"top", "bottom"}:
            position = "top"
        window._sensorgram_control_step_overlay_position = position
    if isinstance(sensorgram_control_step_overlay_opacity, (int, float)):
        window._sensorgram_control_step_overlay_opacity = max(min(float(sensorgram_control_step_overlay_opacity), 100.0), 0.0)
    if isinstance(sensorgram_control_step_overlay_bar_height_px, (int, float)) and float(sensorgram_control_step_overlay_bar_height_px) > 0:
        window._sensorgram_control_step_overlay_bar_height_px = int(sensorgram_control_step_overlay_bar_height_px)
    if any(
        value is not None
        for value in (
            sensorgram_control_step_overlay_style,
            sensorgram_control_step_overlay_position,
            sensorgram_control_step_overlay_opacity,
            sensorgram_control_step_overlay_bar_height_px,
        )
    ) and hasattr(window, "_sync_sensorgram_control_step_overlay"):
        try:
            window._sync_sensorgram_control_step_overlay()
        except Exception:
            pass
    if isinstance(diagnostics_panel_visible, (bool, str)):
        if isinstance(diagnostics_panel_visible, bool):
            visible = bool(diagnostics_panel_visible)
        else:
            visible = str(diagnostics_panel_visible).strip().lower() in {"1", "true", "yes", "on"}
        if hasattr(window, "_set_diagnostics_panel_visible"):
            try:
                window._set_diagnostics_panel_visible(visible)
            except Exception:
                pass
        elif hasattr(window, "_log_section"):
            window._log_section.setVisible(visible)
        elif hasattr(window, "log_terminal"):
            window.log_terminal.setVisible(visible)
        if hasattr(window, "_apply_metric_color_styles"):
            window._apply_metric_color_styles()
    if isinstance(sensorgram_line_mode, str):
        window._sensorgram_line_step_mode = window._normalize_sensorgram_line_mode(sensorgram_line_mode)
    if isinstance(sensorgram_line_width_px, (int, float)) and float(sensorgram_line_width_px) > 0:
        window._sensorgram_line_width_px = max(float(sensorgram_line_width_px), 0.5)
        if hasattr(window, "_apply_metric_color_styles"):
            window._apply_metric_color_styles()
    if isinstance(plot_antialias_enabled, (bool, str)):
        if isinstance(plot_antialias_enabled, bool):
            window._plot_antialias_enabled = bool(plot_antialias_enabled)
        else:
            window._plot_antialias_enabled = str(plot_antialias_enabled).strip().lower() in {"1", "true", "yes", "on"}
        import pyqtgraph as pg

        pg.setConfigOptions(antialias=window._plot_antialias_enabled)
    if isinstance(sensorgram_heatmap_history_max_rows, (int, float)) and float(sensorgram_heatmap_history_max_rows) > 0:
        window._sensorgram_heatmap_history_max_rows = int(max(int(sensorgram_heatmap_history_max_rows), 16))
    if isinstance(sensorgram_frozen, bool):
        window._sensorgram_frozen = bool(sensorgram_frozen)
        window._update_sensorgram_freeze_button_icon()
    if (
        isinstance(residual_y_range, list)
        and len(residual_y_range) == 2
        and all(isinstance(item, (int, float)) for item in residual_y_range)
    ):
        y_min = float(residual_y_range[0])
        y_max = float(residual_y_range[1])
        if y_max > y_min:
            window._residual_y_range = [y_min, y_max]
            if residual_visible and hasattr(window, "residual_view"):
                window.residual_view.setYRange(y_min, y_max, padding=0.0)
                window._residual_axis_autoscaled = True
    window._start_maximized = bool(maximized)
    if isinstance(layout_presets, dict):
        window._layout_presets = {
            key: _coerce_layout_preset_snapshot(key, layout_presets.get(key))
            for key in LAYOUT_PRESET_KEYS
        }
        has_layout_preset_state = True
    else:
        window._layout_presets = _default_layout_presets()
        has_layout_preset_state = False
    if isinstance(layout_preset_selected, str):
        window._layout_preset_selected = normalize_layout_preset_key(layout_preset_selected)
    else:
        window._layout_preset_selected = _default_layout_preset_for_launch_profile(window)
    window._layout_preset_active_variant = str(
        window._layout_presets.get(window._layout_preset_selected, {}).get("layout_variant", "standard")
    )
    if has_layout_preset_state or isinstance(layout_preset_selected, str):
        if not bool(getattr(window, "_ui_startup_ready", False)):
            window._pending_layout_preset_selected = window._layout_preset_selected
        try:
            apply_layout_preset(window, window._layout_preset_selected, save=False)
        except Exception:
            window._sync_view_actions()
    window._sync_view_actions()


def save_ui_state(window) -> None:
    if window.isMaximized():
        geometry = window.normalGeometry()
        width = geometry.width()
        height = geometry.height()
        x_pos = geometry.x()
        y_pos = geometry.y()
    else:
        width = window.width()
        height = window.height()
        x_pos = window.x()
        y_pos = window.y()

    residual_y_range: list[float] = []
    if hasattr(window, "residual_view") and window.residual_view.isVisible():
        try:
            current_range = window.residual_view.viewRange()[1]
            y_min = float(current_range[0])
            y_max = float(current_range[1])
            if y_max > y_min:
                residual_y_range = [y_min, y_max]
        except Exception:
            residual_y_range = []
    elif isinstance(getattr(window, "_residual_y_range", None), list):
        saved_range = window._residual_y_range
        if len(saved_range) == 2 and all(isinstance(item, (int, float)) for item in saved_range):
            residual_y_range = [float(saved_range[0]), float(saved_range[1])]

    save_window_ui_state(
        "main_window",
        {
            "x": int(x_pos),
            "y": int(y_pos),
            "width": int(width),
            "height": int(height),
            "maximized": bool(window.isMaximized()),
            "splitter_sizes": [int(size) for size in window.left_right_splitter.sizes()],
            "plot_splitter_sizes": [int(size) for size in window.plot_splitter.sizes()],
            "sensorgram_header_splitter_sizes": [int(size) for size in window.sensorgram_header_splitter.sizes()],
            "session_stats_splitter_sizes": [int(size) for size in window.session_stats_splitter.sizes()]
            if hasattr(window, "session_stats_splitter") and window.session_stats_splitter is not None
            else [],
            "top_view_mode": window._top_view_mode,
            "sensorgram_content_mode": window._sensorgram_content_mode,
            "sensorgram_time_axis_mode": str(getattr(window, "_sensorgram_time_axis_mode", "elapsed")),
            "metric_display_points": int(getattr(window, "_plot_display_points", 512)),
            "sensorgram_compression_recent_tail_points": int(
                getattr(window, "_sensorgram_compression_recent_tail_points", 300)
            ),
            "metric_autoscale_follow_latest_buffer_fraction": float(
                getattr(window, "_metric_autoscale_follow_latest_buffer_fraction", 0.05)
            ),
            "metric_autoscale_min_interval_s": float(getattr(window, "_metric_autoscale_min_interval_s", 1.0)),
            "metric_autoscale_throttle_mode": str(getattr(window, "_metric_autoscale_throttle_mode", "Medium")),
            "metric_autoscale_skip_tiny_changes_enabled": bool(
                getattr(window, "_metric_autoscale_skip_tiny_changes_enabled", True)
            ),
            "sensorgram_metric_y_axis_mode": str(getattr(window, "_sensorgram_metric_y_axis_mode", "auto")),
            "sensorgram_metric_colors": {
                mode: str(getattr(window, "TRACE_METRIC_COLORS", {}).get(mode, "#444444"))
                for mode in sensorgram_metric_order(window)
            },
            "sensorgram_metric_envelope_overlay_enabled": bool(
                getattr(window, "_sensorgram_metric_envelope_overlay_enabled", False)
            ),
            "sensorgram_metric_envelope_overlay_alpha": int(
                getattr(window, "_sensorgram_metric_envelope_overlay_alpha", 16)
            ),
            "sensorgram_control_step_overlay_enabled": bool(
                getattr(window, "_sensorgram_control_step_overlay_enabled", True)
            ),
            "sensorgram_control_step_overlay_style": str(
                getattr(window, "_sensorgram_control_step_overlay_style", "bar")
            ),
            "sensorgram_control_step_overlay_position": str(
                getattr(window, "_sensorgram_control_step_overlay_position", "top")
            ),
            "sensorgram_control_step_overlay_opacity": float(
                getattr(window, "_sensorgram_control_step_overlay_opacity", 25.0)
            ),
            "sensorgram_control_step_overlay_bar_height_px": int(
                getattr(window, "_sensorgram_control_step_overlay_bar_height_px", 8)
            ),
            "diagnostics_panel_visible": bool(getattr(window, "_diagnostics_panel_enabled", False)),
            "sensorgram_line_mode": "linear" if getattr(window, "_sensorgram_line_step_mode", None) is None else str(window._sensorgram_line_step_mode),
            "sensorgram_line_width_px": float(getattr(window, "_sensorgram_line_width_px", 2.2)),
            "plot_antialias_enabled": bool(getattr(window, "_plot_antialias_enabled", False)),
            "sensorgram_heatmap_history_max_rows": int(getattr(window, "_sensorgram_heatmap_history_max_rows", 800)),
            "sensorgram_frozen": bool(getattr(window, "_sensorgram_frozen", False)),
            "left_controls_visible": window._left_controls_scroll.isVisible(),
            "sensorgram_visible": window._sensorgram_block.isVisible(),
            "sensorgram_visible_modes": [
                mode
                for mode in sensorgram_metric_order(window)
                if mode in set(getattr(window, "_sensorgram_metric_visible_modes", set()))
            ],
            "sensorgram_primary_mode": normalize_sensorgram_metric_name(
                getattr(window, "_sensorgram_metric_primary_mode", getattr(window, "_trace_stats_metric_name", "smoothed_max"))
            ),
            "trace_stats_metric_name": window._trace_stats_metric_name,
            "residual_y_range": residual_y_range,
            "layout_presets": {
                key: deepcopy(_coerce_layout_preset_snapshot(key, getattr(window, "_layout_presets", {}).get(key)))
                for key in LAYOUT_PRESET_KEYS
            },
            "layout_preset_selected": normalize_layout_preset_key(getattr(window, "_layout_preset_selected", "spectra")),
            "collapsible_sections": collapsible_section_state(window),
        },
    )


def collapsible_section_state(window) -> dict[str, bool]:
    sections: dict[str, bool] = {}
    for key, attr in (
        ("source", "_source_section"),
        ("spectra_processing", "_spectra_processing_section"),
        ("session", "_session_section"),
        ("log", "_log_section"),
    ):
        section = getattr(window, attr, None)
        if section is not None:
            sections[key] = bool(section.is_expanded())
    return sections


def restore_collapsible_section_state(window) -> None:
    state = window._ui_state if isinstance(window._ui_state, dict) else {}
    saved = state.get("collapsible_sections")
    if not isinstance(saved, dict):
        return
    for key, attr in (
        ("source", "_source_section"),
        ("spectra_processing", "_spectra_processing_section"),
        ("session", "_session_section"),
        ("log", "_log_section"),
    ):
        section = getattr(window, attr, None)
        value = saved.get(key)
        if section is not None and isinstance(value, bool):
            section.set_expanded(value)
    spectra_section = getattr(window, "_spectra_processing_section", None)
    if spectra_section is not None and "spectra_processing" not in saved and isinstance(saved.get("processing"), bool):
        spectra_section.set_expanded(bool(saved["processing"]))


def set_gui_housekeeping_enabled(window, enabled: bool) -> None:
    from lspr_app.storage.app_config import save_app_setting

    window._gui_housekeeping_enabled = bool(enabled)
    save_app_setting("gui_housekeeping_enabled", window._gui_housekeeping_enabled)
    state_text = "enabled" if window._gui_housekeeping_enabled else "disabled"
    window.status_label.setText(f"GUI housekeeping {state_text}.")
    window._log_info(f"GUI housekeeping {state_text}.")
    window._request_deferred_ui_refresh(stats=True)


def set_sensorgram_heatmap_enabled(window, enabled: bool) -> None:
    from lspr_app.storage.app_config import save_app_setting

    window._sensorgram_heatmap_enabled = bool(enabled)
    save_app_setting("sensorgram_heatmap_enabled", window._sensorgram_heatmap_enabled)
    state_text = "enabled" if window._sensorgram_heatmap_enabled else "disabled"
    window.status_label.setText(f"Sensorgram heatmap {state_text}.")
    window._log_info(f"Sensorgram heatmap {state_text}.")
    if hasattr(window, "trace_heatmap_notice_item"):
        window.trace_heatmap_notice_item.setVisible(False)
    window._refresh_trace_plot("Metric position (nm)")
    window._request_deferred_ui_refresh(trace_plot=True)


def set_metric_plot_enabled(window, enabled: bool) -> None:
    from lspr_app.storage.app_config import save_app_setting

    window._metric_plot_enabled = bool(enabled)
    save_app_setting("metric_plot_enabled", window._metric_plot_enabled)
    state_text = "enabled" if window._metric_plot_enabled else "disabled"
    window.status_label.setText(f"Metric plot {state_text}.")
    window._log_info(f"Metric plot {state_text}.")
    if hasattr(window, "trace_heatmap_notice_item"):
        window.trace_heatmap_notice_item.setVisible(False)
    window._refresh_trace_plot("Metric position (nm)")
    window._request_deferred_ui_refresh(trace_plot=True)


def toggle_experimental_control_panel_visibility(window, checked: bool | None = None) -> None:
    if checked is None:
        window._activate_experimental_control_view() if normalize_top_content_mode(getattr(window, "_top_view_mode", "spectra")) != "experimental_control" else window._activate_spectra_view()
    elif checked:
        window._activate_experimental_control_view()
    else:
        window._activate_spectra_view()


def toggle_flow_panel_visibility(window, checked: bool | None = None) -> None:
    toggle_experimental_control_panel_visibility(window, checked)


def sync_main_view_visibility(window) -> None:
    if window._main_content_widget is None:
        return
    window._main_content_widget.setVisible(True)
    from lspr_app.gui.main_window_titlebar import refresh_hw_device_status_strip

    refresh_hw_device_status_strip(window)


def show_experimental_control_only(window) -> None:
    window._activate_experimental_control_view()


def show_flow_only(window) -> None:
    show_experimental_control_only(window)


def show_plots_only(window) -> None:
    window._activate_spectra_view()
    window._schedule_ui_state_persist()


def show_split_view(window) -> None:
    window._left_controls_scroll.setVisible(True)
    window._sensorgram_block.setVisible(True)
    window._activate_spectra_view()
    window._schedule_ui_state_persist()


def activate_spectra_view(window) -> None:
    set_top_content_mode(window, "spectra")


def activate_experiment_control_view(window) -> None:
    set_top_content_mode(window, "experimental_control")


def activate_experimental_control_view(window) -> None:
    set_top_content_mode(window, "experimental_control")


def activate_flow_view(window) -> None:
    activate_experiment_control_view(window)


def toggle_left_controls(window, checked: bool | None = None) -> None:
    visible = window._left_controls_scroll.isVisible() if checked is None else bool(checked)
    window._left_controls_scroll.setVisible(visible)
    window._sync_view_actions()
    window._schedule_ui_state_persist()


def toggle_sensorgram(window, checked: bool | None = None) -> None:
    visible = window._sensorgram_block.isVisible() if checked is None else bool(checked)
    window._sensorgram_block.setVisible(visible)
    window._sync_view_actions()
    window._schedule_ui_state_persist()


def set_diagnostics_panel_visible(window, visible: bool) -> None:
    visible = bool(visible)
    window._diagnostics_panel_enabled = visible
    if hasattr(window, "_log_section"):
        window._log_section.setVisible(visible)
    if hasattr(window, "log_terminal"):
        window.log_terminal.setVisible(visible)
    action = getattr(window, "_diagnostics_panel_action", None)
    if action is not None:
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)
    window._schedule_ui_state_persist()


def toggle_diagnostics_panel(window, checked: bool | None = None) -> None:
    visible = window._diagnostics_panel_enabled if checked is None else bool(checked)
    set_diagnostics_panel_visible(window, visible)


def sync_view_actions(window) -> None:
    actions = getattr(window, "_view_menu_actions", None)
    if not isinstance(actions, dict):
        return
    top = actions.get("top_view")
    if isinstance(top, dict):
        current = normalize_top_content_mode(getattr(window, "_top_view_mode", "spectra"))
        for mode, action in top.items():
            action.blockSignals(True)
            action.setChecked(current == normalize_top_content_mode(mode))
            action.blockSignals(False)
    left_action = actions.get("left_controls")
    if left_action is not None:
        left_action.blockSignals(True)
        left_action.setChecked(not window._left_controls_scroll.isHidden())
        left_action.blockSignals(False)
    diagnostics_action = getattr(window, "_diagnostics_panel_action", None)
    if diagnostics_action is not None:
        diagnostics_action.blockSignals(True)
        diagnostics_action.setChecked(bool(getattr(window, "_diagnostics_panel_enabled", False)))
        diagnostics_action.setVisible(not window._left_controls_scroll.isHidden())
        diagnostics_action.blockSignals(False)
    sensor_action = actions.get("sensorgram")
    if sensor_action is not None:
        sensor_action.blockSignals(True)
        sensor_action.setChecked(not window._sensorgram_block.isHidden())
        sensor_action.blockSignals(False)
    preset_actions = actions.get("layout_presets")
    if isinstance(preset_actions, dict):
        selected = normalize_layout_preset_key(getattr(window, "_layout_preset_selected", "spectra"))
        for preset_key, action in preset_actions.items():
            if action is None:
                continue
            action.blockSignals(True)
            action.setChecked(normalize_layout_preset_key(preset_key) == selected)
            action.blockSignals(False)


def sync_diagnostics_panel_action(window) -> None:
    action = getattr(window, "_diagnostics_panel_action", None)
    if action is None:
        return
    action.blockSignals(True)
    action.setChecked(bool(getattr(window, "_diagnostics_panel_enabled", False)))
    action.blockSignals(False)


def launch_profile_settings(window):
    try:
        return object.__getattribute__(window, "_launch_profile_spec")
    except Exception:
        return launch_profile_spec(DEFAULT_LAUNCH_PROFILE)


def apply_launch_profile_layout(window) -> None:
    profile = launch_profile_settings(window)
    if hasattr(window, "_recording_context_row") and window._recording_context_row is not None:
        window._recording_context_row.setVisible(bool(profile.show_recording_context))
    if hasattr(window, "_left_controls_scroll"):
        window._left_controls_scroll.setVisible(bool(profile.show_left_controls))
    if hasattr(window, "_sensorgram_block"):
        window._sensorgram_block.setVisible(bool(profile.show_sensorgram))
    if normalize_top_content_mode(getattr(profile, "initial_top_view_mode", "spectra")) == "experimental_control":
        show_experimental_control_only(window)
    else:
        show_plots_only(window)
    sync_view_actions(window)


def acquisition_state_payload(window) -> dict[str, object]:
    acquisition = window._current_settings()
    simulation = window._simulation_backend.simulation_parameters()
    experiment_control_payload = (
        window._experiment_control_window.switch_solution_hdf5_payload()
        if window._experiment_control_window is not None
        else {
            "switch_solution_mode": False,
            "switch_solution_labels": [f"Solution {index}" for index in range(1, 13)],
            "switch_solution_rows": [[str(index), f"Solution {index}"] for index in range(1, 13)],
            "valve_state_labels": {"Open": "Open", "Close": "Close"},
            "valve_state_colors": {"Open": "#4E79A7", "Close": "#B44A4A"},
        }
    )
    if window._experiment_control_window is not None:
        try:
            experiment_control_payload["plan_rows"] = window._experiment_control_window.current_pump_plan_hdf5_rows()
            experiment_control_payload["selected_plan_row"] = window._experiment_control_window._selected_experiment_control_row()
        except Exception:
            pass
    return {
        "source_mode": window._source_mode,
        "plot_mode": window.plot_selector.currentText(),
        "live_rate_hz": float(window.live_rate_spin.value()),
        "show_residual": bool(window.show_residual_button.isChecked()),
        "freeze_plots": bool(window.freeze_plots_button.isChecked()),
        "session_summary_font_size_pt": float(getattr(window.session_summary, "_panel_font_size_pt", 8.0))
        if getattr(window, "session_summary", None) is not None
        else 8.0,
        "log_terminal_font_size_pt": float(getattr(window.log_terminal, "_panel_font_size_pt", 8.0))
        if getattr(window, "log_terminal", None) is not None
        else 8.0,
        "acquisition": {
            "integration_time_ms": float(acquisition.integration_time_ms),
            "averages": int(acquisition.averages),
            "correct_dark_counts": bool(window.correct_dark_check.isChecked()),
            "correct_nonlinearity": bool(window.correct_nonlinearity_check.isChecked()),
        },
        "simulation": {
            "peak_center_nm": float(simulation.peak_center_nm),
            "peak_width_nm": float(simulation.peak_width_nm),
            "peak_height": float(simulation.peak_height),
            "secondary_peak_offset_nm": float(simulation.secondary_peak_offset_nm),
            "secondary_peak_height_percent": float(simulation.secondary_peak_height_percent),
            "secondary_peak_width_percent": float(simulation.secondary_peak_width_percent),
            "baseline": float(simulation.baseline),
            "slope": float(simulation.slope * 100.0),
            "noise": float(simulation.noise),
            "wavelength_resolution_nm": float(simulation.wavelength_resolution_nm),
            "output_rate_hz": float(window.sim_output_rate_spin.value()),
        },
        "experiment_control": experiment_control_payload,
    }


def resolve_initial_source_mode(window, requested_source_mode: str) -> str:
    profile = getattr(window, "_launch_profile_spec", None)
    profile_key = str(getattr(profile, "key", "") or "").strip().lower()
    requested = str(requested_source_mode or "").strip().lower()
    hardware_available = bool(getattr(window, "_hardware_available", False))

    if profile_key == LAUNCH_PROFILE_SIMULATION:
        return "simulation"
    if profile_key == LAUNCH_PROFILE_FULL:
        return "spectrometer" if hardware_available else "simulation"
    if profile_key == LAUNCH_PROFILE_CONTROL_EDITOR:
        if requested in {"spectrometer", "simulation"}:
            return requested
        return "spectrometer" if hardware_available else "simulation"

    if requested in {"spectrometer", "simulation"}:
        if requested == "spectrometer" and not hardware_available:
            return "simulation"
        return requested
    return "spectrometer" if hardware_available else "simulation"


def persist_acquisition_state(window) -> None:
    if window._suspend_acquisition_autosave or not getattr(window, "_acquisition_state_autosave_enabled", True):
        return
    payload = acquisition_state_payload(window)
    window._acquisition_state = payload
    save_acquisition_state(payload)
    if window._measurement_writer is not None:
        writer_payload = dict(payload)
        experiment_control = dict(payload.get("experiment_control", {}))
        if window._experiment_control_window is not None:
            try:
                experiment_control["experiment_plan"] = to_core_experiment_plan(
                    window._experiment_control_window._read_experiment_control_steps()
                )
                experiment_control["plan_rows"] = window._experiment_control_window.current_pump_plan_hdf5_rows()
                experiment_control["selected_plan_row"] = window._experiment_control_window._selected_experiment_control_row()
            except Exception:
                pass
        writer_payload["experiment_control"] = experiment_control
        window._measurement_writer.update_acquisition_state(writer_payload)


def schedule_acquisition_state_persist(window) -> None:
    if window._suspend_acquisition_autosave or not getattr(window, "_acquisition_state_autosave_enabled", True):
        timer = getattr(window, "_acquisition_state_timer", None)
        if timer is not None:
            timer.stop()
        return
    window._acquisition_state_requested_at = perf_counter()


def apply_acquisition_state_to_widgets(window, state: dict[str, object]) -> None:
    if not state:
        window._update_dark_reference_button_icons()
        window._update_freeze_button_icon()
        window._update_residual_button_icon()
        return

    window._suspend_acquisition_autosave = True
    try:
        plot_mode = str(state.get("plot_mode", "Sample"))
        if plot_mode in window.PLOT_MODES:
            window.plot_selector.blockSignals(True)
            window.plot_selector.setCurrentText(plot_mode)
            window.plot_selector.blockSignals(False)

        live_rate_hz = state.get("live_rate_hz")
        if isinstance(live_rate_hz, (int, float)) and float(live_rate_hz) > 0:
            window.live_rate_spin.setValue(float(live_rate_hz))

        source_mode = resolve_initial_source_mode(window, str(state.get("source_mode", window._source_mode)))

        acquisition = state.get("acquisition", {})
        if isinstance(acquisition, dict):
            integration_time_ms = acquisition.get("integration_time_ms")
            if isinstance(integration_time_ms, (int, float)) and float(integration_time_ms) > 0:
                window.integration_spin.setValue(float(integration_time_ms))
            averages = acquisition.get("averages")
            if isinstance(averages, int) and averages > 0:
                window.averages_spin.setValue(averages)
            if isinstance(acquisition.get("correct_dark_counts"), bool):
                window.correct_dark_check.setChecked(bool(acquisition["correct_dark_counts"]))
            if isinstance(acquisition.get("correct_nonlinearity"), bool):
                window.correct_nonlinearity_check.setChecked(bool(acquisition["correct_nonlinearity"]))

        simulation = state.get("simulation", {})
        if isinstance(simulation, dict):
            peak_center_nm = simulation.get("peak_center_nm")
            if isinstance(peak_center_nm, (int, float)):
                window.sim_peak_center_slider.setValue(int(round(float(peak_center_nm))))
            peak_width_nm = simulation.get("peak_width_nm")
            if isinstance(peak_width_nm, (int, float)):
                window.sim_peak_width_slider.setValue(int(round(float(peak_width_nm))))
            peak_height = simulation.get("peak_height")
            if isinstance(peak_height, (int, float)):
                window.sim_peak_height_slider.setValue(int(round(float(peak_height))))
            secondary_peak_offset_nm = simulation.get("secondary_peak_offset_nm")
            if isinstance(secondary_peak_offset_nm, (int, float)):
                window.sim_secondary_peak_offset_slider.setValue(int(round(float(secondary_peak_offset_nm))))
            secondary_peak_height_percent = simulation.get("secondary_peak_height_percent")
            if isinstance(secondary_peak_height_percent, (int, float)):
                window.sim_secondary_peak_height_slider.setValue(int(round(float(secondary_peak_height_percent))))
            secondary_peak_width_percent = simulation.get("secondary_peak_width_percent")
            if isinstance(secondary_peak_width_percent, (int, float)):
                window.sim_secondary_peak_width_slider.setValue(int(round(float(secondary_peak_width_percent))))
            baseline = simulation.get("baseline")
            if isinstance(baseline, (int, float)):
                window.sim_baseline_slider.setValue(int(round(float(baseline))))
            slope = simulation.get("slope")
            if isinstance(slope, (int, float)):
                window.sim_slope_slider.setValue(int(round(float(slope))))
            noise = simulation.get("noise")
            if isinstance(noise, (int, float)):
                window.sim_noise_slider.setValue(int(round(float(noise))))
            wavelength_resolution_nm = simulation.get("wavelength_resolution_nm")
            if isinstance(wavelength_resolution_nm, (int, float)) and float(wavelength_resolution_nm) > 0:
                window.sim_resolution_spin.setValue(float(wavelength_resolution_nm))
            output_rate_hz = simulation.get("output_rate_hz")
            if isinstance(output_rate_hz, (int, float)) and float(output_rate_hz) > 0:
                window.sim_output_rate_spin.setValue(float(output_rate_hz))

        if source_mode == "simulation":
            window.source_tabs.blockSignals(True)
            window.source_tabs.setCurrentIndex(1)
            window.source_tabs.blockSignals(False)
            window._sync_simulation_backend_from_controls()
        else:
            window.source_tabs.blockSignals(True)
            window.source_tabs.setCurrentIndex(0)
            window.source_tabs.blockSignals(False)
        window._apply_source_mode(source_mode if source_mode in {"spectrometer", "simulation"} else "spectrometer", restart_live=False)

        residual_visible = bool(state.get("show_residual", False))
        window.show_residual_button.blockSignals(True)
        window.show_residual_button.setChecked(residual_visible)
        window.show_residual_button.blockSignals(False)
        window._update_residual_axis_visibility(residual_visible)
        window._update_residual_button_icon()

        frozen = bool(state.get("freeze_plots", False))
        window.freeze_plots_button.blockSignals(True)
        window.freeze_plots_button.setChecked(frozen)
        window.freeze_plots_button.blockSignals(False)
        window._set_plots_frozen(frozen)

        window._update_dark_reference_button_icons()
        window._update_window_mode_label()

        session_font_size = state.get("session_summary_font_size_pt")
        if isinstance(session_font_size, (int, float)):
            apply_font_size = getattr(window, "_apply_text_widget_font_size", None)
            if callable(apply_font_size):
                apply_font_size(window.session_summary, float(session_font_size), minimum=7.0, maximum=16.0)

        log_font_size = state.get("log_terminal_font_size_pt")
        if isinstance(log_font_size, (int, float)):
            apply_font_size = getattr(window, "_apply_text_widget_font_size", None)
            if callable(apply_font_size):
                apply_font_size(window.log_terminal, float(log_font_size), minimum=7.0, maximum=16.0)
    finally:
        window._suspend_acquisition_autosave = False
    schedule_acquisition_state_persist(window)

