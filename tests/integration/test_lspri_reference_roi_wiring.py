"""Reference ROIs in the running app (phase 1.2c of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md): the real scan on a small real
dataset, the real wiring (`app_rewrite._wire_analysis_scan`), the real Spectra panel. Driven through module methods and signals, never by screen
coordinate; the window is not shown.

Pinned: a reference ROI is scanned as one region and has no row in the display's view; linking and unlinking change the numbers a sample is
measured against **without** making any stored cell stale and without a scan; a role change does make cells stale; the raw scan's numbers are never
changed; a link made while a freshly loaded file is still being paired does not lose the file's staleness check; the Spectra panel leaves reference
ROIs out and says so.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.reference_pairing import REFERENCE, SAMPLE
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.spectra.panel import SpectraPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 64, 80
WAVELENGTHS = (500.0, 550.0, 600.0)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(3)
    base = rng.uniform(900.0, 1100.0, size=(H, W)).astype(np.float32)
    for cube in (0, 1, 2):
        for wavelength in WAVELENGTHS:
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), base + cube * 40.0 + (wavelength - 500.0) * 0.5)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _pump(until, seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return bool(until())


def _pump_for(seconds: float) -> None:
    _pump(lambda: False, seconds)


class ReferenceRoiWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.workflow = AnalysisTab()
        self.ui_state = UiStateStore({})
        app_rewrite._wire_analysis_scan(self.m, self.workflow, self.ui_state)
        self.m.scan_results._timer.setInterval(30)  # the debounce is 1.5 s in the app
        self.panel = SpectraPanel(self.m.scan_results, self.m.roi_toolbox, self.m.selection, self.m.analysis_settings, live=self.m.analysis_live)
        self.panel.restore_ui_state(self.ui_state)
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))
        self.changed = 0
        self.m.scan_results.results_changed.connect(self._count_change)
        self.staleness = 0
        self.m.scan_results.staleness_changed.connect(self._count_staleness)

    def _count_change(self) -> None:
        self.changed += 1

    def _count_staleness(self) -> None:
        self.staleness += 1

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------------------------------------------------------------

    def _open_and_scan(self, reference: tuple[int, ...] = (3,)) -> None:
        """Three ROIs (1 and 2 samples with a ring, 3 a reference unless told otherwise); the reference role is set *before* the scan."""
        self.m.dataset.load_dataset(_write_dataset(self.root))
        self.m.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=6.0)
        self.m.roi_toolbox.add_roi(55.0, 34.0, sample_diameter_px=6.0)
        self.m.roi_toolbox.add_roi(15.0, 50.0, sample_diameter_px=10.0)
        if reference:
            self.m.roi_toolbox.set_roi_role(reference, "reference")
        self.finished.clear()
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertEqual(self.finished[0][1], "completed")
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.assertTrue(_pump(lambda: self.m.scan_results.stale_cubes() is not None))  # the first check has run

    def _raw(self):
        return self.m.scan_results._stored  # the file as it is, which the display's view is made from

    def _settle_pairing(self) -> None:
        """Wait until the display's view has been made again (the pairing runs on a thread when a reference ROI exists)."""
        before = self.changed
        self.assertTrue(_pump(lambda: self.changed > before), "the display's view was never rebuilt")

    # -- the view ---------------------------------------------------------------------------------------------------------------

    def test_a_reference_roi_has_no_row_and_the_samples_keep_their_ring(self) -> None:
        self._open_and_scan()
        paired, raw = self.m.scan_results.stored(), self._raw()
        self.assertEqual(raw.roi_ids, (1, 2, 3))
        self.assertEqual(paired.roi_ids, (1, 2))
        np.testing.assert_array_equal(paired.values["mean"][:, 0, :, REFERENCE], raw.values["mean"][:, 0, :, REFERENCE])  # no link: the ring
        self.assertTrue(np.isfinite(paired.values["mean"][:, :, :, REFERENCE]).any())
        self.assertTrue(np.isfinite(raw.values["mean"][:, 2, :, SAMPLE]).all())  # the reference ROI's own region was scanned
        self.assertTrue(np.isnan(raw.values["mean"][:, 2, :, REFERENCE]).all())  # and it has no ring

    def test_linking_measures_a_sample_against_the_reference_roi_and_unlinking_brings_the_ring_back(self) -> None:
        self._open_and_scan()
        raw = self._raw()
        before = [a.copy() for a in (raw.values["mean"], raw.std, raw.n_valid, raw.cell_hash)]
        ring = raw.values["mean"][:, 0, :, REFERENCE].copy()
        self.m.roi_toolbox.link_references([1], [3])
        self._settle_pairing()
        paired = self.m.scan_results.stored()
        np.testing.assert_array_equal(paired.values["mean"][:, 0, :, REFERENCE], raw.values["mean"][:, 2, :, SAMPLE])
        np.testing.assert_array_equal(paired.n_valid[:, 0, :, REFERENCE], raw.n_valid[:, 2, :, SAMPLE])
        np.testing.assert_array_equal(paired.values["mean"][:, 1, :, REFERENCE], raw.values["mean"][:, 1, :, REFERENCE])  # ROI 2 has no link: its ring
        self.assertFalse(np.array_equal(paired.values["mean"][:, 0, :, REFERENCE], ring))  # it really is another reference now
        self.m.roi_toolbox.unlink_references([1])
        self._settle_pairing()
        np.testing.assert_array_equal(self.m.scan_results.stored().values["mean"][:, 0, :, REFERENCE], ring)
        for a, b in zip(before, (raw.values["mean"], raw.std, raw.n_valid, raw.cell_hash)):  # the raw numbers were never touched
            np.testing.assert_array_equal(a, b)

    def test_linking_makes_nothing_stale_and_starts_no_scan(self) -> None:
        self._open_and_scan()
        self.assertEqual(self.m.scan_results.stale_cubes(), frozenset())
        self.m.roi_toolbox.link_references([1, 2], [3])
        self._settle_pairing()
        _pump_for(0.4)  # longer than the check's debounce: had a link asked for a check, it would have run
        self.assertEqual(self.m.scan_results.stale_cubes(), frozenset())
        self.assertFalse(self.m.analysis_scan.is_running())

    def test_undo_of_a_link_brings_the_old_numbers_back(self) -> None:
        from lspr_imaging_app.undo import undo_manager

        self._open_and_scan()
        ring = self._raw().values["mean"][:, 0, :, REFERENCE].copy()
        self.m.roi_toolbox.link_references([1], [3])
        self._settle_pairing()
        undo_manager.undo()
        self._settle_pairing()
        np.testing.assert_array_equal(self.m.scan_results.stored().values["mean"][:, 0, :, REFERENCE], ring)

    def test_a_role_change_after_the_scan_does_make_cells_stale(self) -> None:
        self._open_and_scan()
        self.assertEqual(self.m.scan_results.stale_cubes(), frozenset())
        self.m.roi_toolbox.set_roi_role([2], "reference")  # ROI 2 is read as one region now: its stored numbers are not what a scan would give
        self.assertTrue(_pump(lambda: bool(self.m.scan_results.stale_cubes())), "the role change did not make the cells out of date")
        self.assertEqual(len(self.m.scan_results.stale_cubes()), 3)

    def test_a_scan_with_no_reference_roi_is_handed_over_as_it_is(self) -> None:
        self._open_and_scan(reference=())
        self.assertIs(self.m.scan_results.stored(), self._raw())  # nothing copied for a session that never used the feature
        self.assertEqual(self.m.scan_results.stored().roi_ids, (1, 2, 3))

    def test_a_link_made_while_a_new_file_is_being_paired_does_not_lose_its_staleness_check(self) -> None:
        self._open_and_scan()
        results = self.m.scan_results
        known_before = self.staleness
        generation = results._generation
        results._on_loaded(generation, results._stored, None)  # a file arrives: its pairing starts (a thread: a reference ROI exists)
        results.request_pairing()  # ... and a link changes at once: the first pairing is replaced
        self.assertTrue(_pump(lambda: self.staleness > known_before and results.stale_cubes() is not None), "the new file never got its staleness check")
        self.assertIsNotNone(results.stored())
        self.assertFalse(results.is_loading())

    # -- the Spectra panel ------------------------------------------------------------------------------------------------------

    def _wait_drawn(self, n_roi: int) -> None:
        self.assertTrue(
            _pump(lambda: self.panel._last is not None and len(self.panel._last[1]) == n_roi and not self.panel._busy), "the panel never drew"
        )

    def test_the_spectra_panel_leaves_a_reference_roi_out(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1, 3})
        self._wait_drawn(1)  # ROI 1 only
        self.assertEqual([s.roi_id for s in self.panel._last[1]], [1])

    def test_with_only_a_reference_roi_selected_the_spectra_panel_says_why(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({3})
        self.assertTrue(_pump(lambda: "Only reference ROIs" in self.panel._status.text()))

    def test_a_linked_samples_spectrum_is_made_against_the_reference_roi(self) -> None:
        self._open_and_scan()
        self.m.selection.set_roi_selection({1})
        self._wait_drawn(1)
        ring_values = np.array(self.panel._last[1][0].values, copy=True)
        self.m.roi_toolbox.link_references([1], [3])
        self.assertTrue(_pump(lambda: self.panel._last is not None and len(self.panel._last[1]) == 1
                              and not np.array_equal(np.asarray(self.panel._last[1][0].values), ring_values) and not self.panel._busy),
                        "the spectrum did not change when the reference did")


if __name__ == "__main__":
    unittest.main()
