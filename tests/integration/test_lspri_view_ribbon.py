"""The Image panel's View ribbon and its side rails (2026-11-21, reworked 2026-11-22): the View controls live only in a column beside the image (the
top ribbon has no View tab and Preferences no choice), the selection toolbar and the View ribbon are rails that can be docked on either side of
the image and rolled up by their grip, an overlay's icon is its show / hide switch (a left click) and a right-click opens the menu with the rest of
its controls (beside the button, over the image, whichever side the column is on), and the Histogram's "View controls are in front of the person" rule.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import time
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QBoxLayout

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.side_rails import LEFT, RIGHT, valid_side
    from lspr_imaging_app.panels.preferences_dialog import DEFAULT_PREFERENCES, Preferences, PreferencesDialog
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.storage.ui_state_keys import (
        IMAGE_SELECTION_RAIL_OPEN,
        IMAGE_SELECTION_RAIL_SIDE,
        IMAGE_VIEW_RAIL_OPEN,
        IMAGE_VIEW_RAIL_SIDE,
    )
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _press(button: QtWidgets.QWidget, which: Qt.MouseButton) -> None:
    """A mouse press on `button` (what the side ribbon's event filter listens for)."""
    point = QPointF(3.0, 3.0)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, point, point, which, which, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(button, event)


def _mouse(widget: QtWidgets.QWidget, kind: QEvent.Type, global_pos: QPoint) -> None:
    """A left-button mouse event on `widget` at a global position (a drag of the rail's grip: press, move, release)."""
    local = QPointF(widget.mapFromGlobal(global_pos))
    left = Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else left
    which = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else left
    event = QMouseEvent(kind, local, QPointF(global_pos), which, buttons, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(widget, event)


def _drag(grip: QtWidgets.QWidget, to_global: QPoint) -> None:
    """Press the grip, pull it to `to_global` in two steps and let go."""
    start = grip.mapToGlobal(QPoint(grip.width() // 2, grip.height() // 2))
    _mouse(grip, QEvent.Type.MouseButtonPress, start)
    _mouse(grip, QEvent.Type.MouseMove, start + QPoint(30, 0))
    _mouse(grip, QEvent.Type.MouseMove, to_global)
    _mouse(grip, QEvent.Type.MouseButtonRelease, to_global)


def _wait_for_roll(rail, seconds: float = 3.0) -> None:
    """Let a rail's roll-up / roll-down animation run to its end (it is a few event-loop turns, 160 ms)."""
    end = time.monotonic() + seconds
    while rail.is_animating() and time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()


class _PanelCase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.panel = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )
        self.view_tab = self.panel._view_tab
        self.rails = self.panel.side_rails
        self.ribbon = self.panel._view_ribbon
        self.view_rail = self.rails.rail("view")
        self.selection_rail = self.rails.rail("selection")
        self.tool_ribbon = self.panel._tool_ribbon

    def tearDown(self) -> None:
        for rail in (self.view_rail, self.selection_rail):
            rail._animation.stop()
        self.panel.hide()  # a panel left shown prints tracebacks of its ribbons' event filters when the interpreter ends
        self.panel._renderer.stop()
        undo_manager.clear()

    def _show(self) -> None:
        self.panel.resize(760, 560)
        self.panel.show()  # the rails need real places on the screen
        _APP.processEvents()


class ViewRibbonTests(_PanelCase):
    def test_the_top_ribbon_has_no_view_tab_and_the_controls_are_in_the_view_ribbon(self) -> None:
        self.assertNotIn("View", self.tool_ribbon._category_names)
        self.assertTrue(self.ribbon.isAncestorOf(self.view_tab), "the controls are in the column beside the image")
        self.assertTrue(self.view_rail.isAncestorOf(self.ribbon))

    def test_a_saved_view_tab_of_the_top_ribbon_opens_the_first_tab_instead(self) -> None:
        for name in ("View", "Histogram"):
            self.assertFalse(self.tool_ribbon.set_category(name), name)
        self.assertEqual(self.panel.active_ribbon_category(), self.tool_ribbon._category_names[0])

    def test_preferences_has_no_choice_for_it_any_more(self) -> None:
        self.assertFalse(hasattr(DEFAULT_PREFERENCES, "view_placement"))
        self.assertFalse(hasattr(PreferencesDialog(Preferences()), "view_placement_combo"))
        self.assertEqual(PreferencesDialog(Preferences()).values(), Preferences())

    def test_the_controls_are_only_a_column_the_row_look_is_gone(self) -> None:
        self.assertTrue(all(separator._horizontal for separator in self.view_tab._separators), "the dividers are horizontal lines in the column")
        self.assertEqual(self.view_tab._layout.direction(), QBoxLayout.Direction.TopToBottom)
        for retired in ("set_vertical", "is_vertical", "_captions"):
            self.assertFalse(hasattr(self.view_tab, retired), retired)

    def test_the_chunk_icon_stays_greyed_out_without_an_ome_zarr(self) -> None:
        """No OME-Zarr in the test. The chunk icon (and its divider) used to vanish then; since 2026-11-22 it stays, greyed out: its right-click menu holds
        the export's chunk grid, which a TIFF stack has too."""
        chunk = self.view_tab.chunk_grid_controls
        self.assertFalse(self.view_tab._separators[-1].isHidden())
        self.assertFalse(chunk.parentWidget().isHidden())
        self.assertFalse(chunk.isEnabled(), "nothing to show: the dataset has no chunks of its own")
        self.assertIn("no chunks", chunk.toolTip())


class RailTests(_PanelCase):
    """2026-11-22, maintainer request: both side ribbons can be rolled up like the top ribbon and docked on either side of the image."""

    def test_the_toolbar_starts_on_the_left_and_the_view_ribbon_on_the_right_both_rolled_down(self) -> None:
        self.assertEqual(self.rails.sides(), {"selection": LEFT, "view": RIGHT})
        self.assertTrue(self.selection_rail.is_expanded() and self.view_rail.is_expanded())
        self._show()
        image = self.panel._view.geometry()
        self.assertLess(self.selection_rail.geometry().right(), image.left() + 1)
        self.assertGreater(self.view_rail.geometry().left(), image.right() - 1)

    def test_every_rail_is_a_grip_over_its_content_and_the_grip_says_how_to_use_it(self) -> None:
        for rail in (self.selection_rail, self.view_rail):
            self.assertTrue(rail.isAncestorOf(rail.content))
            tip = rail.grip.toolTip()
            for word in ("Drag", "double-click", "right-click"):
                self.assertIn(word, tip, rail.key)

    def test_the_view_ribbon_moves_to_the_left_with_the_same_widgets_and_state(self) -> None:
        controls = self.view_tab.display_range_controls
        controls.toggle_button.setChecked(True)  # fixed display range, 0 to 65535
        self.view_tab.scale_bar_controls.set_visible(True)
        before = (controls.state(), self.view_tab.scale_bar_controls._toggle.isChecked())
        mask_button = self.view_tab.mask_toggle
        for side in (LEFT, RIGHT, LEFT):
            self.rails.set_side("view", side)
            self.assertEqual(self.rails.side_of("view"), side)
            self.assertIs(self.panel._view_tab, self.view_tab)
            self.assertTrue(self.view_rail.isAncestorOf(mask_button), "nothing is copied: it is the same button")
            self.assertEqual((controls.state(), self.view_tab.scale_bar_controls._toggle.isChecked()), before, side)

    def test_the_same_side_again_is_not_a_move_and_an_unknown_side_changes_nothing(self) -> None:
        moved: list[int] = []
        self.rails.arranged.connect(lambda: moved.append(1))
        self.assertFalse(self.rails.set_side("view", RIGHT))
        self.assertFalse(self.rails.set_side("view", "sideways"))
        self.assertEqual(self.rails.side_of("view"), RIGHT)
        self.assertTrue(self.rails.set_side("view", LEFT))
        self.assertEqual(moved, [1])
        self.assertEqual([valid_side(rubbish, LEFT) for rubbish in ("up", "", None, 3)], [LEFT] * 4)

    def test_both_on_the_left_the_toolbar_is_at_the_edge_and_the_view_ribbon_next_to_the_image(self) -> None:
        self.rails.set_side("view", LEFT)
        self._show()
        selection, view, image = self.selection_rail.geometry(), self.view_rail.geometry(), self.panel._view.geometry()
        self.assertEqual(selection.left(), 0)
        self.assertEqual(view.left(), selection.right() + 1)
        self.assertEqual(image.left(), view.right() + 1)

    def test_both_on_the_right_the_order_is_mirrored(self) -> None:
        self.rails.set_side("selection", RIGHT)
        self._show()
        selection, view, image = self.selection_rail.geometry(), self.view_rail.geometry(), self.panel._view.geometry()
        self.assertEqual(selection.right(), self.rails.width() - 1)
        self.assertEqual(view.right(), selection.left() - 1)
        self.assertEqual(image.right(), view.left() - 1)

    def test_the_toolbar_can_go_to_the_right_and_the_view_ribbon_to_the_left(self) -> None:
        self.rails.set_side("selection", RIGHT)
        self.rails.set_side("view", LEFT)
        self._show()
        self.assertLess(self.view_rail.geometry().right(), self.panel._view.geometry().left() + 1)
        self.assertGreater(self.selection_rail.geometry().left(), self.panel._view.geometry().right() - 1)

    def test_the_image_has_no_margin_against_the_toolbar_on_whichever_side_it_is(self) -> None:
        def margins() -> tuple[int, int]:
            box = self.panel._view.ci.layout.getContentsMargins()
            return int(box[0]), int(box[2])

        self.assertEqual(margins(), (0, 9))
        self.rails.set_side("selection", RIGHT)
        self.assertEqual(margins(), (9, 0))
        self.rails.set_side("selection", LEFT)
        self.assertEqual(margins(), (0, 9))

    def test_rolling_a_rail_up_leaves_only_its_handle_and_the_image_gets_the_room(self) -> None:
        self._show()
        before = self.panel._view.width()
        self.assertTrue(self.view_rail.set_expanded(False))
        _wait_for_roll(self.view_rail)
        self.assertTrue(self.view_rail.content.isHidden())
        self.assertEqual((self.view_rail.width(), self.view_rail.height()), (14, 12), "only the handle is left")
        self.assertGreater(self.panel._view.width(), before)
        self.assertIn("Show the view ribbon", self.view_rail.grip.toolTip())
        self.assertTrue(self.view_rail.set_expanded(True))
        _wait_for_roll(self.view_rail)
        self.assertFalse(self.view_rail.content.isHidden())
        self.assertEqual(self.panel._view.width(), before)
        self.assertFalse(self.view_rail.set_expanded(True), "already down")

    def test_a_double_click_on_the_grip_rolls_it_up_and_a_click_on_the_handle_rolls_it_down(self) -> None:
        grip = self.selection_rail.grip
        grip.double_clicked.emit()
        self.assertFalse(self.selection_rail.is_expanded())
        grip.click()
        self.assertTrue(self.selection_rail.is_expanded())
        grip.double_clicked.emit()  # the second click of the double-click that just rolled it down: not a request to roll it up again
        self.assertTrue(self.selection_rail.is_expanded())
        self.selection_rail._expanded_at = float("-inf")
        grip.double_clicked.emit()
        self.assertFalse(self.selection_rail.is_expanded())

    def test_a_click_on_the_grip_of_a_rolled_down_rail_does_nothing(self) -> None:
        self.selection_rail.grip.click()
        self.assertTrue(self.selection_rail.is_expanded())

    def test_dragging_the_grip_to_the_other_half_of_the_image_docks_the_rail_there(self) -> None:
        self._show()
        _drag(self.view_rail.grip, self.rails.mapToGlobal(QPoint(60, 200)))
        self.assertEqual(self.rails.side_of("view"), LEFT)
        self.assertTrue(self.rails._hint.isHidden(), "the frame that showed where it would go is gone")
        _drag(self.view_rail.grip, self.rails.mapToGlobal(QPoint(self.rails.width() - 60, 200)))
        self.assertEqual(self.rails.side_of("view"), RIGHT)

    def test_while_the_grip_is_dragged_a_frame_shows_the_side_it_would_dock_on_and_nothing_moves_yet(self) -> None:
        self._show()
        grip = self.view_rail.grip
        start = grip.mapToGlobal(QPoint(5, 5))
        _mouse(grip, QEvent.Type.MouseButtonPress, start)
        _mouse(grip, QEvent.Type.MouseMove, self.rails.mapToGlobal(QPoint(40, 100)))
        self.assertTrue(grip.is_dragging())
        self.assertFalse(self.rails._hint.isHidden())
        self.assertEqual(self.rails._hint.geometry().left(), 0, "the frame is on the left")
        self.assertEqual(self.rails.side_of("view"), RIGHT, "nothing moves until the button is let go")
        far_right = self.rails.mapToGlobal(QPoint(self.rails.width() - 40, 100))
        _mouse(grip, QEvent.Type.MouseMove, far_right)
        self.assertGreater(self.rails._hint.geometry().left(), self.rails.width() // 2)
        _mouse(grip, QEvent.Type.MouseButtonRelease, far_right)
        self.assertEqual(self.rails.side_of("view"), RIGHT)

    def test_a_small_move_of_the_pointer_is_a_click_not_a_drag(self) -> None:
        self._show()
        self.selection_rail.set_expanded(False, animate=False)
        grip = self.selection_rail.grip
        start = grip.mapToGlobal(QPoint(5, 5))
        _mouse(grip, QEvent.Type.MouseButtonPress, start)
        _mouse(grip, QEvent.Type.MouseMove, start + QPoint(1, 1))
        _mouse(grip, QEvent.Type.MouseButtonRelease, start + QPoint(1, 1))
        self.assertFalse(grip.is_dragging())
        self.assertTrue(self.selection_rail.is_expanded(), "the click on the handle rolled it down")
        self.assertEqual(self.rails.side_of("selection"), LEFT)

    def test_the_grips_menu_names_the_two_commands_and_only_the_other_side(self) -> None:
        self._show()
        cases = (
            (self.selection_rail, LEFT, "Move to the right of the image"),
            (self.view_rail, RIGHT, "Move to the left of the image"),
        )
        for rail, here, there in cases:
            rail._open_menu(rail.mapToGlobal(QPoint(2, 2)))
            menu = rail.findChildren(QtWidgets.QMenu)[-1]
            labels = [action.text() for action in menu.actions()]
            self.assertEqual(labels, [f"Hide the {rail.title.lower()}", there], rail.key)
            self.assertTrue(all(not action.icon().isNull() for action in menu.actions()), "every entry wears an icon")
            self.assertEqual(rail.side(), here)
            menu.hide()

    def test_the_grips_menu_hides_shows_and_moves(self) -> None:
        self._show()
        rail = self.view_rail
        rail._open_menu(rail.mapToGlobal(QPoint(2, 2)))
        rail.findChildren(QtWidgets.QMenu)[-1].actions()[0].trigger()
        self.assertFalse(rail.is_expanded())
        rail._open_menu(rail.mapToGlobal(QPoint(2, 2)))
        menu = rail.findChildren(QtWidgets.QMenu)[-1]
        self.assertEqual(menu.actions()[0].text(), "Show the view ribbon")
        menu.actions()[1].trigger()
        self.assertEqual(self.rails.side_of("view"), LEFT)
        rail._open_menu(rail.mapToGlobal(QPoint(2, 2)))
        rail.findChildren(QtWidgets.QMenu)[-1].actions()[0].trigger()
        self.assertTrue(rail.is_expanded())

    def test_rolling_the_toolbar_up_does_not_change_the_selection_tools_it_holds(self) -> None:
        toolbar = self.panel._selection_tools.toolbar
        self.selection_rail.set_expanded(False)
        self.assertIs(self.panel._selection_tools.toolbar, toolbar)
        self.assertTrue(toolbar.isHidden())
        self.selection_rail.set_expanded(True)
        self.assertFalse(toolbar.isHidden())


class RollAnimationTests(_PanelCase):
    """2026-11-22, maintainer request: a rail eases its height as the top ribbon does. The animation is only the picture: the state does not wait."""

    def setUp(self) -> None:
        super().setUp()
        self._show()

    def _halfway(self, rail) -> int:
        """Put a running roll at the middle of its way (no waiting on the clock) and say how tall the rail may be then."""
        rail._animation.setCurrentTime(rail._animation.duration() // 2)
        return rail.maximumHeight()

    def test_rolling_up_eases_the_height_down_to_the_handle_and_then_settles(self) -> None:
        rail = self.view_rail
        full = rail.height()
        self.assertGreater(full, 100)
        seen: list[bool] = []
        rail.expanded_changed.connect(seen.append)
        self.assertTrue(rail.set_expanded(False))
        self.assertEqual((rail.is_expanded(), seen), (False, [False]), "the state is the new one at once, the picture follows")
        self.assertTrue(rail.is_animating())
        self.assertTrue(12 < self._halfway(rail) < full, "half way between the column and the handle")
        self.assertFalse(rail.content.isHidden(), "the content is shown while the rail rolls up")
        _wait_for_roll(rail)
        self.assertFalse(rail.is_animating())
        self.assertTrue(rail.content.isHidden())
        self.assertEqual((rail.width(), rail.height()), (14, 12))

    def test_rolling_down_eases_the_height_up_to_the_whole_column(self) -> None:
        rail = self.view_rail
        full = rail.height()
        rail.set_expanded(False, animate=False)
        _APP.processEvents()
        self.assertEqual(rail.height(), 12)
        self.assertTrue(rail.set_expanded(True))
        self.assertTrue(rail.is_animating())
        self.assertFalse(rail.content.isHidden(), "the content shows before the rail grows")
        self.assertEqual(rail.maximumWidth(), rail._width, "the width is the column's at once, while the rail is only a strip high")
        self.assertTrue(12 < self._halfway(rail) < full)
        _wait_for_roll(rail)
        self.assertEqual(rail.height(), full)
        self.assertEqual(rail.maximumHeight(), 16777215, "free again after the roll, so a resize of the window can change it")

    def test_a_click_while_it_rolls_up_turns_it_round_from_where_it_is(self) -> None:
        rail = self.view_rail
        rail.set_expanded(False)
        height = self._halfway(rail)
        rail.grip.click()
        self.assertTrue(rail.is_expanded())
        self.assertEqual(rail._animation.startValue(), height, "the roll down begins where the roll up got to, not at the handle")
        _wait_for_roll(rail)
        self.assertFalse(rail.content.isHidden())
        self.assertEqual(rail.maximumHeight(), 16777215)

    def test_a_restore_and_a_rail_that_is_not_on_the_screen_roll_at_once(self) -> None:
        rail = self.view_rail
        self.panel.restore_ui_state(UiStateStore({IMAGE_VIEW_RAIL_OPEN.key: False}))
        self.assertFalse(rail.is_animating())
        self.assertTrue(rail.content.isHidden())
        self.assertEqual((rail.width(), rail.height()), (14, 12))
        self.panel.hide()
        _APP.processEvents()
        rail.set_expanded(True)
        self.assertFalse(rail.is_animating())
        self.assertFalse(rail.content.isHidden())
        self.assertEqual(rail.maximumHeight(), 16777215)


class PopupSideTests(_PanelCase):
    """The pop-ups of a rail open over the image, so they follow the side the rail stands on."""

    def setUp(self) -> None:
        super().setUp()
        self._show()

    def tearDown(self) -> None:
        self.panel.hide()
        super().tearDown()

    def test_the_selection_toolbars_pop_ups_open_to_its_right_on_the_left_and_to_its_left_on_the_right(self) -> None:
        toolbar = self.panel._selection_tools.toolbar
        button = toolbar._mode_button
        toolbar.show_mode_menu()
        self.assertGreaterEqual(toolbar._mode_menu.geometry().left(), button.mapToGlobal(button.rect().topRight()).x())
        toolbar._mode_menu.hide()
        self.rails.set_side("selection", RIGHT)
        _APP.processEvents()
        toolbar.show_mode_menu()
        self.assertLessEqual(toolbar._mode_menu.geometry().right(), button.mapToGlobal(button.rect().topLeft()).x())
        toolbar._mode_menu.hide()

    def test_the_layers_pop_up_follows_the_side_too(self) -> None:
        layers = self.panel._selection_tools.toolbar._layers_button
        self.rails.set_side("selection", RIGHT)
        _APP.processEvents()
        layers.show_menu()
        self.assertLessEqual(layers.menu.geometry().right(), layers.mapToGlobal(layers.rect().topLeft()).x())
        layers.menu.hide()

    def test_the_view_ribbons_menus_open_to_its_right_when_it_is_docked_on_the_left(self) -> None:
        self.rails.set_side("view", LEFT)
        _APP.processEvents()
        for opener in self.view_tab.openers:
            button, menu = opener.button, opener.menu
            menu.hide()
            opener._hidden_at = float("-inf")
            _press(button, Qt.MouseButton.RightButton)
            self.assertTrue(menu.isVisible(), f"{button.toolTip()[:30]!r}")
            self.assertGreaterEqual(menu.geometry().left(), button.mapToGlobal(button.rect().topRight()).x(), "the menu opens to the right of the button")
            menu.hide()
        self.rails.set_side("view", RIGHT)
        _APP.processEvents()
        opener = next(o for o in self.view_tab.openers if o.button is self.view_tab.histogram_toggle)
        opener._hidden_at = float("-inf")
        _press(opener.button, Qt.MouseButton.RightButton)
        self.assertLessEqual(opener.menu.geometry().right(), opener.button.mapToGlobal(opener.button.rect().topLeft()).x(), "and back to the left")
        opener.menu.hide()


class RememberedRailTests(_PanelCase):
    def test_saved_sides_and_roll_ups_are_restored_and_restoring_saves_nothing(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore(
            {
                IMAGE_SELECTION_RAIL_SIDE.key: RIGHT,
                IMAGE_VIEW_RAIL_SIDE.key: RIGHT,
                IMAGE_SELECTION_RAIL_OPEN.key: False,
                IMAGE_VIEW_RAIL_OPEN.key: True,
            },
            on_changed=saved.update,
        )
        self.panel.restore_ui_state(store)
        self.assertEqual(self.rails.sides(), {"selection": RIGHT, "view": RIGHT})
        self.assertFalse(self.selection_rail.is_expanded())
        self.assertTrue(self.view_rail.is_expanded())
        store.flush()
        self.assertEqual(saved, {}, "restoring saves nothing")

    def test_a_change_is_saved(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=saved.update)
        self.panel.restore_ui_state(store)
        self.rails.set_side("view", LEFT)
        self.view_rail.set_expanded(False)
        self.selection_rail.set_expanded(False)
        store.flush()
        self.assertEqual(saved[IMAGE_VIEW_RAIL_SIDE.key], LEFT)
        self.assertEqual(saved[IMAGE_VIEW_RAIL_OPEN.key], False)
        self.assertEqual(saved[IMAGE_SELECTION_RAIL_OPEN.key], False)

    def test_defaults_when_nothing_is_saved_and_rubbish_falls_back_to_them(self) -> None:
        for rubbish in ("sideways", 7, True, None):
            self.panel.restore_ui_state(UiStateStore({IMAGE_VIEW_RAIL_SIDE.key: rubbish, IMAGE_VIEW_RAIL_OPEN.key: rubbish}))
            self.assertEqual(self.rails.side_of("view"), RIGHT, rubbish)
            self.assertTrue(self.view_rail.is_expanded(), rubbish)
        self.panel.restore_ui_state(UiStateStore({}))
        self.assertEqual(self.rails.sides(), {"selection": LEFT, "view": RIGHT})

    def test_a_saved_view_placement_of_the_old_preferences_is_ignored(self) -> None:
        self.panel.restore_ui_state(UiStateStore({"image/view_placement": "right"}))
        self.assertEqual(self.rails.sides(), {"selection": LEFT, "view": RIGHT})
        self.assertNotIn("View", self.tool_ribbon._category_names)


class OrderTests(_PanelCase):
    """2026-11-22, maintainer request: All on top, the scale bar next to the cursor, the Background view under the landmarks in the Masks section."""

    def _groups(self) -> list[QtWidgets.QWidget]:
        layout = self.view_tab.layout()
        return [layout.itemAt(index).widget() for index in range(0, 9, 2)]  # the odd items are the dividers

    def test_all_is_the_first_group_then_image_masks_rois_and_chunks(self) -> None:
        all_group, image_group, masks_group, roi_group, chunk_group = self._groups()
        tab = self.view_tab
        self.assertTrue(all_group.isAncestorOf(tab._all_controls))
        self.assertTrue(image_group.isAncestorOf(tab.display_range_controls))
        self.assertTrue(masks_group.isAncestorOf(tab.mask_toggle))
        self.assertTrue(roi_group.isAncestorOf(tab.roi_labels_button) and roi_group.isAncestorOf(tab.group_labels_button))
        self.assertTrue(chunk_group.isAncestorOf(tab.chunk_grid_controls))
        self.assertEqual(self.view_tab.layout().count(), 10, "five groups, four dividers and the stretch")

    def test_the_scale_bar_icon_is_in_the_image_group_right_after_the_cursor_icon(self) -> None:
        tab = self.view_tab
        cursor = self.panel._cursor_overlay.icon_label
        box = tab.display_range_controls.layout()
        order = [box.itemAt(index).widget() for index in range(box.count())]
        controls = tab.display_range_controls
        self.assertEqual(order, [cursor, tab.scale_bar_toggle, controls.toggle_button])  # no "..." after AR / MR (removed 2026-11-23)
        self.assertTrue(self._groups()[1].isAncestorOf(tab.scale_bar_toggle), "in the Image section, not one of its own")

    def test_the_masks_section_holds_the_landmarks_and_under_them_the_background_view(self) -> None:
        tab = self.view_tab
        landmarks, _scope, background = tab._mirrors
        row = tab.mask_toggle.parentWidget()
        box = row.layout()
        self.assertEqual(
            [box.itemAt(index).widget() for index in range(box.count())], [tab.histogram_toggle, tab.mask_toggle, landmarks, background]
        )
        self.assertTrue(self._groups()[2].isAncestorOf(row), "all four in the Masks section: the landmarks and Background are no sections of their own")
        self.assertEqual(box.direction(), QBoxLayout.Direction.TopToBottom, "in the column the Background view is the lowest of the four")

    def test_the_background_view_still_drives_the_background_tab(self) -> None:
        tab = self.view_tab
        background = tab._mirrors[2]
        before = self.panel._background_tab.view_button().isChecked()
        background.click()
        self.assertEqual(self.panel._background_tab.view_button().isChecked(), not before)
        background.click()


class HistogramRuleTests(_PanelCase):
    def test_the_view_controls_are_in_front_of_the_person_while_the_view_ribbon_is_rolled_down(self) -> None:
        self.assertTrue(self.panel.view_controls_open())
        self.tool_ribbon.set_category("Mask")
        self.assertTrue(self.panel.view_controls_open(), "whichever tab of the top ribbon is open")
        self.rails.set_side("view", LEFT)
        self.assertTrue(self.panel.view_controls_open(), "and on either side")
        self.view_rail.set_expanded(False)
        self.assertFalse(self.panel.view_controls_open(), "rolled up they are not in front of the person")
        self.view_rail.set_expanded(True)
        self.assertTrue(self.panel.view_controls_open())

    def test_rolling_the_view_ribbon_is_announced_and_rolling_the_toolbar_is_not(self) -> None:
        seen: list[bool] = []
        self.panel.view_controls_open_changed.connect(seen.append)
        self.view_rail.set_expanded(False)
        self.view_rail.set_expanded(False)
        self.view_rail.set_expanded(True)
        self.selection_rail.set_expanded(False)
        self.assertEqual(seen, [False, True])


class RightClickTests(_PanelCase):
    def setUp(self) -> None:
        super().setUp()
        self.panel.resize(760, 560)
        self.panel.show()  # the buttons need real places on the screen: a menu opened at x = 0 is pushed right by Qt
        _APP.processEvents()

    def tearDown(self) -> None:
        self.panel.hide()
        super().tearDown()

    def _opener(self, button: QtWidgets.QWidget):
        return next(opener for opener in self.view_tab.openers if opener.button is button)

    def _popup_of(self, button: QtWidgets.QWidget) -> QtWidgets.QWidget:
        return self.view_tab.menu_for(button).actions()[0].defaultWidget()

    def _overlay_buttons(self) -> dict[str, QtWidgets.QToolButton]:
        tab = self.view_tab
        return {
            "histogram": tab.histogram_toggle,
            "mask": tab.mask_toggle,
            "sample": tab._roi_sample_controls.toggle_button,
            "reference": tab._roi_reference_controls.toggle_button,
            "roi labels": tab.roi_labels_button,
            "landmarks": tab._mirrors[0],
            "group labels": tab.group_labels_button,
            "scale bar": tab.scale_bar_toggle,
        }

    def test_every_button_with_a_menu_has_an_opener(self) -> None:
        buttons = [opener.button for opener in self.view_tab.openers]
        self.assertEqual(len(buttons), 10)  # 8 overlays, the AR / MR switch, and the chunk icon
        for name, button in self._overlay_buttons().items():
            self.assertIn(button, buttons, name)
        self.assertIn(self.view_tab.chunk_grid_controls, buttons)
        controls = self.view_tab.display_range_controls
        self.assertIn(controls.toggle_button, buttons)
        self.assertFalse(hasattr(controls, "more_button"), "the \"...\" button is gone: the numbers are in the AR / MR right-click menu")

    def test_a_right_click_opens_the_menu_beside_the_button_to_its_left(self) -> None:
        for opener in self.view_tab.openers:
            button, menu = opener.button, opener.menu
            menu.hide()
            opener._hidden_at = float("-inf")
            _press(button, Qt.MouseButton.RightButton)
            self.assertTrue(menu.isVisible(), f"{button.toolTip()[:30]!r}")
            self.assertLessEqual(menu.geometry().right(), button.mapToGlobal(button.rect().topLeft()).x(), "the menu opens to the left of the button")
            menu.hide()

    def test_the_overlay_icon_is_the_switch_and_a_right_click_does_not_switch_it(self) -> None:
        def is_on(button: QtWidgets.QToolButton) -> bool:
            return getattr(button, "show_button", button).isChecked()  # the group labels' icon keeps its state in the switch it holds

        for name, button in self._overlay_buttons().items():
            before = is_on(button)
            self._opener(button)._hidden_at = float("-inf")
            _press(button, Qt.MouseButton.RightButton)
            self.assertEqual(is_on(button), before, f"{name}: a right-click only opens its menu")
            self.view_tab.menu_for(button).hide()
            self.assertFalse(self.view_tab.menu_for(button).isVisible())
            button.click()
            self.assertEqual(is_on(button), not before, f"{name}: a left click shows or hides it")
            button.click()

    def test_a_left_click_switches_the_overlay_and_does_not_open_its_menu(self) -> None:
        for name, button in self._overlay_buttons().items():
            self._opener(button)._hidden_at = float("-inf")
            _press(button, Qt.MouseButton.LeftButton)
            self.assertFalse(self.view_tab.menu_for(button).isVisible(), name)

    def test_the_switch_reaches_the_panel_as_it_did_from_the_old_menu(self) -> None:
        before = self.panel._mask_tint.visible
        self.view_tab.mask_toggle.click()
        self.assertEqual(self.panel._mask_tint.visible, not before)
        before = self.panel._highlight_tint.visible
        self.view_tab.histogram_toggle.click()
        self.assertEqual(self.panel._highlight_tint.visible, not before)

    def test_the_masks_and_the_rois_are_one_icon_each_and_the_menus_hold_the_rest(self) -> None:
        tab = self.view_tab
        sample, reference = tab._roi_sample_controls, tab._roi_reference_controls
        for controls in (tab.highlight_controls, tab._mask_controls, sample, reference):
            toggle = controls.toggle_button
            popup = self._popup_of(toggle)
            self.assertTrue(popup.isAncestorOf(controls), "the colour and transparency are in the menu")
            self.assertTrue(popup.isAncestorOf(controls._color_button) and popup.isAncestorOf(controls._alpha_slider))
            self.assertFalse(popup.isAncestorOf(toggle), "the switch is the icon in the ribbon, not a row of the menu")
            self.assertTrue(self.ribbon.isAncestorOf(toggle))
        # Masks: the two icons side by side; ROIs: three.
        self.assertIs(tab.histogram_toggle.parentWidget(), tab.mask_toggle.parentWidget())
        self.assertIs(sample.toggle_button.parentWidget(), tab.roi_labels_button.parentWidget())
        self.assertIs(reference.toggle_button.parentWidget(), tab.roi_labels_button.parentWidget())

    def test_the_label_places_and_size_are_in_the_labels_menu(self) -> None:
        popup = self._popup_of(self.view_tab.roi_labels_button)
        self.assertTrue(popup.isAncestorOf(self.view_tab._roi_placement_copy))
        self.assertTrue(popup.isAncestorOf(self.view_tab._roi_size_copy))

    def test_the_landmarks_icon_is_the_switch_and_every_wavelength_is_in_its_menu(self) -> None:
        tab = self.view_tab
        landmarks, scope = tab._mirrors[0], tab._mirrors[1]
        self.assertTrue(self._popup_of(landmarks).isAncestorOf(scope), "the every-wavelength switch moved under the landmarks icon")
        shown = tab._chromatic_tab.show_button().isChecked()
        landmarks.click()
        self.assertEqual(tab._chromatic_tab.show_button().isChecked(), not shown, "a left click shows or hides the landmarks")
        tab._chromatic_tab.show_button().setChecked(True)  # the scope switch is greyed (as on the Chromatic tab) while they are hidden
        self.assertTrue(scope.isEnabled())
        every = tab._chromatic_tab.scope_button().isChecked()
        scope.click()  # the mirror in the menu still drives the chromatic tab's scope button
        self.assertEqual(tab._chromatic_tab.scope_button().isChecked(), not every)

    def test_the_group_labels_icon_is_the_switch_and_the_strip_has_no_switch_of_its_own(self) -> None:
        menu_button = self.view_tab.group_labels_button
        original = self.panel._roi_tab.group_label_menu
        shown = menu_button.show_button.isChecked()
        menu_button.click()
        self.assertEqual(menu_button.show_button.isChecked(), not shown)
        self.assertEqual(original.show_button.isChecked(), not shown, "the ROIs tab's own menu follows")
        self.assertTrue(menu_button.show_button.isHidden(), "the strip does not repeat the switch")
        self.assertTrue(menu_button._separators[0].isHidden())
        self.assertIsNone(menu_button.menu(), "no menu of its own to open on a left click")
        self.assertIsNotNone(original.menu(), "the ROIs tab's menu opens on a click as before")

    def test_the_scale_bar_icon_is_the_switch_and_the_units_and_the_colour_are_in_its_menu(self) -> None:
        controls = self.view_tab.scale_bar_controls
        toggle = self.view_tab.scale_bar_toggle
        popup = self._popup_of(toggle)
        self.assertTrue(popup.isAncestorOf(controls.units_button), "pixels / micrometers moved under the ruler icon")
        self.assertTrue(popup.isAncestorOf(controls._swatch), "so did the colour")
        self.assertFalse(popup.isAncestorOf(toggle), "the switch is the icon in the ribbon, not a row of the menu")
        self.assertTrue(self.ribbon.isAncestorOf(toggle) and not self.ribbon.isAncestorOf(controls.units_button))
        geometry = self.panel._geometry
        shown = bool(geometry.settings().scale_bar_visible)
        toggle.click()
        self.assertEqual(bool(geometry.settings().scale_bar_visible), not shown, "a left click shows or hides the scale bar")
        self.assertEqual(toggle.isChecked(), not shown)
        toggle.click()

    def test_the_display_range_toggle_opens_its_numbers_on_a_right_click(self) -> None:
        controls = self.view_tab.display_range_controls
        _press(controls.toggle_button, Qt.MouseButton.LeftButton)
        self.assertFalse(controls.range_menu.isVisible(), "a left press on the AR / MR switch is left to the button")
        self._opener(controls.toggle_button)._hidden_at = float("-inf")
        _press(controls.toggle_button, Qt.MouseButton.RightButton)
        self.assertTrue(controls.range_menu.isVisible(), "its right-click opens the numbers")
        controls.range_menu.hide()

    def test_a_press_just_after_the_menu_closed_is_the_click_that_closed_it(self) -> None:
        button = self.view_tab.display_range_controls.toggle_button
        menu = self.view_tab.menu_for(button)
        self._opener(button)._hidden_at = float("-inf")
        _press(button, Qt.MouseButton.RightButton)
        menu.hide()  # Qt closes the menu on the press and hands the same press to the button
        _press(button, Qt.MouseButton.RightButton)
        self.assertFalse(menu.isVisible(), "it must not open again at once")

    def test_the_chunk_icon_menu_holds_the_export_grid_switch_and_opens_while_the_icon_is_greyed_out(self) -> None:
        """2026-11-22, maintainer request: the Export tab's grid switch is in the right-click menu of the View tab's chunk icon."""
        tab = self.view_tab
        chunk = tab.chunk_grid_controls
        mirror = tab._export_grid_mirror
        popup = self._popup_of(chunk)
        self.assertTrue(popup.isAncestorOf(mirror), "the switch is a row of the chunk icon's menu")
        self.assertFalse(chunk.isEnabled(), "no OME-Zarr: nothing of the dataset's own to show")
        self.assertTrue(self._opener(chunk).also_disabled)
        self._opener(chunk)._hidden_at = float("-inf")
        _press(chunk, Qt.MouseButton.RightButton)
        self.assertTrue(tab.menu_for(chunk).isVisible(), "a TIFF stack has an export grid to show, so the menu opens on the greyed-out icon")
        tab.menu_for(chunk).hide()
        self.assertIn("Right-click", chunk.toolTip())

    def test_the_switch_in_the_menu_is_the_export_tabs_grid_switch(self) -> None:
        tab = self.view_tab
        export = self.panel._export_tab.chunk_grid_button
        mirror = tab._export_grid_mirror
        dataset = self.panel._dataset
        self.assertFalse(export.isChecked() or mirror.isChecked())
        mirror.click()  # from the menu
        self.assertTrue(export.isChecked(), "it switches the Export tab's own grid button")
        self.assertTrue(dataset.chunk_grid_preview().enabled, "so the export's grid is drawn")
        self.assertTrue(mirror.isChecked())
        export.click()  # from the Export tab
        self.assertFalse(mirror.isChecked(), "the copy follows the original")
        self.assertFalse(dataset.chunk_grid_preview().enabled)

    def test_the_menu_copy_follows_the_export_buttons_icon_after_a_theme_change(self) -> None:
        export_tab = self.panel._export_tab
        mirror = self.view_tab._export_grid_mirror
        export_tab.chunk_grid_button.setChecked(True)
        on = mirror.icon().pixmap(24, 24).toImage()
        export_tab.chunk_grid_button.setIcon(export_tab._icon("grid-4x4", "#ff0000"))  # the original's icon changes without a toggle...
        export_tab.chunk_grid_icon_refreshed.emit()  # ...and the Export tab announces it
        self.assertNotEqual(mirror.icon().pixmap(24, 24).toImage(), on)
        export_tab.chunk_grid_button.setChecked(False)

    def test_the_dataset_chunk_switch_is_independent_of_the_export_grid(self) -> None:
        """Both grids can show at once, to compare (`chunk_grid_controls.py`): the export switch in the menu does not touch the dataset's own."""
        dataset = self.panel._dataset
        self.view_tab._export_grid_mirror.click()
        self.assertFalse(dataset.source_chunk_grid_visible())
        self.assertTrue(dataset.chunk_grid_preview().enabled)
        self.view_tab._export_grid_mirror.click()

    def test_every_overlay_icon_says_what_the_right_click_gives(self) -> None:
        for name, button in self._overlay_buttons().items():
            self.assertIn("Right-click", button.toolTip(), name)


if __name__ == "__main__":
    unittest.main()
