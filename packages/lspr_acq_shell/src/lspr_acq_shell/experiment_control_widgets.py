"""Small widget and delegate classes for the Experiment Control panel.

These are self-contained UI helpers that do not depend on
``ExperimentControlWindow`` state — they can be imported independently.

Extracted from singleLSPR Acquisition's `gui/experiment_control_widgets.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 1 extraction, 2026-08-09)
verbatim except its `lspr_app.domain.pump_plan` import repointed to
`lspr_acq_shell.pump_plan` (moved in Tier 0) - already self-contained per
this file's own docstring above, confirmed by tracing real usage rather than
trusting that claim at face value.

Classes
-------
``_make_frameless_icon_button``
    Factory for borderless, transparent icon ``QToolButton`` instances.
``_NoFocusItemDelegate``
    Item delegate that suppresses the focus/selection/hover visual state so
    cells look clean without a selection border.
``ExperimentControlTableView``
    ``QTableView`` subclass with keyboard shortcuts (copy/paste/reorder) and a
    pinned horizontal scroll position.
``PlanColorDelegate``
    Delegate that renders a cell as a rounded colour swatch with elided text.
``TubeDiameterComboBox``
    Dropdown restricted to the pump's supported tubing diameters; drop-in
    replacement for the free-entry spinbox it used to be.
"""

from __future__ import annotations

from PyQt6.QtCore import QItemSelectionModel, QModelIndex, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QWidget,
)

from lspr_acq_shell.pump_plan import DEFAULT_TUBE_MM, TUBE_DIAMETER_OPTIONS, nearest_tube_diameter_option


def _make_frameless_icon_button(
    icon: QIcon,
    tooltip: str,
    *,
    size: int = 22,
    parent: QWidget | None = None,
) -> QToolButton:
    """Create a borderless, transparent icon button.

    The button has no frame or background in its normal state; a subtle
    background tint appears on hover, checked, and pressed states via
    an inline stylesheet.
    """
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


class _NoFocusItemDelegate(QStyledItemDelegate):
    """Item delegate that strips focus/selection/hover visual decorations.

    Useful for read-only or purely informational table columns where the
    default Qt selection highlight would be distracting.
    """

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.showDecorationSelected = False
        super().paint(painter, opt, index)


class ExperimentControlTableView(QTableView):
    """``QTableView`` subclass for the plan table.

    Adds:
    * Keyboard shortcuts — ``Ctrl+C`` / ``Ctrl+V`` emit :attr:`copy_requested`
      / :attr:`paste_requested` when edit-mode is active.
    * ``PageUp`` / ``PageDown`` emit :attr:`step_move_requested` for row
      reordering without a drag gesture.
    * :meth:`scrollTo` resets the horizontal scroll position to the left so
      the step number is always visible after a vertical scroll.
    * Convenience row/column accessors (:meth:`currentRow`,
      :meth:`currentColumn`, :meth:`setCurrentCell`, :meth:`selectRow`) that
      guard against invalid indices.
    """

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

    def selectRow(self, row: int) -> None:  # Qt API compatibility, not our naming convention
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
                selection_model.select(
                    index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
                )
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


class PlanColorDelegate(QStyledItemDelegate):
    """Delegate that renders a plan-table color cell as a rounded colour swatch.

    The cell background is filled with the colour value stored in the model,
    and the hex string is drawn in contrasting text (black on light swatches,
    white on dark ones).  A gold border appears when the row is selected.
    """

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


class TubeDiameterComboBox(QComboBox):
    """Dropdown listing only the pump's supported tubing diameters.

    The Reglo ICC's "+" command only accepts 26 exact tube sizes (see
    ``lspr_acq_shell.pump_plan.TUBE_DIAMETER_OPTIONS``) - anything else is
    rejected, which silently skips the rest of that channel's setup. This
    replaces a free-entry ``QDoubleSpinBox`` (0.13-3.17 mm in 0.01 mm steps)
    that let a user dial in hundreds of values the pump would never accept.

    Exposes ``value()`` / ``setValue(float)`` / ``valueChanged(float)`` so it
    drops in wherever the spinbox used to be without touching call sites.
    """

    valueChanged = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMaxVisibleItems(5)  # forces a scrollable popup rather than dumping all 26 rows open
        # Popup items are short ("0.25") but were rendering as a bare "..."
        # for every row - Qt's default item delegate can miscompute the
        # available text width against the ::item { text-align: center; }
        # rule applied later by _style_combo_popup_view() and elide even
        # though the popup itself is plenty wide (maintainer-reported, not
        # reproducible offscreen at 100/125/150% simulated scale, so likely
        # font/Windows-DPI dependent). ElideNone forces the real text to
        # always draw regardless of that width miscalculation.
        self.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        for option in TUBE_DIAMETER_OPTIONS:
            self.addItem(f"{option.mm:.2f}", option.mm)
            index = self.count() - 1
            self.setItemData(
                index,
                f"{option.mm:.2f} mm - Ismatec {option.order_no}\n"
                f"Flow range: {option.min_flow_ul_min:.0f}-{option.max_flow_ul_min:.0f} uL/min",
                Qt.ItemDataRole.ToolTipRole,
            )
        self.setCurrentIndex(self._index_for_mm(DEFAULT_TUBE_MM))
        self.currentIndexChanged.connect(self._emit_value_changed)

    def showPopup(self) -> None:  # pragma: no cover - GUI runtime path
        super().showPopup()
        # Belt-and-suspenders alongside ElideNone above: guarantee the popup
        # itself is never narrower than the closed box, in case the "..."
        # rendering was actually the popup window being sized too small
        # rather than (or in addition to) the delegate's elide calculation.
        popup = self.view().window()
        if popup.width() < self.width():
            popup.setMinimumWidth(self.width())
            popup.resize(self.width(), popup.height())

    def value(self) -> float:
        data = self.currentData()
        return float(data) if data is not None else DEFAULT_TUBE_MM

    def setValue(self, mm: float) -> None:
        index = self._index_for_mm(float(mm))
        if index == self.currentIndex():
            self._emit_value_changed(index)
        else:
            self.setCurrentIndex(index)

    def step(self, delta: int) -> None:
        """Move *delta* entries through the supported-diameter list (e.g. for scroll-wheel cycling)."""
        self.setCurrentIndex(max(0, min(self.currentIndex() + int(delta), self.count() - 1)))

    def _emit_value_changed(self, _index: int) -> None:
        self.valueChanged.emit(self.value())

    @staticmethod
    def _index_for_mm(mm: float) -> int:
        option = nearest_tube_diameter_option(mm)
        return TUBE_DIAMETER_OPTIONS.index(option)
