"""The render thread's ready-made picture must be the picture pyqtgraph would have drawn
(`panels/image/display_picture.py`, `TimedImageItem.show_prepared`).

This is the tripwire for a pyqtgraph upgrade: the prepared picture copies pyqtgraph 0.14's automatic levels and
its 8-bit conversion, so it is compared here with `pg.ImageItem` itself, frame by frame: same levels, same alpha
everywhere, same grey value on every visible pixel (byte-identical; measured on 1.2 MP frames too).
"""

from __future__ import annotations

import gc
import sys
import time
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg
    from PyQt6 import QtWidgets
    from PyQt6.QtGui import QImage

    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.panels.image.display_picture import prepare_frame_display
    from lspr_imaging_app.panels.image.render import ImageRenderer, RenderRequest
    from lspr_imaging_app.panels.image.timed_canvas import TimedImageItem
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _frame(shape=(300, 420), seed=2, spread=300.0, centre=2000.0) -> np.ndarray:
    return np.random.default_rng(seed).normal(centre, spread, shape).astype(np.float32)


def _rgba_of(item: pg.ImageItem) -> np.ndarray:
    """What the item would paint: its QImage as (h, w, 4) R, G, B, A bytes."""
    item.render()
    q = item.qimage.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = q.constBits()
    ptr.setsize(q.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(q.height(), q.bytesPerLine() // 4, 4)[:, : q.width()].copy()


def _reference_item(frame: np.ndarray) -> pg.ImageItem:
    item = pg.ImageItem(axisOrder="row-major")
    item.setImage(frame, autoLevels=True)
    return item


def _prepared_item(frame: np.ndarray) -> TimedImageItem:
    display = prepare_frame_display(frame)
    item = TimedImageItem(axisOrder="row-major")
    item.show_prepared(frame, display.picture, display.levels)
    return item


class PreparedPictureMatchesPyqtgraphTest(unittest.TestCase):
    def _assert_same_picture(self, mine: np.ndarray, reference: np.ndarray, label: str) -> None:
        self.assertEqual(mine.shape, reference.shape, label)
        np.testing.assert_array_equal(mine[..., 3], reference[..., 3], err_msg=f"{label}: alpha (NaN pixels)")
        np.testing.assert_array_equal(mine, reference, err_msg=f"{label}: every byte, transparent pixels included")

    def _frames(self) -> dict[str, np.ndarray]:
        corners = _frame()
        corners[:30, :40] = np.nan
        corners[-40:, -25:] = np.nan
        wide = _frame(spread=3000.0, centre=20000.0)
        wide[:20, :] = np.nan
        hot = _frame()
        hot[:10, :10] = np.nan
        hot[3, 301] = 60000.0  # outside the strided level sample: clipped, as pyqtgraph clips it
        negative = _frame(centre=0.0)
        negative[0, :] = np.nan
        constant = np.full((60, 80), 1234.0, np.float32)
        constant[0, 0] = np.nan
        tiny = np.array([[np.nan, 1.0], [2.0, 3.0]], np.float32)
        return {"corners": corners, "wide range": wide, "hot pixel": hot, "negative values": negative, "constant": constant, "tiny": tiny}

    def test_picture_and_levels_equal_pyqtgraphs_own(self) -> None:
        for name, frame in self._frames().items():
            with self.subTest(name):
                reference = _reference_item(frame)
                prepared = _prepared_item(frame)
                np.testing.assert_allclose(np.asarray(prepared.levels, float), np.asarray(reference.levels, float), err_msg=f"{name}: levels")
                self._assert_same_picture(_rgba_of(prepared), _rgba_of(reference), name)

    def test_a_fixed_range_picture_equals_pyqtgraphs_nan_free_path_with_the_same_levels(self) -> None:
        """With a fixed Display range a frame with NaN pixels must get the same grey as any frame without NaN
        pixels (pyqtgraph's direct 8-bit path, 255 / range), so equal values look equal in every frame."""
        for name, frame in self._frames().items():
            for levels in ((500.0, 4000.0), (-300.0, 900.0)):
                with self.subTest(name, levels=levels):
                    display = prepare_frame_display(frame, levels)
                    item = TimedImageItem(axisOrder="row-major")
                    item.show_prepared(frame, display.picture, display.levels)
                    mine = _rgba_of(item)
                    finite = ~np.isnan(frame)
                    reference = pg.ImageItem(axisOrder="row-major")  # the same frame, NaN pixels filled in: the no-NaN path
                    reference.setImage(np.where(finite, frame, np.float32(0.0)), autoLevels=False, levels=np.asarray(levels, np.float32))
                    expected = _rgba_of(reference)
                    np.testing.assert_array_equal(mine[..., :3][finite], expected[..., :3][finite])
                    np.testing.assert_array_equal(mine[..., 3], np.where(finite, 255, 0))

    def test_the_item_still_holds_the_float_frame(self) -> None:
        """The cursor readout and the tests read measured values from `.image`; the picture is only for drawing."""
        frame = self._frames()["corners"]
        item = _prepared_item(frame)
        self.assertEqual(item.image.dtype, np.float32)
        np.testing.assert_array_equal(item.image, frame)  # NaN positions compare equal
        _rgba_of(item)  # drawing must not touch the values either
        np.testing.assert_array_equal(item.image, frame)

    def test_the_picture_survives_garbage_collection_of_everything_but_the_item(self) -> None:
        frame = self._frames()["corners"]
        reference = _rgba_of(_reference_item(frame))
        item = TimedImageItem(axisOrder="row-major")
        display = prepare_frame_display(frame)
        item.show_prepared(frame, display.picture, display.levels)
        del display
        gc.collect()
        self._assert_same_picture(_rgba_of(item), reference, "after gc")

    def test_anything_that_changes_the_look_drops_the_prepared_picture(self) -> None:
        frame = self._frames()["corners"]
        other = _frame(seed=9)
        other[:5, :5] = np.nan
        for name, change in {
            "new image": lambda item: item.setImage(other, autoLevels=True),
            "new levels": lambda item: item.setLevels((1500.0, 2500.0)),
            "colour map": lambda item: item.setLookupTable(np.stack([np.arange(256, dtype=np.uint8)] * 3, axis=-1)),
        }.items():
            with self.subTest(name):
                item = _prepared_item(frame)
                change(item)
                self.assertIsNone(item._prepared_qimage)
                reference = pg.ImageItem(axisOrder="row-major")
                reference.setImage(other if name == "new image" else frame, autoLevels=True)
                if name == "new levels":
                    reference.setLevels((1500.0, 2500.0))
                if name == "colour map":
                    reference.setLookupTable(np.stack([np.arange(256, dtype=np.uint8)] * 3, axis=-1))
                np.testing.assert_array_equal(_rgba_of(item)[..., 3], _rgba_of(reference)[..., 3])
                self.assertEqual(int(np.abs(_rgba_of(item).astype(int) - _rgba_of(reference).astype(int)).max()), 0)

    def test_automatic_downsampling_falls_back_to_pyqtgraphs_own_rendering(self) -> None:
        frame = self._frames()["corners"]
        item = _prepared_item(frame)
        item.setAutoDownsample(True)
        self.assertIsNone(item._prepared_qimage)


class RenderThreadTest(unittest.TestCase):
    """The real render thread: a rotated frame (NaN corners) arrives with its picture, an upright one without."""

    def _render(self, geometry: GeometrySettings):
        frame = _frame((120, 160))
        renderer = ImageRenderer(lambda cube, wavelength: frame)
        results = []
        renderer.rendered.connect(results.append)
        try:
            renderer.submit(
                RenderRequest(
                    cube_index=0, wavelength_nm=500.0, geometry=geometry, background=BackgroundSettings(),
                    authored_mask=None, mask_warp_affine=None, rois=(), detection=None, serial=1,
                )
            )
            deadline = time.monotonic() + 10.0
            while not results and time.monotonic() < deadline:
                _APP.processEvents()
                time.sleep(0.01)
        finally:
            renderer.stop()
        self.assertTrue(results, "the render thread produced nothing")
        return results[0]

    def test_a_rotated_frame_arrives_with_its_picture_and_exact_range(self) -> None:
        result = self._render(GeometrySettings(rotation_angle_deg=3.0))
        self.assertIsNone(result.error)
        self.assertTrue(np.isnan(result.image).any())
        self.assertIsNotNone(result.display.picture)
        finite = result.image[np.isfinite(result.image)]
        self.assertEqual(result.display.data_range, (float(finite.min()), float(finite.max())))
        np.testing.assert_array_equal(result.display.picture[..., 3], np.where(np.isnan(result.image), 0, 255))
        self.assertGreater(result.display_seconds, 0.0)

    def test_an_upright_frame_arrives_with_its_range_but_no_picture(self) -> None:
        result = self._render(GeometrySettings())
        self.assertFalse(np.isnan(result.image).any())
        self.assertIsNone(result.display.picture)
        self.assertEqual(result.display.data_range, (float(result.image.min()), float(result.image.max())))


if __name__ == "__main__":
    unittest.main()
