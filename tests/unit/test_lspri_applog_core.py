"""Tests for the LSPRi rewrite's logging core (`lspr_imaging_app.applog`).

No Qt widgets and no dataset: ring buffer, structured events, timings, the JSON
Lines pipeline (real files in a temp folder), hooks, filters, exports and the
command line summary. Design: `apps/LSPRi/eva/docs/logging_design_2026-10-14.md`.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import tempfile
import threading
import time
import unittest
import warnings
from contextlib import redirect_stdout
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import applog
    from lspr_imaging_app.applog import __main__ as applog_cli
    from lspr_imaging_app.applog import setup as applog_setup
    from lspr_imaging_app.applog.aggregate import TimingAggregator
    from lspr_imaging_app.applog.entry import LogEntry
    from lspr_imaging_app.applog.export import entries_to_csv, entries_to_jsonl, entries_to_text
    from lspr_imaging_app.applog.filters import EntryFilter
    from lspr_imaging_app.applog.handlers import RingBufferHandler
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

TEST_LOGGER = "lspr_test.applog"


class _RingFixture(unittest.TestCase):
    """A ring buffer attached to the root logger for one test (no file, no hooks)."""

    def setUp(self) -> None:
        self.root = logging.getLogger()
        self._old_level = self.root.level
        self.root.setLevel(logging.DEBUG)
        self.ring = RingBufferHandler(capacity=1000)
        self.root.addHandler(self.ring)
        self.log = logging.getLogger(TEST_LOGGER)

    def tearDown(self) -> None:
        self.root.removeHandler(self.ring)
        self.root.setLevel(self._old_level)


class EventsTest(_RingFixture):
    def test_a_plain_log_call_is_captured_without_structure(self) -> None:
        self.log.warning("disk %s is %d%% full", "C:", 93)
        entry = self.ring.snapshot()[-1]
        self.assertEqual((entry.level_name, entry.logger, entry.message), ("WARNING", TEST_LOGGER, "disk C: is 93% full"))
        self.assertIsNone(entry.event)
        self.assertIsNone(entry.dur_ms)

    def test_log_event_carries_name_and_fields(self) -> None:
        applog.log_event(self.log, "render.frame", logging.INFO, cube=2, wl=550.0, source="cache")
        entry = self.ring.snapshot()[-1]
        self.assertEqual(entry.event, "render.frame")
        self.assertEqual(entry.fields, {"cube": 2, "wl": 550.0, "source": "cache"})
        self.assertEqual(entry.message, "render.frame cube=2 wl=550 source=cache")

    def test_timed_measures_the_block_and_records_added_fields(self) -> None:
        with applog.timed(self.log, "dataset.load", folder="a") as t:
            time.sleep(0.02)
            t.set(planes=7)
        entry = self.ring.snapshot()[-1]
        self.assertEqual(entry.event, "dataset.load")
        self.assertGreaterEqual(entry.dur_ms, 15.0)
        self.assertLess(entry.dur_ms, 500.0)
        self.assertEqual(entry.fields, {"folder": "a", "planes": 7})
        self.assertIsNotNone(entry.span)

    def test_nested_timed_blocks_link_child_to_parent(self) -> None:
        with applog.timed(self.log, "outer"):
            with applog.timed(self.log, "inner"):
                pass
        inner, outer = self.ring.snapshot()[-2:]
        self.assertEqual((inner.event, outer.event), ("inner", "outer"))
        self.assertEqual(inner.parent, outer.span)
        self.assertIsNone(outer.parent)

    def test_timed_records_the_exception_type_and_does_not_swallow_it(self) -> None:
        with self.assertRaises(ValueError):
            with applog.timed(self.log, "boom"):
                raise ValueError("x")
        self.assertEqual(self.ring.snapshot()[-1].fields, {"error": "ValueError"})

    def test_disabled_levels_cost_nothing_and_record_nothing(self) -> None:
        self.root.setLevel(logging.INFO)
        before = self.ring.latest_seq()
        with applog.timed(self.log, "quiet", logging.DEBUG) as t:
            t.set(x=1)  # a no-op object, still usable
        applog.log_event(self.log, "quiet.event", logging.DEBUG, a=1)
        self.assertEqual(self.ring.latest_seq(), before)

    def test_timed_call_decorator(self) -> None:
        @applog.timed_call("unit.add")
        def add(a: int, b: int) -> int:
            return a + b

        self.assertEqual(add(1, 2), 3)
        self.assertEqual(self.ring.snapshot()[-1].event, "unit.add")

    def test_emit_timing_for_an_externally_measured_duration(self) -> None:
        applog.emit_timing(self.log, "render.frame", 12.5, cube=1)
        entry = self.ring.snapshot()[-1]
        self.assertEqual((entry.event, entry.dur_ms, entry.fields), ("render.frame", 12.5, {"cube": 1}))


class RingBufferTest(_RingFixture):
    def test_entries_since_returns_only_newer_entries_in_order(self) -> None:
        for number in range(5):
            self.log.info("m%d", number)
        seen = self.ring.latest_seq()
        self.log.info("m5")
        self.log.info("m6")
        self.assertEqual([e.message for e in self.ring.entries_since(seen)], ["m5", "m6"])
        self.assertEqual(self.ring.entries_since(self.ring.latest_seq()), [])

    def test_capacity_drops_the_oldest_and_sequence_keeps_counting(self) -> None:
        small = RingBufferHandler(capacity=3)
        self.root.addHandler(small)
        try:
            for number in range(10):
                self.log.info("m%d", number)
        finally:
            self.root.removeHandler(small)
        self.assertEqual([e.message for e in small.snapshot()], ["m7", "m8", "m9"])
        self.assertEqual(small.latest_seq(), 10)

    def test_timings_are_aggregated_per_event(self) -> None:
        for ms in (10.0, 20.0, 30.0):
            applog.emit_timing(self.log, "x.y", ms)
        rows = {row.event: row for row in self.ring.timing_rows()}
        self.assertEqual(rows["x.y"].count, 3)
        self.assertAlmostEqual(rows["x.y"].mean_ms, 20.0)
        self.ring.reset_timings()
        self.assertEqual(self.ring.timing_rows(), [])

    def test_many_threads_logging_at_once_lose_nothing(self) -> None:
        def work() -> None:
            for _ in range(200):
                self.log.info("t")

        threads = [threading.Thread(target=work) for _ in range(5)]
        before = self.ring.latest_seq()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.ring.latest_seq() - before, 1000)
        seqs = [e.seq for e in self.ring.snapshot()]
        self.assertEqual(seqs, sorted(seqs))

    def test_logging_overhead_is_small(self) -> None:
        started = time.perf_counter()
        for _ in range(2000):
            self.log.info("frame %d", 1)
        per_call_us = (time.perf_counter() - started) / 2000 * 1e6
        self.assertLess(per_call_us, 300.0, f"{per_call_us:.0f} us per record")  # typically 15-40 us


class AggregatorTest(unittest.TestCase):
    def test_percentiles_and_totals(self) -> None:
        aggregator = TimingAggregator()
        for ms in range(1, 101):
            aggregator.add("e", float(ms))
        aggregator.add("slow", 9000.0)
        rows = aggregator.rows()
        self.assertEqual(rows[0].event, "slow")  # biggest total first
        row = next(r for r in rows if r.event == "e")
        self.assertEqual((row.count, row.p50_ms, row.p95_ms, row.max_ms), (100, 50.0, 95.0, 100.0))
        self.assertAlmostEqual(row.total_ms, 5050.0)


def _entry(seq: int, level: int = logging.INFO, message: str = "m", logger: str = "a.b", **kw) -> LogEntry:
    return LogEntry(seq=seq, t=1000.0 + seq, mono=float(seq), level=level, logger=logger, message=message, thread="T", **kw)


class FilterAndExportTest(unittest.TestCase):
    def test_filter_by_level_text_exclusion_and_marker(self) -> None:
        entries = [
            _entry(1, logging.DEBUG, "plane read"),
            _entry(2, logging.INFO, "dataset loaded"),
            _entry(3, logging.ERROR, "plane failed", logger="x.io"),
            _entry(4, logging.INFO, "render frame", dur_ms=4.0, event="render.frame"),
        ]
        self.assertEqual([e.seq for e in EntryFilter(min_level=logging.INFO).apply(entries)], [2, 3, 4])
        self.assertEqual([e.seq for e in EntryFilter(text="plane").apply(entries)], [1, 3])
        self.assertEqual([e.seq for e in EntryFilter(text="plane -failed").apply(entries)], [1])
        self.assertEqual([e.seq for e in EntryFilter(text="render.frame").apply(entries)], [4])  # event name is searched
        self.assertEqual([e.seq for e in EntryFilter(timed_only=True).apply(entries)], [4])
        self.assertEqual([e.seq for e in EntryFilter(excluded_loggers=frozenset({"x."})).apply(entries)], [1, 2, 4])
        self.assertEqual([e.seq for e in EntryFilter(since_seq=3).apply(entries)], [4])

    def test_exports_round_trip_and_are_parseable(self) -> None:
        entries = [_entry(1, message="a, b", fields={"k": 1}, event="e.v", dur_ms=2.5), _entry(2, logging.ERROR, exc="Traceback...")]
        restored = [LogEntry.from_dict(json.loads(line), seq=i) for i, line in enumerate(entries_to_jsonl(entries).splitlines(), 1)]
        self.assertEqual((restored[0].event, restored[0].fields, restored[0].dur_ms), ("e.v", {"k": 1}, 2.5))
        self.assertEqual(restored[1].exc, "Traceback...")
        self.assertIn("Traceback...", entries_to_text(entries))
        csv_text = entries_to_csv(entries)
        self.assertTrue(csv_text.startswith("time,level,logger,event,dur_ms,message,thread,fields"))
        self.assertIn('"a, b"', csv_text)  # quoting survives a comma


class FilePipelineTest(unittest.TestCase):
    """configure_logging with real files (no hooks), then the CLI reads them back."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        self._old_level = logging.getLogger().level
        applog_setup._system = None

    def tearDown(self) -> None:
        system = applog.current()
        if system is not None:
            system.shutdown()
        logging.getLogger().setLevel(self._old_level)
        applog_setup._system = None
        self._tmp.cleanup()

    def test_records_events_and_exceptions_reach_the_file_with_a_header(self) -> None:
        system = applog.configure_logging(self.dir, app_version="9.9", install_hooks=False, console=False)
        log = logging.getLogger(TEST_LOGGER)
        applog.log_event(log, "unit.event", logging.INFO, n=3)
        with applog.timed(log, "unit.timed"):
            pass
        try:
            raise RuntimeError("kaput")
        except RuntimeError:
            log.exception("it failed")
        system.shutdown()  # drains the writer queue

        header, entries = applog_cli.read_entries(system.log_path)
        self.assertEqual((header["schema"], header["version"], header["session"]), ("lspri-log/1", "9.9", system.session_id))
        by_event = {e.event: e for e in entries if e.event}
        self.assertEqual(by_event["unit.event"].fields, {"n": 3})
        self.assertIsNotNone(by_event["unit.timed"].dur_ms)
        failed = next(e for e in entries if e.message == "it failed")
        self.assertIn("RuntimeError: kaput", failed.exc)
        self.assertTrue(all(e.session == system.session_id for e in entries))
        self.assertFalse(system.fault_path.exists() and system.fault_path.stat().st_size > 0)

    def test_configure_is_idempotent_and_capture_level_gates_records(self) -> None:
        first = applog.configure_logging(self.dir, install_hooks=False, console=False)
        self.assertIs(applog.configure_logging(self.dir, install_hooks=False, console=False), first)
        log = logging.getLogger(TEST_LOGGER)
        first.set_capture_level(logging.INFO)
        before = first.ring.latest_seq()
        log.debug("hidden")
        self.assertEqual(first.ring.latest_seq(), before)
        first.set_capture_level(logging.DEBUG)
        log.debug("shown")
        self.assertGreater(first.ring.latest_seq(), before)

    def test_old_sessions_are_cleaned_up(self) -> None:
        for number in range(applog_setup.KEEP_SESSIONS + 3):
            path = self.dir / f"lspri-old{number:02d}.jsonl"
            path.write_text("{}\n")
            (self.dir / f"lspri-old{number:02d}.fault.log").write_text("")
            stamp = time.time() - 1000 + number
            import os

            os.utime(path, (stamp, stamp))
        applog.configure_logging(self.dir, install_hooks=False, console=False)
        remaining = sorted(p.name for p in self.dir.glob("lspri-old*.jsonl"))
        self.assertEqual(len(remaining), applog_setup.KEEP_SESSIONS - 1)  # the new session makes it KEEP in total
        self.assertNotIn("lspri-old00.jsonl", remaining)
        self.assertFalse((self.dir / "lspri-old00.fault.log").exists())

    def test_hooks_log_unhandled_exceptions_from_threads_and_restore_on_shutdown(self) -> None:
        import faulthandler

        was_enabled = faulthandler.is_enabled()
        before_hook, before_thread_hook = sys.excepthook, threading.excepthook
        system = applog.configure_logging(self.dir, install_hooks=True, console=False)
        try:
            self.assertIsNot(sys.excepthook, before_hook)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # pytest's own thread-exception plugin also reports it
                thread = threading.Thread(target=lambda: 1 / 0, name="doomed")
                thread.start()
                thread.join()
            logging.getLogger("lspr_test.warn").warning("direct")
            problems = [e for e in system.ring.snapshot() if "doomed" in e.message]
            self.assertEqual(len(problems), 1)
            self.assertIn("ZeroDivisionError", problems[0].exc)
        finally:
            system.shutdown()
            if was_enabled:
                faulthandler.enable()
        self.assertIs(sys.excepthook, before_hook)
        self.assertIs(threading.excepthook, before_thread_hook)


class ConsoleOnListenerThreadTest(unittest.TestCase):
    """The console stream is written by the file pipeline's listener thread: the thread that logs only queues the record
    (testing12 profile: `StreamHandler.emit` was 8 % of the GUI thread while Play ran with the console on Debug)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        self._old_level = logging.getLogger().level
        applog_setup._system = None

    def tearDown(self) -> None:
        system = applog.current()
        if system is not None:
            system.shutdown()
        logging.getLogger().setLevel(self._old_level)
        applog_setup._system = None
        self._tmp.cleanup()

    def _configure(self, environ: dict[str, str]):
        import os
        from unittest import mock

        writers: list[str] = []

        class _Recording(io.StringIO):
            def write(self, text: str) -> int:
                writers.append(threading.current_thread().name)
                return super().write(text)

        stream = _Recording()
        with mock.patch.object(sys, "stderr", stream), mock.patch.dict(os.environ, environ):
            for name in ("LSPR_CONSOLE_LOG", "LSPR_CONSOLE_LOG_LEVEL"):
                if name not in environ:
                    os.environ.pop(name, None)
            system = applog.configure_logging(self.dir, install_hooks=False)
        return system, stream, writers

    def test_debug_console_is_written_by_the_listener_not_by_the_logging_thread(self) -> None:
        system, stream, writers = self._configure({"LSPR_CONSOLE_LOG": "1"})
        logging.getLogger(TEST_LOGGER).debug("hello console")
        system.shutdown()  # drains the queue
        self.assertIn("hello console", stream.getvalue())
        self.assertNotIn(threading.current_thread().name, writers)
        stream_handlers = [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler]
        self.assertEqual(stream_handlers, [], "the stream must not be attached to the root logger")

    def test_the_console_level_is_still_respected(self) -> None:
        system, stream, _writers = self._configure({"LSPR_CONSOLE_LOG_LEVEL": "WARNING"})
        log = logging.getLogger(TEST_LOGGER)
        log.debug("quiet debug")
        log.warning("loud warning")
        system.shutdown()
        self.assertIn("loud warning", stream.getvalue())
        self.assertNotIn("quiet debug", stream.getvalue())
        records = [json.loads(line) for line in system.log_path.read_text(encoding="utf-8").splitlines()[1:]]
        self.assertIn("quiet debug", [r["msg"] for r in records], "the file still gets everything")


class CommandLineTest(unittest.TestCase):
    def test_summary_and_grep_read_a_saved_file(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "lspri-x.jsonl"
            lines = [
                json.dumps({"schema": "lspri-log/1", "session": "S", "version": "1"}),
                json.dumps(_entry(1, logging.INFO, "hello").to_dict()),
                json.dumps(_entry(2, logging.ERROR, "bad thing").to_dict()),
                json.dumps(_entry(3, message="render.frame x=1", event="render.frame", dur_ms=12.0).to_dict()),
                "this line is not json",
                json.dumps(_entry(4, message="render.frame x=2", event="render.frame", dur_ms=20.0).to_dict()),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(applog_cli.main(["summary", str(path)]), 0)
            text = out.getvalue()
            self.assertIn("4 records", text)
            self.assertIn("bad thing", text)
            self.assertIn("render.frame", text)
            out = io.StringIO()
            with redirect_stdout(out):
                applog_cli.main(["grep", str(path), "--event", "render.frame", "--jsonl"])
            self.assertEqual(len(out.getvalue().strip().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
