"""Regression test for undo/redo desync bugs in mouse-drag interactions.

Two related bugs, both in image_interaction_controller.py's mouse-event
handling:

1. Dragging the crop rectangle to *move* it (right-click drag, not a resize
   via handle) called `_prepare_undo_snapshot("Crop")` on mouse-press but
   never called `_commit_prepared_undo_snapshot()` on release. Since
   UndoManager.prepare() is a no-op while a snapshot is already pending, the
   stale "Crop" snapshot silently blocked the *next* unrelated prepare/commit
   pair (e.g. a mask edit) from capturing the correct before-state, so a
   later Ctrl+Z could restore the wrong point in history.

2. Dragging a rectangle/stamp ROI array to move it never prepared or
   committed an undo snapshot at all - such a move could not be undone.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import RoiDefinition  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402


@contextmanager
def _open_window(folder: Path):
    """Matches test_lspri_metadata_gui.py's MainWindow construction/teardown
    convention: dataset is cleared before close() so the closeEvent's
    processing-state save is a no-op instead of racing the temp dir cleanup."""
    window = MainWindow(folder, fast_startup=True)
    try:
        yield window
    finally:
        window._state.dataset = None
        window.close()
        window.deleteLater()


class TestCropDragCommitsUndoSnapshot(unittest.TestCase):
    def test_crop_move_is_committed_and_does_not_block_the_next_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                window._state.preprocessing.crop.x = 5
                window._state.preprocessing.crop.y = 5

                # Mirrors the right-click-drag press handler in
                # image_interaction_controller.py.
                window._prepare_undo_snapshot("Crop")
                self.assertIsNotNone(window._prepared_undo_snapshot)

                # Mirrors the drag itself moving the crop rect.
                window._state.preprocessing.crop.x = 40
                window._state.preprocessing.crop.y = 40

                # Mirrors the release handler (now including the fix's added
                # _commit_prepared_undo_snapshot() call).
                window._handle_image_tool_settings_changed("Crop moved.", preserve_view=True)
                window._commit_prepared_undo_snapshot()

                self.assertIsNone(
                    window._prepared_undo_snapshot,
                    "commit must clear the pending snapshot",
                )
                self.assertTrue(window._undo_stack, "the crop move should be on the undo stack")
                self.assertEqual(window._undo_stack[-1].label, "Crop")

                # The next, unrelated prepare() must not be silently dropped
                # by a still-pending snapshot from the crop drag.
                window._prepare_undo_snapshot("Edit mask")
                self.assertIsNotNone(window._prepared_undo_snapshot)
                self.assertEqual(window._prepared_undo_snapshot.label, "Edit mask")
                window._commit_prepared_undo_snapshot()

                # And undo actually restores the pre-drag crop position.
                window._undo()
                self.assertEqual(window._state.preprocessing.crop.x, 5)
                self.assertEqual(window._state.preprocessing.crop.y, 5)


class TestRectangleRoiDragCommitsUndoSnapshot(unittest.TestCase):
    def test_rectangle_roi_move_is_undoable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                roi = RoiDefinition(roi_id="stamp-1", name="Stamp 1", center_x=10.0, center_y=10.0)
                window._state.rois = [roi]
                window._selected_rectangle_roi_ids = {"stamp-1"}

                # Mirrors the right-click-drag press handler for rectangle
                # ROI arrays (now including the fix's added
                # _prepare_undo_snapshot() call).
                window._prepare_undo_snapshot("Move ROIs")
                self.assertIsNotNone(window._prepared_undo_snapshot)

                roi.center_x = 55.0
                roi.center_y = 55.0

                # Mirrors the release handler (now including the fix's added
                # _commit_prepared_undo_snapshot() call).
                window._commit_prepared_undo_snapshot()

                self.assertIsNone(window._prepared_undo_snapshot)
                self.assertTrue(window._undo_stack, "the ROI move should be on the undo stack")
                self.assertEqual(window._undo_stack[-1].label, "Move ROIs")

                window._undo()
                restored = window._state.rois[0]
                self.assertEqual(restored.center_x, 10.0)
                self.assertEqual(restored.center_y, 10.0)


if __name__ == "__main__":
    unittest.main()
