"""plan/refractive_index.py (2026-11-12): reading RIs.txt, matching solutions to steps, ages, the session payload. Pure, no Qt."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.plan import refractive_index as ri  # noqa: E402
from lspr_imaging_app.plan.state import added_ref, measured_ref  # noqa: E402

# The lab's file as it is (tab-separated, two names with a space or brackets), shortened.
LAB_FILE = (
    "Sample\tRI\tStd\tTemp\tN\tDate\n"
    "H2O\t1.33268\t0.000000\t24\t5\t09.09.2025\n"
    "MeOH\t1.32831\t0.000029\t24\t5\t20.08.2025\n"
    "20perEG\t1.35542\t0.000016\t24\t5\t02.09.2025\n"
    "PAH(f)\t1.33373\t0.000000\t24\t5\t06.09.2025\n"
    "0.1M NaCl\t1.33368\t0.000000\t24\t5\t03.09.2025\n"
)
NOW = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)


class ParseTests(unittest.TestCase):
    def test_the_lab_file_is_read_with_units_and_dates(self) -> None:
        rows = ri.parse_ri_text(LAB_FILE)
        self.assertEqual([row.name for row in rows], ["H2O", "MeOH", "20perEG", "PAH(f)", "0.1M NaCl"])
        water = rows[0]
        self.assertAlmostEqual(water.ri, 1.33268, places=9)
        self.assertEqual(water.std, 0.0)
        self.assertEqual(water.temp_c, 24.0)
        self.assertEqual(water.n, 5)
        self.assertEqual(water.measured_on, date(2025, 9, 9))
        self.assertAlmostEqual(rows[1].std, 2.9e-5, places=12)

    def test_columns_in_another_order_and_without_the_optional_ones(self) -> None:
        rows = ri.parse_ri_text("ri\tsample\n1.4\tglycerol 10%\n")
        self.assertEqual(rows, (ri.SolutionRi(name="glycerol 10%", ri=1.4),))

    def test_iso_dates_and_decimal_commas_are_read(self) -> None:
        (row,) = ri.parse_ri_text("Sample\tRI\tDate\nH2O\t1,33268\t2025-09-09\n")
        self.assertAlmostEqual(row.ri, 1.33268, places=9)
        self.assertEqual(row.measured_on, date(2025, 9, 9))

    def test_errors_name_the_line(self) -> None:
        cases = {
            "Name\tValue\nH2O\t1.33\n": (1, "no sample / ri column"),
            "Sample\tRI\nH2O 1.33\n": (2, "fewer tab-separated values"),
            "Sample\tRI\nH2O\tabc\n": (2, "is not a number"),
            "Sample\tRI\nH2O\t13.3268\n": (2, "outside"),
            "Sample\tRI\tDate\nH2O\t1.33\t9/9/2025\n": (2, "neither DD.MM.YYYY"),
            "Sample\tRI\nH2O\t1.33\n\nh2o\t1.34\n": (4, "listed twice (first on line 2)"),
            "Sample\tRI\n\n": (0, "no solutions"),
            "": (0, "empty"),
        }
        for text, (line, words) in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(ri.RiFileError) as caught:
                    ri.parse_ri_text(text)
                self.assertEqual(caught.exception.line, line)
                self.assertIn(words.casefold(), str(caught.exception).casefold())

    def test_a_file_from_a_windows_program_is_decoded(self) -> None:
        data = ("﻿" + LAB_FILE).encode("utf-8")
        self.assertEqual(ri.decode(data), LAB_FILE)
        self.assertEqual(ri.decode("Sample\tRI\nH2O \xb0\t1.33\n".encode("cp1252")), "Sample\tRI\nH2O \xb0\t1.33\n")


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = ri.table_from_bytes(LAB_FILE.encode(), "RIs.txt", wavelength_nm=None, now_utc=NOW)

    def test_a_steps_solution_is_the_first_name_of_its_note(self) -> None:
        self.assertEqual(ri.solution_of("20perEG_-_ch1_to_40perEG_-_ch3F_ch4P"), "20perEG")
        self.assertEqual(ri.solution_of("H2O_--_ch1F_ch2P"), "H2O")

    def test_names_match_ignoring_case_and_space_versus_underscore(self) -> None:
        self.assertEqual(self.table.find("0.1M_NaCl").name, "0.1M NaCl")
        self.assertEqual(self.table.find("h2o").name, "H2O")
        self.assertIsNone(self.table.find("H2O2"))

    def test_usage_gives_one_value_per_solution_for_all_its_steps(self) -> None:
        steps = [
            (1, "H2O_--_ch1F_ch2P"), (2, "20perEG_-_ch1_to_40perEG_-_ch3F_ch4P"), (3, "H2O_-_ch3_to_H2O_-_ch1F_ch2P"),
            (4, "PAH(f)_--_ch2F"), (5, "PSS(f)_--_ch3F"), (6, "0.1M_NaCl_--_ch1F"), (7, "PSS(f)_--_ch3F"),
        ]
        found = ri.usage(self.table, steps)
        self.assertEqual([(row.name, numbers) for row, numbers in found.used],
                         [("H2O", (1, 3)), ("20perEG", (2,)), ("PAH(f)", (4,)), ("0.1M NaCl", (6,))])
        self.assertEqual(found.missing, (("PSS(f)", (5, 7)),))
        self.assertEqual(found.unused, ("MeOH",))

    def test_without_a_table_every_solution_is_missing(self) -> None:
        found = ri.usage(None, [(1, "H2O_--_ch1F")])
        self.assertEqual((found.used, found.missing, found.unused), ((), (("H2O", (1,)),), ()))

    def test_age_in_days_to_the_experiment(self) -> None:
        self.assertEqual(ri.age_days(date(2025, 9, 9), date(2025, 9, 12)), 3)
        self.assertEqual(ri.age_days(date(2025, 9, 13), date(2025, 9, 12)), -1)  # measured after the experiment
        self.assertIsNone(ri.age_days(None, date(2025, 9, 12)))

    def test_step_numbers_are_written_with_runs(self) -> None:
        self.assertEqual(ri.step_numbers_text([9, 1, 3, 4, 5]), "1, 3-5, 9")
        self.assertEqual(ri.step_numbers_text([]), "")


class TableTests(unittest.TestCase):
    def test_the_payload_round_trips_every_field(self) -> None:
        table = ri.table_from_bytes(LAB_FILE.encode(), "RIs.txt", wavelength_nm=632.8, now_utc=NOW)
        self.assertEqual(ri.RiTable.from_payload(table.to_payload()), table)
        self.assertEqual(table.imported_at_utc, "2026-11-12T09:30:00Z")
        self.assertEqual(len(table.sha256), 64)
        self.assertIsNone(ri.RiTable.from_payload(None))
        with self.assertRaises(ValueError):
            ri.RiTable.from_payload([1, 2])

    def test_the_checksum_follows_the_bytes(self) -> None:
        first = ri.table_from_bytes(LAB_FILE.encode(), "RIs.txt", wavelength_nm=None, now_utc=NOW)
        edited = ri.table_from_bytes(LAB_FILE.replace("1.33268", "1.33270").encode(), "RIs.txt", wavelength_nm=None, now_utc=NOW)
        self.assertNotEqual(first.sha256, edited.sha256)

    def test_the_file_is_found_in_a_folder_ignoring_case(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(ri.find_ri_file(Path(folder)))
            (Path(folder) / "ris.TXT").write_text(LAB_FILE, encoding="utf-8")
            self.assertEqual(ri.find_ri_file(Path(folder)).name, "ris.TXT")
        self.assertIsNone(ri.find_ri_file(None))


# -- the working table: editing (2026-11-13) -----------------------------------------------------------------------------------------------

STEP_H2O = measured_ref(1, 1000, "H2O_--_ch1F")
STEP_EG = measured_ref(2, 2000, "20perEG_-_ch1")
STEP_PSS = measured_ref(3, 3000, "PSS(f)_--_ch3F")
STEPS = [(1, "H2O_--_ch1F", STEP_H2O), (2, "20perEG_-_ch1", STEP_EG), (3, "PSS(f)_--_ch3F", STEP_PSS), (4, "H2O_--_ch2F", added_ref(7))]


class EditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = ri.table_from_bytes(LAB_FILE.encode(), "RIs.txt", wavelength_nm=589.3, now_utc=NOW)

    def _edited(self, rows) -> ri.RiTable:
        return self.table.with_rows(rows)

    def test_with_no_edits_the_working_table_is_the_imported_one_and_nothing_extra_is_saved(self) -> None:
        self.assertIsNone(self.table.rows)
        self.assertEqual([row.name for row in self.table.working()], ["H2O", "MeOH", "20perEG", "PAH(f)", "0.1M NaCl"])
        self.assertFalse(self.table.has_edits())
        self.assertNotIn("rows", self.table.to_payload())  # a table nobody edited is written exactly as before (schema 1.8 shape)
        self.assertEqual(self.table.edit_summary(), "")
        self.assertIsNone(self._edited(self.table.imported_rows()).rows)  # typing the imported values back is no edit

    def test_an_edited_value_is_used_flagged_and_the_import_is_kept(self) -> None:
        rows = ri.edit_row(self.table.working(), 0, "ri", "1,33300")  # a decimal comma is read, as in the file
        table = self._edited(rows)
        self.assertEqual(table.find("H2O").ri, 1.333)
        self.assertTrue(table.find("H2O").edited)
        self.assertFalse(self.table.find("H2O").edited)
        self.assertEqual(table.solutions, self.table.solutions)  # the imported copy never changes
        self.assertEqual(table.sha256, self.table.sha256)
        self.assertEqual(table.changed_fields(table.working()[0]), ("ri",))
        self.assertEqual(table.status(table.working()[0]), "edited")
        self.assertEqual(table.status(table.working()[1]), "")
        self.assertIn("RI 1.33268", table.imported_text(table.working()[0]))
        self.assertEqual(table.edit_summary(), "1 row edited")

    def test_every_field_can_be_edited(self) -> None:
        rows = self.table.working()
        for field_name, text in (("name", "Water"), ("ri", "1.3330"), ("std", "0.00002"), ("temp_c", "21.5"), ("n", "7"), ("measured_on", "2025-09-11")):
            rows = ri.edit_row(rows, 0, field_name, text)
        row = rows[0]
        self.assertEqual((row.name, row.ri, row.std, row.temp_c, row.n, row.measured_on), ("Water", 1.333, 0.00002, 21.5, 7, date(2025, 9, 11)))
        cleared = ri.edit_row(rows, 0, "n", " ")  # empty = no value
        self.assertIsNone(cleared[0].n)

    def test_a_refused_edit_says_why_and_changes_nothing(self) -> None:
        rows = self.table.working()
        for field_name, text, fragment in (
            ("ri", "3.5", "outside 1.0-3.0"), ("ri", "abc", "not a number"), ("ri", "nan", "not a number"), ("name", "  ", "needs a name"),
            ("name", "meoh", "already in the table"), ("name", "0.1M_NaCl", "already in the table"), ("std", "-1", "negative"), ("n", "2.5", "whole number"),
            ("n", "-1", "whole number"), ("measured_on", "32.13.2025", "neither DD.MM.YYYY nor YYYY-MM-DD"), ("colour", "red", "not a column"),
        ):
            with self.subTest(field=field_name, text=text), self.assertRaises(ri.RiEditError) as raised:
                ri.edit_row(rows, 0, field_name, text)
            self.assertIn(fragment, str(raised.exception))
        with self.assertRaises(ri.RiEditError):
            ri.edit_row(rows, 99, "ri", "1.3")

    def test_a_row_without_an_ri_matches_nothing_until_it_has_one(self) -> None:
        table = self._edited(ri.edit_row(self.table.working(), 0, "ri", ""))
        self.assertIsNone(table.working()[0].ri)
        self.assertIsNone(table.find("H2O"))
        self.assertIsNone(table.find_for_step(STEP_H2O, "H2O_--_ch1F"))
        found = ri.usage(table, STEPS)
        self.assertIn(("H2O", (1, 4)), found.missing)  # its steps are reported as having no RI ...
        self.assertEqual(found.unlisted, (("PSS(f)", (3,)),))  # ... but, unlike PSS(f), it has a row
        self.assertNotIn("H2O", [row.name for row, _ in found.used])

    def test_renaming_a_row_changes_which_steps_it_serves(self) -> None:
        table = self._edited(ri.edit_row(self.table.working(), 0, "name", "20perEG_copy"))
        self.assertIsNone(table.find("H2O"))
        self.assertEqual(table.find("20perEG_copy").ri, 1.33268)

    def test_steps_can_be_given_to_a_row_by_hand_and_back(self) -> None:
        rows = ri.set_row_steps(self.table.working(), 1, [STEP_PSS], describe=str)  # MeOH serves step 3 (PSS) only
        table = self._edited(rows)
        self.assertEqual(table.find_for_step(STEP_PSS, "PSS(f)_--_ch3F").name, "MeOH")  # by hand, whatever its note says
        self.assertIsNone(table.find("MeOH"))  # a row given to steps by hand is no longer found by name
        self.assertTrue(table.find_for_step(STEP_PSS, "PSS(f)_--_ch3F").edited)  # the value is as imported, but its use was changed: flagged
        self.assertIn("steps", table.changed_fields(table.working()[1]))
        found = ri.usage(table, STEPS)
        self.assertEqual(found.row_steps[1], (3,))
        self.assertEqual(found.missing, ())  # PSS(f) has an RI now
        back = self._edited(ri.set_row_steps(table.working(), 1, None))
        self.assertIsNone(back.rows)  # by name again: as imported
        self.assertIsNone(back.find_for_step(STEP_PSS, "PSS(f)_--_ch3F"))

    def test_a_step_given_by_hand_to_two_rows_is_refused(self) -> None:
        rows = ri.set_row_steps(self.table.working(), 1, [STEP_PSS])
        with self.assertRaises(ri.RiEditError) as raised:
            ri.set_row_steps(rows, 3, [STEP_PSS, STEP_EG], describe=lambda ref: "step 3")
        self.assertIn("step 3 is already assigned by hand to 'MeOH'", str(raised.exception))

    def test_by_hand_comes_before_by_name(self) -> None:
        rows = ri.set_row_steps(self.table.working(), 2, [STEP_H2O])  # 20perEG takes step 1, which the note names H2O
        table = self._edited(rows)
        self.assertEqual(table.find_for_step(STEP_H2O, "H2O_--_ch1F").name, "20perEG")
        self.assertEqual(table.find_for_step(added_ref(7), "H2O_--_ch2F").name, "H2O")  # the other H2O step still finds its name

    def test_rows_are_added_removed_reverted_and_restored(self) -> None:
        rows, index = ri.add_row(self.table.working())
        self.assertEqual((index, len(rows)), (5, 6))
        self.assertEqual(ri.add_row(rows), (rows, 5))  # the blank one is reused
        rows = ri.edit_row(ri.edit_row(rows, 5, "name", "PSS(f)"), 5, "ri", "1.3345")
        table = self._edited(rows)
        self.assertEqual(table.status(table.working()[5]), "added")
        self.assertEqual(table.find("pss(f)").ri, 1.3345)
        self.assertTrue(table.find("PSS(f)").edited)
        self.assertEqual(ri.usage(table, STEPS).missing, ())
        removed = self._edited(ri.remove_row(table.working(), 0))  # H2O out
        self.assertEqual([row.name for row in removed.removed_rows()], ["H2O"])
        self.assertIsNone(removed.find("H2O"))
        self.assertEqual(removed.edit_counts(), (0, 1, 1))
        back = self._edited(ri.restore_row(removed.working(), "h2o", removed.imported_rows()))
        self.assertEqual(back.working()[0].name, "H2O")  # back in the file's place, not at the end
        self.assertEqual(back.removed_rows(), ())
        reverted = self._edited(ri.revert_row(back.working(), 5, back.imported_rows()))  # the added row has nothing to go back to: gone
        self.assertIsNone(reverted.rows)
        again = ri.edit_row(self.table.working(), 0, "ri", "1.34")
        self.assertEqual(ri.revert_row(again, 0, self.table.imported_rows()), self.table.imported_rows())
        clash = ri.add_row_with(ri.remove_row(self.table.working(), 0), "H2O", "1.3")
        with self.assertRaises(ri.RiEditError):
            ri.restore_row(clash, "h2o", self.table.imported_rows())

    def test_a_new_row_for_a_solution_with_no_row_is_one_call(self) -> None:
        rows = ri.add_row_with(self.table.working(), "PSS(f)", "1.3351")
        self.assertEqual((rows[-1].name, rows[-1].ri, rows[-1].base), ("PSS(f)", 1.3351, None))
        with self.assertRaises(ri.RiEditError):
            ri.add_row_with(self.table.working(), "H2O", "1.3")  # exists
        with self.assertRaises(ri.RiEditError):
            ri.add_row_with(self.table.working(), "X", "7")  # outside the range

    def test_step_numbers_are_read_the_way_they_are_written(self) -> None:
        self.assertEqual(ri.parse_step_numbers("1, 3-5, 9"), (1, 3, 4, 5, 9))
        self.assertEqual(ri.parse_step_numbers("9; 1  2–4"), (1, 2, 3, 4, 9))
        self.assertEqual(ri.parse_step_numbers(""), ())
        self.assertEqual(ri.parse_step_numbers(ri.step_numbers_text([9, 1, 3, 4, 5])), (1, 3, 4, 5, 9))
        for bad in ("a", "0", "5-3", "1.5", "1,,x"):
            with self.subTest(text=bad), self.assertRaises(ri.RiEditError):
                ri.parse_step_numbers(bad)

    def test_the_working_table_round_trips_through_the_session_payload(self) -> None:
        rows = ri.set_row_steps(ri.edit_row(self.table.working(), 0, "ri", "1.3330"), 1, [STEP_PSS, added_ref(7)])
        rows, index = ri.add_row(rows)
        rows = ri.edit_row(ri.edit_row(ri.edit_row(rows, index, "name", "New"), index, "ri", "1.4"), index, "measured_on", "01.02.2026")
        table = self._edited(ri.remove_row(rows, 3))
        payload = table.to_payload()
        self.assertEqual(len(payload["solutions"]), 5)  # the imported copy, untouched
        self.assertEqual(len(payload["rows"]), 5)
        self.assertEqual(ri.RiTable.from_payload(payload), table)
        self.assertEqual(ri.RiTable.from_payload(payload).working(), table.working())
        with self.assertRaises(ValueError):
            ri.RiTable.from_payload({**payload, "rows": "x"})

    def test_a_session_from_before_the_edits_loads_as_unedited(self) -> None:
        payload = self.table.to_payload()
        self.assertIsNone(ri.RiTable.from_payload(payload).rows)
        self.assertEqual(ri.RiTable.from_payload(payload).working(), self.table.imported_rows())


if __name__ == "__main__":
    unittest.main()
