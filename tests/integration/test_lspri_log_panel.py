"""Tests for the Log panel (Help -> Log Console) and its wiring into the window.

A real `QApplication` built in-process, never `.exec()`; the panel is driven by
direct method calls (the timer is pumped by `_poll()`), and the window is the
real `build_main_window()`.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import applog
    from lspr_imaging_app.applog.handlers import RingBufferHandler
    from lspr_imaging_app.panels.log import LogPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

LOG = logging.getLogger("lspr_test.logpanel")


class LogPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = logging.getLogger()
        self._old_level = self.root.level
        self.root.setLevel(logging.DEBUG)
        self.ring = RingBufferHandler(capacity=5000)
        self.root.addHandler(self.ring)
        self.store = UiStateStore()
        self.panel = LogPanel(self.ring, None, self.store)

    def tearDown(self) -> None:
        self.panel.close()
        self.root.removeHandler(self.ring)
        self.root.setLevel(self._old_level)

    def _rows(self) -> list[str]:
        return [entry.message for entry in self.panel.shown_entries()]

    def test_new_records_appear_after_a_poll(self) -> None:
        LOG.info("first")
        LOG.warning("second")
        self.panel._poll()
        self.assertEqual(self._rows()[-2:], ["first", "second"])

    def test_level_filter_and_text_filter(self) -> None:
        LOG.debug("plane read a")
        LOG.info("dataset loaded")
        LOG.error("plane failed")
        self.panel._poll()
        self.panel._level_combo.setCurrentIndex(self.panel._level_combo.findData(logging.WARNING))
        self.assertEqual(self._rows(), ["plane failed"])
        self.panel._level_combo.setCurrentIndex(0)
        self.panel._search.setText("plane -failed")
        self.assertEqual(self._rows(), ["plane read a"])
        self.panel._search.setText("")
        self.assertEqual(len(self._rows()), 3)

    def test_timed_only_and_event_search(self) -> None:
        LOG.info("plain")
        applog.emit_timing(LOG, "render.frame", 8.0, cube=1)
        self.panel._poll()
        self.panel._timed.setChecked(True)
        self.assertEqual([e.event for e in self.panel.shown_entries()], ["render.frame"])
        self.panel._timed.setChecked(False)
        self.panel._search.setText("render.frame")
        self.assertEqual(len(self._rows()), 1)

    def test_clear_only_empties_the_view_not_the_ring(self) -> None:
        LOG.info("before")
        self.panel._poll()
        self.panel.clear_view()
        self.assertEqual(self._rows(), [])
        LOG.info("after")
        self.panel._poll()
        self.assertEqual(self._rows(), ["after"])
        self.assertTrue(any(e.message == "before" for e in self.ring.snapshot()))

    def test_mark_adds_a_structured_marker_line(self) -> None:
        self.panel.add_mark()
        self.panel.add_mark()
        marks = [e for e in self.panel.shown_entries() if e.event == "ui.mark"]
        self.assertEqual([e.fields for e in marks], [{"n": 1}, {"n": 2}])

    def test_rows_are_capped_so_a_long_session_cannot_grow_the_view(self) -> None:
        from lspr_imaging_app.panels.log.table_model import LogTableModel

        self.panel._model = LogTableModel(max_rows=50, parent=self.panel)
        self.panel._table.setModel(self.panel._model)
        for number in range(120):
            LOG.info("m%d", number)
        self.panel._poll()
        self.assertEqual(self.panel._model.rowCount(), 50)
        self.assertEqual(self._rows()[-1], "m119")

    def test_export_writes_the_shown_entries_in_the_format_of_the_suffix(self) -> None:
        LOG.info("export me")
        applog.emit_timing(LOG, "x.y", 3.0)
        self.panel._poll()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            jsonl, csv_path, txt = (Path(tmp) / name for name in ("a.jsonl", "a.csv", "a.txt"))
            for path in (jsonl, csv_path, txt):
                self.assertGreaterEqual(self.panel.export_to(path), 2)
            self.assertIn('"msg":"export me"', jsonl.read_text(encoding="utf-8"))
            self.assertTrue(csv_path.read_text(encoding="utf-8").startswith("time,level,logger"))
            self.assertIn("export me", txt.read_text(encoding="utf-8"))

    def test_timings_tab_lists_events_and_double_click_filters_the_log(self) -> None:
        for ms in (5.0, 15.0):
            applog.emit_timing(LOG, "render.frame", ms)
        applog.emit_timing(LOG, "other.thing", 1.0)
        self.panel._tabs.setCurrentIndex(1)
        self.panel._refresh_timings()
        table = self.panel._timings
        names = {table.item(row, 0).text(): row for row in range(table.rowCount())}
        row = names["render.frame"]
        self.assertEqual(table.item(row, 1).text(), "2")
        self.assertEqual(table.item(row, 2).text(), "10.00")  # mean
        self.panel._on_timing_double_clicked(row, 0)
        self.assertEqual(self.panel._tabs.currentIndex(), 0)
        self.assertEqual(self.panel._search.text(), "render.frame")

    def test_selecting_an_entry_shows_its_fields_and_traceback(self) -> None:
        try:
            raise KeyError("missing")
        except KeyError:
            LOG.exception("lookup failed")
        applog.log_event(LOG, "dataset.load", logging.INFO, planes=12)
        self.panel._poll()
        rows = self.panel.shown_entries()
        self.panel._table.selectRow(len(rows) - 1)
        self.assertIn('"planes": 12', self.panel._detail.toPlainText())
        self.panel._table.selectRow(len(rows) - 2)
        self.assertIn("KeyError", self.panel._detail.toPlainText())

    def test_the_panel_refreshes_slowly_while_play_runs_and_catches_up_after(self) -> None:
        self.panel.show()
        self.assertEqual(self.panel._timer.interval(), 100)
        self.panel.set_busy(True)
        self.assertEqual(self.panel._timer.interval(), 1000)
        LOG.info("during play")
        self.panel.set_busy(False)  # Play stopped: normal speed and an immediate refresh
        self.assertEqual(self.panel._timer.interval(), 100)
        self.assertIn("during play", [e.message for e in self.panel.shown_entries()])
        self.panel.hide()

    def test_profile_button_profiles_the_gui_thread_and_logs_the_top_functions(self) -> None:
        import time

        def busy_slot() -> None:  # something recognisable for the profiler to find
            end = time.perf_counter() + 0.05
            while time.perf_counter() < end:
                sum(range(1000))

        self.panel._profile_button.click()  # the real signal path: must run for the default duration, not 0 s
        self.assertTrue(self.panel._profile.running)
        self.assertFalse(self.panel._profile_button.isEnabled())
        busy_slot()
        self.panel.finish_profile()  # what the timer calls
        self.assertTrue(self.panel._profile_button.isEnabled())
        entry = next(e for e in reversed(self.panel.shown_entries()) if e.event == "profile.gui")
        self.assertTrue(any("busy_slot" in row["function"] for row in entry.fields["top"]))
        self.assertIn("busy_slot", self.panel._detail.toPlainText())

    def test_filter_choices_are_remembered(self) -> None:
        self.panel._level_combo.setCurrentIndex(self.panel._level_combo.findData(logging.ERROR))
        self.panel._follow.setChecked(False)
        self.assertEqual(self.store.get("log/level"), logging.ERROR)
        self.assertIs(self.store.get("log/follow"), False)
        again = LogPanel(self.ring, None, self.store)
        self.assertEqual(again._level_combo.currentData(), logging.ERROR)
        self.assertFalse(again._follow.isChecked())
        again.close()


class LogConsoleWindowTest(unittest.TestCase):
    """The panel inside the real window: Help menu, shortcut, not a workspace panel."""

    def setUp(self) -> None:
        from lspr_imaging_app.app_rewrite import build_main_window

        self.window = build_main_window()

    def tearDown(self) -> None:
        self.window.close()

    def _menu(self, title: str):
        return next(a.menu() for a in self.window.menuBar().actions() if a.text() == title)

    def test_help_menu_has_the_log_console_toggle_with_a_shortcut(self) -> None:
        actions = {a.text(): a for a in self._menu("&Help").actions()}
        self.assertIn("Log Console", actions)
        self.assertEqual(actions["Log Console"].shortcut().toString(), "Ctrl+Shift+L")
        self.assertIn("Open Log Folder", actions)

    def test_log_dock_starts_hidden_and_floating_and_toggles(self) -> None:
        from lspr_imaging_app.panels.dock_container import PanelContainer

        dock = next(d for d in self.window.findChildren(PanelContainer) if d.objectName() == "LogConsolePanel")
        self.assertTrue(dock.isHidden())
        self.assertTrue(dock.isFloating())
        action = dock.toggleViewAction()
        action.trigger()
        self.assertFalse(dock.isHidden())
        action.trigger()
        self.assertTrue(dock.isHidden())

    def test_hiding_a_display_dock_switches_its_panel_off_and_showing_it_catches_up(self) -> None:
        from lspr_imaging_app.panels.histogram import HistogramPanel
        from lspr_imaging_app.panels.roi_table import RoiTablePanel

        self.window.show()
        _APP.processEvents()
        histogram = self.window.findChildren(HistogramPanel)[0]
        table = self.window.findChildren(RoiTablePanel)[0]
        for panel in (histogram, table):
            dock = panel.parent()
            while dock is not None and not hasattr(dock, "toggleViewAction"):
                dock = dock.parent()
            self.assertTrue(panel._gate.visible)
            dock.hide()
            _APP.processEvents()
            self.assertFalse(panel._gate.visible, f"{type(panel).__name__}: hidden dock -> panel off")
            dock.show()
            _APP.processEvents()
            self.assertTrue(panel._gate.visible)

    def test_log_is_not_listed_among_the_workspace_panels(self) -> None:
        panels_menu = next(a.menu() for a in self._menu("&View").actions() if a.text() == "Panels")
        self.assertNotIn("Log", [a.text() for a in panels_menu.actions()])
        self.assertNotIn("Log Console", [a.text() for a in panels_menu.actions()])


if __name__ == "__main__":
    unittest.main()
