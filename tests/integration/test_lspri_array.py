"""Array (manual array pop-out): settings panel, live ghost preview, Apply / Cancel, remembered settings.

Real `ArrayPanel`, `ArrayController`, `RoiToolbox`, `SelectionModule`, `GeometryModule`, `ChromaticModule` and a pyqtgraph plot; no dataset.
Clicks are `.click()` / `.setValue()` calls, never screen coordinates. One process per file (offscreen Qt).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np
    import pyqtgraph as pg
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QWidget

    from lspr_imaging_app.domain.roi_shapes import Border, Circle
    from lspr_imaging_app.image_tools import ChromaticModule, GeometryModule
    from lspr_imaging_app.panels.image.array import ArrayController
    from lspr_imaging_app.panels.image.array_panel import ArrayPanel
    from lspr_imaging_app.panels.image.array_ghosts import preview_alphas
    from lspr_imaging_app.panels.image.array_pictograms import KINDS, pictogram_pixmap
    from lspr_imaging_app.panels.image.array_picker import SIZE, CornerPicker, NumberingPicker
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.array_layout import ArraySpec
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])


def _settle(controller: ArrayController) -> None:
    """Let the 30 ms coalescing timer fire (what the event loop does), without waiting for it."""
    controller._timer.stop()
    controller._recompute()


class PanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry = GeometryModule()
        self.panel = ArrayPanel(self.geometry)

    def test_every_pictogram_draws(self) -> None:
        for kind in KINDS:
            with self.subTest(kind=kind):
                pixmap = pictogram_pixmap(kind)
                self.assertFalse(pixmap.isNull())
                self.assertFalse(pixmap.toImage().allGray() and pixmap.toImage().pixelColor(0, 0).alpha() == 255)  # not a solid fill
        with self.assertRaises(ValueError):
            pictogram_pixmap("nonsense")

    def test_every_setting_has_a_pictogram_and_a_name(self) -> None:
        kinds = {p.kind() for p in self.panel._pictograms}
        self.assertEqual(kinds, {"rows", "cols", "pitch_x", "pitch_y", "step_x", "step_y", "angle", "row_offset"})

    def test_spec_round_trip(self) -> None:
        spec = ArraySpec(
            kind="triangular", rows=4, cols=7, pitch_x_px=33.5, pitch_y_px=20.0, start_x=1.0, start_y=0.5, angle_deg=12.5,
            pitch_factor_x=0.5, pitch_factor_y=1.25, row_offset=0.25, regular_cell=False,
        )
        self.panel.set_spec(spec)
        self.assertEqual(self.panel.spec(), spec)

    def test_changes_are_reported_once_each(self) -> None:
        seen: list[int] = []
        self.panel.spec_changed.connect(lambda: seen.append(1))
        self.panel.rows.setValue(5)
        self.panel.picker.set_start(1.0, 1.0, emit=True)
        self.panel.pitch_x.set_value_px(10.0)  # programmatic: silent
        self.assertEqual(len(seen), 2)
        self.assertEqual(self.panel.spec().rows, 5)
        self.assertEqual((self.panel.spec().start_x, self.panel.spec().start_y), (1.0, 1.0))

    def test_switching_type_sets_the_usual_row_offset(self) -> None:
        self.panel.set_kind("triangular", emit=True)
        self.assertEqual(self.panel.row_offset.value(), 0.5)
        self.panel.set_kind("rectangular", emit=True)
        self.assertEqual(self.panel.row_offset.value(), 0.0)

    def test_regular_cell_locks_and_shows_the_values_in_force(self) -> None:
        self.panel.set_kind("triangular", emit=True)
        self.panel.pitch_x.set_value_px(40.0)
        self.panel.regular.click()
        self.assertFalse(self.panel.pitch_y.isEnabled())
        self.assertFalse(self.panel.row_offset.isEnabled())
        self.assertAlmostEqual(self.panel.pitch_y.value_px(), 40.0 * 3**0.5 / 2.0, places=9)
        self.panel.regular.click()
        self.assertTrue(self.panel.pitch_y.isEnabled())

    def test_regular_cell_rectangular_is_square(self) -> None:
        self.panel.pitch_x.set_value_px(30.0)
        self.panel.pitch_y.set_value_px(80.0)
        self.panel.step_x.setValue(1.2)
        self.panel.regular.click()
        self.assertEqual(self.panel.pitch_y.value_px(), 30.0)
        self.assertEqual(self.panel.step_y.value(), 1.2)
        self.assertFalse(self.panel.step_y.isEnabled())

    def test_fields_are_narrow(self) -> None:
        for field in (self.panel.rows, self.panel.cols, self.panel.pitch_x, self.panel.pitch_y, self.panel.step_x, self.panel.angle):
            self.assertLessEqual(field.maximumWidth(), 110)

    def test_increment_is_a_factor_that_starts_at_one_and_the_advanced_part_opens(self) -> None:
        self.assertEqual((self.panel.step_x.value(), self.panel.step_y.value()), (1.0, 1.0))
        self.assertEqual((self.panel.spec().pitch_factor_x, self.panel.spec().pitch_factor_y), (1.0, 1.0))
        self.panel.step_x.setValue(1.5)
        self.assertEqual(self.panel.spec().pitch_factor_x, 1.5)
        self.assertFalse(self.panel.is_advanced_open())
        self.panel.set_advanced_open(True)
        self.assertTrue(self.panel.is_advanced_open())

    def test_there_is_no_miniature_and_the_icons_are_not_buttons_with_frames(self) -> None:
        self.assertFalse(hasattr(self.panel, "miniature"))
        for button in (self.panel.kind_button, self.panel.regular):
            self.assertIn("border: none", button.styleSheet())
            self.assertEqual(button.text(), "")
        self.assertIn("border: none", self.panel._advanced_toggle.styleSheet())  # "Advanced" is free-standing text

    def test_apply_and_cancel_are_free_standing_icons_not_buttons(self) -> None:
        for button in (self.panel.apply_button, self.panel.cancel_button):
            self.assertEqual(button.text(), "")
            self.assertIn("border: none", button.styleSheet())
            self.assertFalse(button.icon().isNull())
        self.assertIn("Apply", self.panel.apply_button.toolTip())
        self.assertIn("Cancel", self.panel.cancel_button.toolTip())
        got: list[str] = []
        self.panel.apply_requested.connect(lambda: got.append("apply"))
        self.panel.cancel_requested.connect(lambda: got.append("cancel"))
        self.panel.apply_button.click()
        self.panel.cancel_button.click()
        self.assertEqual(got, ["apply", "cancel"])

    def test_the_type_is_one_icon_that_switches(self) -> None:
        panel = self.panel

        def icon_key(button):
            return button.icon().pixmap(28, 28).toImage()  # the picture itself: two icons are equal when every pixel is

        self.assertEqual(panel.kind(), "rectangular")
        rectangular = icon_key(panel.kind_button)
        self.assertIn("Click for Triangular", panel.kind_button.toolTip())
        panel.kind_button.click()
        self.assertEqual(panel.kind(), "triangular")
        self.assertNotEqual(icon_key(panel.kind_button), rectangular)
        self.assertIn("Click for Rectangular", panel.kind_button.toolTip())
        panel.kind_button.click()
        self.assertEqual(panel.kind(), "rectangular")
        self.assertEqual(icon_key(panel.kind_button), rectangular)

    def test_the_regular_cell_icon_shows_the_cell_you_get(self) -> None:
        panel = self.panel

        def icon_key(button):
            return button.icon().pixmap(28, 28).toImage()  # the picture itself: two icons are equal when every pixel is

        rectangle_off = icon_key(panel.regular)
        panel.regular.click()
        square_on = icon_key(panel.regular)
        self.assertNotEqual(rectangle_off, square_on)  # rectangle vs square
        panel.kind_button.click()  # triangular
        triangle_on = icon_key(panel.regular)
        self.assertNotEqual(triangle_on, square_on)  # the cell icon follows the type
        panel.regular.click()
        self.assertNotEqual(icon_key(panel.regular), triangle_on)  # equilateral vs lopsided

    def test_columns_stand_over_pitch_x_and_rows_over_pitch_y(self) -> None:
        grid = self.panel.layout().itemAt(2).layout()  # rows of the panel: type, start point, the fields grid

        def column_of(widget) -> int:
            return grid.getItemPosition(grid.indexOf(widget))[1]

        def row_of(widget) -> int:
            return grid.getItemPosition(grid.indexOf(widget))[0]

        self.assertEqual(column_of(self.panel.cols), column_of(self.panel.pitch_x))
        self.assertEqual(column_of(self.panel.rows), column_of(self.panel.pitch_y))
        self.assertLess(row_of(self.panel.cols), row_of(self.panel.pitch_x))
        self.assertLess(column_of(self.panel.cols), column_of(self.panel.rows))

    def test_the_start_point_has_a_row_of_its_own_below_the_type_icons(self) -> None:
        rows = self.panel.layout()
        self.assertIs(rows.itemAt(0).layout().itemAt(0).widget(), self.panel.kind_button)  # row 1: the type icons
        start_row = rows.itemAt(1).layout()  # row 2: only the start point (caption + nine dots)
        widgets = [start_row.itemAt(i).widget() for i in range(start_row.count()) if start_row.itemAt(i).widget() is not None]
        self.assertIn(self.panel.picker, widgets)
        self.assertNotIn(self.panel.cols, widgets)

    def test_the_pop_out_has_the_menu_background_and_the_panel_and_labels_paint_none(self) -> None:
        from PyQt6.QtGui import QColor, QImage
        from PyQt6.QtWidgets import QVBoxLayout

        from lspr_imaging_app.panels.image.array import PopupFrame
        from lspr_ui import get_active_theme

        self.assertIn("QLabel { background: transparent; }", self.panel.styleSheet())
        frame = PopupFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        frame.resize(300, 200)
        image = QImage(frame.size(), QImage.Format.Format_RGB32)
        frame.render(image)
        # a spot with no control on it (the top-right corner of the panel's own margin) is the colour every menu pop-up of the app has, not a lighter box
        self.assertEqual(image.pixelColor(image.width() - 3, 3), QColor(get_active_theme().toolbar_bg))

    def test_the_dot_pattern_is_small_but_the_dots_keep_their_size(self) -> None:
        self.assertLessEqual(SIZE, 60)
        picker = CornerPicker()
        self.assertGreaterEqual(picker._dot_centre(0.5, 0.5).x() - picker._dot_centre(0.0, 0.5).x(), 15.0)  # room for the 5 px picked dot

    def test_numbering_sits_next_to_the_start_point(self) -> None:
        start_row = self.panel.layout().itemAt(1).layout()
        widgets = [start_row.itemAt(i).widget() for i in range(start_row.count()) if start_row.itemAt(i).widget() is not None]
        self.assertIn(self.panel.picker, widgets)
        self.assertIn(self.panel.numbering, widgets)
        self.assertLess(widgets.index(self.panel.picker), widgets.index(self.panel.numbering))

    def test_numbering_spec_round_trip_and_the_defaults(self) -> None:
        spec = self.panel.spec()
        self.assertEqual((spec.number_by, spec.number_start_x, spec.number_start_y), ("rows", 0.0, 0.0))
        wanted = ArraySpec(rows=3, cols=3, row_offset=0.0, number_by="columns", number_start_x=1.0, number_start_y=1.0)
        self.panel.set_spec(wanted)
        self.assertEqual(self.panel.spec(), wanted)
        self.assertEqual(self.panel.numbering.by(), "columns")

    def test_there_is_no_rows_columns_button_a_double_click_on_the_picked_corner_switches(self) -> None:
        self.assertFalse(hasattr(self.panel, "number_by_button"))
        seen: list[int] = []
        self.panel.spec_changed.connect(lambda: seen.append(1))
        picker = self.panel.numbering
        picked = picker.start()  # top left
        QTest.mouseDClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))  # on the picked corner: rows -> columns
        self.assertEqual((self.panel.spec().number_by, picker.start()), ("columns", picked))
        self.assertIn("column by column", picker.toolTip())
        QTest.mouseDClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))  # and back
        self.assertEqual(self.panel.spec().number_by, "rows")
        QTest.mouseDClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(SIZE - 4, SIZE - 4))  # another corner: picked, no switch
        self.assertEqual((picker.by(), picker.start()), ("rows", (1.0, 1.0)))
        QTest.mouseClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(4, SIZE - 4))  # a single click only picks
        self.assertEqual((picker.by(), picker.start()), ("rows", (0.0, 1.0)))
        self.assertGreaterEqual(len(seen), 4)
        self.panel.set_spec(ArraySpec(rows=2, cols=2, row_offset=0.0))

    def test_corners_are_picked_in_the_square(self) -> None:
        seen: list[int] = []
        self.panel.spec_changed.connect(lambda: seen.append(1))
        picker = self.panel.numbering
        self.assertEqual(picker.corner_at(2.0, 2.0), (0.0, 0.0))
        self.assertEqual(picker.corner_at(SIZE - 2.0, 2.0), (1.0, 0.0))
        self.assertEqual(picker.corner_at(2.0, SIZE - 2.0), (0.0, 1.0))
        self.assertEqual(picker.corner_at(SIZE - 2.0, SIZE - 2.0), (1.0, 1.0))
        picker.set_numbering("columns", 1.0, 1.0, emit=True)
        self.assertEqual((self.panel.spec().number_start_x, self.panel.spec().number_start_y), (1.0, 1.0))
        self.assertEqual(len(seen), 1)  # one report
        with self.assertRaises(ValueError):
            picker.set_numbering("diagonal", 0.0, 0.0)

    def test_the_numbering_path_is_the_one_the_geometry_uses(self) -> None:
        picker = NumberingPicker()
        picker.set_numbering("columns", 1.0, 0.0)
        self.assertIn("column by column", picker.toolTip())
        self.assertIn("top right", picker.toolTip())

    def test_opacity_wedge_starts_in_the_middle(self) -> None:
        self.assertEqual(self.panel.opacity(), 0.5)
        seen: list[float] = []
        self.panel.opacity_changed.connect(seen.append)
        self.panel.opacity_slider.setValue(0)
        self.assertEqual((self.panel.opacity(), seen), (0.0, [0.0]))
        self.panel.opacity_slider.setValue(100)
        self.assertEqual(self.panel.opacity(), 1.0)
        self.panel.set_opacity(0.6)  # restoring: silent
        self.assertEqual((self.panel.opacity(), len(seen)), (0.6, 2))

    def test_preview_alphas_fade_left_of_the_middle_and_turn_solid_right_of_it(self) -> None:
        outline, fill = preview_alphas(0.5)
        self.assertEqual((outline, fill), (1.0, 0.25))  # the middle: the usual look
        faint = preview_alphas(0.0)
        self.assertEqual(faint[0], 0.1)  # never gone
        self.assertAlmostEqual(faint[1], 0.025)
        last = (0.0, 0.0)
        for step in range(0, 51):  # left half: both rise, together
            alphas = preview_alphas(step / 100.0)
            self.assertGreaterEqual(alphas[0], last[0])
            self.assertGreaterEqual(alphas[1], last[1])
            last = alphas
        last = preview_alphas(0.5)
        for step in range(50, 101):  # right half: the outline stays full, the fill only grows
            alphas = preview_alphas(step / 100.0)
            self.assertEqual(alphas[0], 1.0)
            self.assertGreaterEqual(alphas[1], last[1])
            last = alphas
        self.assertEqual(preview_alphas(1.0), (1.0, 1.0))  # solid
        self.assertEqual(preview_alphas(7.0), (1.0, 1.0))  # out of range is clamped
        self.assertEqual(preview_alphas(-3.0), preview_alphas(0.0))

    def test_corner_picker_is_one_widget_with_nine_places(self) -> None:
        picker = CornerPicker()
        third = SIZE / 3.0
        places = {picker.place_at(col * third + 1.0, row * third + 1.0) for row in range(3) for col in range(3)}
        self.assertEqual(len(places), 9)
        self.assertEqual(picker.place_at(1.0, 1.0), (0.0, 0.0))
        self.assertEqual(picker.place_at(SIZE - 1.0, SIZE - 1.0), (1.0, 1.0))
        self.assertEqual(picker.place_at(-30.0, 500.0), (0.0, 1.0))  # outside the square: the nearest place
        seen: list[tuple[float, float]] = []
        picker.start_changed.connect(lambda x, y: seen.append((x, y)))
        picker.set_start(0.5, 1.0, emit=True)
        self.assertEqual((picker.start(), seen), ((0.5, 1.0), [(0.5, 1.0)]))
        with self.assertRaises(ValueError):
            picker.set_start(0.3, 0.0)


class ControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.geometry = GeometryModule()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.plot = pg.PlotItem()
        self.host = QWidget()
        self.statuses: list[str] = []
        self.controller = ArrayController(
            plot=self.plot, toolbox=self.toolbox, selection=self.selection, chromatic=ChromaticModule(), geometry=self.geometry,
            current_frame=lambda: (0, 600.0), host=self.host,
        )
        self.controller.status.connect(self.statuses.append)
        self.seed = self.toolbox.add_shaped_roi(100.0, 50.0, Circle(20.0), border=Border(2.0, 3.0))
        undo_manager.clear()

    def tearDown(self) -> None:
        self.controller.close()
        self.host.close()
        undo_manager.clear()

    def _open_with_selection(self, spec: ArraySpec | None = None) -> None:
        if spec is not None:
            self.controller.panel.set_spec(spec)
        self.selection.set_roi_selection({self.seed})
        self.controller.open()
        _settle(self.controller)

    def test_nothing_selected_says_so_and_cannot_apply(self) -> None:
        self.controller.open()
        _settle(self.controller)
        self.assertIn("Select the ROI", self.controller.panel.message())
        self.assertFalse(self.controller.panel.apply_button.isEnabled())
        self.assertEqual(self.controller.ghost_count(), 0)

    def test_preview_shows_every_copy_and_follows_each_change(self) -> None:
        self._open_with_selection(ArraySpec(rows=2, cols=3, pitch_x_px=40.0, pitch_y_px=30.0))
        self.assertEqual(self.controller.ghost_count(), 5)  # 6 nodes, the seed is one of them
        self.assertTrue(self.controller.panel.apply_button.isEnabled())
        self.controller.panel.rows.setValue(4)
        _settle(self.controller)
        self.assertEqual(self.controller.ghost_count(), 11)
        self.controller.panel.picker.set_start(1.0, 1.0, emit=True)
        _settle(self.controller)
        self.assertEqual(self.controller.ghost_count(), 11)

    def test_ghosts_sit_where_the_copies_will_be(self) -> None:
        self._open_with_selection(ArraySpec(rows=1, cols=2, pitch_x_px=40.0))
        xs, ys = self.controller._ghosts.outline_xy()
        self.assertAlmostEqual((float(np.nanmin(xs)) + float(np.nanmax(xs))) / 2.0, 140.5, delta=0.05)  # 100 + 40 pitch + 0.5 half-cell shift of the view (a 48-vertex circle is not exactly symmetric)
        self.assertAlmostEqual((float(np.nanmin(ys)) + float(np.nanmax(ys))) / 2.0, 50.5, delta=0.05)
        self.assertAlmostEqual(float(np.nanmax(xs)) - float(np.nanmin(xs)), 20.0, delta=0.05)  # the seed's diameter

    def test_invalid_settings_clear_the_ghosts_and_say_why(self) -> None:
        self._open_with_selection(ArraySpec(rows=2, cols=2, pitch_x_px=40.0, pitch_y_px=40.0))
        self.assertEqual(self.controller.ghost_count(), 3)
        self.controller.panel.pitch_x.set_value_px(0.0)
        self.controller.panel.rows.setValue(3)  # any edit re-runs the check
        _settle(self.controller)
        self.assertEqual(self.controller.ghost_count(), 0)
        self.assertIn("pitch x", self.controller.panel.message())
        self.assertFalse(self.controller.panel.apply_button.isEnabled())

    def test_apply_makes_the_array_in_one_undo_step_and_closes(self) -> None:
        self._open_with_selection(ArraySpec(rows=2, cols=2, pitch_x_px=40.0, pitch_y_px=40.0))
        self.controller.panel.apply_button.click()
        self.assertEqual(len(self.toolbox.rois()), 4)
        self.assertFalse(self.controller.is_open())
        self.assertEqual(self.controller.ghost_count(), 0)
        self.assertFalse(self.controller.button.isChecked())
        self.assertIn("added 3 ROIs", self.statuses[-1])
        undo_manager.undo()
        self.assertEqual(len(self.toolbox.rois()), 1)
        self.assertFalse(undo_manager.can_undo)

    def test_cancel_makes_nothing(self) -> None:
        self._open_with_selection(ArraySpec(rows=3, cols=3))
        self.controller.panel.cancel_button.click()
        self.assertEqual(len(self.toolbox.rois()), 1)
        self.assertFalse(self.controller.is_open())
        self.assertEqual(self.controller.ghost_count(), 0)

    def test_roi_already_in_an_array_is_refused_with_a_reason(self) -> None:
        self._open_with_selection(ArraySpec(rows=1, cols=2, pitch_x_px=40.0))
        self.controller.panel.apply_button.click()
        self.selection.set_roi_selection({self.seed})
        self.controller.open()
        _settle(self.controller)
        self.assertIn("already belongs to array_1", self.controller.panel.message())
        self.assertFalse(self.controller.panel.apply_button.isEnabled())

    def test_button_opens_and_closes_the_panel(self) -> None:
        self.controller.button.click()
        self.assertTrue(self.controller.is_open())
        self.controller.button.click()
        self.assertFalse(self.controller.is_open())

    def test_the_panel_is_inside_the_app_window_not_a_window_of_its_own(self) -> None:
        popup = self.controller.popup
        self.assertIs(popup.parentWidget(), self.host)
        self.assertFalse(popup.isWindow())
        self.host.resize(600, 400)
        self.controller.open()
        self.assertTrue(popup.isVisibleTo(self.host))
        self.assertGreaterEqual(popup.x(), 4)
        self.host.resize(120, 400)  # a narrow window: the panel is pulled back inside, never pushed off the left edge
        _APP.processEvents()
        self.assertGreaterEqual(popup.x(), 4)
        self.assertTrue(self.controller.is_open())  # it stays on until Cancel / Apply / the button

    def test_opening_advanced_makes_the_panel_taller_and_closing_it_brings_it_back(self) -> None:
        """The frame always has the height its content needs (the rows of Advanced must not overlap the rest)."""
        self.host.resize(800, 600)
        self.host.show()  # the app's window is on screen: only then do child layouts get their geometry
        self.controller.open()
        _APP.processEvents()  # the event loop settles the layout (what the running app does)
        panel = self.controller.panel
        closed = self.controller.popup.height()
        self.assertEqual(closed, panel.sizeHint().height())
        self.assertGreaterEqual(panel.height(), panel.sizeHint().height())
        panel.set_advanced_open(True)
        _APP.processEvents()
        opened = self.controller.popup.height()
        self.assertGreater(opened, closed)
        self.assertEqual(opened, panel.sizeHint().height())
        self.assertGreaterEqual(panel.height(), panel.sizeHint().height())
        panel.set_advanced_open(False)
        _APP.processEvents()
        self.assertEqual(self.controller.popup.height(), closed)

    def test_the_opacity_wedge_sets_how_strongly_the_preview_is_drawn_and_is_remembered(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({}, on_changed=saved.append)
        self.controller.bind_ui_state(store)
        self._open_with_selection(ArraySpec(rows=2, cols=2, pitch_x_px=40.0, pitch_y_px=40.0))
        ghosts = self.controller._ghosts
        colour = ghosts.colors()[0]

        def alphas() -> tuple[float, float]:
            return ghosts.outline_color(colour).alphaF(), ghosts.fill_color(colour).alphaF()

        out, fill = alphas()  # the middle: the usual look
        self.assertAlmostEqual(out, 1.0, places=2)
        self.assertAlmostEqual(fill, 0.25, places=2)
        self.controller.panel.opacity_slider.setValue(25)  # left: fades
        out, fill = alphas()
        self.assertAlmostEqual(out, 0.55, places=2)
        self.assertAlmostEqual(fill, 0.1375, places=2)
        self.controller.panel.opacity_slider.setValue(75)  # right: the outline stays, the fill gets solid
        out, fill = alphas()
        self.assertAlmostEqual(out, 1.0, places=2)
        self.assertAlmostEqual(fill, 0.625, places=2)
        self.controller.panel.opacity_slider.setValue(100)
        self.assertAlmostEqual(alphas()[1], 1.0, places=2)
        self.assertIsNone(self.controller.popup.graphicsEffect())  # the panel itself stays opaque
        store.flush()
        self.assertAlmostEqual(saved[-1]["array_star/preview_opacity"], 1.0)

    def test_a_saved_opacity_and_numbering_come_back(self) -> None:
        store = UiStateStore(
            {"array_star/preview_opacity": 0.8, "array_star/number_by": "columns", "array_star/number_start_x": 1.0}, on_changed=lambda _s: None
        )
        self.controller.bind_ui_state(store)
        self.assertAlmostEqual(self.controller.opacity(), 0.8)
        spec = self.controller.panel.spec()
        self.assertEqual((spec.number_by, spec.number_start_x, spec.number_start_y), ("columns", 1.0, 0.0))
        self.selection.set_roi_selection({self.seed})  # (the restored settings stay: only the selection is added)
        self.controller.open()
        _settle(self.controller)
        ghosts = self.controller._ghosts
        self.assertAlmostEqual(ghosts.fill_color(ghosts.colors()[0]).alphaF(), 0.7, places=2)  # the saved setting is what the preview uses (0.8: fill 0.25 + 0.75 * 0.6)

    def test_apply_numbers_the_rois_as_chosen_and_undo_restores_them(self) -> None:
        spec = ArraySpec(rows=1, cols=3, pitch_x_px=10.0, number_start_x=1.0)  # by rows, from the right: the right-most ROI is number 1
        self._open_with_selection(spec)
        self.controller.apply()
        xs = {roi.area_roi_id: roi.center_x for roi in self.toolbox.rois()}
        self.assertEqual(xs, {1: 120.0, 2: 110.0, 3: 100.0})  # the selected ROI (x = 100) is now number 3
        undo_manager.undo()
        self.assertEqual({roi.area_roi_id: roi.center_x for roi in self.toolbox.rois()}, {1: 100.0})

    def test_it_stays_open_when_the_selection_changes(self) -> None:
        self._open_with_selection(ArraySpec(rows=2, cols=2, pitch_x_px=40.0, pitch_y_px=40.0))
        self.selection.set_roi_selection(set())
        _settle(self.controller)
        self.assertTrue(self.controller.is_open())
        self.assertEqual(self.controller.ghost_count(), 0)

    def test_settings_are_remembered(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"array_star/kind": "triangular", "array_star/rows": 6, "array_star/pitch_x_px": 12.5, "array_star/start_x": 1.0, "array_star/pitch_factor_x": 1.25}, on_changed=saved.append)
        self.controller.bind_ui_state(store)
        spec = self.controller.panel.spec()
        self.assertEqual((spec.kind, spec.rows, spec.pitch_x_px, spec.start_x, spec.pitch_factor_x), ("triangular", 6, 12.5, 1.0, 1.25))
        self.controller.panel.cols.setValue(9)
        store.flush()
        self.assertEqual(saved[-1]["array_star/cols"], 9)

    def test_an_unknown_saved_lattice_type_keeps_the_defaults(self) -> None:
        store = UiStateStore({"array_star/kind": "hexagonal"}, on_changed=lambda _s: None)
        self.controller.bind_ui_state(store)
        self.assertEqual(self.controller.panel.spec().kind, "rectangular")


if __name__ == "__main__":
    unittest.main()
