"""The wheel over the wavelength slider goes on into the next / previous cube at its ends (2026-10-17, maintainer request).

A real panel over a 2-cube x 3-wavelength dataset; wheel events are built by hand. Turning the wheel on past the last
wavelength shows the first wavelength of the next cube, past the first one the last wavelength of the previous cube: the
slideshow's own "all wavelengths" order, with its skip rules (ignored frames). A wheel that merely *reaches* an end stops
there. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

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
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.slideshow import MODE_ONE_WAVELENGTH, SlideshowSettings
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.selection.frame_roles_module import FrameRolesModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_WAVELENGTHS = (500.0, 550.0, 600.0)


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(5)
    for cube in (0, 1):
        for wavelength in _WAVELENGTHS:
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(32, 40)).astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class WavelengthWheelEdgeTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.selection = SelectionModule()
        self.reference = ReferenceFrameModule()
        self.roles = FrameRolesModule(self.dataset, self.reference)
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(),
            RoiToolbox(), self.selection, ActiveToolModule(), self.reference, HighlightRangeModule(),
            mask_scope=MaskScopeModule(), frame_roles=self.roles,
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump(0.4)
        self.slider = self.panel._wavelength_slider

    def tearDown(self) -> None:
        self.panel._slideshow.stop()
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    # -- helpers -----------------------------------------------------------------------------------

    def show(self, cube: int, wavelength: float) -> None:
        self.selection.set_cube(cube)
        self.selection.set_wavelength(wavelength)
        _pump()

    def wheel(self, delta: int, widget=None) -> None:
        widget = widget or self.slider
        x = float(widget.width()) / 2.0
        event = QWheelEvent(
            QPointF(x, 5.0), QPointF(x, 5.0), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(widget, event)
        _pump()

    def frame(self) -> tuple[int, float]:
        return int(self.selection.current_cube()), float(self.selection.current_wavelength())

    # -- crossing an end -----------------------------------------------------------------------------

    def test_wheel_up_on_the_last_wavelength_shows_the_first_wavelength_of_the_next_cube(self) -> None:
        self.show(0, 600.0)
        self.assertEqual(self.slider.value(), self.slider.maximum())
        self.wheel(120)
        self.assertEqual(self.frame(), (1, 500.0))
        self.assertEqual(self.slider.value(), 0)  # the slider follows

    def test_wheel_down_on_the_first_wavelength_shows_the_last_wavelength_of_the_previous_cube(self) -> None:
        self.show(1, 500.0)
        self.wheel(-120)
        self.assertEqual(self.frame(), (0, 600.0))
        self.assertEqual(self.slider.value(), self.slider.maximum())

    def test_the_first_and_the_last_frame_of_the_dataset_do_not_wrap(self) -> None:
        self.show(1, 600.0)
        self.wheel(120)
        self.assertEqual(self.frame(), (1, 600.0))
        self.show(0, 500.0)
        self.wheel(-120)
        self.assertEqual(self.frame(), (0, 500.0))

    def test_a_wheel_inside_the_cube_still_steps_the_slider(self) -> None:
        self.show(0, 500.0)
        self.wheel(120)
        self.assertEqual(self.frame(), (0, 550.0))
        self.wheel(-120)
        self.assertEqual(self.frame(), (0, 500.0))

    # -- reaching an end is not crossing it ------------------------------------------------------------

    def test_a_fast_spin_stops_on_the_last_wavelength_and_only_the_next_notch_crosses(self) -> None:
        self.show(0, 550.0)
        self.wheel(360)  # three notches in one event, one tick to go
        self.assertEqual(self.frame(), (0, 600.0))
        self.wheel(120)
        self.assertEqual(self.frame(), (1, 500.0))

    def test_a_touchpad_needs_a_whole_notch_against_the_end_and_then_crosses_once(self) -> None:
        self.show(0, 600.0)
        for _ in range(3):
            self.wheel(30)
        self.assertEqual(self.frame(), (0, 600.0))
        self.wheel(30)  # the fourth adds up to 120
        self.assertEqual(self.frame(), (1, 500.0))
        self.wheel(30)  # the new cube starts counting from nothing, and is not at its upper end anyway
        self.assertEqual(self.frame()[0], 1)

    def test_turning_back_forgets_the_partial_notch(self) -> None:
        self.show(0, 600.0)
        self.wheel(60)
        self.wheel(-120)  # a real step down, away from the end
        self.assertEqual(self.frame(), (0, 550.0))
        self.wheel(120)  # back onto the last tick: reaching it, not crossing it
        self.assertEqual(self.frame(), (0, 600.0))

    # -- the slideshow's rules ---------------------------------------------------------------------------

    def test_the_wheel_ignores_the_slideshows_one_wavelength_mode(self) -> None:
        self.panel._slideshow.set_settings(SlideshowSettings(mode=MODE_ONE_WAVELENGTH))
        self.show(0, 600.0)
        self.wheel(120)
        self.assertEqual(self.frame(), (1, 500.0))  # not (1, 600.0)

    def test_an_ignored_first_wavelength_is_skipped_like_in_play(self) -> None:
        self.show(0, 500.0)
        self.panel._slider_ribbon.frame_tools._ignore_wl.click()  # ignore 500 nm in every cube
        _pump()
        self.assertEqual(self.roles.ignored_wavelengths(), (500.0,))
        self.show(0, 600.0)
        self.wheel(120)
        self.assertEqual(self.frame(), (1, 550.0))

    # -- what is left alone ------------------------------------------------------------------------------

    def test_the_cube_slider_has_no_cube_beyond_its_ends(self) -> None:
        self.show(1, 500.0)
        self.assertEqual(self.panel._cube_slider.value(), self.panel._cube_slider.maximum())
        self.wheel(120, self.panel._cube_slider)
        self.assertEqual(self.frame(), (1, 500.0))


if __name__ == "__main__":
    unittest.main()
