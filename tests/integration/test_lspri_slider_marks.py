"""`SliderMarks` (panels/image/slider_marks.py), built WITHOUT an `ImagePanel`.

Cut out of `ImagePanel` (maintainability audit item D, batch 3). It marks the reference frame, the dark background and the ignored
wavelengths on the two sliders, keeps the ROI row's marks, and caches the cubes the "ROI states" mode steps through. Driven here with
recording fakes for the sliders, the strip, the ribbon and the ROI toolbox. A real `QApplication` built in-process, never `.exec()`.
The panel-level behaviour (the marks on screen, the right-click menu) stays pinned by the Image panel and ROI layout tests."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_model import SCOPE_INDIVIDUAL, SCOPE_PERSISTENT
    from lspr_imaging_app.panels.image.slider_colors import SliderColors
    from lspr_imaging_app.panels.image.slider_marks import SliderMarks
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class _Window:
    def count(self) -> int:
        return 0

    def view_for(self, n: int) -> tuple[float, float]:
        return 0.0, float(n)


class _Slider:
    def __init__(self) -> None:
        self.reference_tick: tuple | None = ("unset",)
        self.reference_highlight: str | None = "unset"
        self.dark_tick: tuple | None = None
        self.ignored_ticks: tuple | None = None
        self.value: int | None = None

    def set_reference_tick(self, index: int | None, color: str | None = None) -> None:
        self.reference_tick = (index, color)

    def set_reference_highlight(self, color: str | None) -> None:
        self.reference_highlight = color

    def set_dark_tick(self, index: int | None, color: str | None = None) -> None:
        self.dark_tick = (index, color)

    def set_ignored_ticks(self, indices: object, color: str) -> None:
        self.ignored_ticks = (frozenset(indices), color)

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt name
        self.value = value

    def axis_window(self) -> _Window:
        return _Window()


class _Strip:
    def __init__(self) -> None:
        self.marks: list | None = None
        self.cursor: object = "unset"
        self.axis_count: int | None = None

    def set_color(self, color: str) -> None:
        pass

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt name
        pass

    def set_axis_count(self, count: int) -> None:
        self.axis_count = count

    def set_marks(self, marks: object) -> None:
        self.marks = list(marks)

    def set_cursor(self, index: int | None) -> None:
        self.cursor = index


class _Toolbox:
    def __init__(self) -> None:
        self.rois_list = [SimpleNamespace(area_roi_id=1), SimpleNamespace(area_roi_id=2)]
        self.points: dict[int, SimpleNamespace] = {}
        self.asked: list = []

    def rois(self) -> list:
        return self.rois_list

    def layout_points(self, only: object = None) -> dict:
        self.asked.append(only)
        return dict(self.points)


class SliderMarksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cubes = [0, 1, 2, 3]
        self.wavelengths = [500.0, 550.0, 600.0]
        self.dataset = SimpleNamespace(spectral_cubes=lambda: self.cubes, wavelengths_for_cube=lambda cube: self.wavelengths)
        self.selected: set[int] = set()
        self.selection = SimpleNamespace(selected_roi_ids=lambda: self.selected)
        self.toolbox = _Toolbox()
        self.roles = SimpleNamespace(dark_wavelength=lambda: None, ignored_wavelengths=lambda: (), ignored_cubes=lambda: ())
        self.cube_slider, self.wavelength_slider, self.strip = _Slider(), _Slider(), _Strip()
        self.colors = SliderColors()
        self.refreshes: list[int] = []
        self.ribbon = SimpleNamespace(
            slider_colors=SimpleNamespace(refresh=lambda: self.refreshes.append(1)), set_roi_row=lambda shown, only: None
        )
        self.row = QtWidgets.QWidget()
        self.parent = QtWidgets.QWidget()
        self.state = {"cube": 1, "wavelength": 550.0, "revision": 0, "reference": None}
        self.status: list[str] = []
        self.marks = SliderMarks(
            self.dataset,
            self.selection,
            self.toolbox,
            self.roles,
            SimpleNamespace(label_for=str),
            cube_slider=self.cube_slider,
            wavelength_slider=self.wavelength_slider,
            roi_row=self.row,
            roi_strip=self.strip,
            slider_colors=self.colors,
            slider_ribbon=self.ribbon,
            menu_parent=self.parent,
            current_cube=lambda: self.state["cube"],
            current_wavelength=lambda: self.state["wavelength"],
            resolve_reference_frame=lambda: self.state["reference"],
            roi_revision=lambda: self.state["revision"],
            status=self.status.append,
            coalesce_ms=1,
        )

    def test_roi_states_are_the_first_cube_and_every_layout_change_and_cached_until_something_changes(self) -> None:
        self.toolbox.points = {2: SimpleNamespace(kind=SCOPE_INDIVIDUAL, roi_ids=(1,)), 9: SimpleNamespace(kind=SCOPE_PERSISTENT, roi_ids=(2,))}
        self.assertEqual(self.marks.roi_state_cubes(), (0, 2))  # cube 9 is not in this dataset
        asked = len(self.toolbox.asked)
        self.assertEqual(self.marks.roi_state_cubes(), (0, 2))
        self.assertEqual(len(self.toolbox.asked), asked)  # cached: asked on every Play step
        self.toolbox.points[3] = SimpleNamespace(kind=SCOPE_PERSISTENT, roi_ids=(1,))
        self.assertEqual(self.marks.roi_state_cubes(), (0, 2))  # same key, still the cached answer
        self.state["revision"] += 1  # a ROI-toolbox change
        self.assertEqual(self.marks.roi_state_cubes(), (0, 2, 3))
        self.toolbox.points.clear()
        self.marks.forget_roi_states()  # another dataset
        self.assertEqual(self.marks.roi_state_cubes(), (0,))

    def test_the_selected_only_switch_changes_which_changes_count(self) -> None:
        self.selected = {2}
        self.marks.on_roi_selected_only_toggled(True)
        self.marks.roi_state_cubes()
        self.assertEqual(self.toolbox.asked[-1], frozenset({2}))
        self.marks.on_roi_selected_only_toggled(False)
        self.state["revision"] += 1
        self.marks.roi_state_cubes()
        self.assertIsNone(self.toolbox.asked[-1])

    def test_the_roi_row_marks_the_start_layout_and_each_change_and_follows_the_viewed_cube(self) -> None:
        self.row.show()
        self.toolbox.points = {
            0: SimpleNamespace(kind=SCOPE_PERSISTENT, roi_ids=(1, 2)),  # the start layout: already marked once
            2: SimpleNamespace(kind=SCOPE_INDIVIDUAL, roi_ids=(1,)),
        }
        self.marks.update_roi_marks()
        self.assertEqual([(m.index, m.kind) for m in self.strip.marks], [(0, SCOPE_PERSISTENT), (2, SCOPE_INDIVIDUAL)])
        self.assertIn("start layout", self.strip.marks[0].tip)
        self.assertEqual(self.strip.axis_count, 4)
        self.assertEqual(self.strip.cursor, 1)  # the viewed cube
        self.cubes = []
        self.marks.update_roi_marks()
        self.assertEqual(self.strip.marks, [])

    def test_a_hidden_row_is_not_updated_and_set_roi_row_shows_it_and_marks(self) -> None:
        self.row.hide()
        self.marks.update_roi_marks()
        self.assertIsNone(self.strip.marks)
        self.marks.schedule_roi_marks()
        self.assertFalse(self.marks._roi_marks_timer.isActive())
        self.marks.set_roi_row(True, False)
        self.assertFalse(self.row.isHidden())
        self.assertIsNotNone(self.strip.marks)
        self.marks.schedule_roi_marks()
        self.assertTrue(self.marks._roi_marks_timer.isActive())

    def test_reference_dark_and_ignored_marks_follow_the_viewed_frame(self) -> None:
        self.state["reference"] = (1, 600.0)
        self.roles.dark_wavelength = lambda: 500.0
        self.roles.ignored_wavelengths = lambda: (550.0,)
        self.roles.ignored_cubes = lambda: (3,)
        self.marks.update_all()
        reference = self.colors.color("reference")
        self.assertEqual(self.cube_slider.reference_tick, (1, reference))
        self.assertEqual(self.wavelength_slider.reference_tick, (2, reference))
        self.assertEqual(self.cube_slider.reference_highlight, reference)  # the viewed cube is the reference cube
        self.assertIsNone(self.wavelength_slider.reference_highlight)  # the viewed wavelength is not
        self.assertEqual(self.wavelength_slider.dark_tick, (0, self.colors.color("dark")))
        self.assertEqual(self.wavelength_slider.ignored_ticks, (frozenset({1}), self.colors.color("ignored")))
        self.assertEqual(self.cube_slider.ignored_ticks, (frozenset({3}), self.colors.color("ignored")))
        self.state["reference"] = None
        self.marks.update_marks()
        self.assertEqual(self.cube_slider.reference_tick, (None, None))  # cleared
        self.assertIsNone(self.cube_slider.reference_highlight)

    def test_a_new_slider_colour_is_kept_shown_and_applied_to_the_marks(self) -> None:
        self.state["reference"] = (1, 550.0)
        self.marks.on_slider_color_changed("reference", "#123456")
        self.assertEqual(self.colors.color("reference"), "#123456")
        self.assertEqual(self.refreshes, [1])
        self.assertEqual(self.cube_slider.reference_tick, (1, "#123456"))


if __name__ == "__main__":
    unittest.main()
