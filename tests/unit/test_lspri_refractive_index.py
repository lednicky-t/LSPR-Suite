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


if __name__ == "__main__":
    unittest.main()
