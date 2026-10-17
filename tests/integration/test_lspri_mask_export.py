"""The "Export all masks" button of the Mask tab's PNG group and its task (2026-10-17).

A real Image panel over a small dataset; the folder dialog and the replace-prompt are replaced by answers. What is worth pinning: the
button is only live when there are masks, the files land named per frame with an index, an existing file asks before it is replaced,
and the export runs off the GUI thread. Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.image_tools.mask.export import INDEX_FILE_NAME
    from lspr_imaging_app.panels.image import ImagePanel, mask_file_actions
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_SHAPE = (64, 80)
_WAVELENGTHS = (500.0, 550.0, 600.0)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(5)
    for cube in range(4):
        for wavelength in _WAVELENGTHS:
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=_SHAPE).astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _blob(x: int) -> np.ndarray:
    plane = np.zeros(_SHAPE, dtype=bool)
    plane[10:20, x : x + 8] = True
    return plane


class MaskExportUiTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), analysis_running=lambda: False,
        )
        self.panel.resize(1000, 700)
        self.panel.show()
        (self.root / "data").mkdir()
        self.dataset.load_dataset(_write_dataset(self.root / "data"))
        _pump()
        self.actions = self.panel._mask_tab._png_actions
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.out = self.root / "export"

    def tearDown(self) -> None:
        self.panel._mask_export.shutdown()
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def draw(self) -> None:
        self.mask.set_mask_change((1, 500.0), "persistent", _blob(5))
        self.mask.set_mask_change((3, 550.0), "individual", _blob(30))
        _pump(0.1)

    def export(self, answer: str | None = None) -> None:
        self.out.mkdir(exist_ok=True)
        with mock.patch.object(mask_file_actions.QFileDialog, "getExistingDirectory", return_value=str(self.out)):
            if answer is None:
                self.actions._export_button.click()
            else:
                reply = (
                    QtWidgets.QMessageBox.StandardButton.Yes if answer == "yes" else QtWidgets.QMessageBox.StandardButton.No
                )
                with mock.patch.object(mask_file_actions.QMessageBox, "question", return_value=reply) as asked:
                    self.actions._export_button.click()
                    self.asked = asked
        deadline = time.monotonic() + 20.0
        while self.panel._mask_export.is_running() and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.005)
        _pump(0.2)

    def test_the_button_is_live_only_when_there_are_masks(self) -> None:
        self.assertFalse(self.actions._export_button.isEnabled())
        self.draw()
        self.assertTrue(self.actions._export_button.isEnabled())
        self.mask.clear_all_masks()
        self.assertFalse(self.actions._export_button.isEnabled())

    def test_every_mask_lands_as_a_named_png_with_an_index(self) -> None:
        self.draw()
        self.export()
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, ["mask_cube1_wl500_persistent.png", "mask_cube3_wl550_individual.png", INDEX_FILE_NAME])
        self.assertIn("Exported 2 masks", self.messages[-1])
        self.assertIn(INDEX_FILE_NAME, self.messages[-1])

    def test_a_folder_with_files_of_those_names_asks_before_replacing_them(self) -> None:
        self.draw()
        self.out.mkdir()
        (self.out / "mask_cube1_wl500_persistent.png").write_bytes(b"old")
        self.export(answer="no")
        self.assertEqual((self.out / "mask_cube1_wl500_persistent.png").read_bytes(), b"old")
        self.assertEqual(self.asked.call_count, 1)
        self.export(answer="yes")
        self.assertNotEqual((self.out / "mask_cube1_wl500_persistent.png").read_bytes(), b"old")

    def test_the_export_runs_off_the_gui_thread(self) -> None:
        self.draw()
        threads: set[int] = set()
        from lspr_imaging_app.image_tools.mask import export as export_module

        real = export_module.write_mask_image

        def spying(mask, path):
            threads.add(threading.get_ident())
            return real(mask, path)

        with mock.patch.object(export_module, "write_mask_image", side_effect=spying):
            self.export()
        self.assertTrue(threads)
        self.assertNotIn(threading.get_ident(), threads)

    def test_the_button_is_locked_while_an_export_runs(self) -> None:
        self.draw()
        with mock.patch.object(self.panel._mask_export, "is_running", return_value=True):
            self.panel._mask_export.running_changed.emit(True)
            self.assertFalse(self.actions._export_button.isEnabled())
        self.panel._mask_export.running_changed.emit(False)
        self.assertTrue(self.actions._export_button.isEnabled())

    def test_a_cancelled_dialog_does_nothing(self) -> None:
        self.draw()
        with mock.patch.object(mask_file_actions.QFileDialog, "getExistingDirectory", return_value=""):
            self.actions._export_button.click()
        self.assertFalse(self.panel._mask_export.is_running())
        self.assertEqual(list(self.root.glob("export*")), [])


if __name__ == "__main__":
    unittest.main()
