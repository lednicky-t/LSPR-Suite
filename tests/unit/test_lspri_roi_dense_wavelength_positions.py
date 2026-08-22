from __future__ import annotations

import sys
import unittest
from copy import deepcopy

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi
from lspr_imaging_app.gui.image_render_manager import _apply_dense_overrides


class TestApplyDenseOverrides(unittest.TestCase):
    def setUp(self) -> None:
        self.original = [
            AreaRoi(area_roi_id=1, center_x=10.0, center_y=20.0, sample_radius_px=5.0),
            AreaRoi(area_roi_id=2, center_x=50.0, center_y=60.0, sample_radius_px=5.0),
        ]

    def test_overwrites_center_when_an_override_exists_for_the_key(self) -> None:
        self.original[0].per_wavelength = {(0, 450.0): (99.0, 88.0)}
        transformed = deepcopy(self.original)
        transformed[0].center_x, transformed[0].center_y = 30.0, 40.0  # what the affine derived
        result = _apply_dense_overrides(transformed, self.original, (0, 450.0))
        self.assertEqual((result[0].center_x, result[0].center_y), (99.0, 88.0))
        # ROI without an override for this key is left exactly as transformed.
        self.assertEqual((result[1].center_x, result[1].center_y), (50.0, 60.0))

    def test_no_override_leaves_transformed_positions_untouched(self) -> None:
        transformed = deepcopy(self.original)
        transformed[0].center_x, transformed[0].center_y = 30.0, 40.0
        result = _apply_dense_overrides(transformed, self.original, (0, 450.0))
        self.assertEqual((result[0].center_x, result[0].center_y), (30.0, 40.0))

    def test_none_image_key_is_a_no_op(self) -> None:
        self.original[0].per_wavelength = {(0, 450.0): (99.0, 88.0)}
        transformed = deepcopy(self.original)
        result = _apply_dense_overrides(transformed, self.original, None)
        self.assertIs(result, transformed)

    def test_override_for_a_different_key_does_not_apply(self) -> None:
        self.original[0].per_wavelength = {(0, 450.0): (99.0, 88.0)}
        transformed = deepcopy(self.original)
        transformed[0].center_x, transformed[0].center_y = 30.0, 40.0
        result = _apply_dense_overrides(transformed, self.original, (0, 550.0))
        self.assertEqual((result[0].center_x, result[0].center_y), (30.0, 40.0))


if __name__ == "__main__":
    unittest.main()
