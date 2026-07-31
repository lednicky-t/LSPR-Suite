from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap, QPen
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QStyle


# ---------------------------------------------------------------------------
# Vendored Tabler icons. See icon_assets/ (the .svg files) and ICONS.md (how
# to add a new one) in this package. Deliberately not a dependency on a full
# icon-library package: only the icons this suite actually uses are vendored
# here as individual SVG files, so adding one icon doesn't pull in - or pay
# the startup cost of indexing - several thousand unused ones. See ICONS.md
# for why (a full third-party icon library's first-use directory scan cost
# ~1 second of singleLSPR Acquisition's startup time before this existed).
# ---------------------------------------------------------------------------

_ICON_ASSETS_DIR = Path(__file__).parent / "icon_assets"


@lru_cache(maxsize=None)
def _icon_svg_template(name: str) -> str | None:
    normalized = str(name or "").strip().replace("_", "-").lower()
    if not normalized:
        return None
    path = _ICON_ASSETS_DIR / f"{normalized}.svg"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def available_tabler_icon_names() -> list[str]:
    """Every vendored Tabler icon name available. Used by tests/tooling."""
    return sorted(p.stem for p in _ICON_ASSETS_DIR.glob("*.svg"))


def tabler_icon_svg(
    name: str,
    *,
    color: str | QColor | None = None,
    stroke_width: float | None = None,
    fill: str | None = None,
) -> str | None:
    """Return a vendored Tabler icon's recolored SVG markup, or None if not vendored.

    Exposed for callers that need to render the SVG themselves (e.g. into a
    custom-cropped QRectF) instead of getting a plain QIcon from
    `load_tabler_icon`.
    """
    svg = _icon_svg_template(name)
    if svg is None:
        return None
    resolved_color = color.name() if isinstance(color, QColor) else (color or "#000000")
    # Vendored icons use one of two conventions: outline icons set
    # stroke="currentColor", filled icons set fill="currentColor" - a blanket
    # substitution covers both without needing to know which is which.
    svg = svg.replace("currentColor", str(resolved_color))
    if stroke_width is not None:
        svg = re.sub(r'stroke-width="[0-9.]+"', f'stroke-width="{stroke_width}"', svg, count=1)
    if fill is not None and fill != "none":
        # Only outline icons carry a literal fill="none" left to override;
        # filled icons already got their color from the substitution above.
        svg = svg.replace('fill="none"', f'fill="{fill}"', 1)
    return svg


def load_tabler_icon(
    name: str,
    *,
    color: str | QColor | None = None,
    size: int = 24,
    stroke_width: float | None = None,
    fill: str | None = None,
) -> QIcon:
    """Render a vendored Tabler icon (icon_assets/<name>.svg) as a QIcon.

    *name* accepts hyphens or underscores (tablerqicon's old attribute-name
    convention used underscores; vendored files use Tabler's native
    hyphenated names). Returns an empty QIcon if *name* isn't vendored -
    callers already handle a missing icon the same way the old
    tablerqicon/tabler_icons wrappers did (an invisible/blank icon).
    """
    svg = tabler_icon_svg(name, color=color, stroke_width=stroke_width, fill=fill)
    if svg is None:
        return QIcon()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def tabler_icon(*names: str) -> QIcon:
    for name in names:
        icon = load_tabler_icon(name)
        if not icon.isNull():
            return icon
    return QIcon()


def tint_icon(icon: QIcon, color: QColor, *, size: int = 24) -> QIcon:
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        return icon
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return QIcon(tinted)


def standard_icon(kind: str, *, theme_mode: str = "dark") -> QIcon:
    style = QApplication.style()
    if kind == "minimize":
        return style.standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton)
    if kind == "maximize":
        return style.standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton)
    if kind == "restore":
        return style.standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
    if kind == "close":
        return style.standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
    if kind == "play":
        return tint_icon(tabler_icon("player_play"), QColor("#47a861" if theme_mode == "dark" else "#1f6b35"))
    if kind == "record":
        return tint_icon(tabler_icon("player_record"), QColor("#d84d4d" if theme_mode == "dark" else "#a11d1d"))
    return QIcon()


_APP_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" fill="none">
  <rect x="4" y="4" width="56" height="56" rx="16" fill="#11161f" stroke="#324256" stroke-width="2"/>
  <path d="M16 32h12" stroke="#38bdf8" stroke-width="4" stroke-linecap="round"/>
  <path d="M28 20l10 12-10 12" stroke="#22c55e" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M38 20h10" stroke="#f59e0b" stroke-width="4" stroke-linecap="round"/>
  <circle cx="20" cy="46" r="4" fill="#8b5cf6"/>
</svg>
"""


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_APP_ICON_SVG.encode("utf-8")))
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def window_control_icon(kind: str) -> QIcon:
    color = QColor("#f4f8fc")
    if kind == "minimize":
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton)
        if not icon.isNull():
            return tint_icon(icon, color, size=18)
    if kind == "maximize":
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton)
        if not icon.isNull():
            return tint_icon(icon, color, size=18)
    if kind == "restore":
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
        if not icon.isNull():
            return tint_icon(icon, color, size=18)
    if kind == "close":
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        if not icon.isNull():
            return tint_icon(icon, color, size=18)
    if kind in {"minimize", "maximize", "restore", "close"}:
        return _draw_window_control_icon(kind, color, size=18)
    raise ValueError(f"Unsupported window control icon kind: {kind}")


def _draw_window_control_icon(kind: str, color: QColor, *, size: int = 18) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if kind == "minimize":
            painter.drawLine(4, size - 5, size - 4, size - 5)
        elif kind == "maximize":
            painter.drawRect(4, 4, size - 8, size - 8)
        elif kind == "restore":
            painter.drawRect(5, 4, size - 9, size - 9)
            painter.drawRect(3, 6, size - 9, size - 9)
        elif kind == "close":
            painter.drawLine(4, 4, size - 4, size - 4)
            painter.drawLine(size - 4, 4, 4, size - 4)
    finally:
        painter.end()
    return QIcon(pixmap)


def flow_tabler_icon(*names: str) -> QIcon:
    fallback_map = {
        "square_plus": ("plus",),
        "edit": ("pencil",),
        "copy": ("copy",),
        "file_import": ("upload", "file_download"),
        "file_export": ("download", "file_upload"),
        "upload": ("arrow_up",),
        "download": ("arrow_down",),
        "icons": ("layout_grid",),
        "icons_off": ("layout_distribute_horizontal",),
        "trash": ("trash",),
        "template": ("settings",),
        "settings": ("settings",),
        "x": ("x", "close"),
        "chevron_up": ("chevron_up",),
        "chevron_down": ("chevron_down",),
        "arrow_move_right": ("arrow_move_right",),
        "clock_pause": ("clock_pause", "clock", "player_pause"),
    }
    for name in names:
        candidates = (name,) + fallback_map.get(name, ())
        for candidate in candidates:
            icon = load_tabler_icon(candidate)
            if not icon.isNull():
                return icon
    return QIcon()


def tint_tabler_icon(icon: QIcon, color: QColor) -> QIcon:
    return tint_icon(icon, color)


def _device_status_svg(color: str) -> str:
    return f"""
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path stroke="none" d="M0 0h24v24H0z" fill="none" />
  <path d="m 8.9109993,10.089011 5.0000007,5 -1.5,1.5 a 3.536,3.536 0 1 1 -5.0000007,-5 l 1.5,-1.5" />
  <path d="m 15.089001,13.910989 -5,-4.9999999 1.5,-1.4999999 a 3.536,3.536 0 1 1 5,4.9999998 l -1.5,1.5" />
  <path d="m 4.9109993,19.089011 2.5,-2.5" />
  <path d="m 16.589001,7.4109892 2.5,-2.5" />
</svg>
"""


def device_status_icon(status: bool | str) -> QIcon:
    normalized = str(status).strip().lower() if isinstance(status, str) else ("connected" if status else "disconnected")
    if normalized in {"busy", "moving"}:
        # Still connected, just executing a command (e.g. an M-switch move
        # in flight) - reuses the connected bolt shape, not the plug icon,
        # in a distinct color so it doesn't read as either "ok" or "broken".
        color = "#4da3ff"
    elif normalized in {"connected", "online", "true", "1", "yes"}:
        color = "#47a861"
    elif normalized in {"discovered", "present", "available", "yellow"}:
        color = "#f2c94c"
    else:
        color = "#d65a63"
    if normalized in {"disconnected", "offline", "false", "0", "no"}:
        return tint_tabler_icon(tabler_icon("plug_connected", "plug"), QColor(color))
    svg = _device_status_svg(color)
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def transport_icon(theme_mode: str, kind: str) -> QIcon:
    palette = {
        "play": QColor("#47a861"),
        "hold": QColor("#4f88ff"),
        "record": QColor("#d84d4d"),
        "pause": QColor("#e8d85f"),
        "stop": QColor("#d84d4d"),
        "previous": QColor("#eef3fa" if theme_mode == "dark" else "#243241"),
        "next": QColor("#eef3fa" if theme_mode == "dark" else "#243241"),
        "stop_all": QColor("#d84d4d"),
        "status": QColor("#eef3fa" if theme_mode == "dark" else "#243241"),
    }
    icon_map = {
        "play": "player-play",
        "hold": "clock-stop",
        "record": "player-record-filled",
        "pause": "player-pause",
        "stop": "player-stop",
        "previous": "player-skip-back",
        "next": "player-skip-forward",
        "stop_all": "hand-stop",
        "status": "reload",
    }
    icon = load_tabler_icon(icon_map.get(kind, "player-play"))
    if icon.isNull() and kind == "hold":
        # tablerqicon's old getattr(TablerQIcon, "clock_stop", TablerQIcon.clock)
        # fallback, preserved in case a future icon_assets edit drops clock-stop.
        icon = load_tabler_icon("clock")
    return tint_tabler_icon(icon, palette.get(kind, QColor("#eef3fa" if theme_mode == "dark" else "#243241")))


def bulb_icon(bulb_color: QColor) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    outline_pen = QPen(QColor("#444444"))
    outline_pen.setWidth(2)
    painter.setPen(outline_pen)
    painter.setBrush(QColor(bulb_color))
    painter.drawEllipse(8, 4, 16, 16)

    painter.setBrush(QColor("#7a7a7a"))
    painter.drawRoundedRect(11, 19, 10, 7, 2, 2)
    painter.drawLine(13, 22, 19, 22)
    painter.drawLine(13, 25, 19, 25)
    painter.end()

    return QIcon(pixmap)


def dark_icon(active: bool) -> QIcon:
    return tint_tabler_icon(
        load_tabler_icon("moon-filled" if active else "moon"),
        QColor("#ffffff" if active else "#8a93a0"),
    )


def reference_icon(active: bool) -> QIcon:
    return tint_tabler_icon(
        load_tabler_icon("sun-filled"),
        QColor("#f4c430" if active else "#8a93a0"),
    )


def flow_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#2f80c1"))
    pen.setWidth(3)
    painter.setPen(pen)
    painter.drawLine(7, 16, 15, 16)
    painter.drawLine(15, 16, 22, 10)
    painter.drawLine(15, 16, 22, 22)
    painter.setBrush(QColor("#2f80c1"))
    painter.drawEllipse(4, 13, 6, 6)
    painter.drawEllipse(20, 7, 6, 6)
    painter.drawEllipse(20, 19, 6, 6)
    painter.end()

    return QIcon(pixmap)


def trash_icon(theme_mode: str) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    color = QColor("#eef3fa" if theme_mode == "dark" else "#243241")
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(9, 10, 14, 16, 2, 2)
    painter.drawLine(8, 9, 24, 9)
    painter.drawLine(12, 7, 20, 7)
    painter.drawLine(14, 13, 14, 22)
    painter.drawLine(18, 13, 18, 22)
    painter.end()
    return QIcon(pixmap)


def snowflake_icon(theme_mode: str, active: bool) -> QIcon:
    return tint_tabler_icon(
        load_tabler_icon("snowflake"),
        QColor("#2f80c1" if active else ("#eef3fa" if theme_mode == "dark" else "#8a93a0")),
    )


def residual_icon(active: bool) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    font = QFont("Segoe UI", 10)
    font.setBold(True)
    painter.setFont(font)
    metrics = QFontMetrics(font)
    glyphs = [
        ("y", QColor("#2f80c1" if active else "#8a93a0")),
        ("-", QColor("#a6adba" if active else "#8a93a0")),
        ("y", QColor("#f28e2b" if active else "#8a93a0")),
    ]
    total_width = sum(metrics.horizontalAdvance(ch) for ch, _ in glyphs)
    total_width += 2
    x = (pixmap.width() - total_width) / 2.0
    baseline = (pixmap.height() + metrics.ascent() - metrics.descent()) / 2.0 - 1.0

    for char, color in glyphs:
        painter.setPen(color)
        painter.drawText(int(round(x)), int(round(baseline)), char)
        x += metrics.horizontalAdvance(char) + 1

    painter.end()
    return QIcon(pixmap)
