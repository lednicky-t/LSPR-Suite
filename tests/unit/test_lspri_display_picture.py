"""Tests for `panels/image/display_picture.py`: the frame's value range and ready-made picture, worked out on the
render thread (no Qt, no files). That the picture equals pyqtgraph's own is checked in
`tests/integration/test_lspri_display_picture.py`; here the rules of the module itself.
"""

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
    from lspr_imaging_app.panels.image.display_picture import LEVEL_SAMPLES, prepare_frame_display
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _frame(shape=(200, 260), seed=4) -> np.ndarray:
    return np.random.default_rng(seed).normal(2000.0, 300.0, shape).astype(np.float32)


def _with_corner_nan(frame: np.ndarray) -> np.ndarray:
    frame = frame.copy()
    frame[:12, :20] = np.nan
    return frame


class DataRangeTest(unittest.TestCase):
    def test_range_is_the_exact_min_and_max_of_the_finite_pixels(self) -> None:
        frame = _with_corner_nan(_frame())
        display = prepare_frame_display(frame)
        finite = frame[np.isfinite(frame)]
        self.assertEqual(display.data_range, (float(finite.min()), float(finite.max())))

    def test_the_range_is_exact_even_for_a_pixel_the_level_sample_skips(self) -> None:
        frame = _with_corner_nan(_frame((300, 500)))
        frame[1, 1] = 99999.0  # odd row and column: never in the strided level sample
        self.assertEqual(prepare_frame_display(frame).data_range[1], 99999.0)

    def test_a_frame_without_values_has_no_range_and_no_picture(self) -> None:
        display = prepare_frame_display(np.full((20, 30), np.nan, np.float32))
        self.assertIsNone(display.data_range)
        self.assertIsNone(display.picture)

    def test_infinite_pixels_are_left_out_of_the_range_and_get_no_picture(self) -> None:
        frame = _with_corner_nan(_frame())
        frame[50, 50] = np.inf
        frame[60, 60] = -np.inf
        display = prepare_frame_display(frame)
        finite = frame[np.isfinite(frame)]
        self.assertEqual(display.data_range, (float(finite.min()), float(finite.max())))
        self.assertIsNone(display.picture)  # pyqtgraph's own handling of infinities is not copied

    def test_only_infinite_pixels_do_not_raise(self) -> None:
        display = prepare_frame_display(np.full((10, 10), np.inf, np.float32))
        self.assertIsNone(display.data_range)


class PictureTest(unittest.TestCase):
    def test_a_frame_without_nan_gets_no_picture(self) -> None:
        display = prepare_frame_display(_frame())
        self.assertIsNone(display.picture)  # pyqtgraph's direct 8-bit path is cheaper
        self.assertIsNone(display.levels)

    def test_nan_pixels_are_transparent_and_all_others_opaque(self) -> None:
        frame = _with_corner_nan(_frame())
        picture = prepare_frame_display(frame).picture
        self.assertEqual(picture.shape, frame.shape + (4,))
        self.assertEqual(picture.dtype, np.uint8)
        self.assertTrue(picture.flags["C_CONTIGUOUS"])
        np.testing.assert_array_equal(picture[..., 3], np.where(np.isnan(frame), 0, 255))

    def test_nan_pixels_are_black_as_well_as_transparent(self) -> None:
        frame = _with_corner_nan(_frame())
        picture = prepare_frame_display(frame).picture
        self.assertEqual(int(picture[np.isnan(frame)].max()), 0)

    def test_the_three_colour_channels_are_equal_grey_values(self) -> None:
        picture = prepare_frame_display(_with_corner_nan(_frame())).picture
        np.testing.assert_array_equal(picture[..., 0], picture[..., 1])
        np.testing.assert_array_equal(picture[..., 0], picture[..., 2])

    def test_grey_follows_the_value_and_is_truncated_not_rounded(self) -> None:
        # a 4 x 4 frame is its own level sample: levels = (0, 255.5), scale = 256 / 255.5
        frame = np.array([[0.0, 100.0, 200.0, 255.5], [np.nan, 1.0, 2.0, 3.0], [4.0, 5.0, 6.0, 7.0], [8.0, 9.0, 10.0, 11.0]], np.float32)
        display = prepare_frame_display(frame)
        self.assertEqual(tuple(display.levels.tolist()), (0.0, 255.5))
        grey = display.picture[..., 0]
        scale = np.float32(256.0 / 255.5)
        expected = np.minimum((frame[np.isfinite(frame)] * scale).astype(np.float32), 255.0).astype(np.uint8)
        np.testing.assert_array_equal(grey[np.isfinite(frame)], expected)
        self.assertEqual(int(grey[0, 3]), 255)  # the top of the range is the top grey, not 256 wrapped to 0

    def test_values_beyond_the_sampled_levels_are_clipped(self) -> None:
        frame = _with_corner_nan(_frame((300, 500)))
        frame[1, 1] = 50000.0  # not in the level sample
        frame[3, 3] = -50000.0
        display = prepare_frame_display(frame)
        self.assertLess(display.levels[1], 50000.0)
        self.assertEqual(int(display.picture[1, 1, 0]), 255)
        self.assertEqual(int(display.picture[3, 3, 0]), 0)

    def test_levels_come_from_a_coarse_sample_like_pyqtgraph(self) -> None:
        frame = _with_corner_nan(_frame((1000, 1200)))
        sample = frame
        while sample.shape[0] * sample.shape[1] > LEVEL_SAMPLES:
            rows, columns = sample.shape
            sample = sample[::2, :] if rows > columns else sample[:, ::2]
        self.assertEqual(tuple(prepare_frame_display(frame).levels.tolist()), (float(np.nanmin(sample)), float(np.nanmax(sample))))

    def test_a_constant_frame_with_nan_uses_pyqtgraphs_zero_to_255_levels(self) -> None:
        frame = np.full((30, 40), 1234.0, np.float32)
        frame[0, 0] = np.nan
        display = prepare_frame_display(frame)
        self.assertEqual(tuple(display.levels.tolist()), (0, 255))
        self.assertEqual(int(display.picture[5, 5, 0]), min(int(1234.0 * 256.0 / 255.0), 255))

    def test_a_view_that_is_not_contiguous_gives_the_same_result(self) -> None:
        frame = _with_corner_nan(_frame((120, 80)))
        wide = np.asfortranarray(frame)  # same values, other memory layout
        a, b = prepare_frame_display(frame), prepare_frame_display(wide)
        self.assertEqual(a.data_range, b.data_range)
        np.testing.assert_array_equal(a.picture, b.picture)

    def test_the_frame_is_not_modified(self) -> None:
        frame = _with_corner_nan(_frame())
        before = frame.copy()
        prepare_frame_display(frame)
        np.testing.assert_array_equal(frame, before)  # NaN positions compare equal

    def test_anything_but_a_plain_2d_float32_frame_is_left_to_the_panel(self) -> None:
        self.assertIsNone(prepare_frame_display(np.zeros((4, 4, 3), np.float32)))
        self.assertIsNone(prepare_frame_display(np.zeros((4, 4), np.float64)))
        self.assertIsNone(prepare_frame_display(np.zeros((4, 4), np.uint16)))
        self.assertIsNone(prepare_frame_display(np.zeros((0, 4), np.float32)))


class FixedRangeTest(unittest.TestCase):
    """The View tab's fixed Display range: one black / white point for every frame (2026-10-14)."""

    def test_every_frame_reports_its_own_automatic_levels(self) -> None:
        for name, frame in (("with NaN", _with_corner_nan(_frame())), ("without NaN", _frame())):
            with self.subTest(name):
                display = prepare_frame_display(frame)
                self.assertEqual(display.auto_levels, (float(np.nanmin(frame)), float(np.nanmax(frame))))  # < 65 536 px: the whole frame
        nan_frame = _with_corner_nan(_frame())
        self.assertEqual(prepare_frame_display(nan_frame, (0.0, 4000.0)).auto_levels, prepare_frame_display(nan_frame).auto_levels)

    def test_a_frame_without_values_still_reports_pyqtgraphs_default_levels(self) -> None:
        self.assertEqual(prepare_frame_display(np.full((20, 30), np.nan, np.float32)).auto_levels, (0.0, 255.0))

    def test_a_fixed_range_scales_by_255_over_the_range_and_truncates(self) -> None:
        frame = np.array([[10.9, 100.5, 254.99, 300.0], [np.nan, -5.0, 0.0, 255.0]], np.float32)
        display = prepare_frame_display(frame, (0.0, 255.0))  # 255 / 255 = 1: the grey is the truncated value
        np.testing.assert_array_equal(display.picture[..., 0], [[10, 100, 254, 255], [0, 0, 0, 255]])
        np.testing.assert_array_equal(display.picture[..., 3], [[255, 255, 255, 255], [0, 255, 255, 255]])
        self.assertEqual(tuple(display.levels.tolist()), (0.0, 255.0))

    def test_equal_values_get_equal_grey_in_different_frames_but_only_with_a_fixed_range(self) -> None:
        dim = _with_corner_nan(_frame(seed=1))
        bright = _with_corner_nan(_frame(seed=2) * 1.5 + 500.0)
        dim[100, 100] = bright[100, 100] = 2222.0
        fixed = (0.0, 6000.0)
        self.assertEqual(
            int(prepare_frame_display(dim, fixed).picture[100, 100, 0]), int(prepare_frame_display(bright, fixed).picture[100, 100, 0])
        )
        self.assertNotEqual(  # automatic: each frame is stretched to its own range, so the same value looks different
            int(prepare_frame_display(dim).picture[100, 100, 0]), int(prepare_frame_display(bright).picture[100, 100, 0])
        )

    def test_a_fixed_range_is_not_changed_by_what_the_frame_contains(self) -> None:
        frame = _with_corner_nan(_frame())
        frame[1, 1] = 50000.0  # a hot pixel moves the exact range, never the fixed one
        display = prepare_frame_display(frame, (500.0, 3500.0))
        self.assertEqual(tuple(display.levels.tolist()), (500.0, 3500.0))
        self.assertEqual(int(display.picture[1, 1, 0]), 255)

    def test_an_unusable_fixed_range_gives_no_picture(self) -> None:
        frame = _with_corner_nan(_frame())
        for bad in ((5.0, 5.0), (9.0, 1.0), (float("nan"), 1.0), (0.0, float("inf")), (1.0,)):
            with self.subTest(bad):
                self.assertIsNone(prepare_frame_display(frame, bad).picture)


if __name__ == "__main__":
    unittest.main()
