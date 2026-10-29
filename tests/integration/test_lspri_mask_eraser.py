"""The Mask tab's Eraser on a real `ImagePanel` (2026-10-29): a click on the image removes the blob of mask pixels under it.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.

The canvas is driven the way the other tool tests drive it: a stand-in for pyqtgraph's click event handed to
`ImagePanel._on_scene_clicked` at a view point (never screen coordinates). A real `QApplication` in-process, never `.exec()`.

What is pinned: the click removes exactly the connected blob (a corner contact counts) and nothing else; it follows the Persistent /
Individual toggle; it stays inside an area selection; it works on a rotated image (every raw pixel of the blob goes, the other blob is
untouched); it only acts while the Mask tab is open and the Eraser is the picked tool, and then the click does not also select a ROI;
a click on an unmasked pixel or with the right button changes nothing; the cursor says the tool is armed.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

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
        MaskEditTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.image_tools.mask_scope import MaskScope
    from lspr_imaging_app.image_tools.preprocess import resolve_external_mask
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402
from scipy import ndimage  # noqa: E402

FRAME = (0, 500.0)
SHAPE = (64, 80)


def _pump_until(condition, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _pump(seconds: float = 0.2) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


class _Click:
    """The slice of pyqtgraph's MouseClickEvent the panel reads."""

    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton) -> None:
        self._pos, self._button = scene_pos, button

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


def _write_dataset(root: Path) -> ImageDataset:
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), np.random.default_rng(1).uniform(100.0, 200.0, size=SHAPE).astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


def _blobs() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """A (6 x 8), B (6 x 8) far apart, and C: two pixels that touch at a corner only."""
    parts = {name: np.zeros(SHAPE, dtype=bool) for name in "ABC"}
    parts["A"][10:16, 10:18] = True
    parts["B"][30:36, 50:58] = True
    parts["C"][50, 10] = parts["C"][51, 11] = True
    return parts["A"] | parts["B"] | parts["C"], parts


class MaskEraserTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.scope = MaskScopeModule()
        self.selection = SelectionModule()
        self.geometry = GeometryModule()
        self.toolbox = RoiToolbox()
        self.panel = ImagePanel(
            self.dataset, self.geometry, self.mask, ChromaticModule(), BackgroundModule(), self.toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(), mask_scope=self.scope,
        )
        self.panel.resize(1100, 800)
        self.panel.show()  # the canvas needs a size for clicks to land on it (the tool tests do the same)
        self.tab = self.panel._mask_tab
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.base, self.parts = _blobs()

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        _pump_until(lambda: self.panel._image_item.image is not None and self.panel._image_item.levels is not None)
        _pump()

    def _arm(self) -> None:
        self.tab.edit_tool.set_tool(MaskEditTool.ERASER)
        self.panel._tool_ribbon.set_category("Mask")

    def _scene(self, x: float, y: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(x, y))

    def _click(self, x: float, y: float, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        self.panel._on_scene_clicked(_Click(self._scene(x, y), button))
        _pump(0.05)

    def _mask_now(self) -> np.ndarray:
        return self.mask.resolve_mask_source(FRAME)[1]

    def _start(self, mask: np.ndarray | None = None) -> None:
        self._load()
        self.mask.set_mask_change(FRAME, "persistent", self.base if mask is None else mask)
        self._arm()

    # -- the click -----------------------------------------------------------------------------------------------------

    def test_a_click_removes_exactly_the_blob_under_it(self) -> None:
        self._start()
        self._click(13.5, 12.5)  # the middle of A
        np.testing.assert_array_equal(self._mask_now(), self.base & ~self.parts["A"])
        self.assertTrue(self.messages[-1].startswith("Eraser: removed a grain of 48 px"))
        self._click(53.5, 33.5)  # B
        np.testing.assert_array_equal(self._mask_now(), self.parts["C"])

    def test_a_corner_contact_is_one_grain(self) -> None:
        self._start()
        self._click(10.5, 50.5)  # the first pixel of C: the diagonal one goes too
        np.testing.assert_array_equal(self._mask_now(), self.parts["A"] | self.parts["B"])
        self.assertIn("2 px", self.messages[-1])

    def test_a_click_beside_the_mask_changes_nothing_and_says_so(self) -> None:
        self._start()
        self._click(30.5, 20.5)  # empty
        np.testing.assert_array_equal(self._mask_now(), self.base)
        self.assertIn("no mask pixel", self.messages[-1])
        self._click(9.5, 12.5)  # one pixel left of A: not on it
        np.testing.assert_array_equal(self._mask_now(), self.base)

    def test_only_the_left_button_erases(self) -> None:
        self._start()
        self._click(13.5, 12.5, Qt.MouseButton.RightButton)
        self._click(13.5, 12.5, Qt.MouseButton.MiddleButton)
        np.testing.assert_array_equal(self._mask_now(), self.base)

    def test_with_no_mask_at_all_a_click_does_nothing(self) -> None:
        self._load()
        self._arm()
        self._click(13.5, 12.5)
        self.assertIsNone(self.mask.resolve_mask_source(FRAME))

    # -- the scope -----------------------------------------------------------------------------------------------------

    def test_it_follows_the_persistent_and_individual_toggle(self) -> None:
        self._start()
        self.scope.set_scope(MaskScope.PERSISTENT)
        self._click(13.5, 12.5)
        self.assertEqual(self.mask.resolve_mask_source(FRAME)[2], "persistent")
        np.testing.assert_array_equal(self._mask_now(), self.base & ~self.parts["A"])

    def test_an_individual_erase_is_authored_as_an_individual_mask(self) -> None:
        self._start()
        self.scope.set_scope(MaskScope.INDIVIDUAL)
        self._click(53.5, 33.5)
        _frame, mask, scope = self.mask.resolve_mask_source(FRAME)
        self.assertEqual(scope, "individual")
        np.testing.assert_array_equal(mask, self.base & ~self.parts["B"])

    # -- the area selection --------------------------------------------------------------------------------------------

    def test_an_area_selection_limits_the_erase(self) -> None:
        self._start()
        self.panel.area_selection().set_rectangle(0.0, 0.0, 14.0, 64.0)  # the left part of A only (columns 10-13)
        self._click(11.5, 12.5)
        inside = np.zeros(SHAPE, dtype=bool)
        inside[:, :14] = True
        np.testing.assert_array_equal(self._mask_now(), self.base & ~(self.parts["A"] & inside))
        self.assertEqual(int((self.base & ~self._mask_now()).sum()), 6 * 4)  # 24 px, not the whole grain
        self._click(53.5, 33.5)  # B lies outside the selection: a click there does nothing
        self.assertIn("outside the area selection", self.messages[-1])
        np.testing.assert_array_equal(self._mask_now() & self.parts["B"], self.parts["B"])

    # -- a rotated image -----------------------------------------------------------------------------------------------

    def test_on_a_rotated_image_every_raw_pixel_of_the_blob_goes_and_the_other_blob_stays(self) -> None:
        self.geometry.set_rotation(10.0)
        self._load()
        self.assertTrue(_pump_until(lambda: self.tab.grain_eraser._last_shape not in (None, SHAPE)))  # the rotated canvas is bigger
        self.mask.set_mask_change(FRAME, "persistent", self.base)
        self._arm()
        shown = resolve_external_mask(self.base, self.geometry.settings(), None)
        labels, count = ndimage.label(shown, structure=np.ones((3, 3), dtype=bool))
        self.assertGreaterEqual(count, 3)
        sizes = ndimage.sum(shown, labels, range(1, count + 1))
        biggest = [i + 1 for i in np.argsort(sizes)[::-1][:2]]  # A and B are the two big blobs
        left = min(biggest, key=lambda i: float(np.mean(np.nonzero(labels == i)[1])))  # A lies to the left of B
        rows, cols = np.nonzero(labels == left)
        middle = len(rows) // 2
        order = np.lexsort((cols, rows))
        row, col = int(rows[order][middle]), int(cols[order][middle])
        self._click(col + 0.5, row + 0.5)
        remaining = self._mask_now()
        self.assertFalse((remaining & self.parts["A"]).any(), "raw pixels of the clicked blob were left behind")
        np.testing.assert_array_equal(remaining & self.parts["B"], self.parts["B"])
        np.testing.assert_array_equal(remaining & self.parts["C"], self.parts["C"])

    # -- when it is armed ----------------------------------------------------------------------------------------------

    def test_nothing_happens_on_another_ribbon_tab_or_with_another_tool(self) -> None:
        self._start()
        self.panel._tool_ribbon.set_category("View")
        self._click(13.5, 12.5)
        np.testing.assert_array_equal(self._mask_now(), self.base)
        self.panel._tool_ribbon.set_category("Mask")
        self.tab.edit_tool.set_tool(MaskEditTool.MORPHOLOGY)
        self._click(13.5, 12.5)
        np.testing.assert_array_equal(self._mask_now(), self.base)
        self.tab.edit_tool.set_tool(MaskEditTool.ERASER)
        self._click(13.5, 12.5)
        self.assertFalse((self._mask_now() & self.parts["A"]).any())  # and it works again as soon as it is armed

    def test_the_click_does_not_also_select_the_roi_under_it(self) -> None:
        self._start()
        roi_id = self.toolbox.add_roi(13.5, 12.5, sample_diameter_px=8.0)
        self._click(13.5, 12.5)
        self.assertEqual(set(self.selection.selected_roi_ids()), set())  # the eraser took the click
        self.tab.edit_tool.set_tool(MaskEditTool.THRESHOLD)
        self._click(13.5, 12.5)
        self.assertEqual(set(self.selection.selected_roi_ids()), {roi_id})  # with another tool the ROI is selected as ever

    def test_the_cursor_is_a_cross_over_the_image_while_it_is_armed(self) -> None:
        self._start()
        viewport = self.panel._view.viewport()
        self.panel._interaction.on_scene_moved(self._scene(13.5, 12.5))
        self.assertEqual(viewport.cursor().shape(), Qt.CursorShape.CrossCursor)
        self.panel._tool_ribbon.set_category("View")
        self.panel._interaction.on_scene_moved(self._scene(13.5, 12.5))
        self.assertNotEqual(viewport.cursor().shape(), Qt.CursorShape.CrossCursor)

    # -- the tool in the picker ----------------------------------------------------------------------------------------

    def test_the_tool_is_in_the_picker_with_an_icon_and_is_remembered_by_name(self) -> None:
        picker = self.tab._edit_picker
        action = next(a for a in picker._menu.actions() if a.text() == "Eraser")
        self.assertFalse(action.icon().isNull())
        self.assertEqual(MaskEditTool("eraser"), MaskEditTool.ERASER)
        action.trigger()
        self.assertIs(self.tab.edit_tool.tool(), MaskEditTool.ERASER)
        self.assertIs(self.tab._edit_stack.currentWidget(), self.tab._eraser_panel)
        self.assertFalse(picker.icon().isNull())


if __name__ == "__main__":
    unittest.main()
