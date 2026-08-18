"""Regression test: write_json_file (storage/workspace.py) - which backs the
debounced session autosave of ROIs/masks/chromatic models/analysis cache -
must write atomically. Before this fix it wrote directly via
Path.write_text(), so a crash or power loss mid-write had a real chance of
leaving a truncated/corrupt JSON file with no recovery path; a reader would
then either fail to parse the session or silently load a partial one.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.storage.workspace import write_json_file  # noqa: E402


class TestWriteJsonFileAtomic(unittest.TestCase):
    def test_round_trips_normally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            write_json_file(path, {"a": 1, "b": [1, 2, 3]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1, "b": [1, 2, 3]})

    def test_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "session.json"
            write_json_file(path, {"ok": True})
            self.assertTrue(path.exists())

    def test_no_leftover_temp_file_after_a_successful_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            write_json_file(path, {"a": 1})
            leftovers = [p for p in Path(tmp).iterdir() if p != path]
            self.assertEqual(leftovers, [])

    def test_a_failed_write_leaves_the_previous_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            write_json_file(path, {"version": 1})
            original_bytes = path.read_bytes()

            # A payload that can't be JSON-serialized fails inside
            # json.dumps(), before any file I/O happens - the simplest way
            # to prove the previous file survives a failed write attempt
            # untouched (an atomic writer never touches the real path until
            # the new content is fully written).
            with self.assertRaises(TypeError):
                write_json_file(path, {"version": 2, "bad": object()})

            self.assertEqual(path.read_bytes(), original_bytes, "the previous file must survive a failed write")
            leftovers = [p for p in Path(tmp).iterdir() if p != path]
            self.assertEqual(leftovers, [], "a failed write must not leave a stray temp file behind")

    def test_temp_file_is_cleaned_up_if_the_replace_step_itself_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            write_json_file(path, {"version": 1})
            original_bytes = path.read_bytes()

            import os
            import lspr_imaging_app.storage.workspace as workspace_module

            real_replace = os.replace

            def _failing_replace(*args, **kwargs):
                raise OSError("simulated crash during rename")

            workspace_module.os.replace = _failing_replace
            try:
                with self.assertRaises(OSError):
                    write_json_file(path, {"version": 2})
            finally:
                workspace_module.os.replace = real_replace

            self.assertEqual(path.read_bytes(), original_bytes, "the previous file must survive a failed replace")
            leftovers = [p for p in Path(tmp).iterdir() if p != path]
            self.assertEqual(leftovers, [], "a failed replace must not leave a stray temp file behind")


if __name__ == "__main__":
    unittest.main()
