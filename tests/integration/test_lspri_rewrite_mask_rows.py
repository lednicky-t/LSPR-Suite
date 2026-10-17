"""The Mask rows under the Cube and wavelength sliders in the Image panel (2026-10-16): marks, mixed glyph, alignment and zoom,
wheel / Previous / Next / Play ("Mask states"), the right-click menus (all undoable), the show / hide toggle.

A real panel over an 8-cube, 3-wavelength dataset; the mask module is the real one and its changes are made directly.
Design: apps/LSPRi/eva/docs/mask_rows_2026-10-16.md. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QContextMenuEvent, QWheelEvent
from PyQt6.QtWidgets import QLabel

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

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
    from lspr_imaging_app.panels.image import ImagePanel, mask_rows
    from lspr_imaging_app.panels.image.data_axis_slider import axis_x
    from lspr_imaging_app.panels.image.slideshow import MODE_MASK_STATES
    from lspr_imaging_app.panels.image.timeline_strip import KIND_MIXED
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import SCOPE_INDIVIDUAL, SCOPE_PERSISTENT
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_CUBES = 8
_WAVELENGTHS = (500.0, 550.0, 600.0)
_SHAPE = (64, 80)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(11)
    for cube in range(_CUBES):
        for wavelength in _WAVELENGTHS:
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=_SHAPE).astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _blob(seed: int) -> np.ndarray:
    mask = np.zeros(_SHAPE, dtype=bool)
    mask[5 + seed : 15 + seed, 5 + seed : 15 + seed] = True
    return mask


class MaskRowsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), analysis_running=lambda: False,
        )
        self.panel.resize(900, 760)
        self.panel.show()
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump()
        self.selection.set_wavelength(550.0)
        self.rows = self.panel._mask_rows
        self.ribbon = self.panel._slider_ribbon
        self.cube_strip = self.rows.cube_strip
        self.wavelength_strip = self.rows.wavelength_strip
        undo_manager.clear()

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    # -- helpers -----------------------------------------------------------------------------------

    def draw_marks(self) -> None:
        """Cube 2: persistent (drawn at 550). Cube 4: individual at 500 and 600. Cube 5: persistent (600) + individual (500)."""
        self.mask.set_mask_change((2, 550.0), "persistent", _blob(1))
        self.mask.set_mask_change((4, 500.0), "individual", _blob(2))
        self.mask.set_mask_change((4, 600.0), "individual", _blob(3))
        self.mask.set_mask_change((5, 600.0), "persistent", _blob(4))
        self.mask.set_mask_change((5, 500.0), "individual", _blob(5))
        _pump(0.3)
        undo_manager.clear()  # the drawing is not undoable, as in the app

    def cube_marks(self) -> dict[int, object]:
        return {mark.index: mark for mark in self.cube_strip.marks()}

    def wavelength_marks(self) -> dict[int, object]:
        return {mark.index: mark for mark in self.wavelength_strip.marks()}

    def go_to(self, cube: int, wavelength: float = 550.0) -> None:
        self.selection.set_cube(cube)
        self.selection.set_wavelength(wavelength)
        _pump(0.1)

    def frame(self) -> tuple[int, float]:
        return int(self.selection.current_cube()), float(self.selection.current_wavelength())

    def messages(self) -> list[str]:
        collected: list[str] = []
        self.panel.tool_status_changed.connect(collected.append)
        return collected

    def wheel(self, widget, delta: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        event = QWheelEvent(
            QPointF(40.0, 5.0), QPointF(40.0, 5.0), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(widget, event)

    def right_click(self, strip, index: int, count: int, choice: str | None):
        """A real right click on the mark at tick `index`; the menu is replaced by `choice`. Returns the (header, entries) it was given."""
        seen: dict[str, object] = {}

        def fake_menu(_parent, *, header, entries, pos=None):
            seen["header"], seen["entries"] = header, list(entries)
            return choice

        x = round(axis_x(index, count, strip.width(), strip._view()))
        with mock.patch.object(mask_rows, "show_entries_menu", side_effect=fake_menu):
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(x, 5), strip.mapToGlobal(QPoint(x, 5)))
            QtWidgets.QApplication.sendEvent(strip, event)
        return seen.get("header"), seen.get("entries", [])

    # -- the rows exist, are named, line up ---------------------------------------------------------------

    def test_titles_the_roi_row_is_called_rois_and_the_new_rows_mask(self) -> None:
        self.assertEqual([title.text() for title in self.rows.titles], ["Mask", "Mask"])
        self.assertEqual(self.panel._roi_row.findChildren(QLabel)[0].text(), "ROIs")

    def test_the_rows_start_empty_nothing_is_masked_until_a_mask_is_drawn(self) -> None:
        self.assertEqual(self.cube_marks(), {})
        self.assertEqual(self.wavelength_marks(), {})

    def test_each_row_shares_its_sliders_zoom_window_and_lines_up_with_it(self) -> None:
        self.assertIs(self.cube_strip._window, self.panel._cube_slider.axis_window())
        self.assertIs(self.wavelength_strip._window, self.panel._wavelength_slider.axis_window())
        for strip, slider in ((self.cube_strip, self.panel._cube_slider), (self.wavelength_strip, self.panel._wavelength_slider)):
            self.assertEqual(strip.width(), slider.width())
            self.assertEqual(strip.mapTo(self.panel, QPoint(0, 0)).x(), slider.mapTo(self.panel, QPoint(0, 0)).x())
        for spacer in self.rows.spacers:
            self.assertEqual(spacer.width(), self.panel._cube_spin.width())

    def test_the_rows_stack_in_the_agreed_order_cube_rois_mask_wavelength_mask(self) -> None:
        def y(widget) -> int:
            return widget.mapTo(self.panel, QPoint(0, 0)).y()

        order = [self.panel._cube_slider, self.panel._roi_row, self.rows.cube_row, self.panel._wavelength_slider, self.rows.wavelength_row]
        self.assertEqual([y(widget) for widget in order], sorted(y(widget) for widget in order))

    def test_the_marks_are_orange(self) -> None:
        self.assertEqual(self.cube_strip._color, mask_rows.MASK_COLOR)
        self.assertEqual(self.wavelength_strip._color, mask_rows.MASK_COLOR)

    # -- marks: persistent, individual, mixed ---------------------------------------------------------------

    def test_the_cube_row_marks_persistent_individual_and_mixed_cubes(self) -> None:
        self.draw_marks()
        marks = self.cube_marks()
        self.assertEqual({index: mark.kind for index, mark in marks.items()}, {2: SCOPE_PERSISTENT, 4: SCOPE_INDIVIDUAL, 5: KIND_MIXED})
        self.assertIn("persistent mask, holds from here on (drawn at 550 nm)", marks[2].tip)
        self.assertIn("2 wavelengths (500, 600 nm)", marks[4].tip)
        self.assertIn("persistent mask (drawn at 600 nm) and individual masks on 1 wavelength (500 nm)", marks[5].tip)

    def test_the_wavelength_row_marks_the_wavelengths_of_the_cube_shown_that_have_their_own_mask(self) -> None:
        self.draw_marks()
        self.go_to(4)
        self.assertEqual(list(self.wavelength_marks()), [0, 2])  # 500 and 600 nm
        self.go_to(5)
        self.assertEqual(list(self.wavelength_marks()), [0])
        self.go_to(2)  # a persistent mask belongs to the whole cube, not to a wavelength: nothing on this row
        self.assertEqual(self.wavelength_marks(), {})

    def test_the_hairlines_follow_the_cube_and_the_wavelength_shown(self) -> None:
        self.draw_marks()
        self.go_to(4, 600.0)
        self.assertEqual(self.cube_strip._cursor, 4)
        self.assertEqual(self.wavelength_strip._cursor, 2)
        self.selection.set_wavelength(500.0)
        _pump(0.1)
        self.assertEqual(self.wavelength_strip._cursor, 0)

    def test_the_rows_follow_every_change_of_the_mask_in_one_update(self) -> None:
        updates: list[int] = []
        original = self.cube_strip.set_marks
        self.cube_strip.set_marks = lambda marks: (updates.append(len(marks)), original(marks))[1]  # type: ignore[method-assign]
        for seed in range(5):  # like the steps of one stroke
            self.mask.set_mask_change((3, 550.0), "persistent", _blob(seed))
        _pump(0.4)
        self.assertEqual(updates, [1])
        self.assertEqual(list(self.cube_marks()), [3])

    def test_undo_takes_a_mark_away_and_brings_it_back(self) -> None:
        self.draw_marks()
        self.mask.delete_persistent_mask(2)
        _pump(0.3)
        self.assertNotIn(2, self.cube_marks())
        undo_manager.undo()
        _pump(0.3)
        self.assertIn(2, self.cube_marks())

    # -- clicks, wheel, Previous / Next / Play ----------------------------------------------------------------

    def test_a_click_on_a_row_moves_its_slider(self) -> None:
        self.cube_strip.index_requested.emit(6)
        self.wavelength_strip.index_requested.emit(2)
        _pump(0.2)
        self.assertEqual(self.frame(), (6, 600.0))

    def test_the_wheel_over_the_cube_row_hops_between_the_cubes_with_a_mask(self) -> None:
        self.draw_marks()
        self.go_to(0)
        visited = []
        for delta in (120, 120, 120, 120, -120):
            self.wheel(self.cube_strip, delta)
            visited.append(self.frame()[0])
        self.assertEqual(visited, [2, 4, 5, 5, 4])  # up = the next mark; at the last mark it stays
        self.assertEqual(self.frame()[1], 550.0)

    def test_the_wheel_over_the_wavelength_row_hops_between_the_wavelengths_with_a_mask(self) -> None:
        self.draw_marks()
        self.go_to(4, 550.0)
        self.wheel(self.wavelength_strip, 120)
        self.assertEqual(self.frame(), (4, 600.0))
        self.wheel(self.wavelength_strip, -120)
        self.assertEqual(self.frame(), (4, 500.0))

    def test_past_the_last_mask_of_a_cube_the_wheel_goes_on_to_the_nearest_marked_frame_of_another_cube(self) -> None:
        self.draw_marks()  # individual masks: cube 4 at 500 and 600, cube 5 at 500 (cube 2 and 5's persistent ones are not on this row)
        self.go_to(4, 600.0)
        self.wheel(self.wavelength_strip, 120)
        self.assertEqual(self.frame(), (5, 500.0))  # the first mark of the next cube that has one
        self.wheel(self.wavelength_strip, 120)
        self.assertEqual(self.frame(), (5, 500.0))  # nothing later: stays
        self.wheel(self.wavelength_strip, -120)
        self.assertEqual(self.frame(), (4, 600.0))  # back past the first mark: the last mark of the previous cube
        self.wheel(self.wavelength_strip, -120)
        self.assertEqual(self.frame(), (4, 500.0))
        self.wheel(self.wavelength_strip, -120)
        self.assertEqual(self.frame(), (4, 500.0))  # no earlier cube with an individual mask

    def test_a_standing_scan_pointer_counts_as_a_frame_to_go_to_and_a_dismissed_one_does_not(self) -> None:
        from lspr_imaging_app.image_tools.mask.transient_detection import KIND_PARTICLE, TransientEvent
        from lspr_imaging_app.image_tools.mask.transient_scan import ScanParams, ScanResult

        events = self.panel._transient_events
        pointer = TransientEvent(
            frame=(7, 550.0), kind=KIND_PARTICLE, area_px=90, centroid_xy=(10.0, 8.0), bbox_xyxy=(5, 4, 15, 12), depth=0.8, z=120.0
        )
        events.set_scan_result(ScanResult(
            events=[pointer], frames_in_range=1, frames_screened=1, frames_hit=1, failed_frames=[], seconds=0.1,
            stage_seconds={}, params=ScanParams(),
        ))
        self.draw_marks()
        self.go_to(5, 500.0)  # the last individual mask
        self.wheel(self.wavelength_strip, 120)
        self.assertEqual(self.frame(), (7, 550.0))  # the nearest later pointer, past cube 6 which has nothing
        self.wheel(self.wavelength_strip, -120)
        self.assertEqual(self.frame(), (5, 500.0))
        events.dismiss([record.id for record in events.records()])
        self.wheel(self.wavelength_strip, 120)
        self.assertEqual(self.frame(), (5, 500.0))  # a dismissed pointer is not a place to go

    def test_mask_states_is_a_stepping_choice_for_previous_next_and_play(self) -> None:
        self.draw_marks()
        panel = self.ribbon._settings_panel
        panel._mode_mask.click()
        self.assertEqual(self.ribbon.settings().mode, MODE_MASK_STATES)
        self.assertTrue(panel._mode_mask.isChecked())
        self.assertFalse(panel._mode_roi.isChecked())
        self.assertIn("mask change", self.ribbon._next.toolTip())
        self.go_to(0)
        steps = []
        for _ in range(4):
            self.ribbon._next.click()
            steps.append(self.frame()[0])
        self.assertEqual(steps, [2, 4, 5, 5])
        self.ribbon._previous.click()
        self.assertEqual(self.frame()[0], 4)
        self.go_to(0)
        self.panel._slideshow.play()
        try:
            self.assertEqual(self.panel._slideshow.upcoming_frames(3), [(2, 550.0), (4, 550.0), (5, 550.0)])
        finally:
            self.panel._slideshow.stop()

    def test_the_mask_stepping_choice_is_remembered(self) -> None:
        store = UiStateStore({"image/slideshow_mode": "mask_states"})
        self.panel.restore_ui_state(store)
        self.assertEqual(self.ribbon.settings().mode, MODE_MASK_STATES)

    # -- zoom ---------------------------------------------------------------------------------------------------

    def test_zooming_a_slider_zooms_its_mask_row_and_keeps_the_marks_under_their_ticks(self) -> None:
        self.draw_marks()
        window = self.panel._cube_slider.axis_window()
        window.zoom(2.0, 0.6)
        self.assertEqual(self.cube_strip._view(), window.view())
        lo, hi = window.view()
        for index in (2, 4, 5):
            if lo <= index <= hi:
                x = axis_x(index, _CUBES, self.cube_strip.width(), window.view())
                self.assertEqual(self.cube_strip.mark_near(x).index, index)

    # -- right click: cube row -----------------------------------------------------------------------------------

    def entry_keys(self, entries) -> list[str]:
        return [entry.key for entry in entries]

    def test_the_cube_menu_of_a_persistent_mask_offers_delete_and_make_individual(self) -> None:
        self.draw_marks()
        header, entries = self.right_click(self.cube_strip, 2, _CUBES, None)
        self.assertEqual(header, "Cube 2 - persistent mask")
        self.assertEqual(self.entry_keys(entries), [mask_rows.GO_TO, mask_rows.DELETE_PERSISTENT, mask_rows.MAKE_INDIVIDUAL])
        self.assertIn("550 nm", entries[1].text)
        self.assertTrue(all(entry.enabled for entry in entries))

    def test_the_cube_menu_of_individual_masks_offers_delete_with_the_wavelengths(self) -> None:
        self.draw_marks()
        header, entries = self.right_click(self.cube_strip, 4, _CUBES, None)
        self.assertEqual(header, "Cube 4 - individual mask")
        self.assertEqual(self.entry_keys(entries), [mask_rows.GO_TO, mask_rows.DELETE_INDIVIDUAL])
        self.assertIn("2 wavelengths: 500, 600 nm", entries[1].text)

    def test_the_cube_menu_of_a_mixed_cube_offers_both_sets(self) -> None:
        self.draw_marks()
        header, entries = self.right_click(self.cube_strip, 5, _CUBES, None)
        self.assertEqual(header, "Cube 5 - persistent and individual mask")
        self.assertEqual(
            self.entry_keys(entries),
            [mask_rows.GO_TO, mask_rows.DELETE_PERSISTENT, mask_rows.MAKE_INDIVIDUAL, mask_rows.DELETE_INDIVIDUAL],
        )

    def test_a_right_click_off_any_mark_opens_no_menu(self) -> None:
        self.draw_marks()
        header, entries = self.right_click(self.cube_strip, 3, _CUBES, None)
        self.assertIsNone(header)

    def test_make_individual_is_greyed_when_that_wavelength_already_has_an_individual_mask(self) -> None:
        self.mask.set_mask_change((3, 550.0), "persistent", _blob(1))
        self.mask.set_mask_change((3, 550.0), "individual", _blob(2))
        _pump(0.3)
        _header, entries = self.right_click(self.cube_strip, 3, _CUBES, None)
        make_individual = next(entry for entry in entries if entry.key == mask_rows.MAKE_INDIVIDUAL)
        self.assertFalse(make_individual.enabled)
        self.assertIn("already has an individual mask", make_individual.tooltip)

    def test_delete_persistent_says_so_and_undo_brings_the_mask_back(self) -> None:
        self.draw_marks()
        messages = self.messages()
        self.right_click(self.cube_strip, 2, _CUBES, mask_rows.DELETE_PERSISTENT)
        _pump(0.3)
        self.assertNotIn(2, self.cube_marks())
        self.assertTrue(messages and "persistent mask was deleted" in messages[-1] and "Ctrl+Z" in messages[-1], messages)
        undo_manager.undo()
        _pump(0.3)
        self.assertEqual(self.cube_marks()[2].kind, SCOPE_PERSISTENT)

    def test_make_individual_moves_the_mask_to_the_wavelength_it_was_drawn_at(self) -> None:
        self.draw_marks()
        self.right_click(self.cube_strip, 2, _CUBES, mask_rows.MAKE_INDIVIDUAL)
        _pump(0.3)
        self.assertEqual(self.cube_marks()[2].kind, SCOPE_INDIVIDUAL)
        self.assertEqual(self.mask.individual_wavelengths(2), (550.0,))
        undo_manager.undo()
        _pump(0.3)
        self.assertEqual(self.cube_marks()[2].kind, SCOPE_PERSISTENT)

    def test_delete_individual_removes_every_individual_mask_of_the_cube(self) -> None:
        self.draw_marks()
        self.right_click(self.cube_strip, 5, _CUBES, mask_rows.DELETE_INDIVIDUAL)
        _pump(0.3)
        self.assertEqual(self.cube_marks()[5].kind, SCOPE_PERSISTENT)  # the persistent mask of the mixed cube stays
        self.assertEqual(self.mask.individual_wavelengths(5), ())
        undo_manager.undo()
        _pump(0.3)
        self.assertEqual(self.cube_marks()[5].kind, KIND_MIXED)

    def test_go_to_this_cube(self) -> None:
        self.draw_marks()
        self.right_click(self.cube_strip, 4, _CUBES, mask_rows.GO_TO)
        _pump(0.2)
        self.assertEqual(self.frame()[0], 4)

    # -- right click: wavelength row ---------------------------------------------------------------------------------

    def test_the_wavelength_menu_offers_go_to_make_persistent_and_delete(self) -> None:
        self.draw_marks()
        self.go_to(4)
        header, entries = self.right_click(self.wavelength_strip, 0, len(_WAVELENGTHS), None)
        self.assertEqual(header, "500 nm, Cube 4 - individual mask")
        self.assertEqual(self.entry_keys(entries), [mask_rows.GO_TO, mask_rows.MAKE_PERSISTENT, mask_rows.DELETE_ONE])
        self.assertTrue(all(entry.enabled for entry in entries))

    def test_make_persistent_is_greyed_for_a_cube_that_has_its_own_persistent_mask(self) -> None:
        self.draw_marks()
        self.go_to(5)  # cube 5 has a persistent mask of its own and an individual one at 500 nm
        _header, entries = self.right_click(self.wavelength_strip, 0, len(_WAVELENGTHS), None)
        make_persistent = next(entry for entry in entries if entry.key == mask_rows.MAKE_PERSISTENT)
        self.assertFalse(make_persistent.enabled)
        self.assertIn("already has its own persistent mask", make_persistent.tooltip)

    def test_make_persistent_turns_the_individual_mask_into_the_cubes_persistent_one(self) -> None:
        self.draw_marks()
        self.go_to(4)
        messages = self.messages()
        self.right_click(self.wavelength_strip, 0, len(_WAVELENGTHS), mask_rows.MAKE_PERSISTENT)
        _pump(0.3)
        self.assertEqual(self.cube_marks()[4].kind, KIND_MIXED)  # persistent from 500 nm, plus the individual one at 600 nm
        self.assertEqual(self.mask.persistent_points()[4], 500.0)
        self.assertTrue(messages and "persistent mask" in messages[-1], messages)
        undo_manager.undo()
        _pump(0.3)
        self.assertEqual(self.cube_marks()[4].kind, SCOPE_INDIVIDUAL)

    def test_delete_one_individual_mask_and_undo(self) -> None:
        self.draw_marks()
        self.go_to(4)
        self.right_click(self.wavelength_strip, 2, len(_WAVELENGTHS), mask_rows.DELETE_ONE)  # the 600 nm mask
        _pump(0.3)
        self.assertEqual(self.mask.individual_wavelengths(4), (500.0,))
        self.assertEqual(list(self.wavelength_marks()), [0])
        undo_manager.undo()
        _pump(0.3)
        self.assertEqual(list(self.wavelength_marks()), [0, 2])

    def test_go_to_a_wavelength_from_the_wavelength_menu(self) -> None:
        self.draw_marks()
        self.go_to(4, 550.0)
        self.right_click(self.wavelength_strip, 2, len(_WAVELENGTHS), mask_rows.GO_TO)
        _pump(0.2)
        self.assertEqual(self.frame(), (4, 600.0))

    def test_an_undo_after_a_newer_stroke_says_why_it_did_nothing(self) -> None:
        self.draw_marks()
        messages = self.messages()
        self.mask.delete_persistent_mask(2)
        self.mask.set_mask_change((2, 550.0), "persistent", _blob(7))  # redrawn, not undoable
        undo_manager.undo()
        self.assertTrue(messages and "not undone" in messages[-1], messages)
        self.assertEqual(self.mask.persistent_points()[2], 550.0)  # the newer mask survived

    # -- show / hide, remembered ------------------------------------------------------------------------------------

    def test_the_mask_toggle_shows_and_hides_both_rows(self) -> None:
        self.assertFalse(self.rows.cube_row.isHidden())
        self.ribbon.mask_rows_button.click()
        self.assertTrue(self.rows.cube_row.isHidden() and self.rows.wavelength_row.isHidden())
        self.ribbon.mask_rows_button.click()
        self.assertFalse(self.rows.cube_row.isHidden() or self.rows.wavelength_row.isHidden())

    def test_the_toggle_is_remembered_without_saving_what_was_just_read(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"image/mask_rows_visible": False}, on_changed=saved.append)
        self.panel.restore_ui_state(store)
        self.assertTrue(self.rows.cube_row.isHidden() and self.rows.wavelength_row.isHidden())
        self.assertFalse(self.ribbon.mask_rows_button.isChecked())
        store.flush()
        self.assertEqual(saved, [], "restoring must not save what it just read")
        self.ribbon.mask_rows_button.click()
        store.flush()
        self.assertTrue(saved[-1]["image/mask_rows_visible"])

    def test_hidden_rows_do_no_work_and_catch_up_when_shown(self) -> None:
        self.ribbon.mask_rows_button.click()  # hide
        self.mask.set_mask_change((3, 550.0), "persistent", _blob(1))
        _pump(0.3)
        self.assertEqual(self.cube_marks(), {})
        self.ribbon.mask_rows_button.click()  # show
        self.assertEqual(list(self.cube_marks()), [3])

    def test_the_mask_section_of_the_ribbon_has_one_toggle_and_the_old_slider_colors_section_is_gone(self) -> None:
        captions = [label.text() for label in self.ribbon.findChildren(QLabel)]
        self.assertIn("Mask", captions)
        self.assertNotIn("Slider colors", captions)


if __name__ == "__main__":
    unittest.main()
