"""The layout check before an export (`panels/image/export_preflight.py`, 2026-11-20): when it speaks, what it says, what each button returns.

The drive is a made-up `DriveInfo` (the real numbers of the maintainer's USB exFAT drive), never the machine's; the message box is a real
`QMessageBox` with `exec` and `clickedButton` replaced (nothing is shown, never `.exec()`). Rewrite-only; skipped when the
`apps/LSPRi/eva` submodule is not on its `rewrite` branch.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

    from lspr_imaging_app.dataset import export_disk_estimate as de
    from lspr_imaging_app.panels.image import export_preflight as ep
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])
GIB = 2**30
LED_V2 = dict(n_cubes=2655, n_wavelengths=27, height=879, width=1279, itemsize=2, chunk_h=80, chunk_w=80)


def drive(file_system: str = "exFAT", unit: int = 2 * 2**20, free_gib: int = 374) -> de.DriveInfo:
    return de.DriveInfo(cluster_bytes=unit, folder_bytes=1024 if file_system == "NTFS" else unit, free_bytes=free_gib * GIB, file_system=file_system)


def assess(info: de.DriveInfo | None, shard_mode: str = de.PER_IMAGE, compressed: bool = False, **over) -> ep.Preflight | None:
    with mock.patch.object(de, "drive_info", return_value=info):
        return ep.assess(Path("E:/Work"), shard_mode=shard_mode, compressed=compressed, **{**LED_V2, **over})


class AssessTests(unittest.TestCase):
    def test_the_real_case_speaks_and_offers_cube(self) -> None:
        pre = assess(drive())
        self.assertTrue(pre.needs_dialog)
        self.assertFalse(pre.current_fits)
        self.assertTrue(pre.slow)
        self.assertTrue(pre.cube_fits)
        self.assertTrue(pre.recommend_cube)
        self.assertEqual(pre.heading(), "This export will not fit on the drive, and its file layout is slow there.")
        details = pre.details()
        self.assertIn("exFAT, 2 MiB allocation unit, ", details)
        self.assertIn("71,685 files and 146,028 folders", details)
        self.assertIn("One file per cube: 2,655 files", details)
        self.assertIn("Every folder takes 2 MiB here", details)

    def test_compressed_says_the_size_is_an_estimate(self) -> None:
        self.assertIn("estimate", assess(drive(), compressed=True).details())
        self.assertNotIn("estimate", assess(drive(), compressed=False).details())

    def test_cube_already_chosen_and_fitting_stays_quiet(self) -> None:
        pre = assess(drive(), shard_mode=de.PER_CUBE)
        self.assertFalse(pre.needs_dialog)
        self.assertFalse(pre.recommend_cube)

    def test_a_roomy_ntfs_drive_stays_quiet_in_either_layout(self) -> None:
        for mode in (de.PER_IMAGE, de.PER_CUBE):
            self.assertFalse(assess(drive("NTFS", 4096, 2000), shard_mode=mode).needs_dialog, mode)

    def test_slow_but_fitting_asks_with_the_speed_heading(self) -> None:
        pre = assess(drive(free_gib=2000))
        self.assertTrue(pre.current_fits)
        self.assertTrue(pre.needs_dialog)
        self.assertEqual(pre.heading(), "This file layout is slow on this drive.")
        self.assertTrue(pre.recommend_cube)

    def test_nothing_fits_offers_no_cube_and_says_so(self) -> None:
        pre = assess(drive(free_gib=50))
        self.assertTrue(pre.needs_dialog)
        self.assertFalse(pre.cube_fits)
        self.assertFalse(pre.recommend_cube)
        self.assertIn("does not fit either", pre.details())

    def test_cube_chosen_but_too_big_still_speaks_without_an_offer(self) -> None:
        pre = assess(drive(free_gib=50), shard_mode=de.PER_CUBE)
        self.assertTrue(pre.needs_dialog)
        self.assertFalse(pre.recommend_cube)
        self.assertNotIn("One file per cube", pre.details())  # it is the layout as set

    def test_a_drive_that_cannot_be_asked_gives_no_check(self) -> None:
        self.assertIsNone(assess(None))


class AskTests(unittest.TestCase):
    """Which button returns what. `clickedButton` is replaced by "the button with this text"."""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.buttons: list[str] = []
        self.default: list[str] = []

    def ask(self, pre: ep.Preflight, click: str) -> str | None:
        def clicked(box: QMessageBox):
            self.buttons = [b.text() for b in box.buttons()]
            self.default = [box.defaultButton().text()] if box.defaultButton() else []
            return next((b for b in box.buttons() if b.text() == click), None)

        # Plain functions, not mocks: they bind as methods, so `box.exec()` / `box.clickedButton()` call them with the box.
        with mock.patch.object(QMessageBox, "exec", new=lambda box: 0), mock.patch.object(QMessageBox, "clickedButton", new=clicked):
            return ep.ask(self.parent, pre)

    def test_use_cube_returns_cube_and_is_the_default(self) -> None:
        pre = assess(drive())
        self.assertEqual(self.ask(pre, "Use Cube layout"), de.PER_CUBE)
        self.assertCountEqual(self.buttons, ["Use Cube layout", "Export anyway", "Cancel"])  # it does not fit: "anyway"; Qt orders them by platform
        self.assertEqual(self.default, ["Use Cube layout"])

    def test_export_anyway_keeps_the_layout_as_set(self) -> None:
        self.assertEqual(self.ask(assess(drive()), "Export anyway"), de.PER_IMAGE)

    def test_a_fitting_slow_layout_says_export_as_set(self) -> None:
        pre = assess(drive(free_gib=2000))
        self.assertEqual(self.ask(pre, "Export as set"), de.PER_IMAGE)
        self.assertIn("Export as set", self.buttons)

    def test_cancel_and_closing_the_box_return_none(self) -> None:
        pre = assess(drive())
        self.assertIsNone(self.ask(pre, "Cancel"))
        self.assertIsNone(self.ask(pre, "no such button"))  # the window closed: no button clicked

    def test_with_no_cube_offer_the_default_is_cancel_never_the_export_that_may_fill_the_drive(self) -> None:
        pre = assess(drive(free_gib=50))
        self.assertIsNone(self.ask(pre, "Cancel"))
        self.assertNotIn("Use Cube layout", self.buttons)
        self.assertEqual(self.default, ["Cancel"])


if __name__ == "__main__":
    unittest.main()
