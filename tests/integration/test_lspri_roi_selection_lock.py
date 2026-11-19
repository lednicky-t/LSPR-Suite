"""The ROI selection lock (2026-11-16, maintainer: "lock selection of ROIs, so clicking and so on would not influence it").

Pins, on the real objects: what the lock blocks (every change of the ROI selection that goes through `SelectionModule.set_roi_selection`: a
click or rubber band on the image, a click in the ROI table, the menus' "Select ..." entries) and what passes it (a delete / renumber, a
session load); that a refused change says so (never silent); the lock button on the ROIs tab and the "Lock selection" entry of both ROI
context menus are one switch with the module's flag; and the button's state is remembered.

Offscreen Qt; menus are never really opened (`QMenu.exec` is patched to look at the menu, and to pick an entry).
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QPointF, Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels import roi_selection_lock
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.roi_context_menu import TOGGLE_LOCK, RoiMenuChoice
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import AreaSelectionModule, HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

SHAPE = (120, 160)
_SELECT_ROWS = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows


def _pump_until(condition, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


class _Click:
    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        self._pos, self._button = scene_pos, button

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


def _write_dataset(root: Path) -> ImageDataset:
    image = np.random.default_rng(4).normal(1000.0, 15.0, SHAPE)
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), image.astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


def _menu_probe(pick: str | None = None):
    """Patch `QMenu.exec` so a menu is not opened: it records (text, enabled, checked) of every entry, and `pick` (an entry's text) is
    triggered, as a click on it would, and returned as the chosen action (the Image panel's menu reads the return value, the table's
    entries are connected to their handlers). Returns (patcher, list of the recorded menus)."""
    seen: list[list[tuple[str, bool, bool]]] = []

    def fake_exec(menu, *_args, **_kwargs):
        seen.append([(a.text(), a.isEnabled(), a.isChecked()) for a in menu.actions() if not a.isSeparator()])
        for action in menu.actions():
            if pick is not None and action.text() == pick:
                action.trigger()
                return action
        return None

    return mock.patch.object(QtWidgets.QMenu, "exec", fake_exec), seen


class ModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = SelectionModule()
        self.changed: list[set] = []
        self.refused: list[bool] = []
        self.lock_events: list[bool] = []
        self.selection.roi_selection_changed.connect(self.changed.append)
        self.selection.roi_selection_refused.connect(lambda: self.refused.append(True))
        self.selection.roi_selection_lock_changed.connect(self.lock_events.append)

    def test_a_locked_selection_does_not_follow_a_new_one_and_says_so(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.selection.set_roi_selection_locked(True)
        self.selection.set_roi_selection({3})
        self.selection.set_roi_selection(set())
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 2}))
        self.assertEqual(len(self.changed), 1, "only the first, free change was announced")
        self.assertEqual(len(self.refused), 2, "each refused change is reported")

    def test_asking_for_the_selection_it_already_has_is_not_a_refusal(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.selection.set_roi_selection_locked(True)
        self.selection.set_roi_selection({2, 1})
        self.assertEqual(self.refused, [])

    def test_the_lock_is_one_switch_announced_once_per_change(self) -> None:
        self.assertFalse(self.selection.roi_selection_locked())
        self.selection.set_roi_selection_locked(True)
        self.selection.set_roi_selection_locked(True)
        self.selection.set_roi_selection_locked(False)
        self.assertEqual(self.lock_events, [True, False])
        self.selection.set_roi_selection({5})
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({5}), "unlocked: free again")

    def test_a_delete_or_renumber_still_reaches_a_locked_selection(self) -> None:
        self.selection.set_roi_selection({2, 4})
        self.selection.set_roi_selection_locked(True)
        self.selection.remap_roi_ids({1: 1, 2: 2, 4: 3})  # ROI 3 was deleted, 4 became 3
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({2, 3}))
        self.selection.remap_roi_ids({2: 1})  # ROI 3 deleted too
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1}), "a deleted ROI leaves the locked selection")
        self.assertTrue(self.selection.roi_selection_locked())

    def test_a_session_load_passes_the_lock(self) -> None:
        self.selection.set_roi_selection({1})
        self.selection.set_roi_selection_locked(True)
        self.selection.restore_roi_selection({7, 8})
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({7, 8}))
        self.assertTrue(self.selection.roi_selection_locked(), "the lock stays on, with the loaded selection")
        self.assertEqual(self.refused, [])


class TableTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.toolbox.roi_ids_renumbered.connect(self.selection.remap_roi_ids)  # as the app wires it
        for x in (10.0, 20.0, 30.0, 40.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.panel = RoiTablePanel(self.toolbox, self.selection, GeometryModule())
        self.panel.refresh_now()
        self.tree = self.panel._tree
        self.model = self.panel._model
        self.refused: list[bool] = []
        self.selection.roi_selection_refused.connect(lambda: self.refused.append(True))

    def tearDown(self) -> None:
        undo_manager.clear()
        self.panel.deleteLater()

    def _select_in_view(self, *roi_ids: int) -> None:
        selection = QItemSelection()
        for roi_id in roi_ids:
            index = self.model.roi_index(roi_id, 0)
            selection.select(index, index)
        self.tree.selectionModel().select(selection, _SELECT_ROWS)

    def _selected_in_view(self) -> list[int]:
        rois, _headers = self.panel._view_selection()
        return sorted(rois)

    def test_a_click_in_the_table_leaves_a_locked_selection_as_it_is(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.selection.set_roi_selection_locked(True)
        self._select_in_view(4)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 2}))
        self.assertEqual(self._selected_in_view(), [1, 2], "the rows go back to the locked ones")
        self.assertTrue(self.refused, "and the refusal was reported")

    def test_unlocked_the_table_selects_as_before(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.selection.set_roi_selection_locked(True)
        self.selection.set_roi_selection_locked(False)
        self._select_in_view(4)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({4}))

    def test_the_context_menu_offers_lock_selection_as_a_check_that_flips_the_lock(self) -> None:
        index = self.model.roi_index(2, 0)
        pos = self.tree.visualRect(index).center()
        patcher, seen = _menu_probe(pick=roi_selection_lock.MENU_TEXT)
        with patcher:
            self.panel._show_context_menu(pos)
        entry = next(e for e in seen[0] if e[0] == roi_selection_lock.MENU_TEXT)
        self.assertFalse(entry[2], "not locked yet: no check")
        self.assertTrue(self.selection.roi_selection_locked(), "picking it locked the selection")
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({2}), "the right-click selected the ROI under it first, then it was locked")
        patcher, seen = _menu_probe(pick=roi_selection_lock.MENU_TEXT)
        with patcher:
            self.panel._show_context_menu(pos)
        entry = next(e for e in seen[0] if e[0] == roi_selection_lock.MENU_TEXT)
        self.assertTrue(entry[2], "locked: the entry is checked")
        self.assertFalse(self.selection.roi_selection_locked(), "picking it again unlocked")

    def test_a_right_click_on_another_roi_does_not_move_a_locked_selection(self) -> None:
        self.selection.set_roi_selection({1})
        self.selection.set_roi_selection_locked(True)
        pos = self.tree.visualRect(self.model.roi_index(3, 0)).center()
        patcher, _seen = _menu_probe()
        with patcher:
            self.panel._show_context_menu(pos)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1}))


class ImagePanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.selection = SelectionModule()
        self.roi_toolbox = RoiToolbox()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.roi_toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=AreaSelectionModule(),
        )
        self.panel.resize(1100, 800)
        self.panel.show()
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.panel._image_item.image is not None and self.panel._image_item.levels is not None)
        _pump()
        self.tab = self.panel._roi_tab
        self.first = self.roi_toolbox.add_roi(40.0, 40.0)
        self.second = self.roi_toolbox.add_roi(100.0, 80.0)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        undo_manager.clear()
        self._tmp.cleanup()

    def _scene(self, x: float, y: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(x, y))

    def _click_on_image(self, x: float, y: float) -> None:
        self.panel._on_scene_clicked(_Click(self._scene(x, y)))

    # -- the button ---------------------------------------------------------------------------------------------

    def test_the_lock_is_a_captioned_section_after_groups_on_the_rois_tab(self) -> None:
        group = self.tab.lock_button.parentWidget().parentWidget()  # the lock sits in the Selection box with Link / Unlink / Links (phase 3), which is in the group
        layout = self.tab.layout()
        self.assertGreater(layout.indexOf(group), layout.indexOf(self.tab._group_controls.parentWidget()))
        self.assertEqual(self.tab._selection_caption.text(), "Selection")
        self.assertTrue(self.tab.lock_button.isCheckable())
        self.assertFalse(self.tab.lock_button.isChecked())
        self.assertIn("Lock the ROI selection", self.tab.lock_button.toolTip())

    def test_the_button_and_the_modules_flag_follow_each_other_and_the_icon_changes(self) -> None:
        button = self.tab.lock_button
        open_face = button.icon().cacheKey()
        button.click()
        self.assertTrue(self.selection.roi_selection_locked())
        self.assertNotEqual(button.icon().cacheKey(), open_face, "a closed padlock")
        self.selection.set_roi_selection_locked(False)  # what a context menu does
        self.assertFalse(button.isChecked())

    def test_the_lock_belongs_to_the_session_not_to_the_remembered_ui_state(self) -> None:
        """Maintainer, 2026-11-16: the lock is saved in the session (`test_lspri_rewrite_session.py`), so it is not also a remembered setting that
        could disagree with the session that is loaded."""
        store = UiStateStore({"image/roi_selection_locked": True})  # a value no version wrote: ignored
        self.tab.bind_ui_state(store)
        self.assertFalse(self.tab.lock_button.isChecked())
        self.assertFalse(self.selection.roi_selection_locked())
        self.tab.lock_button.click()
        self.assertTrue(self.selection.roi_selection_locked())
        self.assertIs(store.get("image/roi_selection_locked"), True, "the store still holds what it was given, untouched")

    def test_a_session_load_sets_the_button_through_the_module(self) -> None:
        self.selection.set_roi_selection_locked(True)  # what `apply_session` does
        self.assertTrue(self.tab.lock_button.isChecked())
        self.selection.set_roi_selection_locked(False)
        self.assertFalse(self.tab.lock_button.isChecked())

    # -- clicks on the image ---------------------------------------------------------------------------------------

    def test_a_click_selects_a_roi_unlocked_and_not_while_locked(self) -> None:
        self._click_on_image(40.0, 40.0)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({self.first}), "unlocked: a click selects the ROI under it")
        self.tab.lock_button.click()
        self._click_on_image(100.0, 80.0)
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({self.first}), "locked: the other ROI is not selected")
        self._click_on_image(5.0, 110.0)  # empty image would clear the selection
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({self.first}))
        self.assertIn(roi_selection_lock.REFUSED_TEXT, self.messages, "and the status line says why nothing happened")

    def test_the_roi_menu_has_lock_selection_and_leaves_out_what_would_change_the_selection(self) -> None:
        self.selection.set_roi_selection({self.first})
        scene = self._scene(40.0, 40.0)
        patcher, seen = _menu_probe(pick=roi_selection_lock.MENU_TEXT)
        with patcher:
            self.panel._interaction._show_roi_menu(scene)
        entries = {text: (enabled, checked) for text, enabled, checked in seen[0]}
        self.assertEqual(entries[roi_selection_lock.MENU_TEXT], (True, False))
        self.assertIn("Deselect 1 ROI", entries, "free: Deselect is offered")
        self.assertTrue(self.selection.roi_selection_locked(), "the entry locked the selection")

        patcher, seen = _menu_probe()
        with patcher:
            self.panel._interaction._show_roi_menu(self._scene(100.0, 80.0))  # a right-click on the other ROI
        entries = {text: (enabled, checked) for text, enabled, checked in seen[0]}
        self.assertEqual(entries[roi_selection_lock.MENU_TEXT], (True, True), "locked: the entry is checked")
        self.assertNotIn("Deselect 1 ROI", entries, "locked: Deselect would be refused, so it is left out (2026-11-19: not greyed)")
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({self.first}), "the right-click did not select the other ROI")

    def test_the_menu_choice_toggles_the_lock(self) -> None:
        with mock.patch("lspr_imaging_app.panels.image.interaction.show_roi_context_menu", return_value=RoiMenuChoice(TOGGLE_LOCK)):
            self.panel._interaction._show_roi_menu(self._scene(40.0, 40.0))
            self.assertTrue(self.selection.roi_selection_locked())
            self.panel._interaction._show_roi_menu(self._scene(40.0, 40.0))
            self.assertFalse(self.selection.roi_selection_locked())


if __name__ == "__main__":
    unittest.main()
