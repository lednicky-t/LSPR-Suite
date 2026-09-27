"""Session index round trip for the LSPRimaging Evaluation rewrite.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

`storage/session_index.py` (2026-09-26) is the manifest of a dataset's
named sessions - which ones exist, and which is active. What matters most
here is the same class of "fails quietly" bug the sibling session-format
test (`test_lspri_rewrite_session.py`) pins:

- Two sessions created within the same second must not collide on id - a
  fast double-click on "New Session" is entirely realistic, and a silent
  collision would mean the second session's first save quietly overwrites
  the first one's folder.
- A missing index file is the ordinary "never used sessions" case, not an
  error - same contract `session.load_session`'s `None` already has.
- An index this build doesn't recognise (wrong schema, future major
  version) must raise rather than be silently treated as empty - guessing
  wrong here risks creating a second "first" session next to ones this
  build simply couldn't see.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.storage.session_index import (
        SESSION_INDEX_SCHEMA_NAME,
        SessionIndex,
        SessionRecord,
        create_session,
        index_path,
        load_session_index,
        save_session_index,
        session_dir_for,
        set_active_session,
        sessions_root,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


class SessionIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.home = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_dataset_with_no_sessions_yet_loads_as_empty(self) -> None:
        index = load_session_index(self.home)
        self.assertEqual(index.sessions, ())
        self.assertIsNone(index.active_session_id)

    def test_create_session_makes_it_active(self) -> None:
        record = create_session(self.home)
        index = load_session_index(self.home)
        self.assertEqual(index.sessions, (record,))
        self.assertEqual(index.active_session_id, record.session_id)

    def test_a_second_session_does_not_replace_the_first(self) -> None:
        first = create_session(self.home)
        second = create_session(self.home)
        index = load_session_index(self.home)
        self.assertEqual({r.session_id for r in index.sessions}, {first.session_id, second.session_id})
        self.assertEqual(index.active_session_id, second.session_id)

    def test_same_second_ids_are_deduplicated(self) -> None:
        """A fast double-click on "New Session" can easily land within the
        same wall-clock second - two ids must never collide."""
        fixed_now = mock.Mock()
        fixed_now.strftime.return_value = "2026-09-26_143012"
        with mock.patch("lspr_imaging_app.storage.session_index.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            fixed_now.isoformat.return_value = "2026-09-26T14:30:12+00:00"
            first = create_session(self.home)
            second = create_session(self.home)
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(first.session_id, "2026-09-26_143012")
        self.assertEqual(second.session_id, "2026-09-26_143012_2")

    def test_set_active_session_switches_the_pointer(self) -> None:
        first = create_session(self.home)
        second = create_session(self.home)
        set_active_session(self.home, first.session_id)
        self.assertEqual(load_session_index(self.home).active_session_id, first.session_id)
        self.assertEqual({r.session_id for r in load_session_index(self.home).sessions}, {first.session_id, second.session_id})

    def test_set_active_session_rejects_an_unknown_id(self) -> None:
        create_session(self.home)
        with self.assertRaises(ValueError):
            set_active_session(self.home, "does-not-exist")

    def test_session_dir_for_is_a_child_of_sessions_root(self) -> None:
        record = create_session(self.home)
        self.assertEqual(session_dir_for(self.home, record.session_id), sessions_root(self.home) / record.session_id)

    def test_round_trip_preserves_a_label(self) -> None:
        create_session(self.home, label="Baseline run")
        self.assertEqual(load_session_index(self.home).sessions[0].label, "Baseline run")

    def test_save_then_load_round_trips_exactly(self) -> None:
        index = SessionIndex(
            sessions=(SessionRecord("2026-09-26_090000", None, "2026-09-26T09:00:00+00:00"),),
            active_session_id="2026-09-26_090000",
        )
        save_session_index(self.home, index)
        self.assertEqual(load_session_index(self.home), index)

    def test_a_foreign_schema_name_is_rejected(self) -> None:
        path = index_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_name": "something_else", "schema_version": "1.0"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_session_index(self.home)

    def test_a_future_major_version_is_rejected(self) -> None:
        path = index_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"schema_name": SESSION_INDEX_SCHEMA_NAME, "schema_version": "99.0"}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            load_session_index(self.home)


if __name__ == "__main__":
    unittest.main()
