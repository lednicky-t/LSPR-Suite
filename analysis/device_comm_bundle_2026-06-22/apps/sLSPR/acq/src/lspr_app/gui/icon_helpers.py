from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from lspr_ui import (
    bulb_icon,
    dark_icon,
    device_status_icon,
    flow_icon,
    flow_tabler_icon,
    reference_icon,
    residual_icon,
    snowflake_icon,
    tabler_icon,
    tint_tabler_icon,
    transport_icon,
    trash_icon,
)

from lspr_app.resources import shared_icon_path


_PRISM_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
  <path d="M2 13h4.45" stroke="#ffffff"/>
  <path d="M18 5l-4.5 6" stroke="#7b3ff2"/>
  <path d="M22 9l-7.75 3.25" stroke="#00c853"/>
  <path d="M22 15l-7 -1.5" stroke="#ff3b30"/>
  <path d="M4.731 19h11.539a1 1 0 0 0 .866 -1.5l-5.769 -10a1 1 0 0 0 -1.732 0l-5.769 10a1 1 0 0 0 .865 1.5" fill="#8edcff" fill-opacity="0.25" stroke="#8edcff"/>
</svg>
"""


def prism_tab_icon() -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_PRISM_ICON_SVG.encode("utf-8")))
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def math_function_tab_icon(color: QColor | None = None) -> QIcon:
    tint = color or QColor("#8a98a8")
    try:
        return tint_tabler_icon(tabler_icon("math_function"), tint)
    except Exception:
        return tint_tabler_icon(tabler_icon("math"), tint)


def storage_compression_icon(active: bool) -> QIcon:
    tint = QColor("#f2c94c" if active else "#b9a24b")
    try:
        return tint_tabler_icon(tabler_icon("file_zip"), tint)
    except Exception:
        return tint_tabler_icon(flow_tabler_icon("archive", "database", "box"), tint)


def storage_save_icon() -> QIcon:
    tint = QColor("#47a861")
    try:
        return tint_tabler_icon(tabler_icon("device_floppy"), tint)
    except Exception:
        return tint_tabler_icon(flow_tabler_icon("device_floppy", "device_save", "floppy"), tint)


def heatmap_icon() -> QIcon:
    return QIcon(str(shared_icon_path("heatmap_icon_clean.svg")))


def reload_icon() -> QIcon:
    tint = QColor("#4da3ff")
    try:
        return tint_tabler_icon(tabler_icon("reload"), tint)
    except Exception:
        return tint_tabler_icon(tabler_icon("refresh"), tint)


__all__ = [
    "bulb_icon",
    "dark_icon",
    "device_status_icon",
    "flow_icon",
    "flow_tabler_icon",
    "math_function_tab_icon",
    "heatmap_icon",
    "reload_icon",
    "prism_tab_icon",
    "reference_icon",
    "residual_icon",
    "snowflake_icon",
    "storage_compression_icon",
    "storage_save_icon",
    "tabler_icon",
    "tint_tabler_icon",
    "transport_icon",
    "trash_icon",
]
