"""Interactive timeline widget for the Experiment Control panel.

``PumpPlanTimelineWidget`` renders a pump-plan step sequence as a horizontal
colour-coded bar with zoom/pan support, a live progress cursor, and a status
line that shows elapsed time, ETA and step count.

Extracted from singleLSPR Acquisition's `gui/experiment_control_timeline.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 1 extraction, 2026-08-09)
- unlike Tier 0, this is Qt-heavy (custom painting, mouse/wheel event
handling), but was already self-contained (a real `QWidget` subclass with no
window reference, talking outward only through signals/public setters -
confirmed by tracing real `window.`/`self._window` token counts, same
rigor as every prior extraction), so this moved with no rewrite needed -
just its `PumpPlanStep`/`recompute_plan_timing` and
`DeviceLifecycleController`/`SELECTOR` imports repointed to where those
already live in this package.

Public API used by ``ExperimentControlWindow``
----------------------------------------------
``set_steps(steps, selected_row, progress_s, runtime_s, step_runtime_s, already_normalized=False)``
    Replace the displayed steps and repaint. Pass ``already_normalized=True``
    when the caller has already run the steps through
    ``recompute_plan_timing`` (e.g. ``ExperimentControlWindow._read_experiment_control_steps``)
    to skip a redundant recompute/deepcopy pass on every call.
``set_progress(progress_s)``
    Update the progress cursor without replacing steps.
``set_theme(theme_mode)``
    Switch between ``"light"`` and ``"dark"`` colour schemes.
``set_theme_palette(palette)``
    Supply a ``{role: hex_colour}`` dict that overrides the built-in defaults.
``set_time_unit_mode(mode)``
    Set the duration display unit: ``"s"``, ``"min"``, or ``"h"``.
``set_color_palette_entries(entries)``
    Provide a list of ``(name, colour)`` tuples used for the colour-name label
    mode.
``set_label_mode(mode)``
    Switch between ``"comment"`` (step description) and ``"color_name"``
    (palette name lookup).
``set_follow_current_step(enabled)``
    Enable or disable auto-scroll to the active step.
``reset_zoom()``
    Return zoom and pan to the default (fit-all) state.

Signals
-------
``step_activated(int)``
    Emitted on a left-click or drag-hover over a step segment (row index).
``step_double_activated(int)``
    Emitted on a left-button double-click (row index).
``step_reordered(int, int)``
    Emitted when a drag-reorder gesture completes: ``(source_row, target_row)``.
``label_mode_toggled()``
    Emitted when the user clicks the label-mode toggle button.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QToolButton, QToolTip, QWidget

from lspr_acq_shell.device_lifecycle import DeviceLifecycleController
from lspr_acq_shell.device_types import SELECTOR
from lspr_acq_shell.pump_plan import PumpPlanStep, recompute_plan_timing


class PumpPlanTimelineWidget(QWidget):
    """Horizontal, interactive timeline bar for a pump experiment plan.

    Renders a colour-coded strip where each segment represents one
    :class:`~lspr_acq_shell.pump_plan.PumpPlanStep`.  Supports:

    * **Zoom** — mouse-wheel zooms around the cursor position.
    * **Pan** — right-drag scrolls the zoomed view.
    * **Selection** — left-click highlights a step and emits
      :attr:`step_activated`.
    * **Reorder** — left-drag from one segment to another emits
      :attr:`step_reordered` so the parent can update the plan table.
    * **Progress cursor** — a vertical line marks the current elapsed time
      when a plan is running.
    * **Status line** — shows step index, elapsed time, ETA, and totals.

    All colours adapt to the active theme via :meth:`set_theme` and
    :meth:`set_theme_palette`.  Duration labels switch unit with
    :meth:`set_time_unit_mode`.
    """

    # Emitted when the user clicks a step segment or hovers during a drag.
    step_activated = pyqtSignal(int)
    # Emitted on a left-button double-click on a step segment.
    step_double_activated = pyqtSignal(int)
    # Emitted when a drag-reorder gesture finishes: (source_row, target_row).
    step_reordered = pyqtSignal(int, int)
    # Emitted when the user clicks the label-mode toggle button.
    label_mode_toggled = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # ── Plan data ────────────────────────────────────────────────────────
        self._steps: list[PumpPlanStep] = []
        self._selected_row: int | None = None
        # Cached segment geometry — rebuilt each paintEvent.
        self._segment_rects: list[QRectF] = []
        self._bar_rect = QRectF()

        # ── Progress / timing (supplied by parent via set_steps / set_progress)
        self._progress_s: float | None = None
        self._runtime_s: float | None = None
        self._step_runtime_s: float | None = None
        # _plan_active_row is always None on this widget (the parent manages it
        # separately); the first branch in _timeline_status_parts that checks
        # it is therefore unreachable.  _plan_elapsed_s is initialised
        # defensively so it never raises AttributeError if that ever changes.
        self._plan_active_row: int | None = None
        self._plan_elapsed_s: float = 0.0

        # ── Mouse-interaction state ───────────────────────────────────────────
        self._dragging = False
        self._drag_start_row: int | None = None
        self._drag_target_row: int | None = None
        self._drag_mode: str | None = None  # "pan" | "reorder" | None
        self._drag_press_point = None
        self._drag_origin_pan_px = 0.0
        self._hover_row: int | None = None

        # ── Viewport zoom / pan ───────────────────────────────────────────────
        self._zoom_factor = 1.0   # 1.0 = fit all steps; >1 = zoomed in
        self._pan_px = 0.0        # horizontal scroll offset in pixels
        self._min_zoom = 1.0
        self._max_zoom = 24.0
        self._follow_current_step = True

        # ── Visual settings ───────────────────────────────────────────────────
        self._theme_mode = "light"
        self._time_unit_mode = "s"
        self._theme_palette: dict[str, str] = {}
        self._color_palette_entries: list[tuple[str, str]] = []
        # "comment" shows step descriptions; "color_name" looks up palette names.
        self._label_mode = "comment"

        # ── Label-mode toggle button (top-left corner) ────────────────────────
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
        self.setToolTip("Drag a step to reorder it, double-click to edit, right-drag to pan, scroll to zoom.")
        self._update_label_mode_button()

    # ── Public setters ────────────────────────────────────────────────────────

    def set_theme(self, theme_mode: str) -> None:
        """Switch the colour scheme.  *theme_mode* must be ``"light"`` or ``"dark"``."""
        self._theme_mode = theme_mode if theme_mode in {"light", "dark"} else "light"
        self.update()

    def set_theme_palette(self, palette: dict[str, str]) -> None:
        """Override built-in colours with *palette* (``{role: hex_colour}``)."""
        self._theme_palette = dict(palette or {})
        self.update()

    def set_time_unit_mode(self, mode: str) -> None:
        """Set the duration display unit: ``"s"``, ``"min"``, or ``"h"``."""
        self._time_unit_mode = mode if mode in {"s", "min", "h"} else "s"
        self.update()

    def set_color_palette_entries(self, entries: list[tuple[str, str]] | list[dict[str, str]]) -> None:
        """Supply palette entries used for the ``"color_name"`` label mode.

        *entries* may be either ``[(name, colour), ...]`` tuples or
        ``[{"name": ..., "color": ...}, ...]`` dicts.
        """
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
        """Set what text is drawn inside each step segment.

        *mode* is ``"comment"`` (step description) or ``"color_name"``
        (palette name resolved from the step colour).
        """
        normalized = str(mode or "").strip().lower()
        if normalized not in {"comment", "color_name"}:
            normalized = "comment"
        if self._label_mode != normalized:
            self._label_mode = normalized
        self._update_label_mode_button()
        self.update()

    def set_steps(
        self,
        steps: list[PumpPlanStep],
        selected_row: int | None = None,
        progress_s: float | None = None,
        runtime_s: float | None = None,
        step_runtime_s: float | None = None,
        *,
        already_normalized: bool = False,
    ) -> None:
        """Replace the displayed steps and repaint.

        Timing is recomputed via :func:`~lspr_acq_shell.pump_plan.recompute_plan_timing`
        so cumulative ``start_s`` / ``end_s`` are always consistent with the
        current step durations, unless *already_normalized* is ``True`` — pass
        that when the caller ran the steps through ``recompute_plan_timing``
        itself moments ago (e.g. every experiment-control progress tick), so
        this doesn't deepcopy and retime every step a second time.

        Args:
            steps: New plan steps (mutated in-place only to add timing fields).
            selected_row: Row index to highlight; ``None`` for no highlight.
            progress_s: Elapsed seconds to place the progress cursor; ``None``
                hides the cursor.
            runtime_s: Total plan elapsed seconds (for the status line).
            step_runtime_s: Current-step elapsed seconds (for the status line).
            already_normalized: Skip ``recompute_plan_timing`` when the caller
                guarantees *steps* already has consistent timing fields.
        """
        self._steps = steps if already_normalized else recompute_plan_timing(steps)
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
        """Update only the progress cursor without replacing the step list."""
        self._progress_s = progress_s
        if progress_s is not None:
            self._follow_progress_step(progress_s)
        self.update()

    def set_follow_current_step(self, enabled: bool) -> None:
        """Enable or disable auto-scroll to keep the active step in view."""
        self._follow_current_step = bool(enabled)

    def set_zoom_factor(self, factor: float, *, anchor_x: float | None = None) -> None:
        """Zoom to *factor* (1.0 = fit-all, up to ``_max_zoom``), anchored at *anchor_x* pixels."""
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
        """Return zoom and pan to the fit-all (zoom=1) state."""
        self._recalculate_zoom_floor()
        self._zoom_factor = 1.0
        self._pan_px = 0.0
        self._ensure_visible_target()
        self._position_title_button()
        self.update()

    # ── Label-mode button helpers ─────────────────────────────────────────────

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

    # ── Colour helpers ────────────────────────────────────────────────────────

    def _contrast_text_color(self, color: QColor) -> QColor:
        """Return black or white depending on which contrasts better with *color*."""
        if not color.isValid():
            return QColor("#1d2733" if self._theme_mode != "dark" else "#e6ebf1")
        luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        return QColor("#111111" if luminance > 150 else "#ffffff")

    # ── Status text helpers ───────────────────────────────────────────────────

    def _status_time_text(self, label: str, value_s: float | None) -> str:
        if value_s is None:
            return f"{label}: -"
        return f"{label}: {self._format_duration(max(float(value_s), 0.0))}"

    def _status_eta_text(self, label: str, value_s: float | None, clock_text: str | None) -> str:
        if value_s is None or clock_text is None:
            return f"{label}: -"
        return f"{label}: {self._format_duration(max(float(value_s), 0.0))} / {clock_text}"

    def _timeline_status_text(self) -> str:
        parts = self._timeline_status_parts()
        return " | ".join(part["text"] for part in parts)

    def _step_label_text(self, step: PumpPlanStep) -> str:
        """Return the text drawn inside a step segment for the current label mode."""
        if self._label_mode == "color_name":
            color_text = str(step.color or "").strip()
            for name, color in self._color_palette_entries:
                if str(color or "").strip().casefold() == color_text.casefold() and name:
                    return name
            return color_text or "-"
        return str(step.description or "").strip() or "-"

    def _timeline_status_parts(self) -> list[dict[str, object]]:
        """Build the status-line parts list for the header area.

        Each entry is ``{"text": str, "accent": bool, "bold": bool}``.
        Parts 0-2 (step, runtime, ETA) are accented; parts 3-4 (totals) are
        muted.
        """
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
            # Running plan: derive ETA from _plan_active_row + _plan_elapsed_s.
            # This branch is currently unreachable because the parent never sets
            # _plan_active_row on the widget — it is kept for potential future use.
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
            # Selected step (including during a running plan where selected_row == active row).
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
            {"text": self._status_eta_text("ETA", step_eta_s, step_eta_clock), "accent": True, "bold": True},
            {"text": self._status_time_text("Total Runtime", total_runtime_s), "accent": False, "bold": False},
            {"text": self._status_eta_text("Total ETA", total_eta_s, total_eta_clock), "accent": False, "bold": False},
        ]
        return parts

    # ── Font helpers ──────────────────────────────────────────────────────────

    def _scaled_font(self, base_font: QFont, *, delta: float = 0.0, minimum: float = 1.0, bold: bool | None = None) -> QFont:
        """Return a copy of *base_font* with point size adjusted by *delta*."""
        font = QFont(base_font)
        base_size = float(font.pointSizeF())
        if base_size <= 0:
            base_size = 10.0
        new_size = max(base_size + delta, minimum, 1.0)
        font.setPointSizeF(new_size)
        if bold is not None:
            font.setBold(bold)
        return font

    # ── Qt event overrides ────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        dark = self._theme_mode == "dark"
        palette = self._theme_palette or {}
        text_color = QColor(palette.get("fg", "#e6ebf1" if dark else "#1d2733"))
        muted = QColor(palette.get("muted", "#a8b0ba" if dark else "#5f7388"))
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            # ── Header: "Timeline" title + status parts ───────────────────────
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
                part_color = text_color if is_step_part else QColor("#f4f8fc" if dark else "#314355")
                painter.setPen(QPen(part_color))
                painter.drawText(x, title_y, text)
                x += metrics.horizontalAdvance(text) + 10
                # Separator "|" between per-step parts and plan-total parts.
                if index == 2 and index < len(status_parts) - 1:
                    painter.setPen(QPen(muted))
                    painter.drawText(x, title_y, "|")
                    x += painter.fontMetrics().horizontalAdvance("|") + 10

            # ── Timeline bar ──────────────────────────────────────────────────
            left_pad = self._left_pad()
            bar_rect = QRectF(left_pad, 28, self._content_width(), 22)
            self._bar_rect = bar_rect
            painter.setPen(Qt.PenStyle.NoPen)
            self._segment_rects = []

            if not self._steps:
                painter.setPen(QPen(muted))
                painter.setFont(self._scaled_font(self.font(), minimum=9.0))
                # Vertically centered within bar_rect (the same band the step
                # bar itself occupies), not a hardcoded baseline y=56 - this
                # widget's height is pinned to as little as 48px
                # (experiment_control_window.py's
                # timeline_widget.setMinimumHeight(48)), so a fixed y=56
                # baseline drew this text almost entirely below the widget's
                # visible bottom edge - only the tops of a few ascenders
                # peeked through, rendering as a handful of stray dots
                # instead of readable text (reported by the maintainer).
                painter.drawText(
                    QRectF(left_pad, bar_rect.top(), max(self.width() - left_pad * 2, 20), bar_rect.height()),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "No pump-plan steps.",
                )
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
                # Selection highlight
                if index == self._selected_row:
                    painter.setPen(QPen(QColor(255, 255, 255, 255), 2.2))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -2, -2), 4, 4)
                # Step label (elided to fit the segment width)
                label_value = self._step_label_text(step)
                if label_value and label_value != "-" and width >= 32:
                    text_rect = rect.adjusted(3, 3, -3, -3)
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

            # ── Progress cursor ───────────────────────────────────────────────
            if self._progress_s is not None:
                total = max(self._steps[-1].end_s, 1.0)
                clamped = min(max(float(self._progress_s), 0.0), total)
                x_pos = bar_rect.left() + bar_rect.width() * (clamped / total) - self._pan_px
                painter.setPen(QPen(text_color, 2))
                painter.drawLine(
                    int(x_pos), int(bar_rect.top()) - 3,
                    int(x_pos), int(bar_rect.bottom()) + 3,
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
        # Hover tooltip + cursor - only step segments are actually
        # clickable/draggable, so the pointing-hand cursor is scoped to them
        # rather than the whole bar (which also includes empty timeline
        # background where a click is a no-op).
        self._hover_row = None
        for index, rect in enumerate(self._segment_rects):
            if rect.contains(point_f):
                step = self._steps[index]
                self._hover_row = index
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                switch_line = (
                    f"Switch: port {max(min(int(step.switch_position), 12), 1)}\n"
                    if DeviceLifecycleController.shared().is_device_type_enabled(SELECTOR)
                    else ""
                )
                QToolTip.showText(
                    self.mapToGlobal(point),
                    (
                        f"Step {step.step}\n"
                        f"{step.description or '-'}\n"
                        f"Valve: {step.valve or '-'}\n"
                        f"{switch_line}"
                        f"Start: {self._format_duration(step.start_s)}\n"
                        f"End: {self._format_duration(step.end_s)}\n"
                        f"Duration: {self._format_duration(step.duration_s)}"
                    ),
                    self,
                )
                return
        QToolTip.hideText()
        self.unsetCursor()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() == Qt.MouseButton.RightButton and self._steps:
            # Right-drag = pan
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
        if (
            self._dragging
            and self._drag_mode == "reorder"
            and self._drag_start_row is not None
            and self._drag_target_row is not None
            and self._drag_start_row != self._drag_target_row
        ):
            self.step_reordered.emit(self._drag_start_row, self._drag_target_row)
        self._dragging = False
        self._drag_start_row = None
        self._drag_target_row = None
        self._drag_mode = None
        self._drag_press_point = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        """Zoom in/out centred on the mouse position."""
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

    # ── Hit-testing ───────────────────────────────────────────────────────────

    def _row_for_point(self, point_f) -> int | None:
        """Return the step-list row index under *point_f*, or ``None``."""
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

    # ── Duration formatting ───────────────────────────────────────────────────

    def _duration_display_decimals(self) -> int:
        if self._time_unit_mode == "min":
            return 1
        if self._time_unit_mode == "h":
            return 2
        return 0

    def _format_duration(self, seconds: float, *, decimals: int | None = None) -> str:
        """Format *seconds* as a human-readable string using the active time unit."""
        if decimals is None:
            decimals = self._duration_display_decimals()
        seconds = max(float(seconds), 0.0)
        if self._time_unit_mode == "min":
            return f"{seconds / 60.0:.{decimals}f} min"
        if self._time_unit_mode == "h":
            return f"{seconds / 3600.0:.{decimals}f} h"
        value = f"{int(round(seconds))}" if decimals == 0 else f"{seconds:.{decimals}f}"
        return f"{value} s"

    # ── Zoom / pan geometry ───────────────────────────────────────────────────

    def _left_pad(self) -> int:
        """Horizontal padding on each side of the timeline bar in pixels."""
        return 6

    def _recalculate_zoom_floor(self) -> None:
        """Recompute ``_max_zoom`` so step labels remain legible at maximum zoom."""
        if not self._steps:
            self._max_zoom = 24.0
            return
        viewport_width = max(self.width() - 2 * self._left_pad(), 1)
        min_duration = min(
            (float(step.duration_s) for step in self._steps if float(step.duration_s) > 0.0),
            default=0.0,
        )
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
        """Total scrollable width of the timeline bar in pixels."""
        viewport_width = max(self.width() - 2 * self._left_pad(), 1)
        return max(viewport_width * self._zoom_factor, viewport_width)

    def _max_pan(self) -> float:
        """Maximum allowable pan offset so the view does not scroll past the end."""
        viewport_width = max(self.width() - 2 * self._left_pad(), 1)
        return max(self._content_width() - viewport_width, 0.0)

    def _clamp_pan(self) -> None:
        self._pan_px = max(0.0, min(float(self._pan_px), self._max_pan()))

    def _time_at_x(self, x: float, *, total: float, content_width: float | None = None) -> float:
        """Convert a widget x-coordinate to elapsed seconds."""
        if content_width is None:
            content_width = self._content_width()
        rel = (float(x) + self._pan_px - self._left_pad()) / max(content_width, 1.0)
        return min(max(rel, 0.0), 1.0) * total

    def _pan_for_time(self, elapsed_s: float, anchor_x: float, *, total: float, content_width: float | None = None) -> float:
        """Return the pan offset that places *elapsed_s* at *anchor_x* pixels."""
        if content_width is None:
            content_width = self._content_width()
        rel = min(max(float(elapsed_s), 0.0), total) / max(total, 1.0)
        return (self._left_pad() + rel * content_width) - float(anchor_x)

    def _step_row_for_elapsed(self, elapsed_s: float | None) -> int | None:
        """Return the step row that contains *elapsed_s*, or ``None``."""
        if elapsed_s is None or not self._steps:
            return None
        total = max(self._steps[-1].end_s, 1.0)
        clamped = min(max(float(elapsed_s), 0.0), total)
        for index, step in enumerate(self._steps):
            if step.start_s <= clamped < step.end_s or index == len(self._steps) - 1:
                return index
        return None

    def _ensure_step_visible(self, row: int | None, *, center: bool = False) -> None:
        """Scroll the view to ensure *row* is visible; center it if *center* is True."""
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
        """Keep the active or selected step in view after zoom/resize."""
        if self._follow_current_step and self._progress_s is not None:
            self._follow_progress_step(self._progress_s)
        elif self._selected_row is not None:
            self._ensure_step_visible(self._selected_row, center=False)

    def _follow_progress_step(self, progress_s: float) -> None:
        """Scroll so the step containing *progress_s* is centred."""
        if not self._follow_current_step or self._zoom_factor <= 1.0 or not self._steps:
            return
        row = self._step_row_for_elapsed(progress_s)
        self._ensure_step_visible(row, center=True)
