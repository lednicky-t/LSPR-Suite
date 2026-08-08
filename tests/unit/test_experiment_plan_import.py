"""Tests for the experiment-plan CSV/TXT import/export format helpers
(lspr_acq_shell.experiment_control_import).

Moved from apps/sLSPR/acq/tests/ (Phase 2, LSPRi acq experiment-control
reuse - Tier 0 extraction, 2026-08-09) - tests the real owner now that this
module lives in the shared package, matching the "test the real owner"
convention from every Phase 1 extraction (see
docs/architecture/general/lspri_acq_build_log.md).

Covers:
- A regression test for the bug where a successful plain-text (CSV/TSV/TXT)
  import never emitted ``signals.finished``, leaving the import spinner
  running forever with nothing loaded.
- Parsing of the "FR<n>"/"Direction<n>"/packed-valve pump-plan format used
  by some external plan editors, alongside this app's native
  "Ch-<n> Flow"/"Ch-<n> Direction"/L-R-valve format.
- The inverse (export-side) helpers for that same external format, and
  that they round-trip through the import-side parsers.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_acq_shell.experiment_control_import import (
    ExperimentPlanImportTask,
    _experiment_plan_normalize_valve,
    _format_duration_as_clock,
    _normalize_pump_direction,
    _pack_valve_state_token,
    _parse_time_to_seconds,
    _pump_direction_to_external_token,
)

_NATIVE_FORMAT_CSV = (
    "Step;Ch-1 Flow [ml/min];Ch-1 Direction;Ch-2 Flow [ml/min];Ch-2 Direction;"
    "Time;Valve;Color;Descritption\n"
    "1;0.02;CW;0.01;CW;300;R;#AEAAAA;_CB_--_1F_2P\n"
    "2;0.01;CW;0.02;CW;900;L;#9BC2E6;Cap_--_1p_2F\n"
)

_EXTERNAL_FORMAT_CSV = (
    "FR1 [ml/min];Direction1;FR2 [ml/min];Direction2;FR3 [ml/min];Direction3;"
    "FR4 [ml/min];Direction4;Time;Valves;Notes\n"
    "2.00E-02;aclckw;1.00E-02;aclckw;;;;;00:05:00;V1oV2cV3cV4c;Plastic buffer\n"
    "1.00E-02;aclckw;2.00E-02;aclckw;;;;;00:07:30;V1cV2cV3cV4c;Plastic\n"
)


def _run_import_task(text: str) -> tuple[bool, object]:
    """Run an ExperimentPlanImportTask synchronously against *text* and
    return ``(finished_fired, payload_or_message)``."""
    with TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "plan.csv"
        path.write_text(text, encoding="utf-8")
        task = ExperimentPlanImportTask(1, path)
        result: dict[str, object] = {}
        task.signals.finished.connect(lambda gen, payload: result.update(ok=True, payload=payload))
        task.signals.failed.connect(lambda gen, message: result.update(ok=False, payload=message))
        task.run()
        if not result:
            return False, None
        return True, (result["payload"] if result["ok"] else RuntimeError(result["payload"]))


class ExperimentPlanImportSignalTest(unittest.TestCase):
    """Regression test: a successful CSV/TXT import must emit `finished`."""

    def test_native_format_emits_finished(self) -> None:
        fired, payload = _run_import_task(_NATIVE_FORMAT_CSV)
        self.assertTrue(fired, "ExperimentPlanImportTask.run() completed without emitting a signal")
        self.assertNotIsInstance(payload, Exception)
        self.assertEqual(len(payload.steps), 2)

    def test_external_format_emits_finished(self) -> None:
        fired, payload = _run_import_task(_EXTERNAL_FORMAT_CSV)
        self.assertTrue(fired, "ExperimentPlanImportTask.run() completed without emitting a signal")
        self.assertNotIsInstance(payload, Exception)
        self.assertEqual(len(payload.steps), 2)


class ExternalPumpPlanFormatTest(unittest.TestCase):
    """Column detection and value parsing for the FR<n>/Direction<n> format."""

    def test_flow_and_direction_columns(self) -> None:
        _, payload = _run_import_task(_EXTERNAL_FORMAT_CSV)
        step = payload.steps[0]
        self.assertEqual(step.channels[0].flow_ul_min, 20)
        self.assertEqual(step.channels[0].direction, "CCW")
        self.assertEqual(step.channels[1].flow_ul_min, 10)
        self.assertEqual(step.channels[1].direction, "CCW")

    def test_hh_mm_ss_time_converted_to_seconds(self) -> None:
        _, payload = _run_import_task(_EXTERNAL_FORMAT_CSV)
        self.assertEqual(payload.steps[0].duration_s, 300.0)
        self.assertEqual(payload.steps[1].duration_s, 450.0)

    def test_packed_valve_string_reads_v1_only(self) -> None:
        _, payload = _run_import_task(_EXTERNAL_FORMAT_CSV)
        self.assertEqual(payload.steps[0].valve, "Open")   # V1o...
        self.assertEqual(payload.steps[1].valve, "Close")  # V1c...

    def test_description_from_notes_column(self) -> None:
        _, payload = _run_import_task(_EXTERNAL_FORMAT_CSV)
        self.assertEqual(payload.steps[0].description, "Plastic buffer")


class ParseTimeToSecondsTest(unittest.TestCase):
    def test_plain_seconds(self) -> None:
        self.assertEqual(_parse_time_to_seconds("300"), 300.0)

    def test_hh_mm_ss(self) -> None:
        self.assertEqual(_parse_time_to_seconds("00:05:00"), 300.0)
        self.assertEqual(_parse_time_to_seconds("00:07:30"), 450.0)

    def test_mm_ss(self) -> None:
        self.assertEqual(_parse_time_to_seconds("05:00"), 300.0)

    def test_blank_returns_default(self) -> None:
        self.assertEqual(_parse_time_to_seconds("", default=1.0), 1.0)

    def test_garbage_returns_default(self) -> None:
        self.assertEqual(_parse_time_to_seconds("not-a-time", default=2.0), 2.0)


class NormalizePumpDirectionTest(unittest.TestCase):
    def test_cw_variants(self) -> None:
        self.assertEqual(_normalize_pump_direction("CW"), "CW")
        self.assertEqual(_normalize_pump_direction(""), "CW")
        self.assertEqual(_normalize_pump_direction("garbage"), "CW")

    def test_ccw_variants(self) -> None:
        self.assertEqual(_normalize_pump_direction("CCW"), "CCW")
        self.assertEqual(_normalize_pump_direction("aclckw"), "CCW")
        self.assertEqual(_normalize_pump_direction("ACLCKW"), "CCW")

    def test_tolerates_aclcwk_misspelling(self) -> None:
        # Not the canonical spelling this app writes on export, but seen in
        # user-typed context - tolerate it as CCW rather than misreading it
        # as CW.
        self.assertEqual(_normalize_pump_direction("aclcwk"), "CCW")


class NormalizeValveTest(unittest.TestCase):
    def test_open_close_tokens(self) -> None:
        self.assertEqual(_experiment_plan_normalize_valve("Open", l_is_open=True), "Open")
        self.assertEqual(_experiment_plan_normalize_valve("Close", l_is_open=True), "Close")

    def test_l_r_tokens_respect_l_is_open(self) -> None:
        self.assertEqual(_experiment_plan_normalize_valve("L", l_is_open=True), "Open")
        self.assertEqual(_experiment_plan_normalize_valve("R", l_is_open=True), "Close")
        self.assertEqual(_experiment_plan_normalize_valve("L", l_is_open=False), "Close")
        self.assertEqual(_experiment_plan_normalize_valve("R", l_is_open=False), "Open")

    def test_packed_valve_string_reads_v1(self) -> None:
        self.assertEqual(_experiment_plan_normalize_valve("V1oV2cV3cV4c", l_is_open=True), "Open")
        self.assertEqual(_experiment_plan_normalize_valve("V1cV2cV3cV4c", l_is_open=True), "Close")
        # l_is_open must not affect the packed format - "o"/"c" are explicit.
        self.assertEqual(_experiment_plan_normalize_valve("V1oV2cV3cV4c", l_is_open=False), "Open")


class FormatDurationAsClockTest(unittest.TestCase):
    def test_matches_sample_file_values(self) -> None:
        self.assertEqual(_format_duration_as_clock(300), "00:05:00")
        self.assertEqual(_format_duration_as_clock(450), "00:07:30")
        self.assertEqual(_format_duration_as_clock(900), "00:15:00")

    def test_zero_and_negative_clamp_to_zero(self) -> None:
        self.assertEqual(_format_duration_as_clock(0), "00:00:00")
        self.assertEqual(_format_duration_as_clock(-5), "00:00:00")

    def test_hours_roll_over(self) -> None:
        self.assertEqual(_format_duration_as_clock(3661), "01:01:01")


class PumpDirectionToExternalTokenTest(unittest.TestCase):
    def test_cw_and_ccw(self) -> None:
        self.assertEqual(_pump_direction_to_external_token("CW"), "clckw")
        self.assertEqual(_pump_direction_to_external_token("CCW"), "aclckw")

    def test_off_and_blank_default_to_clockwise_token(self) -> None:
        self.assertEqual(_pump_direction_to_external_token("OFF"), "clckw")
        self.assertEqual(_pump_direction_to_external_token(""), "clckw")


class PackValveStateTokenTest(unittest.TestCase):
    def test_open_and_close(self) -> None:
        self.assertEqual(_pack_valve_state_token("Open"), "V1oV2cV3cV4c")
        self.assertEqual(_pack_valve_state_token("Close"), "V1cV2cV3cV4c")

    def test_v2_through_v4_always_closed(self) -> None:
        # This app only drives V1 - V2-V4 must always come out closed
        # regardless of the requested V1 state.
        for valve in ("Open", "Close"):
            token = _pack_valve_state_token(valve)
            self.assertTrue(token.endswith("V2cV3cV4c"))


class ExternalFormatRoundTripTest(unittest.TestCase):
    """Export-side helpers must produce tokens the import-side parsers
    read back to the exact same value - otherwise a plan exported by this
    app and re-imported later (or opened in the external pump-plan editor
    and re-imported) would silently drift.
    """

    def test_direction_round_trip(self) -> None:
        for direction in ("CW", "CCW"):
            token = _pump_direction_to_external_token(direction)
            self.assertEqual(_normalize_pump_direction(token), direction)

    def test_duration_round_trip(self) -> None:
        for seconds in (0.0, 5.0, 300.0, 450.0, 3661.0):
            clock = _format_duration_as_clock(seconds)
            self.assertEqual(_parse_time_to_seconds(clock), seconds)

    def test_valve_round_trip(self) -> None:
        for valve in ("Open", "Close"):
            token = _pack_valve_state_token(valve)
            self.assertEqual(_experiment_plan_normalize_valve(token, l_is_open=True), valve)


if __name__ == "__main__":
    unittest.main()
