"""`frame_naming.wavelength_decimal_precision` keeps its answer while it gets faster (2026-11-28).

A saved session's mask files carry the result in their names (`mask_cube0000_wl570.000_persi_v1.png`), so the answer may never change for the
same input: the fast path is checked against a copy of the loop as it was, on lists with and without repeated wavelengths. Pure, no Qt."""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.frame_naming import FrameNamingScheme, wavelength_decimal_precision
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def as_it_was(wavelengths_nm: list[float], max_decimals: int = 3) -> int:
    """The function before the fast path (copied, not imported)."""
    for decimals in range(max_decimals + 1):
        rounded = [round(float(wl), decimals) for wl in wavelengths_nm]
        if len(set(rounded)) == len(wavelengths_nm):
            return decimals
    return max_decimals


class WavelengthPrecisionTests(unittest.TestCase):
    WAVELENGTHS = [470.0 + 10.0 * i for i in range(27)]  # the LbL dataset's 27 wavelengths

    def test_one_list_per_dataset_whole_nanometres_are_enough(self):
        self.assertEqual(wavelength_decimal_precision(self.WAVELENGTHS), 0)

    def test_close_wavelengths_need_decimals(self):
        self.assertEqual(wavelength_decimal_precision([500.0, 500.4]), 1)
        self.assertEqual(wavelength_decimal_precision([500.0, 500.04]), 2)
        self.assertEqual(wavelength_decimal_precision([500.0, 500.004]), 3)
        self.assertEqual(wavelength_decimal_precision([500.0, 500.0004]), 3)  # not told apart even at the maximum

    def test_empty_and_single(self):
        self.assertEqual(wavelength_decimal_precision([]), 0)
        self.assertEqual(wavelength_decimal_precision([650.0]), 0)

    def test_a_list_with_one_entry_per_cube_and_wavelength_gives_the_maximum(self):
        """What the app hands over (`app_rewrite._frame_naming`): every wavelength once per cube. Always the maximum, as before; the
        saved mask files of existing sessions are named with it."""
        per_cube = self.WAVELENGTHS * 2655
        self.assertEqual(wavelength_decimal_precision(per_cube), 3)
        self.assertEqual(FrameNamingScheme.for_dataset(list(range(2655)), per_cube).frame_tag(0, 570.0), "cube0000_wl570.000")

    def test_the_answer_is_the_one_of_the_old_loop_on_many_lists(self):
        rng = np.random.default_rng(5)
        for trial in range(300):
            count = int(rng.integers(0, 40))
            base = rng.choice([0.0, 0.4, 0.04, 0.004, 0.0004, 0.5, 1.0, 10.0], size=count) + rng.integers(400, 900, size=count)
            values = [float(v) for v in base]
            if trial % 3 == 0 and values:
                values += values[: int(rng.integers(1, len(values) + 1))]  # repeats, like one entry per cube
            with self.subTest(trial=trial):
                self.assertEqual(wavelength_decimal_precision(values), as_it_was(values))
                self.assertEqual(wavelength_decimal_precision(values, max_decimals=2), as_it_was(values, max_decimals=2))


if __name__ == "__main__":
    unittest.main()
