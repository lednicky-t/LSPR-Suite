from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.processing.reference_selection import bimodal_dip_contrast


def _bimodal_image(background: float, spot_value: float, *, spot_fraction: float = 0.05) -> np.ndarray:
    rng = np.random.default_rng(0)
    image = np.full((200, 200), background, dtype=np.float32)
    image += rng.normal(0.0, background * 0.002, image.shape).astype(np.float32)
    spot_count = int(image.size * spot_fraction)
    flat = image.reshape(-1)
    flat[:spot_count] = spot_value
    return image


class TestBimodalDipContrast(unittest.TestCase):
    def test_uniform_image_has_no_contrast(self) -> None:
        image = np.full((100, 100), 40000.0, dtype=np.float32)
        self.assertEqual(bimodal_dip_contrast(image), 0.0)

    def test_deeper_dark_spots_score_higher(self) -> None:
        shallow = _bimodal_image(background=50000.0, spot_value=40000.0)
        deep = _bimodal_image(background=50000.0, spot_value=1000.0)
        self.assertGreater(bimodal_dip_contrast(deep), bimodal_dip_contrast(shallow))

    def test_score_is_illumination_invariant(self) -> None:
        # This is the property that matters: two images with the same *relative*
        # spot depth but very different overall brightness (e.g. two wavelengths
        # where the light source/filter throughput differs) must score the same.
        # A raw min/max or percentile-range score would incorrectly favor the
        # brighter one instead of the one with the strongest true absorbance.
        dim = _bimodal_image(background=20000.0, spot_value=400.0)
        bright = _bimodal_image(background=60000.0, spot_value=1200.0)
        self.assertAlmostEqual(bimodal_dip_contrast(dim), bimodal_dip_contrast(bright), places=2)


if __name__ == "__main__":
    unittest.main()
