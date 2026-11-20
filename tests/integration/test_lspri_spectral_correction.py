"""The spectral correction in the app (2026-11-20): the module that owns the table and the mode, its place in the session, the hand-over to the scan the display sees, the
File -> Import entry's helpers and the Spectra ribbon's pick-up and "i" text. The maths and the fits are pinned without Qt in `tests/unit/test_lspri_spectral_passband.py`.

The real module set (`app_rewrite._build_modules`), a real dataset folder and session, a synthetic sweep written to a file. Driven through methods and signals, never by screen
coordinate; the window is not shown and no dialog is opened (a dialog is modal: its pieces are tested as functions).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from dataclasses import replace
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
    from lspr_imaging_app.analysis import passband_store as ps
    from lspr_imaging_app.analysis import spectral_passband as sp
    from lspr_imaging_app.analysis.reference_pairing import PairedScan
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.file_menu import _sweep_file_kind
    from lspr_imaging_app.panels.spectra.correction_group import fit_effect, info_html
    from lspr_imaging_app.panels.spectra.panel import SpectraPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.selection.spectral_correction_module import SpectralCorrectionModule, import_report
    from lspr_imaging_app.storage.session import session_path
    from tests.unit.test_lspri_spectral_passband import DARK, PIXEL, make_stored, sweep_csv
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 32, 40
WAVELENGTHS = (500.0, 550.0, 600.0)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    for cube in (0, 1):
        for wavelength in WAVELENGTHS:
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), np.full((H, W), 1000.0, dtype=np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _pump_until(condition, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class SpectralCorrectionAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.spectral: SpectralCorrectionModule = self.m.spectral
        self.notices: list[str] = []
        self.spectral.notice.connect(self.notices.append)
        self.finished: list[tuple[object, str]] = []
        self.spectral.import_finished.connect(lambda table, error: self.finished.append((table, error)))
        self.sweep = self.root / "spectral_sweep_test.csv"
        self.sweep.write_bytes(sweep_csv())
        self.panels: list[SpectraPanel] = []

    def tearDown(self) -> None:
        for panel in self.panels:
            panel.shutdown()
            panel.deleteLater()
        self.m.session_autosave.flush()
        self.spectral.shutdown()
        _APP.processEvents()
        self._tmp.cleanup()

    # -- helpers ----------------------------------------------------------------------------------------------------------------

    def _open(self) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        _pump_until(lambda: self.m.session_coordinator.active_session_id() is not None)
        _APP.processEvents()

    def _session_root(self) -> Path:
        return self.root / "sessions" / self.m.session_coordinator.active_session_id()

    def _saved_payload(self) -> dict:
        self.m.session_autosave.schedule()  # a session nobody changed has no file yet
        self.m.session_autosave.flush()
        root = self._session_root()
        self.m.session_autosave.wait_until_written(root)
        return json.loads(session_path(root).read_text(encoding="utf-8"))

    def _import(self, path: Path | None = None, dark: Path | None = None, lamp: str = "test LED") -> None:
        before = len(self.finished)
        self.spectral.import_sweep(path or self.sweep, dark, lamp)
        self.assertTrue(_pump_until(lambda: len(self.finished) > before), "the import did not finish")

    def _table_file(self) -> Path:
        return self._session_root() / "spectral_correction" / ps.FILE_NAME

    # -- the import and the session ---------------------------------------------------------------------------------------------

    def test_an_import_is_copied_into_the_session_and_comes_back_with_it(self) -> None:
        self._open()
        self.assertIsNone(self.spectral.state())  # a new session: no table, no block
        self.assertNotIn("spectral_correction", self._saved_payload())
        self._import()
        table, error = self.finished[-1]
        self.assertEqual(error, "")
        self.assertTrue(self._table_file().is_file())
        self.assertEqual(self.spectral.mode(), "none", "an import never changes what the numbers show")
        self.assertTrue(self.spectral.set_mode("centroid"))
        payload = self._saved_payload()
        block = payload["spectral_correction"]
        self.assertEqual(payload["schema_version"], "1.13")
        self.assertEqual((block["mode"], block["sha256"], block["source_name"], block["illumination"]), ("centroid", table.sha256, "spectral_sweep_test.csv", "test LED"))
        self.assertEqual(block["file"], "spectral_correction/spectral_correction.h5")
        centres = self.spectral.passbands().centroid_nm.copy()
        self.m.dataset.clear_dataset()
        self._open()
        self.assertTrue(_pump_until(self.spectral.has_table), "the table was not read back")
        self.assertEqual(self.spectral.mode(), "centroid")
        np.testing.assert_array_equal(self.spectral.passbands().centroid_nm, centres)
        self.assertEqual(self.spectral.problem(), "")

    def test_the_mode_cannot_be_picked_without_a_table(self) -> None:
        self._open()
        self.assertFalse(self.spectral.set_mode("centroid"))
        self.assertEqual(self.spectral.mode(), "none")
        self.assertTrue(any("Import" in n for n in self.notices))
        self.assertTrue(self.spectral.set_mode("none"))  # leaving it as it is always works
        with self.assertRaises(ValueError):
            self.spectral.set_mode("everything")

    def test_a_file_that_is_not_a_sweep_changes_nothing(self) -> None:
        self._open()
        bad = self.root / "dark_spectrum_x.csv"
        bad.write_text("wavelength_nm,intensity\n500,1\n", encoding="utf-8")
        self._import(bad)
        table, error = self.finished[-1]
        self.assertIsNone(table)
        self.assertIn("dark-spectrum file", error)
        self.assertFalse(self.spectral.has_table())
        self.assertFalse(self._table_file().exists())
        self._import()  # a good one afterwards works
        self.assertTrue(self.spectral.has_table())
        self._import(bad)  # and a bad one now leaves the good table in place
        self.assertTrue(self.spectral.has_table())
        self.assertEqual(self.spectral.table().source_name, "spectral_sweep_test.csv")

    def test_a_missing_session_is_said_not_ignored(self) -> None:
        self._import()  # no dataset, so no session folder
        table, error = self.finished[-1]
        self.assertIsNone(table)
        self.assertIn("No session is open", error)

    def test_a_second_import_keeps_the_first_as_the_backup_and_the_mode(self) -> None:
        self._open()
        self._import(lamp="lamp one")
        first_digest = self.spectral.table().sha256
        self.spectral.set_mode("profile")
        second = self.root / "spectral_sweep_second.csv"
        second.write_bytes(sweep_csv(noise=2.0, seed=9))
        self._import(second, lamp="lamp two")
        self.assertEqual(self.spectral.table().illumination, "lamp two")
        self.assertEqual(self.spectral.mode(), "profile")
        backup = self._session_root() / "spectral_correction" / ps.BACKUP_NAME
        self.assertTrue(backup.is_file())
        self.assertEqual(ps.read_table(backup).sha256, first_digest)
        self.assertNotEqual(self.spectral.table().sha256, first_digest)

    def test_a_separate_dark_file_is_taken_with_the_sweep(self) -> None:
        self._open()
        sweep = self.root / "nodark.csv"
        sweep.write_bytes(sweep_csv(with_dark=False))
        dark = self.root / "dark_spectrum_x.csv"
        dark.write_text("wavelength_nm,intensity\n" + "\n".join(f"{p:.3f},{v:.4f}" for p, v in zip(PIXEL, DARK)) + "\n", encoding="utf-8")
        self._import(sweep, dark)
        self.assertIsNotNone(self.spectral.table().dark)
        self.assertEqual(self.finished[-1][1], "")

    # -- a table that cannot be used ------------------------------------------------------------------------------------------

    def test_a_damaged_table_is_said_not_hidden_and_its_block_is_kept(self) -> None:
        self._open()
        self._import()
        self.spectral.set_mode("centroid")
        block = self._saved_payload()["spectral_correction"]
        path = self._table_file()
        self.m.dataset.clear_dataset()
        path.write_bytes(b"this is not an hdf5 file")
        self.notices.clear()
        self._open()
        self.assertTrue(_pump_until(lambda: bool(self.spectral.problem())))
        self.assertFalse(self.spectral.has_table())
        self.assertEqual(self.spectral.mode(), "none", "no table, so nothing is corrected: the pick-up must not say otherwise")
        self.assertTrue(any("could not be used" in n for n in self.notices))
        self.assertEqual(self.spectral.state(), block, "the session still names the table, so it can be found again")
        self.assertIsNone(self.m.scan_results.stored())  # (no scan in this test: nothing to carry it)
        self.assertEqual(self.m.scan_results.spectral_correction()[0], None)

    def test_a_table_that_is_not_the_one_the_session_was_saved_with_is_refused(self) -> None:
        self._open()
        self._import()
        path = self._table_file()
        self.m.dataset.clear_dataset()
        ps.write_table(path, ps.parse_sweep(sweep_csv(noise=3.0, seed=4), "other.csv"))  # a valid table, but another one
        self._open()
        self.assertTrue(_pump_until(lambda: bool(self.spectral.problem())))
        self.assertIn("checksum", self.spectral.problem())
        self.assertFalse(self.spectral.has_table())

    def test_a_block_may_not_point_outside_the_session(self) -> None:
        self._open()
        outside = self.root / "outside.h5"
        ps.write_table(outside, ps.parse_sweep(sweep_csv(), "x.csv"))
        self.spectral.restore_state({"mode": "centroid", "file": "../../outside.h5", "sha256": ""})
        self.assertTrue(_pump_until(lambda: bool(self.spectral.problem())))
        self.assertIn("not inside the session folder", self.spectral.problem())

    # -- the scan the display sees ----------------------------------------------------------------------------------------------

    def test_the_display_gets_a_scan_that_carries_the_correction(self) -> None:
        self._open()
        stored = make_stored()
        results = self.m.scan_results
        results._paired = PairedScan(stored, {})  # a scan as the pairing leaves it (no file is read in this test)
        changes: list[int] = []
        results.results_changed.connect(lambda: changes.append(1))
        self.assertIs(results.stored(), stored, "no table, no mode: the display's scan is the paired scan itself")
        self._import()
        self.assertEqual(changes, [], "an import alone changes nothing the display shows")
        self.assertIs(results.stored(), stored)
        self.spectral.set_mode("centroid")
        shown = results.stored()
        self.assertIsNot(shown, stored)
        self.assertEqual((shown.spectral.mode, shown.spectral.n_covered), ("centroid", 26))
        self.assertIs(results.stored(), shown, "the same object until something changes: caches key on its identity")
        np.testing.assert_array_equal(shown.values["mean"], stored.values["mean"])  # the numbers are shared, never copied or changed
        self.assertEqual(shown.wavelength_nm, stored.wavelength_nm)  # the nominal axis stays: hashes and plan matching read it
        self.assertEqual(len(changes), 1)
        self.spectral.set_mode("profile")
        self.assertEqual(results.stored().spectral.mode, "profile")
        self.assertIsNot(results.stored(), shown)
        self.spectral.set_mode("none")
        self.assertIs(results.stored(), stored)
        self.assertEqual(len(changes), 3)
        self.assertIsNone(stored.spectral, "the paired scan itself is never modified")

    # -- the Spectra ribbon -----------------------------------------------------------------------------------------------------

    def _panel(self) -> SpectraPanel:
        panel = SpectraPanel(self.m.scan_results, self.m.roi_toolbox, self.m.selection, self.m.analysis_settings, spectral=self.spectral)
        self.panels.append(panel)
        return panel

    def test_the_pick_up_goes_back_when_there_is_no_table(self) -> None:
        self._open()
        panel = self._panel()
        pick = panel._correction.pick
        self.assertEqual([pick._choices[i].key for i in range(pick.count())], ["none", "centroid", "profile"])
        self.assertEqual(pick.currentData(), "none")
        pick.setCurrentIndex(pick.findData("centroid"))
        self.assertEqual(pick.currentData(), "none", "a refused pick goes back to what is in force")
        self.assertEqual(self.spectral.mode(), "none")
        self.assertTrue(self.notices)

    def test_the_pick_up_sets_the_module_and_follows_it(self) -> None:
        self._open()
        panel = self._panel()
        self._import()
        pick = panel._correction.pick
        pick.setCurrentIndex(pick.findData("profile"))
        self.assertEqual(self.spectral.mode(), "profile")
        self.spectral.set_mode("centroid")  # changed from elsewhere (a restored session, a test): the pick-up follows
        self.assertEqual(pick.currentData(), "centroid")

    def test_the_i_text_says_what_is_in_force(self) -> None:
        self._open()
        panel = self._panel()
        info = panel._correction.info
        self.assertIn("No table imported", info.toolTip())
        self.assertIn("Full profile", info.toolTip())  # the explanation is always there
        self._import()
        self.spectral.set_mode("profile")
        text = info.toolTip()
        self.assertIn("spectral_sweep_test.csv", text)
        self.assertIn("test LED", text)
        self.assertIn("filter at 30.8", text)
        self.assertIn("61 of 61 bands", text)
        self.assertIn("Polynomial fit: the full profile is applied", text)
        self.m.analysis_settings.set_metric_settings(replace(self.m.analysis_settings.metric_settings(), fit_method="gaussian"))
        self.assertIn("Gaussian fit: read as Centroid", info.toolTip())  # follows the fit method

    def test_the_i_text_says_why_a_table_is_not_used(self) -> None:
        text = info_html(None, "", "", "none", "polynomial", None, "The spectral-correction file is missing.")
        self.assertIn("The spectral-correction file is missing.", text)
        self.assertIn("No correction is applied meanwhile", text)
        self.assertNotIn("No table imported", text)

    def test_every_mode_says_what_it_does_with_every_fit(self) -> None:
        for fit in ("polynomial", "gaussian", "voigt", "template", "response", "none"):
            sentences = {mode: fit_effect(mode, fit) for mode in sp.MODES}
            self.assertEqual(len(set(sentences.values())), 3, fit)  # three modes, three different sentences, for every fit
        self.assertIn("applied", fit_effect("profile", "polynomial"))
        for fit in ("gaussian", "voigt", "template", "response", "none"):
            self.assertIn("read as Centroid", fit_effect("profile", fit), fit)  # never a silent fall-back: the sentence says it

    # -- the import entry's pieces ----------------------------------------------------------------------------------------------

    def test_the_file_kind_is_read_from_the_header_not_the_name(self) -> None:
        dark = self.root / "whatever.csv"
        dark.write_text("wavelength_nm,intensity\n500,1\n", encoding="utf-8")
        other = self.root / "spectral_sweep_looks_right.csv"
        other.write_text("a,b\n1,2\n", encoding="utf-8")
        self.assertEqual(_sweep_file_kind(self.sweep), "sweep")
        self.assertEqual(_sweep_file_kind(dark), "dark")
        self.assertEqual(_sweep_file_kind(other), "")
        self.assertEqual(_sweep_file_kind(self.root / "missing.csv"), "")

    def test_the_import_report_says_what_was_read_and_what_to_do_next(self) -> None:
        table = ps.parse_sweep(sweep_csv(), "s.csv", illumination="white LED")
        report = import_report(table, sp.derive(table), [470.0, 480.0, 487.0], "none")
        self.assertIn("61 of 61 bands read from s.csv (420-720 nm)", report)
        self.assertIn("Light source: white LED", report)
        self.assertIn("30.8 °C", report)
        self.assertIn("3 wavelengths; 2 have a band", report)
        self.assertIn("Pick Centroid or Full profile", report)
        self.assertIn("There is no scan yet", import_report(table, sp.derive(table), None, "none"))

    def test_the_file_menu_has_the_entry_and_enables_it_with_a_dataset(self) -> None:
        window = QtWidgets.QMainWindow()
        menu, _edit, _view = app_rewrite._build_menu_bar(window)
        actions = app_rewrite._wire_file_menu(menu, window, self.m, UiStateStore({}))
        try:
            self.assertEqual(actions.import_spectral_action.text(), "Spectral Correction...")
            self.assertFalse(actions.import_spectral_action.isEnabled())  # no dataset yet
            self._open()
            self.assertTrue(actions.import_spectral_action.isEnabled())
        finally:
            actions.manager.hide()
            window.deleteLater()
            _APP.processEvents()


if __name__ == "__main__":
    unittest.main()
