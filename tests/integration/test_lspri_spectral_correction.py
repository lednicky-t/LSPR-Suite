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
from unittest import mock
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
    from lspr_imaging_app.selection.spectral_correction_module import SpectralCorrectionModule, camera_report, import_report
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
        self.responses: list[tuple[object, str]] = []
        self.spectral.response_finished.connect(lambda response, error: self.responses.append((response, error)))
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
        self.assertEqual(payload["schema_version"], "1.16")
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
        self.assertIn("Gaussian fit: the full profile is applied", info.toolTip())  # follows the fit method
        self.m.analysis_settings.set_metric_settings(replace(self.m.analysis_settings.metric_settings(), fit_method="template"))
        self.assertIn("Template shift: read as Centroid", info.toolTip())

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
        for fit in ("template", "response", "none"):
            self.assertIn("read as Centroid", fit_effect("profile", fit), fit)  # never a silent fall-back: the sentence says it
        for fit in ("polynomial", "gaussian", "voigt"):
            self.assertIn("full profile is applied", fit_effect("profile", fit), fit)

    # -- the camera response ----------------------------------------------------------------------------------------------------

    def _camera_file(self, slope: float = -0.003, name: str = "camera_linear.csv") -> Path:
        path = self.root / name
        rows = "".join(f"{wl:.0f},{1.0 + slope * (wl - 600.0):.5f}\n" for wl in range(380, 821, 10))
        path.write_text("# a linear response for the tests\nwavelength_nm,relative_response\n" + rows, encoding="utf-8")
        return path

    def _import_camera(self, path: Path) -> None:
        before = len(self.responses)
        self.spectral.import_camera_response(path)
        self.assertTrue(_pump_until(lambda: len(self.responses) > before), "the camera import did not finish")

    def test_a_camera_response_is_kept_with_the_session_and_weights_the_bands(self) -> None:
        self._open()
        self._import()
        flat = self.spectral.passbands().centroid_nm.copy()
        self._import_camera(self._camera_file())
        response, error = self.responses[-1]
        self.assertEqual(error, "")
        self.assertEqual((self.spectral.has_response(), self.spectral.camera_response().source_name), (True, "camera_linear.csv"))
        pb = self.spectral.passbands()
        self.assertEqual(pb.camera, "camera_linear.csv")
        self.assertLess(pb.centroid_nm[60] - flat[60], -0.05)  # 720 nm moves blue
        np.testing.assert_allclose(pb.centroid_flat_nm, flat, atol=1e-12)
        self.assertEqual(self.spectral.mode(), "none", "the camera never changes what the numbers show by itself")
        block = self._saved_payload()["spectral_correction"]
        self.assertEqual(block["camera_response"]["source_name"], "camera_linear.csv")
        self.assertEqual(len(block["camera_response"]["wavelength_nm"]), 45)
        self.spectral.set_mode("centroid")
        centres = self.spectral.passbands().centroid_nm.copy()
        self.m.dataset.clear_dataset()
        self._open()
        self.assertTrue(_pump_until(self.spectral.has_table), "the table was not read back")
        self.assertEqual((self.spectral.mode(), self.spectral.camera_response().source_name), ("centroid", "camera_linear.csv"))
        np.testing.assert_array_equal(self.spectral.passbands().centroid_nm, centres)  # derived with the camera again, not flat
        self.assertEqual(self.spectral.problem(), "")

    def test_the_camera_can_come_before_the_table_and_the_order_makes_no_difference(self) -> None:
        self._open()
        self._import_camera(self._camera_file())
        self.assertFalse(self.spectral.has_table())
        block = self._saved_payload()["spectral_correction"]
        self.assertNotIn("file", block)  # a block with only the camera
        self.assertEqual(block["camera_response"]["source_name"], "camera_linear.csv")
        self._import()  # the table, afterwards, is derived with the camera
        first = self.spectral.passbands().centroid_nm.copy()
        self.assertEqual(self.spectral.passbands().camera, "camera_linear.csv")
        self.spectral.remove_camera_response()
        self.assertTrue(_pump_until(lambda: not self.spectral.has_response() and self.spectral.passbands().camera == ""))
        self._import_camera(self._camera_file())
        np.testing.assert_allclose(self.spectral.passbands().centroid_nm, first, atol=1e-12)  # table-then-camera == camera-then-table

    def test_removing_the_camera_goes_back_to_a_flat_camera_and_leaves_no_trace(self) -> None:
        self._open()
        self._import()
        flat = self.spectral.passbands().centroid_nm.copy()
        self._import_camera(self._camera_file())
        before = len(self.responses)
        self.spectral.remove_camera_response()
        self.assertTrue(_pump_until(lambda: len(self.responses) > before))
        self.assertEqual(self.responses[-1], (None, ""))
        self.assertFalse(self.spectral.has_response())
        np.testing.assert_allclose(self.spectral.passbands().centroid_nm, flat, atol=1e-12)
        self.assertNotIn("camera_response", self._saved_payload()["spectral_correction"])

    def test_a_bad_camera_file_or_no_session_changes_nothing(self) -> None:
        self._import_camera(self._camera_file())  # no dataset yet: no session to keep it in
        self.assertEqual(self.responses[-1][0], None)
        self.assertIn("No session is open", self.responses[-1][1])
        self._open()
        self._import()
        bad = self.root / "bad.csv"
        bad.write_text("400,1\n500,2\n", encoding="utf-8")
        self._import_camera(bad)
        self.assertIn("at least 5 points", self.responses[-1][1])
        short = self._camera_file(name="short.csv")
        short.write_text("wavelength_nm,r\n" + "".join(f"{wl},1\n" for wl in range(450, 701, 10)), encoding="utf-8")  # does not reach 420 and 720
        self._import_camera(short)
        self.assertIn("the sweep goes from 420 to 720", self.responses[-1][1])
        self.assertFalse(self.spectral.has_response())
        self.assertEqual(self.spectral.passbands().camera, "")

    def test_a_camera_that_does_not_cover_the_table_is_said_when_the_session_opens(self) -> None:
        self._open()
        self._import()
        self.m.dataset.clear_dataset()
        self._open()  # reload the table, then put a response that does not cover it into the saved block
        self.assertTrue(_pump_until(self.spectral.has_table))
        block = dict(self.spectral.state())
        block["camera_response"] = sp.CameraResponse(np.arange(450.0, 701.0, 10.0), np.ones(26), "short.csv").to_payload()
        self.m.dataset.clear_dataset()
        self._open()
        self.assertTrue(_pump_until(self.spectral.has_table))  # the session's own load has been applied: only then is the forced block the last word
        self.spectral.restore_state(block)
        self.assertTrue(_pump_until(lambda: bool(self.spectral.problem())))
        self.assertIn("the sweep goes from 420 to 720", self.spectral.problem())
        self.assertFalse(self.spectral.has_table())

    def test_a_damaged_camera_block_is_said_and_written_back_as_it_was(self) -> None:
        self._open()
        raw = {"wavelength_nm": [1, 2, 3], "response": [1, 2, 3]}
        self.spectral.restore_state({"mode": "none", "camera_response": raw})
        self.assertIn("camera response could not be used", self.spectral.problem())
        self.assertFalse(self.spectral.has_response())
        self.assertEqual(self.spectral.state()["camera_response"], raw)

    def test_a_new_camera_is_a_different_correction_for_the_display_only_when_one_is_on(self) -> None:
        self._open()
        stored = make_stored()
        results = self.m.scan_results
        results._paired = PairedScan(stored, {})
        self._import()
        changes: list[int] = []
        results.results_changed.connect(lambda: changes.append(1))
        self._import_camera(self._camera_file())
        self.assertEqual(changes, [], "mode None: a camera changes nothing the display shows")
        self.spectral.set_mode("centroid")
        shown = results.stored()
        self.assertIn("camera_linear.csv", shown.spectral_info()["camera_response"])
        n = len(changes)
        self._import_camera(self._camera_file(slope=-0.002, name="camera_two.csv"))
        self.assertEqual(len(changes), n + 1, "a different camera with a correction on: the display's scan is a new object")
        self.assertIsNot(results.stored(), shown)
        self.assertEqual(results.stored().spectral_info()["camera_response"], "camera_two.csv")

    def test_the_i_text_says_what_the_camera_is(self) -> None:
        self._open()
        panel = self._panel()
        info = panel._correction.info
        self._import_camera(self._camera_file())
        self.assertIn("Camera response: camera_linear.csv (kept", info.toolTip())  # a response with no table yet
        self._import()
        text = info.toolTip()
        self.assertIn("Camera response: camera_linear.csv: the bands are weighted by it", text)
        self.assertIn("nm on average", text)
        self.spectral.remove_camera_response()
        self.assertTrue(_pump_until(lambda: "Camera: taken as flat" in info.toolTip()))

    def test_the_camera_report_says_what_it_does(self) -> None:
        table = ps.parse_sweep(sweep_csv(), "s.csv")
        response = sp.CameraResponse(np.arange(380.0, 821.0, 10.0), 1.0 - 0.003 * (np.arange(380.0, 821.0, 10.0) - 600.0), "cam.csv", "ab" * 32)
        none = camera_report(response, None, "none")
        self.assertIn("Camera response read from cam.csv: 45 points, 380-820 nm", none)
        self.assertIn("no spectral-correction table yet", none)
        with_table = camera_report(response, sp.derive(table, response), "none")
        self.assertIn("the band centres move by", with_table)
        self.assertIn("do not change until you pick Centroid or Full profile", with_table)
        self.assertIn("the shown numbers change now", camera_report(response, sp.derive(table, response), "centroid"))
        self.assertIn("Remove Camera Response", with_table)

    def test_the_file_menu_has_the_camera_entries(self) -> None:
        window = QtWidgets.QMainWindow()
        menu, _edit, _view = app_rewrite._build_menu_bar(window)
        actions = app_rewrite._wire_file_menu(menu, window, self.m, UiStateStore({}))
        try:
            self.assertEqual((actions.import_camera_action.text(), actions.remove_camera_action.text()), ("Camera Response...", "Remove Camera Response"))
            self.assertFalse(actions.import_camera_action.isEnabled())
            self._open()
            self.assertTrue(actions.import_camera_action.isEnabled())
            self.assertFalse(actions.remove_camera_action.isEnabled(), "nothing to remove yet")
            with mock.patch("lspr_imaging_app.panels.file_menu.QMessageBox.information") as report:  # the report is a modal dialog: nobody is here to close it
                self._import_camera(self._camera_file())
            self.assertEqual(report.call_count, 1)
            self.assertIn("Camera response read from camera_linear.csv", report.call_args.args[2])
            self.assertTrue(actions.remove_camera_action.isEnabled())
        finally:
            actions.manager.hide()
            window.deleteLater()
            _APP.processEvents()

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
