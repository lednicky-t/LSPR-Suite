"""Pure builders for the OME-Zarr export metadata (`dataset/ome_metadata.py`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring. No Qt, no files.
"""

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
    from lspr_imaging_app.dataset.ome_metadata import (
        build_axes,
        build_omero_channels,
        build_scale,
        display_window,
        plane_times_s,
        valid_pixel_size,
        wavelength_to_hex,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


class AxesAndScaleTests(unittest.TestCase):
    def test_the_first_axis_is_an_untyped_index_never_time(self) -> None:
        for pixel_size in (None, (0.5, 0.5)):
            axes = build_axes(pixel_size)
            self.assertEqual(axes[0], {"name": "cube_index"})
            self.assertFalse(any(a.get("type") == "time" for a in axes))
        self.assertEqual(
            [(a["name"], a["type"]) for a in build_axes(None)[1:]],
            [("wavelength", "channel"), ("y", "space"), ("x", "space")],
        )

    def test_space_axes_carry_a_unit_only_when_calibrated(self) -> None:
        self.assertTrue(all("unit" not in a for a in build_axes(None)))
        self.assertEqual([a.get("unit") for a in build_axes((0.5, 0.5))], [None, None, "micrometer", "micrometer"])

    def test_scale_is_one_for_the_index_axes_then_y_then_x(self) -> None:
        self.assertEqual(build_scale((0.4, 0.6)), [1.0, 1.0, 0.6, 0.4])
        self.assertEqual(build_scale(None), [1.0, 1.0, 1.0, 1.0])


class PlaneTimesTests(unittest.TestCase):
    def test_every_plane_gets_its_own_time_relative_to_the_earliest(self) -> None:
        # Two cubes, three wavelengths, planes acquired one after another.
        per_frame = {
            (0, 500.0): 10_000, (0, 600.0): 10_300, (0, 700.0): 10_650,
            (1, 500.0): 20_000, (1, 600.0): 20_310, (1, 700.0): 20_640,
        }
        times, origin = plane_times_s(per_frame, [0, 1], [500.0, 600.0, 700.0])
        self.assertEqual(origin, 10_000)
        self.assertEqual(times.shape, (2, 3))
        np.testing.assert_allclose(times, [[0.0, 0.3, 0.65], [10.0, 10.31, 10.64]])

    def test_a_plane_without_a_time_is_nan_not_zero(self) -> None:
        times, origin = plane_times_s({(0, 500.0): 5000, (1, 600.0): 7000}, [0, 1], [500.0, 600.0])
        self.assertEqual(origin, 5000)
        np.testing.assert_allclose(times, [[0.0, np.nan], [np.nan, 2.0]])

    def test_no_timing_at_all_gives_none(self) -> None:
        self.assertIsNone(plane_times_s({}, [0, 1], [500.0]))
        self.assertIsNone(plane_times_s({(5, 500.0): 1}, [0, 1], [500.0]))  # times exist, but not for exported planes

    def test_invalid_pixel_sizes_are_rejected(self) -> None:
        self.assertEqual(valid_pixel_size(0.5, 0.25), (0.5, 0.25))
        for bad in ((0.0, 1.0), (1.0, -1.0), (float("nan"), 1.0), (1.0, float("inf"))):
            self.assertIsNone(valid_pixel_size(*bad))


class ChannelTests(unittest.TestCase):
    def test_colours(self) -> None:
        self.assertEqual(wavelength_to_hex(650.0), "FF0000")
        self.assertEqual(wavelength_to_hex(532.0)[2:4], "FF")  # strong green
        self.assertEqual(wavelength_to_hex(900.0), "FFFFFF")  # NIR: no visible colour
        self.assertEqual(wavelength_to_hex(300.0), "FFFFFF")
        for wl in np.arange(380.0, 781.0, 5.0):
            value = wavelength_to_hex(float(wl))
            self.assertEqual(len(value), 6)
            int(value, 16)

    def test_window_uses_dtype_range_and_finite_percentiles(self) -> None:
        plane = np.arange(10000, dtype=np.float32).reshape(100, 100)
        plane[:10, :10] = np.nan
        window = display_window(plane, np.dtype(np.uint16))
        self.assertEqual((window["min"], window["max"]), (0.0, 65535.0))
        self.assertTrue(0.0 < window["start"] < window["end"] < 10000.0)

    def test_window_with_no_finite_pixel_falls_back_to_the_full_range(self) -> None:
        window = display_window(np.full((4, 4), np.nan), np.dtype(np.uint16))
        self.assertEqual(window, {"min": 0.0, "max": 65535.0, "start": 0.0, "end": 65535.0})
        self.assertEqual(display_window(np.array([]), np.dtype(np.uint16))["end"], 65535.0)

    def test_channels_follow_the_wavelength_order(self) -> None:
        windows = [display_window(np.arange(100.0), np.dtype(np.uint16))] * 2
        channels = build_omero_channels([500.0, 600.5], windows)
        self.assertEqual([c["label"] for c in channels], ["500 nm", "600.5 nm"])
        self.assertTrue(all(c["active"] for c in channels))


if __name__ == "__main__":
    unittest.main()
