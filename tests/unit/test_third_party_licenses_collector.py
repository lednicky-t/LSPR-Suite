"""`apps/suite_launcher/collect_third_party_licenses.py`: the licence texts that the portable bundle has to carry.

Pure file work (standard library only, no Qt): it reads the licence files that the installed packages brought with them."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from importlib import metadata
from pathlib import Path

from tests._paths import REPO_ROOT

SCRIPT = REPO_ROOT / "apps" / "suite_launcher" / "collect_third_party_licenses.py"


def _load():
    spec = importlib.util.spec_from_file_location("collect_third_party_licenses", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(SCRIPT.is_file(), "the Suite Launcher is not in this checkout")
class LicenceFileNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load()

    def test_licence_files_are_recognised_by_name_and_source_files_are_not(self) -> None:
        for name in (
            "LICENSE", "LICENCE", "license.txt", "COPYING", "NOTICE.md", "LICENSE-APACHE", "LICENSE_bundled.txt",
            "pkg-1.0.dist-info/licenses/MIT.txt",  # the folder says what the file is
            "pkg-1.0.dist-info/LICENSES/BSD-3-Clause.txt",
        ):
            with self.subTest(name=name):
                self.assertTrue(self.module._is_licence_file(Path(name)))
        for name in ("license.py", "licenses.py", "licensing.rst", "METADATA", "RECORD", "notice_utils.pyi", "README.md", "pkg/licenses/helper.py"):
            with self.subTest(name=name):
                self.assertFalse(self.module._is_licence_file(Path(name)))


@unittest.skipUnless(SCRIPT.is_file(), "the Suite Launcher is not in this checkout")
class CollectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Copying the licence files of every installed package takes a few seconds: do it once for all the tests below.
        cls.module = _load()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.destination = Path(cls._tmp.name) / "THIRD_PARTY_LICENSES"
        cls.rows = cls.module.collect(cls.destination)

    def test_the_gpl_text_of_pyqt6_and_an_index_are_collected(self) -> None:
        try:
            version = metadata.version("PyQt6")
        except metadata.PackageNotFoundError:
            self.skipTest("PyQt6 is not installed here")
        rows = self.rows
        folder = self.destination / f"PyQt6-{version}"
        self.assertTrue(folder.is_dir())
        texts = [path.read_text(encoding="utf-8", errors="replace") for path in folder.iterdir()]
        self.assertTrue(any("GNU GENERAL PUBLIC LICENSE" in text.upper() for text in texts), "the GPL text of PyQt6 is in its folder")
        index = (self.destination / "INDEX.txt").read_text(encoding="utf-8")
        self.assertIn("PyQt6", index)
        self.assertIn("GPL-3.0", index)
        self.assertTrue(any(name == "PyQt6" and copied >= 1 for name, _v, _l, copied in rows))

    def test_pyserial_gets_its_text_from_the_extra_folder(self) -> None:
        # Its wheel ships no licence file, but its BSD licence asks for the notice in a binary redistribution (the portable bundle).
        try:
            version = metadata.version("pyserial")
        except metadata.PackageNotFoundError:
            self.skipTest("pyserial is not installed here")
        folder = self.destination / f"pyserial-{version}"
        texts = [path.read_text(encoding="utf-8", errors="replace") for path in folder.iterdir()]
        self.assertTrue(any("Chris Liechti" in text for text in texts))

    def test_the_suites_own_packages_are_left_out(self) -> None:
        rows = self.rows
        self.assertFalse([name for name, *_ in rows if name.lower().startswith("lspr")])
        self.assertFalse([p.name for p in self.destination.iterdir() if p.name.lower().startswith("lspr")])

    def test_a_package_without_a_licence_file_is_flagged_in_the_index(self) -> None:
        rows = self.rows
        index = (self.destination / "INDEX.txt").read_text(encoding="utf-8")
        for name, _version, _label, copied in rows:
            if copied == 0:
                self.assertIn("NO LICENCE FILE FOUND", next(line for line in index.splitlines() if line.startswith(name)))


if __name__ == "__main__":
    unittest.main()
