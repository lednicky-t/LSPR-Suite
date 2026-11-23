"""`ChromaticModule.affine_between_memo` must answer exactly what `affine_between` answers, to the last bit.

The scan's snapshot asks for the matrix between a mask's authored frame and every frame of the dataset (69,029 calls on a 2,655-cube
dataset); the memo exists to make that loop fast. The matrices go into the scan's cell hash as bytes, so "almost equal" is not good
enough: one ulp of difference would make cubes stale without a pixel moving. Cost record: `docs/snapshot_source_cost_2026-11-23.md`.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, replace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.chromatic import ChromaticModule
    from lspr_imaging_app.image_tools.chromatic.model import ChromaticSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


@dataclass
class _Model:
    """What `fitted_affine_for` reads from a fitted model."""

    affine_matrix: np.ndarray


def _module(enabled: bool = True) -> ChromaticModule:
    module = ChromaticModule()
    module._settings = replace(ChromaticSettings(), chromatic_correction_enabled=enabled)
    rng = np.random.default_rng(7)
    models = {}
    for cube in range(4):
        for wl in (0.0, 450.0, 520.0, 589.3, 640.0):
            if wl == 589.3:
                continue  # no model: the identity, like the reference wavelength
            # A few cubes share a matrix with another (as a real session does), most differ in the last digits.
            seed_matrix = np.array([[1.0 + 1e-3 * wl / 100, 2e-4, 3.5], [-2e-4, 1.0 - 1e-3 * wl / 100, -1.25]])
            noise = 0.0 if cube % 2 == 0 else rng.normal(scale=1e-6, size=(2, 3))
            models[(cube, wl)] = _Model(seed_matrix + noise)
    module._models = models
    return module


class AffineBetweenMemoTests(unittest.TestCase):
    def test_every_pair_is_bit_identical_to_affine_between(self) -> None:
        module = _module()
        between = module.affine_between_memo()
        frames = [(cube, wl) for cube in range(4) for wl in (0.0, 450.0, 520.0, 589.3, 640.0)]
        for authored in frames:
            for frame in frames + frames:  # twice: the second pass is answered from the memo
                expected = module.affine_between(authored, frame)
                got = between(authored, frame)
                self.assertEqual(expected.dtype, got.dtype)
                self.assertEqual(expected.shape, got.shape)
                self.assertEqual(expected.tobytes(), got.tobytes(), msg=f"{authored} -> {frame}")

    def test_correction_switched_off_gives_identity_like_affine_between(self) -> None:
        module = _module(enabled=False)
        between = module.affine_between_memo()
        for authored, frame in [((0, 450.0), (2, 520.0)), ((1, 520.0), (3, 640.0)), ((0, 450.0), (0, 450.0))]:
            self.assertEqual(module.affine_between(authored, frame).tobytes(), between(authored, frame).tobytes())

    def test_a_fresh_memo_sees_a_changed_model_and_a_used_one_is_not_to_be_kept(self) -> None:
        module = _module()
        authored, frame = (0, 450.0), (1, 520.0)
        old = module.affine_between_memo()(authored, frame).copy()
        module._models[frame] = _Model(np.array([[1.01, 0.0, 9.0], [0.0, 0.99, -4.0]]))
        fresh = module.affine_between_memo()(authored, frame)
        self.assertEqual(module.affine_between(authored, frame).tobytes(), fresh.tobytes())
        self.assertNotEqual(old.tobytes(), fresh.tobytes())

    def test_results_are_read_only_so_a_shared_matrix_cannot_be_changed_by_accident(self) -> None:
        between = _module().affine_between_memo()
        result = between((0, 450.0), (1, 520.0))
        self.assertFalse(result.flags.writeable)
        # the snapshot copies before keeping one (`np.array(..., copy=True)`), and that copy is writable
        self.assertTrue(np.array(result, copy=True).flags.writeable)


if __name__ == "__main__":
    unittest.main()
