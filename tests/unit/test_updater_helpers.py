from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import updater_main


def _write_fake_release_zip(zip_path: Path, marker_text: str) -> None:
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("LSPR Suite Launcher/LSPR Suite Launcher.exe", marker_text)
        archive.writestr("Updater.exe", "not part of the swap")


class ExtractUpdateTests(unittest.TestCase):
    def test_extract_update_returns_the_launcher_folder(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            zip_path = root / "update.zip"
            _write_fake_release_zip(zip_path, "new build")
            extract_dir = root / "extracted"

            launcher_folder = updater_main.extract_update(zip_path, extract_dir)

            self.assertEqual(launcher_folder.name, "LSPR Suite Launcher")
            self.assertTrue((launcher_folder / "LSPR Suite Launcher.exe").exists())

    def test_extract_update_raises_when_folder_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            zip_path = root / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("SomethingElse/file.txt", "x")
            extract_dir = root / "extracted"

            with self.assertRaises(LookupError):
                updater_main.extract_update(zip_path, extract_dir)


class SwapInPlaceTests(unittest.TestCase):
    def test_swap_in_place_replaces_contents_and_keeps_target_path(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            target = root / "LSPR Suite Launcher"
            target.mkdir()
            (target / "old_marker.txt").write_text("old version")

            new_folder = root / "staged" / "LSPR Suite Launcher"
            new_folder.mkdir(parents=True)
            (new_folder / "new_marker.txt").write_text("new version")

            updater_main.swap_in_place(new_folder, target)

            self.assertTrue((target / "new_marker.txt").exists())
            self.assertFalse((target / "old_marker.txt").exists())
            self.assertFalse(target.with_name(target.name + ".old").exists())
            self.assertFalse(new_folder.exists())

    def test_swap_in_place_works_when_target_does_not_exist_yet(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            target = root / "LSPR Suite Launcher"
            new_folder = root / "staged" / "LSPR Suite Launcher"
            new_folder.mkdir(parents=True)
            (new_folder / "new_marker.txt").write_text("new version")

            updater_main.swap_in_place(new_folder, target)

            self.assertTrue((target / "new_marker.txt").exists())


if __name__ == "__main__":
    unittest.main()
