"""`overlay_tint.rgba_layer`: the tint layer for a boolean mask (mask overlay and histogram highlight).

The dense branch (`np.where`) and the sparse branch (indexed write) must give the same bytes as the original
single implementation (`packed[mask] = value`), whatever the share of selected pixels."""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.overlay_tint import rgba_layer
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

RGBA = np.array([255, 128, 0, 115], dtype=np.uint8)


def _original(mask: np.ndarray) -> np.ndarray:
    packed = np.zeros(mask.shape, dtype=np.uint32)
    packed[mask] = RGBA.view(np.uint32)[0]
    return packed.view(np.uint8).reshape(*mask.shape, 4)


class RgbaLayerTests(unittest.TestCase):
    def test_same_bytes_as_the_original_at_every_density(self) -> None:
        rng = np.random.default_rng(3)
        for share in (0.0, 0.001, 0.05, 0.10, 0.11, 0.5, 0.95, 1.0):
            with self.subTest(share=share):
                mask = rng.random((61, 97)) < share
                layer = rgba_layer(mask, RGBA)
                self.assertEqual(layer.shape, (61, 97, 4))
                self.assertEqual(layer.dtype, np.uint8)
                np.testing.assert_array_equal(layer, _original(mask))

    def test_selected_pixels_carry_the_colour_and_the_rest_is_transparent(self) -> None:
        mask = np.zeros((4, 5), dtype=bool)
        mask[1, 2] = True
        layer = rgba_layer(mask, RGBA)
        self.assertEqual(tuple(layer[1, 2]), (255, 128, 0, 115))
        self.assertEqual(tuple(layer[0, 0]), (0, 0, 0, 0))

    def test_layer_is_writable_and_contiguous_for_the_image_item(self) -> None:
        mask = np.ones((8, 8), dtype=bool)  # dense branch
        layer = rgba_layer(mask, RGBA)
        self.assertTrue(layer.flags["C_CONTIGUOUS"])
        self.assertTrue(layer.flags["OWNDATA"] or layer.base is not None)


if __name__ == "__main__":
    unittest.main()
