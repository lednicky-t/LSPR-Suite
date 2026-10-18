"""Scale bar sizing rules (pure functions, no Qt app needed).

Written with `unittest` (not pytest): the CI workflow runs `python -m unittest discover` and installs no pytest, so a
pytest-style file fails to import there, and even with pytest installed `unittest` would find no test in it.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.scale_bar_overlay import TARGET_FRACTION, choose_bar, nice_length
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _close(test: unittest.TestCase, actual: float, expected: float) -> None:
    """Relative tolerance 1e-6, as `pytest.approx` used."""
    test.assertAlmostEqual(actual, expected, delta=1e-6 * abs(expected))


class ScaleBarSizingTests(unittest.TestCase):
    def test_nice_length_is_1_2_5(self) -> None:
        cases = [(1.0, 1.0), (1.4, 1.0), (1.6, 2.0), (3.4, 2.0), (3.6, 5.0), (7.4, 5.0), (7.6, 10.0), (0.03, 0.02), (730.0, 500.0)]
        for target, expected in cases:
            with self.subTest(target=target):
                _close(self, nice_length(target), expected)

    def test_pixel_bar_stays_within_view_fraction(self) -> None:
        for width in (3.0, 50.0, 640.0, 2048.0, 1e5):
            with self.subTest(width=width):
                length, label = choose_bar(width, None)
                self.assertTrue(label.endswith(" px"))
                self.assertTrue(0.13 * width <= length <= 0.36 * width or length == 1.0)  # 1 px floor at extreme zoom-in

    def test_um_bar_label_is_readable_and_consistent(self) -> None:
        cases = [(2048, 0.1), (2048, 0.5), (100, 0.065), (20, 0.065), (4000, 3.0), (5000, 0.0004)]
        for width, um_per_px in cases:
            with self.subTest(width=width, um_per_px=um_per_px):
                length_px, label = choose_bar(width, um_per_px)
                number, unit = label.split(" ")
                self.assertIn(unit, ("nm", "µm", "mm"))
                self.assertTrue(1.0 <= float(number) < 1000.0)
                factor = {"nm": 1e-3, "µm": 1.0, "mm": 1e3}[unit]
                # the drawn length really is the labelled physical length
                _close(self, length_px * um_per_px, float(number) * factor)
                self.assertTrue(0.13 * width <= length_px <= 0.36 * width)

    def test_target_fraction_is_what_the_docstring_says(self) -> None:
        _close(self, TARGET_FRACTION, 0.2)


if __name__ == "__main__":
    unittest.main()
