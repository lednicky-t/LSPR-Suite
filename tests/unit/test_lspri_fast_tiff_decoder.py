"""Tests for the render thread's fast TIFF decoder (`dataset/io.py`: `use_fast_tiff_decoder`, `_read_tiff_fast`).

It must give exactly the pixels tifffile gives, be strictly opt-in per thread, fall back for anything that is
not a plain 2-D image, and verify itself against tifffile (a multi-page file is the case libtiff and tifffile
read differently). Real files in a temp folder, no Qt.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import tifffile

    from lspr_imaging_app.dataset import io as dataset_io
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _reset_fast_path_state() -> None:
    dataset_io._fast_tiff_verified = 0
    dataset_io._fast_tiff_disabled = False
    dataset_io.use_fast_tiff_decoder(False)


@unittest.skipIf(dataset_io._imagecodecs is None, "imagecodecs with libtiff is not available")
class FastTiffDecoderTest(unittest.TestCase):
    def setUp(self) -> None:
        _reset_fast_path_state()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        rng = np.random.default_rng(5)
        self.int16 = rng.normal(2000, 300, (60, 80)).clip(0, 65535).astype(np.uint16)
        self.float32 = rng.normal(2000, 300, (60, 80)).astype(np.float32)
        self.float32[:4, :6] = np.nan

    def tearDown(self) -> None:
        _reset_fast_path_state()
        self._tmp.cleanup()

    def _write(self, name: str, data: np.ndarray, **kwargs) -> str:
        path = str(self.dir / name)
        tifffile.imwrite(path, data, **kwargs)
        return path

    def _both(self, path: str) -> tuple[np.ndarray, np.ndarray]:
        dataset_io.use_fast_tiff_decoder(False)
        reference = dataset_io._load_image_array_uncached(path)
        dataset_io.use_fast_tiff_decoder(True)
        fast = dataset_io._load_image_array_uncached(path)
        return fast, reference

    def test_pixels_are_identical_to_tifffile_across_tiff_variants(self) -> None:
        variants = {
            "uint16 lzw strips": (self.int16, dict(compression="lzw", rowsperstrip=3)),
            "float32 lzw": (self.float32, dict(compression="lzw")),
            "float32 lzw + predictor": (self.float32, dict(compression="lzw", predictor=True)),
            "uint16 zlib tiled": (self.int16, dict(compression="zlib", tile=(32, 32))),
            "uint16 uncompressed": (self.int16, dict(compression=None)),
            "uint16 bigtiff": (self.int16, dict(compression="lzw", bigtiff=True)),
            "uint16 big-endian": (self.int16, dict(compression="lzw", byteorder=">")),
            "uint16 orientation tag": (self.int16, dict(compression="lzw", extratags=[(274, "H", 1, 3, True)])),
            "uint16 min-is-white": (self.int16, dict(compression="lzw", photometric="miniswhite")),
        }
        for name, (data, kwargs) in variants.items():
            with self.subTest(name):
                fast, reference = self._both(self._write(name.replace(" ", "_") + ".tiff", data, **kwargs))
                self.assertEqual(fast.dtype, np.float32)
                np.testing.assert_array_equal(fast, reference)  # NaN positions included (assert_array_equal treats NaN == NaN)

    def test_it_is_used_only_on_threads_that_opt_in(self) -> None:
        path = self._write("a.tiff", self.int16, compression="lzw")
        calls = []
        original = dataset_io._read_tiff_fast
        dataset_io._read_tiff_fast = lambda p: (calls.append(threading.current_thread().name), original(p))[1]
        try:
            dataset_io._load_image_array_uncached(path)  # this thread has not opted in
            self.assertEqual(calls, [])
            seen = []

            def worker() -> None:
                dataset_io.use_fast_tiff_decoder(True)
                dataset_io._load_image_array_uncached(path)
                seen.append(True)

            thread = threading.Thread(target=worker, name="opted-in")
            thread.start()
            thread.join()
            self.assertEqual(calls, ["opted-in"])
            dataset_io._load_image_array_uncached(path)  # and this thread still has not
            self.assertEqual(calls, ["opted-in"])
        finally:
            dataset_io._read_tiff_fast = original

    def test_an_environment_style_switch_turns_it_off(self) -> None:
        path = self._write("a.tiff", self.int16, compression="lzw")
        dataset_io.use_fast_tiff_decoder(True)
        self.assertTrue(dataset_io.fast_tiff_decoder_active())
        dataset_io._fast_tiff_disabled = True  # what LSPR_TIFF_DECODER=tifffile sets at import
        self.assertFalse(dataset_io.fast_tiff_decoder_active())
        np.testing.assert_array_equal(dataset_io._load_image_array_uncached(path), self.int16.astype(np.float32))

    def test_anything_but_a_plain_2d_image_falls_back_to_tifffile(self) -> None:
        rgb = self._write("rgb.tiff", np.stack([self.int16.astype(np.uint8)] * 3, axis=-1), photometric="rgb")
        dataset_io.use_fast_tiff_decoder(True)
        self.assertIsNone(dataset_io._read_tiff_fast(rgb))  # 3-D: not the fast path's job
        fast, reference = self._both(rgb)
        np.testing.assert_array_equal(fast, reference)

    def test_a_damaged_file_does_not_raise_from_the_fast_path(self) -> None:
        path = self._write("a.tiff", self.int16, compression="lzw")
        data = Path(path).read_bytes()
        Path(path).write_bytes(data[: len(data) // 3])  # truncated
        dataset_io.use_fast_tiff_decoder(True)
        try:
            result = dataset_io._read_tiff_fast(path)
        except Exception as exc:  # pragma: no cover
            self.fail(f"the fast path raised {exc!r}; it must return None so tifffile can report the problem")
        self.assertTrue(result is None or result.ndim == 2)

    def test_the_first_files_are_verified_and_a_multipage_file_switches_the_fast_path_off(self) -> None:
        """libtiff returns page 1 only, tifffile the whole stack: the self-check must notice and fall back."""
        stack = self._write("stack.tiff", np.stack([self.int16, self.int16 // 2]), compression="lzw")
        dataset_io.use_fast_tiff_decoder(True)
        with self.assertLogs("lspr_imaging_app.workflow", level="WARNING") as logs:
            fast, reference = self._both(stack)
        self.assertEqual(fast.shape, reference.shape)  # tifffile's answer was used
        self.assertEqual(fast.shape, (2, 60, 80))
        self.assertTrue(dataset_io._fast_tiff_disabled)
        self.assertIn("switched off", logs.output[0])
        self.assertFalse(dataset_io.fast_tiff_decoder_active())

    def test_verification_counts_good_files_and_then_stops_checking(self) -> None:
        dataset_io.use_fast_tiff_decoder(True)
        for index in range(dataset_io.FAST_TIFF_VERIFY_FILES + 2):
            path = self._write(f"f{index}.tiff", self.int16 + index, compression="lzw")
            dataset_io._load_image_array_uncached(path)
        self.assertEqual(dataset_io._fast_tiff_verified, dataset_io.FAST_TIFF_VERIFY_FILES)
        self.assertFalse(dataset_io._fast_tiff_disabled)


if __name__ == "__main__":
    unittest.main()
