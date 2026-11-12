"""Refractive indices in the app (2026-11-12): RIs.txt imported into the session, auto-import from the dataset folder, a changed file
reported but not taken in, the Dataset Manager's table, File -> Import -> Refractive Indices, and the Preferences wavelength.

The real modules (`app_rewrite._build_modules`) and File menu (`_build_menu_bar` + `_wire_file_menu`), a small real dataset with two
experiment steps; sessions are written and read back from disk. Dialogs are patched.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming, ImagingPlan, ImagingPlanStep

    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels import file_menu as file_menu_module
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.storage.session import SESSION_SCHEMA_VERSION, session_path
    from lspr_imaging_app.storage.ui_state_keys import RI_WAVELENGTH_NM
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

H, W = 24, 30
WAVELENGTHS = (500.0, 550.0)
T0 = 1_700_000_000_000  # 2023-11-14 (UTC and Central Europe): the experiment day
CUBE_MS = 3_000

# H2O measured 4 days before the experiment, 10perEG 13 days before (older than the 7-day limit), MeOH not used by any step.
RI_FILE = (
    "Sample\tRI\tStd\tTemp\tN\tDate\n"
    "H2O\t1.33268\t0.000000\t24\t5\t10.11.2023\n"
    "10perEG\t1.34322\t0.000010\t24\t5\t01.11.2023\n"
    "MeOH\t1.32831\t0.000029\t24\t5\t20.08.2023\n"
)


def _metadata() -> ImagingAcquisitionMetadata:
    timings = [
        ImagingCubeTiming(spectral_cube_index=cube, wavelength_nm=wl, acquired_at_unix_ms=T0 + CUBE_MS * cube + 100 * k)
        for cube in range(4) for k, wl in enumerate(WAVELENGTHS)
    ]
    steps = [
        ImagingPlanStep(step=1, label="H2O_--_ch1F_ch2P", start_unix_ms=T0, end_unix_ms=T0 + 2 * CUBE_MS),
        ImagingPlanStep(step=2, label="10perEG_-_ch1_to_20perEG_-_ch2F", start_unix_ms=T0 + 2 * CUBE_MS, end_unix_ms=T0 + 3 * CUBE_MS),
        ImagingPlanStep(step=3, label="PSS(f)_--_ch3F", start_unix_ms=T0 + 3 * CUBE_MS, end_unix_ms=T0 + 4 * CUBE_MS + 200),
    ]
    return ImagingAcquisitionMetadata(source_format="test", image_timings=timings, plan=ImagingPlan(steps=steps))


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    for cube in range(4):
        for wavelength in WAVELENGTHS:
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), np.full((H, W), 1000.0, dtype=np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack", acquisition_metadata=_metadata())


def _pump_until(condition, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class RefractiveIndexAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.plan = self.m.experiment_plan
        self.window = QtWidgets.QMainWindow()
        self.ui_state = UiStateStore({})
        menu, _edit, _view = app_rewrite._build_menu_bar(self.window)
        self.actions = app_rewrite._wire_file_menu(menu, self.window, self.m, self.ui_state)
        self.notices: list[str] = []
        self.plan.ri_notice.connect(self.notices.append)

    def tearDown(self) -> None:
        self.m.session_autosave.flush()
        self.actions.manager.hide()
        self.window.deleteLater()
        _APP.processEvents()
        self._tmp.cleanup()

    def _open(self) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        _pump_until(lambda: self.m.session_coordinator.active_session_id() is not None)
        _APP.processEvents()

    def _saved_payload(self) -> dict:
        self.m.session_autosave.flush()
        root = self.root / "sessions" / self.m.session_coordinator.active_session_id()
        self.m.session_autosave.wait_until_written(root)
        return json.loads(session_path(root).read_text(encoding="utf-8"))

    # -- auto-import and the session ------------------------------------------------------------------

    def test_RIs_txt_in_the_dataset_folder_is_imported_and_saved_with_the_session(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        table = self.plan.ri_table()
        self.assertIsNotNone(table)
        self.assertEqual([row.name for row in table.solutions], ["H2O", "10perEG", "MeOH"])  # the whole file is kept
        self.assertTrue(any("imported" in text for text in self.notices))
        payload = self._saved_payload()
        self.assertEqual(payload["schema_version"], SESSION_SCHEMA_VERSION)
        self.assertEqual(payload["refractive_index"]["sha256"], table.sha256)
        self.assertAlmostEqual(payload["refractive_index"]["solutions"][0]["ri"], 1.33268, places=9)

    def test_without_RIs_txt_there_is_no_table_and_no_block(self) -> None:
        self._open()
        self.assertIsNone(self.plan.ri_table())
        self.m.session_autosave.schedule()  # nothing changed, so nothing was written yet: ask for a save
        self.assertNotIn("refractive_index", self._saved_payload())

    def test_a_changed_file_is_reported_not_taken_in(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        imported = self.plan.ri_table()
        self._saved_payload()  # on disk before the dataset is opened again
        (self.root / "RIs.txt").write_text(RI_FILE.replace("1.33268", "1.33300").replace("10.11.2023", "13.11.2023"), encoding="utf-8")
        self.notices.clear()
        self.m.dataset.clear_dataset()
        self.assertIsNone(self.plan.ri_table())
        self._open()
        self.assertTrue(_pump_until(lambda: self.plan.ri_table() is not None), "the saved table comes back with the session")
        _pump_until(lambda: self.plan.ri_file_changed())
        self.assertEqual(self.plan.ri_table(), imported, "the session keeps the values it was analysed with")
        self.assertTrue(self.plan.ri_file_changed())
        self.assertTrue(any("has changed" in text for text in self.notices))

    def test_closing_the_dataset_does_not_save_the_cleared_plan_over_the_session(self) -> None:
        """Found 2026-11-12: the plan module cleared itself on `dataset_cleared` while autosave still pointed at the session, and the
        session's hand-over then wrote that cleared state (plan edits and RI table gone) over the saved file."""
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        self.plan.set_label(0, "H2O renamed by the user")
        self._saved_payload()
        self.m.dataset.clear_dataset()
        self._open()
        self.assertTrue(_pump_until(lambda: self.plan.ri_table() is not None))
        self.assertEqual(self.plan.steps()[0].label, "H2O renamed by the user")

    # -- File -> Import -> Refractive Indices --------------------------------------------------------

    def test_import_from_the_menu_stamps_the_preference_wavelength(self) -> None:
        self._open()
        path = self.root / "other_RIs.txt"
        path.write_text(RI_FILE, encoding="utf-8")
        self.ui_state.set(RI_WAVELENGTH_NM.key, 589.3)
        app_rewrite._apply_ri_settings(self.m, self.actions, 589.3, 7)
        with mock.patch.object(file_menu_module.QFileDialog, "getOpenFileName", return_value=(str(path), "")), \
                mock.patch.object(file_menu_module.QMessageBox, "information") as info:
            self.actions.import_ri_action.trigger()
        table = self.plan.ri_table()
        self.assertEqual(table.source_name, "other_RIs.txt")
        self.assertEqual(table.wavelength_nm, 589.3)
        report = info.call_args.args[2]
        self.assertIn("No RI for: PSS(f) (step 3)", report)
        self.assertIn("Not used by any step (kept, not shown): MeOH", report)

    def test_a_bad_file_is_refused_and_the_old_table_stays(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        before = self.plan.ri_table()
        bad = self.root / "bad.txt"
        bad.write_text("Sample\tRI\nH2O\tnot-a-number\n", encoding="utf-8")
        with mock.patch.object(file_menu_module.QFileDialog, "getOpenFileName", return_value=(str(bad), "")), \
                mock.patch.object(file_menu_module.QMessageBox, "critical") as critical:
            self.actions.import_ri_action.trigger()
        self.assertIn("line 2", critical.call_args.args[2])
        self.assertEqual(self.plan.ri_table(), before)

    # -- the Dataset Manager's table ------------------------------------------------------------------

    def test_the_table_shows_the_used_solutions_with_their_age(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        view = self.actions.manager.refractive
        self.actions.manager.present("plan")
        _APP.processEvents()
        rows = [[view.table.item(r, c).text() for c in range(view.table.columnCount())] for r in range(view.table.rowCount())]
        self.assertEqual(
            rows,
            [
                ["H2O", "1.33268", "0.000000", "24.0", "5", "10.11.2023", "4", "1"],
                ["10perEG", "1.34322", "0.000010", "24.0", "5", "01.11.2023", "13", "2"],
            ],
        )
        self.assertEqual(view.table.item(0, 6).toolTip(), "")  # 4 days: within the 7-day limit
        self.assertIn("limit: 7 days", view.table.item(1, 6).toolTip())  # 13 days: amber
        self.assertIn("No RI for: PSS(f) (step 3)", view.notes_label.text())
        self.assertIn("not used by any step: MeOH", view.notes_label.text())
        self.assertIn("Refractometer wavelength: 589.3 nm", view.source_label.text())  # the default: the lab's J357
        self.assertEqual(self.plan.ri_table().wavelength_nm, 589.3)

    def test_the_age_limit_follows_preferences(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        self.actions.manager.present("plan")
        app_rewrite._apply_ri_settings(self.m, self.actions, 0.0, 14)
        _APP.processEvents()
        self.assertEqual(self.actions.manager.refractive.table.item(1, 6).toolTip(), "")
        self.assertIsNone(self.plan.ri_wavelength_nm, "0 means not given, never a wavelength")


if __name__ == "__main__":
    unittest.main()
