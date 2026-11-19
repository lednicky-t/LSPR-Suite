"""`applog.StageTimer`: the stage timing of one unit of work, as one DEBUG record (2026-11-19).

No Qt, no files: records are caught with a plain handler. The cost claims (a mark is one clock reading, a disabled `emit` does nothing) are pinned by what the code does, not by a timing.
"""

from __future__ import annotations

import logging
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.applog import StageTimer
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class _Catch(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class StageTimerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("tests.stage_timer")
        self.logger.propagate = False
        self.catch = _Catch()
        self.logger.addHandler(self.catch)
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self) -> None:
        self.logger.removeHandler(self.catch)

    def test_stages_are_kept_in_the_order_they_were_first_marked_and_a_stage_marked_twice_adds_up(self) -> None:
        stages = StageTimer()
        stages.mark("a")
        stages.mark("b")
        stages.mark("a")
        self.assertEqual(list(stages.ms()), ["a", "b"])
        self.assertTrue(all(value >= 0.0 for value in stages.ms().values()))

    def test_add_puts_a_time_measured_elsewhere_in_a_stage(self) -> None:
        stages = StageTimer()
        stages.add("callee", 12.5)
        stages.add("callee", 2.5)
        self.assertEqual(stages.ms()["callee"], 15.0)

    def test_ms_is_a_copy(self) -> None:
        stages = StageTimer()
        stages.mark("a")
        stages.ms()["a"] = 1e9
        self.assertLess(stages.ms()["a"], 1e6)

    def test_emit_writes_one_debug_record_with_the_total_the_fields_and_every_stage(self) -> None:
        stages = StageTimer()
        stages.add("first", 1.234567)
        stages.mark("second")
        stages.emit(self.logger, "demo.unit", rows=7, reset=True)
        (record,) = self.catch.records
        self.assertEqual(record.levelno, logging.DEBUG)
        self.assertEqual(record.lspr_event, "demo.unit")
        fields = record.lspr_fields
        self.assertEqual((fields["rows"], fields["reset"]), (7, True))
        self.assertEqual(fields["first_ms"], 1.23, "milliseconds, two decimals")
        self.assertIn("second_ms", fields)
        self.assertGreaterEqual(record.lspr_dur_ms, fields["second_ms"])
        self.assertIn("demo.unit", record.getMessage())

    def test_extra_stages_of_another_timer_are_added_to_the_record(self) -> None:
        inner = StageTimer()
        inner.add("rows", 3.0)
        stages = StageTimer()
        stages.add("model", 4.0)
        stages.emit(self.logger, "demo.unit", extra={f"model_{k}": v for k, v in inner.ms().items()})
        (record,) = self.catch.records
        self.assertEqual((record.lspr_fields["model_ms"], record.lspr_fields["model_rows_ms"]), (4.0, 3.0))

    def test_emit_writes_nothing_when_the_logger_does_not_take_the_level(self) -> None:
        self.logger.setLevel(logging.INFO)
        stages = StageTimer()
        stages.mark("a")
        stages.emit(self.logger, "demo.unit", rows=1)
        self.assertEqual(self.catch.records, [])
        stages.emit(self.logger, "demo.unit", logging.INFO, rows=1)  # a level it does take
        self.assertEqual(len(self.catch.records), 1)


if __name__ == "__main__":
    unittest.main()
