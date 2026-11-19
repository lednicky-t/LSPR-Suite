"""The ROI / Group table's stage timing records (2026-11-19, `docs/roi_table_performance_2026-11-19.md`, "Where to look in the log"):

* ``roi_table.rebuild``: one record per rebuild, with where the time went;
* ``roi_table.fit_step_columns``: one record per fit of the step columns' widths;
* ``roi_table.slow_paint``: only for a paint over the threshold.

All DEBUG, one record per unit of work (never per ROI or cell), and nothing at all when DEBUG is off.
"""

from __future__ import annotations

import logging
import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.roi_table import view as table_view
    from lspr_imaging_app.panels.roi_table.model import StepCells
    from lspr_imaging_app.selection.step_table_module import CellValue, StepColumn

    from tests.integration.test_lspri_rewrite_roi_table_panel import _PanelCase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

PANEL = "lspr_imaging_app.panels.roi_table.panel"
VIEW = "lspr_imaging_app.panels.roi_table.view"
REBUILD_STAGES = {"model_ms", "view_ms", "select_ms", "toolbar_ms", "columns_ms", "fit_ms"}
MODEL_STAGES = {"model_rows_ms", "model_decimals_ms", "model_tree_ms", "model_apply_ms"}


class _Cells(StepCells):
    def roi_cell(self, roi_id, ref):
        return CellValue(n=10, mean=590.0 + roi_id * 0.01 + ref[1], sd=0.05, current=True, sem=0.015)

    def group_cell(self, key, members, ref):
        return CellValue(n=10, mean=590.0, sd=0.05, current=True, sem=0.015)


def _events(logs, name: str) -> list:
    return [r for r in logs.records if getattr(r, "lspr_event", None) == name]


class RebuildRecordTest(_PanelCase):
    def test_a_rebuild_writes_one_record_with_the_stages_and_what_kind_of_rebuild_it_was(self) -> None:
        self.toolbox.add_roi(5.0, 40.0)
        with self.assertLogs(PANEL, level="DEBUG") as logs:
            self.refresh()
        (record,) = _events(logs, "roi_table.rebuild")
        fields = record.lspr_fields
        self.assertEqual(fields["rows"], 6)
        self.assertEqual(fields["step_columns"], 0)
        self.assertTrue(fields["reset"], "a new row: the model was reset")
        self.assertTrue(REBUILD_STAGES | MODEL_STAGES <= set(fields), sorted(set(fields)))
        self.assertGreaterEqual(record.lspr_dur_ms, max(fields[name] for name in REBUILD_STAGES))

    def test_a_values_only_rebuild_says_it_was_not_a_reset(self) -> None:
        self.refresh()
        self.toolbox.place_rois([1], x=77.0)
        with self.assertLogs(PANEL, level="DEBUG") as logs:
            self.refresh()
        (record,) = _events(logs, "roi_table.rebuild")
        self.assertFalse(record.lspr_fields["reset"])

    def test_step_columns_are_counted_and_the_decimals_stage_is_there(self) -> None:
        self.model.set_step_columns([StepColumn((1, i), i + 1, f"step {i + 1}", "", (), "#58a6ff") for i in range(3)], _Cells())
        with self.assertLogs(PANEL, level="DEBUG") as logs:
            self.refresh()
        (record,) = _events(logs, "roi_table.rebuild")
        self.assertEqual(record.lspr_fields["step_columns"], 3)
        self.assertGreater(record.lspr_fields["model_decimals_ms"], 0.0, "with step columns the common number of decimals is worked out")

    def test_nothing_is_logged_when_debug_is_off(self) -> None:
        logger = logging.getLogger(PANEL)
        old = logger.level
        logger.setLevel(logging.INFO)
        handler = logging.Handler(logging.DEBUG)
        records: list = []
        handler.emit = records.append  # type: ignore[method-assign]
        logger.addHandler(handler)
        try:
            self.refresh()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old)
        self.assertEqual(records, [])


class FitAndPaintRecordTest(_PanelCase):
    def setUp(self) -> None:
        super().setUp()
        self.model.set_step_columns([StepColumn((1, i), i + 1, f"step {i + 1}", "", (), "#58a6ff") for i in range(3)], _Cells())
        self.refresh()

    def test_fitting_the_step_columns_writes_one_record_whatever_the_number_of_columns(self) -> None:
        with self.assertLogs(VIEW, level="DEBUG") as logs:
            self.tree.fit_step_columns()
        (record,) = _events(logs, "roi_table.fit_step_columns")
        fields = record.lspr_fields
        self.assertEqual((fields["columns"], fields["rows"]), (3, 5))
        self.assertTrue({"title_ms", "format_ms", "measure_ms", "set_ms"} <= set(fields), sorted(fields))

    def test_a_paint_is_logged_only_when_it_is_slow(self) -> None:
        self.tree.resize(400, 300)
        self.tree.show()
        _APP.processEvents()
        original = table_view._SLOW_PAINT_MS
        try:
            table_view._SLOW_PAINT_MS = 1e9  # nothing is that slow
            with self.assertNoLogs(VIEW, level="DEBUG"):
                self.tree.grab()
            table_view._SLOW_PAINT_MS = -1.0  # everything is
            with self.assertLogs(VIEW, level="DEBUG") as logs:
                self.tree.grab()
        finally:
            table_view._SLOW_PAINT_MS = original
            self.tree.hide()
        records = _events(logs, "roi_table.slow_paint")
        self.assertTrue(records)
        self.assertTrue({"ms", "width", "height"} <= set(records[0].lspr_fields))


if __name__ == "__main__":
    unittest.main()
