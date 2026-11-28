"""`storage.ui_state_keys.read_file_stats` / `with_file_stats`: the remembered file count of each dataset folder. Pure, no Qt."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.storage.ui_state_keys import FILE_STATS, FILE_STATS_KEPT, read_file_stats, with_file_stats
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class FileStatsKeysTests(unittest.TestCase):
    def test_a_saved_count_is_read_back(self) -> None:
        store: dict = {}
        store[FILE_STATS] = with_file_stats(store, "D:/a", (1234, 9.5))
        self.assertEqual(read_file_stats(store, "D:/a"), (1234, 9.5))
        self.assertIsNone(read_file_stats(store, "D:/other"))

    def test_no_oldest_time_is_kept_as_none(self) -> None:
        store: dict = {}
        store[FILE_STATS] = with_file_stats(store, "D:/a", (10, None))
        self.assertEqual(read_file_stats(store, "D:/a"), (10, None))

    def test_the_newest_entries_are_kept_and_a_repeat_moves_to_the_end(self) -> None:
        store: dict = {}
        for index in range(FILE_STATS_KEPT + 3):
            store[FILE_STATS] = with_file_stats(store, f"D:/{index}", (index + 1, None))
        self.assertEqual(len(store[FILE_STATS]), FILE_STATS_KEPT)
        self.assertIsNone(read_file_stats(store, "D:/0"))
        self.assertIsNotNone(read_file_stats(store, f"D:/{FILE_STATS_KEPT + 2}"))
        store[FILE_STATS] = with_file_stats(store, "D:/5", (99, None))  # opened again: now the newest
        self.assertEqual(list(store[FILE_STATS])[-1], "D:/5")
        self.assertEqual(len(store[FILE_STATS]), FILE_STATS_KEPT)

    def test_damaged_values_read_as_nothing(self) -> None:
        for bad in (
            None, "text", [], {"D:/a": 5}, {"D:/a": {"bytes": "x", "oldest": None}},
            {"D:/a": {"bytes": 0, "oldest": None}}, {"D:/a": {"bytes": True, "oldest": None}},
            {"D:/a": {"bytes": 5, "oldest": "yesterday"}},
        ):
            self.assertIsNone(read_file_stats({FILE_STATS: bad}, "D:/a"), bad)

    def test_a_damaged_table_is_replaced_not_extended(self) -> None:
        self.assertEqual(list(with_file_stats({FILE_STATS: "garbage"}, "D:/a", (1, None))), ["D:/a"])


if __name__ == "__main__":
    unittest.main()
