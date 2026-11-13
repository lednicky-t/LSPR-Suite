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
from datetime import date
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
    from lspr_imaging_app.analysis.step_table import OP_BULK
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels import file_menu as file_menu_module
    from lspr_imaging_app.panels.dataset_manager.refractive_indices import COL_NAME, COL_RI, COL_STATUS, COL_STD, COL_STEPS
    from lspr_imaging_app.plan.refractive_index import RiEditError
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.storage.session import SESSION_SCHEMA_VERSION, session_path
    from lspr_imaging_app.storage.ui_state_keys import RI_WAVELENGTH_NM
    from lspr_imaging_app.undo import undo_manager
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
        undo_manager.clear()

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
        self.assertIn("Not used by any step (kept in the table): MeOH", report)

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
        rows = [[view.table.item(r, c).text() for c in range(view.table.columnCount())] for r in range(2)]  # (the other rows: the editing tests below)
        self.assertEqual(
            rows,
            [
                ["H2O", "1.33268", "0.000000", "24.0", "5", "10.11.2023", "4", "1", ""],
                ["10perEG", "1.34322", "0.000010", "24.0", "5", "01.11.2023", "13", "2", ""],
            ],
        )
        self.assertEqual(view.table.item(0, 6).toolTip(), "")  # 4 days: within the 7-day limit
        self.assertIn("limit: 7 days", view.table.item(1, 6).toolTip())  # 13 days: amber
        self.assertIn("No RI for: PSS(f) (step 3)", view.notes_label.text())
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


    # -- editing the table (2026-11-13) ---------------------------------------------------------------

    def _view(self):
        self.actions.manager.present("plan")
        _APP.processEvents()
        return self.actions.manager.refractive

    def _cells(self, view) -> list[list[str]]:
        return [[view.table.item(r, c).text() for c in range(view.table.columnCount())] for r in range(view.table.rowCount())]

    def test_an_edit_is_used_marked_saved_and_comes_back_with_the_session(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        step1 = self.plan.steps()[0]
        self.assertEqual((self.plan.ri_of_step(step1).ri, self.plan.ri_of_step(step1).edited), (1.33268, False))
        changes: list[int] = []
        self.plan.ri_changed.connect(lambda: changes.append(1))
        imported = self.plan.ri_table()
        self.assertTrue(self.plan.ri_edit(0, "ri", "1.33300"))
        self.assertTrue(changes)  # everything that follows the table (S_bulk columns, bulk plot, plan bar, autosave) is told
        found = self.plan.ri_of_step(step1)
        self.assertEqual((found.ri, found.edited), (1.333, True))
        self.assertFalse(self.plan.ri_edit(0, "ri", "1.33300"))  # the value it is: no change, no undo step
        payload = self._saved_payload()
        block = payload["refractive_index"]
        self.assertEqual(payload["schema_version"], "1.9")
        self.assertEqual(block["sha256"], imported.sha256)
        self.assertAlmostEqual(block["solutions"][0]["ri"], 1.33268, places=9)  # the imported copy is never changed
        self.assertAlmostEqual(block["rows"][0]["ri"], 1.333, places=9)
        edited = self.plan.ri_table()
        self.m.dataset.clear_dataset()
        self.assertIsNone(self.plan.ri_table())
        self._open()
        self.assertTrue(_pump_until(lambda: self.plan.ri_table() is not None and self.plan.ri_table().has_edits()))
        self.assertEqual(self.plan.ri_table(), edited)
        self.assertEqual(self.plan.ri_of_step(self.plan.steps()[0]).ri, 1.333)

    def test_a_table_nobody_edited_is_saved_without_rows_and_an_older_session_still_loads(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        self.assertNotIn("rows", self._saved_payload()["refractive_index"])  # as schema 1.8 wrote it
        self.plan.ri_edit(0, "ri", "1.4")
        self.plan.ri_revert_all()
        self.m.session_autosave.schedule()
        self.assertNotIn("rows", self._saved_payload()["refractive_index"])  # edited and put back: nothing to keep

    def test_every_edit_is_one_undo_step_and_an_import_in_between_is_not_overwritten(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        undo_manager.clear()
        self.plan.ri_edit(0, "ri", "1.33300")
        self.plan.ri_edit(1, "name", "10perEG_x")
        self.assertEqual(self.plan.ri_table().edit_counts(), (2, 0, 0))
        undo_manager.undo()
        self.assertEqual(self.plan.ri_table().find("10perEG").ri, 1.34322)  # the rename is undone ...
        self.assertEqual(self.plan.ri_table().find("H2O").ri, 1.333)  # ... the first edit is not
        undo_manager.undo()
        self.assertIsNone(self.plan.ri_table().rows)
        undo_manager.redo()
        self.assertEqual(self.plan.ri_table().find("H2O").ri, 1.333)
        other = self.root / "other_RIs.txt"  # now an import replaces the table, and the undo of the earlier edit must not bring the old table back
        other.write_text(RI_FILE.replace("1.33268", "1.40000"), encoding="utf-8")
        self.plan.import_ri_file(other)
        self.notices.clear()
        undo_manager.undo()
        self.assertEqual(self.plan.ri_table().source_name, "other_RIs.txt")
        self.assertIsNone(self.plan.ri_table().rows)
        self.assertTrue(any("not undone" in text for text in self.notices))

    def test_a_table_can_be_started_by_hand_without_any_file(self) -> None:
        self._open()
        self.assertIsNone(self.plan.ri_table())
        self.assertTrue(self.plan.ri_add_row_for("H2O", "1.3330"))
        table = self.plan.ri_table()
        self.assertEqual((table.source_name, table.sha256, table.solutions), ("entered by hand", "", ()))
        self.assertEqual(self.plan.ri_of_step(self.plan.steps()[0]).ri, 1.333)
        self.assertTrue(self.plan.ri_of_step(self.plan.steps()[0]).edited)  # typed by you: marked like any value you made
        self.assertEqual(self._saved_payload()["refractive_index"]["rows"][0]["name"], "H2O")
        self.plan.ri_revert_all()
        self.assertIsNone(self.plan.ri_table())  # nothing left: no table again

    def test_the_s_bulk_numbers_follow_an_edit_and_the_export_record_says_which_values_were_edited(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        table = self.m.step_table
        self.plan.add_to_bulk([0, 1])  # steps 1 and 2: H2O and 10perEG
        table.set_bulk_shown(True)
        self.assertEqual([p.ri for p in table.bulk_points()], [1.33268, 1.34322])
        self.assertFalse(table.export_info()["bulk_sensitivity"]["ri_file"]["table_edited"])  # nothing edited yet
        self.assertEqual(table.export_info()["bulk_sensitivity"]["ri_edits"], [])
        emitted: list[int] = []
        table.numbers_changed.connect(lambda: emitted.append(1))
        self.plan.ri_edit(0, "ri", "1.33300")
        self.assertEqual([p.ri for p in table.bulk_points()], [1.333, 1.34322])  # the S_bulk points have the new RI ...
        self.assertTrue(emitted)  # ... and the column's numbers were told to recalculate
        record = table.export_info()["bulk_sensitivity"]
        self.assertTrue(record["ri_file"]["table_edited"])
        self.assertEqual([(e["solution"], e["ri_used"]) for e in record["ri_edits"]], [("H2O", 1.333)])
        self.assertIn("1.33268", record["ri_edits"][0]["imported"])  # what the file had
        bulk_column = next(c for c in table.columns() if c.op == OP_BULK)
        self.assertIn("Uses refractive indices you edited: H2O 1.33300", bulk_column.tooltip)
        self.plan.ri_revert_all()
        self.assertEqual(table.export_info()["bulk_sensitivity"]["ri_edits"], [])
        self.assertNotIn("you edited", next(c for c in table.columns() if c.op == OP_BULK).tooltip)

    def test_the_plan_bar_hover_says_when_a_value_is_edited(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        edited = self.plan.ri_edited_values_used_by(self.plan.steps())
        self.assertEqual(edited, [])
        self.plan.ri_edit(0, "ri", "1.33300")
        used = self.plan.ri_edited_values_used_by(self.plan.steps())
        self.assertEqual([(value.name, text.startswith("H2O: RI 1.33268")) for value, text in used], [("H2O", True)])

    def test_the_steps_of_a_row_can_be_given_by_hand_and_a_refused_one_changes_nothing(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        steps = self.plan.steps()
        self.assertTrue(self.plan.ri_set_steps(2, "3"))  # MeOH (unused) serves step 3, the PSS(f) step, which had no RI
        self.assertEqual(self.plan.ri_of_step(steps[2]).name, "MeOH")
        self.assertEqual(self.plan.ri_usage().missing, ())
        before = self.plan.ri_table()
        for text, fragment in (("4", "there is no step 4"), ("1, x", "not a step number"), ("0", "start at 1")):
            with self.subTest(text=text), self.assertRaises(RiEditError) as raised:
                self.plan.ri_set_steps(0, text)
            self.assertIn(fragment, str(raised.exception))
        with self.assertRaises(RiEditError) as raised:
            self.plan.ri_set_steps(0, "3")  # H2O wants the step MeOH has by hand
        self.assertIn("step 3 is already assigned by hand to 'MeOH'", str(raised.exception))
        self.assertEqual(self.plan.ri_table(), before)
        self.assertTrue(self.plan.ri_set_steps(2, ""))  # back to its name
        self.assertIsNone(self.plan.ri_of_step(steps[2]))

    # -- the Dataset Manager's table, edited ----------------------------------------------------------

    def test_the_table_lists_every_row_a_row_for_a_solution_with_none_and_dims_the_unused(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        view = self._view()
        meoh_age = str((date(2023, 11, 14) - date(2023, 8, 20)).days)
        self.assertEqual(
            self._cells(view),
            [
                ["H2O", "1.33268", "0.000000", "24.0", "5", "10.11.2023", "4", "1", ""],
                ["10perEG", "1.34322", "0.000010", "24.0", "5", "01.11.2023", "13", "2", ""],
                ["MeOH", "1.32831", "0.000029", "24.0", "5", "20.08.2023", meoh_age, "", ""],  # no step uses it: dimmed, still there
                ["PSS(f)", "", "", "", "", "", "", "3", "no RI"],  # the step solution with no row: type its RI here
            ],
        )
        self.assertFalse(view.edits_label.isVisible())
        self.assertFalse(view.table.item(0, COL_RI).font().bold())
        self.assertFalse(view.revert_all_button.isEnabled())

    def test_typing_a_value_changes_the_calculation_and_marks_the_cell(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        view = self._view()
        view.table.item(0, COL_RI).setText("1.33300")
        _APP.processEvents()
        self.assertEqual(self.plan.ri_table().find("H2O").ri, 1.333)
        cells = self._cells(view)[0]
        self.assertEqual((cells[COL_RI], cells[COL_STATUS]), ("1.33300", "edited"))
        self.assertTrue(view.table.item(0, COL_RI).font().bold())  # the cell you changed is marked ...
        self.assertFalse(view.table.item(0, COL_STD).font().bold())  # ... the others are not
        self.assertIn("As imported: H2O: RI 1.33268", view.table.item(0, COL_RI).toolTip())
        self.assertIn("1 row edited", view.edits_label.text())
        self.assertTrue(view.revert_all_button.isEnabled())

    def test_a_refused_value_is_put_back_and_the_reason_shown(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        view = self._view()
        view.table.item(0, COL_RI).setText("7")
        _APP.processEvents()
        self.assertFalse(view.error_label.isHidden())
        self.assertIn("outside 1.0-3.0", view.error_label.text())
        self.assertEqual(view.table.item(0, COL_RI).text(), "1.33268")  # the cell shows what is stored
        self.assertIsNone(self.plan.ri_table().rows)
        view.table.item(1, COL_NAME).setText("h2o")
        _APP.processEvents()
        self.assertIn("already in the table", view.error_label.text())
        view.table.item(0, COL_RI).setText("1.334")  # the next good edit clears the message
        _APP.processEvents()
        self.assertTrue(view.error_label.isHidden())

    def test_the_steps_cell_gives_a_row_to_steps_by_hand_and_the_placeholder_gets_its_ri_by_typing(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        view = self._view()
        view.table.item(3, COL_RI).setText("1.3351")  # the PSS(f) placeholder: its RI makes it a row
        _APP.processEvents()
        row = self.plan.ri_table().find("PSS(f)")
        self.assertEqual((row.ri, row.edited), (1.3351, True))
        cells = self._cells(view)
        self.assertEqual((cells[3][COL_NAME], cells[3][COL_STEPS], cells[3][COL_STATUS]), ("PSS(f)", "3", "added"))
        self.assertEqual(len(cells), 4)  # no placeholder any more
        view.table.item(2, COL_STEPS).setText("1")  # MeOH to step 1 as well, which H2O has by name: by hand comes first
        _APP.processEvents()
        self.assertEqual(self.plan.ri_of_step(self.plan.steps()[0]).name, "MeOH")
        cells = self._cells(view)
        self.assertEqual((cells[2][COL_STEPS], cells[0][COL_STEPS]), ("1", ""))  # H2O now serves nothing
        self.assertIn("steps by hand", cells[2][COL_STATUS])

    def test_add_remove_and_revert_buttons(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        view = self._view()
        view.add_button.click()
        _APP.processEvents()
        self.assertEqual(self._cells(view)[3][COL_STATUS], "added, no RI")  # a blank row after the table rows, before the placeholder
        view.table.item(3, COL_NAME).setText("PSS(f)")
        _APP.processEvents()
        self.assertIsNone(self.plan.ri_of_step(self.plan.steps()[2]))  # a name without an RI serves nothing yet
        self.assertIn("no RI", self._cells(view)[3][COL_STATUS])
        view.table.item(3, COL_RI).setText("1.3345")
        _APP.processEvents()
        self.assertEqual(self.plan.ri_of_step(self.plan.steps()[2]).ri, 1.3345)
        view.table.selectRow(0)  # remove H2O
        self.assertTrue(view.remove_button.isEnabled())
        view.remove_button.click()
        _APP.processEvents()
        self.assertEqual([row.name for row in self.plan.ri_table().removed_rows()], ["H2O"])
        self.assertIsNone(self.plan.ri_of_step(self.plan.steps()[0]))
        last = view.table.rowCount() - 1
        self.assertEqual(self._cells(view)[last][COL_STATUS], "removed")  # shown struck out at the end
        self.assertTrue(view.table.item(last, COL_NAME).font().strikeOut())
        view.table.selectRow(last)
        self.assertFalse(view.remove_button.isEnabled())  # a removed row cannot be removed again ...
        self.assertEqual(view.revert_button.text(), "Restore row")  # ... it is brought back
        view.revert_button.click()
        _APP.processEvents()
        self.assertEqual(self.plan.ri_table().removed_rows(), ())
        self.assertEqual(self.plan.ri_of_step(self.plan.steps()[0]).ri, 1.33268)
        view.revert_all_button.click()
        _APP.processEvents()
        self.assertIsNone(self.plan.ri_table().rows)
        self.assertEqual(len(self._cells(view)), 4)

    def test_importing_over_edits_asks_first_and_cancel_keeps_them(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        self.plan.ri_edit(0, "ri", "1.33300")
        edited = self.plan.ri_table()
        other = self.root / "other_RIs.txt"
        other.write_text(RI_FILE.replace("1.33268", "1.40000"), encoding="utf-8")
        with mock.patch.object(file_menu_module.QFileDialog, "getOpenFileName", return_value=(str(other), "")), \
                mock.patch.object(file_menu_module.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Cancel) as ask, \
                mock.patch.object(file_menu_module.QMessageBox, "information"):
            self.actions.import_ri_action.trigger()
        self.assertIn("1 row edited", ask.call_args.args[2])  # it says what would be lost
        self.assertEqual(self.plan.ri_table(), edited)
        with mock.patch.object(file_menu_module.QFileDialog, "getOpenFileName", return_value=(str(other), "")), \
                mock.patch.object(file_menu_module.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes), \
                mock.patch.object(file_menu_module.QMessageBox, "information"):
            self.actions.import_ri_action.trigger()
        self.assertEqual(self.plan.ri_table().source_name, "other_RIs.txt")
        self.assertIsNone(self.plan.ri_table().rows)
        self.assertEqual(self.plan.ri_table().find("H2O").ri, 1.4)

    def test_importing_over_an_unedited_table_does_not_ask(self) -> None:
        (self.root / "RIs.txt").write_text(RI_FILE, encoding="utf-8")
        self._open()
        other = self.root / "other_RIs.txt"
        other.write_text(RI_FILE, encoding="utf-8")
        with mock.patch.object(file_menu_module.QFileDialog, "getOpenFileName", return_value=(str(other), "")), \
                mock.patch.object(file_menu_module.QMessageBox, "question") as ask, mock.patch.object(file_menu_module.QMessageBox, "information"):
            self.actions.import_ri_action.trigger()
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
