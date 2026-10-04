"""End-to-end tests for the Image panel's "Chromatic" (Chromatic Corrections) tab.

A real `QApplication` built in-process, never `.exec()`; real widgets; real
TIFF files (synthetic dark-disk sweep with a known chromatic transform, see
`tests/_lspri_chromatic_synthetic.py`); driven by direct `.click()`/signal
calls, no screen coordinates. The run happens on the real background thread
and the result lands on the GUI thread, like in the app.

Pinned here: the run -> progress -> result -> module state chain, the
Default/Detailed switch, the Apply switch really changing what consumers see,
one undo step, a readable failure, and cancellation.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

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
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tests import _lspri_chromatic_synthetic as synth  # noqa: E402


def _pump_until(condition, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _write_dataset(root: Path, blank: bool = False) -> ImageDataset:
    records = []
    for cube in (0, 1):
        for wavelength in synth.WAVELENGTHS:
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            frame = np.full(synth.SHAPE, 40000.0, dtype=np.float32) if blank else synth.make_frame(wavelength, seed=cube)
            tifffile.imwrite(str(path), frame)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class ChromaticTabTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.chromatic = ChromaticModule()
        self.reference_frame = ReferenceFrameModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, MaskModule(), self.chromatic, BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), self.reference_frame, HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )
        self.tab = self.panel._chromatic_tab
        self.reference_frame.set_manual_frame(0, synth.REFERENCE_NM)
        self.reference_frame.set_mode("manual")

    def tearDown(self) -> None:
        self.panel._chromatic_auto.shutdown()
        self.panel._renderer.stop()
        self._tmp.cleanup()

    def _load(self, blank: bool = False) -> None:
        self.dataset.load_dataset(_write_dataset(self.root, blank=blank))
        _pump_until(lambda: self.panel._image_item.image is not None, 10.0)

    def _run_and_wait(self) -> None:
        self.tab.run_button().click()
        self.assertTrue(self.panel._chromatic_auto.is_running())
        self.assertTrue(_pump_until(lambda: not self.panel._chromatic_auto.is_running()))
        _APP.processEvents()

    # -- the tab exists and starts idle ---------------------------------------

    def test_tab_is_in_the_ribbon_and_idle_without_a_dataset(self) -> None:
        ribbon = self.panel._tool_ribbon
        self.assertIn("Chromatic", ribbon._category_names)
        button = ribbon._tab_buttons[ribbon._category_names.index("Chromatic")]
        self.assertEqual(button.toolTip(), "Chromatic Corrections")  # short label, full name in the tooltip
        self.assertFalse(self.tab.run_button().isEnabled())
        self.assertIn("Load a dataset", self.tab.status_text())

    def test_default_and_detailed_switch_set_the_values(self) -> None:
        pop = self.tab.popover()
        self.assertEqual((pop.landmark_count(), pop.stride()), (15, 3))
        self.assertEqual(pop.switch().choice(), "Default")
        pop.switch().button("Detailed").click()
        self.assertEqual((pop.landmark_count(), pop.stride()), (32, 1))
        pop._stride.setValue(2)  # a hand edit leaves neither preset selected
        self.assertIsNone(pop.switch().choice())
        pop.switch().button("Default").click()
        self.assertEqual((pop.landmark_count(), pop.stride()), (15, 3))

    # -- a real run ----------------------------------------------------------------

    def test_run_fits_the_true_correction_and_it_is_one_undo_step(self) -> None:
        self._load()
        pop = self.tab.popover()
        pop._count.setValue(12)
        pop._stride.setValue(2)
        self._run_and_wait()

        self.assertIn("landmarks", self.tab.status_text())
        self.assertIn("fit error", self.tab.status_text())
        self.assertTrue(self.chromatic.settings().chromatic_correction_enabled)
        self.assertGreaterEqual(len(self.chromatic.models()), len(synth.WAVELENGTHS))

        # The fitted correction moves a point the way the true optics did (cube 1 too: the model is static).
        point = np.array([[250.0, 140.0]])
        for cube in (0, 1):
            matrix = self.chromatic.affine_for((cube, 600.0))
            got = (point @ matrix[:, :2].T + matrix[:, 2])[0]
            expected = synth.transform(point, 600.0)[0]
            self.assertLess(float(np.hypot(*(got - expected))), 0.4)

        self.assertEqual(undo_manager.undo_label, "Automatic chromatic correction")
        undo_manager.undo()
        self.assertEqual(self.chromatic.models(), ())
        self.assertFalse(self.chromatic.settings().chromatic_correction_enabled)

    def test_the_apply_switch_changes_what_consumers_see(self) -> None:
        self._load()
        self.tab.popover()._count.setValue(12)
        self._run_and_wait()
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        self.assertFalse(np.allclose(self.chromatic.affine_for((0, 600.0)), identity))
        self.tab.apply_button().setChecked(False)
        self.assertTrue(np.allclose(self.chromatic.affine_for((0, 600.0)), identity))
        self.tab.apply_button().setChecked(True)
        self.assertFalse(np.allclose(self.chromatic.affine_for((0, 600.0)), identity))

    def test_landmark_overlay_shows_and_hides(self) -> None:
        self._load()
        self.tab.popover()._count.setValue(12)
        self._run_and_wait()
        self.panel._draw_landmarks()
        self.assertEqual(len(self.panel._landmark_fitted_item.data), 12)
        self.assertEqual(len(self.panel._landmark_observed_item.data), 12)  # 550 nm was tracked
        self.tab.show_button().setChecked(False)
        _APP.processEvents()
        self.panel._draw_landmarks()
        self.assertEqual(len(self.panel._landmark_fitted_item.data), 0)

    def test_view_group_holds_the_overlay_toggles_and_scope_switches_modes(self) -> None:
        self._load()
        self.tab.popover()._count.setValue(12)
        self._run_and_wait()
        # Current wavelength: 12 fitted dots, 12 estimated crosses (550 nm was tracked), and a line per cross to its dot.
        self.panel._draw_landmarks()
        self.assertEqual(len(self.panel._landmark_fitted_item.data), 12)
        self.assertEqual(len(self.panel._landmark_observed_item.data), 12)
        self.assertGreater(len(self.panel._landmark_line_item.getData()[0]), 0)
        # Every wavelength at once: a fitted dot per landmark per wavelength (many colours), crosses where tracked, path lines.
        self.tab.scope_button().setChecked(True)
        _APP.processEvents()
        self.panel._draw_landmarks()
        self.assertEqual(len(self.panel._landmark_fitted_item.data), 12 * len(synth.WAVELENGTHS))
        self.assertEqual(len(self.panel._landmark_observed_item.data), len(self.chromatic.landmarks()))
        colours = {brush.color().name() for brush in self.panel._landmark_fitted_item.data["brush"]}
        self.assertEqual(len(colours), len(synth.WAVELENGTHS))
        # Hiding landmarks disables the scope switch and empties the overlay.
        self.tab.show_button().setChecked(False)
        self.assertFalse(self.tab.scope_button().isEnabled())
        self.panel._draw_landmarks()
        self.assertEqual(len(self.panel._landmark_fitted_item.data), 0)

    def test_with_the_correction_on_each_landmark_converges_to_one_point(self) -> None:
        """The overlay is a check of the correction: switched on, landmarks are
        shown corrected, so every wavelength's dot lands on the same spot and the
        tracked crosses scatter only by the fit error; switched off, the raw
        chromatic shift (a streak per landmark) is shown."""
        self._load()
        self.tab.popover()._count.setValue(12)
        self._run_and_wait()
        self.tab.scope_button().setChecked(True)
        _APP.processEvents()
        count, wavelengths = 12, len(synth.WAVELENGTHS)

        def spreads() -> tuple[float, float]:
            self.panel._draw_landmarks()
            dots = self.panel._landmark_fitted_item.data
            dots = np.column_stack([dots["x"], dots["y"]]).reshape(wavelengths, count, 2)
            crosses = self.panel._landmark_observed_item.data
            crosses = np.column_stack([crosses["x"], crosses["y"]]).reshape(-1, count, 2)
            widest = lambda arr: float(np.max(np.hypot(*(arr - arr.mean(axis=0)).transpose(2, 0, 1))))  # noqa: E731
            return widest(dots), widest(crosses)

        self.assertTrue(self.chromatic.settings().chromatic_correction_enabled)
        dot_spread, cross_spread = spreads()
        self.assertLess(dot_spread, 1e-6)  # exactly one point per landmark
        self.assertLess(cross_spread, 0.5)  # the fit error, well under a pixel
        self.tab.apply_button().setChecked(False)
        _APP.processEvents()
        dot_spread, cross_spread = spreads()
        self.assertGreater(dot_spread, 1.0)  # raw: the chromatic shift is visible
        self.assertGreater(cross_spread, 1.0)
        self.tab.apply_button().setChecked(True)
        _APP.processEvents()
        self.assertLess(spreads()[0], 1e-6)

    def test_each_wavelength_has_its_own_colour_also_in_single_wavelength_mode(self) -> None:
        self._load()
        self.tab.popover()._count.setValue(12)
        self._run_and_wait()
        seen = {}
        for wavelength in (synth.WAVELENGTHS[0], synth.REFERENCE_NM, synth.WAVELENGTHS[-1]):
            self.panel._selection.set_wavelength(wavelength)
            _pump_until(lambda: False, 0.3)
            self.panel._draw_landmarks()
            seen[wavelength] = self.panel._landmark_fitted_item.opts["brush"].color().name()  # one shared brush in this mode
        self.assertEqual(len(set(seen.values())), 3)

    def test_view_toggles_are_reported_for_saving_and_restored(self) -> None:
        seen = []
        self.panel.chromatic_view_changed.connect(lambda show, every: seen.append((show, every)))
        self.tab.scope_button().setChecked(True)
        self.tab.show_button().setChecked(False)
        self.assertEqual(seen, [(True, True), (False, True)])
        panel = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), initial_chromatic_view=(False, True),
        )
        try:
            self.assertFalse(panel._chromatic_tab.show_button().isChecked())
            self.assertTrue(panel._chromatic_tab.scope_button().isChecked())
            self.assertFalse(panel._landmark_overlay_visible)
            self.assertTrue(panel._landmark_all_wavelengths)
        finally:
            panel._renderer.stop()

    def test_clear_removes_landmarks_and_correction(self) -> None:
        self._load()
        self.tab.popover()._count.setValue(12)
        self._run_and_wait()
        self.tab.clear_button().click()
        self.assertEqual(self.chromatic.models(), ())
        self.assertEqual(self.chromatic.landmarks(), ())
        self.assertFalse(self.chromatic.settings().chromatic_correction_enabled)

    def test_the_tab_is_green_exactly_while_the_correction_is_applied(self) -> None:
        self._load()
        ribbon = self.panel._tool_ribbon
        button = ribbon._tab_buttons[ribbon._category_names.index("Chromatic")]
        self.assertNotIn("34, 197, 94", button.styleSheet())  # not applied yet
        self.tab.popover()._count.setValue(12)
        self._run_and_wait()
        self.assertIn("34, 197, 94", button.styleSheet())  # accent green fill, even though another tab could be open
        self.assertIn("applied", button.toolTip())
        self.tab.apply_button().setChecked(False)
        self.assertNotIn("34, 197, 94", button.styleSheet())
        self.tab.apply_button().setChecked(True)
        self.assertIn("34, 197, 94", button.styleSheet())
        self.tab.clear_button().click()
        self.assertNotIn("34, 197, 94", button.styleSheet())

    def test_the_correction_controls_live_in_the_widget_not_the_popover(self) -> None:
        pop = self.tab.popover()
        for name in ("_apply", "_show_landmarks", "_clear"):
            self.assertFalse(hasattr(pop, name), name)
        self.assertTrue(self.tab.show_button().isVisibleTo(self.tab))
        self.assertTrue(self.tab.apply_button().isVisibleTo(self.tab))
        self.assertTrue(self.tab.clear_button().isVisibleTo(self.tab))

    # -- popover values are remembered when applied ----------------------------

    def test_values_are_emitted_for_saving_only_after_a_successful_run(self) -> None:
        saved = []
        self.tab.settings_applied.connect(saved.append)
        self._load(blank=True)
        self._run_and_wait()  # fails: no features
        self.assertEqual(saved, [])
        self.dataset.clear_dataset()
        fresh = self.root / "second"  # a new folder: the plane loader caches by path, so reusing files would reuse the blanks
        fresh.mkdir()
        self.dataset.load_dataset(_write_dataset(fresh))
        _pump_until(lambda: self.panel._image_item.image is not None, 10.0)
        pop = self.tab.popover()
        pop._count.setValue(12)
        pop._stride.setValue(2)
        pop._border.setValue(7)
        self._run_and_wait()
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0].landmark_count, saved[0].stride, saved[0].border_percent), (12, 2, 7.0))

    def test_saved_values_come_back_in_the_popover(self) -> None:
        from lspr_imaging_app.panels.image.chromatic_tab import ChromaticUiValues

        panel = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
            initial_chromatic_values=ChromaticUiValues(landmark_count=32, stride=1, border_percent=8.0, max_step_px=6.5, feature_diameter_px=30.0),
        )
        try:
            values = panel._chromatic_tab.values()
            self.assertEqual((values.landmark_count, values.stride, values.border_percent, values.max_step_px, values.feature_diameter_px),
                             (32, 1, 8.0, 6.5, 30.0))
            self.assertEqual(panel._chromatic_tab.popover().switch().choice(), "Detailed")
        finally:
            panel._renderer.stop()

    # -- failures are shown --------------------------------------------------

    def test_featureless_images_fail_with_a_readable_message(self) -> None:
        self._load(blank=True)
        self._run_and_wait()
        self.assertEqual(self.chromatic.models(), ())
        self.assertTrue(self.tab.status_text())
        self.assertNotIn("Not computed", self.tab.status_text())
        self.assertIn("feature", self.tab.status_text().lower())

    def test_dark_frame_as_reference_is_refused_with_a_message(self) -> None:
        self._load()
        self.reference_frame.set_manual_frame(0, 0.0)
        self.tab.run_button().click()
        self.assertFalse(self.panel._chromatic_auto.is_running())
        self.assertIn("dark frame", self.tab.status_text())

    def test_cancel_stops_the_run_and_changes_nothing(self) -> None:
        self._load()
        self.tab.popover()._count.setValue(12)
        self.tab.run_button().click()
        self.tab.run_button().click()  # the same button is Cancel while running
        self.assertTrue(_pump_until(lambda: not self.panel._chromatic_auto.is_running()))
        _APP.processEvents()
        self.assertEqual(self.chromatic.models(), ())
        self.assertIn("Cancelled", self.tab.status_text())


if __name__ == "__main__":
    unittest.main()
