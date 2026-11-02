"""`FrameStatusText`, `RoiQueries` and `CursorReadout` (panels/image/), built WITHOUT an `ImagePanel`.

Cut out of `ImagePanel` (maintainability audit item D, batch 2); they take what they need from the panel as small functions, so they can be
driven alone with fakes. A real `QApplication` built in-process, never `.exec()`. The panel-level behaviour (the dock subtitle, clicking a
ROI, the readout under the mouse) stays pinned by the Image panel tests."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.cursor_readout import CursorReadout
    from lspr_imaging_app.panels.image.frame_status_text import FrameStatusText
    from lspr_imaging_app.panels.image.roi_queries import RoiQueries
    from lspr_imaging_app.selection.cube_display_module import format_elapsed_seconds
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class _FakeDataset:
    def __init__(self) -> None:
        self.comments: dict[tuple[int, float], tuple[bool, str]] = {}
        self.seconds: dict[tuple[int, float], float] = {}

    def spectral_cubes(self) -> list[int]:
        return [0, 1, 12]

    def wavelengths(self) -> list[float]:
        return [500.0, 550.0, 1000.0]

    def frame_comment(self, cube: int, wavelength: float) -> tuple[bool, str]:
        return self.comments.get((cube, wavelength), (False, ""))

    def frame_start_seconds(self, cube: int, wavelength: float) -> float | None:
        return self.seconds.get((cube, wavelength))


class _FakeCubeDisplay:
    def label_for(self, cube: int) -> str:
        return str(cube)


class FrameStatusTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = _FakeDataset()
        self.lines: list[tuple[str, str]] = []
        self.fields: list[tuple] = []
        self.status = FrameStatusText(
            self.dataset,
            _FakeCubeDisplay(),
            None,
            slider_color=lambda role: "#abcdef",
            status_changed=lambda text, tooltip: self.lines.append((text, tooltip)),
            columns_changed=self.fields.append,
        )

    def test_a_message_without_a_frame_goes_into_the_comment_field(self) -> None:
        self.status.set_status("No dataset loaded.")
        self.assertEqual(self.status.status, "No dataset loaded.")
        self.assertEqual(self.lines, [("No dataset loaded.", "")])
        self.assertEqual([c.text for c in self.status.columns()], ["", "", "", "No dataset loaded.", ""])
        self.assertEqual(self.fields[-1], self.status.columns())

    def test_a_real_frame_fills_cube_wavelength_time_and_comment_in_fixed_fields(self) -> None:
        self.dataset.comments[(1, 550.0)] = (True, "buffer in")
        self.dataset.seconds[(1, 550.0)] = 12.5
        self.status.show_frame(1, 550.0)
        time_text = format_elapsed_seconds(12.5, decimals=2)
        self.assertEqual(self.status.status, f"Cube 1, 550 nm | {time_text} | buffer in")
        cube, wavelength, time, comment, role = self.status.columns()
        self.assertEqual((cube.text, wavelength.text, time.text, comment.text, role.text), ("Cube 1", "550 nm", time_text, "buffer in", ""))
        self.assertEqual((cube.widest, wavelength.widest), ("Cube 12", "1000 nm"))  # sized by the widest of the dataset, so nothing shifts
        self.assertEqual(comment.tooltip, "buffer in")

    def test_a_frame_without_a_comment_says_why_in_the_tooltip(self) -> None:
        self.status.show_frame(0, 500.0)  # not linked to experiment data
        self.assertEqual(self.status.status, "Cube 0, 500 nm | N/A")
        self.assertIn("not linked", self.lines[-1][1])
        self.dataset.comments[(0, 500.0)] = (True, "")  # linked, but nothing recorded yet
        self.status.show_frame(0, 500.0)
        self.assertIn("No comment was recorded", self.lines[-1][1])

    def test_refresh_describes_the_viewed_frame_again_until_it_is_forgotten(self) -> None:
        self.status.refresh()
        self.assertEqual(self.lines, [])  # nothing viewed yet
        self.status.show_frame(0, 500.0)
        self.dataset.comments[(0, 500.0)] = (True, "later note")
        self.status.refresh()
        self.assertEqual(self.status.status, "Cube 0, 500 nm | later note")
        self.status.forget_frame()  # a background estimate is on screen: not a real frame
        count = len(self.lines)
        self.status.refresh()
        self.assertEqual(len(self.lines), count)

    def test_the_widest_texts_are_looked_up_again_after_a_change(self) -> None:
        self.status.set_status("x")
        self.assertEqual(self.status.columns()[0].widest, "Cube 12")
        self.dataset.spectral_cubes = lambda: [0, 1, 123]
        self.status.set_status("x")
        self.assertEqual(self.status.columns()[0].widest, "Cube 12")  # cached: the status is rebuilt on every Play step
        self.status.forget_widest()
        self.status.set_status("x")
        self.assertEqual(self.status.columns()[0].widest, "Cube 123")


class _FakeToolbox:
    def __init__(self) -> None:
        self.rois_list = [
            SimpleNamespace(area_roi_id=1, sample_diameter_px=20.0),  # centre (40, 30), radius 10
            SimpleNamespace(area_roi_id=2, sample_diameter_px=6.0),  # centre (44, 30), radius 3, inside ROI 1
        ]
        self.centers = np.array([[40.0, 30.0], [44.0, 30.0]])

    def rois_at(self, cube: int) -> list:
        return self.rois_list

    def rois(self) -> list:
        return self.rois_list

    def display_positions(self, frame: tuple[int, float], affine: object) -> np.ndarray:
        return self.centers


class RoiQueriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.affine = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        self.queries = RoiQueries(
            SimpleNamespace(),
            _FakeToolbox(),
            SimpleNamespace(affine_for=lambda frame: self.affine),
            current_cube=lambda: 0,
            current_wavelength=lambda: 550.0,
            reference_visible=lambda: True,
        )

    def test_the_nearest_centre_wins_where_apertures_overlap(self) -> None:
        self.assertEqual(self.queries.roi_at(40.0, 30.0), 1)
        self.assertEqual(self.queries.roi_at(44.0, 30.0), 2)  # the small ROI inside the large one stays reachable
        self.assertEqual(self.queries.roi_at(49.0, 30.0), 1)  # inside ROI 1 only
        self.assertIsNone(self.queries.roi_at(5.0, 5.0))

    def test_the_rubber_band_selects_by_centre(self) -> None:
        self.assertEqual(self.queries.rois_in_rect(35.0, 25.0, 42.0, 35.0), {1})
        self.assertEqual(self.queries.rois_in_rect(35.0, 25.0, 50.0, 35.0), {1, 2})
        self.assertEqual(self.queries.rois_in_rect(0.0, 0.0, 10.0, 10.0), set())

    def test_a_display_point_maps_back_to_the_reference_frame_through_the_affine(self) -> None:
        self.affine = np.array([[2.0, 0.0, 10.0], [0.0, 2.0, -4.0]])  # display = 2 * reference + shift
        self.assertEqual(self.queries.display_to_reference(30.0, 20.0), (10.0, 12.0))
        np.testing.assert_array_equal(self.queries.display_linear(), np.array([[2.0, 0.0], [0.0, 2.0]]))

    def test_a_singular_affine_leaves_the_point_unchanged(self) -> None:
        self.affine = np.zeros((2, 3))
        self.assertEqual(self.queries.display_to_reference(7.0, 8.0), (7.0, 8.0))


class CursorReadoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.viewport = QtWidgets.QWidget()
        self.item = pg.ImageItem(axisOrder="row-major")
        self.values: list[float | None] = []
        self.readout = CursorReadout(self.viewport, self.item, value_changed=self.values.append)

    def test_the_pixel_under_the_cursor_is_read_and_reported(self) -> None:
        self.item.setImage(np.arange(12, dtype=np.float32).reshape(3, 4))
        found = self.readout.value_at(2.7, 1.2)  # column 2, row 1 -> value 6
        self.assertEqual(found[:2], (2.5, 1.5))
        self.assertTrue(found[2].startswith("(2, 1) = 6"))
        self.assertEqual(self.values, [6.0])

    def test_off_the_image_or_without_an_image_there_is_nothing_to_show(self) -> None:
        self.assertIsNone(self.readout.value_at(0.5, 0.5))  # no image yet
        self.item.setImage(np.zeros((3, 4), dtype=np.float32))
        self.assertIsNone(self.readout.value_at(9.0, 0.5))
        self.assertIsNone(self.readout.value_at(-0.2, 0.5))
        self.assertEqual(self.values, [])

    def test_a_pixel_without_a_value_reports_none(self) -> None:
        image = np.zeros((2, 2), dtype=np.float32)
        image[0, 0] = np.nan
        self.item.setImage(image)
        self.readout.value_at(0.5, 0.5)
        self.assertEqual(self.values, [None])

    def test_the_label_shows_text_and_hides_for_an_empty_string(self) -> None:
        self.readout.set_text("(3, 4) = 150")
        self.assertEqual(self.readout.label.text(), "(3, 4) = 150")
        self.assertFalse(self.readout.label.isHidden())
        self.readout.set_text("")
        self.assertTrue(self.readout.label.isHidden())
        self.assertEqual(self.values, [None])  # switching the readout off tells the Histogram to drop its tick


if __name__ == "__main__":
    unittest.main()
