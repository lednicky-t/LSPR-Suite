from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from suite_launcher.targets import AppTarget


class ResolveLocalVersionTests(unittest.TestCase):
    def test_reads_app_version_from_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version_file = root / "src" / "some_app" / "version.py"
            version_file.parent.mkdir(parents=True)
            version_file.write_text('APP_NAME = "Some App"\nAPP_VERSION = "1.2.3"\n', encoding="utf-8")

            target = AppTarget(
                key="some_app",
                title="Some App",
                subtitle="",
                address="",
                root_candidates=(root,),
                version_file="src/some_app/version.py",
            )
            self.assertEqual(target.resolve_local_version(), "1.2.3")

    def test_returns_none_when_version_file_is_unset(self) -> None:
        target = AppTarget(
            key="some_app",
            title="Some App",
            subtitle="",
            address="",
            root_candidates=(),
        )
        self.assertIsNone(target.resolve_local_version())

    def test_returns_none_when_root_cannot_be_resolved(self) -> None:
        target = AppTarget(
            key="some_app",
            title="Some App",
            subtitle="",
            address="",
            root_candidates=(Path("this/does/not/exist"),),
            version_file="src/some_app/version.py",
        )
        self.assertIsNone(target.resolve_local_version())

    def test_returns_none_when_version_file_is_missing_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = AppTarget(
                key="some_app",
                title="Some App",
                subtitle="",
                address="",
                root_candidates=(root,),
                version_file="src/some_app/version.py",
            )
            self.assertIsNone(target.resolve_local_version())


if __name__ == "__main__":
    unittest.main()
