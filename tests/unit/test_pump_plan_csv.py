"""Reader for the pump-plan CSV (`lspr_io.read_pump_plan_csv`). The two defects found in the 2026-10
bulk-sensitivity example file are reproduced here on purpose: a flow typed `.00E-02` (reads as 0) and a row with
a stray `;` that shifts every later column."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_io import is_pump_plan_csv, read_pump_plan_csv  # noqa: E402

HEADER = "FR1 [ml/min];Direction1;FR2 [ml/min];Direction2;FR3 [ml/min];Direction3;FR4 [ml/min];Direction4;Time;Valves;Notes"
ROW_1 = "4.00E-02;aclckw;2.00E-02;aclckw;;;;;00:05:00;V1cV2cV3cV4c;H2O_--_ch1F_ch2P"
ROW_2 = ";;4.00E-02;aclckw;2.00E-02;aclckw;;;00:04:10;V1oV2cV3cV4c;10perEG_--_ch2F_ch3P"


def _read(*lines: str, header: str = HEADER, encoding: str = "utf-8"):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.csv"
        path.write_bytes(("\r\n".join([header, *lines])).encode(encoding))
        return read_pump_plan_csv(path)


class ReadPumpPlanCsvTests(unittest.TestCase):
    def test_reads_steps_in_plan_time_with_flows_in_ul_per_min(self) -> None:
        result = _read(ROW_1, ROW_2)
        steps = result.plan.steps
        self.assertEqual([s.id for s in steps], [1, 2])
        self.assertEqual((steps[0].start_s, steps[0].end_s), (0.0, 300.0))
        self.assertEqual((steps[1].start_s, steps[1].end_s), (300.0, 550.0))
        self.assertEqual(steps[0].comment, "H2O_--_ch1F_ch2P")
        channels = steps[0].devices["channels"]
        self.assertEqual(
            [(c["channel"], c["flow_ul_min"], c["direction"]) for c in channels],
            [(1, 40.0, "CCW"), (2, 20.0, "CCW"), (3, 0.0, "OFF"), (4, 0.0, "OFF")],
        )
        self.assertEqual(result.plan.units["flow"], "uL/min")
        self.assertEqual(result.warnings, [])

    def test_valves_are_read_for_every_valve_and_v1_is_also_given_on_its_own(self) -> None:
        steps = _read(ROW_1, ROW_2).plan.steps
        self.assertEqual(steps[0].devices["valves"], {"V1": "Close", "V2": "Close", "V3": "Close", "V4": "Close"})
        self.assertEqual(steps[0].devices["valve"], "Close")
        self.assertEqual(steps[1].devices["valves"]["V1"], "Open")
        self.assertEqual(steps[1].devices["valve"], "Open")

    def test_microlitre_header_is_not_scaled(self) -> None:
        header = HEADER.replace("ml/min", "ul/min")
        steps = _read("40;clckw;;;;;;;00:00:10;V1cV2cV3cV4c;A", header=header).plan.steps
        self.assertEqual(steps[0].devices["channels"][0]["flow_ul_min"], 40.0)
        self.assertEqual(steps[0].devices["channels"][0]["direction"], "CW")

    def test_unrecognised_flow_unit_is_reported(self) -> None:
        header = HEADER.replace(" [ml/min]", "")
        result = _read(ROW_1, header=header)
        self.assertTrue(any("flow unit" in w for w in result.warnings))

    def test_duration_formats(self) -> None:
        durations = []
        for cell in ("00:05:00", "5:00", "300", "00:00:07.5"):
            durations.append(_read(f"4.00E-02;aclckw;;;;;;;{cell};V1cV2cV3cV4c;A").plan.steps[0].end_s)
        self.assertEqual(durations, [300.0, 300.0, 300.0, 7.5])

    def test_flow_typed_without_its_leading_digit_is_reported_not_silently_kept(self) -> None:
        # The real file has `.00E-02` where 2.00E-02 was meant: a valid number (0.0), so only the set direction gives it away.
        result = _read("2.00E-02;aclckw;;;;;.00E-02;aclckw;00:00:10;V1cV2cV3cV4c;A")
        self.assertEqual(result.plan.steps[0].devices["channels"][3]["flow_ul_min"], 0.0)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("channel 4", result.warnings[0])
        self.assertIn(".00E-02", result.warnings[0])

    def test_flow_without_a_direction_is_reported(self) -> None:
        result = _read("4.00E-02;;;;;;;;00:00:10;V1cV2cV3cV4c;A")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("no direction", result.warnings[0])

    def test_unknown_direction_is_reported(self) -> None:
        result = _read("4.00E-02;sideways;;;;;;;00:00:10;V1cV2cV3cV4c;A")
        self.assertTrue(any("unknown direction" in w for w in result.warnings))

    def test_a_stray_separator_before_time_is_realigned_and_reported(self) -> None:
        stray = "4.00E-02;aclckw;2.00E-02;aclckw;;;;;;00:01:00;V1cV2cV3cV4c;H2O_--_ch1F_ch2p"  # 12 cells, header has 11
        result = _read(ROW_1, stray)
        last = result.plan.steps[1]
        self.assertEqual(last.end_s - last.start_s, 60.0)
        self.assertEqual(last.devices["valves"]["V1"], "Close")
        self.assertEqual(last.comment, "H2O_--_ch1F_ch2p")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("Step 2", result.warnings[0])
        self.assertIn("realigned", result.warnings[0])

    def test_extra_cells_that_are_not_empty_stop_the_import(self) -> None:
        bad = "4.00E-02;aclckw;2.00E-02;aclckw;;;7;aclckw;x;00:01:00;V1cV2cV3cV4c;A"  # 12 cells, the extra one holds data
        with self.assertRaises(ValueError) as caught:
            _read(ROW_1, bad)
        self.assertIn("Step 2", str(caught.exception))

    def test_short_row_is_padded_and_reported(self) -> None:
        result = _read("4.00E-02;aclckw;2.00E-02;aclckw;;;;;00:00:10;V1cV2cV3cV4c")  # no Notes cell
        self.assertEqual(result.plan.steps[0].comment, "")
        self.assertTrue(any("missing cells" in w for w in result.warnings))

    def test_empty_duration_stops_the_import(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _read("4.00E-02;aclckw;;;;;;;;V1cV2cV3cV4c;A")
        self.assertIn("Time cell is empty", str(caught.exception))

    def test_unreadable_duration_and_flow_stop_the_import(self) -> None:
        with self.assertRaises(ValueError):
            _read("4.00E-02;aclckw;;;;;;;soon;V1cV2cV3cV4c;A")
        with self.assertRaises(ValueError) as caught:
            _read("lots;aclckw;;;;;;;00:00:10;V1cV2cV3cV4c;A")
        self.assertIn("channel 1", str(caught.exception))

    def test_blank_rows_are_skipped_and_steps_stay_consecutive(self) -> None:
        result = _read(ROW_1, ";;;;;;;;;;", "", ROW_2)
        self.assertEqual([s.id for s in result.plan.steps], [1, 2])

    def test_windows_1252_file_is_read_and_flagged(self) -> None:
        result = _read("4.00E-02;aclckw;;;;;;;00:00:10;V1cV2cV3cV4c;5 µM PAH", encoding="cp1252")
        self.assertEqual(result.plan.steps[0].comment, "5 µM PAH")
        self.assertTrue(any("Windows-1252" in w for w in result.warnings))

    def test_file_with_a_header_only_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            _read()


class IsPumpPlanCsvTests(unittest.TestCase):
    def test_recognises_the_plan_by_its_header_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plan = folder / "plan.csv"
            plan.write_text(f"{HEADER}\r\n{ROW_1}\r\n", encoding="utf-8")
            times = folder / "measureing_times.csv"
            times.write_text("Image;Measuring time [ms];Measuring time [s];Note pump plan\n", encoding="utf-8")
            other = folder / "RIs.txt"
            other.write_text("Sample\tRI\tStd\nH2O\t1.33268\t0\n", encoding="utf-8")
            empty = folder / "empty.csv"
            empty.write_text("", encoding="utf-8")
            self.assertTrue(is_pump_plan_csv(plan))
            self.assertFalse(is_pump_plan_csv(times))
            self.assertFalse(is_pump_plan_csv(other))
            self.assertFalse(is_pump_plan_csv(empty))
            self.assertFalse(is_pump_plan_csv(folder / "missing.csv"))


if __name__ == "__main__":
    unittest.main()
