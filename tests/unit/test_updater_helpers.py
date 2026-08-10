from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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


class RenameWithRetriesTests(unittest.TestCase):
    def test_retries_past_a_transient_lock_then_succeeds(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            src = root / "src"
            src.mkdir()
            dst = root / "dst"

            attempts = {"count": 0}
            original_rename = Path.rename

            def _flaky_rename(self_path, target):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise OSError(32, "The process cannot access the file because it is being used by another process")
                return original_rename(self_path, target)

            with mock.patch.object(Path, "rename", _flaky_rename), mock.patch.object(updater_main.time, "sleep"):
                updater_main._rename_with_retries(src, dst, attempts=5, delay_s=0.01)

            self.assertTrue(dst.exists())
            self.assertFalse(src.exists())
            self.assertEqual(attempts["count"], 3)

    def test_raises_after_exhausting_all_attempts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            src = root / "src"
            src.mkdir()
            dst = root / "dst"

            def _always_locked(self_path, target):
                raise OSError(32, "The process cannot access the file because it is being used by another process")

            with mock.patch.object(Path, "rename", _always_locked), mock.patch.object(
                updater_main.time, "sleep"
            ) as sleep_mock:
                with self.assertRaises(OSError):
                    updater_main._rename_with_retries(src, dst, attempts=4, delay_s=0.01)
            self.assertEqual(sleep_mock.call_count, 4)


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


class MainStandaloneLaunchTests(unittest.TestCase):
    def test_running_without_required_args_shows_a_friendly_message(self) -> None:
        # Someone double-clicking Updater.exe by hand, rather than the Suite
        # Launcher spawning it with its required flags, used to just vanish
        # silently (argparse's usage/error text goes to sys.stderr, which is
        # None in this --windowed build). This should explain what happened
        # instead.
        with mock.patch("sys.argv", ["Updater.exe"]), mock.patch.object(
            updater_main, "_show_error"
        ) as show_error_mock:
            with self.assertRaises(SystemExit):
                updater_main.main()
        show_error_mock.assert_called_once()
        self.assertIn("isn't meant to be started by hand", show_error_mock.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
